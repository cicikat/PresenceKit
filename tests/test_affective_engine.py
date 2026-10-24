"""Engineering acceptance: causal state, reappraisal, independent expression."""
import json
import math
import time

import pytest

from core import affect
from core.memory import mood_state as ms


def proposal(**values):
    return {**dict.fromkeys(affect.EMOTIONS, 0.0), "confidence": .9, "relevance": 1.0,
            "strategy": "express", "goal_impact": "conflict", "agency": "uncertain",
            "correction_target": "none", **values}


@pytest.fixture
def clock(monkeypatch):
    now = [10000.0]
    monkeypatch.setattr(time, "time", lambda: now[0])
    return now


def commit(turn, at, raw=None, expression="", char="companion"):
    return ms.update(expression, char_id=char, source_turn_id=turn, source_at=at, appraisal=raw)


def test_smiling_does_not_clear_anger_and_concern(sandbox, clock):
    state = commit("t1", 9999, proposal(anger=.8, concern=.7, strategy="suppress"), "happy")
    assert state["current"] == "happy"
    assert state["affect"]["emotions"]["anger"] > .7
    assert state["affect"]["emotions"]["concern"] > .6
    assert state["affect"]["strategy"] == "suppress"
    assert ms.get_inner_current(char_id="companion") == "angry"
    assert ms.get_current(char_id="companion") == "happy"
    commit("t2", 10000, expression="gentle")
    assert ms.load(char_id="companion")["affect"]["emotions"]["anger"] > .7


def test_reappraisal_replaces_contribution_keeps_original_and_evidence(sandbox, clock):
    commit("t1", 9990, proposal(anger=.9))
    state = commit("t2", 9999, proposal(anger=.1, concern=.6, strategy="reappraise", correction_target="t1", goal_impact="repair"))
    assert len(state["affect_events"]) == 1
    event = state["affect_events"][0]
    assert event["source_turn_id"] == "t1" and event["correction_turn_id"] == "t2"
    assert event["revisions"][0]["appraisal"]["emotions"]["anger"] == .9
    assert state["affect"]["emotions"]["anger"] < .1
    assert event["occurred_at"] == 9990


def test_decay_has_separate_time_scales_and_reads_never_write(sandbox, clock):
    initial = commit("t1", 10000, proposal(anger=.8, surprise=.8, disappointment=.8))
    path = sandbox.mood_state(char_id="companion")
    original = path.read_bytes()
    clock[0] += 2700
    state = ms.load(char_id="companion")
    v = state["affect"]["emotions"]
    assert v["surprise"] < v["anger"] < v["disappointment"]
    assert v["anger"] == pytest.approx(initial["affect"]["emotions"]["anger"] / 2, abs=.001)
    assert state["affect"]["mood"]["valence"] < 0
    assert not state["has_signal"]
    assert path.read_bytes() == original
    clock[0] += affect.MAX_AGE
    assert not ms.load(char_id="companion")["affect"]["has_signal"]
    assert path.read_bytes() == original


def test_duplicate_late_and_equal_time_independent_turns(sandbox, clock):
    commit("new", 9999, proposal(anger=.8))
    path = sandbox.mood_state(char_id="companion")
    original = path.read_bytes()
    commit("new", 10000, proposal(joy=.9))
    commit("late", 9998, proposal(joy=.9))
    commit("future", 10001, proposal(joy=.9))
    assert path.read_bytes() == original
    assert commit("same_time", 9999, proposal(concern=.5))["version"] == 2


@pytest.mark.parametrize("bad", [math.nan, math.inf, -.1, 1.1, True, "0.5", None])
def test_invalid_candidates_cannot_change_inner_state(sandbox, clock, bad):
    commit("t1", 9990, proposal(anger=.8))
    state = commit("t2", 9999, proposal(anger=bad), "happy")
    assert state["appraisal_status"] == "rejected"
    assert len(state["affect_events"]) == 1
    assert state["current"] == "happy"


@pytest.mark.parametrize("values", [{"confidence": .2}, {"correction_target": "another-character"},
                                    {"strategy": "attack"}, {"agency": "user-is-evil"},
                                    {"correction_target": "t1", "confidence": .6}])
def test_unknown_or_uncertain_interpretation_is_rejected(sandbox, clock, values):
    commit("t1", 9990, proposal(anger=.8))
    state = commit("t2", 9999, proposal(**values))
    assert state["appraisal_status"] == "rejected"
    assert len(state["affect_events"]) == 1


def test_char_isolation_reopen_and_feature_off(sandbox, clock, monkeypatch):
    commit("a", 9999, proposal(anger=.8), "happy")
    other = commit("b", 9999, proposal(joy=.8), "gentle", char="other")
    assert other["affect"]["dominant"] == "joy"
    assert ms.load(char_id="companion")["affect"]["dominant"] == "anger"
    monkeypatch.setattr(ms, "enabled", lambda: False)
    assert ms.get_inner_current(char_id="companion") == "happy"
    state = commit("c", 10000, proposal(joy=1.0), "gentle")
    assert len(state["affect_events"]) == 1
    assert ms.get_inner_current(char_id="companion") == "gentle"


def test_v2_expression_migrates_without_inventing_inner_feeling(sandbox, clock):
    ms.save({"schema_version": 2, "current": "happy", "source_turn_id": "old", "source_at": 9999,
             "expires_at": 11000, "has_signal": True, "intensity": .6}, char_id="companion")
    state = ms.load(char_id="companion")
    assert state["schema_version"] == 3 and state["current"] == "happy"
    assert not state["affect"]["has_signal"]
    assert not state["affect_events"]


def test_retention_and_receipts_are_bounded(sandbox, clock):
    for i in range(210):
        clock[0] += 1
        commit(str(i), clock[0], proposal(concern=.1))
    state = ms.load(char_id="companion")
    assert len(state["affect_events"]) == affect.MAX_EVENTS
    assert len(state["receipts"]) == affect.MAX_RECEIPTS
    assert not any(k.startswith("user_") for k in state)


def test_corruption_and_write_failure_are_explicit(sandbox, clock, monkeypatch):
    path = sandbox.mood_state(char_id="companion")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{bad", encoding="utf-8")
    state = ms.load(char_id="companion")
    assert state["appraisal_status"] == "invalid_store" and not state["affect"]["has_signal"]
    monkeypatch.setattr(ms, "safe_write_json", lambda *a, **kw: False)
    with pytest.raises(OSError):
        commit("t1", 9999, proposal(anger=.8))
