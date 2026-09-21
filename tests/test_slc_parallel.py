"""Concurrent transfer, cancellation and manifest tests using synthetic responses."""
from contextlib import redirect_stdout, redirect_stderr
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from sentinel_1_stack import slc as app
from sentinel_1_stack.workspace import initialize
from test_slc import feature, FakeResponse


class ParallelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.config = self.root/'project.yaml'
        self.config.write_text('schema_version: 1\nwork_dir: .\n')
        with redirect_stdout(io.StringIO()):
            initialize(self.root)
        self.input = self.root/'scenes.geojson'
        self.features = [feature(f'S1A_IW_SLC__1SDV_2017120{i}T215256_2017120{i}T215325_019529_021262_A26F.zip') for i in range(1,5)]
        self.input.write_text(json.dumps({'type':'FeatureCollection','features':self.features}))
        self.before = self.input.read_bytes()
        self.output = self.root/'input/slc'

    def run_app(self, *options):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            result = app.main([str(self.config),str(self.input),'--jobs','2',*options])
        return result

    def record(self):
        return json.loads(next((self.root/'logs/downloads').glob('slc_*.json')).read_text())

    def test_real_parallel_transfers_are_bounded_and_sessions_are_private(self):
        gate = threading.Barrier(2)
        mutex = threading.Lock()
        sessions = []
        active = 0
        peak = 0
        class Session:
            def __enter__(this):
                nonlocal active, peak
                with mutex:
                    active += 1; peak = max(peak,active)
                    sessions.append(this)
                return this
            def __exit__(this,*args):
                nonlocal active
                with mutex: active -= 1
            def get(this,*args,**kwargs):
                gate.wait(timeout=5)
                return FakeResponse([b'da',b'ta'])
        with patch.object(app,'create_session',side_effect=Session):
            self.assertEqual(self.run_app(),0)
        self.assertEqual(peak,2)
        self.assertEqual(active,0)
        self.assertEqual(len({id(s) for s in sessions}),4)
        for f in self.features:
            self.assertEqual((self.output/f['properties']['fileName']).read_bytes(),b'data')
        record = self.record()
        self.assertEqual(record['status'],'complete')
        self.assertEqual(record['jobs'],2)
        self.assertEqual([p['status'] for p in record['products']],['downloaded']*4)
        self.assertEqual(self.input.read_bytes(),self.before)

    def test_failure_stops_new_submissions_and_records_all_active_results(self):
        gate = threading.Barrier(2)
        sessions = []
        class Session:
            def __enter__(this): sessions.append(this); return this
            def __exit__(this,*args): pass
        def transfer(product, directory, session, *, stop, **kwargs):
            gate.wait(timeout=5)
            if '20171201' in product.filename:
                raise app.DownloadError('simulated HTTP 500')
            stop.wait(timeout=5)
            app.check_cancelled(stop)
            raise AssertionError('peer failure did not cancel transfer')
        with patch.object(app,'create_session',side_effect=Session), patch.object(app,'fetch_product',side_effect=transfer):
            self.assertEqual(self.run_app(),1)
        record = self.record()
        self.assertEqual(record['status'],'failed')
        self.assertEqual(record['products'][0]['status'],'failed')
        self.assertEqual(record['products'][1]['status'],'cancelled')
        self.assertEqual([p['status'] for p in record['products'][2:]],['pending','pending'])
        self.assertEqual(len(sessions),2)
        self.assertFalse((self.output/self.features[0]['properties']['fileName']).exists())
        self.assertNotIn('checking_or_downloading',[p['status'] for p in record['products']])

    def test_bad_checksum_never_promotes_a_partial_zip(self):
        class Session:
            def __enter__(this): return this
            def __exit__(this,*args): pass
            def get(this,*args,**kwargs): return FakeResponse([b'bad!'])
        with patch.object(app,'create_session',side_effect=Session):
            self.assertEqual(self.run_app(),1)
        self.assertEqual(self.record()['status'],'failed')
        self.assertFalse(list(self.output.glob('*.zip')))
        self.assertTrue(list(self.output.glob('*.part')))
        self.assertIn('failed',[p['status'] for p in self.record()['products']])

    def test_interrupt_waits_for_workers_before_closing_logs(self):
        stopped = []
        def transfer(product, directory, session, *, stop, **kwargs):
            try:
                stop.wait(timeout=5)
                app.check_cancelled(stop)
            finally:
                stopped.append(product.filename)
        class Session:
            def __enter__(this): return this
            def __exit__(this,*args): pass
        with patch.object(app,'create_session',side_effect=Session), \
             patch.object(app,'fetch_product',side_effect=transfer), \
             patch.object(app,'wait',side_effect=KeyboardInterrupt):
            self.assertEqual(self.run_app(),130)
        self.assertEqual(self.record()['status'],'interrupted')
        self.assertNotIn('checking_or_downloading',[p['status'] for p in self.record()['products']])
        self.assertTrue(stopped)

    def test_parallel_dry_run_and_invalid_jobs_do_not_create_sessions(self):
        before = set(self.root.rglob('*'))
        with patch.object(app,'create_session') as factory:
            self.assertEqual(self.run_app('--dry-run'),0)
            self.assertEqual(self.run_app('--jobs','0'),2)
            factory.assert_not_called()
        self.assertEqual(before,set(self.root.rglob('*')))
