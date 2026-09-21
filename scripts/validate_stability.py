"""Run bounded, repeatable environment acceptance checks on an explicit Docker image.

Does not modify the image or real SAR projects. Real-data/rebuild acceptance is separate.
"""
import argparse
import json
from pathlib import Path
import subprocess
import uuid

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--python', default='python', help='Interpreter inside the image')
    parser.add_argument('--xml-trials', type=int, default=100)
    parser.add_argument('--suite-trials', type=int, default=10)
    args = parser.parse_args()
    if args.xml_trials < 1 or args.suite_trials < 1:
        parser.error('trial counts must be positive')
    args.output.mkdir(parents=True, exist_ok=False)
    image = subprocess.check_output(['docker', 'image', 'inspect', '--format', '{{.Id}}', args.image], text=True).strip()
    report = {'image': image, 'tag': args.image, 'python': args.python, 'results': [], 'status': 'running',
              'scope': 'Dependency, XML reproducer, full unit suite only; not real-data or rebuild acceptance.'}
    def save():
        (args.output/'result.json').write_text(json.dumps(report, indent=2)+'\n')
    save()
    cases = [('pip-check', [args.python,'-m','pip','check'])]
    cases += [(f'xml-{i:03}', [args.python,'/project/tests/runtime/xml_probe.py','asf_search']) for i in range(args.xml_trials)]
    cases += [(f'suite-{i:02}', [args.python,'/project/tests/runtime/probe.py','suite']) for i in range(args.suite_trials)]
    for name, command in cases:
        container='sentinel-stability-'+uuid.uuid4().hex[:12]
        argv=['docker','run','--rm','--name',container,'--network','none',
              '--mount',f'type=bind,source={ROOT},target=/project,readonly','--workdir','/project',
              '-e','OPENBLAS_NUM_THREADS=1','-e','OMP_NUM_THREADS=1',image]+command
        try:
            with (args.output/(name+'.log')).open('w') as stream:
                try:
                    code=subprocess.run(argv,stdout=stream,stderr=subprocess.STDOUT,timeout=60).returncode
                except subprocess.TimeoutExpired:
                    code='timeout'
                except KeyboardInterrupt:
                    report['status']='interrupted';save();raise
        finally:
            # Only this uniquely named test container; no SAR jobs touched.
            subprocess.run(['docker','rm','-f',container],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=15)
        report['results'].append({'case':name,'exit':code});save()
        print(name,code,flush=True)
        if code != 0:
            report['status']='failed';save();return 1
    report['status']='passed';save();return 0


if __name__=='__main__':
    raise SystemExit(main())
