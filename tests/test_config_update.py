import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
import yaml
from sentinel_1_stack.config_update import update_dem, dem_text
from sentinel_1_stack.workspace import WorkspaceError


class ConfigUpdateTests(unittest.TestCase):
    def test_preserves_comments_backup_and_permissions(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'project.yaml'
            original = b'# project\npaths:\n  dem: null # height\n  slc: input/slc\nprocessing: {swaths: null}\n'
            path.write_bytes(original)
            path.chmod(0o640)
            update_dem(path, original, 'input/dem/dem.wgs84')
            self.assertEqual(path.read_bytes(), original.replace(b'null # height', b'"input/dem/dem.wgs84" # height'))
            self.assertEqual(list(Path(tmp).glob('*.bak'))[0].read_bytes(), original)
            self.assertEqual(path.stat().st_mode & 0o777, 0o640)
            update_dem(path, path.read_bytes(), 'input/dem/dem.wgs84')
            self.assertEqual(len(list(Path(tmp).glob('*.bak'))), 1)

    def test_missing_keys_and_flow_mapping(self):
        for text in ['work_dir: ..\n', 'paths: {}\n', 'paths: {slc: input/slc}\n',
                     'paths:\n  slc: input/slc # keep\n', 'paths: {dem: null}\n']:
            with self.subTest(text=text):
                result = yaml.safe_load(dem_text(text, 'input/a # x'))
                self.assertEqual(result['paths']['dem'], 'input/a # x')

    def test_conflicts_invalid_yaml_and_aliases_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'p.yaml'
            path.write_text('paths: {}\n# changed\n')
            with self.assertRaises(WorkspaceError):
                update_dem(path, b'paths: {}\n', 'a')
            self.assertEqual(path.read_text(), 'paths: {}\n# changed\n')
            self.assertFalse(list(Path(tmp).glob('*.bak')))
        for text in ['paths: {dem: null, dem: x}', 'paths: &p {dem: null}', 'paths: [a]']:
            with self.subTest(text=text), self.assertRaises(WorkspaceError):
                dem_text(text, 'a')

    def test_atomic_replace_failure_preserves_original(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'p.yaml'
            original = b'paths: {dem: null}\n'
            path.write_bytes(original)
            with patch('sentinel_1_stack.config_update.os.replace', side_effect=OSError('test')):
                with self.assertRaises(OSError):
                    update_dem(path, original, 'a')
            self.assertEqual(path.read_bytes(), original)
