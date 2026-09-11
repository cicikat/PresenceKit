from datetime import datetime

from core.data_paths import DEFAULT_CHAR_ID
from core.memory import event_log, short_term
from core.memory.fixation_pipeline import capture_turn
from core.write_envelope import stamp_trigger
from admin.routers.chat_log import _parse_day
import pytest


def test_proactive_turn_survives_history_reload_without_fake_user(sandbox):
    capture_turn('fixture-owner', 'autonomy', 'Hello again.', turn_id='fixture-turn',
                 trigger_name='autonomy', envelope=stamp_trigger(), char_id=DEFAULT_CHAR_ID)
    rows = short_term.load('fixture-owner', char_id=DEFAULT_CHAR_ID)
    assert any(row['role'] == 'assistant' and row['content'] == 'Hello again.' for row in rows)
    assert not any(row['role'] == 'user' for row in rows)
    path = event_log._day_file_read('fixture-owner', datetime.now(), char_id=DEFAULT_CHAR_ID)
    entries = _parse_day(path.read_text(encoding='utf-8'))
    assert entries == [{'time': entries[0]['time'], 'user': '', 'assistant': 'Hello again.', 'turn_id': 'fixture-turn'}]


def test_new_trigger_has_own_time_block_after_chat(sandbox):
    event_log.append('fixture-owner', 'user', 'Question', turn_id='one')
    event_log.append('fixture-owner', 'assistant', 'Answer', turn_id='one')
    event_log.append('fixture-owner', 'assistant', 'Later', turn_id='two', trigger_name='autonomy')
    path = event_log._day_file_read('fixture-owner', datetime.now())
    entries = _parse_day(path.read_text(encoding='utf-8'))
    assert [row['assistant'] for row in entries] == ['Answer', 'Later']
    assert entries[1]['user'] == ''


@pytest.mark.asyncio
async def test_display_style_reloaded_from_ledger_not_memory(sandbox, monkeypatch):
    from admin.routers import chat_log
    capture_turn('fixture-owner', 'Question', 'Hello', turn_id='styled-turn',
                 envelope=stamp_trigger(), char_id=DEFAULT_CHAR_ID,
                 visible_reply='<hl>Hello</hl>')
    monkeypatch.setattr(chat_log, '_owner_qq', lambda: 'fixture-owner')
    monkeypatch.setattr(chat_log, '_resolve_char_id', lambda _: DEFAULT_CHAR_ID)
    result = await chat_log.get_day(datetime.now().strftime('%Y-%m-%d'))
    assert result['entries'][0]['assistant'] == 'Hello'
    assert result['entries'][0]['assistant_display_text'] == '<hl>Hello</hl>'
    assert '<hl>' not in str(short_term.load('fixture-owner', char_id=DEFAULT_CHAR_ID))
