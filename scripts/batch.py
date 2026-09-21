#!/usr/bin/env python3
"""Optional acquisition and processing pipeline; host standard library only."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import os
import signal
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import uuid

from launch import bind, docker_base, file_path
from sentinel_1_stack.workspace import WorkspaceError, check_layout

LAUNCHER = Path(__file__).with_name('run.sh')


def steps(args):
    base = [str(LAUNCHER), '--image', args.image]
    config = str(args.config)
    commands = []
    if not args.resume:
        commands += [('prepare-check', base + ['prepare', config, '--dry-run']),
                     ('prepare', base + ['prepare', config])]
    run = base + ['run', config, '--unwrap-jobs', str(args.unwrap_jobs)]
    if args.resume:
        run.append('--resume')
    commands.append(('run', run))
    export = base + ['export', config]
    if args.unwrap:
        export.append('--include-unwrapped')
    commands.append(('export', export))
    return commands


def execute(command, log):
    print('$ ' + shlex.join(command), flush=True)
    log.write('$ ' + shlex.join(command) + '\n')
    log.flush()
    with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True, errors='replace', start_new_session=True) as proc:
        try:
            for line in proc.stdout:
                print(line, end='', flush=True)
                log.write(line)
                log.flush()
            code = proc.wait()
        except KeyboardInterrupt:
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
            raise
    if code:
        raise RuntimeError(f'終了コード {code}: {shlex.join(command)}')


@contextmanager
def lock(root):
    with (root / '.batch.lock').open('a') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise WorkspaceError('同じ作業ディレクトリで一括処理が実行中です。') from exc
        yield


def acquire(args, info, root, log, run_id):
    if args.resume:
        if 'dem' not in info['settings']['paths']:
            raise WorkspaceError('--resumeには前回生成した一括実行用YAMLを指定してください。')
        return
    base = [str(LAUNCHER), '--image', args.image]
    original = str(args.config)
    execute(base + ['init', original], log)
    processing = root / info['settings']['paths']['processing']
    if any(processing.iterdir()):
        raise WorkspaceError('processingが空ではありません。生成済みの一括実行用YAMLで --resume するか、既存処理を退避してください。')
    download = base + ['download', original]
    if args.download_retries is not None:
        download += ['--retries', str(args.download_retries)]
    if args.download_jobs != 1:
        download += ['--jobs', str(args.download_jobs)]
    if args.json:
        download += ['--json', str(args.json.expanduser().absolute())]
    execute(download, log)
    orbit = base + ['orbit', original]
    if args.allow_restituted:
        orbit.append('--allow-restituted')
    execute(orbit, log)
    document = info['document']
    if not document.get('paths', {}).get('dem'):
        # A dedicated directory identifies this exact DEM invocation, including reuse.
        dem_logs = root / info['settings']['paths']['logs'] / run_id / 'dem'
        command = base + ['dem', original, '--log-dir', str(dem_logs)]
        if args.fill_missing_zero:
            command.append('--fill-missing-zero')
        execute(command, log)
        records = list(dem_logs.glob('dem_*.json'))
        if len(records) != 1:
            raise WorkspaceError('今回のDEM実行記録を一意に確認できません。')
        record = json.loads(records[0].read_text())
        if record['status'] != 'complete':
            raise WorkspaceError('DEMの完了記録を確認できません。')
        try:
            relative = Path(record['output']).relative_to('/work') / 'dem.wgs84'
        except ValueError as exc:
            raise WorkspaceError('DEMの出力が作業ルート内ではありません。') from exc
        for suffix in ('', '.xml', '.vrt'):
            if not (root / (str(relative) + suffix)).is_file():
                raise WorkspaceError('DEM一式が見つかりません。')
        document.setdefault('paths', {})['dem'] = str(relative)
    document['work_dir'] = str(root)
    destination = root / 'config' / (run_id + '.yaml')
    # JSON is valid YAML; retain every user setting without a host PyYAML dependency.
    with destination.open('x') as stream:
        json.dump(document, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    args.config = destination
    message = f'一括実行用の設定: {destination}'
    print(message, flush=True)
    log.write(message + '\n')
    retry = [str(Path(__file__).with_name('batch.sh')), str(destination), '--image', args.image, '--resume']
    print('解析中断後の再開: ' + shlex.join(retry), flush=True)
    log.write('解析中断後の再開: ' + shlex.join(retry) + '\n')


def main(argv=None):
    parser = argparse.ArgumentParser(description='設定YAMLからSLC・軌道・DEM取得とISCE2処理を一括実行する補助ツール')
    parser.add_argument('config', type=Path)
    parser.add_argument('--image', default='sentinel-1-stack:0.1.0-rc4')
    parser.add_argument('--image-archive', type=Path, help='docker loadする配布tar/tar.gz。省略時は読込済みイメージを使用')
    parser.add_argument('--json', type=Path, help='ASF JSONを指定（省略時は設定YAMLと同じ場所から自動選択）')
    parser.add_argument('--allow-restituted', action='store_true')
    parser.add_argument('--fill-missing-zero', action='store_true')
    parser.add_argument('--resume', action='store_true', help='prepareを省略し、既存処理をrun --resumeで再開')
    parser.add_argument('--download-retries', type=int, default=None, help='SLCの追加再試行回数（対応イメージでは既定3）')
    parser.add_argument('--download-jobs', type=int, default=1, help='SLCの同時取得数（既定1）')
    parser.add_argument('--unwrap-jobs', type=int, default=1)
    parser.add_argument('--plan', action='store_true', help='実行順だけを表示。Docker起動・イメージ読込・ファイル変更なし')
    args = parser.parse_args(argv)
    if args.download_retries is not None and args.download_retries < 0:
        parser.error('--download-retries は0以上で指定してください。')
    if args.download_jobs < 1:
        parser.error('--download-jobs は1以上で指定してください。')
    if args.unwrap_jobs < 1:
        parser.error('--unwrap-jobs は1以上で指定してください。')
    log = None
    log_path = None
    try:
        args.config = file_path(args.config)
        if args.image_archive:
            args.image_archive = file_path(args.image_archive)
        if args.plan:
            if args.image_archive:
                print(shlex.join(['docker', 'load', '-i', str(args.image_archive)]))
            print(f'イメージとYAMLの検査: {args.image} / {args.config}')
            if not args.resume:
                print('init → download（JSON自動選択または --json）→ orbit → dem（paths.dem未設定時）')
                print('DEMパスを設定した一括実行用YAMLのコピーを作成します。元のYAMLは変更しません。')
            args.unwrap = False
            for name, command in steps(args):
                print(f'{name}: {shlex.join(command)}')
            print('unwrap=true（既定）ならexportに --include-unwrapped を追加します。')
            print('計画表示のみ。入力ファイルの内容や処理可否は検証していません。')
            return 0
        # Retain bootstrap diagnostics even if Docker cannot load the image/config.
        log = tempfile.NamedTemporaryFile(mode='w+', prefix='sentinel-batch-', suffix='.log', delete=False)
        log_path = Path(log.name)
        if args.image_archive:
            execute(['docker', 'load', '-i', str(args.image_archive)], log)
        inspect = subprocess.run(['docker', 'image', 'inspect', args.image, '--format', '{{.Id}}'],
                                 text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if inspect.returncode:
            log.write(inspect.stderr)
            raise WorkspaceError('指定イメージを確認できません。Dockerの起動と --image / --image-archive を確認してください。')
        # All later calls use the immutable ID checked here.
        args.image = inspect.stdout.strip()
        metadata = subprocess.run(docker_base() + ['--network', 'none'] + bind(args.config, '/run/project.yaml', True) +
            [args.image, 'python', '-c',
             'import json,yaml; from sentinel_1_stack.config import read; '
             'from sentinel_1_stack.prepare import parameters; '
             'p="/run/project.yaml"; s=read(p); d=yaml.safe_load(open(p)); '
             'parameters(d); print(json.dumps({"settings":s,"unwrap":d["processing"].get("unwrap",True),"document":d}))'],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if metadata.returncode:
            log.write(metadata.stderr)
            raise WorkspaceError('処理設定を確認できません。swaths・bbox・基準日などを設定してから実行してください。')
        info = json.loads(metadata.stdout)
        settings = info['settings']
        args.unwrap = info['unwrap']
        root = Path(settings['work_dir']).expanduser()
        if not root.is_absolute():
            root = args.config.parent / root
        root = root.resolve()
        check_layout(root, settings['directories'])
        root.mkdir(parents=True, exist_ok=True)
        logs = root / settings['paths']['logs']
        logs.mkdir(parents=True, exist_ok=True)
        final_path = logs / ('batch_' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '_' + uuid.uuid4().hex[:8] + '.log')
        log.flush()
        log.seek(0)
        bootstrap = log.read()
        log.close()
        log = final_path.open('x')
        log.write(bootstrap)
        log_path.unlink()
        log_path = final_path
        print(f'一括実行ログ: {log_path}', flush=True)
        with lock(root):
            acquire(args, info, root, log, log_path.stem)
            for name, command in steps(args):
                log.write(f'開始: {name}\n')
                execute(command, log)
                log.write(f'完了: {name}\n')
        print('一括処理が完了しました。', flush=True)
        return 0
    except (WorkspaceError, RuntimeError, OSError, ValueError, KeyError) as exc:
        message = f'ERROR: {exc}'
        print(message, file=sys.stderr)
        if log is not None and not log.closed:
            log.write(message + '\n')
        return 1
    except KeyboardInterrupt:
        if log is not None and not log.closed:
            log.write('中断しました。\n')
        print('中断しました。', file=sys.stderr)
        return 130
    finally:
        if log is not None:
            log.close()
        if log_path:
            print(f'ログ: {log_path}', flush=True)


if __name__ == '__main__':
    raise SystemExit(main())
