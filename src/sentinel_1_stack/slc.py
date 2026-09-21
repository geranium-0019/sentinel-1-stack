"""Download explicitly selected Sentinel-1 IW SLCs, and verify them without modifying the JSON.

Adapted from sar-tools/download-sentinel-1; that standalone tool is unchanged.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from contextlib import ExitStack, contextmanager, redirect_stdout, redirect_stderr
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import sys
import time
import threading
from urllib.parse import urlsplit
from uuid import uuid4

from .workspace import WorkspaceError, require_initialized

CHUNK_SIZE = 1024 * 1024
SLC_NAME = re.compile(
    r"S1[ABCD]_IW_SLC__1S[SD][HV]_\d{8}T\d{6}_\d{8}T\d{6}_"
    r"\d{6}_[0-9A-F]{6}_[0-9A-F]{4}\.zip"
)


class InputError(ValueError):
    """Invalid arguments or input metadata."""


class DownloadCancelled(RuntimeError):
    """A peer failed or the user interrupted this batch."""


class DownloadError(RuntimeError):
    """A download or verification operation could not complete."""


@dataclass(frozen=True)
class Product:
    filename: str
    url: str
    size: int
    md5: str


@dataclass(frozen=True)
class Batch:
    path: Path
    content: bytes
    products: tuple[Product, ...]


def select_input(path: Path) -> Path:
    if path.is_symlink():
        raise InputError("入力にはシンボリックリンクではなく通常ファイルを置いてください。")
    if path.suffix.lower() not in {".json", ".geojson"}:
        raise InputError("入力ファイルの拡張子は .geojson または .json にしてください。")
    if not path.is_file():
        raise InputError(f"入力 JSON がありません: {path}")
    return path


def parse_product(feature: object, index: int) -> Product:
    if not isinstance(feature, dict) or feature.get("type") != "Feature":
        raise InputError(f"製品 {index}: GeoJSON Feature ではありません。")
    props = feature.get("properties")
    if not isinstance(props, dict):
        raise InputError(f"製品 {index}: properties がありません。")
    required = ("url", "fileName", "bytes", "md5sum")
    missing = [key for key in required if key not in props]
    if missing:
        raise InputError(f"製品 {index}: 必須項目がありません: {', '.join(missing)}")

    filename = props["fileName"]
    if (
        not isinstance(filename, str)
        or not filename
        or filename in {".", ".."}
        or "/" in filename
        or "\\" in filename
        or any(ord(char) < 32 or ord(char) == 127 for char in filename)
    ):
        raise InputError(f"製品 {index}: fileName はパスを含まないファイル名にしてください。")
    if not SLC_NAME.fullmatch(filename):
        raise InputError(f"製品 {index}: Sentinel-1 IW SLC の ZIP ではありません: {filename}")

    url = props["url"]
    try:
        parsed = urlsplit(url) if isinstance(url, str) else None
        valid_url = (
            parsed is not None
            and parsed.scheme == "https"
            and parsed.hostname is not None
            and (parsed.hostname == "asf.alaska.edu" or parsed.hostname.endswith(".asf.alaska.edu"))
            and parsed.username is None
            and parsed.password is None
            and parsed.port in (None, 443)
        )
    except ValueError:
        valid_url = False
    if not valid_url:
        raise InputError(f"製品 {index}: url は ASF の HTTPS ダウンロードURLにしてください。")

    size = props["bytes"]
    if isinstance(size, str) and re.fullmatch(r"[0-9]+", size):
        size = int(size)
    if type(size) is not int or size <= 0:
        raise InputError(f"製品 {index}: bytes は正の整数にしてください。")
    checksum = props["md5sum"]
    if not isinstance(checksum, str) or not re.fullmatch(r"[0-9a-fA-F]{32}", checksum):
        raise InputError(f"製品 {index}: md5sum は32桁の16進数にしてください。")
    return Product(filename, url, size, checksum.lower())


def load_batch(path: Path) -> Batch:
    path = select_input(path)
    content = path.read_bytes()
    try:
        data = json.loads(content)
    except (ValueError, UnicodeError) as exc:
        raise InputError("入力ファイルを JSON として読み込めません。") from exc
    if not isinstance(data, dict) or data.get("type") != "FeatureCollection":
        raise InputError("入力は ASF GeoJSON の FeatureCollection にしてください。")
    features = data.get("features")
    if not isinstance(features, list) or not features:
        raise InputError("features には1件以上の製品が必要です。")
    products = tuple(parse_product(feature, i) for i, feature in enumerate(features, 1))
    filenames = {product.filename for product in products}
    if len(filenames) != len(products):
        raise InputError("同じ fileName の製品が複数あります。入力の重複を解消してください。")
    if any(f"{name}.part" in filenames for name in filenames):
        raise InputError("fileName が他の製品の一時ファイル名と衝突しています。")
    return Batch(path, content, products)


def print_plan(batch: Batch, output_dir: Path) -> None:
    total = sum(product.size for product in batch.products)
    print(f"入力: {batch.path}")
    print(f"保存先: {output_dir}")
    print(f"製品数: {len(batch.products)}")
    print(f"合計容量: {total:,} bytes ({total / 10**9:.2f} GB / {total / 2**30:.2f} GiB)")
    for i, product in enumerate(batch.products, 1):
        print(f"  {i}. {product.filename} ({product.size:,} bytes)")


@contextmanager
def batch_lock(output_dir: Path):
    # Keep the lock file after unlocking: unlinking it can let a third process in.
    import fcntl

    path = output_dir / ".download.lock"
    check_file_slot(path)
    with path.open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise DownloadError("別のダウンロード処理が実行中です。") from exc
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def check_file_slot(path: Path) -> None:
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise DownloadError(f"通常ファイル以外が保存先に存在します: {path}")


def verified_file(path: Path, product: Product) -> bool:
    check_file_slot(path)
    if not path.is_file() or path.stat().st_size != product.size:
        return False
    checksum = hashlib.md5(usedforsecurity=False)
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(CHUNK_SIZE), b""):
            checksum.update(chunk)
    return checksum.hexdigest() == product.md5


def create_session():
    try:
        from asf_search import ASFSession
    except ImportError as exc:
        raise DownloadError("asf_search がありません。SLC 取得対応のイメージをビルドしてください。") from exc
    # ASFSession applies .netrc credentials across the Earthdata redirects.
    session = ASFSession()
    # ZIPs are already compressed; receive exact product bytes for size/MD5.
    session.headers["Accept-Encoding"] = "identity"
    return session


def is_dns_error(error: BaseException) -> bool:
    """Requests wraps urllib3/socket errors, sometimes in args or reason."""
    from urllib3.exceptions import NameResolutionError

    pending = [error]
    seen = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, (socket.gaierror, NameResolutionError)):
            return True
        nested = (current.__cause__, current.__context__, getattr(current, "reason", None), *current.args)
        pending.extend(item for item in nested if isinstance(item, BaseException))
    return False


def request_product(session, product, *, allow_redirects=True, stop=None, headers=None):
    """Retry DNS failures before consuming any product bytes, including redirects."""
    from requests.exceptions import RequestException

    delays = (1, 2, 4)
    for attempt in range(len(delays) + 1):
        check_cancelled(stop)
        try:
            return session.get(product.url, stream=True, timeout=(30, 120),
                               allow_redirects=allow_redirects, **({"headers": headers} if headers else {}))
        except RequestException as exc:
            if not is_dns_error(exc) or attempt == len(delays):
                raise
            # Exception strings may contain signed redirect URLs. Never log them.
            delay = delays[attempt]
            print(f"[{getattr(product, 'filename', 'ASF問い合わせ')}] DNS 再試行 {attempt + 1}/{len(delays)}: "
                  f"名前解決に失敗したため {delay} 秒後に再接続します。", flush=True)
            if stop is None:
                time.sleep(delay)
            elif stop.wait(delay):
                check_cancelled(stop)


def check_cancelled(stop):
    if stop is not None and stop.is_set():
        raise DownloadCancelled("他の取得失敗または中断により停止しました。")


class RetryTransfer(DownloadError):
    """A bounded retry can recover this transfer."""


def retry_pause(attempt, stop):
    delay = min(2 ** min(attempt + 1, 5), 30)
    if stop is None:
        time.sleep(delay)
    elif stop.wait(delay):
        check_cancelled(stop)


def fetch_product(product: Product, output_dir: Path, session, *, replace_invalid=False,
                  stop=None, retries=3) -> str:
    from requests.exceptions import RequestException, ConnectionError, Timeout, ChunkedEncodingError, SSLError

    check_cancelled(stop)
    destination = output_dir / product.filename
    partial = output_dir / f"{product.filename}.part"
    check_file_slot(partial)
    if verified_file(destination, product):
        print(f"[{product.filename}] サイズ・MD5 一致: 取得済みのためスキップ", flush=True)
        return "skipped"
    if destination.exists() and not replace_invalid:
        raise DownloadError(f"既存 ZIP のサイズまたは MD5 が不一致です: {product.filename}。"
                            "置き換える場合は --replace-invalid を指定してください。")
    if verified_file(partial, product):
        os.replace(partial, destination)
        print(f"[{product.filename}] 検証済みの一時ファイルを正式名に変更", flush=True)
        return "recovered"

    # A complete but corrupt (or oversized) partial cannot be resumed. Preserve it
    # until a fresh response has been validated and is ready to replace its bytes.
    restart = partial.exists() and partial.stat().st_size >= product.size
    for attempt in range(retries + 1):
        check_cancelled(stop)
        existing = partial.stat().st_size if partial.exists() else 0
        if existing >= product.size:
            if not restart and verified_file(partial, product):
                os.replace(partial, destination)
                print(f"[{product.filename}] 完全受信済みの .part を検証して確定しました。", flush=True)
                return "downloaded"
            restart = True
        offset = 0 if restart else existing
        checksum = hashlib.md5(usedforsecurity=False)
        if offset:
            with partial.open('rb') as stream:
                for chunk in iter(lambda: stream.read(CHUNK_SIZE), b''):
                    check_cancelled(stop)
                    checksum.update(chunk)
            print(f"[{product.filename}] {offset:,} bytes からの再開を要求します。", flush=True)
        if shutil.disk_usage(output_dir).free + existing < product.size:
            raise DownloadError(f"保存先の空き容量が不足しています: {product.filename}")
        headers = {'Range': f'bytes={offset}-', 'Accept-Encoding': 'identity'} if offset else {'Accept-Encoding': 'identity'}
        try:
            with request_product(session, product, stop=stop, headers=headers) as response:
                status = response.status_code
                if status in {401, 403}:
                    raise DownloadError("Earthdata 認証に失敗しました。~/.netrc の設定とデータへのアクセス権を確認してください。")
                if status in {429, 500, 502, 503, 504}:
                    raise RetryTransfer(f"HTTP {status}: {product.filename}")
                if status == 416 and offset:
                    restart = True
                    raise RetryTransfer(f"再開位置が拒否されました。先頭から再取得します: {product.filename}")
                if status not in {200, 206}:
                    raise DownloadError(f"HTTP {status}: {product.filename} の取得に失敗しました。")
                response_headers = getattr(response, 'headers', {})
                if response_headers.get('Content-Encoding', 'identity').lower() not in {'identity', ''}:
                    raise DownloadError(f"圧縮されたHTTP応答には追記しません: {product.filename}")
                if status == 206:
                    match = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)', response_headers.get('Content-Range', ''))
                    if not offset or not match or tuple(map(int, match.groups())) != (offset, product.size - 1, product.size):
                        raise DownloadError(f"Content-Range が要求した位置・サイズと一致しません。追記せず停止します: {product.filename}")
                else:
                    if offset:
                        print(f"[{product.filename}] サーバーが再開に応じないため、先頭から取得します。", flush=True)
                    offset = 0
                    checksum = hashlib.md5(usedforsecurity=False)
                length = response_headers.get('Content-Length')
                if length is not None and (not length.isdigit() or int(length) != product.size - offset):
                    raise DownloadError(f"Content-Length が予定サイズと一致しません: {product.filename}")
                written = offset
                restart = False
                last_report = time.monotonic()
                with partial.open('ab' if offset else 'wb') as stream:
                    for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
                        check_cancelled(stop)
                        if not chunk:
                            continue
                        if written + len(chunk) > product.size:
                            raise DownloadError(f"受信データが予定サイズを超えました: {product.filename}")
                        stream.write(chunk)
                        checksum.update(chunk)
                        written += len(chunk)
                        now = time.monotonic()
                        if now - last_report >= 5:
                            print(f"[{product.filename}] {written / product.size:.1%} ({written:,} / {product.size:,} bytes)", flush=True)
                            last_report = now
                    stream.flush()
                    os.fsync(stream.fileno())
            check_cancelled(stop)
            if written != product.size:
                raise RetryTransfer(f"サイズ不一致: {product.filename}（予定 {product.size:,} / 取得 {written:,} bytes）")
            if checksum.hexdigest() != product.md5:
                restart = True
                raise RetryTransfer(f"MD5 不一致: {product.filename}。先頭から再取得します。")
            os.replace(partial, destination)
            print(f"[{product.filename}] 100% — サイズ・MD5 検証成功", flush=True)
            return "downloaded"
        except (RetryTransfer, RequestException) as exc:
            # Never log exception strings or redirect URLs from requests.
            if isinstance(exc, RequestException):
                if is_dns_error(exc):
                    raise DownloadError(f"名前解決（DNS）に失敗しました: {product.filename}。ASF / Earthdata に接続するネットワークの DNS 設定を確認してください。") from exc
                message = f"通信に失敗しました ({type(exc).__name__}): {product.filename}"
                if isinstance(exc, SSLError) or not isinstance(exc, (ConnectionError, Timeout, ChunkedEncodingError)):
                    raise DownloadError(message) from exc
            else:
                message = str(exc)
            if attempt == retries:
                raise DownloadError(message + "。自動再試行の上限に達しました。.part を残して停止します。") from exc
            print(f"[{product.filename}] 自動再試行 {attempt + 1}/{retries}: {message}", flush=True)
            retry_pause(attempt, stop)


def fetch_parallel(products, output_dir, jobs, replace_invalid, manifest, manifest_path, retries=3):
    """Each worker owns its session; only the coordinator updates the manifest."""
    stop = threading.Event()
    counts = {"downloaded": 0, "skipped": 0, "recovered": 0}
    pending = {}
    next_index = 0
    failure = None
    pool = ThreadPoolExecutor(max_workers=min(jobs, len(products)))

    def worker(product):
        check_cancelled(stop)
        try:
            with create_session() as session:
                return fetch_product(product, output_dir, session,
                                     replace_invalid=replace_invalid, stop=stop, retries=retries)
        except BaseException:
            stop.set()
            raise

    def collect(future, index):
        nonlocal failure
        try:
            result = future.result()
            manifest["products"][index]["status"] = result
            counts[result] += 1
        except DownloadCancelled:
            manifest["products"][index]["status"] = "cancelled"
        except BaseException as exc:
            manifest["products"][index]["status"] = "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed"
            stop.set()
            if failure is None:
                failure = exc

    try:
        while next_index < len(products) or pending:
            while not stop.is_set() and next_index < len(products) and len(pending) < jobs:
                index = next_index
                next_index += 1
                print(f"[{index + 1}/{len(products)}] {products[index].filename}", flush=True)
                manifest["products"][index]["status"] = "checking_or_downloading"
                write_manifest(manifest_path, manifest)
                pending[pool.submit(worker, products[index])] = index
            if not pending:
                break
            done, _ = wait(pending, return_when=FIRST_COMPLETED)
            for future in done:
                collect(future, pending.pop(future))
            write_manifest(manifest_path, manifest)
    except BaseException as exc:
        failure = exc
        stop.set()
    finally:
        # Keep locks and logs open until every active writer has stopped.
        stop.set()
        pool.shutdown(wait=True)
        for future, index in pending.items():
            collect(future, index)
        write_manifest(manifest_path, manifest)
    if failure is not None:
        raise failure
    return counts


def ensure_input_unchanged(batch: Batch) -> None:
    if batch.path.is_symlink() or not batch.path.is_file() or batch.path.read_bytes() != batch.content:
        raise DownloadError("実行中に入力ファイルが変更されました。入力 JSON の内容を確認して再実行してください。")


class Tee:
    """Write progress to the terminal and the per-run UTF-8 log."""

    def __init__(self, terminal, log, lock=None):
        self.terminal, self.log = terminal, log
        self.lock = lock or threading.RLock()

    def write(self, text):
        with self.lock:
            self.terminal.write(text)
            self.log.write(text)
            self.log.flush()
        return len(text)

    def flush(self):
        with self.lock:
            self.terminal.flush()
            self.log.flush()


@contextmanager
def input_lock(path: Path):
    import fcntl

    with path.open("rb") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise DownloadError("この入力 JSON は別のダウンロード処理で使用中です。") from exc
        yield


def add_arguments(parser):
    parser.add_argument("--retries", type=int, default=3, help="途中切断等の追加再試行回数（既定3、0で無効）")
    parser.add_argument("--jobs", type=int, default=1, help="同時に取得するファイル数（既定1、まず2を推奨）")
    parser.add_argument("config", type=Path, help="work_dir を指定した設定 YAML")
    parser.add_argument("json", type=Path, help="ASF GeoJSON ファイルを1個指定")
    parser.add_argument("-out", "--out", type=Path,
                        help="SLC ZIP の保存先を変更（既定: 作業ルート/input/slc）")
    parser.add_argument("--log-dir", type=Path,
                        help="ログ・実行記録の保存先（既定: 作業ルート/logs/downloads）")
    parser.add_argument("--dry-run", action="store_true",
                        help="入力と計画を表示。通信・書き込み・MD5 全件読込は行わない")
    parser.add_argument("--replace-invalid", action="store_true",
                        help="検証不一致の既存 ZIP を、再取得・検証成功後に置き換える")


def write_manifest(path, data):
    temp = path.with_suffix(".json.tmp")
    with temp.open("w", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)


def run(args) -> int:
    # Keep the transcript open while reporting errors, including Ctrl+C.
    contexts = ExitStack()
    manifest = None
    manifest_path = None
    try:
        retries = getattr(args, "retries", 3)
        if type(retries) is not int or retries < 0:
            raise InputError("--retries は0以上の整数で指定してください。")
        jobs = getattr(args, "jobs", 1)
        if type(jobs) is not int or jobs < 1:
            raise InputError("--jobs は1以上の整数で指定してください。")
        input_path = args.json.expanduser().absolute()
        batch = load_batch(input_path)
        from . import config
        settings, root = config.load(args.config)
        work_dir = require_initialized(root, settings["directories"])
        output_dir = (args.out or work_dir / settings["paths"]["slc"]).expanduser().resolve()
        log_dir = (args.log_dir or work_dir / settings["paths"]["logs"] / "downloads").expanduser().resolve()
        for path in (output_dir, log_dir):
            if path.exists() and not path.is_dir():
                raise InputError(f"ディレクトリではありません: {path}")
        for product in batch.products:
            check_file_slot(output_dir / product.filename)
            check_file_slot(output_dir / (product.filename + ".part"))
        if args.dry_run:
            print_plan(batch, output_dir)
            print(f"最大同時取得数: {jobs}")
            print("入力 JSON は元の場所に残します。")
            print(f"ログ保存先: {log_dir}")
            print("DRY RUN: 通信・ファイル変更は行いません。既存 ZIP の MD5 検証は実行時に行います。")
            return 0

        contexts.enter_context(input_lock(batch.path))
        ensure_input_unchanged(batch)
        output_dir.mkdir(parents=True, exist_ok=True)
        contexts.enter_context(batch_lock(output_dir))
        log_dir.mkdir(parents=True, exist_ok=True)
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "_" + uuid4().hex[:8]
        log_path = log_dir / f"slc_{run_id}.log"
        log = contexts.enter_context(log_path.open("x", encoding="utf-8"))
        log_lock = threading.RLock()
        contexts.enter_context(redirect_stdout(Tee(sys.stdout, log, log_lock)))
        contexts.enter_context(redirect_stderr(Tee(sys.stderr, log, log_lock)))
        manifest_path = log_dir / f"slc_{run_id}.json"
        manifest = {
            "schema_version": 1, "status": "running", "jobs": jobs, "retries": retries, "input": str(batch.path),
            "input_sha256": hashlib.sha256(batch.content).hexdigest(),
            "output": str(output_dir), "work_dir": str(work_dir),
            "started_at": datetime.now(timezone.utc).isoformat(),
            "products": [
                {"filename": p.filename, "bytes": p.size, "md5": p.md5,
                 "url": urlsplit(p.url)._replace(query="", fragment="").geturl(),
                 "status": "pending"} for p in batch.products
            ],
        }
        write_manifest(manifest_path, manifest)
        print_plan(batch, output_dir)
        print("入力 JSON は元の場所に残します。")
        print(f"ログ: {log_path}")
        print(f"実行記録: {manifest_path}")
        counts = {"downloaded": 0, "skipped": 0, "recovered": 0}
        print(f"最大同時取得数: {jobs}", flush=True)
        if jobs == 1:
            session = contexts.enter_context(create_session())
            for i, product in enumerate(batch.products):
                print(f"[{i + 1}/{len(batch.products)}] {product.filename}", flush=True)
                manifest["products"][i]["status"] = "checking_or_downloading"
                write_manifest(manifest_path, manifest)
                try:
                    result = fetch_product(product, output_dir, session, replace_invalid=args.replace_invalid, retries=retries)
                except (DownloadError, OSError, KeyboardInterrupt) as exc:
                    manifest["products"][i]["status"] = "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed"
                    raise
                counts[result] += 1
                manifest["products"][i]["status"] = result
                write_manifest(manifest_path, manifest)
        else:
            counts = fetch_parallel(batch.products, output_dir, jobs, args.replace_invalid, manifest, manifest_path, retries=retries)
        ensure_input_unchanged(batch)
        manifest["status"] = "complete"
        manifest["finished_at"] = datetime.now(timezone.utc).isoformat()
        write_manifest(manifest_path, manifest)
        print(f"完了: 新規 {counts['downloaded']} / 取得済み {counts['skipped']} / 一時ファイルから復元 {counts['recovered']}")
        print(f"入力 JSON を保持: {batch.path}")
        return 0
    except (InputError, WorkspaceError, DownloadError, OSError, KeyboardInterrupt) as exc:
        code = 130 if isinstance(exc, KeyboardInterrupt) else 2 if isinstance(exc, (InputError, WorkspaceError)) else 1
        message = "中断しました。検証済み製品は再実行時に再利用できます。" if code == 130 else str(exc)
        print(f"ERROR: {message}", file=sys.stderr)
        if manifest is not None:
            manifest["status"] = "interrupted" if code == 130 else "failed"
            manifest["error"] = message
            manifest["finished_at"] = datetime.now(timezone.utc).isoformat()
            try:
                write_manifest(manifest_path, manifest)
            except OSError:
                print("ERROR: 実行記録を保存できませんでした。", file=sys.stderr)
        return code
    finally:
        contexts.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description="ASF GeoJSON から Sentinel-1 IW SLC を取得します。")
    add_arguments(parser)
    return run(parser.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
