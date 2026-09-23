import io
import json
from pathlib import Path
import shlex
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from types import SimpleNamespace
from unittest.mock import patch
from sentinel_1_stack import runner
from sentinel_1_stack.unwrap_resume import legacy_completed, products


class UnwrapResumeTests(unittest.TestCase):
    def test_parallel_success_checkpoint_survives_sibling_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);work=root/'processing';logs=root/'logs';work.mkdir();logs.mkdir()
            marker=root/'done';gate=root/'gate'
            a=[sys.executable,'-c',f'from pathlib import Path; p=Path({str(marker)!r}); p.write_text(p.read_text()+"x" if p.exists() else "x")']
            b=[sys.executable,'-c',f'import time; from pathlib import Path; time.sleep(0.5); raise SystemExit(0 if Path({str(gate)!r}).exists() else 7)']
            steps=[('run_16_unwrap',[[a,b]])]
            args=SimpleNamespace(config=root/'project.yaml',dry_run=False,resume=False,unwrap_jobs=2)
            with patch.object(runner,'execution_plan',return_value=(work,logs,'same',steps,True)), redirect_stdout(io.StringIO()),redirect_stderr(io.StringIO()):
                self.assertEqual(runner.run(args),2)
                state=json.loads((logs/'run.json').read_text())
                self.assertEqual(state['steps']['run_16_unwrap']['command_results']['0']['status'],'complete')
                gate.touch();args.resume=True;args.unwrap_jobs=1
                self.assertEqual(runner.run(args),0)
                self.assertEqual(marker.read_text(),'x')
                self.assertEqual(len(json.loads((logs/'run.json').read_text())['steps']['run_16_unwrap']['command_results']),2)

    def test_legacy_requires_serial_successor_and_matching_log_headers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);commands=[['a'],['b'],['c']]
            paths=[]
            for i,c in enumerate(commands):
                p=root/f'{i}.log';p.write_text(shlex.join(c)+'\n');paths.append(str(p))
            item={'unwrap_jobs':1,'logs':paths}
            with patch('sentinel_1_stack.unwrap_resume.products',return_value=[{'path':'ok'}]):
                self.assertEqual(set(legacy_completed(item,commands,root,root)),{0,1})
                self.assertEqual(legacy_completed({**item,'unwrap_jobs':2},commands,root,root),{})
                (root/'1.log').write_text('other\n')
                with self.assertRaises(ValueError):legacy_completed(item,commands,root,root)

    def test_missing_or_modified_output_invalidates_checkpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);out=root/'unw';out.write_bytes(b'123')
            record={'status':'complete','command':['a'],'outputs':[{'path':str(out),'size':3,'mtime_ns':out.stat().st_mtime_ns}]}
            item={'command_results':{'0':record}}
            self.assertEqual(len(runner.unwrap_checkpoints(item,[[['a']]],root,root)),1)
            out.write_bytes(b'bad data')
            self.assertEqual(runner.unwrap_checkpoints(item,[[['a']]],root,root),{})

    def test_native_outputs_require_complete_sizes_metadata_and_finite_phase(self):
        import numpy as np
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);unw=root/'fine.unw';coh=root/'fine.cor';labels=Path(str(unw)+'.conncomp')
            xml='<imageFile><component name="coordinate1"><property name="size"><value>2</value></property></component><component name="coordinate2"><property name="size"><value>2</value></property></component><property name="scheme"><value>BIL</value></property><property name="data_type"><value>FLOAT</value></property></imageFile>'
            for p in (unw,coh,labels):
                Path(str(p)+'.xml').write_text(xml)
                Path(str(p)+'.vrt').write_text('<VRTDataset rasterXSize="2" rasterYSize="2"/>')
            np.ones((2,2,2),dtype='<f4').tofile(unw);labels.write_bytes(b'\1'*4)
            conf=root/'config';conf.write_text(f'[Common]\n[Function-1]\nunwrap:\nmethod: snaphu\nrmfilter: False\nunw: {unw}\ncoh: {coh}\n')
            command=['SentinelWrapper.py','-c',str(conf)]
            self.assertEqual(len(products(command,root,scan=True)),6)
            unw.write_bytes(b'partial')
            with self.assertRaises(ValueError):products(command,root,scan=True)
