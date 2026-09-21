"""Validate local inputs and generate, but never execute, ISCE2 run files."""

from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys

import yaml

from . import config, orbit, swaths as swath_selection
from .workspace import WorkspaceError, require_initialized


def add_arguments(parser):
    parser.add_argument('config', type=Path)
    parser.add_argument('--dry-run', action='store_true', help='入力とコマンドを確認。生成なし')


OPTION_DEFAULTS = {
    'esd_coherence_threshold': 0.85,
    'snr_misreg_threshold': 10.0,
    'num_overlap_connections': 3,
    'virtual_merge': True,
    'remove_filter_effect': False,
}


def extra_arguments(p):
    result = ['--esd_coherence_threshold', str(p['esd_coherence_threshold']),
              '--snr_misreg_threshold', str(p['snr_misreg_threshold']),
              '--num_overlap_connections', str(p['num_overlap_connections']),
              '--virtual_merge', str(p['virtual_merge'])]
    if p['remove_filter_effect']:
        result.append('-rmFilter')
    return result


def parameters(document):
    p = document.get('processing', {})
    e = document.get('execution', {})
    pairs = document.get('pairs', {})
    if not all(isinstance(x, dict) for x in (p, e, pairs)):
        raise WorkspaceError('processing / execution / pairs はマッピングで指定してください。')
    p = {**OPTION_DEFAULTS, **p}
    allowed = set(OPTION_DEFAULTS) | {'workflow', 'polarization', 'coregistration', 'unwrap_method', 'unwrap',
              'swaths', 'bbox', 'reference_date', 'range_looks', 'azimuth_looks', 'filter_strength'}
    for name, values, keys in (('processing', p, allowed), ('execution', e, {'num_processes', 'num_processes_topo'}),
                               ('pairs', pairs, {'connections'})):
        unknown = set(values) - keys
        if unknown:
            raise WorkspaceError(f'{name} に未対応の設定があります: {sorted(unknown)}')
    for key in ('esd_coherence_threshold', 'snr_misreg_threshold'):
        v = p[key]
        if type(v) not in (float, int) or not math.isfinite(v) or v < 0:
            raise WorkspaceError(f'processing.{key} は有限の0以上の数値にしてください。')
    if not 0 < p['esd_coherence_threshold'] < 1:
        raise WorkspaceError('processing.esd_coherence_threshold は0より大きく1未満にしてください。')
    if type(p['num_overlap_connections']) is not int or p['num_overlap_connections'] < 1:
        raise WorkspaceError('processing.num_overlap_connections は正の整数にしてください。')
    for key in ('virtual_merge', 'remove_filter_effect'):
        if type(p[key]) is not bool:
            raise WorkspaceError(f'processing.{key} は true / false にしてください。')
    for key, values in {'workflow': ('interferogram',), 'polarization': ('vv', 'vh', 'hh', 'hv'),
                        'coregistration': ('NESD', 'geometry'), 'unwrap_method': ('snaphu', 'icu')}.items():
        if p.get(key) not in values:
            raise WorkspaceError(f'processing.{key} は {values} から指定してください。')
    if type(p.get('unwrap', True)) is not bool:
        raise WorkspaceError('processing.unwrap は true / false で指定してください。')
    swaths = p.get('swaths')
    if swaths is not None and (not isinstance(swaths, list) or not swaths or
            any(type(x) is not int or x not in (1, 2, 3) for x in swaths) or len(set(swaths)) != len(swaths)):
        raise WorkspaceError('processing.swaths は null（自動）または [3] のようなリストにしてください。')
    bbox = p.get('bbox')
    if (not isinstance(bbox, list) or len(bbox) != 4 or
            any(type(x) not in (float, int) or not math.isfinite(x) for x in bbox) or
            not (-90 <= bbox[0] < bbox[1] <= 90 and -180 <= bbox[2] < bbox[3] <= 180)):
        raise WorkspaceError('processing.bbox は有効な [南, 北, 西, 東] を指定してください。')
    date = p.get('reference_date')
    if not isinstance(date, str) or not re.fullmatch(r'\d{8}', date):
        raise WorkspaceError('processing.reference_date は "YYYYMMDD" 形式の文字列にしてください。')
    datetime.strptime(date, '%Y%m%d')
    for mapping, keys in ((p, ('range_looks', 'azimuth_looks')), (e, ('num_processes', 'num_processes_topo')),
                          (pairs, ('connections',))):
        for key in keys:
            if type(mapping.get(key)) is not int or mapping[key] < 1:
                raise WorkspaceError(f'{key} は正の整数にしてください。')
    if type(p.get('filter_strength')) not in (int, float) or not 0 <= p['filter_strength'] <= 1:
        raise WorkspaceError('filter_strength は 0〜1 にしてください。')
    return p, e, pairs['connections']


def safe_path(path, root):
    if not path.resolve().is_relative_to(root):
        raise WorkspaceError(f'作業ルート外へのリンクは使用できません: {path}')
    # Upstream writes unquoted paths into shell scripts and config values.
    if not re.fullmatch(r'[A-Za-z0-9_./-]+', str(path)):
        raise WorkspaceError(f'ISCE2 の生成先・入力パスには英数字・_・-・.・/ を使用してください: {path}')
    return path


def plan(config_path):
    settings, root = config.load(config_path)
    require_initialized(root, settings['directories'])
    document = yaml.safe_load(config_path.read_text())
    p, e, connections = parameters(document)
    paths = {k: safe_path(root / v, root) for k, v in settings['paths'].items()}
    if 'dem' not in paths:
        raise WorkspaceError('paths.dem に生成済みの DEM を指定してください。')
    for suffix in ('', '.xml', '.vrt'):
        if not Path(str(paths['dem']) + suffix).is_file():
            raise WorkspaceError(f'DEM ファイルがありません: {paths["dem"]}{suffix}')
    scenes = orbit.discover(paths['slc'])
    dates = sorted({s.start.strftime('%Y%m%d') for s in scenes})
    if len(dates) < 2 or p['reference_date'] not in dates:
        raise WorkspaceError('2日以上の SLC と指定した基準日の SLC が必要です。')
    inputs = []
    selected = []
    candidates = [(f, orbit.parse_orbit(f.name)) for f in sorted(paths['orbit'].glob('*.EOF'))]
    for scene in scenes:
        slc = paths['slc'] / (scene.name + '.zip')
        if not slc.exists():
            slc = paths['slc'] / (scene.name + '.SAFE')
        safe_path(slc, root)
        inputs.append(str(slc))
        matches = [(f, o) for f, o in candidates if o.covers(scene)]
        if not matches:
            raise WorkspaceError(f'ローカル軌道がありません。先に orbit を実行してください: {scene.name}')
        f, o = max(matches, key=lambda item: (item[1].kind == 'POEORB', item[1].generated))
        safe_path(f, root)
        orbit.validate_eof(f, o, [scene])
        selected.append({'scene': scene.name, 'path': str(f), 'kind': o.kind})
    selection = {'mode': 'explicit'}
    if p.get('swaths') is None:
        p['swaths'], evidence = swath_selection.select(inputs, p['bbox'], p['polarization'])
        selection = {'mode': 'auto', 'observations': evidence}
    executable = shutil.which('stackSentinel.py')
    if executable is None:
        raise WorkspaceError('stackSentinel.py がありません。ISCE2 コンテナで実行してください。')
    destination = paths['processing']
    command = [sys.executable, executable, '-s', str(destination / 'slc_inputs.txt'),
               '-o', str(destination / 'orbits'), '-a', str(paths['aux']), '-w', str(destination),
               '-d', str(paths['dem']), '-W', p['workflow'], '-p', p['polarization'],
               '-n', ' '.join(map(str, p['swaths'])), '-b', ' '.join(map(str, p['bbox'])),
               '-m', p['reference_date'], '-C', p['coregistration'], '-r', str(p['range_looks']),
               '-z', str(p['azimuth_looks']), '-f', str(p['filter_strength']), '-u', p['unwrap_method'],
               '-c', str(connections), '--num_proc', str(e['num_processes']),
               '--num_proc4topo', str(e['num_processes_topo'])] + extra_arguments(p)
    return {'destination': str(destination), 'command': command, 'inputs': inputs, 'orbits': selected,
            'dates': dates, 'pairs': [[a, b] for i, a in enumerate(dates) for b in dates[i+1:i+1+connections]],
            'swath_selection': selection, 'processing': p, 'execution': e, 'config_sha256': hashlib.sha256(config_path.read_bytes()).hexdigest()}


def check_dem(path, bbox):
    import isce
    import isceobj
    from osgeo import gdal
    gdal.UseExceptions()
    image = isceobj.createDemImage()
    image.load(str(path) + '.xml')
    if image.reference != 'WGS84':
        raise WorkspaceError('DEM は WGS84 楕円体高にしてください。')
    dataset = gdal.Open(str(path) + '.vrt')
    x, dx, rx, y, ry, dy = dataset.GetGeoTransform()
    if rx or ry or dx <= 0 or dy >= 0:
        raise WorkspaceError('DEM は北向きの経緯度グリッドにしてください。')
    s, n, w, east = bbox
    if not (x <= w < east <= x + dx * dataset.RasterXSize and y + dy * dataset.RasterYSize <= s < n <= y):
        raise WorkspaceError('DEM が処理 bbox 全体を含んでいません。')


def reserve_destination(destination):
    destination.mkdir(exist_ok=True)
    if any(destination.iterdir()):
        raise WorkspaceError(f'処理ディレクトリに既存ファイルがあります: {destination}。再生成する場合は既存の内容を別の場所へ退避してください。')
    # Exclusive creation also prevents two simultaneous generators.
    (destination / '.prepare.lock').touch(exist_ok=False)


def run(args):
    record = None
    destination = None
    created = False
    record_path = None
    try:
        record = plan(args.config)
        settings, root = config.load(args.config)
        check_dem(root / settings['paths']['dem'], record['processing']['bbox'])
        log_directory = safe_path(root / settings['paths']['logs'], root)
        log_path = log_directory / 'prepare.log'
        record_path = log_directory / 'prepare.json'
        record['log'] = str(log_path)
        record['record'] = str(record_path)
        if record.get('swath_selection', {}).get('mode') == 'auto':
            print(f"swath自動選択（bboxと全観測の位置情報）: {record['processing']['swaths']}")
        print('観測日: ' + ', '.join(record['dates']))
        print(f"干渉ペア: {len(record['pairs'])} / swaths: {record['processing']['swaths']}")
        for item in record['orbits']:
            print(f"軌道 {item['kind']}: {item['scene']}")
        print('生成先: ' + record['destination'])
        print(f'ログ: {log_path}\n実行記録: {record_path}')
        print('生成コマンド: ' + shlex.join(record['command']), flush=True)
        unwrap = record['processing'].get('unwrap', True)
        print(f'処理本体は実行しません。アンラップ: {"実行対象" if unwrap else "省略"}', flush=True)
        if args.dry_run:
            return 0
        destination = Path(record['destination'])
        for path in (log_path, record_path):
            if path.exists() or path.is_symlink():
                raise WorkspaceError(f'既存ログ・記録は上書きしません。再生成時は退避してください: {path}')
        reserve_destination(destination)
        record['status'] = 'generating'
        with record_path.open('x') as stream:
            created = True
            stream.write(json.dumps(record, indent=2) + '\n')
        (destination / 'project.yaml').write_bytes(args.config.read_bytes())
        (destination / 'slc_inputs.txt').write_text('\n'.join(record['inputs']) + '\n')
        (destination / 'orbits').mkdir()
        for item in record['orbits']:
            target = destination / 'orbits' / Path(item['path']).name
            if not target.exists():
                shutil.copyfile(item['path'], target)
        with log_path.open('x') as log:
            with subprocess.Popen(record['command'], cwd=destination, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, text=True) as process:
                try:
                    for line in process.stdout:
                        print(line, end='', flush=True)
                        log.write(line)
                        log.flush()
                    status = process.wait()
                except BaseException:
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                    raise
        if status:
            raise WorkspaceError(f'stackSentinel.py が終了コード {status} で失敗しました。ログ: {log_path}')
        files = sorted((destination / 'run_files').glob('run_*'))
        if not files or not any(f.name.endswith('_filter_coherence') for f in files):
            raise WorkspaceError('期待する run_files が生成されませんでした。')
        record['run_files'] = [str(f) for f in files if unwrap or not f.name.endswith('_unwrap')]
        record['skipped_run_files'] = [str(f) for f in files if not unwrap and f.name.endswith('_unwrap')]
        record['status'] = 'prepared'
        print(f'生成完了: {len(files)} 工程（実行対象: {len(record["run_files"])} 工程）')
        print('実処理・ジオコード・PGV は未実行です。')
        return 0
    except KeyboardInterrupt:
        if record:
            record['status'] = 'interrupted'
        print('中断しました。', file=sys.stderr)
        return 130
    except (WorkspaceError, OSError, ValueError, RuntimeError) as exc:
        if record:
            record['status'] = 'failed'
            record['error'] = str(exc)
        print(f'ERROR: {exc}', file=sys.stderr)
        return 2
    finally:
        if created:
            record_path.write_text(json.dumps(record, indent=2) + '\n')
