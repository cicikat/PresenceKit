"""
tests/test_dream_guard_fail_closed.py — P2.4: Dream guard fail-closed semantics

Coverage:
  Unit   (U1-U6): get_reality_guard_status() contract — ALLOW / BLOCK_ACTIVE / BLOCK_UNCERTAIN
  Desktop (D1-D5): _check_reality_not_in_dream() fail-closed HTTP behaviour
  Mobile  (M1):   mobile path uses the same guard function (fail-closed)
  Wake    (W1-W2): /desktop/wake Path B blocked under DREAM_ACTIVE and corrupt state
  Log     (L1):   corrupt file produces logger.error on core.dream.dream_state
"""

import logging

import pytest
from fastapi import HTTPException


_OWNER_ID = "99999"


# ── helpers ──────────────────────────────────────────────────────────────────

def _write_dream_state(sandbox, uid: str, status: str):
    from core.dream.dream_state import write_state
    write_state(uid, {"status": status, "user_id": uid})


def _write_corrupt_dream_state(sandbox, uid: str, content: str = "{corrupted_json"):
    from core.sandbox import get_paths
    path = get_paths().dream_state_path(uid)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _write_non_dict_dream_state(sandbox, uid: str):
    from core.sandbox import get_paths
    path = get_paths().dream_state_path(uid)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('["not", "a", "dict"]', encoding="utf-8")


# ═══════════════════════════════════════════════════════════════════════════════
# U1. File missing → ALLOW  (normal no-dream startup)
# ═══════════════════════════════════════════════════════════════════════════════

def test_guard_status_file_missing_allow(sandbox):
    from core.dream.dream_state import get_reality_guard_status, DreamGuardStatus
    assert get_reality_guard_status(_OWNER_ID) == DreamGuardStatus.ALLOW


# ═══════════════════════════════════════════════════════════════════════════════
# U2. Status REALITY_CHAT → ALLOW
# ═══════════════════════════════════════════════════════════════════════════════

def test_guard_status_reality_chat_allow(sandbox):
    from core.dream.dream_state import get_reality_guard_status, DreamGuardStatus, DreamStatus
    _write_dream_state(sandbox, _OWNER_ID, DreamStatus.REALITY_CHAT.value)
    assert get_reality_guard_status(_OWNER_ID) == DreamGuardStatus.ALLOW


# ═══════════════════════════════════════════════════════════════════════════════
# U3. Status DREAM_ACTIVE → BLOCK_ACTIVE
# ═══════════════════════════════════════════════════════════════════════════════

def test_guard_status_dream_active_block_active(sandbox):
    from core.dream.dream_state import get_reality_guard_status, DreamGuardStatus, DreamStatus
    _write_dream_state(sandbox, _OWNER_ID, DreamStatus.DREAM_ACTIVE.value)
    assert get_reality_guard_status(_OWNER_ID) == DreamGuardStatus.BLOCK_ACTIVE


# ═══════════════════════════════════════════════════════════════════════════════
# U4. Status DREAM_CLOSING → BLOCK_ACTIVE
# ═══════════════════════════════════════════════════════════════════════════════

def test_guard_status_dream_closing_block_active(sandbox):
    from core.dream.dream_state import get_reality_guard_status, DreamGuardStatus, DreamStatus
    _write_dream_state(sandbox, _OWNER_ID, DreamStatus.DREAM_CLOSING.value)
    assert get_reality_guard_status(_OWNER_ID) == DreamGuardStatus.BLOCK_ACTIVE


# ═══════════════════════════════════════════════════════════════════════════════
# U5. JSON corrupt → BLOCK_UNCERTAIN
# ═══════════════════════════════════════════════════════════════════════════════

def test_guard_status_json_corrupt_block_uncertain(sandbox):
    from core.dream.dream_state import get_reality_guard_status, DreamGuardStatus
    _write_corrupt_dream_state(sandbox, _OWNER_ID)
    assert get_reality_guard_status(_OWNER_ID) == DreamGuardStatus.BLOCK_UNCERTAIN


# ═══════════════════════════════════════════════════════════════════════════════
# U6. Valid JSON but not a dict → BLOCK_UNCERTAIN
# ═══════════════════════════════════════════════════════════════════════════════

def test_guard_status_non_dict_block_uncertain(sandbox):
    from core.dream.dream_state import get_reality_guard_status, DreamGuardStatus
    _write_non_dict_dream_state(sandbox, _OWNER_ID)
    assert get_reality_guard_status(_OWNER_ID) == DreamGuardStatus.BLOCK_UNCERTAIN


# ═══════════════════════════════════════════════════════════════════════════════
# D1. Desktop guard: DREAM_ACTIVE → HTTP 409
# ═══════════════════════════════════════════════════════════════════════════════

def test_desktop_guard_dream_active_raises_409(sandbox):
    from core.dream.dream_state import DreamStatus
    _write_dream_state(sandbox, _OWNER_ID, DreamStatus.DREAM_ACTIVE.value)

    from admin.routers.chat import _check_reality_not_in_dream
    with pytest.raises(HTTPException) as exc:
        _check_reality_not_in_dream(_OWNER_ID)
    assert exc.value.status_code == 409
    assert "梦里" in exc.value.detail or "dream active" in exc.value.detail


# ═══════════════════════════════════════════════════════════════════════════════
# D2. Desktop guard: DREAM_CLOSING → HTTP 409
# ═══════════════════════════════════════════════════════════════════════════════

def test_desktop_guard_dream_closing_raises_409(sandbox):
    from core.dream.dream_state import DreamStatus
    _write_dream_state(sandbox, _OWNER_ID, DreamStatus.DREAM_CLOSING.value)

    from admin.routers.chat import _check_reality_not_in_dream
    with pytest.raises(HTTPException) as exc:
        _check_reality_not_in_dream(_OWNER_ID)
    assert exc.value.status_code == 409


# ═══════════════════════════════════════════════════════════════════════════════
# D3. Desktop guard: REALITY_CHAT → no exception (allow)
# ═══════════════════════════════════════════════════════════════════════════════

def test_desktop_guard_reality_chat_allows(sandbox):
    from core.dream.dream_state import DreamStatus
    _write_dream_state(sandbox, _OWNER_ID, DreamStatus.REALITY_CHAT.value)

    from admin.routers.chat import _check_reality_not_in_dream
    _check_reality_not_in_dream(_OWNER_ID)  # must not raise


# ═══════════════════════════════════════════════════════════════════════════════
# D4. Desktop guard: file missing → allow (normal startup / no session yet)
# ═══════════════════════════════════════════════════════════════════════════════

def test_desktop_guard_missing_state_allows(sandbox):
    from admin.routers.chat import _check_reality_not_in_dream
    _check_reality_not_in_dream(_OWNER_ID)  # must not raise


# ═══════════════════════════════════════════════════════════════════════════════
# D5. Desktop guard: JSON corrupt → HTTP 409  (fail-closed, not fail-open)
# ═══════════════════════════════════════════════════════════════════════════════

def test_desktop_guard_corrupt_state_fail_closed(sandbox):
    _write_corrupt_dream_state(sandbox, _OWNER_ID)

    from admin.routers.chat import _check_reality_not_in_dream
    with pytest.raises(HTTPException) as exc:
        _check_reality_not_in_dream(_OWNER_ID)
    assert exc.value.status_code == 409
    assert "梦境状态" in exc.value.detail


# ═══════════════════════════════════════════════════════════════════════════════
# M1. Mobile guard: JSON corrupt → HTTP 409  (uses same _check_reality_not_in_dream)
# ═══════════════════════════════════════════════════════════════════════════════

def test_mobile_guard_corrupt_state_fail_closed(sandbox):
    _write_corrupt_dream_state(sandbox, _OWNER_ID)

    from admin.routers.chat import _check_reality_not_in_dream
    with pytest.raises(HTTPException) as exc:
        _check_reality_not_in_dream(_OWNER_ID)
    assert exc.value.status_code == 409
    assert "梦境状态" in exc.value.detail


# ═══════════════════════════════════════════════════════════════════════════════
# W1. Desktop wake Path B: DREAM_ACTIVE → dream_guard_blocked, LLM not called
# ═══════════════════════════════════════════════════════════════════════════════

async def test_desktop_wake_path_b_blocked_when_dream_active(sandbox, monkeypatch):
    from core.dream.dream_state import DreamStatus
    _write_dream_state(sandbox, _OWNER_ID, DreamStatus.DREAM_ACTIVE.value)

    monkeypatch.setattr(
        "core.config_loader.get_config",
        lambda: {"scheduler": {"owner_id": _OWNER_ID}},
    )

    pipeline_reached = []

    class _FakePipeline:
        async def fetch_context(self, *a, **kw):
            pipeline_reached.append("fetch_context")
            return {}
        def build_prompt(self, *a, **kw):
            return [], {}
        async def run_llm(self, msgs):
            pipeline_reached.append("run_llm")
            return "reply"

    import core.pipeline_registry as _reg
    monkeypatch.setattr(_reg, "_pipeline", _FakePipeline())

    from admin.routers.chat import desktop_wake
    result = await desktop_wake({})

    assert result["source"] == "dream_guard_blocked"
    assert result["reply"] is None
    assert not pipeline_reached


# ═══════════════════════════════════════════════════════════════════════════════
# W2. Desktop wake Path B: JSON corrupt → fail-closed, LLM not called
# ═══════════════════════════════════════════════════════════════════════════════

async def test_desktop_wake_path_b_blocked_when_corrupt(sandbox, monkeypatch):
    _write_corrupt_dream_state(sandbox, _OWNER_ID)

    monkeypatch.setattr(
        "core.config_loader.get_config",
        lambda: {"scheduler": {"owner_id": _OWNER_ID}},
    )

    pipeline_reached = []

    class _FakePipeline:
        async def fetch_context(self, *a, **kw):
            pipeline_reached.append("fetch_context")
            return {}
        def build_prompt(self, *a, **kw):
            return [], {}
        async def run_llm(self, msgs):
            pipeline_reached.append("run_llm")
            return "reply"

    import core.pipeline_registry as _reg
    monkeypatch.setattr(_reg, "_pipeline", _FakePipeline())

    from admin.routers.chat import desktop_wake
    result = await desktop_wake({})

    assert result["source"] in ("dream_guard_blocked", "dream_guard_error")
    assert result["reply"] is None
    assert not pipeline_reached


# ═══════════════════════════════════════════════════════════════════════════════
# L1. Corrupt file produces logger.error on core.dream.dream_state
# ═══════════════════════════════════════════════════════════════════════════════

def test_guard_corrupt_logs_error(sandbox, caplog):
    _write_corrupt_dream_state(sandbox, _OWNER_ID)

    with caplog.at_level(logging.ERROR, logger="core.dream.dream_state"):
        from core.dream.dream_state import get_reality_guard_status
        get_reality_guard_status(_OWNER_ID)

    assert any("dream_guard" in r.message for r in caplog.records)
