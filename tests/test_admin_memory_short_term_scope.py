"""
tests/test_admin_memory_short_term_scope.py — P1-0C: ShortTermMemory char_id scope

Covers:
1.  ShortTermMemory.load(user_id, char_id="character_b") reads character_b bucket, not yexuan.
2.  ShortTermMemory.clear(user_id, char_id="character_b") only clears character_b bucket.
3.  admin GET short-term with no char_id uses active character (active=character_b).
4.  admin DELETE short-term with no char_id only clears active character bucket.
5.  admin GET/DELETE with explicit char_id uses that char (not active).
6.  active_character missing/empty → admin GET returns 503, no load called.
7.  specified invalid char_id → admin GET/DELETE returns 422, no load/clear called.
8.  Regression: load_for_prompt / append char_id passthrough unchanged.
"""

import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import core.asset_registry as _reg_mod
from core.asset_registry import AssetRegistry


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def chars_tree(tmp_path):
    """Minimal characters/ tree with yexuan + character_b."""
    chars = tmp_path / "characters"
    chars.mkdir()
    (chars / "yexuan.json").write_text(
        json.dumps({"name": "Companion", "description": "test", "world_book": []}),
        encoding="utf-8",
    )
    (chars / "character_b.json").write_text(
        json.dumps({"name": "DemoUser", "description": "character_b test", "world_book": []}),
        encoding="utf-8",
    )
    jb = chars / "reality" / "jailbreaks"
    jb.mkdir(parents=True)
    (jb / "base.json").write_text(json.dumps({"entries": []}), encoding="utf-8")
    return tmp_path


@pytest.fixture
def registry(chars_tree, monkeypatch):
    monkeypatch.chdir(chars_tree)
    # This fixture deliberately moves cwd away from the repository root so the
    # asset registry scans the synthetic characters/ tree.  Short-term writes
    # are not under test for config loading here, so keep their retention
    # policy deterministic instead of depending on config.yaml or worker cache
    # state.  Patch the consumer alias because short_term imports get_config
    # directly from core.config_loader.
    monkeypatch.setattr(
        "core.memory.short_term.get_config",
        lambda: {
            "memory": {
                "short_term_rounds": 20,
                "short_term_disk_rounds": 20,
            }
        },
    )
    reg = AssetRegistry()
    monkeypatch.setattr(_reg_mod, "_registry", reg)
    return reg


def _seed_active(sandbox, char_id: str):
    """Write active_prompt_assets.json with the given char_id."""
    p = sandbox.active_prompt_assets()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps({"active_character": char_id, "enabled_lorebooks": [], "enabled_jailbreaks": []}),
        encoding="utf-8",
    )


# ── 1 & 2: ShortTermMemory class method char_id isolation ─────────────────────

def test_stm_load_reads_character_b_bucket_not_yexuan(sandbox):
    """ShortTermMemory.load with char_id=character_b reads character_b bucket only."""
    from core.memory.short_term import ShortTermMemory, append

    uid = "u_stm_load"
    SENTINEL = "草莓大福-character_b-load"

    # Write to character_b bucket, leave yexuan empty
    append(uid, "user", SENTINEL, char_id="character_b")

    stm = ShortTermMemory()
    character_b_history = stm.load(uid, char_id="character_b")
    yexuan_history = stm.load(uid, char_id="yexuan")

    assert any(SENTINEL in m.get("content", "") for m in character_b_history), (
        f"character_b bucket should contain sentinel; got {character_b_history}"
    )
    assert not any(SENTINEL in m.get("content", "") for m in yexuan_history), (
        f"yexuan bucket must not contain character_b sentinel; got {yexuan_history}"
    )


def test_stm_clear_clears_character_b_bucket_not_yexuan(sandbox):
    """ShortTermMemory.clear with char_id=character_b only empties the character_b bucket."""
    from core.memory.short_term import ShortTermMemory, append

    uid = "u_stm_clear"
    SENTINEL_H = "草莓大福-character_b-clear"
    SENTINEL_Y = "草莓大福-yexuan-clear"

    append(uid, "user", SENTINEL_H, char_id="character_b")
    append(uid, "user", SENTINEL_Y, char_id="yexuan")

    stm = ShortTermMemory()
    stm.clear(uid, char_id="character_b")

    character_b_history = stm.load(uid, char_id="character_b")
    yexuan_history = stm.load(uid, char_id="yexuan")

    assert character_b_history == [], f"character_b bucket must be empty after clear; got {character_b_history}"
    assert any(SENTINEL_Y in m.get("content", "") for m in yexuan_history), (
        f"yexuan bucket must be untouched; got {yexuan_history}"
    )


# ── 3: admin GET uses active character when char_id omitted ───────────────────

def test_admin_get_uses_active_char_when_omitted(sandbox, registry, monkeypatch):
    """GET short-term with no char_id resolves to active_character (character_b)."""
    from core.memory import short_term as _st
    from admin.routers.memory import get_short_term

    _seed_active(sandbox, "character_b")

    uid = "u_admin_get_active"
    SENTINEL = "草莓大福-admin-get"
    _st.append(uid, "user", SENTINEL, char_id="character_b")

    result = asyncio.run(get_short_term(uid, char_id=None, auth="dummy"))

    assert result["char_id"] == "character_b", f"expected char_id=character_b, got {result['char_id']!r}"
    contents = [m.get("content", "") for m in result["history"]]
    assert any(SENTINEL in c for c in contents), (
        f"character_b sentinel must appear in history; got {contents}"
    )


# ── 4: admin DELETE uses active character when char_id omitted ────────────────

def test_admin_delete_only_clears_active_char(sandbox, registry, monkeypatch):
    """DELETE short-term with no char_id only clears active_character bucket."""
    from core.memory import short_term as _st
    from admin.routers.memory import clear_short_term

    _seed_active(sandbox, "character_b")

    uid = "u_admin_delete_active"
    SENTINEL_H = "草莓大福-delete-character_b"
    SENTINEL_Y = "草莓大福-delete-yexuan"

    _st.append(uid, "user", SENTINEL_H, char_id="character_b")
    _st.append(uid, "user", SENTINEL_Y, char_id="yexuan")

    result = asyncio.run(clear_short_term(uid, char_id=None, auth="dummy"))

    assert result["char_id"] == "character_b"
    assert _st.load(uid, char_id="character_b") == [], "character_b bucket must be empty"
    yexuan_history = _st.load(uid, char_id="yexuan")
    assert any(SENTINEL_Y in m.get("content", "") for m in yexuan_history), (
        "yexuan bucket must be untouched after delete of character_b"
    )


# ── 5: admin GET/DELETE with explicit char_id uses that char ──────────────────

def test_admin_get_explicit_char_id(sandbox, registry, monkeypatch):
    """GET short-term with explicit char_id=yexuan reads yexuan, not active (character_b)."""
    from core.memory import short_term as _st
    from admin.routers.memory import get_short_term

    _seed_active(sandbox, "character_b")

    uid = "u_admin_get_explicit"
    SENTINEL_Y = "草莓大福-explicit-yexuan"
    _st.append(uid, "user", SENTINEL_Y, char_id="yexuan")

    result = asyncio.run(get_short_term(uid, char_id="yexuan", auth="dummy"))

    assert result["char_id"] == "yexuan"
    contents = [m.get("content", "") for m in result["history"]]
    assert any(SENTINEL_Y in c for c in contents), (
        f"yexuan sentinel must appear; got {contents}"
    )


def test_admin_delete_explicit_char_id(sandbox, registry, monkeypatch):
    """DELETE short-term with explicit char_id=yexuan clears yexuan, not active (character_b)."""
    from core.memory import short_term as _st
    from admin.routers.memory import clear_short_term

    _seed_active(sandbox, "character_b")

    uid = "u_admin_delete_explicit"
    SENTINEL_H = "草莓大福-explicit-h"
    SENTINEL_Y = "草莓大福-explicit-y"

    _st.append(uid, "user", SENTINEL_H, char_id="character_b")
    _st.append(uid, "user", SENTINEL_Y, char_id="yexuan")

    result = asyncio.run(clear_short_term(uid, char_id="yexuan", auth="dummy"))

    assert result["char_id"] == "yexuan"
    assert _st.load(uid, char_id="yexuan") == [], "yexuan bucket must be empty"
    character_b_history = _st.load(uid, char_id="character_b")
    assert any(SENTINEL_H in m.get("content", "") for m in character_b_history), (
        "character_b bucket must be untouched"
    )


# ── 6: active_character missing/empty → 503, no readers called ────────────────

def test_admin_get_missing_active_returns_503(sandbox, registry, monkeypatch):
    """GET short-term with no char_id when active_character is empty → HTTP 503."""
    from fastapi import HTTPException
    from admin.routers.memory import get_short_term

    p = sandbox.active_prompt_assets()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps({"active_character": "", "enabled_lorebooks": [], "enabled_jailbreaks": []}),
        encoding="utf-8",
    )

    load_called = []

    import core.memory.short_term as _st
    orig_load = _st.load
    monkeypatch.setattr(_st, "load", lambda uid, **kw: load_called.append(kw) or [])

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(get_short_term("u_503", char_id=None, auth="dummy"))

    assert exc_info.value.status_code == 503
    assert not load_called, "short_term.load must not be called when active_character is invalid"


def test_admin_delete_missing_active_returns_503(sandbox, registry, monkeypatch):
    """DELETE short-term with no char_id when active_character is empty → HTTP 503."""
    from fastapi import HTTPException
    from admin.routers.memory import clear_short_term

    p = sandbox.active_prompt_assets()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps({"active_character": ""}),
        encoding="utf-8",
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(clear_short_term("u_503_del", char_id=None, auth="dummy"))

    assert exc_info.value.status_code == 503


# ── 7: invalid explicit char_id → 422, no readers called ─────────────────────

def test_admin_get_invalid_char_id_returns_422(sandbox, registry, monkeypatch):
    """GET short-term with unknown char_id → HTTP 422, short_term.load not called."""
    from fastapi import HTTPException
    from admin.routers.memory import get_short_term

    _seed_active(sandbox, "yexuan")

    load_called = []
    import core.memory.short_term as _st
    monkeypatch.setattr(_st, "load", lambda uid, **kw: load_called.append(kw) or [])

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(get_short_term("u_422", char_id="nonexistent_char", auth="dummy"))

    assert exc_info.value.status_code == 422
    assert not load_called, "short_term.load must not be called for invalid char_id"


def test_admin_delete_invalid_char_id_returns_422(sandbox, registry, monkeypatch):
    """DELETE short-term with unknown char_id → HTTP 422, short_term.clear not called."""
    from fastapi import HTTPException
    from admin.routers.memory import clear_short_term

    _seed_active(sandbox, "yexuan")

    clear_called = []
    import core.memory.short_term as _st
    monkeypatch.setattr(_st, "clear", lambda uid, **kw: clear_called.append(kw))

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(clear_short_term("u_422_del", char_id="bad_id", auth="dummy"))

    assert exc_info.value.status_code == 422
    assert not clear_called, "short_term.clear must not be called for invalid char_id"


# ── 8: Regression — load_for_prompt / append passthrough unchanged ────────────

def test_load_for_prompt_char_id_passthrough(sandbox):
    """load_for_prompt still accepts char_id kwarg and reads the correct bucket."""
    from core.memory.short_term import append, load_for_prompt

    uid = "u_reg_prompt"
    SENTINEL = "草莓大福-load_for_prompt"
    append(uid, "user", SENTINEL, char_id="character_b")

    result = load_for_prompt(uid, char_id="character_b")
    contents = [m.get("content", "") for m in result]
    assert any(SENTINEL in c for c in contents), (
        f"load_for_prompt(char_id=character_b) must read character_b bucket; got {contents}"
    )

    result_y = load_for_prompt(uid, char_id="yexuan")
    contents_y = [m.get("content", "") for m in result_y]
    assert not any(SENTINEL in c for c in contents_y), (
        "load_for_prompt(char_id=yexuan) must not see character_b content"
    )


def test_append_char_id_passthrough(sandbox):
    """append still writes to the correct char_id bucket."""
    from core.memory.short_term import append, load

    uid = "u_reg_append"
    SENTINEL = "草莓大福-append"
    append(uid, "assistant", SENTINEL, char_id="character_b")

    assert any(SENTINEL in m.get("content", "") for m in load(uid, char_id="character_b")), (
        "append(char_id=character_b) must write to character_b bucket"
    )
    assert not any(SENTINEL in m.get("content", "") for m in load(uid, char_id="yexuan")), (
        "yexuan bucket must remain clean"
    )
