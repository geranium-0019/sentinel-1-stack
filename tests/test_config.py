from contextlib import redirect_stdout, redirect_stderr
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from sentinel_1_stack import config
from sentinel_1_stack.cli import main
from sentinel_1_stack.workspace import WorkspaceError


class ConfigTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / "project.yaml"

    def test_relative_root_and_custom_paths(self):
        self.path.write_text("work_dir: 'new work'\nschema_version: 1\npaths:\n  slc: scenes\n  logs: reports\n  dem: input/dem/elevation.dem\n")
        with redirect_stdout(io.StringIO()):
            self.assertEqual(main(["init", str(self.path)]), 0)
        for directory in ("scenes", "reports", "input/dem"):
            self.assertTrue((self.root / "new work" / directory).is_dir())
        self.assertFalse((self.root / "new work/input/dem/elevation.dem").exists())
        self.assertFalse((self.root / "new work/input/slc").exists())

    def test_invalid_settings_do_not_create_work(self):
        documents = ["[]", "work_dir: work", "work_dir: []\nschema_version: 1",
                     "work_dir: work\nschema_version: true", "work_dir: work\nschema_version: 2",
                     "work_dir: work\nschema_version: 1\npaths: []", "a: [",
                     *[f"work_dir: work\nschema_version: 1\npaths:\n  slc: {p}"
                       for p in ("/tmp/slc", "../outside", "null", "'./'", "'~/'")]]
        for document in documents:
            with self.subTest(document=document):
                self.path.write_text(document)
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    self.assertEqual(main(["init", str(self.path)]), 2)
                self.assertEqual(list(self.root.iterdir()), [self.path])

    def test_symlink_escape_and_broken_link_rejected_before_creation(self):
        self.path.write_text("work_dir: work\nschema_version: 1\n")
        root = self.root / "work"
        root.mkdir()
        outside = self.root / "outside"
        outside.mkdir()
        for target in (outside, self.root / "missing"):
            link = root / "processing"
            link.symlink_to(target, target_is_directory=True)
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(main(["init", str(self.path)]), 2)
            self.assertFalse((root / "input").exists())
            link.unlink()

    def test_container_runtime_root_is_explicit(self):
        self.path.write_text("work_dir: /mnt/ssd/project\nschema_version: 1\n")
        with patch.dict("os.environ", {"SENTINEL_STACK_RUNTIME_WORK_DIR": "/work"}):
            settings, root = config.load(self.path)
        self.assertEqual(root, Path("/work"))
        self.assertEqual(settings["work_dir"], "/mnt/ssd/project")
