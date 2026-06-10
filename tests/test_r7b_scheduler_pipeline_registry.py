"""
tests/test_r7b_scheduler_pipeline_registry.py — Fable R7-B: scheduler pipeline registry unification

Verifies that the scheduler reads its pipeline exclusively from pipeline_registry,
has no private _pipeline true-value, and that the deprecated set_pipeline shim
delegates correctly.

Coverage:
1.  _pipeline_send uses the pipeline currently held in pipeline_registry.
2.  Replacing the registry pipeline causes _pipeline_send to use the new object.
3.  set_pipeline() (deprecated shim) writes to pipeline_registry, not a local store.
4.  loop.py has no module-level _pipeline attribute (no private true-value).
5.  desktop_wake / _pipeline_send still finds the pipeline after hot-swap.
6.  Existing scheduler active-window tests do not regress.
"""
from __future__ import annotations

import asyncio
import sys
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _FakePipeline:
    def __init__(self, name: str = "default"):
        self.name = name

    async def fetch_context(self, uid, query):
        return {}

    def build_prompt(self, uid, prompt, context, **kwargs):
        return [{"role": "user", "content": prompt}], {}

    async def run_llm(self, messages):
        return f"reply-from-{self.name}"


def _make_perceive_accepted():
    """Return a mock receive_perceive_event that always ACCEPTs."""
    from unittest.mock import AsyncMock
    from types import SimpleNamespace

    async def _accept(event):
        return SimpleNamespace(
            status=_accepted_status(),
            event_id="e1",
            dedupe_key="k1",
        )
    return _accept


def _accepted_status():
    """Return the PerceiveStatus.ACCEPTED value without importing the full module."""
    from core.perceive_event import PerceiveStatus
    return PerceiveStatus.ACCEPTED


# ---------------------------------------------------------------------------
# 1. _pipeline_send reads from pipeline_registry
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pipeline_send_reads_from_registry(monkeypatch):
    """_pipeline_send must use the pipeline held in pipeline_registry."""
    import core.pipeline_registry as _preg
    from core.scheduler import loop

    pipeline = _FakePipeline("registered")
    monkeypatch.setattr(_preg, "_pipeline", pipeline)

    replies = []
    async def fake_record_assistant_turn(**kwargs):
        replies.append(kwargs)
        return SimpleNamespace(fanout_failures={})

    monkeypatch.setattr(loop, "_owner_id", lambda: "u1")
    monkeypatch.setattr(loop, "_user_active_recently", lambda: False)
    monkeypatch.setattr(loop, "_active_char_id_or_none", lambda: "yexuan")
    monkeypatch.setattr(loop, "_char_name", lambda: "叶瑄")
    monkeypatch.setattr("core.perceive_event.receive_perceive_event", _make_perceive_accepted())
    monkeypatch.setattr("core.scheduler.triggers.birthday._is_birthday_period", lambda: False)
    monkeypatch.setattr("core.conversation_gate.conversation_lock", _passthrough_lock)
    monkeypatch.setattr("core.turn_sink.record_assistant_turn", fake_record_assistant_turn)

    result = await loop._pipeline_send("hello", trigger_name="morning_greeting", kind="trigger")

    assert result == "reply-from-registered"


# ---------------------------------------------------------------------------
# 2. Hot-swap: replacing registry pipeline → _pipeline_send uses new object
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_hot_swap_registry_pipeline(monkeypatch):
    """After replacing pipeline in registry, _pipeline_send uses the new object."""
    import core.pipeline_registry as _preg
    from core.scheduler import loop

    first = _FakePipeline("first")
    second = _FakePipeline("second")

    # Start with 'first'
    monkeypatch.setattr(_preg, "_pipeline", first)

    replies = []
    async def fake_record_assistant_turn(**kwargs):
        replies.append(kwargs)
        return SimpleNamespace(fanout_failures={})

    monkeypatch.setattr(loop, "_owner_id", lambda: "u1")
    monkeypatch.setattr(loop, "_user_active_recently", lambda: False)
    monkeypatch.setattr(loop, "_active_char_id_or_none", lambda: "yexuan")
    monkeypatch.setattr(loop, "_char_name", lambda: "叶瑄")
    monkeypatch.setattr("core.perceive_event.receive_perceive_event", _make_perceive_accepted())
    monkeypatch.setattr("core.scheduler.triggers.birthday._is_birthday_period", lambda: False)
    monkeypatch.setattr("core.conversation_gate.conversation_lock", _passthrough_lock)
    monkeypatch.setattr("core.turn_sink.record_assistant_turn", fake_record_assistant_turn)

    r1 = await loop._pipeline_send("turn1", trigger_name="night_reminder", kind="trigger")
    assert r1 == "reply-from-first"

    # Hot-swap to 'second'
    _preg.register(second)
    r2 = await loop._pipeline_send("turn2", trigger_name="night_reminder", kind="trigger")
    assert r2 == "reply-from-second"


# ---------------------------------------------------------------------------
# 3. set_pipeline shim writes to pipeline_registry, not a local store
# ---------------------------------------------------------------------------

def test_set_pipeline_delegates_to_registry(monkeypatch):
    """set_pipeline() must update pipeline_registry._pipeline."""
    import core.pipeline_registry as _preg
    from core.scheduler import loop

    original = _preg.get()
    try:
        fake = _FakePipeline("via-set-pipeline")
        loop.set_pipeline(fake)

        assert _preg.get() is fake, (
            "set_pipeline() must write to pipeline_registry, not a private variable"
        )
    finally:
        # Restore registry state
        _preg.register(original)


# ---------------------------------------------------------------------------
# 4. loop.py has no module-level _pipeline attribute
# ---------------------------------------------------------------------------

def test_loop_has_no_private_pipeline_attribute():
    """core.scheduler.loop must not have a module-level _pipeline variable."""
    from core.scheduler import loop

    assert not hasattr(loop, "_pipeline"), (
        "loop._pipeline still exists as a module attribute — "
        "remove it so there is only one true-value (pipeline_registry)"
    )


# ---------------------------------------------------------------------------
# 5. _pipeline_send degrades gracefully when registry is empty
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pipeline_send_degrades_when_registry_empty(monkeypatch):
    """When registry returns None, _pipeline_send falls back to direct send."""
    import core.pipeline_registry as _preg
    from core.scheduler import loop

    monkeypatch.setattr(_preg, "_pipeline", None)

    sent = []
    async def fake_send(content, behavior=None):
        sent.append(content)

    monkeypatch.setattr(loop, "_owner_id", lambda: "u1")
    monkeypatch.setattr(loop, "_user_active_recently", lambda: False)
    monkeypatch.setattr(loop, "_send", fake_send)

    result = await loop._pipeline_send("fallback", trigger_name="morning_greeting", kind="trigger")

    assert result == "fallback"
    assert sent == ["fallback"]


# ---------------------------------------------------------------------------
# 6. Regression: existing active-window tests still pass
#    (covered by running test_scheduler_active_window.py together)
# ---------------------------------------------------------------------------

def test_loop_module_imports_cleanly():
    """Sanity: loop module imports without error and exposes expected API."""
    from core.scheduler import loop

    assert callable(loop.set_pipeline)
    assert callable(loop._pipeline_send)
    assert callable(loop.set_pipeline)


# ---------------------------------------------------------------------------
# Utility: async context manager that does nothing (passthrough lock)
# ---------------------------------------------------------------------------

from contextlib import asynccontextmanager

@asynccontextmanager
async def _passthrough_lock(uid):
    yield
