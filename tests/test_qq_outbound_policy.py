import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from core import qq_adapter, qq_service


@pytest.fixture
def setup(monkeypatch):
    cfg = {'qq': {}}
    monkeypatch.setattr(qq_service, 'get_config', lambda: cfg)
    monkeypatch.setattr(qq_service, '_service', None)
    socket = AsyncMock()
    socket.closed = False
    monkeypatch.setattr(qq_adapter, '_ws', socket)
    return cfg, socket


@pytest.mark.asyncio
async def test_default_blocks_all_message_types_without_read_block(setup, tmp_path):
    _, socket = setup
    await qq_adapter.send_message('10001', 'text')
    await qq_adapter.send_record('10001', 'base64://AA==')
    await qq_adapter.send_image('10001', str(tmp_path / 'missing.png'))
    assert await qq_adapter.ws_call('send_private_msg', {'user_id': 10001, 'message': 'text'}) is None
    socket.send_str.assert_not_called()
    # A read request still reaches the socket, even though this fixture has no reply.
    await qq_adapter.ws_call('get_status', timeout=0.001)
    assert json.loads(socket.send_str.call_args.args[0])['action'] == 'get_status'


@pytest.mark.asyncio
async def test_reply_target_hot_mute_and_child_task_revocation(setup, tmp_path):
    cfg, socket = setup
    image = tmp_path / 'image.png'
    image.write_bytes(b'image')
    with qq_service.reply_scope('10001'):
        await qq_adapter.send_message('10001', 'text')
        await qq_adapter.send_record('10001', 'base64://AA==')
        await qq_adapter.send_image('10001', str(image))
        await qq_adapter.send_message('10002', 'wrong recipient')
        await qq_adapter.send_message('10001', 'wrong group', True)
        await asyncio.create_task(qq_adapter.send_message('10001', 'background'))
        assert socket.send_str.await_count == 3
        cfg['qq']['outbound_mode'] = 'muted'
        await qq_adapter.send_message('10001', 'muted')
        cfg['qq']['outbound_mode'] = 'reply_only'
    await qq_adapter.send_message('10001', 'after turn')
    assert socket.send_str.await_count == 3


@pytest.mark.asyncio
async def test_fanout_keeps_custom_clients_and_never_qq(setup, monkeypatch):
    from channels import registry
    from channels.qq import QQChannel
    from core.turn_sink import _fanout, TurnSource
    from types import SimpleNamespace
    monkeypatch.setattr(qq_service, 'operational_state', lambda: {'connected': True})
    channels = {name: SimpleNamespace(name=name, is_active=True, send=AsyncMock())
                for name in ('desktop', 'mobile')}
    channels['qq'] = QQChannel('10001')
    monkeypatch.setattr(registry, '_channels', channels)
    monkeypatch.setattr('channels.desktop_ws.is_connected', lambda: True)
    monkeypatch.setattr('core.client_channels.enabled', lambda name: True)
    for source in (TurnSource.USER_CHAT, TurnSource.TRIGGER):
        targets, failures = await _fanout(assistant_text='text', uid='10001', fanout='all',
                                        behavior=None, source=source)
        assert set(targets) == {'desktop', 'mobile'} and not failures
    setup[0]['qq']['outbound_mode'] = 'all'
    assert channels['qq'].is_active


@pytest.mark.asyncio
async def test_main_ingress_opens_scope_only_for_qq(setup, monkeypatch):
    import main
    from types import SimpleNamespace
    assert hasattr(main.handle_message, '__wrapped__')
    @qq_service.qq_reply_handler
    async def handle(message, *, ingress=None):
        await qq_adapter.send_message(message['user_id'], 'reply')
    await handle({'user_id': '10001'})
    await handle({'user_id': '10001'}, ingress=SimpleNamespace(channel='wechat'))
    assert setup[1].send_str.await_count == 1


@pytest.mark.asyncio
async def test_deferred_reply_media_has_bounded_scope_and_respects_hot_mute(setup):
    cfg, socket = setup
    ready = asyncio.Event()
    async def media():
        await ready.wait()
        await qq_adapter.send_record('10001', 'base64://AA==')
    with qq_service.reply_scope('10001'):
        task = qq_service.create_reply_media_task(media())
    ready.set()
    await task
    assert socket.send_str.await_count == 1
    ready.clear()
    with qq_service.reply_scope('10001'):
        task = qq_service.create_reply_media_task(media())
    cfg['qq']['outbound_mode'] = 'muted'
    ready.set()
    await task
    assert socket.send_str.await_count == 1


@pytest.mark.asyncio
async def test_desktop_explicit_expression_sticker_still_schedules(sandbox, monkeypatch):
    from core.pipeline import Pipeline
    from core.write_envelope import stamp_user_chat
    from types import SimpleNamespace
    sent = AsyncMock()
    monkeypatch.setattr('core.output.sticker.maybe_send_sticker', sent)
    monkeypatch.setattr('core.memory.mood_state.update', lambda *args, **kwargs: None)
    pipeline = Pipeline(SimpleNamespace(name='Companion'), lore_engine=None)
    await pipeline.post_process('desktop-owner', 'hello', '<feel>开心</feel><say>hello</say>',
                                target_id='', envelope=stamp_user_chat())
    await asyncio.sleep(0)
    sent.assert_awaited_once()
    assert sent.call_args.args[1] is None
