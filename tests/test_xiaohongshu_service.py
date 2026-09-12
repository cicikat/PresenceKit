from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import zipfile

import pytest

from core import xiaohongshu_install as installer
from core import xiaohongshu_service as service


@pytest.fixture
def local(monkeypatch, tmp_path):
    cfg = {'tools': {'read_xiaohongshu': {'enabled': True}},
           'xiaohongshu': {'local_service': True, 'reader_url': service.LOCAL_URL}}
    monkeypatch.setattr(service, 'get_config', lambda: cfg)
    monkeypatch.setattr('core.sandbox.get_paths', lambda: SimpleNamespace(mode='production'))
    monkeypatch.setattr(service, 'supported', lambda: True)
    exe = tmp_path / 'reader.exe'
    monkeypatch.setattr(service, 'executable', lambda: exe)
    monkeypatch.setattr(service, 'install_dir', lambda: tmp_path)
    monkeypatch.setattr(service, '_runtime', None)
    return cfg, exe


async def test_missing_install_never_spawns(local, monkeypatch):
    owner = service.ReaderService()
    owner._health = AsyncMock(return_value='offline')
    spawn = Mock()
    monkeypatch.setattr(service, '_spawn', spawn)
    await owner.reconcile()
    assert owner.state == 'not_installed'
    spawn.assert_not_called()


async def test_owned_start_disable_and_cleanup(local, monkeypatch):
    cfg, exe = local
    exe.touch()
    process = Mock()
    process.poll.return_value = None
    spawn = Mock(return_value=process)
    stop = Mock()
    monkeypatch.setattr(service, '_spawn', spawn)
    monkeypatch.setattr(service, '_stop_process', stop)
    owner = service.ReaderService()
    owner._health = AsyncMock(side_effect=['offline', 'ready'])
    await owner.reconcile()
    assert owner.state == 'starting'
    assert spawn.call_args.args[0] == [str(exe), '-port', '127.0.0.1:18060']
    assert spawn.call_args.kwargs['env']['COOKIES_PATH'] == str(exe.parent / 'cookies.json')
    assert 'AUTH_TOKEN' not in spawn.call_args.kwargs['env']
    await owner.reconcile()
    assert owner.state == 'running'
    spawn.assert_called_once()
    cfg['tools']['read_xiaohongshu']['enabled'] = False
    await owner.reconcile()
    stop.assert_called_once_with(process)
    assert owner.state == 'disabled'
    await owner.close()
    stop.assert_called_once()


async def test_existing_reader_is_reused_not_killed(local, monkeypatch):
    owner = service.ReaderService()
    owner._health = AsyncMock(return_value='ready')
    spawn, stop = Mock(), Mock()
    monkeypatch.setattr(service, '_spawn', spawn)
    monkeypatch.setattr(service, '_stop_process', stop)
    await owner.reconcile()
    assert owner.state == 'reused'
    assert not owner.view()['owned']
    await owner.close()
    spawn.assert_not_called()
    stop.assert_not_called()


@pytest.mark.parametrize('mode', ['external', 'disabled', 'sandbox', 'remote_deployment', 'local_url_required'])
async def test_disallowed_modes_do_not_probe_or_spawn(local, monkeypatch, mode):
    cfg, _ = local
    if mode == 'external': cfg['xiaohongshu']['local_service'] = False
    if mode == 'disabled': cfg['tools']['read_xiaohongshu']['enabled'] = False
    if mode == 'sandbox': monkeypatch.setattr('core.sandbox.get_paths', lambda: SimpleNamespace(mode='test'))
    if mode == 'remote_deployment': cfg['deployment'] = {'mode': 'remote_server'}
    if mode == 'local_url_required': cfg['xiaohongshu']['reader_url'] = 'https://example.invalid'
    owner = service.ReaderService()
    owner._health = AsyncMock()
    await owner.reconcile()
    assert owner.state == mode
    owner._health.assert_not_called()


async def test_crash_backoff_then_restart(local, monkeypatch):
    _, exe = local
    exe.touch()
    owner = service.ReaderService()
    owner.process = Mock()
    owner.process.poll.return_value = 1
    owner._health = AsyncMock(return_value='offline')
    spawn = Mock()
    monkeypatch.setattr(service, '_spawn', spawn)
    await owner.reconcile()
    assert owner.state == 'retry_wait'
    spawn.assert_not_called()
    owner.retry_at = 0
    await owner.reconcile()
    spawn.assert_called_once()


async def test_port_conflict_does_not_start_process(local, monkeypatch):
    owner = service.ReaderService()
    owner._health = AsyncMock(return_value='port_conflict')
    spawn = Mock()
    monkeypatch.setattr(service, '_spawn', spawn)
    await owner.reconcile()
    assert owner.state == 'port_conflict'
    spawn.assert_not_called()


async def test_shutdown_stops_owned_worker(local, monkeypatch):
    owner = service.ReaderService()
    owner.process = Mock()
    stop = Mock()
    monkeypatch.setattr(service, '_stop_process', stop)
    monkeypatch.setattr(service, '_runtime', owner)
    await service.shutdown()
    assert service._runtime is None
    stop.assert_called_once()


def test_portable_install_directory(monkeypatch, tmp_path):
    monkeypatch.setattr(installer.sys, 'platform', 'linux')
    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path / 'custom'))
    assert installer.executable() == tmp_path / 'custom/presencekit/xiaohongshu/xiaohongshu-mcp-managed'


def test_installer_rejects_archive_escape(tmp_path):
    archive = tmp_path / 'bad.zip'
    with zipfile.ZipFile(archive, 'w') as out:
        out.writestr('../outside.txt', 'bad')
    with pytest.raises(ValueError, match='unsafe_archive'):
        installer._extract(archive, tmp_path / 'extract')
    assert not (tmp_path / 'outside.txt').exists()


def test_existing_install_never_downloads_or_replaces_cookies(monkeypatch, tmp_path):
    exe = tmp_path / 'reader'
    exe.write_text('installed')
    monkeypatch.setattr(installer, 'executable', lambda: exe)
    monkeypatch.setattr(installer, 'supported', lambda: True)
    download = Mock()
    monkeypatch.setattr(installer.urllib.request, 'urlopen', download)
    installer.install()
    download.assert_not_called()
    assert exe.read_text() == 'installed'


def test_admin_install_login_scopes_and_local_url(local, monkeypatch, tmp_path):
    import yaml
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from admin import auth
    from admin.routers import settings_tools as routes
    cfg, _ = local
    path = tmp_path / 'config.yaml'
    path.write_text(yaml.safe_dump(cfg), encoding='utf-8')
    monkeypatch.setattr(routes, 'CONFIG_FILE', path)
    monkeypatch.setattr(routes, 'get_config', lambda: cfg)
    monkeypatch.setattr('core.config_loader.reload_config', lambda: None)
    monkeypatch.setattr(auth, 'resolve_token', lambda token: auth.TokenInfo('fixture', frozenset({token})))
    owner = Mock()
    owner.login = AsyncMock(return_value={'login_status': 'logged_in'})
    monkeypatch.setattr(service, 'runtime', lambda: owner)
    app = FastAPI()
    app.include_router(routes.router)
    client = TestClient(app)
    for endpoint in ['install', 'login/qrcode', 'login/status']:
        assert client.post('/settings/xiaohongshu/' + endpoint, headers={'Authorization': 'Bearer state.read'}).status_code == 403
    owner.install.assert_not_called()
    owner.login.assert_not_called()
    headers = {'Authorization': 'Bearer admin'}
    assert client.post('/settings/xiaohongshu/install', headers=headers).status_code == 202
    response = client.post('/settings/xiaohongshu/login/status', headers=headers)
    assert response.status_code == 200
    assert response.headers['cache-control'] == 'no-store'
    assert 'cookie' not in response.text
    body = {'enabled': True, 'local_service': True, 'reader_url': 'http://example.invalid'}
    assert client.put('/settings/xiaohongshu', json=body, headers=headers).status_code == 422
    body['reader_url'] = ''
    assert client.put('/settings/xiaohongshu', json=body, headers=headers).status_code == 200
    saved = yaml.safe_load(path.read_text(encoding='utf-8'))
    assert saved['xiaohongshu']['local_service']
    assert saved['xiaohongshu']['reader_url'] == service.LOCAL_URL
