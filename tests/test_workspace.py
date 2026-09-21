from contextlib import redirect_stdout, redirect_stderr
import io
from pathlib import Path
import tempfile
import unittest

from sentinel_1_stack.cli import main
from sentinel_1_stack.workspace import DIRECTORIES


class WorkspaceTests(unittest.TestCase):
    def test_init_creates_tree_and_repeat_preserves_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "new project"
            config = Path(temporary) / "project.yaml"
            config.write_text("work_dir: new project\nschema_version: 1\n")
            with redirect_stdout(io.StringIO()):
                self.assertEqual(main(["init", str(config)]), 0)
            for name in DIRECTORIES:
                self.assertTrue((root / name).is_dir())
            existing = root / "input/slc/existing.zip"
            existing.write_bytes(b"keep")
            with redirect_stdout(io.StringIO()):
                self.assertEqual(main(["init", str(config)]), 0)
            self.assertEqual(existing.read_bytes(), b"keep")
            self.assertFalse((root / "files").exists())
            self.assertFalse((root / "archive_files").exists())

    def test_init_conflict_does_not_create_partial_tree(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "work"
            root.mkdir()
            config = Path(temporary) / "project.yaml"
            config.write_text("work_dir: work\nschema_version: 1\n")
            (root / "processing").write_bytes(b"keep")
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(main(["init", str(config)]), 2)
            self.assertEqual(list(root.iterdir()), [root / "processing"])
