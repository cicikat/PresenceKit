from tests.fixtures.public_assets import TEST_CHAR_ID

from core.tools import file_path_hints, fs_browse

_UID = "owner"
_CHAR = TEST_CHAR_ID


def test_hint_only_current_owner_private_text(monkeypatch):
    monkeypatch.setattr('core.config_loader.get_config', lambda: {'scheduler': {'owner_id': 'owner'}})
    text = '读取 ./notes/report.md 和 report.txt'
    hint = file_path_hints.prompt_hint(text, uid='owner')
    assert hint['_layer'] == '11.5_file_path_hints'
    assert './notes/report.md' in hint['content']
    assert '不是授权' in hint['content']
    assert 'backend' in hint['content']
    assert file_path_hints.prompt_hint(text, uid='other') is None
    assert file_path_hints.prompt_hint(text, uid='owner', is_group=True) is None
    assert file_path_hints.prompt_hint(text, uid='owner', is_proactive=True) is None
    assert file_path_hints.prompt_hint('你好', uid='owner') is None


def test_relative_read_and_denial_reasons(tmp_path, sandbox, monkeypatch):
    root = tmp_path / 'allowed'
    root.mkdir()
    monkeypatch.setattr(fs_browse, '_project_data_dir', lambda: tmp_path / 'internal')
    monkeypatch.setattr(fs_browse, '_repo_root', lambda: tmp_path / 'repo')
    monkeypatch.setattr(fs_browse, '_fs_config', lambda: {
        'enabled': True, 'backend_read': True, 'external_read': True,
        'allow_roots': [str(root)],
    })
    (root / 'note.txt').write_text('正常文本', encoding='utf-8')
    assert fs_browse.fs_read('note.txt', user_id=_UID, char_id=_CHAR) == '正常文本'
    (root / 'binary.txt').write_bytes(b'\x00\x01\x02')
    assert fs_browse.fs_read('binary.txt', user_id=_UID, char_id=_CHAR) == 'unsupported_file_type'
    (root / 'secrets.txt').write_text('fixture', encoding='utf-8')
    assert fs_browse.fs_read('secrets.txt', user_id=_UID, char_id=_CHAR) == 'fixture'
    assert fs_browse.fs_read('../outside.txt', user_id=_UID, char_id=_CHAR) == 'path_not_found'
    assert fs_browse.fs_read(str(root), user_id=_UID, char_id=_CHAR) == 'not_a_file'
    state = fs_browse.effective_state()
    assert state['backend_read']['effective']
    assert state['external_read']['effective']
    monkeypatch.setattr('core.deployment_capabilities.is_remote_server', lambda: True)
    remote = fs_browse.effective_state()
    assert remote['external_read']['blocking_reason'] == 'disabled_remote_server_local_capability'
    assert remote['backend_read']['effective'] is True
    assert fs_browse.fs_read('note.txt', user_id=_UID, char_id=_CHAR) == 'disabled_remote_server_local_capability'


def test_ambiguous_relative_path(tmp_path, monkeypatch):
    roots = [tmp_path / 'one', tmp_path / 'two']
    for root in roots:
        root.mkdir()
        (root / 'note.txt').write_text('fixture', encoding='utf-8')
    monkeypatch.setattr(fs_browse, '_fs_config', lambda: {
        'enabled': True, 'external_read': True, 'allow_roots': list(map(str, roots)),
    })
    monkeypatch.setattr(fs_browse, '_project_data_dir', lambda: tmp_path / 'internal')
    monkeypatch.setattr(fs_browse, '_repo_root', lambda: tmp_path / 'repo')
    assert fs_browse.fs_read('note.txt', user_id=_UID, char_id=_CHAR) == 'path_not_found'
