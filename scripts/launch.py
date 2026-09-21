"""Linux/WSL host launcher. Only Python's standard library is required here."""

import argparse
import csv
import io
import json
import os
from pathlib import Path
import subprocess
import sys

REPOSITORY = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPOSITORY / "src"))
from sentinel_1_stack.workspace import WorkspaceError, initialize, require_initialized


def bind(source, target, readonly=False):
    stream = io.StringIO()
    fields = ["type=bind", f"source={source}", f"target={target}"]
    if readonly:
        fields.append("readonly")
    csv.writer(stream, lineterminator="").writerow(fields)
    return ["--mount", stream.getvalue()]


def file_path(value):
    path = value.expanduser().absolute()
    if path.is_symlink() or not path.is_file():
        raise WorkspaceError(f"通常のファイルを指定してください: {path}")
    return path.resolve()


def docker_base():
    return ["docker", "run", "--rm", "--init", "--platform", "linux/amd64",
            "--user", f"{os.getuid()}:{os.getgid()}"]


def output_binding(value, root, target):
    path = value.expanduser().resolve()
    if path.exists() and not path.is_dir():
        raise WorkspaceError(f"ディレクトリではありません: {path}")
    if path.is_relative_to(root):
        return [], str(Path("/work") / path.relative_to(root)), path
    # Bind the closest existing directory so dry-run never creates anything.
    ancestor = path
    while not ancestor.exists():
        ancestor = ancestor.parent
    if not ancestor.is_dir():
        raise WorkspaceError(f"ディレクトリではありません: {ancestor}")
    return bind(ancestor, target), str(Path(target) / path.relative_to(ancestor)), path


def main(argv=None):
    parser = argparse.ArgumentParser(description="設定 YAML の work_dir を使って ISCE2 環境を起動")
    parser.add_argument("--image", default="sentinel-1-stack:dev")
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="作業ディレクトリを作成（取得・処理なし）")
    init.add_argument("config", type=Path)
    download = commands.add_parser("download", aliases=["download-slc"], help="指定 JSON の SLC を取得")
    download.add_argument("config", type=Path)
    download.add_argument("json", type=Path, nargs="?", help="ASF JSON（省略時は設定YAMLと同じ場所から1件選択）")
    download.add_argument("--json", dest="json_file", type=Path, help="ASF JSONを明示指定")
    download.add_argument("-out", "--out", type=Path)
    download.add_argument("--log-dir", type=Path)
    download.add_argument("--dry-run", action="store_true")
    download.add_argument("--jobs", type=int, default=1, help="同時取得数（既定1）")
    download.add_argument("--replace-invalid", action="store_true")
    orbit = commands.add_parser("orbit", help="取得済み SLC に対応する ASF 軌道を取得")
    orbit.add_argument("config", type=Path)
    orbit.add_argument("-out", "--out", type=Path)
    orbit.add_argument("--log-dir", type=Path)
    orbit.add_argument("--dry-run", action="store_true", help="ASF に問い合わせて計画を表示。保存なし")
    orbit.add_argument("--allow-restituted", action="store_true", help="精密軌道がない場合に速報軌道を許可")
    dem = commands.add_parser("dem", help="SLC の画像範囲から DEM を取得・作成")
    dem.add_argument("config", type=Path)
    dem.add_argument("-out", "--out", type=Path)
    dem.add_argument("--log-dir", type=Path)
    dem.add_argument("--margin", type=float, default=0.1, help="画像範囲の余白（度、既定: 0.1）")
    dem.add_argument("--fill-missing-zero", action="store_true", help="未配布タイル・欠損画素を0m（EGM96）で補完")
    dem.add_argument("--dry-run", action="store_true", help="範囲と配布一覧の問い合わせのみ。保存なし")
    prepare = commands.add_parser("prepare", help="ISCE2 処理スクリプトを生成（実処理なし）")
    prepare.add_argument("config", type=Path)
    prepare.add_argument("--dry-run", action="store_true")
    run = commands.add_parser("run", help="ISCE2 工程を実行（既定はアンラップまで）")
    run.add_argument("config", type=Path)
    run.add_argument("--dry-run", action="store_true")
    run.add_argument("--resume", action="store_true")
    run.add_argument("--unwrap-jobs", type=int, default=1,
                     help="アンラップの最大同時実行数（既定1、再開時も変更可能）")
    export = commands.add_parser("export", help="位相・コヒーレンスを共通グリッドに出力")
    export.add_argument("config", type=Path)
    export.add_argument("--dry-run", action="store_true")
    export.add_argument("--include-unwrapped", action="store_true", help="完了済みアンラップ位相と連結成分も出力")
    args = parser.parse_args(argv)
    if args.command == "run" and args.unwrap_jobs < 1:
        parser.error("--unwrap-jobs は1以上の整数で指定してください。")
    if args.command in ("download", "download-slc") and args.jobs < 1:
        parser.error("--jobs は1以上の整数で指定してください。")
    try:
        config = file_path(args.config)
        request = None
        if args.command in ("download", "download-slc"):
            if args.json is not None and args.json_file is not None:
                raise WorkspaceError("JSONの位置引数と --json は同時に指定できません。")
            selected = args.json_file or args.json
            if selected is None:
                candidates = sorted(p for p in config.parent.iterdir()
                                    if p.suffix.lower() in {".json", ".geojson"}
                                    and (p.is_file() or p.is_symlink()))
                if len(candidates) != 1:
                    names = "、".join(p.name for p in candidates) or "なし"
                    raise WorkspaceError(
                        f"設定YAMLと同じディレクトリのJSONは1件必要です（{len(candidates)}件）: {config.parent}\n"
                        f"候補: {names}\n"
                        "JSONを1件置くか、対象を --json で指定してください。実行例:\n"
                        "  scripts/run.sh --image IMAGE download config/project.yaml --json /path/to/results.geojson\n"
                        "IMAGEと各パスは使用中のものに置き換えてください。")
                selected = candidates[0]
            request = file_path(selected)
            print(f"入力JSON: {request}", flush=True)
        base = docker_base()
        config_mount = bind(config, "/run/project.yaml", readonly=True)
        inspected = subprocess.run(
            base + config_mount + [args.image, "python", "-m", "sentinel_1_stack.config", "/run/project.yaml"],
            stdout=subprocess.PIPE, text=True, check=False,
        )
        if inspected.returncode:
            return inspected.returncode
        settings = json.loads(inspected.stdout)
        root = Path(settings["work_dir"]).expanduser()
        if not root.is_absolute():
            root = config.parent / root
        root = root.resolve()
        print(f"設定: {config}\n作業ルート（ホスト）: {root}", flush=True)
        if args.command == "init":
            initialize(root, settings["directories"])
            print(f"SLC の既定保存先: {root / settings['paths']['slc']}", flush=True)
            return 0
        require_initialized(root, settings["directories"])
        if args.command in ("prepare", "run", "export"):
            command = base + config_mount + bind(root, "/work", readonly=args.dry_run)
            command += ["--network", "none", "--workdir", "/work",
                        "--env", "SENTINEL_STACK_RUNTIME_WORK_DIR=/work",
                        "--env", "OPENBLAS_NUM_THREADS=1", "--env", "OMP_NUM_THREADS=2",
                        "--env", "MPLCONFIGDIR=/tmp/matplotlib",
                        args.image, "python", "-m", "sentinel_1_stack", args.command, "/run/project.yaml"]
            if args.dry_run:
                command.append("--dry-run")
            if getattr(args, "resume", False):
                command.append("--resume")
            if args.command == "run":
                command += ["--unwrap-jobs", str(args.unwrap_jobs)]
            if getattr(args, "include_unwrapped", False):
                command.append("--include-unwrapped")
            return subprocess.run(command, check=False).returncode
        is_orbit = args.command == "orbit"
        is_dem = args.command == "dem"
        is_download = not (is_orbit or is_dem)
        command = base + config_mount + bind(root, "/work")
        if is_download:
            command += bind(request, "/run/scenes.geojson", readonly=True)
        command += ["--workdir", "/work", "--env", "SENTINEL_STACK_RUNTIME_WORK_DIR=/work",
                    "--env", "MPLCONFIGDIR=/tmp/matplotlib", "--env", "OPENBLAS_NUM_THREADS=1",
                    "--env", f"OMP_NUM_THREADS={os.environ.get('OMP_NUM_THREADS', '2')}"]
        if "RES_OPTIONS" in os.environ:
            command += ["--env", "RES_OPTIONS"]
        options = []
        if is_download and args.jobs != 1:
            options += ["--jobs", str(args.jobs)]
        output_default = root / "input/dem" if is_dem else root / settings["paths"]["orbit" if is_orbit else "slc"]
        log_subdir = "dem" if is_dem else "orbits" if is_orbit else "downloads"
        for key, default in (("out", output_default),
                             ("log_dir", root / settings["paths"]["logs"] / log_subdir)):
            value = getattr(args, key)
            if value is not None:
                mounts, destination, host_path = output_binding(value, root, f"/overrides/{key}")
                command += mounts
                options += ["--" + key.replace("_", "-"), destination]
            else:
                host_path = default
            label = ("DEM 保存先" if is_dem else "軌道保存先" if is_orbit else "SLC 保存先") if key == "out" else "ログ保存先"
            print(f"{label}（ホスト）: {host_path}", flush=True)
        if args.dry_run:
            options.append("--dry-run")
        elif is_download:
            credentials = file_path(Path(os.environ.get("EARTHDATA_NETRC", "~/.netrc")))
            command += bind(credentials, "/run/secrets/earthdata.netrc", readonly=True)
            command += ["--env", "NETRC=/run/secrets/earthdata.netrc"]
        if getattr(args, "replace_invalid", False):
            options.append("--replace-invalid")
        if is_orbit and args.allow_restituted:
            options.append("--allow-restituted")
        if is_dem:
            options += ["--margin", str(args.margin)]
            if args.fill_missing_zero:
                options.append("--fill-missing-zero")
        command += [args.image, "python", "-m", "sentinel_1_stack",
                    "dem" if is_dem else "orbit" if is_orbit else "download", "/run/project.yaml"]
        if is_download:
            command.append("/run/scenes.geojson")
        command += options
        return subprocess.run(command, check=False).returncode
    except WorkspaceError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except (OSError, ValueError, KeyError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("中断しました。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
