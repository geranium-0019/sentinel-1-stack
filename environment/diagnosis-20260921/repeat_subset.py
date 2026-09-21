import subprocess,json,uuid
from pathlib import Path
root=Path(__file__).resolve().parent
project=root.parents[1]
images={'pristine':'python:3.11-slim@sha256:5501a4fe605abe24de87c2f3d6cf9fd760354416a0cad0296cf284fddcdca9e2','candidate':'sentinel-1-stack:0.1.0-rc1'}
results=[]
for case,args in [('subset',['python','/diagnosis/subset_probe.py'])]:
 for trial in range(20):
  name='s1-control-'+uuid.uuid4().hex[:10]
  cmd=['docker','run','--rm','--name',name,'--network','none','-e','OPENBLAS_NUM_THREADS=1','-e','PYTHONDONTWRITEBYTECODE=1','-v',f'{root}:/diagnosis:ro','-v',f'{project}:/project:ro','-w','/project']
  if case=='subset':cmd+=['-e','PYTHONMALLOC=debug']
  cmd+=[images['pristine' if case=='pristine' else 'candidate']]+args
  try:
   with (root/f'{case}-{trial:02}.log').open('w') as out:
    try: code=subprocess.run(cmd,stdout=out,stderr=subprocess.STDOUT,timeout=90).returncode
    except subprocess.TimeoutExpired:code='timeout'
  finally:subprocess.run(['docker','rm','-f',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
  results.append({'case':case,'trial':trial,'exit':code});(root/'subset-results.json').write_text(json.dumps(results,indent=2)+'\n')
  print(case,trial,code,flush=True)
  if code!=0:break
