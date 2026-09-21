"""Batch sequencing and failure boundaries; no Docker, network or SAR processing."""
from contextlib import redirect_stdout, redirect_stderr
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import batch
from sentinel_1_stack.config import read
from sentinel_1_stack.workspace import initialize


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.config = self.root / 'original.yaml'
        self.document = {'schema_version': 1, 'work_dir': str(self.root),
                         'paths': {'dem': None}, 'processing': {'unwrap': True}}
        self.config.write_text(json.dumps(self.document))
        self.original = self.config.read_bytes()
        self.settings = read(self.config)
        self.calls = []
        self.failure = None
        self.info = {'settings': self.settings, 'document': self.document, 'unwrap': True}

    def metadata(self, command, **kwargs):
        if command[:3] == ['docker', 'image', 'inspect']:
            output = 'sha256:fixed\n'
        else:
            output = json.dumps(self.info)
        return subprocess.CompletedProcess(command, 0, output, '')

    def execute(self, command, log):
        self.calls.append(command)
        step = command[3] if command[0] != 'docker' else 'load'
        if step == self.failure:
            raise RuntimeError('simulated failure')
        if step == 'init':
            initialize(self.root)
        if step == 'dem':
            logs = Path(command[command.index('--log-dir') + 1]); logs.mkdir(parents=True)
            dest = self.root / 'input/dem/example'; dest.mkdir()
            for suffix in ('', '.xml', '.vrt'):
                (dest / ('dem.wgs84' + suffix)).write_text('fixture')
            (logs/'dem_fixture.json').write_text(json.dumps({'status':'complete', 'output':'/work/input/dem/example'}))

    def run_batch(self, *args):
        with patch.object(batch.subprocess, 'run', side_effect=self.metadata), \
             patch.object(batch, 'execute', side_effect=self.execute), \
             redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return batch.main([str(self.config), *args])

    def test_full_sequence_and_dem_snapshot_preserve_original(self):
        self.assertEqual(self.run_batch(), 0)
        self.assertEqual([c[3] for c in self.calls], ['init','download','orbit','dem','prepare','prepare','run','export'])
        generated = Path(self.calls[-1][4])
        self.assertNotEqual(generated, self.config)
        doc = json.loads(generated.read_text())
        self.assertEqual(doc['paths']['dem'], 'input/dem/example/dem.wgs84')
        self.assertEqual(self.config.read_bytes(), self.original)
        self.assertIn('--include-unwrapped', self.calls[-1])
        self.assertTrue(all(c[2] == 'sha256:fixed' for c in self.calls))

    def test_download_failure_stops_all_later_steps(self):
        self.failure = 'download'
        self.assertEqual(self.run_batch(), 1)
        self.assertEqual([c[3] for c in self.calls], ['init','download'])
        logs = list((self.root/'logs').glob('batch_*.log'))
        self.assertIn('simulated failure', logs[0].read_text())
        self.assertEqual(self.config.read_bytes(), self.original)

    def test_existing_dem_is_used_and_unwrap_false_controls_export(self):
        self.document['paths']['dem'] = 'input/dem/custom.dem'
        self.settings['paths']['dem'] = 'input/dem/custom.dem'
        self.document['processing']['unwrap'] = False
        self.info['unwrap'] = False
        self.assertEqual(self.run_batch('--json', str(self.root/'chosen.geojson'), '--allow-restituted', '--download-jobs', '2', '--download-retries', '5'), 0)
        self.assertNotIn('dem', [c[3] for c in self.calls])
        self.assertIn('--json', self.calls[1])
        self.assertEqual(self.calls[1][self.calls[1].index('--retries')+1], '5')
        self.assertEqual(self.calls[1][self.calls[1].index('--jobs')+1], '2')
        self.assertIn('--allow-restituted', self.calls[2])
        self.assertNotIn('--include-unwrapped', self.calls[-1])

    def test_resume_skips_acquisition_and_prepare(self):
        self.settings['paths']['dem'] = 'input/dem/custom.dem'
        self.assertEqual(self.run_batch('--resume'), 0)
        self.assertEqual([c[3] for c in self.calls], ['run','export'])
        self.assertIn('--resume', self.calls[0])

    def test_plan_does_not_call_docker_or_write_files(self):
        before = set(self.root.rglob('*'))
        with patch.object(batch.subprocess, 'run') as run, patch.object(batch, 'execute') as execute, redirect_stdout(io.StringIO()):
            self.assertEqual(batch.main([str(self.config), '--plan']), 0)
        run.assert_not_called(); execute.assert_not_called()
        self.assertEqual(set(self.root.rglob('*')), before)

    def test_existing_processing_stops_before_download(self):
        (self.root/'processing').mkdir()
        (self.root/'processing/keep').write_text('existing')
        self.assertEqual(self.run_batch(), 1)
        self.assertEqual([c[3] for c in self.calls], ['init'])
        self.assertEqual((self.root/'processing/keep').read_text(), 'existing')

    def test_prepare_failure_stops_run_and_export(self):
        self.failure = 'prepare'
        self.assertEqual(self.run_batch(), 1)
        self.assertEqual(self.calls[-1][3], 'prepare')
        self.assertNotIn('run', [c[3] for c in self.calls])

    def test_real_child_failure_is_logged_and_reported(self):
        log = io.StringIO()
        with redirect_stdout(io.StringIO()), self.assertRaisesRegex(RuntimeError, '終了コード 7'):
            batch.execute([sys.executable, '-c', 'print("failure marker", flush=True); raise SystemExit(7)'], log)
        self.assertIn('failure marker', log.getvalue())
