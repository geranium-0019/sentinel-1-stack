"""Host path mapping and side effects, with Docker calls replaced by a fake."""

from contextlib import redirect_stdout, redirect_stderr
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from sentinel_1_stack.config import read
from sentinel_1_stack.workspace import initialize

spec = importlib.util.spec_from_file_location("stack_launcher", Path(__file__).resolve().parents[1] / "scripts/launch.py")
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


class LauncherTests(unittest.TestCase):
    def test_export_forwards_unwrapped_option(self):
        self.initialize()
        self.assertEqual(self.launch('export', str(self.config), '--include-unwrapped', '--dry-run'), 0)
        self.assertIn('--include-unwrapped', self.calls[-1])
        self.assertIn('--dry-run', self.calls[-1])

    def test_run_forwards_unwrap_limit_and_resume(self):
        self.initialize()
        self.assertEqual(self.launch('run', str(self.config), '--resume', '--unwrap-jobs', '2'), 0)
        command = self.calls[-1]
        self.assertIn('--resume', command)
        self.assertEqual(command[command.index('--unwrap-jobs') + 1], '2')
        self.assertEqual(self.launch('run', str(self.config), '--dry-run'), 0)
        command = self.calls[-1]
        self.assertEqual(command[command.index('--unwrap-jobs') + 1], '1')

    def test_invalid_unwrap_limit_does_not_invoke_docker(self):
        with self.assertRaises(SystemExit):
            self.launch('run', str(self.config), '--unwrap-jobs', '0')
        self.assertEqual(self.calls, [])

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config = self.root / "a project.yaml"
        self.config.write_text("work_dir: work space\nschema_version: 1\n")
        self.request = self.root / "scenes $(literal).geojson"
        self.request.write_text("{}")
        self.work = self.root / "work space"
        self.settings = read(self.config)
        self.calls = []

    def fake_docker(self, command, **kwargs):
        self.calls.append(command)
        return subprocess.CompletedProcess(command, 0, json.dumps(self.settings))

    def launch(self, *args, docker=None):
        with patch.object(launcher.subprocess, "run", side_effect=docker or self.fake_docker), \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return launcher.main(list(args))

    def initialize(self):
        with redirect_stdout(io.StringIO()):
            initialize(self.work)

    def test_init_uses_config_location_and_preserves_existing_files(self):
        self.assertEqual(self.launch("init", str(self.config)), 0)
        data = self.work / "input/slc/keep.zip"
        data.write_bytes(b"keep")
        self.assertEqual(self.launch("init", str(self.config)), 0)
        self.assertEqual(data.read_bytes(), b"keep")
        self.assertEqual(len(self.calls), 2)  # Only YAML inspection, no download.

    def test_dry_run_maps_arbitrary_json_and_external_output_without_writes(self):
        self.initialize()
        output = self.root / "external output/new/slc"
        before = set(self.root.rglob("*"))
        self.assertEqual(self.launch("download", str(self.config), str(self.request),
                                     "--out", str(output), "--dry-run"), 0)
        command = self.calls[-1]
        self.assertIn(f"type=bind,source={self.request},target=/run/scenes.geojson,readonly", command)
        self.assertIn("/overrides/out/external output/new/slc", command)
        self.assertIn("SENTINEL_STACK_RUNTIME_WORK_DIR=/work", command)
        self.assertNotIn("NETRC=/run/secrets/earthdata.netrc", command)
        self.assertEqual(set(self.root.rglob("*")), before)

    def test_uninitialized_download_does_not_mount_or_create_root(self):
        self.assertEqual(self.launch("download", str(self.config), str(self.request), "--dry-run"), 2)
        self.assertFalse(self.work.exists())
        self.assertEqual(len(self.calls), 1)

    def test_download_mounts_credentials_readonly_and_propagates_failure(self):
        self.initialize()
        credential = self.root / "credentials"
        credential.write_text("dummy test file")
        def docker(command, **kwargs):
            result = self.fake_docker(command, **kwargs)
            if len(self.calls) == 2:
                result.returncode = 1
            return result
        with patch.dict("os.environ", {"EARTHDATA_NETRC": str(credential)}):
            self.assertEqual(self.launch("download", str(self.config), str(self.request), docker=docker), 1)
        self.assertIn(f"type=bind,source={credential},target=/run/secrets/earthdata.netrc,readonly", self.calls[-1])

    def test_yaml_inspection_failure_has_no_side_effects(self):
        def fail(command, **kwargs):
            return subprocess.CompletedProcess(command, 2, "")
        self.assertEqual(self.launch("init", str(self.config), docker=fail), 2)
        self.assertFalse(self.work.exists())

    def test_override_inside_work_uses_existing_mount(self):
        self.initialize()
        mounts, destination, host = launcher.output_binding(self.work / "other", self.work, "/extra")
        self.assertEqual(mounts, [])
        self.assertEqual(destination, "/work/other")
        self.assertEqual(host, self.work / "other")

    def test_orbit_uses_same_config_without_json_or_credentials(self):
        self.initialize()
        with patch.dict("os.environ", {"EARTHDATA_NETRC": "/missing/credential", "RES_OPTIONS": "edns0"}):
            self.assertEqual(self.launch("orbit", str(self.config), "--allow-restituted"), 0)
        command = self.calls[-1]
        self.assertIn("orbit", command)
        self.assertIn("--allow-restituted", command)
        self.assertIn("RES_OPTIONS", command)
        self.assertNotIn("NETRC=/run/secrets/earthdata.netrc", command)
        self.assertNotIn("/run/scenes.geojson", command)

    def test_orbit_dry_run_with_external_output_creates_nothing(self):
        self.initialize()
        before = set(self.root.rglob("*"))
        self.assertEqual(self.launch("orbit", str(self.config), "--dry-run", "--out", str(self.root / "other/orbits")), 0)
        self.assertEqual(set(self.root.rglob("*")), before)
        self.assertIn("/overrides/out/other/orbits", self.calls[-1])

    def test_dem_needs_no_json_or_credentials_and_passes_explicit_options(self):
        self.initialize()
        with patch.dict("os.environ", {"EARTHDATA_NETRC": "/missing/credential"}):
            self.assertEqual(self.launch("dem", str(self.config), "--margin", "0.2", "--fill-missing-zero"), 0)
        command = self.calls[-1]
        self.assertIn("dem", command)
        self.assertIn("--fill-missing-zero", command)
        self.assertEqual(command[command.index("--margin") + 1], "0.2")
        self.assertNotIn("/run/scenes.geojson", command)
        self.assertNotIn("NETRC=/run/secrets/earthdata.netrc", command)

    def test_prepare_is_offline_and_dry_run_mounts_work_readonly(self):
        self.initialize()
        self.assertEqual(self.launch("prepare", str(self.config), "--dry-run"), 0)
        command = self.calls[-1]
        self.assertEqual(command[command.index("--network") + 1], "none")
        self.assertIn(f"type=bind,source={self.work},target=/work,readonly", command)
        self.assertIn("prepare", command)
        self.assertNotIn("/run/scenes.geojson", command)
        self.assertNotIn("NETRC=/run/secrets/earthdata.netrc", command)
