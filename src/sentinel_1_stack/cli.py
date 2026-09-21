"""Command line entry point; each operation is explicitly requested."""

import argparse
from pathlib import Path
import sys

from . import config, dem, export, orbit, prepare, runner, slc, workspace


def main(argv=None):
    parser = argparse.ArgumentParser(prog="sentinel-1-stack")
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="ISCE2 用の作業ディレクトリを作成")
    init.add_argument("config", type=Path, help="work_dir を指定した設定 YAML")
    download = commands.add_parser("download", aliases=["download-slc"],
                                   help="ASF GeoJSON の SLC を input/slc に取得")
    slc.add_arguments(download)
    orbit_parser = commands.add_parser("orbit", help="取得済み SLC に対応する ASF 軌道を取得")
    orbit.add_arguments(orbit_parser)
    dem_parser = commands.add_parser("dem", help="SLC の画像範囲から DEM を取得・作成")
    dem.add_arguments(dem_parser)
    prepare_parser = commands.add_parser("prepare", help="ISCE2 処理スクリプトを生成（実処理なし）")
    prepare.add_arguments(prepare_parser)
    run_parser = commands.add_parser("run", help="生成済み ISCE2 工程を実行")
    runner.add_arguments(run_parser)
    export_parser = commands.add_parser("export", help="品質確認と位相・コヒーレンスのGeoTIFF出力")
    export.add_arguments(export_parser)
    args = parser.parse_args(argv)
    if args.command == "init":
        try:
            settings, root = config.load(args.config)
            workspace.initialize(root, settings["directories"])
            print(f"SLC の既定保存先: {root / settings['paths']['slc']}")
            return 0
        except (workspace.WorkspaceError, OSError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2 if isinstance(exc, workspace.WorkspaceError) else 1
    if args.command == "export":
        return export.run(args)
    if args.command == "run":
        return runner.run(args)
    if args.command == "prepare":
        return prepare.run(args)
    if args.command == "dem":
        return dem.run(args)
    return orbit.run(args) if args.command == "orbit" else slc.run(args)


if __name__ == "__main__":
    raise SystemExit(main())
