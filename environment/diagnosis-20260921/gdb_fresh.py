import subprocess,json,uuid
from pathlib import Path
root=Path(__file__).resolve().parent; project=root.parents[1]
results=[]
for i in range(10):
 name='s1-gdb-fresh-'+uuid.uuid4().hex[:8];log=root/f'gdb-fresh-{i:02}.log'
 cmd=['docker','run','--rm','--name',name,'--network','none','-e','OPENBLAS_NUM_THREADS=1','-e','OMP_NUM_THREADS=1','-e','PYTHONMALLOC=debug','-v',f'{project}:/project:ro','sentinel-1-stack:rc1-debug','env','-u','PYTHONPATH','-u','LD_LIBRARY_PATH','/usr/bin/gdb','-nx','-batch','-ex','set disable-randomization off','-ex','set environment LD_LIBRARY_PATH /usr/local/lib:/opt/conda/lib','-ex','set environment PYTHONPATH /opt/conda/share/sentinel-1-stack:/opt/conda/packages:/opt/conda/share/isce2/stack:/opt/conda/lib/python3.11/site-packages','-ex','run','-ex','thread apply all bt','-ex','info registers','-ex','x/12i $pc-16','--args','/usr/local/bin/python','/project/tests/runtime/probe.py','suite']
 try:
  with log.open('w') as f:
   try:code=subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,timeout=90).returncode
   except subprocess.TimeoutExpired:code='timeout'
 finally:subprocess.run(['docker','rm','-f',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
 text=log.read_text();signal='received signal' in text;normal='exited normally' in text
 results.append({'trial':i,'exit':code,'signal_captured':signal,'normal_exit':normal});(root/'gdb-fresh.json').write_text(json.dumps(results,indent=2)+'\n')
 print(results[-1],flush=True)
 if signal or not normal:break
