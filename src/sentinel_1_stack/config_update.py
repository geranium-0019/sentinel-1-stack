"""Surgical YAML updates with optimistic conflict detection and atomic replacement."""
import fcntl
import json
import os
from pathlib import Path
import stat
import tempfile
from uuid import uuid4

import yaml
from yaml.nodes import MappingNode, ScalarNode
from .workspace import WorkspaceError


def field_text(text, value, section, key):
    try:
        # Anchors/aliases can make a local edit change another setting.
        if any(isinstance(t, (yaml.tokens.AnchorToken, yaml.tokens.AliasToken)) for t in yaml.scan(text)):
            raise WorkspaceError(f'アンカー・別名を含むYAMLは自動更新できません。{section}.{key}を手動で設定してください。')
        root = yaml.compose(text)
        def entries(node):
            if not isinstance(node, MappingNode):
                raise WorkspaceError('設定はマッピングで指定してください。')
            result = {}
            for key, val in node.value:
                if not isinstance(key, ScalarNode) or key.value in result:
                    raise WorkspaceError('重複キーを含む設定は自動更新できません。')
                result[key.value] = val
            return result
        top = entries(root)
        paths = top.get(section)
        values = entries(paths) if paths is not None else {}
        old = values.get(key)
        replacement = json.dumps(value, ensure_ascii=False)
        if old is not None:
            if not isinstance(old, ScalarNode):
                raise WorkspaceError(f'{section}.{key}は文字列またはnullにしてください。')
            return text[:old.start_mark.index] + replacement + text[old.end_mark.index:]
        if paths is None and root.flow_style:
            end = root.end_mark.index - 1
            return text[:end] + (', ' if root.value else '') + section + ': {' + key + ': ' + replacement + '}' + text[end:]
        if paths is None:
            return text.rstrip('\r\n') + '\n' + section + ':\n  ' + key + ': ' + replacement + '\n'
        if paths.flow_style:
            end = paths.end_mark.index - 1
            return text[:end] + (', ' if paths.value else '') + key + ': ' + replacement + text[end:]
        # Insert before the first existing path, preserving all existing lines/comments.
        first = paths.value[0][0].start_mark
        start = text.rfind('\n', 0, first.index) + 1
        return text[:start] + ' ' * first.column + key + ': ' + replacement + '\n' + text[start:]
    except yaml.YAMLError as exc:
        raise WorkspaceError(f'YAMLを自動更新できません: {exc}') from exc


def update_field(path, original, value, section, key):
    path = Path(path)
    updated = field_text(original.decode('utf-8'), value, section, key).encode('utf-8')
    # Validate that only {section}.{key} changed, even with unusual YAML scalar syntax.
    before = yaml.safe_load(original)
    expected = dict(before)
    expected[section] = {**before.get(section, {}), key: value}
    if yaml.safe_load(updated) != expected:
        raise WorkspaceError(f'コメントを保った更新ができません。{section}.{key}を手動で設定してください。')
    with path.with_name('.' + path.name + '.update.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if path.is_symlink() or path.read_bytes() != original:
            raise WorkspaceError(f'処理中に設定が変更されました。上書きしません。{section}.{key}を確認してください。')
        if before.get(section, {}).get(key) == value:
            print(f'設定確認: {section}.{key} は設定済みです: {value}')
            return original
        backup = path.with_name(path.name + '.' + uuid4().hex[:12] + '.bak')
        mode = stat.S_IMODE(path.stat().st_mode)
        with backup.open('xb') as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(original)
            stream.flush()
            os.fsync(stream.fileno())
        temp = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.' + path.name, delete=False) as stream:
                temp = Path(stream.name)
                os.fchmod(stream.fileno(), mode)
                stream.write(updated)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, path)
        finally:
            if temp is not None:
                temp.unlink(missing_ok=True)
        print(f'設定更新: {path}\n  {section}.{key}: {before.get(section, {}).get(key)} → {value}\nバックアップ: {backup}')

        return updated


def dem_text(text, value):
    return field_text(text, value, 'paths', 'dem')


def update_dem(path, original, value):
    return update_field(path, original, value, 'paths', 'dem')
