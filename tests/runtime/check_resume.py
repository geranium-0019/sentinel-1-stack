"""Interrupt a real runner child, then resume an isolated synthetic job (not SAR data)."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import patch


def worker(root, resume):
    from sentinel_1_stack import runner
    first = "from pathlib import Path; p=Path('first.txt'); p.write_text(p.read_text()+'x' if p.exists() else 'x')"
    second = "import os,time; from pathlib import Path; Path('child.pid').write_text(str(os.getpid()));\nwhile not Path('continue').exists(): time.sleep(.05)\nPath('done').write_text('ok')"
    steps = [('run_01', [[[sys.executable, '-c', first]]]),
             ('run_02_unwrap', [[[sys.executable, '-c', second]]])]
    args = SimpleNamespace(config=root/'config.yaml', dry_run=False, resume=resume, unwrap_jobs=1)
    with patch.object(runner, 'execution_plan', return_value=(root/'processing', root/'logs', 'fixed', steps, True)):
        return runner.run(args)


def main():
    if len(sys.argv) > 1:
        return worker(Path(sys.argv[1]), sys.argv[2] == 'resume')
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root/'processing').mkdir(); (root/'logs').mkdir()
        with (root/'worker.log').open('w') as log:
            process = subprocess.Popen([sys.executable, __file__, str(root), 'start'], stdout=log, stderr=log)
            try:
                deadline = time.monotonic() + 20
                while not (root/'processing/child.pid').exists():
                    assert process.poll() is None, 'runner exited early'
                    assert time.monotonic() < deadline, 'child startup timeout'
                    time.sleep(.05)
                process.send_signal(signal.SIGINT)
                assert process.wait(timeout=15) == 130
            finally:
                if process.poll() is None:
                    process.kill(); process.wait()
        state = json.loads((root/'logs/run.json').read_text())
        assert state['status'] == 'interrupted', state
        child_pid = int((root/'processing/child.pid').read_text())
        try:
            os.kill(child_pid, 0)
        except ProcessLookupError:
            pass
        else:
            raise AssertionError('child survived interruption')
        (root/'processing/continue').touch()
        subprocess.run([sys.executable, __file__, str(root), 'resume'], check=True, timeout=20)
        state = json.loads((root/'logs/run.json').read_text())
        assert state['status'] == 'complete' and 'error' not in state, state
        assert state['steps']['run_01']['attempts'] == 1
        assert state['steps']['run_02_unwrap']['attempts'] == 2
        assert (root/'processing/first.txt').read_text() == 'x'
        assert (root/'processing/done').read_text() == 'ok'
        print('Real child SIGINT cleanup and resume: OK (synthetic commands, not SAR processing)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
