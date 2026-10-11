"""Startup-owned QQ lifecycle and per-turn delivery revocation."""
import asyncio
import time
from contextvars import ContextVar
from contextlib import contextmanager
from functools import wraps

from core.config_loader import get_config

_service = None
_turn_generation = ContextVar('qq_turn_generation', default=None)
_reply_scope = ContextVar('qq_reply_scope', default=None)


def outbound_mode():
    mode = (get_config().get('qq') or {}).get('outbound_mode', 'reply_only')
    return mode if mode in {'reply_only', 'muted', 'all'} else 'muted'


@contextmanager
def reply_scope(target_id, is_group=False):
    # A shared revocable object prevents child tasks retaining a completed turn.
    scope = {'target': str(target_id), 'group': bool(is_group),
             'task': asyncio.current_task(), 'active': True, 'expires': time.monotonic() + 300}
    token = _reply_scope.set(scope)
    try:
        yield
    finally:
        scope['active'] = False
        _reply_scope.reset(token)


def message_delivery_allowed(target_id, is_group=False):
    if not delivery_allowed():
        return False
    mode = outbound_mode()
    if mode == 'all':
        return True
    scope = _reply_scope.get()
    return bool(mode == 'reply_only' and scope and scope['active']
                and time.monotonic() < scope['expires']
                and scope['task'] is asyncio.current_task()
                and scope['target'] == str(target_id) and scope['group'] == bool(is_group))


def qq_reply_handler(handler):
    @wraps(handler)
    async def wrapped(message, *, ingress=None):
        if ingress is None or ingress.channel == 'qq':
            with reply_scope(message.get('group_id') or message['user_id'], bool(message.get('group_id'))):
                return await handler(message, ingress=ingress)
        return await handler(message, ingress=ingress)
    return wrapped


def create_reply_media_task(coro):
    """Grant only the chosen reply's TTS/sticker job a bounded media send scope."""
    parent = _reply_scope.get()
    if parent is None:
        return asyncio.create_task(coro)

    async def run():
        scope = {**parent, 'task': asyncio.current_task(), 'active': True}
        token = _reply_scope.set(scope)
        try:
            return await coro
        finally:
            scope['active'] = False
            _reply_scope.reset(token)

    return asyncio.create_task(run())


def connection_settings():
    cfg = get_config()
    qq = cfg.get('qq') or {}
    reason = ('disabled' if not qq.get('enabled', False) else
              'standalone-blocked' if cfg.get('standalone_mode', False) else '')
    return reason, (qq.get('host', '127.0.0.1'), qq.get('port', 3001),
                    str((cfg.get('scheduler') or {}).get('owner_id') or ''))


def get_service():
    return _service


def operational_state():
    reason, _ = connection_settings()
    if _service is None:
        state = {'effective_state': reason or 'runtime_not_started', 'connected': False,
                 'runtime_started': False, 'last_error': ''}
    else:
        state = _service.snapshot()
    return {**state, 'outbound_mode': outbound_mode(),
            'broadcast_enabled': outbound_mode() == 'all'}


def delivery_allowed():
    # Direct adapter users retain compatibility; startup always installs service.
    return _service is None or _service.accepts(_turn_generation.get())


class QQService:
    def __init__(self, handler, *, connector=None):
        from core import qq_adapter
        self.handler = handler
        self.connector = connector or qq_adapter.connect_and_listen
        self.running = False
        self.task = None
        self.signature = None
        self.generation = 0
        self.last_error = ''
        self.lock = asyncio.Lock()

    def accepts(self, generation=None):
        reason, signature = connection_settings()
        return (self.running and not reason and signature == self.signature
                and self.task is not None and not self.task.done()
                and (generation is None or generation == (self, self.generation)))

    def snapshot(self):
        from core import qq_adapter
        reason, _ = connection_settings()
        connected = self.accepts() and qq_adapter.is_connected()
        return {'effective_state': reason or ('runtime_not_started' if not self.running else
                                             'connected' if connected else 'connecting'),
                'connected': bool(connected), 'runtime_started': self.running,
                'last_error': self.last_error}

    async def handle(self, message):
        generation = message.pop('_qq_runtime_generation', None)
        if generation is None or not self.accepts(generation):
            return
        token = _turn_generation.set(generation)
        try:
            await self.handler(message)
        finally:
            _turn_generation.reset(token)

    async def _stop(self):
        self.generation += 1
        if self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None
        from core import qq_adapter
        await qq_adapter.disconnect()

    async def apply(self):
        async with self.lock:
            reason, signature = connection_settings()
            desired = None if reason or not self.running else signature
            if desired == self.signature and (desired is None or self.task and not self.task.done()):
                return
            if self.task and self.task.done() and not self.task.cancelled():
                error = self.task.exception()
                self.last_error = type(error).__name__ if error else 'connection_closed'
            await self._stop()
            self.signature = desired
            if desired is None:
                return
            from core import message_queue, qq_adapter
            generation = (self, self.generation)

            async def receive(message):
                if self.accepts(generation):
                    await message_queue.enqueue({**message, '_qq_runtime_generation': generation})

            qq_adapter.on_message(receive)
            self.last_error = ''
            self.task = asyncio.create_task(self.connector(), name='qq-connection')

    async def run(self):
        from core import message_queue
        message_queue.set_handler(self.handle)
        self.running = True
        try:
            while True:
                await self.apply()
                await asyncio.sleep(1)
        finally:
            self.running = False
            async with self.lock:
                await self._stop()
                self.signature = None


def install_service(handler):
    global _service
    _service = QQService(handler)
    return _service
