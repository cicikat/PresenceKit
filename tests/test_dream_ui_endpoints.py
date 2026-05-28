"""
tests/test_dream_ui_endpoints.py — Dream UI read/settings endpoint contract tests

Covers:
  ① GET /dream/state 纯只读：调用前后无文件写入、mood_state 未变、scheduler 未变
  ② GET /dream/state 无活动梦 → status=REALITY_CHAT，不报错
  ③ GET /dream/state DREAM_ACTIVE → 返回 body{heat,sensitivity,tension} + yexuan_tension
  ④ GET /dream/settings 返回全字段（含所有 _DEFAULTS）
  ⑤ PATCH /dream/settings 写入并回读一致
  ⑥ PATCH 非法枚举值被拒（422）、不落盘
  ⑦ ★ footgun：DREAM_ACTIVE 期间 PATCH world_layer=abo → dream_state.frozen_world 仍旧
       正控：退梦后 enter_dream → frozen_world 变 abo（证明 PATCH 确实写了、只是不回溯）

★ 每个"X不在Y"断言均配正样本对照（反假绿铁律）。
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

_UID = "ui_ep_test"

_SNAPSHOT_BASE = {
    "created_at": 0.0,
    "user_id": _UID,
    "yexuan_awareness": "lucid_shared",
    "boundary": "dream_only",
    "entry_reason": "",
    "memory_access": "relationship_summary",
    "relationship_state": {},
    "recent_reality_context": "",
    "episodic_summary": "",
    "mid_term_context": "",
    "profile_impression": "",
}


# ═══════════════════════════════════════════════════════════════════════════════
# ① GET /dream/state 纯只读
# ═══════════════════════════════════════════════════════════════════════════════

def test_state_get_is_readonly_no_file_writes(sandbox):
    """
    GET /dream/state never writes any file.
    Positive control: writing dream_settings.json DOES create a file.
    """
    from admin.routers.dream import dream_state_get

    pre = set(sandbox._base.rglob("*")) if sandbox._base.exists() else set()

    with patch("admin.routers.dream._owner_uid", return_value=_UID):
        result = asyncio.run(dream_state_get())

    post = set(sandbox._base.rglob("*")) if sandbox._base.exists() else set()
    new_files = {p for p in (post - pre) if p.is_file()}
    assert not new_files, f"GET /dream/state wrote files: {new_files}"

    # Positive control: a real write DOES show up
    from core.dream.dream_settings import save as _save
    _save(_UID, {"enable_dream_lorebook": True})
    post2 = set(sandbox._base.rglob("*"))
    new_files2 = {p for p in (post2 - post) if p.is_file()}
    assert new_files2, "Positive control failed: dream_settings.save() should create a file"


def test_state_get_mood_state_unchanged(sandbox):
    """
    GET /dream/state does not touch mood_state.json.
    Positive control: mood_state.update() DOES create the file.
    """
    from admin.routers.dream import dream_state_get

    mood_path = sandbox.mood_state()
    assert not mood_path.exists(), "Precondition: mood_state.json should not exist"

    with patch("admin.routers.dream._owner_uid", return_value=_UID):
        asyncio.run(dream_state_get())

    assert not mood_path.exists(), "GET /dream/state must not touch mood_state.json"

    # Positive control
    from core.memory.mood_state import update as _mood_update
    _mood_update("calm", 0.5, source="positive_control")
    assert mood_path.exists(), "Positive control: mood_state.update() should create the file"


def test_state_get_scheduler_state_unchanged(sandbox):
    """
    GET /dream/state does not touch scheduler_state.json.
    """
    from admin.routers.dream import dream_state_get

    sched_path = sandbox.scheduler_state()
    assert not sched_path.exists()

    with patch("admin.routers.dream._owner_uid", return_value=_UID):
        asyncio.run(dream_state_get())

    assert not sched_path.exists(), "GET /dream/state must not touch scheduler_state.json"


# ═══════════════════════════════════════════════════════════════════════════════
# ② GET /dream/state 无活动梦 → safe defaults
# ═══════════════════════════════════════════════════════════════════════════════

def test_state_get_no_dream_returns_reality_chat(sandbox):
    """No dream_state.json → status=REALITY_CHAT, body zeros, no error."""
    from admin.routers.dream import dream_state_get

    with patch("admin.routers.dream._owner_uid", return_value=_UID):
        result = asyncio.run(dream_state_get())

    assert result["status"] == "REALITY_CHAT"
    assert result["dream_id"] is None
    assert result["frozen_world"] is None
    assert result["lucid_mode"] is None
    assert result["yexuan_tension"] == 0.0
    assert result["body"] == {"heat": 0.0, "sensitivity": 0.0, "tension": 0.0}


# ═══════════════════════════════════════════════════════════════════════════════
# ③ GET /dream/state DREAM_ACTIVE → correct body + yexuan_tension
# ═══════════════════════════════════════════════════════════════════════════════

def test_state_get_dream_active_returns_projected_body(sandbox):
    """DREAM_ACTIVE state → projected body{heat,sensitivity,tension} + yexuan_tension."""
    from core.dream.dream_state import write_state, DreamStatus
    from admin.routers.dream import dream_state_get

    uid = _UID + "_active"
    write_state(uid, {
        "user_id": uid,
        "status": DreamStatus.DREAM_ACTIVE.value,
        "dream_id": f"dream_{uid}_proj",
        "frozen_world": "vampire",
        "lucid_mode": "non_lucid",
        "emotional_tension": 0.42,
        "body_state": {
            "heat": 30.0, "sensitivity": 25.0, "tension": 15.0,
            "heat_cap": 80.0, "sensitivity_cap": 80.0, "tension_cap": 90.0,
        },
    })

    with patch("admin.routers.dream._owner_uid", return_value=uid):
        result = asyncio.run(dream_state_get())

    assert result["status"] == "DREAM_ACTIVE"
    assert result["dream_id"] == f"dream_{uid}_proj"
    assert result["frozen_world"] == "vampire"
    assert result["lucid_mode"] == "non_lucid"
    assert result["yexuan_tension"] == pytest.approx(0.42)
    # body projects only heat/sensitivity/tension, not caps
    assert set(result["body"].keys()) == {"heat", "sensitivity", "tension"}
    assert result["body"]["heat"] == pytest.approx(30.0)
    assert result["body"]["sensitivity"] == pytest.approx(25.0)
    assert result["body"]["tension"] == pytest.approx(15.0)

    # Positive control: caps are NOT in the projected body (UI gets just the values)
    assert "heat_cap" not in result["body"]


def test_state_get_body_zero_when_no_body_state(sandbox):
    """DREAM_ACTIVE without body_state → body defaults to zeros, no error."""
    from core.dream.dream_state import write_state, DreamStatus
    from admin.routers.dream import dream_state_get

    uid = _UID + "_nobody"
    write_state(uid, {
        "user_id": uid,
        "status": DreamStatus.DREAM_ACTIVE.value,
        "dream_id": f"dream_{uid}_nb",
        "frozen_world": "reality_derived",
        "lucid_mode": "lucid_shared",
    })

    with patch("admin.routers.dream._owner_uid", return_value=uid):
        result = asyncio.run(dream_state_get())

    assert result["body"] == {"heat": 0.0, "sensitivity": 0.0, "tension": 0.0}
    assert result["yexuan_tension"] == 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# ④ GET /dream/settings 返回全字段
# ═══════════════════════════════════════════════════════════════════════════════

def test_settings_get_returns_all_defaults(sandbox):
    """GET /dream/settings returns all fields from _DEFAULTS."""
    from admin.routers.dream import dream_settings_get
    from core.dream.dream_settings import _DEFAULTS

    with patch("admin.routers.dream._owner_uid", return_value=_UID):
        result = asyncio.run(dream_settings_get())

    for key in _DEFAULTS:
        assert key in result, f"Missing default field in GET /dream/settings: {key!r}"

    # Positive control: writing a custom value is reflected
    from core.dream.dream_settings import save as _save
    _save(_UID, {"memory_access": "card_only"})
    with patch("admin.routers.dream._owner_uid", return_value=_UID):
        result2 = asyncio.run(dream_settings_get())
    assert result2["memory_access"] == "card_only"


# ═══════════════════════════════════════════════════════════════════════════════
# ⑤ PATCH /dream/settings 写入并回读一致
# ═══════════════════════════════════════════════════════════════════════════════

def test_settings_patch_write_and_reread_consistent(sandbox):
    """PATCH settings → re-read via GET returns same values."""
    from admin.routers.dream import dream_settings_patch, dream_settings_get

    uid = _UID + "_patch"

    with patch("admin.routers.dream._owner_uid", return_value=uid):
        resp = asyncio.run(dream_settings_patch({
            "memory_access": "card_only",
            "boundary_level": "numbers_visible",
            "world_layer": "abo",
            "lucid_mode": "non_lucid",
            "enable_dream_lorebook": False,
        }))
        assert resp["ok"]

        reread = asyncio.run(dream_settings_get())

    assert reread["memory_access"] == "card_only"
    assert reread["boundary_level"] == "numbers_visible"
    assert reread["world_layer"] == "abo"
    assert reread["lucid_mode"] == "non_lucid"
    assert reread["enable_dream_lorebook"] is False


def test_settings_patch_partial_update_only_changes_given_fields(sandbox):
    """PATCH with a subset of fields only changes those fields."""
    from admin.routers.dream import dream_settings_patch, dream_settings_get

    uid = _UID + "_partial"

    # Set known baseline
    from core.dream.dream_settings import save as _save
    _save(uid, {"world_layer": "cat", "memory_access": "full_snapshot"})

    with patch("admin.routers.dream._owner_uid", return_value=uid):
        asyncio.run(dream_settings_patch({"world_layer": "abo"}))
        result = asyncio.run(dream_settings_get())

    assert result["world_layer"] == "abo"
    assert result["memory_access"] == "full_snapshot", (
        "PATCH should not touch fields not in the request"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# ⑥ PATCH 非法枚举值被拒、不落盘
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("field,bad_val", [
    ("world_layer", "narnia"),
    ("memory_access", "everything"),
    ("boundary_level", "total_access"),
    ("lucid_mode", "super_lucid"),
])
def test_settings_patch_invalid_enum_rejected(sandbox, field, bad_val):
    """PATCH with invalid enum value → 422, settings not written."""
    from fastapi import HTTPException
    from admin.routers.dream import dream_settings_patch, dream_settings_get

    uid = _UID + "_inv"

    with patch("admin.routers.dream._owner_uid", return_value=uid):
        before = asyncio.run(dream_settings_get())

        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(dream_settings_patch({field: bad_val}))

        assert exc_info.value.status_code == 422, (
            f"Expected 422 for {field}={bad_val!r}, got {exc_info.value.status_code}"
        )

        after = asyncio.run(dream_settings_get())

    assert after[field] == before[field], (
        f"PATCH with invalid {field}={bad_val!r} should not persist; "
        f"before={before[field]!r} after={after[field]!r}"
    )


def test_settings_patch_invalid_enable_lorebook_rejected(sandbox):
    """PATCH enable_dream_lorebook with non-bool → 422, not written."""
    from fastapi import HTTPException
    from admin.routers.dream import dream_settings_patch, dream_settings_get

    uid = _UID + "_inv_bool"

    with patch("admin.routers.dream._owner_uid", return_value=uid):
        before = asyncio.run(dream_settings_get())

        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(dream_settings_patch({"enable_dream_lorebook": "yes"}))

        assert exc_info.value.status_code == 422
        after = asyncio.run(dream_settings_get())

    assert after["enable_dream_lorebook"] == before["enable_dream_lorebook"]


def test_settings_patch_empty_body_rejected(sandbox):
    """PATCH with no valid keys → 422."""
    from fastapi import HTTPException
    from admin.routers.dream import dream_settings_patch

    with patch("admin.routers.dream._owner_uid", return_value=_UID):
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(dream_settings_patch({"unknown_key": "foo"}))

    assert exc_info.value.status_code == 422


# ═══════════════════════════════════════════════════════════════════════════════
# ⑦ ★ Footgun: DREAM_ACTIVE 期间 PATCH world_layer 不回溯冻结世界
# ═══════════════════════════════════════════════════════════════════════════════

def test_footgun_patch_world_layer_during_dream_not_backfilled(sandbox):
    """
    ★ Core footgun: PATCH world_layer during DREAM_ACTIVE → dream_state.frozen_world UNCHANGED.
    Positive control: exit + enter_dream → new frozen_world = abo (PATCH was written, just not backfilled).

    Tests invariant E2: PATCH /dream/settings never modifies a running dream's frozen state.
    """
    uid = _UID + "_footgun"

    from core.dream.dream_state import write_state, read_state, DreamStatus
    from core.dream.dream_settings import save as _save_settings, load as _load_settings
    from admin.routers.dream import dream_settings_patch

    # Setup: settings with reality_derived, active dream frozen on reality_derived
    _save_settings(uid, {"world_layer": "reality_derived"})
    write_state(uid, {
        "user_id": uid,
        "status": DreamStatus.DREAM_ACTIVE.value,
        "dream_id": f"dream_{uid}_fg",
        "frozen_world": "reality_derived",
        "lucid_mode": "lucid_shared",
        "context_snapshot": dict(_SNAPSHOT_BASE, user_id=uid),
    })

    # ── PATCH world_layer to abo while dream is active ──────────────────────
    with patch("admin.routers.dream._owner_uid", return_value=uid):
        resp = asyncio.run(dream_settings_patch({"world_layer": "abo"}))
    assert resp["ok"], "PATCH should succeed"

    # Assert: dream_state.frozen_world is still reality_derived (not backfilled)
    state_mid = read_state(uid)
    assert state_mid["frozen_world"] == "reality_derived", (
        f"E2 violated: PATCH backfilled frozen_world to {state_mid['frozen_world']!r}; "
        "must remain 'reality_derived' for the current dream"
    )

    # Assert: settings.world_layer was actually written (PATCH was not a no-op)
    settings_mid = _load_settings(uid)
    assert settings_mid["world_layer"] == "abo", (
        f"PATCH should write settings.world_layer='abo', got {settings_mid['world_layer']!r}"
    )

    # ── Positive control: exit then enter_dream ─────────────────────────────
    # Simulate hard exit (write REALITY_AFTERGLOW — frozen_world is cleared by clear_local_state)
    write_state(uid, {
        "user_id": uid,
        "status": DreamStatus.REALITY_AFTERGLOW.value,
        "last_dream_id": f"dream_{uid}_fg",
        "last_exit_type": "hard_exit",
    })

    # enter_dream reads settings and freezes world_layer → frozen_world
    with patch(
        "core.dream.dream_context.build_snapshot",
        AsyncMock(return_value=dict(_SNAPSHOT_BASE, user_id=uid)),
    ):
        from core.dream.dream_pipeline import enter_dream
        result = asyncio.run(enter_dream(uid, entry_reason="footgun_positive_ctrl"))

    assert result["ok"], f"enter_dream should succeed, got: {result}"

    state_new = read_state(uid)
    assert state_new["frozen_world"] == "abo", (
        f"After re-enter, frozen_world should be 'abo' (PATCH took effect for next dream), "
        f"got {state_new['frozen_world']!r}"
    )
    assert state_new["status"] == DreamStatus.DREAM_ACTIVE.value
