import copy
from pathlib import Path
import unittest
import tempfile
import io
from contextlib import redirect_stdout, redirect_stderr
from types import SimpleNamespace
from unittest.mock import patch

from sentinel_1_stack import prepare

from sentinel_1_stack.prepare import parameters, safe_path, reserve_destination, extra_arguments
from sentinel_1_stack.workspace import WorkspaceError


class PrepareTests(unittest.TestCase):
    def test_generation_logs_use_configured_logs_directory_and_preserve_existing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            destination = root / 'processing'
            destination.mkdir()
            logs = root / 'custom_logs'
            logs.mkdir()
            config = root / 'project.yaml'
            config.write_text('example')
            record = {'destination': str(destination), 'command': ['unused'], 'inputs': [],
                      'orbits': [], 'dates': [], 'pairs': [], 'processing': {'bbox': [], 'swaths': [3]}}
            settings = {'paths': {'dem': 'dem', 'logs': 'custom_logs'}}
            with patch.object(prepare, 'plan', side_effect=lambda _: copy.deepcopy(record)), \
                    patch.object(prepare.config, 'load', return_value=(settings, root)), \
                    patch.object(prepare, 'check_dem'), patch.object(prepare.subprocess, 'Popen') as popen, \
                    redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                before = set(root.rglob('*'))
                self.assertEqual(prepare.run(SimpleNamespace(config=config, dry_run=True)), 0)
                self.assertEqual(set(root.rglob('*')), before)
                def start(*args, **kwargs):
                    runfiles = destination / 'run_files'
                    runfiles.mkdir()
                    (runfiles / 'run_01_filter_coherence').write_text('true\n')
                    return popen.return_value
                popen.side_effect = start
                process = popen.return_value.__enter__.return_value
                process.stdout = ['generated\n']
                process.wait.return_value = 0
                self.assertEqual(prepare.run(SimpleNamespace(config=config, dry_run=False)), 0)
                self.assertEqual((logs / 'prepare.log').read_text(), 'generated\n')
                self.assertFalse((destination / 'prepare.log').exists())
                self.assertFalse((destination / 'prepare.json').exists())
                content = (logs / 'prepare.json').read_bytes()
                self.assertEqual(prepare.run(SimpleNamespace(config=config, dry_run=False)), 2)
                self.assertEqual((logs / 'prepare.json').read_bytes(), content)

    def test_existing_processing_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'processing'
            path.mkdir()
            reserve_destination(path)
            before = (path / '.prepare.lock').read_bytes()
            with self.assertRaises(WorkspaceError):
                reserve_destination(path)
            self.assertEqual((path / '.prepare.lock').read_bytes(), before)

    def setUp(self):
        self.document = {
            'processing': {'workflow': 'interferogram', 'polarization': 'vv', 'swaths': [3],
                           'bbox': [-6.27, -5.93, 105.25, 105.59], 'reference_date': '20260729',
                           'coregistration': 'NESD', 'range_looks': 3, 'azimuth_looks': 1,
                           'filter_strength': 0.5, 'unwrap_method': 'snaphu'},
            'execution': {'num_processes': 4, 'num_processes_topo': 2},
            'pairs': {'connections': 1}}

    def test_explicit_processing_parameters(self):
        p, e, connections = parameters(self.document)
        self.assertEqual((p['swaths'], e['num_processes'], connections), ([3], 4, 1))

    def test_thresholds_and_other_options_reach_isce_arguments(self):
        self.document['processing'].update(esd_coherence_threshold=0.7, snr_misreg_threshold=8,
            num_overlap_connections=2, virtual_merge=False, remove_filter_effect=True)
        p, _, _ = parameters(self.document)
        self.assertEqual(extra_arguments(p), ['--esd_coherence_threshold', '0.7',
            '--snr_misreg_threshold', '8', '--num_overlap_connections', '2',
            '--virtual_merge', 'False', '-rmFilter'])

    def test_new_options_validate_and_default(self):
        p, _, _ = parameters(self.document)
        self.assertEqual(p['esd_coherence_threshold'], 0.85)
        for key, value in [('esd_coherence_threshold', 1), ('esd_coherence_threshold', 0),
                           ('snr_misreg_threshold', -1), ('num_overlap_connections', 0),
                           ('virtual_merge', 'false'), ('remove_filter_effect', 1),
                           ('esd_coherance_threshold', 0.7)]:
            doc = copy.deepcopy(self.document)
            doc['processing'][key] = value
            with self.subTest(key=key), self.assertRaises(WorkspaceError):
                parameters(doc)

    def test_rejects_ambiguous_or_invalid_parameters(self):
        cases = [('swaths', None), ('swaths', [True]), ('swaths', [3, 3]),
                 ('bbox', [1, 0, 2, 3]), ('bbox', [float('nan'), 1, 2, 3]),
                 ('reference_date', 20260729), ('reference_date', '20260230'),
                 ('range_looks', True), ('azimuth_looks', 0),
                 ('filter_strength', float('nan')), ('workflow', 'slc')]
        for key, value in cases:
            with self.subTest(key=key, value=value):
                doc = copy.deepcopy(self.document)
                doc['processing'][key] = value
                with self.assertRaises(ValueError):
                    parameters(doc)

    def test_rejects_shell_syntax_and_outside_root(self):
        for path in ('/work/a b', '/work/a;touch', '/work/$(id)', '/work/../outside'):
            with self.subTest(path=path), self.assertRaises(WorkspaceError):
                safe_path(Path(path), Path('/work'))
        self.assertEqual(safe_path(Path('/work/input/dem.wgs84'), Path('/work')), Path('/work/input/dem.wgs84'))
