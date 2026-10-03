"""工单 S5b：隐性状态证据化（schema v2）回归测试。"""
from __future__ import annotations

import math

import pytest

from core.memory import user_hidden_state as hs
from core.memory.user_hidden_state import (
    EVIDENCE_MAX,
    default_hidden_state,
    from_dict,
    scalar_view,
    to_dict,
    to_dream_snapshot,
)
from core.memory.user_hidden_state_integrator import RealityEventType, integrate_event
from core.write_envelope import stamp_user_chat

NOW = "2026-10-03T00:00:00Z"


def _v1_dict():
    return {
        "schema_version": 1,
        "last_decay_tick": "2026-09-01T00:00:00Z",
        "sensitivity": {
            "baseline": {"value": 50.0, "last_updated": "2026-09-01T00:00:00Z", "last_update_source": "time_decay"},
            "current": {"value": 60.0, "last_updated": "2026-09-02T00:00:00Z", "last_update_source": "reality_behavior"},
        },
        "touch_need": {
            "baseline": {"value": 50.0, "last_updated": None, "last_update_source": "init"},
            "deficit": {"value": 10.0, "last_updated": "2026-09-03T00:00:00Z", "last_update_source": "consolidation"},
        },
        "embodied_ease": {"value": 50.0, "last_updated": None, "last_update_source": "init"},
        "body_memory": {"entries": [], "max_entries": 32},
    }


def test_v1_to_v2_migration():
    st = from_dict(_v1_dict())
    assert st.schema_version == 2
    assert st.sensitivity.current.evidence == []
    assert st.sensitivity.current.last_confirmed_at == "2026-09-02T00:00:00Z"
    assert st.sensitivity.baseline.last_confirmed_at is None  # time_decay 来源
    assert st.touch_need.deficit.last_confirmed_at is None  # consolidation 来源
    assert st.sensitivity.current.value == 60.0


def test_roundtrip_keeps_evidence():
    st = default_hidden_state()
    hs.append_evidence(st, "sensitivity.current", at=NOW, source="reality_behavior",
                       event_type="body_topic", delta=2.0, ref="t1")
    st2 = from_dict(to_dict(st))
    assert st2.sensitivity.current.evidence[0]["ref"] == "t1"
    assert st2.sensitivity.current.last_confirmed_at == NOW


def test_decay_does_not_overwrite_source_or_confirmed():
    st = default_hidden_state()
    integrate_event(RealityEventType.BODY_TOPIC, st, stamp_user_chat(), "2026-10-01T00:00:00Z", ref="turn-1")
    st.last_decay_tick = "2026-10-01T00:00:00Z"
    hs.apply_time_decay(st, NOW)
    cur = st.sensitivity.current
    assert cur.last_update_source == hs.UpdateSource.REALITY_BEHAVIOR
    assert cur.last_confirmed_at == "2026-10-01T00:00:00Z"
    assert cur.last_decay_at == NOW
    assert cur.evidence[-1]["ref"] == "turn-1"


def test_integrate_event_records_evidence_with_ref():
    st = default_hidden_state()
    _, res = integrate_event(RealityEventType.BODY_TOPIC, st, stamp_user_chat(), NOW, ref="turn-9")
    assert res.accepted
    ev = st.sensitivity.current.evidence
    assert len(ev) == 1
    assert ev[0]["ref"] == "turn-9" and ev[0]["event_type"] == "body_topic" and ev[0]["delta"] > 0
    assert set(ev[0]) == {"at", "source", "event_type", "delta", "ref"}


def test_evidence_ring_buffer_limit():
    st = default_hidden_state()
    for i in range(EVIDENCE_MAX + 5):
        hs.append_evidence(st, "touch_need.deficit", at=NOW, source="reality_behavior",
                           event_type="x", delta=1.0, ref=f"r{i}")
    ev = st.touch_need.deficit.evidence
    assert len(ev) == EVIDENCE_MAX
    assert ev[0]["ref"] == "r5" and ev[-1]["ref"] == f"r{EVIDENCE_MAX + 4}"


def test_confidence_boundaries():
    st = default_hidden_state()
    assert scalar_view(st, "sensitivity.current", NOW)["confidence"] == 0.0
    for i in range(5):
        hs.append_evidence(st, "sensitivity.current", at=NOW, source="reality_behavior",
                           event_type="x", delta=1.0, ref=f"r{i}")
    assert scalar_view(st, "sensitivity.current", NOW)["confidence"] == pytest.approx(1.0)
    later = "2026-10-10T00:00:00Z"  # 7 天后
    assert scalar_view(st, "sensitivity.current", later)["confidence"] == pytest.approx(math.exp(-1), abs=1e-3)
    st2 = default_hidden_state()
    hs.append_evidence(st2, "sensitivity.current", at=NOW, source="reality_behavior",
                       event_type="x", delta=1.0, ref="a")
    assert scalar_view(st2, "sensitivity.current", NOW)["confidence"] == pytest.approx(0.2)


def test_scalar_view_refs_split_by_direction():
    st = default_hidden_state()
    st.touch_need.deficit.value = 30.0  # 偏离 0 为正
    hs.append_evidence(st, "touch_need.deficit", at=NOW, source="reality_behavior", event_type="a", delta=5.0, ref="up")
    hs.append_evidence(st, "touch_need.deficit", at=NOW, source="reality_behavior", event_type="b", delta=-5.0, ref="down")
    v = scalar_view(st, "touch_need.deficit", NOW)
    assert v["evidence_refs"] == ["up"] and v["counterevidence_refs"] == ["down"]
    assert v["update_source"] == "reality_behavior"


def _set_gating(monkeypatch, on, min_conf=0.3):
    monkeypatch.setattr(hs, "confidence_gating_settings", lambda: (on, min_conf))


def test_dream_snapshot_unknown_when_gated(monkeypatch):
    st = default_hidden_state()
    st.sensitivity.current.value = 90.0
    _set_gating(monkeypatch, False)
    assert to_dream_snapshot(st, NOW)["sensitivity"] == "high"
    _set_gating(monkeypatch, True)
    snap = to_dream_snapshot(st, NOW)
    assert snap["sensitivity"] == "unknown" and snap["touch_appetite"] == "unknown"
    from core.dream.dream_prompt import _format_hidden_state_snapshot
    text = _format_hidden_state_snapshot(snap)
    assert "sensitivity:" not in text and "touch_appetite:" not in text
    assert "embodied_ease:" in text


def _overflow(monkeypatch, state):
    from core.memory import user_hidden_state_store
    from core.scheduler import overflow_bucket
    monkeypatch.setattr(user_hidden_state_store, "load_hidden_state", lambda uid, *, char_id: state)
    return overflow_bucket.compute_signals("u", char_id="c")


def _hot_state(with_evidence: bool):
    st = default_hidden_state()
    st.touch_need.deficit.value = 100.0
    if with_evidence:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        for i in range(5):
            hs.append_evidence(st, "touch_need.deficit", at=now, source="reality_behavior",
                               event_type="x", delta=1.0, ref=f"r{i}")
    return st


def test_overflow_gating_off_equals_raw_and_records_both(monkeypatch):
    _set_gating(monkeypatch, False)
    sig = _overflow(monkeypatch, _hot_state(False))
    assert sig.hidden_need_score == pytest.approx(1.0)
    assert sig.hidden_need_raw == pytest.approx(1.0)
    assert sig.hidden_need_gated == 0.0
    assert sig.hidden_need_confidence["touch_need.deficit"] == 0.0


def test_overflow_gating_on_zeroes_low_confidence(monkeypatch):
    _set_gating(monkeypatch, True)
    assert _overflow(monkeypatch, _hot_state(False)).hidden_need_score == 0.0
    sig = _overflow(monkeypatch, _hot_state(True))
    assert sig.hidden_need_score == pytest.approx(1.0, abs=0.05)


def test_letter_reason_gating(monkeypatch):
    from core.memory import user_hidden_state_store
    from core.scheduler.triggers.letter_writer import _hidden_state_reason
    st = default_hidden_state()
    st.touch_need.deficit.value = 90.0  # ratio 1.8
    monkeypatch.setattr(user_hidden_state_store, "load_hidden_state", lambda uid, *, char_id: st)
    _set_gating(monkeypatch, False)
    assert _hidden_state_reason("u", char_id="c")
    _set_gating(monkeypatch, True)
    assert _hidden_state_reason("u", char_id="c") is None
