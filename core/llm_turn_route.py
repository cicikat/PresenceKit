"""Ephemeral tool-turn routing; never mutates registry configuration."""
from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import wraps
from uuid import uuid4
import logging

_leases: dict[int, int] = {}
_retired: dict[int, object] = {}


def defer_client_close(client) -> bool:
    """Reload retires leased pools; the last turn closes them on release."""
    key = id(client)
    if _leases.get(key, 0):
        _retired[key] = client
        return True
    return False


@dataclass
class TurnRoute:
    turn_id: str = field(default_factory=lambda: uuid4().hex)
    effective: object = None
    original: object = None
    category: str = ""
    char_id: str | None = None
    switch_reason: str = ""
    steps: int = 0
    closed: bool = False
    routes: dict = field(default_factory=dict)
    clients: dict = field(default_factory=dict)

    def freeze(self, primary, fallback, skip, category, char_id, explicit):
        key = (category, char_id, primary.name if explicit else None)
        if key not in self.routes:
            self.routes[key] = (primary, fallback, skip)
            for mc in (primary, fallback):
                client = getattr(mc, "client", None)
                if client is not None and id(client) not in self.clients:
                    self.clients[id(client)] = client
                    _leases[id(client)] = _leases.get(id(client), 0) + 1
        return self.routes[key]

    async def close(self):
        if self.closed:
            return
        self.closed = True
        closing = []
        for key in self.clients:
            remaining = _leases.get(key, 1) - 1
            if remaining:
                _leases[key] = remaining
            else:
                _leases.pop(key, None)
                client = _retired.pop(key, None)
                if client is not None:
                    closing.append(client)
        self.clients.clear()
        for client in closing:
            try:
                close = getattr(client, "close", None) or getattr(client, "aclose", None)
                await close()
            except Exception:
                logging.getLogger(__name__).warning("retired turn client close failed", exc_info=True)

    def select(self, primary, fallback, category, char_id, explicit):
        self.steps += 1
        if (not self.closed and not explicit and self.original is primary
                and self.effective is fallback and self.effective is not None
                and self.category == category and self.char_id == char_id):
            return self.effective
        return primary

    def accept(self, original, outcome, category, char_id):
        if not self.closed and outcome.ok and outcome.role == "fallback":
            self.original = original
            self.effective = outcome.mc
            self.category = category
            self.char_id = char_id
            self.switch_reason = outcome.failover_reason or self.switch_reason


_current: ContextVar[TurnRoute | None] = ContextVar("tool_turn_route", default=None)


def current_route():
    state = _current.get()
    return state if state is not None and not state.closed else None


class _RoutedStream:
    def __init__(self, source, state):
        self.source = source
        self.state = state

    def __aiter__(self):
        return self

    async def __anext__(self):
        token = _current.set(self.state)
        try:
            return await anext(self.source)
        except BaseException:
            await self.aclose()
            raise
        finally:
            _current.reset(token)

    async def aclose(self):
        try:
            await self.source.aclose()
        finally:
            await self.state.close()


def scoped_tool_turn(fn):
    """Own state across wait_for tasks and a lazily consumed final stream.

    Context tokens never survive a yield, so closing in a different task is
    safe. Captured child contexts retain a closed state after cancellation.
    """
    @wraps(fn)
    async def wrapped(*args, **kwargs):
        state = TurnRoute()
        token = _current.set(state)
        deferred = False
        try:
            result = await fn(*args, **kwargs)
            if hasattr(result, "__aiter__"):
                deferred = True
                return _RoutedStream(result, state)
            return result
        finally:
            if not deferred:
                await state.close()
            _current.reset(token)
    return wrapped
