"""Project configuration shared by local commands and the Docker launcher."""

import json
import os
from pathlib import Path, PurePosixPath
import sys

import yaml

from .workspace import WorkspaceError


DEFAULT_PATHS = {
    "slc": "input/slc", "orbit": "input/orbit", "aux": "input/aux",
    "processing": "processing", "output": "output", "logs": "logs",
}


def read(path):
    try:
        document = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (yaml.YAMLError, UnicodeError) as exc:
        raise WorkspaceError(f"設定 YAML を読み込めません: {path}") from exc
    if not isinstance(document, dict):
        raise WorkspaceError("設定 YAML はマッピング形式で指定してください。")
    if type(document.get("schema_version")) is not int or document["schema_version"] != 1:
        raise WorkspaceError("schema_version: 1 を指定してください。")
    root = document.get("work_dir")
    if not isinstance(root, str) or not root.strip() or "\x00" in root:
        raise WorkspaceError("work_dir に作業ルートのパスを指定してください。")
    paths = document.get("paths", {})
    if not isinstance(paths, dict):
        raise WorkspaceError("paths はマッピング形式で指定してください。")
    # Read older project configurations without moving their directories.
    if "scratch" in paths:
        if "processing" in paths:
            raise WorkspaceError("paths.scratch と paths.processing は同時に指定できません。")
        paths = dict(paths)
        paths["processing"] = paths.pop("scratch")
    unknown = set(paths) - set(DEFAULT_PATHS) - {"dem"}
    if unknown:
        raise WorkspaceError(f"未対応の paths キー: {', '.join(map(str, unknown))}")
    normalized = dict(DEFAULT_PATHS)
    for key, value in paths.items():
        if key == "dem" and value is None:
            continue
        if (not isinstance(value, str) or not value.strip() or "\x00" in value
                or "\\" in value or value.startswith("~")
                or PurePosixPath(value).is_absolute()
                or ".." in PurePosixPath(value).parts or not PurePosixPath(value).parts):
            raise WorkspaceError(f"paths.{key} は work_dir 内の相対パスで指定してください。")
        normalized[key] = str(PurePosixPath(value))
    # DEM is a future input file, not a directory to create.
    directories = list(dict.fromkeys([
        *[normalized[key] for key in DEFAULT_PATHS], "input/dem", "config",
    ]))
    return {"work_dir": root, "paths": normalized, "directories": directories}


def resolve_work_dir(settings, config_path):
    root = Path(settings["work_dir"]).expanduser()
    if not root.is_absolute():
        root = Path(config_path).resolve().parent / root
    return root.resolve()


def load(path):
    settings = read(path)
    # Set only by the host launcher, after binding the configured host directory.
    override = os.environ.get("SENTINEL_STACK_RUNTIME_WORK_DIR")
    root = Path(override).resolve() if override else resolve_work_dir(settings, path)
    return settings, root


def main():
    try:
        print(json.dumps(read(sys.argv[1]), ensure_ascii=False))
        return 0
    except (WorkspaceError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
