import subprocess,json,uuid
from pathlib import Path
root=Path(__file__).resolve().parent
cases=[('pristine','python:3.11-slim@sha256:5501a4fe605abe24de87c2f3d6cf9fd760354416a0cad0296cf284fddcdca9e2',[]),('candidate-stdlib','sentinel-1-stack:0.1.0-rc1',[]),('candidate-asf','sentinel-1-stack:0.1.0-rc1',['asf_search']),('candidate-all','sentinel-1-stack:0.1.0-rc1',['sentinel_1_stack.cli,osgeo.gdal,shapely'])]
results=[]
for name,image,args in cases:
    container='s1-diagnosis-'+uuid.uuid4().hex[:10]
    try:
        with (root/(name+'.log')).open('w') as stream:
            try:
                code=subprocess.run(['docker','run','--rm','--name',container,'--network','none','-e','PYTHONDONTWRITEBYTECODE=1','-e','OPENBLAS_NUM_THREADS=1','-v',f'{root}:/diagnosis:ro',image,'python','/diagnosis/stdlib_stress.py']+args,stdout=stream,stderr=subprocess.STDOUT,timeout=150).returncode
            except subprocess.TimeoutExpired: code='timeout'
    finally:
        subprocess.run(['docker','rm','-f',container],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    results.append({'case':name,'exit':code})
    (root/'matrix.json').write_text(json.dumps(results,indent=2)+'\n')
    print(name,code,flush=True)
