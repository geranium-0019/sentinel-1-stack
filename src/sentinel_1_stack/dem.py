"""Derive DEM bounds from SAFE manifests, fetch SRTM, then run ISCE2 locally."""

from contextlib import ExitStack, redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from uuid import uuid4
import xml.etree.ElementTree as ET
import zipfile

import requests

from . import config
from .orbit import discover
from .slc import (DownloadError, InputError, Tee, batch_lock, check_file_slot,
                  is_dns_error, request_product, write_manifest)
from .workspace import WorkspaceError, require_initialized

SOURCE = "https://step.esa.int/auxdata/dem/SRTMGL1/"
SAMPLES = 3601
HGT_BYTES = SAMPLES * SAMPLES * 2
MANIFEST_LIMIT = 4 * 1024 * 1024
ZIP_LIMIT = 40 * 1024 * 1024
FILL_EXAMPLE = (
    "未配布タイル・欠損画素を0m（EGM96高）で補完して進める場合の実行例:\n"
    "  scripts/run.sh dem config/project.yaml --fill-missing-zero\n"
    "config/project.yaml は使用している設定ファイルに置き換えてください。"
    "陸域の欠損も0mになるため、対象の範囲を確認してください。"
)


def xml_root(data):
    if len(data) > MANIFEST_LIMIT:
        raise InputError("manifest.safe がサイズ上限を超えています。")
    try:
        text = data.decode("utf-8")
        if "\x00" in text or re.search(r"<!\s*(DOCTYPE|ENTITY)", text, re.I):
            raise ValueError("DTD / ENTITY は使用できません")
        return ET.fromstring(text)
    except (UnicodeError, ValueError, ET.ParseError) as exc:
        raise InputError("manifest.safe の XML が不正です。") from exc


def footprint(data):
    points = []
    for element in xml_root(data).iter():
        if element.tag.rsplit("}", 1)[-1] != "footPrint":
            continue
        if not element.get("srsName", "").endswith(("#4326", ":4326", "/4326")):
            raise InputError("manifest.safe の座標系 EPSG:4326 を確認できません。")
        for node in element:
            if node.tag.rsplit("}", 1)[-1] == "coordinates":
                try:
                    ring = [tuple(map(float, token.split(","))) for token in (node.text or "").split()]
                except ValueError as exc:
                    raise InputError("manifest.safe の座標が不正です。") from exc
                if len(ring) < 4 or any(len(p) != 2 or not all(math.isfinite(v) for v in p)
                                       or not -90 <= p[0] <= 90 or not -180 <= p[1] <= 180 for p in ring):
                    raise InputError("manifest.safe の緯度・経度が不正です。")
                points.extend(ring)  # Sentinel-1 SAFE coordinates are latitude,longitude.
    if not points:
        raise InputError("manifest.safe に画像範囲の座標がありません。")
    south, north = min(p[0] for p in points), max(p[0] for p in points)
    west, east = min(p[1] for p in points), max(p[1] for p in points)
    if south >= north or west >= east:
        raise InputError("画像範囲の面積が0です。")
    if east - west > 180:
        raise InputError("日付変更線をまたぐ画像範囲には現在対応していません。")
    return [south, north, west, east]


def scene_manifest(directory, scene):
    safe = directory / (scene.name + ".SAFE")
    archive = directory / (scene.name + ".zip")
    if safe.is_dir():
        path = safe / "manifest.safe"
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MANIFEST_LIMIT:
            raise InputError(f"有効な manifest.safe がありません: {safe}")
        return path.read_bytes()
    try:
        with zipfile.ZipFile(archive) as source:
            name = scene.name + ".SAFE/manifest.safe"
            matches = [item for item in source.infolist() if item.filename == name]
            if len(matches) != 1 or matches[0].file_size > MANIFEST_LIMIT:
                raise InputError(f"有効な manifest.safe が ZIP にありません: {archive.name}")
            return source.read(matches[0])
    except (zipfile.BadZipFile, RuntimeError) as exc:
        raise InputError(f"SLC ZIP を読み込めません: {archive.name}") from exc


def tile_name(lat, lon):
    return f"{'N' if lat >= 0 else 'S'}{abs(lat):02d}{'E' if lon >= 0 else 'W'}{abs(lon):03d}"


def make_plan(directory, margin):
    if not math.isfinite(margin) or not 0 <= margin <= 1:
        raise InputError("--margin は0〜1度で指定してください。")
    scenes = discover(directory)
    inputs = []
    for scene in scenes:
        data = scene_manifest(directory, scene)
        inputs.append({"scene": scene.name, "manifest_sha256": hashlib.sha256(data).hexdigest(),
                       "bbox_snwe": footprint(data)})
    bounds = [min(p["bbox_snwe"][0] for p in inputs), max(p["bbox_snwe"][1] for p in inputs),
              min(p["bbox_snwe"][2] for p in inputs), max(p["bbox_snwe"][3] for p in inputs)]
    south, north = math.floor(bounds[0] - margin), math.ceil(bounds[1] + margin)
    west, east = math.floor(bounds[2] - margin), math.ceil(bounds[3] + margin)
    if south < -56 or north > 60:
        raise InputError("画像範囲と余白が SRTM の対応緯度（南緯56度〜北緯60度）を超えます。")
    if west < -180 or east > 180 or east - west > 180:
        raise InputError("日付変更線をまたぐ取得範囲には現在対応していません。")
    tiles = [tile_name(lat, lon) for lat in range(south, north) for lon in range(west, east)]
    if len(tiles) > 100:
        raise InputError(f"取得範囲が大きすぎます（{len(tiles)} タイル）。SLC の対象を分けてください。")
    return {"inputs": inputs, "image_bbox_snwe": bounds, "margin_degrees": margin,
            "bbox_snwe": [south, north, west, east], "tiles": tiles,
            "bundle": f"srtm1_{tile_name(south, west)}_{tile_name(north, east)}"}


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def available_tiles(session):
    with request_product(session, SimpleNamespace(url=SOURCE)) as response:
        if response.status_code != 200:
            raise DownloadError(f"ESA DEM 一覧の取得に失敗しました: HTTP {response.status_code}")
        data = bytearray()
        for block in response.iter_content(chunk_size=1024 * 1024):
            data.extend(block)
            if len(data) > 8 * 1024 * 1024:
                raise DownloadError("ESA DEM 一覧がサイズ上限を超えました。")
    names = set(re.findall(rb"([NS]\d{2}[EW]\d{3})\.SRTMGL1\.hgt\.zip", data))
    if not names:
        raise DownloadError("ESA DEM 一覧にタイル名がありません。")
    return {name.decode("ascii") for name in names}


def validate_tile(path, name):
    import numpy as np

    check_file_slot(path)
    if path.stat().st_size > ZIP_LIMIT:
        raise DownloadError(f"DEM ZIP がサイズ上限を超えています: {path.name}")
    try:
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
            if len(members) != 1 or members[0].filename != name + ".hgt" or members[0].file_size != HGT_BYTES:
                raise ValueError("HGT 名・サイズが不正です")
            data = archive.read(members[0])  # CRC checked by ZipFile.
        heights = np.frombuffer(data, dtype=">i2")
        missing = int(np.count_nonzero(heights == -32768))
        return data, missing
    except (zipfile.BadZipFile, RuntimeError, ValueError) as exc:
        raise DownloadError(f"DEM タイル検証失敗: {path.name}") from exc


def fetch_tile(session, cache, name):
    path = cache / (name + ".SRTMGL1.hgt.zip")
    partial = path.with_suffix(path.suffix + ".part")
    check_file_slot(path)
    check_file_slot(partial)
    if path.exists():
        data, missing = validate_tile(path, name)
        return path, data, missing
    with request_product(session, SimpleNamespace(url=SOURCE + path.name)) as response:
        if response.status_code != 200:
            # HTTP/network failures must never be turned into a synthetic terrain tile.
            raise DownloadError(f"DEM タイル取得 HTTP {response.status_code}: {name}")
        written = 0
        with partial.open("wb") as stream:
            for block in response.iter_content(chunk_size=1024 * 1024):
                written += len(block)
                if written > ZIP_LIMIT:
                    raise DownloadError(f"DEM ZIP がサイズ上限を超えました: {name}")
                stream.write(block)
            stream.flush()
            os.fsync(stream.fileno())
    data, missing = validate_tile(partial, name)
    os.replace(partial, path)
    return path, data, missing


def stage_tiles(session, names, unavailable, cache, staging, fill_zero):
    import numpy as np

    records = []
    for index, name in enumerate(names):
        print(f"[{index + 1}/{len(names)}] {name}", flush=True)
        target = staging / (name + ".SRTMGL1.hgt.zip")
        if name in unavailable:
            if not fill_zero:
                raise DownloadError("未配布タイルがあります。\n" + FILL_EXAMPLE)
            data, missing = bytes(HGT_BYTES), SAMPLES * SAMPLES
            record = {"tile": name, "source": "synthetic_zero", "filled_pixels": missing}
        else:
            cached, data, missing = fetch_tile(session, cache, name)
            record = {"tile": name, "source": SOURCE + cached.name, "sha256": sha256(cached),
                      "filled_pixels": missing}
            if missing and not fill_zero:
                raise DownloadError(f"{name} に欠損画素が {missing} 個あります。\n" + FILL_EXAMPLE)
            if not missing:
                shutil.copyfile(cached, target)
                records.append(record)
                continue
            heights = np.frombuffer(data, dtype=">i2").copy()
            heights[heights == -32768] = 0
            data = heights.tobytes()
        print(f"  0m（EGM96）で補完: {missing:,} 画素", flush=True)
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
            archive.writestr(name + ".hgt", data)
        records.append(record)
    return records


def run_dem_py(staging, bounds):
    executable = shutil.which("dem.py")
    if executable is None:
        raise DownloadError("ISCE2 の dem.py がありません。")
    command = [sys.executable, executable, "-a", "stitch", "-b", *map(str, bounds),
               "-s", "1", "-l", "-k", "-r", "-c", "-o", "dem", "-d", "."]
    print("実行: " + shlex.join(command), flush=True)
    with subprocess.Popen(command, cwd=staging, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, text=True, bufsize=1) as process:
        try:
            for line in process.stdout:
                print(line, end="", flush=True)
            status = process.wait()
        except BaseException:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            raise
    if status != 0:
        raise DownloadError(f"dem.py が失敗しました（終了コード {status}）。")


def validate_dem(directory, bounds):
    import isce  # Set up ISCE2's component import paths.
    import isceobj
    from osgeo import gdal

    gdal.UseExceptions()
    path = directory / "dem.wgs84"
    for suffix in ("", ".xml", ".vrt"):
        item = Path(str(path) + suffix)
        check_file_slot(item)
        if not item.is_file():
            raise DownloadError(f"DEM 出力がありません: {item.name}")
    image = isceobj.createDemImage()
    try:
        image.load(str(path) + ".xml")
    except (ValueError, RuntimeError, ET.ParseError) as exc:
        raise DownloadError("DEM の ISCE2 メタデータを読み込めません。") from exc
    south, north, west, east = bounds
    width, height = (east - west) * 3600, (north - south) * 3600
    if (image.reference != "WGS84" or image.width != width or image.length != height
            or image.dataType.upper() != "SHORT" or path.stat().st_size != width * height * 2):
        raise DownloadError("DEM の基準面・寸法・ファイルサイズが一致しません。")
    try:
        dataset = gdal.Open(str(path) + ".vrt")
        expected = (west, 1 / 3600, 0, north, 0, -1 / 3600)
        if (dataset.RasterXSize != width or dataset.RasterYSize != height
                or any(abs(a - b) > 1e-9 for a, b in zip(dataset.GetGeoTransform(), expected))):
            raise DownloadError("DEM の座標範囲が取得範囲と一致しません。")
        for row in range(0, height, 256):
            data = dataset.GetRasterBand(1).ReadAsArray(0, row, width, min(256, height - row))
            if (data == -32768).any():
                raise DownloadError("作成された DEM に未処理の欠損値があります。")
    except RuntimeError as exc:
        raise DownloadError("作成された DEM を GDAL で読み込めません。") from exc


def relocate_metadata(staging, final):
    # dem.py stores absolute paths in XML; VRT already uses a relative raw filename.
    path = staging / "dem.wgs84.xml"
    tree = ET.parse(path)
    for prop in tree.getroot().iter("property"):
        key = prop.get("name", "").upper().replace("_", "")
        value = prop.find("value")
        if value is not None and key in ("FILENAME", "METADATALOCATION"):
            value.text = str(final / "dem.wgs84") + (".xml" if key == "METADATALOCATION" else "")
    tree.write(path, encoding="unicode")


def existing_bundle(final, plan, fill_zero):
    if not final.exists() and not final.is_symlink():
        return None
    if final.is_symlink() or not final.is_dir():
        raise DownloadError(f"DEM 保存先が通常のディレクトリではありません: {final}")
    record_path = final / "dem.json"
    check_file_slot(record_path)
    try:
        record = json.loads(record_path.read_text())
        if record["bbox_snwe"] != plan["bbox_snwe"] or record["source"] != SOURCE:
            raise ValueError("source/bounds mismatch")
        if not fill_zero and any(t["filled_pixels"] for t in record["tiles"]):
            raise DownloadError("既存 DEM は0m補完を含みます。\n" + FILL_EXAMPLE)
        for name in ("dem.wgs84", "dem.wgs84.xml", "dem.wgs84.vrt"):
            check_file_slot(final / name)
            if sha256(final / name) != record["files"][name]:
                raise ValueError("checksum mismatch")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise DownloadError(f"既存 DEM の記録・検証に失敗しました。上書きせず停止します: {final}") from exc
    validate_dem(final, plan["bbox_snwe"])
    return record


def add_arguments(parser):
    parser.add_argument("config", type=Path, help="work_dir を指定した設定 YAML")
    parser.add_argument("--out", "-out", type=Path, help="DEM 保存ディレクトリ（既定: input/dem）")
    parser.add_argument("--log-dir", type=Path, help="ログ保存ディレクトリ")
    parser.add_argument("--margin", type=float, default=0.1, help="画像範囲に加える余白（度、既定: 0.1）")
    parser.add_argument("--fill-missing-zero", action="store_true", help="未配布タイル・欠損画素を0m（EGM96）で補完")
    parser.add_argument("--dry-run", action="store_true", help="範囲と取得計画を表示。配布一覧の問い合わせのみ、保存なし")


def run(args):
    contexts = ExitStack()
    record = record_path = None
    try:
        settings, root = config.load(args.config)
        root = require_initialized(root, settings["directories"])
        plan = make_plan(root / settings["paths"]["slc"], args.margin)
        output = (args.out or root / "input/dem").expanduser().resolve()
        logs = (args.log_dir or root / settings["paths"]["logs"] / "dem").expanduser().resolve()
        final = output / plan["bundle"]
        print(f"対象 SLC: {len(plan['inputs'])}\n画像全体の範囲 [南 北 西 東]: {plan['image_bbox_snwe']}")
        print(f"余白: {args.margin} 度\nDEM 取得範囲 [南 北 西 東]: {plan['bbox_snwe']}")
        print(f"SRTM 1秒角タイル: {len(plan['tiles'])} 枚\n出力: {final / 'dem.wgs84'}", flush=True)
        for path in (output, logs):
            if path.exists() and not path.is_dir():
                raise InputError(f"ディレクトリではありません: {path}")
        if not args.dry_run:
            output.mkdir(parents=True, exist_ok=True)
            contexts.enter_context(batch_lock(output))
            logs.mkdir(parents=True, exist_ok=True)
            run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "_" + uuid4().hex[:8]
            transcript = contexts.enter_context((logs / f"dem_{run_id}.log").open("x"))
            contexts.enter_context(redirect_stdout(Tee(sys.stdout, transcript)))
            contexts.enter_context(redirect_stderr(Tee(sys.stderr, transcript)))
            record_path = logs / f"dem_{run_id}.json"
            record = {**plan, "status": "running", "source": SOURCE, "output": str(final),
                      "fill_missing_zero": args.fill_missing_zero,
                      "started_at": datetime.now(timezone.utc).isoformat()}
            write_manifest(record_path, record)
            print(f"ログ: {logs / f'dem_{run_id}.log'}\nDEM 取得範囲: {plan['bbox_snwe']}")
        existing = existing_bundle(final, plan, args.fill_missing_zero)
        if existing is not None:
            print("既存 DEM の検証成功: 再利用します。")
        else:
            session = contexts.enter_context(requests.Session())
            available = available_tiles(session)
            unavailable = [name for name in plan["tiles"] if name not in available]
            print("取得タイル: " + ", ".join(plan["tiles"]))
            if unavailable:
                print("未配布タイル: " + ", ".join(unavailable))
                if not args.fill_missing_zero:
                    raise DownloadError("DEM の配布範囲に欠けがあります。\n" + FILL_EXAMPLE)
            if args.dry_run:
                print("DRY RUN: 範囲と配布一覧の確認のみ。DEM・ログは保存しません。")
                return 0
            cache = output / "tiles"
            if cache.is_symlink():
                raise InputError("DEM タイルキャッシュのシンボリックリンクは使用できません。")
            cache.mkdir(exist_ok=True)
            required = len(plan["tiles"]) * HGT_BYTES * 4
            if shutil.disk_usage(output).free < required:
                raise DownloadError(f"DEM 作成の空き容量が不足しています（必要目安: {required:,} bytes）。")
            with tempfile.TemporaryDirectory(prefix=".dem-build-", dir=output) as temporary:
                staging = Path(temporary)
                tiles = stage_tiles(session, plan["tiles"], unavailable, cache, staging, args.fill_missing_zero)
                run_dem_py(staging, plan["bbox_snwe"])
                validate_dem(staging, plan["bbox_snwe"])
                relocate_metadata(staging, final)
                # Publish a whole bundle, including a completion record, in one rename.
                bundle = staging / "publish"
                bundle.mkdir()
                for name in ("dem.wgs84", "dem.wgs84.xml", "dem.wgs84.vrt"):
                    shutil.move(str(staging / name), bundle / name)
                details = {**plan, "source": SOURCE, "vertical_reference": "WGS84", "tiles": tiles,
                           "files": {p.name: sha256(p) for p in bundle.iterdir()}}
                write_manifest(bundle / "dem.json", details)
                if final.exists() or final.is_symlink():
                    raise DownloadError(f"処理中に出力先が作成されました。上書きせず停止します: {final}")
                bundle.rename(final)
            print("DEM の取得・結合・WGS84 楕円体高への変換が完了しました。")
        if args.dry_run:
            print("DRY RUN: 既存 DEM の確認のみ。ファイル変更なし。")
            return 0
        record.update(status="complete", reused=existing is not None,
                      finished_at=datetime.now(timezone.utc).isoformat())
        write_manifest(record_path, record)
        dem_path = final / "dem.wgs84"
        print(f"DEM: {dem_path}\n設定ファイルは変更していません。")
        if dem_path.is_relative_to(root):
            print("処理で使う paths.dem の指定例:\n  dem: " + str(dem_path.relative_to(root)))
        else:
            print("paths.dem は work_dir 内の相対パスです。処理に使う際は DEM 一式を work_dir 配下へ配置してください。")
        return 0
    except (InputError, WorkspaceError, DownloadError, OSError, requests.RequestException,
            zipfile.BadZipFile, KeyboardInterrupt) as exc:
        code = 130 if isinstance(exc, KeyboardInterrupt) else 2 if isinstance(exc, (InputError, WorkspaceError)) else 1
        if isinstance(exc, KeyboardInterrupt):
            message = "中断しました。同じコマンドで再実行できます。"
        elif isinstance(exc, requests.RequestException):
            message = "DEM 取得の通信に失敗しました" + ("（DNS）" if is_dns_error(exc) else f"（{type(exc).__name__}）")
        else:
            message = str(exc)
        print(f"ERROR: {message}", file=sys.stderr)
        if record is not None:
            record.update(status="interrupted" if code == 130 else "failed", error=message,
                          finished_at=datetime.now(timezone.utc).isoformat())
            try:
                write_manifest(record_path, record)
            except OSError:
                print("ERROR: 実行記録を保存できませんでした。", file=sys.stderr)
        return code
    finally:
        contexts.close()
