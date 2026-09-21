"""Create and check the explicit ISCE2 project directory layout."""

from pathlib import Path


DIRECTORIES = (
    "input/slc", "input/orbit", "input/dem", "input/aux",
    "config", "processing", "output", "logs",
)


class WorkspaceError(ValueError):
    pass


def check_layout(directory: Path, directories=DIRECTORIES) -> Path:
    root = directory.expanduser().resolve()
    # Preflight all parents before creating anything, preserving existing data.
    for name in directories:
        path = root / name
        if not path.resolve().is_relative_to(root):
            raise WorkspaceError(f"作業ルート外へのリンクがあります: {path}")
        for parent in (path, *path.parents):
            if (parent.exists() or parent.is_symlink()) and not parent.is_dir():
                raise WorkspaceError(f"ディレクトリの場所にファイルがあります: {parent}")
    return root


def initialize(directory: Path, directories=DIRECTORIES) -> Path:
    root = check_layout(directory, directories)
    print(f"作成先の作業ルート: {root}", flush=True)
    for name in directories:
        path = root / name
        existed = path.is_dir()
        path.mkdir(parents=True, exist_ok=True)
        print(f"{'既存' if existed else '作成'}: {path}")
    print(f"作業ルート: {root}")
    return root


def require_initialized(directory: Path, directories=DIRECTORIES) -> Path:
    root = check_layout(directory, directories)
    missing = [name for name in directories if not (root / name).is_dir()]
    if missing:
        raise WorkspaceError(
            f"作業ディレクトリが未準備です: {root}（不足: {', '.join(missing)}）。"
            "先に init を実行してください。"
        )
    return root
