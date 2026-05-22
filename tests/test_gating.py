def _proposal(name, urgency, requires_state, bypass=False):
    from core.scheduler.gating import TriggerProposal

    return TriggerProposal(
        trigger_name=name,
        urgency=urgency,
        topic_source="random",
        requires_state=requires_state,
        bypass_state_machine=bypass,
    )


def test_picks_highest_urgency(monkeypatch):
    from core.scheduler import gating
    from core.scheduler.state_machine import TriggerState

    monkeypatch.setattr(gating, "get_current_state", lambda uid: TriggerState.QUIET)
    monkeypatch.setattr(gating, "is_trigger_ready", lambda name: True)

    picked = gating.collect_and_decide(
        "u1",
        [
            _proposal("low", 0.5, [TriggerState.QUIET]),
            _proposal("high", 0.9, [TriggerState.QUIET]),
        ],
    )

    assert picked.trigger_name == "high"


def test_requires_state_filters_candidates(monkeypatch):
    from core.scheduler import gating
    from core.scheduler.state_machine import TriggerState

    monkeypatch.setattr(gating, "get_current_state", lambda uid: TriggerState.CHATTING)
    monkeypatch.setattr(gating, "is_trigger_ready", lambda name: True)

    picked = gating.collect_and_decide("u1", [_proposal("quiet_only", 0.9, [TriggerState.QUIET])])

    assert picked is None


def test_bypass_state_machine_skips_state_filter(monkeypatch):
    from core.scheduler import gating
    from core.scheduler.state_machine import TriggerState

    monkeypatch.setattr(gating, "get_current_state", lambda uid: TriggerState.CHATTING)
    monkeypatch.setattr(gating, "is_trigger_ready", lambda name: True)

    picked = gating.collect_and_decide(
        "u1",
        [_proposal("hr_critical", 0.9, [TriggerState.QUIET], bypass=True)],
    )

    assert picked.trigger_name == "hr_critical"


def test_one_tick_returns_at_most_one_candidate(monkeypatch):
    from core.scheduler import gating
    from core.scheduler.state_machine import TriggerState

    monkeypatch.setattr(gating, "get_current_state", lambda uid: TriggerState.QUIET)
    monkeypatch.setattr(gating, "is_trigger_ready", lambda name: True)

    picked = gating.collect_and_decide(
        "u1",
        [
            _proposal("a", 0.8, [TriggerState.QUIET]),
            _proposal("b", 0.7, [TriggerState.QUIET]),
            _proposal("c", 0.6, [TriggerState.QUIET]),
        ],
    )

    assert picked.trigger_name == "a"


def test_collect_and_decide_only_reads_cooldown(monkeypatch):
    from core.scheduler import gating
    from core.scheduler.state_machine import TriggerState

    calls = []

    monkeypatch.setattr(gating, "get_current_state", lambda uid: TriggerState.QUIET)
    monkeypatch.setattr(gating, "is_trigger_ready", lambda name: calls.append(name) or True)

    picked = gating.collect_and_decide("u1", [_proposal("random_message", 0.5, [TriggerState.QUIET])])

    assert picked.trigger_name == "random_message"
    assert calls == ["random_message", "random_message"]
