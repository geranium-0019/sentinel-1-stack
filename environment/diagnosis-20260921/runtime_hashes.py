import hashlib,json
from pathlib import Path
root=Path('/usr/local')
result={}
for p in sorted(root.rglob('*')):
 if p.is_file() and not p.is_symlink():
  result[str(p.relative_to(root))]=hashlib.sha256(p.read_bytes()).hexdigest()
print(json.dumps(result,sort_keys=True))
