"""
tests/test_r7_config_truth.py — Fable R7-A: config truth unification

Verifies that memory.short_term_rounds is the single owner for the
short-term context budget. context.max_turns is not a read alias.

Coverage:
1.  get_history() reads memory.short_term_rounds (owner) when set.
2.  get_history() does not read context.max_turns when the owner is absent.
3.  When both exist, memory.short_term_rounds still wins.
4.  load_for_prompt() reads memory.short_term_rounds.
5.  Admin PUT /context-config writes memory.short_term_rounds.
6.  Admin PUT /context-config does NOT write context.max_turns.
7.  Admin GET /context-config returns value from memory.short_term_rounds.
8.  Admin GET /context-config does not fall back to context.max_turns.
9.  Docs no longer describe context.max_turns as current owner.
"""
from __future__ import annotations
from tests.fixtures.public_assets import TEST_CHAR_ID

import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).parent.parent
VALID_TOKEN = "r7-test-secret"


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    """Build a minimal FastAPI app with the settings_misc router wired in."""
    import admin.routers.settings_misc as sm

    # Redirect CONFIG_FILE to a temp path
    temp_cfg = tmp_path / "config.yaml"
    monkeypatch.setattr(sm, "CONFIG_FILE", temp_cfg)
    monkeypatch.setattr("admin.auth.get_admin_secret", lambda: VALID_TOKEN)

    # Prevent actual config reload side-effects
    with patch("core.config_loader.reload_config", return_value=None):
        from admin.routers.settings_misc import router as sm_router
        app = FastAPI()
        app.include_router(sm_router)
        yield TestClient(app), temp_cfg


# ---------------------------------------------------------------------------
# 1. get_history reads memory.short_term_rounds (owner)
# ---------------------------------------------------------------------------

def test_get_history_reads_memory_short_term_rounds(sandbox, monkeypatch):
    """get_history() uses memory.short_term_rounds when it is set."""
    import core.memory.short_term as st

    monkeypatch.setattr(st, "get_config", lambda: {"memory": {"short_term_rounds": 5}})
    # Append 10 rounds worth of messages
    for i in range(10):
        st.append("u1", "user", f"msg {i}", char_id=TEST_CHAR_ID)
        st.append("u1", "assistant", f"rep {i}", char_id=TEST_CHAR_ID)

    result = st.get_history("u1", char_id=TEST_CHAR_ID)
    assert len(result) <= 10, f"Expected ≤10 msgs for 5-round budget, got {len(result)}"


# ---------------------------------------------------------------------------
# 2. get_history ignores leftover context.max_turns
# ---------------------------------------------------------------------------

def test_get_history_ignores_legacy_context_max_turns(sandbox, monkeypatch):
    """get_history() uses the default 20 when memory.short_term_rounds is absent."""
    import core.memory.short_term as st

    monkeypatch.setattr(st, "get_config", lambda: {"context": {"max_turns": 3}})
    for i in range(10):
        st.append("u2", "user", f"msg {i}", char_id=TEST_CHAR_ID)
        st.append("u2", "assistant", f"rep {i}", char_id=TEST_CHAR_ID)

    result = st.get_history("u2", char_id=TEST_CHAR_ID)
    assert len(result) == 20, (
        f"Expected default 20-round budget (10 rounds × 2 msgs) when owner absent; got {len(result)}"
    )


# ---------------------------------------------------------------------------
# 3. memory.short_term_rounds wins over context.max_turns when both exist
# ---------------------------------------------------------------------------

def test_get_history_owner_wins_over_alias(sandbox, monkeypatch):
    """memory.short_term_rounds takes priority over context.max_turns."""
    import core.memory.short_term as st

    monkeypatch.setattr(st, "get_config", lambda: {
        "memory": {"short_term_rounds": 2},   # owner: 2 rounds
        "context": {"max_turns": 10},          # alias: 10 rounds
    })
    for i in range(10):
        st.append("u3", "user", f"msg {i}", char_id=TEST_CHAR_ID)
        st.append("u3", "assistant", f"rep {i}", char_id=TEST_CHAR_ID)

    result = st.get_history("u3", char_id=TEST_CHAR_ID)
    assert len(result) <= 4, (
        f"memory.short_term_rounds=2 should win over context.max_turns=10; got {len(result)} msgs"
    )


# ---------------------------------------------------------------------------
# 4. load_for_prompt reads memory.short_term_rounds
# ---------------------------------------------------------------------------

def test_load_for_prompt_reads_memory_short_term_rounds(sandbox, monkeypatch):
    """load_for_prompt() uses memory.short_term_rounds for the budget."""
    import core.memory.short_term as st

    monkeypatch.setattr(st, "get_config", lambda: {"memory": {"short_term_rounds": 3}})
    for i in range(10):
        st.append("u4", "user", f"q {i}", char_id=TEST_CHAR_ID)
        st.append("u4", "assistant", f"a {i}", char_id=TEST_CHAR_ID)

    result = st.load_for_prompt("u4", char_id=TEST_CHAR_ID)
    # load_for_prompt returns turn-groups, each group = 2 msgs; budget=3 → ≤6 msgs
    assert len(result) <= 6, (
        f"load_for_prompt with short_term_rounds=3 should return ≤6 msgs, got {len(result)}"
    )


# ---------------------------------------------------------------------------
# 5. Admin PUT writes memory.short_term_rounds
# ---------------------------------------------------------------------------

def test_admin_put_writes_memory_short_term_rounds(admin_client):
    """PUT /context-config persists value under memory.short_term_rounds."""
    client, temp_cfg = admin_client
    temp_cfg.write_text("memory:\n  short_term_rounds: 20\n", encoding="utf-8")

    resp = client.put(
        "/context-config",
        json={"max_turns": 15},
        headers={"Authorization": f"Bearer {VALID_TOKEN}"},
    )
    assert resp.status_code == 200

    saved = yaml.safe_load(temp_cfg.read_text(encoding="utf-8"))
    assert saved.get("memory", {}).get("short_term_rounds") == 15, (
        "PUT must write memory.short_term_rounds"
    )


# ---------------------------------------------------------------------------
# 6. Admin PUT does NOT write context.max_turns
# ---------------------------------------------------------------------------

def test_admin_put_does_not_write_context_max_turns(admin_client):
    """PUT /context-config must not create or update context.max_turns."""
    client, temp_cfg = admin_client
    # Start with a clean config (no context section at all)
    temp_cfg.write_text("memory:\n  short_term_rounds: 20\n", encoding="utf-8")

    resp = client.put(
        "/context-config",
        json={"max_turns": 25},
        headers={"Authorization": f"Bearer {VALID_TOKEN}"},
    )
    assert resp.status_code == 200

    saved = yaml.safe_load(temp_cfg.read_text(encoding="utf-8"))
    assert "max_turns" not in saved.get("context", {}), (
        "PUT must not write context.max_turns"
    )


# ---------------------------------------------------------------------------
# 7. Admin GET returns value from memory.short_term_rounds
# ---------------------------------------------------------------------------

def test_admin_get_reads_memory_short_term_rounds(monkeypatch):
    """GET /context-config returns memory.short_term_rounds when set."""
    import admin.routers.settings_misc as sm
    monkeypatch.setattr(
        sm, "get_config",
        lambda: {"memory": {"short_term_rounds": 12}},
    )
    monkeypatch.setattr("admin.auth.get_admin_secret", lambda: VALID_TOKEN)

    from admin.routers.settings_misc import router as sm_router
    app = FastAPI()
    app.include_router(sm_router)
    client = TestClient(app)
    resp = client.get("/context-config", headers={"Authorization": f"Bearer {VALID_TOKEN}"})
    assert resp.status_code == 200
    assert resp.json()["max_turns"] == 12


# ---------------------------------------------------------------------------
# 8. Admin GET ignores leftover context.max_turns
# ---------------------------------------------------------------------------

def test_admin_get_ignores_legacy_context_max_turns(monkeypatch):
    """GET /context-config uses the default 20 when memory.short_term_rounds is absent."""
    import admin.routers.settings_misc as sm
    monkeypatch.setattr(
        sm, "get_config",
        lambda: {"context": {"max_turns": 7}},
    )
    monkeypatch.setattr("admin.auth.get_admin_secret", lambda: VALID_TOKEN)

    from admin.routers.settings_misc import router as sm_router
    app = FastAPI()
    app.include_router(sm_router)
    client = TestClient(app)
    resp = client.get("/context-config", headers={"Authorization": f"Bearer {VALID_TOKEN}"})
    assert resp.status_code == 200
    assert resp.json()["max_turns"] == 20


# ---------------------------------------------------------------------------
# 9. Docs: known-issues.md no longer marks this as unfixed
# ---------------------------------------------------------------------------

def test_known_issues_not_open():
    """known-issues.md must not describe context.max_turns as now-safe-to-fix/open."""
    issues_path = PROJECT_ROOT / "docs" / "known-issues.md"
    text = issues_path.read_text(encoding="utf-8")

    # The old section was marked 'now-safe-to-fix'; after the fix it should be 'fixed'
    # Find the P1 block and assert it says fixed
    assert "now-safe-to-fix" not in text or _p1_block_is_fixed(text), (
        "known-issues.md still marks the context.max_turns issue as 'now-safe-to-fix'; "
        "update it to reflect the R7-A fix."
    )


def _p1_block_is_fixed(text: str) -> bool:
    """Return True if the P1 context.max_turns block is marked as fixed."""
    idx = text.find("context.max_turns 不影响真实 prompt 预算")
    if idx == -1:
        return True  # block removed entirely — also acceptable
    block = text[idx: idx + 300]
    return "fixed" in block
