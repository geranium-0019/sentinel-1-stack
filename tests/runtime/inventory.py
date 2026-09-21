"""Emit a deterministic package inventory inside a candidate container."""
import importlib.metadata
import json
from pathlib import Path
import platform
import sys

records = []
for path in sorted(Path('/opt/conda/conda-meta').glob('*.json')):
    data = json.loads(path.read_text())
    records.append({key: data.get(key) for key in ('name', 'version', 'build', 'sha256', 'md5', 'url')})
result = {
    'python': {'version': platform.python_version(), 'compiler': platform.python_compiler(),
               'executable': sys.executable},
    'isce2_commit': Path('/opt/conda/share/isce2/source-commit.txt').read_text().strip(),
    'conda': sorted(records, key=lambda row: row['name']),
    'python_distributions': sorted(
        [{'name': d.metadata['Name'], 'version': d.version, 'location': str(d.locate_file(''))}
         for d in importlib.metadata.distributions()], key=lambda row: (row['name'], row['location'])),
}
print(json.dumps(result, sort_keys=True, indent=2))
