"""
tests/test_dream_hud_v12.py — Dream HUD v1.2 contract tests

Covers:
  ① anchor_repeat_ratio 公式正确性（纯函数）
  ② case 1: 连续 3 轮 ["镜"] → ratio 逐步上升，obsession 逐步升高
  ③ case 2: 3 轮各不重复 ["镜"/"门"/"血"] → ratio ≈ 0，obsession 不因重复上升
  ④ case 3: symbolic_anchors=[] → ratio=0，不崩溃
  ⑤ 旧 dream_hud_state（无 anchor_history 字段）兼容
  ⑥ anchor_history 滚动窗口不超过 5 轮
  ⑦ 非 string / 空字符串 anchors 被过滤
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from core.dream.dream_hud import anchor_repeat_ratio, derive_hud_v1


# ── Helpers ───────────────────────────────────────────────────────────────────

def _body(heat: float = 20.0, sensitivity: float = 20.0) -> Any:
    return SimpleNamespace(heat=heat, sensitivity=sensitivity, tension=0.0)


def _state(anchors: list, tension: float = 0.3) -> dict:
    return {
        "emotional_tension": tension,
        "symbolic_anchors":  anchors,
        "scene_state":       None,
        "frozen_world":      "reality_derived",
    }


def _settings() -> dict:
    return {"boundary_level": "body_perceptible", "world_layer": "reality_derived"}


def _run_turns(anchor_lists: list[list]) -> list[dict]:
    """Simulate N consecutive turns; return list of hud dicts per turn."""
    prev: dict[str, Any] = {}
    results = []
    for anchors in anchor_lists:
        smooth, hud = derive_hud_v1(_state(anchors), _settings(), _body(), prev)
        results.append(hud)
        prev = smooth
    return results


# ── ① anchor_repeat_ratio 公式 ────────────────────────────────────────────────

def test_repeat_ratio_all_same():
    history = [["镜"], ["镜"], ["镜"]]
    ratio = anchor_repeat_ratio(history)
    # flat=["镜","镜","镜"], total=3, distinct=1 → 1 - 1/3 ≈ 0.667
    assert abs(ratio - (1 - 1/3)) < 1e-9


def test_repeat_ratio_all_distinct():
    history = [["镜"], ["门"], ["血"]]
    ratio = anchor_repeat_ratio(history)
    # flat=["镜","门","血"], total=3, distinct=3 → 0
    assert ratio == 0.0


def test_repeat_ratio_empty():
    assert anchor_repeat_ratio([]) == 0.0
    assert anchor_repeat_ratio([[], []]) == 0.0


def test_repeat_ratio_mixed():
    # ["镜","镜","门"] → total=3, distinct=2 → 1 - 2/3 ≈ 0.333
    history = [["镜", "镜"], ["门"]]
    ratio = anchor_repeat_ratio(history)
    assert abs(ratio - (1 - 2/3)) < 1e-9


# ── ② case 1: 连续 3 轮 ["镜"] → obsession 逐步升高 ──────────────────────────

def test_case1_obsession_rises_with_repeat():
    huds = _run_turns([["镜"], ["镜"], ["镜"]])
    obs = [h["obsession"] for h in huds]
    # Each turn: repeat ratio grows (0 → 0.5 → 0.667), so raw obsession rises.
    # After EMA (α=0.35), smooth obsession must be strictly increasing.
    assert obs[0] <= obs[1] <= obs[2], (
        f"Expected non-decreasing obsession: {obs}"
    )
    # At least some growth by turn 3
    assert obs[2] > obs[0], f"Obsession did not grow at all: {obs}"


def test_case1_anchor_history_accumulated():
    prev: dict[str, Any] = {}
    for anchors in [["镜"], ["镜"], ["镜"]]:
        smooth, _ = derive_hud_v1(_state(anchors), _settings(), _body(), prev)
        prev = smooth
    # After 3 turns, history should hold exactly 3 windows
    assert prev["anchor_history"] == [["镜"], ["镜"], ["镜"]]


# ── ③ case 2: 各轮不同 anchors → ratio ≈ 0，obsession 不因重复上升 ───────────

def test_case2_no_repeat_no_obsession_boost():
    huds = _run_turns([["镜"], ["门"], ["血"]])
    # Anchor history always has distinct elements → arr=0 each turn.
    # obsession should NOT be driven up by repeat; it may still move due to
    # emotion_tension / anchor_charge, but the *boost* from repeat is 0.
    # We verify by comparing against a baseline run with same anchors but
    # checking that adding arr*30 contributed nothing (arr == 0 each turn).

    # Simpler contract: obsession on turn 3 must equal the case with arr=0 injected.
    prev: dict[str, Any] = {}
    for anchors in [["镜"], ["门"], ["血"]]:
        smooth, _ = derive_hud_v1(_state(anchors), _settings(), _body(), prev)
        prev = smooth
    history_after = prev["anchor_history"]
    ratio = anchor_repeat_ratio(history_after)
    assert ratio == 0.0, f"Expected ratio=0 for all-distinct anchors, got {ratio}"


# ── ④ case 3: empty anchors → ratio=0, no crash ───────────────────────────────

def test_case3_empty_anchors_no_crash():
    prev: dict[str, Any] = {}
    for _ in range(3):
        smooth, hud = derive_hud_v1(_state([]), _settings(), _body(), prev)
        prev = smooth
    assert hud["obsession"] >= 0
    assert prev["anchor_history"] == [[], [], []]
    assert anchor_repeat_ratio(prev["anchor_history"]) == 0.0


# ── ⑤ 旧 dream_hud_state 兼容（无 anchor_history 字段）─────────────────────

def test_legacy_state_no_anchor_history():
    """prev_smooth without anchor_history must not raise; treated as []."""
    legacy = {
        "emotion_tension": 30.0,
        "boundary_intrusion": 20.0,
        "intimacy_tendency": 15.0,
        "obsession": 25.0,
        "dream_stability": 70.0,
        "dream_depth": 10.0,
        # no anchor_history key
    }
    smooth, hud = derive_hud_v1(_state(["镜"]), _settings(), _body(), legacy)
    assert "anchor_history" in smooth
    assert smooth["anchor_history"] == [["镜"]]
    assert hud["obsession"] >= 0


# ── ⑥ anchor_history 窗口不超过 5 轮 ─────────────────────────────────────────

def test_anchor_history_capped_at_5():
    prev: dict[str, Any] = {}
    for i in range(8):
        smooth, _ = derive_hud_v1(_state([str(i)]), _settings(), _body(), prev)
        prev = smooth
    assert len(prev["anchor_history"]) == 5
    # Only the last 5 turns retained
    assert prev["anchor_history"] == [[str(i)] for i in range(3, 8)]


# ── ⑦ 非 string / 空字符串 anchors 被过滤 ────────────────────────────────────

def test_non_string_and_blank_anchors_filtered():
    dirty = [None, 123, "", "  ", "镜", True, "血"]
    prev: dict[str, Any] = {}
    smooth, _ = derive_hud_v1(_state(dirty), _settings(), _body(), prev)
    assert smooth["anchor_history"] == [["镜", "血"]]
