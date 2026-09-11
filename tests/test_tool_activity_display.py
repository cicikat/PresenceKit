import asyncio
from types import SimpleNamespace

from core import tool_activity


def test_chain_and_status_without_arguments_or_results(monkeypatch):
    events = []
    monkeypatch.setattr('core.config_loader.get_config', lambda: {'scheduler': {'owner_id': 'owner'}})
    monkeypatch.setattr('core.memory.action_trace.finalize_display', lambda *args: None)
    async def send(event):
        events.append(event)
    monkeypatch.setattr('channels.desktop_ws._send_json', send)
    async def execute(*args, **kwargs):
        return SimpleNamespace(status='tool_executed', confirmation_request=None, result='private result')
    @tool_activity.display_chain
    async def chain():
        for name in ['get_time', 'weather']:
            await tool_activity.execute_visible(execute, name, {'secret': 'private argument'}, 'owner', 'owner', False, None, origin='autonomy_loop', char_id='char')
    asyncio.run(chain())
    assert [item['status'] for item in events] == ['running', 'success', 'running', 'success']
    assert len({item['chain_id'] for item in events}) == 1
    assert len({item['event_id'] for item in events}) == 2
    assert 'private' not in str(events)


def test_group_does_not_emit_owner_display(monkeypatch):
    monkeypatch.setattr('core.config_loader.get_config', lambda: {'scheduler': {'owner_id': 'owner'}})
    async def execute(*args, **kwargs): return 'ok'
    assert asyncio.run(tool_activity.execute_visible(execute, 'get_time', {}, 'owner', 'group', True, None, origin='assistant_loop', char_id='char')) == 'ok'


def test_only_trusted_action_footer_becomes_narration():
    from admin.routers.chat_log import _parse_day
    entries = _parse_day('## 12:00\n**角色**：做了一件事：get_time\n> emotion:neutral intensity:1 trigger:action_trace turn_id:receipt\n---\n')
    assert entries[0]['entry_kind'] == 'narration'
    assert entries[0]['turn_id'] == 'receipt'
    entries = _parse_day('## 12:00\n**角色**：做了一件事：get_time\n> emotion:neutral intensity:1\n---\n')
    assert 'entry_kind' not in entries[0]


def test_receipt_history_recovers_without_a_chat_file(sandbox, monkeypatch, tmp_path):
    from datetime import datetime
    from core.memory import action_trace
    from admin.routers import chat_log
    import time
    monkeypatch.setattr(action_trace, '_enabled', lambda: True)
    monkeypatch.setattr(chat_log, '_owner_qq', lambda: 'owner')
    monkeypatch.setattr(chat_log, '_resolve_char_id', lambda value: 'char')
    monkeypatch.setattr(chat_log, '_log_dir', lambda value: tmp_path)
    event = {'type': 'tool_activity', 'event_id': 'a', 'chain_id': 'c', 'char_id': 'char',
             'source': 'reality', 'origin': 'autonomy', 'tool_name': 'get_time', 'status': 'error', 'ts': time.time()}
    action_trace.finalize_display('owner', 'char', event)
    day = datetime.fromtimestamp(event['ts']).strftime('%Y-%m-%d')
    assert day in asyncio.run(chat_log.list_dates())['dates']
    result = asyncio.run(chat_log.get_day(day))
    assert result['entries'][0]['tool_activity'] == event
    action_trace.finalize_display('owner', 'char', {**event, 'status': 'success'})
    assert len(asyncio.run(chat_log.get_day(day))['entries']) == 1
