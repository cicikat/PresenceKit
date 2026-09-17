from tests.fixtures.public_assets import TEST_CHAR_ID
import asyncio

import pytest


def test_bucket_score_and_threshold():
    from core.scheduler.overflow_bucket import OverflowSignals

    signals = OverflowSignals(
        time_gap_score=1.0,
        episodic_score=0.8,
        hidden_need_score=0.5,
        garden_score=0.8,
        mood_score=1.0,
    )

    assert signals.bucket_score() == pytest.approx(1.84)
    assert signals.is_overflow(jitter=0.0) is True
    assert signals.is_overflow(jitter=0.15) is True


def test_compute_signals_reads_current_models_and_picks_weighted_top(monkeypatch):
    from core.garden import manager as garden_manager
    from core.memory import episodic_memory, mood_state, short_term
    from core.memory import user_hidden_state_store
    from core.memory.user_hidden_state import default_hidden_state
    from core.scheduler import overflow_bucket

    now = 2_000_000_000.0
    hidden = default_hidden_state()
    hidden.sensitivity.current.value = 100.0
    hidden.sensitivity.baseline.value = 50.0
    hidden.touch_need.deficit.value = 100.0

    monkeypatch.setattr(overflow_bucket.time, "time", lambda: now)
    monkeypatch.setattr(
        short_term,
        "load",
        lambda uid, *, char_id: [{"timestamp": now - 24 * 3600}],
    )
    monkeypatch.setattr(
        episodic_memory,
        "_load_memories",
        lambda uid, *, char_id: [{
            "strength": 0.9,
            "timestamp": now - 5 * 86400,
            "last_retrieved": None,
            "narrative_summary": "那次认真谈过的事",
        }],
    )
    monkeypatch.setattr(
        user_hidden_state_store,
        "load_hidden_state",
        lambda uid, *, char_id: hidden,
    )
    monkeypatch.setattr(
        garden_manager,
        "get_shareable_event",
        lambda *, char_id: "玫瑰开花了",
    )
    monkeypatch.setattr(mood_state, "get_intensity", lambda *, char_id: 1.0)

    signals = overflow_bucket.compute_signals("u1", char_id="character_b")

    assert signals.time_gap_score == pytest.approx(1.0)
    assert signals.episodic_score == pytest.approx(0.9)
    assert signals.hidden_need_score == pytest.approx(1.0)
    assert signals.garden_score == pytest.approx(0.8)
    assert signals.mood_score == pytest.approx(1.0)
    assert signals.bucket_score() == pytest.approx(2.09)
    assert signals.top_signal == "time_gap"
    assert "24小时" in signals.top_signal_detail


def test_compute_signals_is_fail_closed_per_source(monkeypatch):
    from core.garden import manager as garden_manager
    from core.memory import episodic_memory, mood_state, short_term
    from core.memory import user_hidden_state_store
    from core.scheduler import overflow_bucket

    def fail(*args, **kwargs):
        raise RuntimeError("unavailable")

    monkeypatch.setattr(short_term, "load", fail)
    monkeypatch.setattr(episodic_memory, "_load_memories", fail)
    monkeypatch.setattr(user_hidden_state_store, "load_hidden_state", fail)
    monkeypatch.setattr(garden_manager, "get_shareable_event", fail)
    monkeypatch.setattr(mood_state, "get_intensity", fail)

    signals = overflow_bucket.compute_signals("u1", char_id=TEST_CHAR_ID)

    assert signals.bucket_score() == 0.0
    assert signals.top_signal == ""


def test_proposer_returns_grounded_quiet_proposal(monkeypatch):
    from core.scheduler import loop
    from core.scheduler.overflow_bucket import OverflowSignals
    from core.scheduler.state_machine import TriggerState
    from core.scheduler.triggers import overflow

    signals = OverflowSignals(
        time_gap_score=1.0,
        episodic_score=1.0,
        hidden_need_score=1.0,
        garden_score=1.0,
        mood_score=1.0,
        top_signal="episodic",
        top_signal_detail="那次认真谈过的事",
    )
    monkeypatch.setattr(
        "core.config_loader.get_config",
        lambda: {"scheduler": {"overflow_trigger": True}},
    )
    monkeypatch.setattr(loop, "_is_ready", lambda name, **_kwargs: True)
    monkeypatch.setattr(loop, "_owner_id", lambda: "owner")
    monkeypatch.setattr(loop, "_active_char_id_or_none", lambda: "character_b")
    monkeypatch.setattr(overflow, "compute_signals", lambda uid, *, char_id: signals)
    monkeypatch.setattr(overflow.random, "uniform", lambda low, high: 0.0)

    proposal = overflow.propose({})

    assert proposal.trigger_name == "overflow"
    assert proposal.topic_source == "overflow_bucket"
    assert proposal.requires_state == [TriggerState.QUIET]
    assert proposal.execute is not None


def test_proposer_execute_uses_overflow_prompt(monkeypatch):
    from core.scheduler import execution
    from core.scheduler.overflow_bucket import OverflowSignals
    from core.scheduler.triggers import overflow

    captured = {}

    async def fake_execute_prompt(**kwargs):
        captured.update(kwargs)
        return "sent"

    monkeypatch.setattr(execution, "execute_prompt", fake_execute_prompt)
    signals = OverflowSignals(
        episodic_score=1.0,
        top_signal="episodic",
        top_signal_detail="那次认真谈过的事",
    )

    result = asyncio.run(overflow._make_execute(signals)(dry_run=True))

    assert result == "sent"
    assert captured["trigger_name"] == "overflow"
    assert captured["would_mark"] == ["overflow"]
    prompt = captured["prompt_factory"]()
    assert "那次认真谈过的事" in prompt
    assert "触发机制" in prompt
