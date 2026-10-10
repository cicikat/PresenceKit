import json
from unittest.mock import patch

import pytest

from core import character_cleanup as cleanup


@pytest.fixture
def cleanup_env(sandbox, monkeypatch):
    monkeypatch.setattr('core.config_loader.get_config', lambda: {})
    monkeypatch.setattr('core.pipeline_registry.get', lambda: None)
    monkeypatch.setattr('core.conversation_gate.locked_conversation_uids', lambda: [])
    monkeypatch.setattr('core.memory.locks.locked_uids', lambda: [])
    monkeypatch.setattr('core.memory.locks.locked_globals', lambda: [])
    monkeypatch.setattr('core.post_process.slow_queue.queue_size', lambda: 0)
    monkeypatch.setattr('core.post_process.slow_queue.current_task_type', lambda: None)
    monkeypatch.setattr(sandbox, 'legacy_character_cards_dir', lambda: sandbox._base / 'legacy-cards')
    monkeypatch.setattr(sandbox, 'legacy_authored_character_dir', lambda *, char_id: sandbox._base / 'legacy-authored' / char_id)
    monkeypatch.setattr(sandbox, 'legacy_reality_dir', lambda: sandbox._base / 'legacy-reality')
    from core.asset_registry import AssetRegistry
    registry = AssetRegistry()
    monkeypatch.setattr('core.asset_registry.get_registry', lambda: registry)
    monkeypatch.setattr('core.asset_registry.reload_registry', lambda: None)
    return sandbox


def seed(p, cid='role-remove'):
    card = p.character_card_write_path(cid)
    card.parent.mkdir(parents=True, exist_ok=True)
    card.write_bytes(b'{"name":"QA Role"}')
    memory = p.memory_char_root(char_id=cid) / 'owner-a' / 'episodic.json'
    memory.parent.mkdir(parents=True, exist_ok=True)
    memory.write_bytes(b'[]')
    return card, memory


def test_delete_restore_scope_and_shared_data(cleanup_env):
    p = cleanup_env
    card, memory = seed(p)
    other, other_memory = seed(p, 'role-keep')
    shared = p.user_stickers_dir() / 'happy' / 'shared.png'
    shared.parent.mkdir(parents=True)
    shared.write_bytes(b'png')
    plan = cleanup.preview('role-remove')
    assert plan['can_delete'] and plan['include_data']
    result = cleanup.remove('role-remove', True, plan['fingerprint'])
    assert not card.exists() and not memory.exists()
    assert other.exists() and other_memory.exists() and shared.exists()
    assert cleanup.history()[0]['status'] == 'deleted'
    cleanup.restore(result['id'])
    assert card.exists() and memory.exists()
    assert cleanup.history()[0]['status'] == 'restored'


def test_card_only_keeps_memory_and_removes_both_card_layers(cleanup_env):
    card, memory = seed(cleanup_env)
    legacy = cleanup_env.legacy_character_cards_dir() / card.name
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(b'{}')
    plan = cleanup.preview('role-remove', False)
    result = cleanup.remove('role-remove', False, plan['fingerprint'])
    assert not card.exists() and not legacy.exists() and memory.exists()
    cleanup.restore(result['id'])
    assert card.exists() and legacy.exists() and memory.exists()


def test_preview_change_rejects_without_deleting(cleanup_env):
    card, memory = seed(cleanup_env)
    plan = cleanup.preview('role-remove')
    memory.write_bytes(b'[1]')
    with pytest.raises(cleanup.CleanupConflict, match='数据已变化'):
        cleanup.remove('role-remove', True, plan['fingerprint'])
    assert card.exists() and memory.exists()


def test_default_and_group_roles_blocked(cleanup_env, monkeypatch):
    card, _ = seed(cleanup_env)
    monkeypatch.setattr('core.config_loader.get_config', lambda: {'character': {'default': 'role-remove'}})
    assert not cleanup.preview('role-remove')['can_delete']
    monkeypatch.setattr('core.config_loader.get_config', lambda: {})
    group = cleanup_env.stage_meta(group_id='group-one')
    group.parent.mkdir(parents=True)
    group.write_bytes(json.dumps({'roster': ['role-remove', 'role-keep']}).encode())
    assert not cleanup.preview('role-remove')['can_delete'] and card.exists()


def test_mid_move_failure_rolls_back(cleanup_env):
    card, memory = seed(cleanup_env)
    plan = cleanup.preview('role-remove')
    move = cleanup.shutil.move
    calls = 0
    def fail_second(src, dst):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError('fixture failure')
        return move(src, dst)
    with patch.object(cleanup.shutil, 'move', side_effect=fail_second), pytest.raises(OSError):
        cleanup.remove('role-remove', True, plan['fingerprint'])
    assert card.exists() and memory.exists()
    assert cleanup.history()[0]['status'] == 'rolled_back'


def test_restore_refuses_existing_data(cleanup_env):
    card, memory = seed(cleanup_env)
    result = cleanup.remove('role-remove', True, cleanup.preview('role-remove')['fingerprint'])
    seed(cleanup_env)
    with pytest.raises(cleanup.CleanupConflict, match='同 ID'):
        cleanup.restore(result['id'])
    assert card.exists() and memory.exists()


def test_unicode_orphan_data_can_be_removed(cleanup_env):
    cid = '\u65e7\u89d2\u8272'
    memory = cleanup_env.memory_char_root(char_id=cid)
    memory.mkdir(parents=True)
    (memory / 'state.json').write_bytes(b'{}')
    assert any(row['id'] == cid and row['orphan'] for row in cleanup.candidates())
    plan = cleanup.preview(cid)
    result = cleanup.remove(cid, True, plan['fingerprint'])
    assert not memory.exists()
    cleanup.restore(result['id'])
    assert memory.exists()


@pytest.mark.parametrize('cid', ['../other', 'a/b', 'a\\b', '.', '..'])
def test_unsafe_identity_refused(cleanup_env, cid):
    with pytest.raises(ValueError): cleanup.preview(cid)


def test_unfinished_task_blocked(cleanup_env):
    seed(cleanup_env)
    state = cleanup_env.agent_runtime_task_state('owner-a', char_id='role-remove')
    state.parent.mkdir(parents=True)
    state.write_bytes(b'{"tasks":[{"status":"running"}]}')
    assert not cleanup.preview('role-remove')['can_delete']
