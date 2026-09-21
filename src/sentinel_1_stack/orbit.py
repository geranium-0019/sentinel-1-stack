"""Fetch validated ASF orbits for completed SLCs, as an explicit preparation step.

Acquisition/validity matching follows the local fetchOrbit_asf.py reference.
The current ASF scene API supplies the preferred file from its public S3 bucket.
"""

from contextlib import ExitStack, redirect_stderr, redirect_stdout
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import math
import os
from pathlib import Path
import re
import sys
from types import SimpleNamespace
from urllib.parse import urlsplit
from uuid import uuid4
import xml.etree.ElementTree as ET

import requests

from . import config
from .slc import (DownloadError, InputError, SLC_NAME, Tee, batch_lock,
                  check_file_slot, is_dns_error, request_product, write_manifest)
from .workspace import WorkspaceError, require_initialized

API = "https://s1-orbits.asf.alaska.edu/scene/"
BUCKET = "https://s1-orbits.s3.amazonaws.com"
MAX_EOF_BYTES = 16 * 1024 * 1024
MARGIN = timedelta(seconds=60)  # ISCE2 TOPS extractPreciseOrbit default margin.
EOF_NAME = re.compile(
    r"(S1[ABCD])_OPER_AUX_(POEORB|RESORB)_OPOD_(\d{8}T\d{6})_V"
    r"(\d{8}T\d{6})_(\d{8}T\d{6})\.EOF"
)


@dataclass(frozen=True)
class Scene:
    name: str
    satellite: str
    start: datetime
    stop: datetime


@dataclass(frozen=True)
class Orbit:
    name: str
    satellite: str
    kind: str
    generated: datetime
    start: datetime
    stop: datetime

    @property
    def url(self):
        return f"{BUCKET}/AUX_{self.kind}/{self.name}"

    def covers(self, scene):
        return (self.satellite == scene.satellite and self.start <= scene.start - MARGIN
                and self.stop >= scene.stop + MARGIN)


def stamp(text):
    return datetime.strptime(text, "%Y%m%dT%H%M%S")


def parse_orbit(name):
    match = EOF_NAME.fullmatch(name)
    if not match:
        raise InputError(f"軌道ファイル名が不正です: {name}")
    sat, kind, generated, start, stop = match.groups()
    try:
        orbit = Orbit(name, sat, kind, stamp(generated), stamp(start), stamp(stop))
    except ValueError as exc:
        raise InputError(f"軌道ファイル名の日時が不正です: {name}") from exc
    if orbit.start >= orbit.stop:
        raise InputError(f"軌道ファイルの有効期間が不正です: {name}")
    return orbit


def discover(directory):
    if not directory.is_dir():
        raise InputError(f"SLC ディレクトリがありません: {directory}")
    scenes = {}
    partials = 0
    for path in sorted(directory.iterdir()):
        if path.name.endswith(".part"):
            partials += 1
            continue
        if path.suffix not in (".zip", ".SAFE"):
            continue
        if path.is_symlink() or not (path.is_file() if path.suffix == ".zip" else path.is_dir()):
            raise InputError(f"SLC は通常の ZIP または SAFE ディレクトリを指定してください: {path}")
        name = path.stem
        if not SLC_NAME.fullmatch(name + ".zip"):
            raise InputError(f"Sentinel-1 IW SLC の名前ではありません: {path.name}")
        fields = name.split("_")
        try:
            scene = Scene(name, fields[0], stamp(fields[-5]), stamp(fields[-4]))
        except ValueError as exc:
            raise InputError(f"SLC の観測日時が不正です: {path.name}") from exc
        if scene.start >= scene.stop:
            raise InputError(f"SLC の観測期間が不正です: {path.name}")
        scenes[name] = scene  # A ZIP and its extracted SAFE refer to one scene.
    if partials:
        print(f"未完了の .part は対象外: {partials} 個。SLC 取得完了後に再実行してください。")
    if not scenes:
        raise InputError("取得済みの SLC ZIP / SAFE がありません。先に SLC を取得してください。")
    return list(scenes.values())


def validate_eof(path, orbit, scenes):
    check_file_slot(path)
    if path.stat().st_size > MAX_EOF_BYTES:
        raise DownloadError(f"EOF がサイズ上限を超えています: {path.name}")
    data = path.read_bytes()
    try:
        text = data.decode("utf-8")
        if "\x00" in text or re.search(r"<!\s*(DOCTYPE|ENTITY)", text, re.I):
            raise ValueError("DTD / ENTITY は使用できません")
        root = ET.fromstring(text)
        if root.tag != "Earth_Explorer_File":
            raise ValueError("EOF XML ではありません")
        header = root.find("Earth_Explorer_Header/Fixed_Header")
        if header is None:
            raise ValueError("ヘッダーがありません")
        expected = {"File_Name": orbit.name[:-4], "File_Type": "AUX_" + orbit.kind,
                    "Mission": "Sentinel-1" + orbit.satellite[-1]}
        for field, value in expected.items():
            if header.findtext(field, "").strip() != value:
                raise ValueError(f"{field} がファイル名と一致しません")
        variable = root.find("Earth_Explorer_Header/Variable_Header")
        if (variable is None or variable.findtext("Ref_Frame", "").strip() != "EARTH_FIXED"
                or variable.findtext("Time_Reference", "").strip() != "UTC"):
            raise ValueError("座標系 EARTH_FIXED / 時刻系 UTC を確認できません")
        def utc(value):
            if not value or not value.startswith("UTC="):
                raise ValueError("UTC 時刻が不正です")
            result = datetime.fromisoformat(value[4:])
            if result.tzinfo is not None:
                raise ValueError("EOF の UTC 時刻形式が不正です")
            return result
        start = utc(header.findtext("Validity_Period/Validity_Start"))
        stop = utc(header.findtext("Validity_Period/Validity_Stop"))
        if start != orbit.start or stop != orbit.stop:
            raise ValueError("XML の有効期間がファイル名と一致しません")
        vectors = root.find("Data_Block/List_of_OSVs")
        if vectors is None or len(vectors) < 4 or int(vectors.get("count", "0")) != len(vectors):
            raise ValueError("軌道ベクトル数が不正です")
        times, quality = [], []
        for vector in vectors:
            times.append(utc(vector.findtext("UTC")))
            quality.append(vector.findtext("Quality", "").strip())
            for field in ("X", "Y", "Z", "VX", "VY", "VZ"):
                node = vector.find(field)
                if node is None or node.get("unit") != ("m/s" if field.startswith("V") else "m"):
                    raise ValueError("軌道ベクトルの単位が不正です")
                if not math.isfinite(float(node.text)):
                    raise ValueError("軌道ベクトルに非数値があります")
        if any(a >= b for a, b in zip(times, times[1:])):
            raise ValueError("軌道ベクトルの時刻が昇順ではありません")
        for scene in scenes:
            if not orbit.covers(scene) or times[0] > scene.start - MARGIN or times[-1] < scene.stop + MARGIN:
                raise ValueError(f"観測期間（前後60秒を含む）を覆いません: {scene.name}")
            window = [i for i, t in enumerate(times) if scene.start - MARGIN <= t <= scene.stop + MARGIN]
            if len(window) < 4 or any(quality[i] != "NOMINAL" for i in window):
                raise ValueError("観測期間内の正常な軌道ベクトルが不足しています")
            # Include bracketing samples so a gap at a window edge is also caught.
            selected = times[max(window[0] - 1, 0): min(window[-1] + 2, len(times))]
            if any((b - a).total_seconds() > 60 for a, b in zip(selected, selected[1:])):
                raise ValueError("観測期間内の軌道ベクトルに欠損があります")
    except (ET.ParseError, UnicodeError, ValueError, TypeError) as exc:
        raise DownloadError(f"EOF 検証失敗: {path.name}（{exc}）") from exc
    return hashlib.sha256(data).hexdigest()


def resolve_orbit(session, scene):
    with request_product(session, SimpleNamespace(url=API + scene.name), allow_redirects=False) as response:
        if response.status_code == 404:
            raise DownloadError(f"ASF に対応する軌道がありません: {scene.name}")
        if response.status_code not in (301, 302, 303, 307, 308):
            raise DownloadError(f"ASF 軌道検索 HTTP {response.status_code}: {scene.name}")
        location = response.headers.get("Location", "")
    try:
        parsed = urlsplit(location)
    except ValueError as exc:
        raise DownloadError("ASF 軌道検索の転送先 URL が不正です。") from exc
    if parsed.scheme != "https" or parsed.netloc != "s1-orbits.s3.amazonaws.com" or parsed.query or parsed.fragment:
        raise DownloadError("ASF 軌道検索の転送先が想定外です。取得を停止します。")
    orbit = parse_orbit(parsed.path.rsplit("/", 1)[-1])
    if location != orbit.url or not orbit.covers(scene):
        raise DownloadError(f"ASF が返した軌道の衛星・有効期間が一致しません: {scene.name}")
    return orbit


def make_plan(scenes, output, session, allow_restituted):
    local = [parse_orbit(p.name) for p in output.glob("*.EOF") if EOF_NAME.fullmatch(p.name)]
    result = {}
    for scene in scenes:
        candidates = sorted((o for o in local if o.kind == "POEORB" and o.covers(scene)),
                            key=lambda o: (o.generated, o.name), reverse=True)
        orbit = candidates[0] if candidates else resolve_orbit(session, scene)
        if orbit.kind == "RESORB" and not allow_restituted:
            raise DownloadError(
                f"精密軌道 POEORB が未提供です: {scene.name}。\n"
                "精密軌道の公開を待って再実行してください。\n"
                "速報軌道（RESORB）を使って進める場合の実行例:\n"
                "  scripts/run.sh orbit config/project.yaml --allow-restituted\n"
                "config/project.yaml は、使用している設定ファイルのパスに置き換えてください。"
            )
        path = output / orbit.name
        check_file_slot(path)
        check_file_slot(path.with_suffix(".EOF.part"))
        if path.exists():
            validate_eof(path, orbit, [scene])
        print(f"{scene.name}\n  → {orbit.kind}: {orbit.name}（{'取得済み' if path.exists() else '取得予定'}）", flush=True)
        if orbit.name not in result:
            result[orbit.name] = {"orbit": orbit, "scenes": []}
        result[orbit.name]["scenes"].append(scene)
    return list(result.values())


def fetch_orbit(entry, output, session):
    orbit, scenes = entry["orbit"], entry["scenes"]
    path = output / orbit.name
    partial = path.with_suffix(".EOF.part")
    check_file_slot(path)
    check_file_slot(partial)
    if path.exists():
        return "skipped", validate_eof(path, orbit, scenes)
    with request_product(session, orbit, allow_redirects=False) as response:
        if response.status_code != 200:
            raise DownloadError(f"軌道取得 HTTP {response.status_code}: {orbit.name}")
        written = 0
        with partial.open("wb") as stream:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                written += len(chunk)
                if written > MAX_EOF_BYTES:
                    raise DownloadError(f"EOF がサイズ上限を超えました: {orbit.name}")
                stream.write(chunk)
            stream.flush()
            os.fsync(stream.fileno())
    digest = validate_eof(partial, orbit, scenes)
    os.replace(partial, path)
    return "downloaded", digest


def add_arguments(parser):
    parser.add_argument("config", type=Path, help="work_dir を指定した設定 YAML")
    parser.add_argument("--out", "-out", type=Path, help="軌道ファイルの保存先を変更")
    parser.add_argument("--log-dir", type=Path, help="ログ保存先を変更")
    parser.add_argument("--allow-restituted", action="store_true", help="精密軌道がない場合に速報軌道 RESORB を許可")
    parser.add_argument("--dry-run", action="store_true", help="ASF に問い合わせて計画を表示。EOF・ログは保存しない")


def run(args):
    contexts = ExitStack()
    manifest = manifest_path = None
    try:
        settings, root = config.load(args.config)
        root = require_initialized(root, settings["directories"])
        output = (args.out or root / settings["paths"]["orbit"]).expanduser().resolve()
        logs = (args.log_dir or root / settings["paths"]["logs"] / "orbits").expanduser().resolve()
        for path in (output, logs):
            if path.exists() and not path.is_dir():
                raise InputError(f"ディレクトリではありません: {path}")
        if not args.dry_run:
            output.mkdir(parents=True, exist_ok=True)
            contexts.enter_context(batch_lock(output))
            logs.mkdir(parents=True, exist_ok=True)
            run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "_" + uuid4().hex[:8]
            log_path = logs / f"orbits_{run_id}.log"
            stream = contexts.enter_context(log_path.open("x", encoding="utf-8"))
            contexts.enter_context(redirect_stdout(Tee(sys.stdout, stream)))
            contexts.enter_context(redirect_stderr(Tee(sys.stderr, stream)))
            manifest_path = logs / f"orbits_{run_id}.json"
            manifest = {"schema_version": 1, "status": "running", "work_dir": str(root),
                        "output": str(output), "allow_restituted": args.allow_restituted,
                        "started_at": datetime.now(timezone.utc).isoformat(), "orbits": []}
            write_manifest(manifest_path, manifest)
            print(f"ログ: {log_path}\n実行記録: {manifest_path}")
        scenes = discover(root / settings["paths"]["slc"])
        print(f"対象 SLC: {len(scenes)}\n軌道保存先: {output}")
        print("軌道方針: " + ("POEORB 優先・未提供時のみ RESORB" if args.allow_restituted else "POEORB のみ"))
        session = contexts.enter_context(requests.Session())
        plan = make_plan(scenes, output, session, args.allow_restituted)
        if args.dry_run:
            print(f"DRY RUN: 必要な軌道 {len(plan)} 個。問い合わせのみ、ファイル変更なし。")
            return 0
        manifest["orbits"] = [
            {"filename": e["orbit"].name, "type": e["orbit"].kind, "url": e["orbit"].url,
             "validity_start": e["orbit"].start.isoformat(), "validity_stop": e["orbit"].stop.isoformat(),
             "scenes": [s.name for s in e["scenes"]], "status": "pending"} for e in plan]
        write_manifest(manifest_path, manifest)
        for index, entry in enumerate(plan):
            print(f"[{index + 1}/{len(plan)}] {entry['orbit'].name}", flush=True)
            status, digest = fetch_orbit(entry, output, session)
            manifest["orbits"][index].update(status=status, sha256=digest)
            write_manifest(manifest_path, manifest)
            print(f"  {'取得・XML 検証成功' if status == 'downloaded' else '検証済み・再利用'}")
        manifest.update(status="complete", finished_at=datetime.now(timezone.utc).isoformat())
        write_manifest(manifest_path, manifest)
        print(f"完了: SLC {len(scenes)} 件に対応する軌道 {len(plan)} 個")
        return 0
    except (InputError, WorkspaceError, DownloadError, OSError, requests.RequestException, KeyboardInterrupt) as exc:
        code = 130 if isinstance(exc, KeyboardInterrupt) else 2 if isinstance(exc, (InputError, WorkspaceError)) else 1
        if isinstance(exc, KeyboardInterrupt):
            message = "中断しました。同じコマンドで再実行できます。"
        elif isinstance(exc, requests.RequestException):
            message = "ASF 軌道取得の通信に失敗しました" + ("（DNS）" if is_dns_error(exc) else f"（{type(exc).__name__}）")
        else:
            message = str(exc)
        print(f"ERROR: {message}", file=sys.stderr)
        if manifest is not None:
            manifest.update(status="interrupted" if code == 130 else "failed", error=message,
                            finished_at=datetime.now(timezone.utc).isoformat())
            try:
                write_manifest(manifest_path, manifest)
            except OSError:
                print("ERROR: 実行記録を保存できませんでした。", file=sys.stderr)
        return code
    finally:
        contexts.close()
