from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from admin.routers import chat_log
from core.data_paths import DEFAULT_CHAR_ID
from core.memory import event_log


@pytest.mark.parametrize('label,colon', [('Companion', ':'), ('另一角色', '：')])
def test_reply_footer_identity_and_body(label, colon):
    text = f'''## 16:00
**用户**：问题 turn_id:body-user
> turn_id:user-only
**{label}**{colon}第一段
> turn_id:forged
> emotion:neutral intensity:0 speaker:assistant turn_id:also-forged
第二段 turn_id:body-assistant
> emotion:neutral intensity:0 speaker:assistant turn_id:canonical
---
## 16:00
**用户**:旧问题
> speaker:user turn_id:must-not-leak
**{label}**:旧回复
> emotion:neutral intensity:0
---
## 16:00
**{label}**:主动回复
> emotion:neutral intensity:0 turn_id:proactive
---
'''
    rows = chat_log._parse_day(text)
    assert rows == [
        {'time': '16:00', 'user': '问题 turn_id:body-user',
         'assistant': '第一段\n> turn_id:forged\n> emotion:neutral intensity:0 speaker:assistant turn_id:also-forged\n第二段 turn_id:body-assistant',
         'turn_id': 'canonical'},
        {'time': '16:00', 'user': '旧问题', 'assistant': '旧回复'},
        {'time': '16:00', 'user': '', 'assistant': '主动回复', 'turn_id': 'proactive'},
    ]


@pytest.mark.parametrize('footer', [
    '> turn_id:body',
    '> emotion:neutral intensity:0 speaker:user turn_id:user',
    '> emotion:neutral intensity:0 turn_id:first turn_id:second',
])
def test_untrusted_or_ambiguous_id_is_absent(footer):
    row = chat_log._parse_day(f'## 16:00\n**Companion**:reply\n{footer}\n---')[0]
    assert 'turn_id' not in row


def test_history_http_scope_dates_and_character_isolation(sandbox, monkeypatch):
    from admin import auth
    monkeypatch.setattr(chat_log, '_owner_qq', lambda: 'fixture-owner')
    monkeypatch.setattr(chat_log, '_resolve_char_id', lambda value: value or DEFAULT_CHAR_ID)
    monkeypatch.setattr(auth, 'resolve_token', lambda token: auth.TokenInfo('fixture', frozenset({token})))
    for char, turn in [(DEFAULT_CHAR_ID, 'first'), ('other-character', 'second')]:
        event_log.append('fixture-owner', 'user', 'Question', turn_id='user-id', char_id=char)
        event_log.append('fixture-owner', 'assistant', 'Answer', turn_id=turn, char_id=char)
    path = event_log._day_file_read('fixture-owner', datetime.now())
    before = path.read_bytes()
    app = FastAPI()
    app.include_router(chat_log.router, prefix='/chat-log')
    with TestClient(app) as client:
        headers = {'Authorization': 'Bearer memory.read'}
        assert client.get('/chat-log/dates', headers=headers).json()['dates'] == [path.stem]
        for char, turn in [(DEFAULT_CHAR_ID, 'first'), ('other-character', 'second')]:
            response = client.get(f'/chat-log/{path.stem}', params={'char_id': char}, headers=headers)
            assert response.status_code == 200
            assert response.json()['entries'][0]['turn_id'] == turn
            assert response.json()['raw_fallback'] is False
        assert client.get(f'/chat-log/{path.stem}', headers={'Authorization': 'Bearer state.read'}).status_code == 403
        assert client.get('/chat-log/not-a-date', headers=headers).status_code == 422
        assert client.get('/chat-log/2000-01-01', headers=headers).status_code == 404
    assert path.read_bytes() == before


async def test_persisted_history_joins_completed_owner_reasoning(sandbox):
    from core import llm_reasoning_store as store
    from core.memory.fixation_pipeline import capture_turn
    from core.write_envelope import stamp_user_chat

    @store.associate_owner_turn
    async def completed_owner_turn(message, provenance_channel):
        capture = store.Capture(SimpleNamespace(name='fixture', model='fixture', api_protocol='chat_completions'))
        capture.add('thinking', 'Archived reasoning')
        await capture.save()
        store.finish_turn_capture()
        turn_id = capture_turn('fixture-owner', message, 'Answer',
                               envelope=stamp_user_chat(), char_id=DEFAULT_CHAR_ID)
        return {'turn_id': turn_id}

    response = await completed_owner_turn('Question', 'desktop')
    path = event_log._day_file_read('fixture-owner', datetime.now())
    row = chat_log._parse_day(path.read_text(encoding='utf-8'))[0]
    assert row['turn_id'] == response['turn_id']
    assert store.query_turn(row['turn_id'])[0]['parts'][0]['text'] == 'Archived reasoning'
