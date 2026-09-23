"""Execute generated ISCE2 commands with checked child exits and persistent logs."""
import fcntl
import configparser
import xml.etree.ElementTree as ET
import hashlib
import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import time

import yaml

from . import config
from .prepare import parameters, OPTION_DEFAULTS
from .workspace import WorkspaceError, require_initialized


def add_arguments(parser):
    parser.add_argument('config', type=Path)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--resume', action='store_true', help='成功工程をスキップし、失敗・中断工程から再開')
    parser.add_argument('--unwrap-jobs', type=int, default=1,
                        help='アンラップの最大同時実行数（既定1、再生成なしで変更可能）')


def unwrap_enabled(document):
    value = document.get('processing', {}).get('unwrap', True)
    if type(value) is not bool:
        raise WorkspaceError('processing.unwrap は true / false で指定してください。省略時は true です。')
    return value


def command_groups(text):
    """Parse only the simple argv / background / wait syntax emitted by ISCE2."""
    group = []
    for line in text.splitlines():
        lexer = shlex.shlex(line, posix=True, punctuation_chars=';&|<>')
        lexer.whitespace_split = True
        args = list(lexer)
        if not args:
            continue
        if args == ['wait']:
            if group:
                yield group
                group = []
            continue
        background = args[-1] == '&'
        if background:
            args.pop()
        if not args or any(x in (';', '&', '&&', '|', '||', '>', '>>', '<') or '$' in x or '`' in x for x in args):
            raise WorkspaceError(f'未対応のシェル構文です: {line}')
        group.append(args)
        if not background:
            yield group
            group = []
    if group:
        yield group  # Upstream sometimes omits the last wait.


def terminate(processes):
    for p in processes:
        try:
            os.killpg(p.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    until = time.monotonic() + 5
    while any(p.poll() is None for p in processes) and time.monotonic() < until:
        time.sleep(0.1)
    for p in processes:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        p.wait()


def failure_guidance(log_path, returncode=None):
    if returncode in (-signal.SIGKILL, 128 + signal.SIGKILL):
        return (
            '\n【強制終了 SIGKILL】メモリ不足（OOM）または外部からの停止が考えられます。'
            '\nこの終了コードだけでは OOM と断定できません。ホストの dmesg と Docker/WSL のメモリを確認してください。'
            '\nアンラップ中の場合は、生成ファイルや YAML を変更せず、1ペアずつ再開してください。'
            '\n  scripts/run.sh run config/project.yaml --resume --unwrap-jobs 1'
            '\nconfig/project.yaml は使用中の設定ファイルへ置き換えてください。'
            '\n1ペアでも不足する場合は、利用可能メモリの増加や処理範囲・分割処理を検討してください。'
        )
    # Read only the tail; SAR processing logs can be very large.
    with log_path.open('rb') as stream:
        stream.seek(0, 2)
        stream.seek(max(0, stream.tell() - 65536))
        text = stream.read().decode('utf-8', errors='replace')
    if 'No points left for reliable ESD estimate' not in text:
        return ''
    return (
        '\n【NESD の有効画素不足】バースト重複領域に、コヒーレンス閾値を満たす画素がありません。'
        '\n海域が多い範囲や時間間隔の長い組などで起こります。'
        '\n対処手順:'
        '\n1. ESD のコヒーレンス画像と有効画素数を確認してください。閾値を下げるだけでは精度を保証できません。'
        '\n2. YAML の processing.esd_coherence_threshold を検討してください（既定0.85、比較例0.7）。'
        '\n3. 安定した陸域を含む bbox への拡張、または processing.num_overlap_connections の見直しも検討してください。'
        '\n   pairs.connections は最終干渉ペア用であり、NESD の接続数とは別です。'
        '\n4. 設定を変えた場合は、既存の processing/ と logs/ を別の場所へ退避してから init → prepare → run を実行してください。'
        '\n   設定ファイルは残してください。paths を変更している場合は対応するディレクトリを退避してください。'
        '\n実行例（config/project.yaml は使用中の設定ファイルに置き換える）:'
        '\n  scripts/run.sh init config/project.yaml'
        '\n  scripts/run.sh prepare config/project.yaml --dry-run'
        '\n  scripts/run.sh prepare config/project.yaml'
        '\n  scripts/run.sh run config/project.yaml'
        '\n設定変更後の単純な --resume は使用できません。geometry への変更も自動では行いません。'
    )


def execute_group(commands, cwd, logs, on_complete=None):
    processes, streams = [], []
    recorded = set()
    try:
        for command, path in zip(commands, logs):
            stream = path.open('x')
            streams.append(stream)
            stream.write(shlex.join(command) + '\n')
            stream.flush()
            processes.append(subprocess.Popen(command, cwd=cwd, stdout=stream,
                                               stderr=subprocess.STDOUT, start_new_session=True))
        while True:
            codes = [p.poll() for p in processes]
            for i, code in enumerate(codes):
                if code == 0 and i not in recorded:
                    if on_complete is not None:
                        on_complete(i)
                    recorded.add(i)
            for i, code in enumerate(codes):
                if code is not None and code != 0:
                    raise WorkspaceError(f'終了コード {code}: {shlex.join(commands[i])}\nログ: {logs[i]}' + failure_guidance(logs[i], code))
            if all(code is not None for code in codes):
                return
            time.sleep(0.2)
    finally:
        if any(p.poll() is None for p in processes):
            terminate(processes)
        for stream in streams:
            stream.close()


def save(path, record):
    temporary = path.with_suffix('.json.tmp')
    with temporary.open('w') as stream:
        stream.write(json.dumps(record, indent=2, ensure_ascii=False) + '\n')
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def route_native_log(directory, logs):
    source, target = directory / 'isce.log', logs / 'isce.log'
    if source.is_symlink():
        if source.resolve() != target.resolve():
            raise WorkspaceError('processing/isce.log が別の保存先を参照しています。')
        return
    if source.exists():
        if target.exists():
            raise WorkspaceError('isce.log が processing と logs の両方にあるため、整理が必要です。')
        source.rename(target)
    source.symlink_to(os.path.relpath(target, directory))


def execution_plan(config_path):
    settings, root = config.load(config_path)
    require_initialized(root, settings['directories'])
    directory = root / settings['paths']['processing']
    logs = root / settings['paths']['logs']
    document = yaml.safe_load(config_path.read_text())
    p, e, connections = parameters(document)
    unwrap = unwrap_enabled(document)
    record = json.loads((logs / 'prepare.json').read_text())
    if record.get('status') != 'prepared' or record['destination'] != str(directory):
        raise WorkspaceError('prepare が完了していないか、生成先が異なります。')
    original = yaml.safe_load((directory / 'project.yaml').read_text())
    original_p, original_e, original_c = parameters(original)
    p = {k: v for k, v in p.items() if k != 'unwrap'}
    original_p = {k: v for k, v in original_p.items() if k != 'unwrap'}
    if (p, e, connections) != (original_p, original_e, original_c) or settings['paths'] != config.read(directory / 'project.yaml')['paths']:
        raise WorkspaceError('生成後に処理設定が変更されています。prepare を再生成してください（unwrap の切替だけは再生成不要）。')
    files = sorted((directory / 'run_files').glob('run_*'))
    if not files or not any(f.name.endswith('_unwrap') for f in files):
        raise WorkspaceError('アンラップを含む生成済み run_files が必要です。')
    fingerprint = hashlib.sha256()
    for f in files + sorted((directory / 'configs').glob('*')):
        if not f.is_file() or f.is_symlink():
            raise WorkspaceError(f'通常ファイルではありません: {f}')
        fingerprint.update(str(f.relative_to(directory)).encode())
        fingerprint.update(f.read_bytes())
    # Preserve fingerprints of plans generated before these explicit default options existed.
    fingerprint_p = {k: v for k, v in p.items() if k not in OPTION_DEFAULTS or v != OPTION_DEFAULTS[k]}
    fingerprint.update(json.dumps([fingerprint_p, e, connections, settings['paths']], sort_keys=True).encode())
    selected = [f for f in files if unwrap or not f.name.endswith('_unwrap')]
    steps = [(f.name, list(command_groups(f.read_text()))) for f in selected]
    return directory, logs, fingerprint.hexdigest(), steps, unwrap


def unwrap_checkpoints(item, groups, directory, logs):
    from .unwrap_resume import legacy_completed
    commands = [command for group in groups for command in group]
    if 'command_results' not in item:
        return legacy_completed(item, commands, directory, logs)
    result = {}
    for key, record in item['command_results'].items():
        index = int(key)
        if index < 0 or index >= len(commands) or record.get('command') != commands[index]:
            raise WorkspaceError('アンラップの完了記録とコマンドが一致しません。')
        if record.get('status') != 'complete' or record.get('cacheable') is False:
            continue
        outputs = record.get('outputs', [])
        try:
            if any(not Path(f['path']).is_file() or Path(f['path']).is_symlink()
                   or Path(f['path']).stat().st_size != f['size']
                   or Path(f['path']).stat().st_mtime_ns != f['mtime_ns'] for f in outputs):
                continue
        except OSError:
            continue
        result[index] = record
    return result


def run(args):
    try:
        unwrap_jobs = getattr(args, 'unwrap_jobs', 1)
        if type(unwrap_jobs) is not int or unwrap_jobs < 1:
            raise WorkspaceError('--unwrap-jobs は1以上の整数で指定してください。')
        directory, logs, fingerprint, steps, unwrap = execution_plan(args.config)
        print(f'アンラップ: {"実行" if unwrap else "省略（processing.unwrap: false）"}')
        print(f'アンラップ最大同時実行数: {unwrap_jobs}（生成済みの wait 境界も保持）')
        print(f'処理先: {directory}\nログ: {logs}')
        for name, groups in steps:
            print(f'  {name}: {sum(map(len, groups))} コマンド')
        if args.dry_run:
            if args.resume and (logs / 'run.json').exists():
                prior = json.loads((logs / 'run.json').read_text())
                if prior['fingerprint'] != fingerprint:
                    raise WorkspaceError('生成スクリプト・設定が変わっているため再開できません。')
                for name, groups in steps:
                    if name.endswith('_unwrap'):
                        prior_item = prior.get('steps', {}).get(name, {})
                        done = unwrap_checkpoints(prior_item, groups, directory, logs)
                        print(f'アンラップ再開予定: 完了 {len(done)} / 残り {sum(map(len, groups))-len(done)} ペア')
            return 0
        with (logs / '.run.lock').open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise WorkspaceError('同じプロジェクトで処理が実行中です。') from exc
            path = logs / 'run.json'
            if path.exists():
                if not args.resume:
                    raise WorkspaceError('実行記録があります。再開は scripts/run.sh run config/project.yaml --resume を使用してください。')
                state = json.loads(path.read_text())
                if state['fingerprint'] != fingerprint:
                    raise WorkspaceError('生成スクリプト・設定が変わっているため再開できません。')
            else:
                state = {'fingerprint': fingerprint, 'steps': {}}
            if args.resume and any(name.endswith('_unwrap') and state.get('steps', {}).get(name, {}).get('logs')
                                   and 'command_results' not in state['steps'][name] for name, _ in steps):
                backup = logs / ('run.before-pair-resume-' + str(time.time_ns()) + '.json')
                save(backup, state)
                print(f'旧実行記録のバックアップ: {backup}')
            if args.resume and any(name.endswith('_unwrap') and state.get('steps', {}).get(name, {}).get('logs')
                                   and 'command_results' not in state['steps'][name] for name, _ in steps):
                backup = logs / ('run.before-pair-resume-' + str(time.time_ns()) + '.json')
                save(backup, state)
                print(f'旧実行記録のバックアップ: {backup}')
            route_native_log(directory, logs)
            state['unwrap'] = unwrap
            state['unwrap_jobs'] = unwrap_jobs
            with (logs / 'run.log').open('a') as summary:
                def report(message):
                    print(message, flush=True)
                    summary.write(time.strftime('%Y-%m-%dT%H:%M:%S%z ') + message + '\n')
                    summary.flush()
                try:
                    state['status'] = 'running'
                    save(path, state)
                    for name, groups in steps:
                        item = state['steps'].setdefault(name, {'attempts': 0})
                        if item.get('status') == 'complete':
                            report('完了済み: ' + name)
                            continue
                        is_unwrap = name.endswith('_unwrap')
                        completed = unwrap_checkpoints(item, groups, directory, logs) if is_unwrap else {}
                        indexed = []
                        offset = 0
                        for group in groups:
                            indexed.append([(offset+i, command) for i, command in enumerate(group)])
                            offset += len(group)
                        if item.get('logs'):
                            item.setdefault('log_history', []).append(list(item['logs']))
                        item.update(status='running', attempts=item['attempts'] + 1, logs=[])
                        if is_unwrap:
                            item['unwrap_jobs'] = unwrap_jobs
                            item['command_results'] = {str(k): v for k, v in completed.items()}
                            indexed = [[entry for entry in group if entry[0] not in completed] for group in indexed]
                            indexed = [group[i:i+unwrap_jobs] for group in indexed for i in range(0, len(group), unwrap_jobs)]
                            report(f'アンラップ: 完了済み {len(completed)} ペアを再利用 / 残り {offset-len(completed)} ペア')
                        save(path, state)
                        report('開始: ' + name)
                        for group in indexed:
                            if not group:
                                continue
                            commands = [command for _, command in group]
                            names = [logs / f'{name}_attempt{item["attempts"]:03d}_{index+1:03d}.log' for index, _ in group]
                            item['logs'].extend(map(str, names))
                            save(path, state)
                            if is_unwrap:
                                def checkpoint(position):
                                    index, command = group[position]
                                    result = {'status': 'complete', 'command': command, 'log': str(names[position]),
                                              'evidence': 'exit_zero'}
                                    try:
                                        from .unwrap_resume import products
                                        result['outputs'] = products(command, directory)
                                    except (OSError, ValueError, AttributeError, configparser.Error, ET.ParseError):
                                        # Unsupported/invalid native outputs are rerun on interruption.
                                        result['cacheable'] = Path(command[0]).name != 'SentinelWrapper.py'
                                    item['command_results'][str(index)] = result
                                    save(path, state)
                                execute_group(commands, directory, names, on_complete=checkpoint)
                                for position in range(len(group)):
                                    if str(group[position][0]) not in item['command_results']:
                                        checkpoint(position)
                            else:
                                execute_group(commands, directory, names)
                        item['status'] = 'complete'
                        save(path, state)
                        report('完了: ' + name)
                    state['status'] = 'complete'
                    state.pop('error', None)
                    report('指定された全工程が完了しました。')
                except BaseException as exc:
                    state['status'] = 'interrupted' if isinstance(exc, KeyboardInterrupt) else 'failed'
                    for item in state['steps'].values():
                        if item.get('status') == 'running':
                            item['status'] = state['status']
                    state['error'] = str(exc)
                    report('停止: ' + str(exc))
                    raise
                finally:
                    save(path, state)
        return 0
    except KeyboardInterrupt:
        print('中断しました。', file=sys.stderr)
        return 130
    except (OSError, ValueError, RuntimeError, KeyError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 2
