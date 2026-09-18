"""
tests/test_dream_flow_entries.py — Dream flow entries (Brief 25 §2)

Covers:
  ① generate_flow_entries: scene_shift / tension_up / tension_down / anchor_new detection
  ② tension delta below threshold → no tension_* hit
  ③ append_status_shift: canned summaries per event
  ④ FIFO cap at 10, keeps newest entries
  ⑤ clear_flow_entries resets to []
  ⑥ GET /dream/state exposes flow_entries + char_tension
  ⑦ GET /dream/state with no active dream → flow_entries == []
"""

import asyncio
from unittest.mock import patch

import pytest

_UID = "flow_entries_test_user"


# ═══════════════════════════════════════════════════════════════════════════════
# ① generate_flow_entries: detection rules
# ═══════════════════════════════════════════════════════════════════════════════

def test_generate_flow_entries_scene_shift():
    from core.dream.dream_flow import generate_flow_entries

    prev = {"scene_state": "旧场景"}
    new = {"scene_state": "潮湿的地下室走廊"}
    hits = generate_flow_entries(prev, new)
    assert ("scene_shift", "场景转入：潮湿的地下室走廊") in hits


def test_generate_flow_entries_tension_up():
    from core.dream.dream_flow import generate_flow_entries

    prev = {"emotional_tension": 0.2}
    new = {"emotional_tension": 0.5}
    hits = generate_flow_entries(prev, new)
    assert ("tension_up", "他的情绪张力在上升") in hits


def test_generate_flow_entries_tension_down():
    from core.dream.dream_flow import generate_flow_entries

    prev = {"emotional_tension": 0.6}
    new = {"emotional_tension": 0.3}
    hits = generate_flow_entries(prev, new)
    assert ("tension_down", "他的情绪张力在回落") in hits


def test_generate_flow_entries_tension_delta_below_threshold_no_hit():
    """Δ<0.15 must NOT trigger tension_up/tension_down (positive control above)."""
    from core.dream.dream_flow import generate_flow_entries

    prev = {"emotional_tension": 0.40}
    new = {"emotional_tension": 0.48}
    hits = generate_flow_entries(prev, new)
    kinds = [k for k, _ in hits]
    assert "tension_up" not in kinds
    assert "tension_down" not in kinds


def test_generate_flow_entries_anchor_new():
    from core.dream.dream_flow import generate_flow_entries

    prev = {"symbolic_anchors": ["旧锚点"]}
    new = {"symbolic_anchors": ["旧锚点", "一枚生锈的钥匙"]}
    hits = generate_flow_entries(prev, new)
    assert ("anchor_new", "新的象征浮现：一枚生锈的钥匙") in hits


def test_generate_flow_entries_caps_at_two_per_round():
    from core.dream.dream_flow import generate_flow_entries

    prev = {"scene_state": "旧场景", "emotional_tension": 0.1, "symbolic_anchors": []}
    new = {
        "scene_state": "新场景",
        "emotional_tension": 0.9,
        "symbolic_anchors": ["锚1", "锚2", "锚3"],
    }
    hits = generate_flow_entries(prev, new)
    assert len(hits) == 2


# ═══════════════════════════════════════════════════════════════════════════════
# ③ append_status_shift: canned summaries
# ═══════════════════════════════════════════════════════════════════════════════

def test_append_status_shift_known_events():
    from core.dream.dream_flow import append_status_shift

    state = {}
    for event, expected in (
        ("enter", "梦境正在成形"),
        ("exit_requested", "醒来的边缘在靠近"),
        ("closing", "梦在慢慢消散"),
        ("retained", "他把你留了下来"),
    ):
        s = append_status_shift(state, event)
        assert s["flow_entries"][-1]["kind"] == "status_shift"
        assert s["flow_entries"][-1]["summary"] == expected


def test_append_status_shift_unknown_event_is_noop():
    from core.dream.dream_flow import append_status_shift

    state = {"flow_entries": []}
    s = append_status_shift(state, "not_a_real_event")
    assert s["flow_entries"] == []


# ═══════════════════════════════════════════════════════════════════════════════
# ④ FIFO cap at 10
# ═══════════════════════════════════════════════════════════════════════════════

def test_append_flow_entry_fifo_cap():
    from core.dream.dream_flow import append_flow_entry

    state: dict = {}
    for i in range(12):
        state = append_flow_entry(state, "scene_shift", f"场景{i}")

    entries = state["flow_entries"]
    assert len(entries) == 10
    # Newest 10 retained (场景2..场景11), oldest two (场景0, 场景1) dropped
    summaries = [e["summary"] for e in entries]
    assert summaries == [f"场景{i}" for i in range(2, 12)]


# ═══════════════════════════════════════════════════════════════════════════════
# ⑤ clear_flow_entries
# ═══════════════════════════════════════════════════════════════════════════════

def test_clear_flow_entries_resets():
    from core.dream.dream_flow import append_flow_entry, clear_flow_entries

    state = append_flow_entry({}, "scene_shift", "x")
    assert state["flow_entries"]
    cleared = clear_flow_entries(state)
    assert cleared["flow_entries"] == []


# ═══════════════════════════════════════════════════════════════════════════════
# ⑥ GET /dream/state exposes flow_entries + char_tension
# ═══════════════════════════════════════════════════════════════════════════════

def test_state_get_exposes_flow_entries_and_char_tension(sandbox):
    from core.dream.dream_state import write_state, DreamStatus
    from admin.routers.dream import dream_state_get

    uid = _UID + "_active"
    write_state(uid, {
        "user_id": uid,
        "status": DreamStatus.DREAM_ACTIVE.value,
        "dream_id": f"dream_{uid}_flow",
        "emotional_tension": 0.37,
        "flow_entries": [
            {"ts": "2026-01-01T00:00:00+00:00", "kind": "status_shift", "summary": "梦境正在成形"},
        ],
    })

    with patch("admin.routers.dream._owner_uid", return_value=uid):
        result = asyncio.run(dream_state_get())

    assert result["flow_entries"] == [
        {"ts": "2026-01-01T00:00:00+00:00", "kind": "status_shift", "summary": "梦境正在成形"},
    ]
    assert result["char_tension"] == pytest.approx(0.37)
    assert "yexuan_tension" not in result


# ═══════════════════════════════════════════════════════════════════════════════
# ⑦ No active dream → flow_entries == []
# ═══════════════════════════════════════════════════════════════════════════════

def test_state_get_no_dream_flow_entries_empty(sandbox):
    from admin.routers.dream import dream_state_get

    with patch("admin.routers.dream._owner_uid", return_value=_UID + "_none"):
        result = asyncio.run(dream_state_get())

    assert result["flow_entries"] == []
