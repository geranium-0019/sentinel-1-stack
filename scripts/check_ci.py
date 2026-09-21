"""Run the CI checks locally or on a Linux runner; never downloads SAR data."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    report = {'status': 'running', 'tag': args.image, 'checks': [],
              'scope': 'Offline runtime and repeated tests; excludes real SAR processing.'}

    def save():
        (args.output / 'result.json').write_text(json.dumps(report, indent=2) + '\n')

    save()
    try:
        image = subprocess.check_output(
            ['docker', 'image', 'inspect', '--format', '{{.Id}}', args.image],
            text=True, timeout=30).strip()
        report['image'] = image
        cases = [
            ('inventory', ['python', '/project/tests/runtime/inventory.py']),
            ('environment', ['python', '/project/tests/check_environment.py']),
            ('runtime', ['python', '/project/tests/runtime/check_runtime.py']),
            ('codecs', ['python', '/project/tests/runtime/probe.py', 'codec']),
            ('snaphu', ['python', '/project/tests/runtime/check_snaphu.py']),
            ('resume', ['python', '/project/tests/runtime/check_resume.py']),
        ]
        for name, command in cases:
            container = 'sentinel-ci-' + uuid.uuid4().hex[:12]
            argv = ['docker', 'run', '--rm', '--name', container, '--network', 'none',
                    '--user', f'{os.getuid()}:{os.getgid()}',
                    '--mount', f'type=bind,source={ROOT},target=/project,readonly',
                    '--workdir', '/project', '-e', 'OPENBLAS_NUM_THREADS=1',
                    '-e', 'OMP_NUM_THREADS=1', image] + command
            log = args.output / (name + ('.json' if name == 'inventory' else '.log'))
            print(name, flush=True)
            try:
                with log.open('w') as stream:
                    subprocess.run(argv, stdout=stream, stderr=subprocess.STDOUT,
                                   check=True, timeout=120)
            finally:
                subprocess.run(['docker', 'rm', '-f', container],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
            report['checks'].append(name)
            save()
        print('Repeated checks: XML 100, full suite 10', flush=True)
        with (args.output / 'stability.log').open('w') as stream:
            subprocess.run(
                [sys.executable, str(ROOT / 'scripts/validate_stability.py'),
                 '--image', image, '--output', str(args.output / 'stability')],
                stdout=stream, stderr=subprocess.STDOUT, check=True)
        report['checks'].append('stability')
        report['status'] = 'passed'
        print('CI checks: OK', flush=True)
        return 0
    except (OSError, subprocess.SubprocessError, KeyboardInterrupt) as error:
        report['status'] = 'failed'
        report['error'] = str(error)
        print(f'CI checks failed: {error}; logs: {args.output}', file=sys.stderr)
        return 1
    finally:
        save()


if __name__ == '__main__':
    raise SystemExit(main())
