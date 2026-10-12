import hashlib
from datetime import date

import pytest

from core import diary_mirror
from core.tools import diary_reader


@pytest.mark.asyncio
async def test_mirror_is_owned_by_sync_character(sandbox):
    content = 'alpha private diary'
    await diary_mirror.apply_batch(char_id='alpha', generation='g1', entries=[{
        'logical_date': '2026-10-12', 'content': content,
        'sha256': hashlib.sha256(content.encode()).hexdigest(), 'revision': 1,
    }])
    assert diary_mirror.read_entry(date(2026, 10, 12), char_id='alpha') == content
    assert diary_mirror.read_entry(date(2026, 10, 12), char_id='beta') == ''
    assert diary_mirror.status(char_id='beta')['entry_count'] == 0
    assert diary_mirror.has_any_entry(char_id='beta') is False


def test_local_diary_source_is_not_inherited(sandbox, monkeypatch):
    from core import config_loader
    root = sandbox.diary_fallback(char_id='alpha')
    root.mkdir(parents=True)
    (root / '2026-10-12.md').write_text('alpha only', encoding='utf-8')
    monkeypatch.setattr(config_loader, 'get_config', lambda: {'diary': {'obsidian_path': str(root)}})
    assert diary_reader.read_diary(date(2026, 10, 12), char_id='beta') == ''
    assert diary_reader.read_diary(date(2026, 10, 12), char_id='alpha') == 'alpha only'
    assert not sandbox.diary_fallback(char_id='beta').exists()


@pytest.mark.asyncio
async def test_scheduler_diary_injection_uses_current_character(sandbox, monkeypatch):
    from core.scheduler.triggers import diary
    from core.memory import diary_context
    diary_context.save('owner', 'alpha stays', char_id='alpha')
    monkeypatch.setattr(diary, '_cfg', lambda: {'enabled': True})
    monkeypatch.setattr(diary, '_is_ready', lambda _: True)
    monkeypatch.setattr(diary, '_owner_id', lambda: 'owner')
    monkeypatch.setattr(diary, '_active_char_id_or_none', lambda: 'beta')
    monkeypatch.setattr(diary, '_mark', lambda _: None)
    monkeypatch.setattr(diary_reader, 'read_recent', lambda **kw: 'beta new' if kw['char_id'] == 'beta' else 'wrong')
    await diary._check_diary_inject()
    assert diary_context.load('owner', char_id='beta') == 'beta new'
    assert diary_context.load('owner', char_id='alpha') == 'alpha stays'


def test_character_inventory_counts_only_owned_database_rows(sandbox):
    import sqlite3
    from core.character_data import inventory
    path = sandbox.quote_notebook_db()
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE quotes(char_id TEXT)')
        db.execute("INSERT INTO quotes VALUES('alpha')")
        db.execute("INSERT INTO quotes VALUES('alpha')")
        db.execute("INSERT INTO quotes VALUES('beta')")
    rows = {row['id']: row for row in inventory('alpha')['row_stores']}
    assert rows['quote_notebook']['count'] == 2
    assert rows['food_memory']['count'] == 0
