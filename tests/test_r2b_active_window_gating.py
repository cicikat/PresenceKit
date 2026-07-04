"""
R2-B: active-window / DND 前移到 gating 仲裁阶段的专项测试

覆盖：
  1. gating._decide 的 active-window 过滤（POLICY_TABLE driven）
  2. gating._decide 的 DND 过滤（emergency 豁免）
  3. 被阻止的 proposal 不触发 mark
  4. 维护型 trigger 不受 active-window/DND 影响（结构保证）
  5. _pipeline_send legacy 安全网（policy 委托，非 _HIGH_PRIORITY_TRIGGERS inline）
  6. _pipeline_send DND 检查
"""

from __future__ import annotations

import inspect
import pathlib
import time
from types import SimpleNamespace
from typing import Optional

import pytest

ROOT = pathlib.Path(__file__).parent.parent


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures / helpers
# ─────────────────────────────────────────────────────────────────────────────

class _FakePipeline:
    async def fetch_context(self, uid, query, **kwargs):
        return {}

    def build_prompt(self, uid, prompt, context, **kwargs):
        return [{"role": "user", "content": prompt}], {}

    async def run_llm(self, messages):
        return "reply"


def _make_proposal(trigger_name: str, urgency: float = 0.5):
    """Create a minimal TriggerProposal for testing."""
    from core.scheduler.gating import TriggerProposal
    from core.scheduler.state_machine import TriggerState

    return TriggerProposal(
        trigger_name=trigger_name,
        urgency=urgency,
        topic_source="test",
        requires_state=[TriggerState.QUIET],
    )


def _patch_decide_env(monkeypatch, *, user_active: bool, dnd_active: bool):
    """Patch loop._user_active_recently and dnd.is_dnd to fixed values."""
    import core.scheduler.loop as _loop
    import core.scheduler.triggers.dnd as _dnd

    monkeypatch.setattr(_loop, "_user_active_recently", lambda: user_active)
    monkeypatch.setattr(_dnd, "is_dnd", lambda uid: dnd_active)
    # Fix state to QUIET and cooldown to ready so only aw/dnd filters are tested.
    from core.scheduler.state_machine import TriggerState
    monkeypatch.setattr("core.scheduler.gating.get_current_state", lambda uid: TriggerState.QUIET)
    monkeypatch.setattr("core.scheduler.gating.is_trigger_ready", lambda name: True)


# ─────────────────────────────────────────────────────────────────────────────
# A. gating._decide active-window filter
# ─────────────────────────────────────────────────────────────────────────────

class TestGatingActiveWindowFilter:
    """gating._decide uses POLICY_TABLE to apply active-window behavior."""

    def test_filler_drop_blocked_when_user_active(self, monkeypatch):
        """random_message (filler/drop) is filtered when user is active."""
        from core.scheduler.gating import _decide

        _patch_decide_env(monkeypatch, user_active=True, dnd_active=False)
        proposals = [_make_proposal("random_message")]
        picked, reason, _ = _decide("u1", proposals)
        assert picked is None
        assert reason == "active_window_filtered"

    def test_normal_defer_blocked_when_user_active(self, monkeypatch):
        """topic_followup (normal/defer) is filtered when user is active."""
        from core.scheduler.gating import _decide

        _patch_decide_env(monkeypatch, user_active=True, dnd_active=False)
        proposals = [_make_proposal("topic_followup")]
        picked, reason, _ = _decide("u1", proposals)
        assert picked is None
        assert reason == "active_window_filtered"

    def test_emergency_exempt_passes_when_user_active(self, monkeypatch):
        """hr_critical (emergency/exempt) passes even when user is active."""
        from core.scheduler.gating import _decide

        _patch_decide_env(monkeypatch, user_active=True, dnd_active=False)
        proposals = [_make_proposal("hr_critical", urgency=1.0)]
        picked, reason, _ = _decide("u1", proposals)
        assert picked is not None
        assert picked.trigger_name == "hr_critical"

    def test_birthday_midnight_exempt_passes_when_user_active(self, monkeypatch):
        """birthday_midnight (high/exempt) passes when user is active."""
        from core.scheduler.gating import _decide

        _patch_decide_env(monkeypatch, user_active=True, dnd_active=False)
        proposals = [_make_proposal("birthday_midnight", urgency=0.9)]
        picked, reason, _ = _decide("u1", proposals)
        assert picked is not None
        assert picked.trigger_name == "birthday_midnight"

    def test_birthday_eve_exempt_passes_when_user_active(self, monkeypatch):
        """birthday_eve is now exempt (R2-B alignment) and passes when user active."""
        from core.scheduler.gating import _decide

        _patch_decide_env(monkeypatch, user_active=True, dnd_active=False)
        proposals = [_make_proposal("birthday_eve", urgency=0.9)]
        picked, reason, _ = _decide("u1", proposals)
        assert picked is not None
        assert picked.trigger_name == "birthday_eve"

    def test_all_proposals_blocked_returns_none(self, monkeypatch):
        """All non-exempt proposals blocked → picks nothing."""
        from core.scheduler.gating import _decide

        _patch_decide_env(monkeypatch, user_active=True, dnd_active=False)
        proposals = [
            _make_proposal("random_message"),
            _make_proposal("morning_greeting"),
            _make_proposal("topic_followup"),
        ]
        picked, reason, _ = _decide("u1", proposals)
        assert picked is None
        assert reason == "active_window_filtered"

    def test_exempt_wins_over_non_exempt_when_user_active(self, monkeypatch):
        """When user active, only exempt proposal wins even if others have higher urgency."""
        from core.scheduler.gating import _decide

        _patch_decide_env(monkeypatch, user_active=True, dnd_active=False)
        proposals = [
            _make_proposal("random_message", urgency=0.99),   # filler/drop – blocked
            _make_proposal("hr_critical", urgency=0.5),        # emergency/exempt – passes
        ]
        picked, reason, _ = _decide("u1", proposals)
        assert picked is not None
        assert picked.trigger_name == "hr_critical"

    def test_no_filter_when_user_inactive(self, monkeypatch):
        """When user is not active, active-window filter does not run."""
        from core.scheduler.gating import _decide

        _patch_decide_env(monkeypatch, user_active=False, dnd_active=False)
        proposals = [_make_proposal("random_message", urgency=0.5)]
        picked, reason, _ = _decide("u1", proposals)
        assert picked is not None
        assert picked.trigger_name == "random_message"

    def test_unknown_trigger_blocked_when_user_active(self, monkeypatch):
        """Unknown trigger (not in POLICY_TABLE) defaults to 'defer' → blocked when user active."""
        from core.scheduler.gating import TriggerProposal, _decide
        from core.scheduler.state_machine import TriggerState

        _patch_decide_env(monkeypatch, user_active=True, dnd_active=False)
        # Unknown trigger not in POLICY_TABLE
        proposal = TriggerProposal(
            trigger_name="__nonexistent_trigger__",
            urgency=0.5,
            topic_source="test",
            requires_state=[TriggerState.QUIET],
        )
        picked, reason, _ = _decide("u1", [proposal])
        assert picked is None
        assert reason == "active_window_filtered"

    def test_candidate_serialization_includes_aw_fields(self, monkeypatch):
        """Serialized candidate includes aw_behavior, aw_blocked, dnd_blocked fields."""
        from core.scheduler.gating import _decide

        _patch_decide_env(monkeypatch, user_active=True, dnd_active=False)
        proposals = [_make_proposal("random_message")]
        _, _, candidates = _decide("u1", proposals)
        assert len(candidates) == 1
        c = candidates[0]
        assert "aw_behavior" in c
        assert "aw_blocked" in c
        assert "dnd_blocked" in c
        assert c["aw_blocked"] is True
        assert c["dnd_blocked"] is False


# ─────────────────────────────────────────────────────────────────────────────
# B. gating._decide DND filter
# ─────────────────────────────────────────────────────────────────────────────

class TestGatingDNDFilter:
    """gating._decide DND filter: only emergency passes when DND is active."""

    def test_dnd_blocks_normal_trigger(self, monkeypatch):
        """Normal trigger blocked when DND is active."""
        from core.scheduler.gating import _decide

        _patch_decide_env(monkeypatch, user_active=False, dnd_active=True)
        proposals = [_make_proposal("morning_greeting")]
        picked, reason, _ = _decide("u1", proposals)
        assert picked is None
        assert reason == "dnd_filtered"

    def test_dnd_blocks_high_priority_trigger(self, monkeypatch):
        """High (non-emergency) trigger is still blocked when DND is active."""
        from core.scheduler.gating import _decide

        _patch_decide_env(monkeypatch, user_active=False, dnd_active=True)
        proposals = [_make_proposal("birthday_midnight")]   # high, not emergency
        picked, reason, _ = _decide("u1", proposals)
        assert picked is None
        assert reason == "dnd_filtered"

    def test_dnd_allows_emergency_trigger(self, monkeypatch):
        """hr_critical (emergency) passes even when DND is active."""
        from core.scheduler.gating import _decide

        _patch_decide_env(monkeypatch, user_active=False, dnd_active=True)
        proposals = [_make_proposal("hr_critical", urgency=1.0)]
        picked, reason, _ = _decide("u1", proposals)
        assert picked is not None
        assert picked.trigger_name == "hr_critical"

    def test_dnd_does_not_block_when_inactive(self, monkeypatch):
        """When DND is not active, normal trigger passes."""
        from core.scheduler.gating import _decide

        _patch_decide_env(monkeypatch, user_active=False, dnd_active=False)
        proposals = [_make_proposal("morning_greeting")]
        picked, reason, _ = _decide("u1", proposals)
        assert picked is not None

    def test_dnd_blocked_candidate_has_dnd_blocked_true(self, monkeypatch):
        """Serialized candidate shows dnd_blocked=True when DND active."""
        from core.scheduler.gating import _decide

        _patch_decide_env(monkeypatch, user_active=False, dnd_active=True)
        proposals = [_make_proposal("morning_greeting")]
        _, _, candidates = _decide("u1", proposals)
        assert candidates[0]["dnd_blocked"] is True


# ─────────────────────────────────────────────────────────────────────────────
# C. Blocked proposal is not marked
# ─────────────────────────────────────────────────────────────────────────────

class TestBlockedProposalNotMarked:
    """When proposal blocked by active-window or DND, _mark is never called."""

    def test_active_window_block_gating_returns_none(self, monkeypatch):
        """gating._decide returns None when user active and all proposals are non-exempt."""
        _patch_decide_env(monkeypatch, user_active=True, dnd_active=False)
        from core.scheduler.gating import _decide

        execute_calls = []

        async def _fake_execute(*, dry_run):
            execute_calls.append(dry_run)

        proposals = [_make_proposal("random_message")]
        proposals[0] = proposals[0].__class__(
            trigger_name=proposals[0].trigger_name,
            urgency=proposals[0].urgency,
            topic_source=proposals[0].topic_source,
            requires_state=proposals[0].requires_state,
            execute=_fake_execute,
        )
        picked, reason, _ = _decide("u1", proposals)

        # gating returned None → execute was never even considered
        assert picked is None
        assert reason == "active_window_filtered"
        assert execute_calls == []

    @pytest.mark.asyncio
    async def test_execute_prompt_does_not_mark_when_pipeline_returns_none(self, monkeypatch):
        """execute_prompt does not call _mark when _pipeline_send returns None (any block reason)."""
        from core.scheduler import execution
        import core.scheduler.loop as _loop

        marks = []

        async def blocked_pipeline(prompt, **kwargs):
            return None  # simulates active-window or DND block

        monkeypatch.setattr(_loop, "_pipeline_send", blocked_pipeline)
        monkeypatch.setattr(_loop, "_mark", lambda name: marks.append(name))

        result = await execution.execute_prompt(
            trigger_name="random_message",
            prompt_factory=lambda: "prompt",
            dry_run=False,
            would_mark=["random_message"],
        )

        assert result.sent is False
        assert marks == []


# ─────────────────────────────────────────────────────────────────────────────
# D. Maintenance triggers not affected by active-window/DND
# ─────────────────────────────────────────────────────────────────────────────

class TestMaintenanceTriggerIsolation:
    """Maintenance triggers do not go through _pipeline_send or gating proposals."""

    def test_hidden_state_decay_no_pipeline_send(self):
        """hidden_state_decay does not call _pipeline_send (maintenance trigger)."""
        src = (ROOT / "core/scheduler/triggers/hidden_state_decay.py").read_text(encoding="utf-8")
        assert "_pipeline_send" not in src

    def test_episodic_sweep_no_pipeline_send(self):
        """episodic_sweep does not call _pipeline_send (maintenance trigger)."""
        src = (ROOT / "core/scheduler/triggers/episodic_sweep.py").read_text(encoding="utf-8")
        assert "_pipeline_send" not in src

    def test_maintenance_triggers_not_in_policy_table(self):
        """Pure-maintenance triggers have no POLICY_TABLE entry (not speaking, never filtered)."""
        from core.scheduler.policy import POLICY_TABLE

        pure_maintenance = {
            "episodic_decay", "dlq_monitor", "log_maintenance",
            "episodic_sweep", "hidden_state_decay", "hidden_state_consolidate",
        }
        for tid in pure_maintenance:
            assert tid not in POLICY_TABLE, (
                f"{tid} appeared in POLICY_TABLE — maintenance triggers must not be speaking triggers."
            )


# ─────────────────────────────────────────────────────────────────────────────
# E. _pipeline_send: R2-C — safety-net helpers removed, gating is sole authority
# ─────────────────────────────────────────────────────────────────────────────

class TestPipelineSendR2C:
    """R2-C: _pipeline_send no longer re-gates triggers; gating._decide is the sole authority."""

    def test_pipeline_send_has_no_legacy_active_window_blocks_call(self):
        """R2-C: _pipeline_send must NOT call _legacy_active_window_blocks (deleted)."""
        import core.scheduler.loop as loop
        src = inspect.getsource(loop._pipeline_send)
        assert "_legacy_active_window_blocks" not in src

    def test_pipeline_send_has_no_legacy_dnd_blocks_call(self):
        """R2-C: _pipeline_send must NOT call _legacy_dnd_blocks (deleted)."""
        import core.scheduler.loop as loop
        src = inspect.getsource(loop._pipeline_send)
        assert "_legacy_dnd_blocks" not in src

    def test_legacy_active_window_blocks_does_not_exist(self):
        """R2-C: _legacy_active_window_blocks has been removed from loop module."""
        import core.scheduler.loop as loop
        assert not hasattr(loop, "_legacy_active_window_blocks"), (
            "_legacy_active_window_blocks still exists in loop — R2-C requires removal"
        )

    def test_legacy_dnd_blocks_does_not_exist(self):
        """R2-C: _legacy_dnd_blocks has been removed from loop module."""
        import core.scheduler.loop as loop
        assert not hasattr(loop, "_legacy_dnd_blocks"), (
            "_legacy_dnd_blocks still exists in loop — R2-C requires removal"
        )

    @pytest.mark.asyncio
    async def test_exempt_trigger_sends_when_user_active(self, monkeypatch):
        """hr_critical (exempt) sends via gating path even when user is active."""
        import core.scheduler.loop as loop

        recorded = []

        async def fake_record_assistant_turn(**kwargs):
            recorded.append(kwargs)
            return SimpleNamespace(fanout_failures={})

        monkeypatch.setattr("core.pipeline_registry.get", lambda: _FakePipeline())
        monkeypatch.setattr(loop, "_owner_id", lambda: "u1")
        monkeypatch.setattr(loop, "_last_user_message_time", time.time())  # user active
        monkeypatch.setattr("core.scheduler.triggers.birthday._is_birthday_period", lambda: False)
        monkeypatch.setattr("core.turn_sink.record_assistant_turn", fake_record_assistant_turn)

        result = await loop._pipeline_send("prompt", trigger_name="hr_critical")
        assert result == "reply"
        assert recorded and recorded[0]["trigger_name"] == "hr_critical"

    @pytest.mark.asyncio
    async def test_pipeline_send_does_not_block_filler_directly(self, monkeypatch):
        """R2-C: _pipeline_send no longer blocks filler triggers — that is gating's job."""
        import core.scheduler.loop as loop

        recorded = []

        async def fake_record_assistant_turn(**kwargs):
            recorded.append(kwargs)
            return SimpleNamespace(fanout_failures={})

        monkeypatch.setattr("core.pipeline_registry.get", lambda: _FakePipeline())
        monkeypatch.setattr(loop, "_owner_id", lambda: "u1")
        monkeypatch.setattr(loop, "_last_user_message_time", time.time())  # user active
        monkeypatch.setattr("core.scheduler.triggers.birthday._is_birthday_period", lambda: False)
        monkeypatch.setattr("core.turn_sink.record_assistant_turn", fake_record_assistant_turn)

        # random_message (filler/drop) is no longer blocked by _pipeline_send;
        # gating already filtered it before execute() was called.
        result = await loop._pipeline_send("prompt", trigger_name="random_message")
        assert result == "reply"

    @pytest.mark.asyncio
    async def test_execute_prompt_does_not_mark_when_pipeline_returns_none(self, monkeypatch):
        """execute_prompt does not call _mark when _pipeline_send returns None."""
        from core.scheduler import execution
        import core.scheduler.loop as _loop

        marks = []

        async def blocked_pipeline(prompt, **kwargs):
            return None

        monkeypatch.setattr(_loop, "_pipeline_send", blocked_pipeline)
        monkeypatch.setattr(_loop, "_mark", lambda name: marks.append(name))

        result = await execution.execute_prompt(
            trigger_name="random_message",
            prompt_factory=lambda: "prompt",
            dry_run=False,
            would_mark=["random_message"],
        )

        assert result.sent is False
        assert marks == []

    @pytest.mark.asyncio
    async def test_pipeline_send_passes_through_with_dnd_on(self, monkeypatch):
        """R2-C: _pipeline_send does not check DND — gating handles it before execute()."""
        import core.scheduler.loop as loop
        import core.scheduler.triggers.dnd as _dnd

        recorded = []

        async def fake_record_assistant_turn(**kwargs):
            recorded.append(kwargs)
            return SimpleNamespace(fanout_failures={})

        monkeypatch.setattr("core.pipeline_registry.get", lambda: _FakePipeline())
        monkeypatch.setattr(loop, "_owner_id", lambda: "u1")
        monkeypatch.setattr(loop, "_last_user_message_time", 0.0)
        monkeypatch.setattr(_dnd, "is_dnd", lambda uid: True)
        monkeypatch.setattr("core.scheduler.triggers.birthday._is_birthday_period", lambda: False)
        monkeypatch.setattr("core.turn_sink.record_assistant_turn", fake_record_assistant_turn)

        # _pipeline_send itself no longer checks DND; gating already did.
        result = await loop._pipeline_send("prompt", trigger_name="morning_greeting")
        assert result == "reply"


# ─────────────────────────────────────────────────────────────────────────────
# F. policy.py alignment (R2-B mismatch resolution)
# ─────────────────────────────────────────────────────────────────────────────

class TestPolicyAlignment:
    """R2-B: policy.py exempts align with loop._HIGH_PRIORITY_TRIGGERS."""

    def test_policy_exempt_set_matches_high_priority_triggers(self):
        """POLICY_TABLE exempt set == loop._HIGH_PRIORITY_TRIGGERS (no mismatch)."""
        from core.scheduler.policy import POLICY_TABLE
        from core.scheduler.loop import _HIGH_PRIORITY_TRIGGERS

        policy_exempt = {
            tid for tid, p in POLICY_TABLE.items()
            if p.active_window_behavior == "exempt"
        }
        assert policy_exempt == _HIGH_PRIORITY_TRIGGERS, (
            f"Mismatch: policy_exempt={policy_exempt}, "
            f"_HIGH_PRIORITY_TRIGGERS={_HIGH_PRIORITY_TRIGGERS}"
        )

    def test_birthday_series_all_exempt(self):
        """All four birthday triggers are exempt in POLICY_TABLE after R2-B."""
        from core.scheduler.policy import POLICY_TABLE

        for tid in ("birthday_midnight", "birthday_eve", "birthday_afternoon", "birthday_night"):
            assert POLICY_TABLE[tid].active_window_behavior == "exempt", (
                f"{tid} not exempt in POLICY_TABLE after R2-B."
            )

    def test_filler_triggers_all_drop(self):
        """Filler triggers (random_message etc.) have active_window_behavior='drop'."""
        from core.scheduler.policy import POLICY_TABLE

        fillers = ["random_message", "spontaneous_recall", "festival", "holiday_boost", "timenode"]
        for tid in fillers:
            if tid in POLICY_TABLE:
                assert POLICY_TABLE[tid].active_window_behavior == "drop", (
                    f"{tid} active_window_behavior changed from drop."
                )

    def test_policy_validate_all_still_passes(self):
        """_validate_all() still passes after R2-B policy changes."""
        from core.scheduler.policy import _validate_all
        _validate_all()
