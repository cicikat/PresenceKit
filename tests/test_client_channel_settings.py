import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from core.client_channels import ClientChannelGate
from admin.routers import settings_clients as settings


def test_disabled_client_admission_keeps_other_routes(monkeypatch):
    monkeypatch.setattr('core.client_channels.get_config', lambda: {'client_channels': {'mobile': False}})
    app = FastAPI()
    app.add_middleware(ClientChannelGate)
    @app.get('/mobile/poll')
    @app.get('/desktop/chat')
    @app.get('/settings/clients')
    async def endpoint():
        return {'ok': True}
    client = TestClient(app)
    assert client.get('/mobile/poll').status_code == 503
    assert client.get('/desktop/chat').status_code == 200
    assert client.get('/settings/clients').status_code == 200


@pytest.mark.asyncio
async def test_disabled_mobile_never_writes_queue(monkeypatch):
    from channels.mobile import MobileChannel
    monkeypatch.setattr('core.client_channels.get_config', lambda: {'client_channels': {'mobile': False}})
    channel = MobileChannel()
    async def fail(*args, **kwargs):
        pytest.fail('disabled channel wrote queue')
    monkeypatch.setattr(channel, '_write_to_queue', fail)
    await channel.send('text', 'owner')
    await channel.send_with_behavior('text', 'owner', {})
    channel.touch()
    assert not channel.is_active


@pytest.mark.asyncio
async def test_client_setting_preserves_unrelated_config(monkeypatch):
    cfg = {'qq': {'enabled': True}, 'unrelated': {'keep': True}}
    monkeypatch.setattr(settings, 'read_config_file', lambda path: cfg)
    monkeypatch.setattr(settings, 'write_config_file', lambda path, data: None)
    monkeypatch.setattr(settings, 'reload_config', lambda: None)
    monkeypatch.setattr(settings, 'get_config', lambda: cfg)
    monkeypatch.setattr('core.client_channels.get_config', lambda: cfg)
    result = await settings.put_settings(settings.ClientSettings(mobile=False, qq_host='bridge.example', qq_port=3010), None)
    assert result['restart_required']
    assert result['clients']['mobile']['effective_state'] == 'disabled'
    assert cfg['unrelated'] == {'keep': True}
    assert cfg['qq']['enabled']


def test_invalid_connection_port_rejected():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        settings.ClientSettings(qq_port=65536)


@pytest.mark.asyncio
async def test_disabled_mobile_is_excluded_even_from_durable_mirror(monkeypatch):
    from channels import registry
    from channels.mobile import MobileChannel
    from core.turn_sink import _fanout
    monkeypatch.setattr('core.client_channels.get_config', lambda: {'client_channels': {'mobile': False}})
    monkeypatch.setattr(registry, '_channels', {'mobile': MobileChannel()})
    targets, failures = await _fanout(assistant_text='text', uid='owner', fanout='all', behavior=None)
    assert targets == []
    assert failures == {}
