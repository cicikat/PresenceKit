"""Backend-owned optional native reader. No spawn/network work at import or tool time."""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import sys
import time

import httpx

from core.config_loader import get_config
from core.no_outbound import assert_outbound_allowed
from core.xiaohongshu_install import executable, install_dir, supported

logger = logging.getLogger(__name__)
LOCAL_URL = 'http://127.0.0.1:18060'
_runtime = None


def _policy(cfg):
    raw = cfg.get('xiaohongshu', {})
    enabled = cfg.get('tools', {}).get('read_xiaohongshu', False)
    if isinstance(enabled, dict):
        enabled = enabled.get('enabled', False)
    if not enabled:
        return 'disabled'
    if not raw.get('local_service', False):
        return 'external'
    from core.sandbox import get_paths
    if get_paths().mode == 'test':
        return 'sandbox'
    if cfg.get('deployment', {}).get('mode', 'local') != 'local':
        return 'remote_deployment'
    if raw.get('reader_url', '').rstrip('/') != LOCAL_URL:
        return 'local_url_required'
    if not supported():
        return 'unsupported_platform'
    return ''


def status():
    result = {'installed': executable().is_file(), 'supported': supported(),
              'installer_available': bool(shutil.which('go')), 'state': 'not_started',
              'owned': False, 'login_status': 'not_checked', 'install_state': 'idle',
              'install_error': '', 'retry_in_seconds': 0}
    if _runtime is not None:
        result.update(_runtime.view())
    return result


def _spawn(command, *, cwd, env=None):
    options = {'creationflags': subprocess.CREATE_NO_WINDOW} if sys.platform == 'win32' else {'start_new_session': True}
    return subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **options)


def _stop_process(process):
    if process.poll() is not None:
        return
    if sys.platform == 'win32':
        subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=8, creationflags=subprocess.CREATE_NO_WINDOW)
    else:
        import signal
        os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


class ReaderService:
    def __init__(self):
        self.process = None
        self.worker = None
        self.installer = None
        self.install_task = None
        self.state = 'not_started'
        self.login_status = 'not_checked'
        self.install_state = 'idle'
        self.install_error = ''
        self.retry_at = 0.0
        self.started_at = 0.0
        self.lock = asyncio.Lock()
        self.login_lock = asyncio.Lock()

    def view(self):
        return {'state': self.state, 'owned': self.process is not None and self.process.poll() is None,
                'login_status': self.login_status, 'install_state': self.install_state,
                'install_error': self.install_error,
                'retry_in_seconds': max(0, int(self.retry_at - time.monotonic()))}

    async def _health(self):
        try:
            assert_outbound_allowed('xiaohongshu.local_health')
            async with httpx.AsyncClient(trust_env=False, timeout=2) as client:
                response = await client.get(LOCAL_URL + '/health', follow_redirects=False)
                payload = response.json()
            if response.status_code == 200 and payload.get('data', {}).get('service') == 'xiaohongshu-mcp':
                return 'ready'
            return 'port_conflict'
        except httpx.ConnectError:
            return 'offline'
        except Exception:
            return 'unavailable'

    async def _stop(self):
        if self.process is not None:
            await asyncio.to_thread(_stop_process, self.process)
            self.process = None

    async def reconcile(self):
        async with self.lock:
            reason = _policy(get_config())
            if reason:
                await self._stop()
                self.state = reason
                self.login_status = 'not_checked'
                return
            if self.process is not None and self.process.poll() is not None:
                self.process = None
                self.retry_at = time.monotonic() + 30
                self.login_status = 'not_checked'
            health = await self._health()
            if health == 'ready':
                self.state = 'running' if self.process is not None else 'reused'
                return
            if self.process is not None:
                if time.monotonic() - self.started_at < 120:
                    self.state = 'starting'
                    return
                await self._stop()
                self.retry_at = time.monotonic() + 30
            if time.monotonic() < self.retry_at:
                self.state = 'retry_wait'
                return
            if health != 'offline':
                self.state = health
                return
            if not executable().is_file():
                self.state = 'not_installed'
                return
            assert_outbound_allowed('xiaohongshu.local_start')
            env = os.environ.copy()
            env['COOKIES_PATH'] = str(install_dir() / 'cookies.json')
            # Do not inherit a generic auth token from another service.
            env.pop('AUTH_TOKEN', None)
            self.process = _spawn([str(executable()), '-port', '127.0.0.1:18060'], cwd=install_dir(), env=env)
            self.started_at = time.monotonic()
            self.state = 'starting'

    async def run(self):
        try:
            while True:
                try:
                    await self.reconcile()
                except Exception:
                    self.state = 'start_failed'
                    self.retry_at = time.monotonic() + 30
                    logger.warning('小红书本地服务启动/检查失败，稍后重试')
                await asyncio.sleep(5)
        finally:
            await self._stop()

    async def close(self):
        for task in (self.worker, self.install_task):
            if task:
                task.cancel()
        await asyncio.gather(*(t for t in (self.worker, self.install_task) if t), return_exceptions=True)
        await self._stop()
        self.state = 'stopped'

    def install(self):
        if self.install_task and not self.install_task.done():
            return
        reason = _policy(get_config())
        if reason:
            raise ValueError(reason)
        if not shutil.which('go'):
            raise ValueError('go_required')
        assert_outbound_allowed('xiaohongshu.install')
        self.install_state = 'installing'
        self.install_error = ''
        self.install_task = asyncio.create_task(self._install())

    async def _install(self):
        try:
            from pathlib import Path
            self.installer = _spawn([sys.executable, '-m', 'core.xiaohongshu_install'], cwd=Path(__file__).resolve().parent.parent)
            while self.installer.poll() is None:
                await asyncio.sleep(1)
            self.install_state = 'installed' if self.installer.returncode == 0 else 'failed'
            self.install_error = '' if self.installer.returncode == 0 else 'install_failed'
        except Exception:
            self.install_state, self.install_error = 'failed', 'install_failed'
        finally:
            if self.installer:
                await asyncio.to_thread(_stop_process, self.installer)
                self.installer = None

    async def login(self, action):
        if _policy(get_config()) or self.state not in {'running', 'reused'}:
            raise ValueError('service_not_ready')
        if self.login_lock.locked():
            raise ValueError('login_busy')
        async with self.login_lock:
            assert_outbound_allowed('xiaohongshu.login')
            try:
                async with httpx.AsyncClient(trust_env=False, timeout=45) as client:
                    response = await client.get(LOCAL_URL + '/api/v1/login/' + action, follow_redirects=False)
                    response.raise_for_status()
                    data = response.json().get('data', {})
                self.login_status = 'logged_in' if data.get('is_logged_in') else 'logged_out'
                result = {'login_status': self.login_status}
                if action == 'qrcode' and not data.get('is_logged_in'):
                    img = str(data.get('img') or '')
                    if not img.startswith('data:image/png;base64,') or len(img) > 500_000:
                        raise ValueError('invalid_qrcode')
                    result.update(img=img, timeout=data.get('timeout', '4m'))
                return result
            except Exception as exc:
                self.login_status = 'check_failed'
                raise ValueError('login_check_failed') from exc


async def startup():
    global _runtime
    if _runtime is not None:
        return
    _runtime = ReaderService()
    _runtime.worker = asyncio.create_task(_runtime.run(), name='xiaohongshu-local-service')
    # Bounded readiness wait at startup only, never in the chat/send path.
    for _ in range(20):
        await asyncio.sleep(0.5)
        if _runtime.state not in {'not_started', 'starting'}:
            break


async def shutdown():
    global _runtime
    if _runtime is not None:
        await _runtime.close()
        _runtime = None


def runtime():
    if _runtime is None:
        raise ValueError('backend_restart_required')
    return _runtime
