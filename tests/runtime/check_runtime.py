"""Check default commands and child interpreters in the runtime candidate."""
import json
from pathlib import Path
import subprocess
import sys

assert Path(sys.executable).resolve() == Path('/usr/local/bin/python3.11')
for command in ('python', 'python3'):
    actual = subprocess.check_output(
        [command, '-c', 'import json,sys; print(json.dumps([sys.executable, sys.version_info[:3]]))'],
        text=True, timeout=15)
    executable, version = json.loads(actual)
    assert Path(executable).resolve() == Path(sys.executable).resolve(), actual
    assert version == [3, 11, 10], actual
for command in ('SentinelWrapper.py', 'stackSentinel.py', 'dem.py'):
    result = subprocess.run([command, '--help'], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, (command, result.stdout, result.stderr)
print('Default Python, child Python, ISCE2 entry points: OK')
