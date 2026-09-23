"""Conservative validation of native SNAPHU products for restart checkpoints."""
import configparser
from pathlib import Path
import shlex
import xml.etree.ElementTree as ET

from .workspace import WorkspaceError


def products(command, directory, host_root=None, scan=False):
    def local(value):
        p = Path(value)
        if host_root is not None and p.is_relative_to('/work'):
            p = Path(host_root) / p.relative_to('/work')
        if not p.is_absolute():
            p = directory / p
        if not p.resolve().is_relative_to(directory.resolve()):
            raise WorkspaceError(f'処理先外のファイルです: {p}')
        return p
    if len(command) != 3 or Path(command[0]).name != 'SentinelWrapper.py' or command[1] != '-c':
        raise WorkspaceError('標準のアンラップコマンドではありません。')
    conf = configparser.ConfigParser(interpolation=None)
    conf.read_string(local(command[2]).read_text())
    if conf.sections() != ['Common', 'Function-1']:
        raise WorkspaceError('標準のアンラップ設定ではありません。')
    section = conf['Function-1']
    if 'unwrap' not in section or section.get('method') != 'snaphu' or section.getboolean('rmfilter', fallback=False):
        raise WorkspaceError('既存結果の復元は標準SNAPHU・rmfilter=falseのみ対応します。')
    unw = local(section['unw'])
    coh = local(section['coh'])
    def shape(path):
        tree = ET.parse(str(path) + '.xml').getroot()
        sizes = []
        for name in ('coordinate1', 'coordinate2'):
            sizes.append(int(tree.find(f"component[@name='{name}']/property[@name='size']/value").text))
        if min(sizes) <= 0:
            raise WorkspaceError('画像寸法が不正です。')
        return tuple(sizes)
    width, height = shape(coh)
    result = []
    for p, bytes_per_pixel in ((unw, 8), (Path(str(unw)+'.conncomp'), 1)):
        if shape(p) != (width, height) or p.stat().st_size != width * height * bytes_per_pixel:
            raise WorkspaceError(f'未完了または寸法・サイズが不正です: {p}')
        vrt = ET.parse(str(p)+'.vrt').getroot()
        if (int(vrt.get('rasterXSize')), int(vrt.get('rasterYSize'))) != (width, height):
            raise WorkspaceError(f'VRTの寸法が不正です: {p}')
        for f in (p, Path(str(p)+'.xml'), Path(str(p)+'.vrt')):
            if f.is_symlink() or not f.is_file():
                raise WorkspaceError(f'通常ファイルではありません: {f}')
            st = f.stat()
            result.append({'path': str(f), 'size': st.st_size, 'mtime_ns': st.st_mtime_ns})
    if scan:
        import numpy as np
        tree = ET.parse(str(unw)+'.xml').getroot()
        props = {p.get('name').lower(): p.findtext('value') for p in tree.findall('property')}
        if props.get('scheme', '').upper() != 'BIL' or props.get('data_type', '').upper() != 'FLOAT':
            raise WorkspaceError('標準のFloat32 BIL出力ではありません。')
        data = np.memmap(unw, dtype=('>f4' if props.get('byte_order') == 'b' else '<f4'), mode='r', shape=(height, 2, width))
        labels = np.memmap(str(unw)+'.conncomp', dtype='u1', mode='r', shape=(height, width))
        valid_count = 0
        for row in range(0, height, 128):
            phase = data[row:row+128, 1, :]
            valid = labels[row:row+128] > 0
            if not np.isfinite(phase[valid]).all():
                raise WorkspaceError('連結成分内に非有限の位相があります。')
            valid_count += int(valid.sum())
        if not valid_count:
            raise WorkspaceError('有効な連結成分がありません。')
    return result


def legacy_completed(item, commands, directory, logs, host_root=None):
    """A serial runner starting command N proves commands 1..N-1 exited zero."""
    if item.get('command_results') is not None or item.get('unwrap_jobs') != 1:
        return {}
    previous = item.get('logs', [])
    if len(previous) > len(commands):
        return {}
    # Establish exact command order from runner-written log headers.
    for number, value in enumerate(previous):
        path = logs / Path(value).name
        with path.open() as stream:
            if shlex.split(stream.readline()) != commands[number]:
                raise WorkspaceError('過去ログとコマンド順が異なるため完了結果を復元できません。')
    result = {}
    for index, command in enumerate(commands[:max(0, len(previous)-1)]):
        try:
            evidence = products(command, directory, host_root, scan=True)
        except (OSError, ValueError, AttributeError, configparser.Error, ET.ParseError):
            continue
        result[index] = {'status': 'complete', 'command': command, 'outputs': evidence,
                         'log': previous[index], 'evidence': 'serial_successor_started_and_products_validated'}
    return result
