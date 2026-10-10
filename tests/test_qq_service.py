import asyncio
import json

import pytest

from core import qq_adapter, qq_service


@pytest.fixture
def config(monkeypatch):
    cfg = {'qq': {'enabled': False, 'host': '127.0.0.1', 'port': 3001},
           'scheduler': {'owner_id': 'owner'}}
    monkeypatch.setattr(qq_service, 'get_config', lambda: cfg)
    monkeypatch.setattr(qq_service, '_service', None)
    monkeypatch.setattr(qq_adapter, '_ws', None)
    monkeypatch.setattr(qq_adapter, '_pending_responses', {})
    return cfg


@pytest.mark.asyncio
async def test_enable_disable_readdress_and_standalone(config, monkeypatch):
    connections = []

    async def connect():
        connections.append(dict(config['qq']))
        await asyncio.Event().wait()

    async def handler(message):
        pytest.fail('unexpected message')

    service = qq_service.QQService(handler, connector=connect)
    service.running = True
    monkeypatch.setattr(qq_service, '_service', service)
    await service.apply()
    assert service.task is None
    config['qq']['enabled'] = True
    await service.apply()
    await asyncio.sleep(0)
    first = service.task
    assert len(connections) == 1
    await service.apply()
    assert service.task is first
    config['qq']['port'] = 3010
    assert not qq_service.delivery_allowed()  # gate before apply
    await service.apply()
    await asyncio.sleep(0)
    assert first.cancelled()
    assert connections[-1]['port'] == 3010
    config['standalone_mode'] = True
    await service.apply()
    assert service.task is None
    assert service.snapshot()['effective_state'] == 'standalone-blocked'
    config['standalone_mode'] = False
    await service.apply()
    assert service.task is not None
    config['qq']['enabled'] = False
    await service.apply()
    assert service.task is None
    assert service.snapshot()['effective_state'] == 'disabled'


@pytest.mark.asyncio
async def test_old_queued_and_running_turn_cannot_send_after_reenable(config, monkeypatch):
    entered, finish = asyncio.Event(), asyncio.Event()
    sent = []
    completed = []

    class Socket:
        closed = False

        async def close(self):
            self.closed = True

        async def send_str(self, payload):
            sent.append(payload)

    async def connect():
        await asyncio.Event().wait()

    async def handler(message):
        entered.set()
        await finish.wait()
        # Completion / local post-processing survives disable; delivery does not.
        completed.append(message['content'])
        await qq_adapter.send_message('123456', 'old reply')
        await qq_adapter.send_record('123456', 'base64://AA==')
        await qq_adapter.send_image('123456', 'base64://AA==')
        assert await qq_adapter.ws_call('get_status') is None

    service = qq_service.QQService(handler, connector=connect)
    service.running = True
    monkeypatch.setattr(qq_service, '_service', service)
    config['qq']['enabled'] = True
    await service.apply()
    generation = (service, service.generation)
    turn = asyncio.create_task(service.handle({'content': 'old', '_qq_runtime_generation': generation}))
    await entered.wait()
    config['qq']['enabled'] = False
    await service.apply()
    config['qq']['enabled'] = True
    await service.apply()
    monkeypatch.setattr(qq_adapter, '_ws', Socket())
    finish.set()
    await turn
    await service.handle({'content': 'queued', '_qq_runtime_generation': generation})
    assert completed == ['old']
    assert not sent
    await qq_adapter.send_message('123456', 'current reply')
    assert json.loads(sent[0])['params']['message'] == 'current reply'
    await service._stop()


@pytest.mark.asyncio
async def test_disconnect_clears_identity_and_releases_pending_requests(config, monkeypatch):
    class Socket:
        closed = False

        async def close(self):
            self.closed = True

    socket = Socket()
    future = asyncio.get_running_loop().create_future()
    monkeypatch.setattr(qq_adapter, '_ws', socket)
    monkeypatch.setattr(qq_adapter, '_self_id', '123456')
    qq_adapter._pending_responses['waiting'] = future
    await qq_adapter.disconnect()
    assert socket.closed and not qq_adapter.is_connected()
    assert future.result() is None
    assert not qq_adapter._self_id and not qq_adapter._pending_responses


@pytest.mark.asyncio
async def test_supervisor_shutdown_stops_connection(config, monkeypatch):
    connected = asyncio.Event()

    async def connect():
        connected.set()
        await asyncio.Event().wait()

    config['qq']['enabled'] = True
    service = qq_service.QQService(lambda message: None, connector=connect)
    monkeypatch.setattr('core.message_queue.set_handler', lambda handler: None)
    supervisor = asyncio.create_task(service.run())
    await connected.wait()
    task = service.task
    supervisor.cancel()
    await asyncio.gather(supervisor, return_exceptions=True)
    assert task.cancelled() and service.task is None and not service.running


@pytest.mark.asyncio
async def test_both_admin_save_paths_apply_existing_service(config, monkeypatch):
    from admin.routers import settings_clients, settings_feature_flags

    applied = []

    class Service:
        async def apply(self):
            applied.append(True)

    monkeypatch.setattr(qq_service, 'get_service', lambda: Service())
    monkeypatch.setattr(qq_service, 'operational_state', lambda: {'effective_state': 'connecting', 'connected': False})
    for router in (settings_clients, settings_feature_flags):
        monkeypatch.setattr(router, 'read_config_file', lambda path: config)
        monkeypatch.setattr(router, 'write_config_file', lambda path, data: None)
        monkeypatch.setattr(router, 'get_config', lambda: config)
    monkeypatch.setattr(settings_clients, 'reload_config', lambda: None)
    monkeypatch.setattr('core.config_loader.get_config', lambda: config)
    monkeypatch.setattr('core.client_channels.get_config', lambda: config)
    monkeypatch.setattr('core.config_loader.reload_config', lambda: None)
    await settings_clients.put_settings(settings_clients.ClientSettings(qq_port=3010), None)
    result = await settings_feature_flags.update_feature_flags(settings_feature_flags.FeatureFlagsUpdate(flags={'qq': True}), None)
    assert len(applied) == 2
    assert result['restart_required'] == []


@pytest.mark.asyncio
async def test_real_socket_reconnect_and_close_bypass_proxy(config, monkeypatch):
    from aiohttp import web

    sessions = []
    arrived = asyncio.Queue()
    closed = asyncio.Queue()
    ready = asyncio.Queue()
    original_session = qq_adapter.aiohttp.ClientSession
    original_fetch = qq_adapter._fetch_self_id

    async def fetch_identity(ws):
        await original_fetch(ws)
        await ready.put(True)

    def session(**kwargs):
        sessions.append(kwargs)
        return original_session(**kwargs)

    async def bridge(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await arrived.put(True)
        async for frame in ws:
            payload = json.loads(frame.data)
            if payload['action'] == 'get_login_info':
                await ws.send_json({'status': 'ok', 'echo': 'get_self_id', 'data': {'user_id': '123456'}})
        await closed.put(True)
        return ws

    app = web.Application()
    app.router.add_get('/', bridge)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    monkeypatch.setattr(qq_adapter, 'get_config', lambda: config)
    monkeypatch.setattr(qq_adapter.aiohttp, 'ClientSession', session)
    monkeypatch.setattr(qq_adapter, '_load_blacklist', lambda: None)
    monkeypatch.setattr(qq_adapter, '_startup_notify_sent', True)
    monkeypatch.setattr(qq_adapter, '_fetch_self_id', fetch_identity)
    monkeypatch.setattr('core.no_outbound.assert_outbound_allowed', lambda operation: None)
    service = qq_service.QQService(lambda message: None)
    service.running = True
    monkeypatch.setattr(qq_service, '_service', service)
    config['qq'].update(enabled=True, port=port)
    try:
        await service.apply()
        await asyncio.wait_for(arrived.get(), 2)
        await asyncio.wait_for(ready.get(), 2)
        assert service.snapshot()['connected']
        config['qq']['enabled'] = False
        await service.apply()
        await asyncio.wait_for(closed.get(), 2)
        assert not qq_adapter.is_connected()
        config['qq']['enabled'] = True
        await service.apply()
        await asyncio.wait_for(arrived.get(), 2)
        await asyncio.wait_for(ready.get(), 2)
        assert service.snapshot()['connected']
        assert all(item['trust_env'] is False for item in sessions)
    finally:
        await service._stop()
        await runner.cleanup()
