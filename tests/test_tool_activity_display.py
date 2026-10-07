import asyncio
import time
from types import SimpleNamespace

from core import tool_activity


def test_owner_request_links_only_its_receipts_and_resets_context(monkeypatch):
    links = []
    events = []
    monkeypatch.setattr('core.config_loader.get_config', lambda: {'scheduler': {'owner_id': 'owner'}})
    monkeypatch.setattr('core.memory.action_trace.finalize_display', lambda *args: None)
    monkeypatch.setattr('core.memory.action_trace.link_display_turn', lambda *args: links.append(args))
    async def send(event):
        events.append(event)
    monkeypatch.setattr('channels.desktop_ws._send_json', send)
    async def execute(*args, **kwargs):
        return SimpleNamespace(status='tool_executed', confirmation_request=None)
    @tool_activity.associate_owner_turn
    async def turn(*, request_id):
        await tool_activity.execute_visible(execute, 'get_time', {}, 'owner', 'owner', False, None,
                                            origin='assistant_loop', char_id='char')
        return {'turn_id': 'turn-' + request_id}
    async def run():
        await turn(request_id='req_a')
        await turn(request_id='req_b')
        await tool_activity.execute_visible(execute, 'get_time', {}, 'owner', 'owner', False, None,
                                            origin='assistant_loop', char_id='char')
    asyncio.run(run())
    assert [item[1:] for item in links] == [('turn-req_a', 'req_a'), ('turn-req_b', 'req_b')]
    assert links[0][0][0][2] != links[1][0][0][2]
    assert events[0]['request_id'] == 'req_a'
    assert events[2]['request_id'] == 'req_b'
    assert 'request_id' not in events[-1]


def test_link_display_preserves_status_and_never_recreates_evicted_receipts(sandbox, monkeypatch):
    from core.memory import action_trace
    monkeypatch.setattr(action_trace, '_enabled', lambda: True)
    event = {'event_id': 'existing', 'chain_id': 'chain', 'char_id': 'char', 'source': 'reality',
             'origin': 'chat', 'tool_name': 'get_time', 'status': 'unknown', 'ts': time.time()}
    action_trace.finalize_display('owner', 'char', event)
    action_trace.link_display_turn([('owner', 'char', 'existing'), ('owner', 'char', 'evicted')], 'turn', 'req')
    rows = action_trace.recent('owner', 'char', max_items=30)
    assert len(rows) == 1
    assert rows[0]['display_activity'] == {**event, 'turn_id': 'turn', 'request_id': 'req'}


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


def test_tool_activity_send_timeout_does_not_block_execution(monkeypatch):
    from channels import desktop_ws

    monkeypatch.setattr('core.config_loader.get_config', lambda: {'scheduler': {'owner_id': 'owner'}})
    monkeypatch.setattr('core.memory.action_trace.finalize_display', lambda *args: None)
    monkeypatch.setattr(desktop_ws, '_SEND_TIMEOUT_S', 0.05)
    monkeypatch.setattr(desktop_ws, '_TOOL_STATUS_SEND_TIMEOUT_S', 0.05)

    class _HangingWS:
        async def send_text(self, _text):
            await asyncio.sleep(1)

        async def close(self, code=1000, reason=""):
            return

    desktop_ws._current_ws = _HangingWS()

    async def execute(*args, **kwargs):
        return SimpleNamespace(status='tool_executed', confirmation_request=None, result='ok')

    try:
        started = time.perf_counter()
        outcome = asyncio.run(tool_activity.execute_visible(
            execute, 'get_time', {}, 'owner', 'owner', False, None,
            origin='assistant_loop', char_id='char',
        ))
        assert outcome.status == 'tool_executed'
        assert time.perf_counter() - started < 0.4
        assert desktop_ws.is_connected() is False
    finally:
        desktop_ws._current_ws = None


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
    event = {'type': 'tool_activity', 'event_id': 'a', 'chain_id': 'c', 'char_id': 'char',
             'source': 'reality', 'origin': 'autonomy', 'tool_name': 'get_time', 'status': 'error', 'ts': time.time()}
    action_trace.finalize_display('owner', 'char', event)
    day = datetime.fromtimestamp(event['ts']).strftime('%Y-%m-%d')
    assert day in asyncio.run(chat_log.list_dates(x_presence_session=None))['dates']
    result = asyncio.run(chat_log.get_day(day, x_presence_session=None))
    assert result['entries'][0]['tool_activity'] == event
    action_trace.finalize_display('owner', 'char', {**event, 'status': 'success'})
    assert len(asyncio.run(chat_log.get_day(day, x_presence_session=None))['entries']) == 1
