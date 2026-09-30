"""Video-call work order F: call-scoped proactivity, per-source cooldown, per-call caps."""
from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from core import video_call, video_call_presence as presence


@pytest.fixture(autouse=True)
def _clean():
    presence.reset_for_tests()
    video_call._camera_sessions.clear()
    yield
    video_call._camera_sessions.clear()
    presence.reset_for_tests()


def _enable(monkeypatch, **overrides):
    cfg = {**presence.DEFAULTS, "enabled": True, **overrides}
    monkeypatch.setattr(presence, "settings", lambda config=None: cfg)
    return cfg


def _open_call(uid="owner", char_id="char"):
    now = time.monotonic()
    video_call._camera_sessions[(uid, char_id)] = {
        "seen_at": now, "opened_at": now, "token_label": "desktop", "description": ""}
    return video_call._camera_sessions[(uid, char_id)]


def _admission_ready(monkeypatch, trigger_state_name="QUIET", last_owner_turn_ago=None):
    from core.dream.dream_state import DreamGuardStatus
    from core.scheduler import state_machine

    monkeypatch.setattr("core.character_loader.is_proactive_disabled", lambda: False)
    monkeypatch.setattr("core.dream.dream_state.get_reality_guard_status", lambda _uid: DreamGuardStatus.ALLOW)
    monkeypatch.setattr(state_machine, "get_state", lambda _uid: getattr(state_machine.TriggerState, trigger_state_name))
    monkeypatch.setattr(state_machine, "snapshot", lambda _uid: {
        "last_owner_turn_ts": time.time() - (last_owner_turn_ago or 0)})
    monkeypatch.setattr("core.conversation_gate.conversation_lock", lambda _uid: SimpleNamespace(locked=lambda: False))
    monkeypatch.setattr("core.message_queue.active_sessions", lambda: set())
    monkeypatch.setattr("core.message_queue.queue_size", lambda _uid: 0)
    monkeypatch.setattr("core.coplay.session.is_active", lambda *_a, **_k: False)


def _state(sandbox, sources):
    from core.autonomy import store
    state = store.load("owner", "char")
    state["config"]["enabled"] = True
    state["config"]["min_interval_seconds"] = 900
    state["sources"] = sources
    return state


# ── defaults and validation ─────────────────────────────────────────────────

def test_feature_is_off_by_default_and_validates_ranges():
    assert presence.settings({})["enabled"] is False
    assert presence.settings({})["max_talks_per_call"] == 3
    assert presence.settings({})["min_gap_seconds"] == 180
    for bad in ({"max_talks_per_call": 0}, {"min_gap_seconds": 5}, {"silence_seconds": 500},
                {"enabled": "yes"}, {"max_talks_per_call": True}):
        with pytest.raises(ValueError):
            presence.validate(bad)
    assert presence.settings({"video_call_presence": {"max_talks_per_call": 0}}) == presence.DEFAULTS


# ── per-source cooldown (camera only) ───────────────────────────────────────

def test_camera_job_is_not_blocked_by_other_sources_evaluating(sandbox, monkeypatch):
    from core.autonomy import policy

    _admission_ready(monkeypatch)
    state = _state(sandbox, {"interval": {"last_evaluated_at": time.time() - 60}})
    # Other sources keep their global semantics.
    assert policy.admission("owner", "char", state) == "duplicate"
    assert policy.admission("owner", "char", state, allow_observed_activity=True) == "duplicate"
    # The camera source is judged by its own clock.
    assert policy.admission("owner", "char", state, allow_observed_activity=True,
                            allow_camera_silence=True) is None
    state["sources"]["video_call_camera"] = {"last_evaluated_at": time.time() - 60}
    assert policy.admission("owner", "char", state, allow_observed_activity=True,
                            allow_camera_silence=True) == "duplicate"


# ── call-scoped relaxation ──────────────────────────────────────────────────

def test_call_scope_shortens_the_quiet_window_but_global_rule_is_unchanged(sandbox, monkeypatch):
    from core.autonomy import policy

    _admission_ready(monkeypatch, "CHATTING", last_owner_turn_ago=60)
    state = _state(sandbox, {})
    kwargs = dict(allow_observed_activity=True, allow_camera_silence=True)
    assert policy.admission("owner", "char", state, **kwargs) == "blocked_user_active"  # 60 s < 120 s
    _open_call()
    _enable(monkeypatch, silence_seconds=30)
    assert policy.admission("owner", "char", state, call_scoped=True, **kwargs) is None
    assert policy.admission("owner", "char", state, **kwargs) == "blocked_user_active"  # not scoped


def test_call_scope_lowers_min_interval_for_the_camera_source(sandbox, monkeypatch):
    from core.autonomy import policy

    _admission_ready(monkeypatch)
    state = _state(sandbox, {"video_call_camera": {"last_evaluated_at": time.time() - 300}})
    kwargs = dict(allow_observed_activity=True, allow_camera_silence=True)
    assert policy.admission("owner", "char", state, **kwargs) == "duplicate"  # 300 s < 900 s
    _open_call()
    _enable(monkeypatch, min_gap_seconds=180)
    assert policy.admission("owner", "char", state, call_scoped=True, **kwargs) is None


def test_loop_cancellation_uses_the_same_scoped_window(monkeypatch):
    from core.autonomy import runner
    from core.autonomy.models import Job
    from core.scheduler import loop

    job = Job(uid="owner", char_id="char", source="autonomy",
              opportunity={"signals": [{"source": "video_call_camera"}]})
    monkeypatch.setattr(loop, "last_user_message_time", lambda: time.time() - 60)
    monkeypatch.setattr(loop, "_user_active_recently", lambda window_seconds=120: True)
    assert runner._user_became_active_for_job(job) is True  # feature off: global 120 s rule
    _open_call()
    _enable(monkeypatch, silence_seconds=30)
    assert runner._user_became_active_for_job(job) is False  # 60 s quiet > 30 s call window
    monkeypatch.setattr(loop, "last_user_message_time", lambda: time.time() - 10)
    assert runner._user_became_active_for_job(job) is True


# ── per-call cap and gap, session scoped ────────────────────────────────────

def test_per_call_cap_and_gap_then_restored_when_the_call_ends(monkeypatch):
    _enable(monkeypatch, max_talks_per_call=2, min_gap_seconds=180)
    session = _open_call()
    assert presence.talk_check("owner", "char") == ("allow", "ok")
    presence.record_talk("owner", "char")
    assert presence.talk_check("owner", "char")[0] == "soft"  # gap not elapsed
    session["presence_last_talk_at"] = time.time() - 181
    assert presence.talk_check("owner", "char") == ("allow", "ok")
    presence.record_talk("owner", "char")
    session["presence_last_talk_at"] = time.time() - 999
    assert presence.talk_check("owner", "char")[0] == "hard"  # cap reached: not skippable
    assert presence.snapshot()["gate_blocks"] == {"call_gap_not_elapsed": 1, "call_talk_cap": 1}
    assert presence.snapshot()["talks_total"] == 2
    video_call.close_camera("owner", "char", "desktop")
    assert presence.talk_check("owner", "char") == ("allow", "ok")  # scope is gone with the session


def test_a_new_call_starts_with_a_fresh_count(monkeypatch):
    _enable(monkeypatch, max_talks_per_call=1)
    _open_call()
    presence.record_talk("owner", "char")
    assert presence.talk_check("owner", "char")[0] == "hard"
    video_call.close_camera("owner", "char", "desktop")
    _open_call()
    assert presence.talk_check("owner", "char") == ("allow", "ok")


def test_talk_gate_swaps_only_the_gap_and_daily_checks(monkeypatch):
    from core.autonomy import talk_gate
    from core.dream.dream_state import DreamGuardStatus

    monkeypatch.setattr("core.scheduler.proactive_ledger.can_send", lambda *a, **k: (False, "gap_not_elapsed"))
    monkeypatch.setattr("core.scheduler.proactive_ledger.continuity_status",
                        lambda uid: {"consecutive_unanswered_talks": 0})
    monkeypatch.setattr("core.character_loader.is_proactive_disabled", lambda: False)
    monkeypatch.setattr("core.scheduler.triggers.dnd.is_dnd", lambda uid: False)
    monkeypatch.setattr("core.dream.dream_state.get_reality_guard_status", lambda _uid: DreamGuardStatus.ALLOW)
    _enable(monkeypatch)
    _open_call()
    assert talk_gate.check("owner") == ("soft", "gap_not_elapsed")  # global rule
    assert talk_gate.check("owner", call_char_id="char") == ("allow", "ok")  # call-scoped rule
    # Hard rules still bind inside a call.
    monkeypatch.setattr("core.scheduler.proactive_ledger.continuity_status",
                        lambda uid: {"consecutive_unanswered_talks": 2})
    assert talk_gate.check("owner", call_char_id="char")[0] == "hard"
    monkeypatch.setattr("core.scheduler.proactive_ledger.continuity_status",
                        lambda uid: {"consecutive_unanswered_talks": 0})
    monkeypatch.setattr("core.scheduler.triggers.dnd.is_dnd", lambda uid: True)
    assert talk_gate.check("owner", call_char_id="char")[0] == "hard"


@pytest.mark.asyncio
async def test_in_call_talk_is_still_recorded_in_the_ledger(monkeypatch):
    from core.autonomy import talk_gate

    recorded = []
    monkeypatch.setattr(talk_gate, "check", lambda *a, **k: ("allow", "ok"))
    monkeypatch.setattr("core.pipeline_registry.get", lambda: object())
    monkeypatch.setattr("channels.registry.get_active", lambda: ["desktop"])
    monkeypatch.setattr("core.autonomy.store.claim_delivery_correlation", lambda *a, **k: True)

    async def fake_turn(**_kwargs):
        return SimpleNamespace(fanout_targets=["desktop"])

    monkeypatch.setattr("core.turn_sink.record_assistant_turn", fake_turn)
    monkeypatch.setattr("core.scheduler.proactive_ledger.record_send",
                        lambda *a, **k: recorded.append((a, k)))
    _enable(monkeypatch)
    _open_call()
    ok, reason = await talk_gate.send("owner", "char", "你在看什么呀", source="autonomy",
                                      run_id="r1", call_scoped=True)
    assert (ok, reason) == (True, "sent")
    assert len(recorded) == 1  # exemption skips the gate, never the accounting
    assert video_call._camera_sessions[("owner", "char")]["presence_talks"] == 1


def test_camera_signal_interval_follows_the_call_scope(monkeypatch):
    session = _open_call()
    assert presence.camera_signal_interval(session) == video_call.CAMERA_SIGNAL_INTERVAL_SECONDS
    _enable(monkeypatch, camera_signal_interval_seconds=20)
    assert presence.camera_signal_interval(session) == 20.0
    assert "presence" in video_call.snapshot({})
