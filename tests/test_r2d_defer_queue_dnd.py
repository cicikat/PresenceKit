"""
tests/test_r2d_defer_queue_dnd.py

R2-D: defer 队列最小实现 + DND 主入口接线专项测试

覆盖：
 1. defer_queue.enqueue_defer 幂等性（只记录首次 deferral 时间）
 2. defer_queue.release_defer 正常移除
 3. defer_queue.scan_expired → force_send（on_defer_expire="force_send"）
 4. defer_queue.scan_expired → dropped（on_defer_expire="drop"）
 5. max_defer_age_secs=0 的条目不过期
 6. defer_queue.get_queue_snapshot 可观测状态
 7. gating._decide 活跃窗口过滤 defer 触发器时入队
 8. gating._decide 活跃窗口过滤 drop 触发器时不入队
 9. 过期 force_send 触发器在 user_active 时也能被选中
10. 过期 drop 触发器被 scan_expired 清除后不被选中
11. 被选中的触发器从 defer 队列释放
12. 被 defer/block 的触发器不被 mark
13. DND detect_and_set: DND 关键词 → 设置 DND
14. DND detect_and_set: 结束词 → 清除 DND
15. DND detect_and_set: 中性消息 → 不改变状态
16. DND 生效时阻止普通 scheduler 发言
17. DND 生效时 emergency 触发器仍可通过
18. DND 不影响维护型 tick（结构保证）
19. execution 层不重新做 winner/block 决策（no active_window/DND in _pipeline_send）
20. policy.py 被 gating runtime 路径使用（import 链完整）
21. defer 触发器被 block 时不调用 _mark
22. main.py 中 detect_and_set 调用点存在（DND 接线审计）
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

@pytest.fixture(autouse=True)
def _clean_defer_queue():
    """Ensure defer queue is clean before and after each test."""
    from core.scheduler.defer_queue import clear_all
    clear_all()
    yield
    clear_all()


def _make_proposal(trigger_name: str, urgency: float = 0.5):
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

    monkeypatch.setattr(_loop, "_user_active_recently", lambda *a, **kw: user_active)
    monkeypatch.setattr(_dnd, "is_dnd", lambda uid: dnd_active)
    from core.scheduler.state_machine import TriggerState
    monkeypatch.setattr("core.scheduler.gating.get_current_state", lambda uid: TriggerState.QUIET)
    monkeypatch.setattr("core.scheduler.gating.is_trigger_ready", lambda name: True)


# ─────────────────────────────────────────────────────────────────────────────
# 1–6. defer_queue 单元测试
# ─────────────────────────────────────────────────────────────────────────────

class TestDeferQueueUnit:
    """Unit tests for core/scheduler/defer_queue.py."""

    def test_enqueue_idempotent(self):
        """enqueue_defer records first enqueue_ts; subsequent calls are no-ops."""
        from core.scheduler.defer_queue import enqueue_defer, get_queue_snapshot

        enqueue_defer("u1", "hr_high")
        snap1 = get_queue_snapshot("u1")
        assert len(snap1) == 1
        first_ts = snap1[0]["enqueue_ts"]

        # second enqueue must NOT update enqueue_ts
        time.sleep(0.01)
        enqueue_defer("u1", "hr_high")
        snap2 = get_queue_snapshot("u1")
        assert len(snap2) == 1
        assert snap2[0]["enqueue_ts"] == first_ts, "enqueue_ts must not change on second call"

    def test_release_removes_item(self):
        """release_defer removes the entry from the queue."""
        from core.scheduler.defer_queue import enqueue_defer, release_defer, get_queue_snapshot

        enqueue_defer("u1", "hr_high")
        assert len(get_queue_snapshot("u1")) == 1
        release_defer("u1", "hr_high")
        assert len(get_queue_snapshot("u1")) == 0

    def test_release_nonexistent_is_noop(self):
        """Releasing a non-existent key does not raise."""
        from core.scheduler.defer_queue import release_defer
        release_defer("u1", "nonexistent")  # must not raise

    def test_scan_expired_force_send(self):
        """scan_expired returns force_send_names for reminders (on_defer_expire=force_send)."""
        from core.scheduler.defer_queue import enqueue_defer, scan_expired, get_queue_snapshot
        from core.scheduler.policy import POLICY_TABLE

        # Verify reminders uses force_send
        assert POLICY_TABLE["reminders"].on_defer_expire == "force_send"
        max_age = POLICY_TABLE["reminders"].max_defer_age_secs

        # Fast-forward past expiry by injecting enqueue_ts in the past
        enqueue_defer("u1", "reminders")
        # Manually set enqueue_ts to now - (max_age + 1)
        import core.scheduler.defer_queue as _dq
        _dq._defer_queue[("u1", "reminders")].enqueue_ts = time.time() - (max_age + 1)

        force_send, dropped = scan_expired("u1")
        assert "reminders" in force_send
        assert "reminders" not in dropped
        # Item must be removed from queue
        assert len(get_queue_snapshot("u1")) == 0

    def test_scan_expired_drop(self):
        """scan_expired returns dropped_names for hr_high (on_defer_expire=drop)."""
        from core.scheduler.defer_queue import enqueue_defer, scan_expired, get_queue_snapshot
        from core.scheduler.policy import POLICY_TABLE

        assert POLICY_TABLE["hr_high"].on_defer_expire == "drop"
        max_age = POLICY_TABLE["hr_high"].max_defer_age_secs

        enqueue_defer("u1", "hr_high")
        import core.scheduler.defer_queue as _dq
        _dq._defer_queue[("u1", "hr_high")].enqueue_ts = time.time() - (max_age + 1)

        force_send, dropped = scan_expired("u1")
        assert "hr_high" in dropped
        assert "hr_high" not in force_send
        assert len(get_queue_snapshot("u1")) == 0

    def test_scan_not_expired(self):
        """scan_expired returns empty sets when items are within TTL."""
        from core.scheduler.defer_queue import enqueue_defer, scan_expired, get_queue_snapshot

        enqueue_defer("u1", "hr_high")
        # Do NOT fast-forward time: item should still be fresh

        force_send, dropped = scan_expired("u1")
        assert not force_send
        assert not dropped
        assert len(get_queue_snapshot("u1")) == 1  # item still in queue

    def test_scan_zero_max_defer_never_expires(self):
        """A trigger with max_defer_age_secs=0 is never considered expired."""
        from core.scheduler.defer_queue import enqueue_defer, scan_expired, get_queue_snapshot
        import core.scheduler.defer_queue as _dq
        from unittest.mock import patch
        from core.scheduler.policy import TriggerPolicy

        # Enqueue the trigger first, then age it artificially
        enqueue_defer("u1", "topic_followup")
        _dq._defer_queue[("u1", "topic_followup")].enqueue_ts = time.time() - 9999  # very old

        fake_policy = TriggerPolicy(
            trigger_id="topic_followup",
            priority="normal",
            active_window_behavior="defer",
            max_defer_age_secs=0,  # no expiry
            on_defer_expire="drop",
        )
        import core.scheduler.policy as _policy
        with patch.dict(_policy.POLICY_TABLE, {"topic_followup": fake_policy}):
            force_send, dropped = scan_expired("u1")
        # Should not expire (max_defer_age_secs=0)
        assert "topic_followup" not in force_send
        assert "topic_followup" not in dropped
        # Item must still be in queue
        assert len(get_queue_snapshot("u1")) == 1

    def test_get_queue_snapshot_is_observable(self):
        """get_queue_snapshot returns current state including age_secs."""
        from core.scheduler.defer_queue import enqueue_defer, get_queue_snapshot

        enqueue_defer("u1", "hr_high")
        enqueue_defer("u1", "topic_followup")

        snap = get_queue_snapshot("u1")
        names = {e["trigger_name"] for e in snap}
        assert "hr_high" in names
        assert "topic_followup" in names
        for entry in snap:
            assert "enqueue_ts" in entry
            assert "age_secs" in entry
            assert entry["age_secs"] >= 0

    def test_snapshot_uid_filter(self):
        """get_queue_snapshot(uid) returns only entries for that uid."""
        from core.scheduler.defer_queue import enqueue_defer, get_queue_snapshot

        enqueue_defer("u1", "hr_high")
        enqueue_defer("u2", "topic_followup")

        snap_u1 = get_queue_snapshot("u1")
        snap_u2 = get_queue_snapshot("u2")
        assert all(e["uid"] == "u1" for e in snap_u1)
        assert all(e["uid"] == "u2" for e in snap_u2)

    def test_clear_uid(self):
        """clear_uid removes only entries for that uid."""
        from core.scheduler.defer_queue import enqueue_defer, clear_uid, get_queue_snapshot

        enqueue_defer("u1", "hr_high")
        enqueue_defer("u2", "topic_followup")

        clear_uid("u1")
        assert len(get_queue_snapshot("u1")) == 0
        assert len(get_queue_snapshot("u2")) == 1

    def test_multi_uid_isolation(self):
        """scan_expired only processes entries for the given uid."""
        from core.scheduler.defer_queue import enqueue_defer, scan_expired, get_queue_snapshot
        from core.scheduler.policy import POLICY_TABLE
        import core.scheduler.defer_queue as _dq

        max_age = POLICY_TABLE["hr_high"].max_defer_age_secs
        enqueue_defer("u1", "hr_high")
        enqueue_defer("u2", "hr_high")
        # Only expire u1's entry
        _dq._defer_queue[("u1", "hr_high")].enqueue_ts = time.time() - (max_age + 1)

        scan_expired("u1")
        assert len(get_queue_snapshot("u1")) == 0
        assert len(get_queue_snapshot("u2")) == 1  # u2 untouched


# ─────────────────────────────────────────────────────────────────────────────
# 7–11. gating._decide 与 defer 队列集成
# ─────────────────────────────────────────────────────────────────────────────

class TestGatingDeferQueueIntegration:
    """gating._decide integrates with defer_queue for defer-behavior triggers."""

    def test_defer_trigger_enqueued_when_user_active(self, monkeypatch):
        """When user active and defer trigger proposed, it's enqueued in defer queue."""
        from core.scheduler.gating import _decide
        from core.scheduler.defer_queue import get_queue_snapshot

        _patch_decide_env(monkeypatch, user_active=True, dnd_active=False)
        proposals = [_make_proposal("hr_high")]  # defer behavior
        picked, reason, _ = _decide("u1", proposals)

        assert picked is None
        assert reason == "active_window_filtered"
        snap = get_queue_snapshot("u1")
        names = {e["trigger_name"] for e in snap}
        assert "hr_high" in names, "defer trigger must be enqueued when blocked"

    def test_drop_trigger_not_enqueued_when_user_active(self, monkeypatch):
        """When user active and drop trigger proposed, it is NOT enqueued."""
        from core.scheduler.gating import _decide
        from core.scheduler.defer_queue import get_queue_snapshot

        _patch_decide_env(monkeypatch, user_active=True, dnd_active=False)
        proposals = [_make_proposal("random_message")]  # filler/drop behavior
        picked, reason, _ = _decide("u1", proposals)

        assert picked is None
        assert reason == "active_window_filtered"
        snap = get_queue_snapshot("u1")
        assert len(snap) == 0, "drop trigger must NOT be enqueued in defer queue"

    def test_expired_force_send_bypasses_active_window(self, monkeypatch):
        """Expired force_send trigger (reminders) passes active_window filter even when user active."""
        from core.scheduler.gating import _decide
        from core.scheduler.defer_queue import enqueue_defer
        from core.scheduler.policy import POLICY_TABLE
        import core.scheduler.defer_queue as _dq

        _patch_decide_env(monkeypatch, user_active=True, dnd_active=False)

        # Pre-expire the reminders entry
        max_age = POLICY_TABLE["reminders"].max_defer_age_secs
        enqueue_defer("u1", "reminders")
        _dq._defer_queue[("u1", "reminders")].enqueue_ts = time.time() - (max_age + 1)

        proposals = [_make_proposal("reminders", urgency=0.8)]
        picked, reason, _ = _decide("u1", proposals)

        assert picked is not None
        assert picked.trigger_name == "reminders"
        assert reason == "picked_highest_urgency"

    def test_expired_drop_trigger_not_picked(self, monkeypatch):
        """Expired drop trigger (hr_high) is removed from queue and not force-sent.

        After scan_expired drops the expired item, _decide re-enqueues hr_high freshly
        (user still active, defer behavior).  So the queue has 1 fresh item, not 0.
        """
        from core.scheduler.gating import _decide
        from core.scheduler.defer_queue import enqueue_defer, get_queue_snapshot
        from core.scheduler.policy import POLICY_TABLE
        import core.scheduler.defer_queue as _dq

        _patch_decide_env(monkeypatch, user_active=True, dnd_active=False)

        max_age = POLICY_TABLE["hr_high"].max_defer_age_secs
        # Pre-enqueue with an already-expired timestamp
        enqueue_defer("u1", "hr_high")
        old_ts = time.time() - (max_age + 60)
        _dq._defer_queue[("u1", "hr_high")].enqueue_ts = old_ts

        proposals = [_make_proposal("hr_high", urgency=0.8)]
        picked, reason, _ = _decide("u1", proposals)

        assert picked is None  # hr_high has on_defer_expire="drop", not force_send
        assert reason == "active_window_filtered"
        # The expired item was dropped by scan_expired, but then _decide re-enqueues
        # hr_high freshly because user is still active and hr_high is defer behavior.
        snap = get_queue_snapshot("u1")
        assert len(snap) == 1
        # Confirm the re-enqueued item is FRESH (not the old expired timestamp)
        assert snap[0]["enqueue_ts"] > old_ts, "fresh re-enqueue must have a newer timestamp"

    def test_picked_trigger_released_from_defer_queue(self, monkeypatch):
        """When a deferred trigger is picked (user became inactive), it's removed from queue."""
        from core.scheduler.gating import _decide
        from core.scheduler.defer_queue import enqueue_defer, get_queue_snapshot

        # First tick: user active → enqueue
        _patch_decide_env(monkeypatch, user_active=True, dnd_active=False)
        _decide("u1", [_make_proposal("hr_high")])
        assert len(get_queue_snapshot("u1")) == 1  # deferred

        # Second tick: user inactive → trigger picked and released
        import core.scheduler.loop as _loop
        import core.scheduler.triggers.dnd as _dnd
        monkeypatch.setattr(_loop, "_user_active_recently", lambda *a, **kw: False)
        monkeypatch.setattr(_dnd, "is_dnd", lambda uid: False)

        _decide("u1", [_make_proposal("hr_high")])
        assert len(get_queue_snapshot("u1")) == 0, "picked trigger must be released from queue"

    def test_candidate_serialization_includes_force_send_field(self, monkeypatch):
        """Serialized candidate includes force_send and deferred_age_secs fields."""
        from core.scheduler.gating import _decide
        from core.scheduler.defer_queue import enqueue_defer
        from core.scheduler.policy import POLICY_TABLE
        import core.scheduler.defer_queue as _dq

        _patch_decide_env(monkeypatch, user_active=True, dnd_active=False)

        # Pre-expire reminders to trigger force_send path
        max_age = POLICY_TABLE["reminders"].max_defer_age_secs
        enqueue_defer("u1", "reminders")
        _dq._defer_queue[("u1", "reminders")].enqueue_ts = time.time() - (max_age + 1)

        proposals = [_make_proposal("reminders")]
        _, _, candidates = _decide("u1", proposals)
        assert len(candidates) == 1
        c = candidates[0]
        assert "force_send" in c
        assert "deferred_age_secs" in c
        assert c["force_send"] is True

    def test_defer_enqueue_is_idempotent_across_ticks(self, monkeypatch):
        """Enqueue_ts only set on first tick; subsequent active-window blocks don't reset it."""
        from core.scheduler.gating import _decide
        from core.scheduler.defer_queue import get_queue_snapshot

        _patch_decide_env(monkeypatch, user_active=True, dnd_active=False)

        _decide("u1", [_make_proposal("hr_high")])
        snap1 = get_queue_snapshot("u1")
        ts1 = snap1[0]["enqueue_ts"]

        time.sleep(0.02)
        _decide("u1", [_make_proposal("hr_high")])
        snap2 = get_queue_snapshot("u1")
        ts2 = snap2[0]["enqueue_ts"]

        assert ts1 == ts2, "enqueue_ts must not update on second deferral"

    def test_non_deferred_trigger_not_in_queue_when_user_inactive(self, monkeypatch):
        """When user is inactive, no triggers are enqueued (normal pass-through)."""
        from core.scheduler.gating import _decide
        from core.scheduler.defer_queue import get_queue_snapshot

        _patch_decide_env(monkeypatch, user_active=False, dnd_active=False)
        _decide("u1", [_make_proposal("hr_high")])
        assert len(get_queue_snapshot("u1")) == 0


# ─────────────────────────────────────────────────────────────────────────────
# 12. 被 defer/block 的触发器不 mark
# ─────────────────────────────────────────────────────────────────────────────

class TestDeferredTriggerNotMarked:
    """Deferred/blocked trigger must not call _mark."""

    def test_deferred_trigger_not_marked(self, monkeypatch):
        """When trigger is active_window_filtered (deferred), _mark is never called."""
        import core.scheduler.loop as _loop

        _patch_decide_env(monkeypatch, user_active=True, dnd_active=False)
        from core.scheduler.gating import _decide

        marked = []
        monkeypatch.setattr(_loop, "_mark", lambda name: marked.append(name))

        _decide("u1", [_make_proposal("hr_high")])
        assert not marked, "_mark must not be called when trigger is deferred"

    def test_dnd_blocked_trigger_not_marked(self, monkeypatch):
        """When trigger is dnd_filtered, _mark is never called."""
        import core.scheduler.loop as _loop

        _patch_decide_env(monkeypatch, user_active=False, dnd_active=True)
        from core.scheduler.gating import _decide

        marked = []
        monkeypatch.setattr(_loop, "_mark", lambda name: marked.append(name))

        _decide("u1", [_make_proposal("topic_followup")])
        assert not marked, "_mark must not be called when trigger is DND-filtered"


# ─────────────────────────────────────────────────────────────────────────────
# 13–15. DND detect_and_set 行为
# ─────────────────────────────────────────────────────────────────────────────

class TestDNDDetectAndSet:
    """detect_and_set correctly sets / clears / ignores DND based on message content."""

    def test_dnd_keyword_sets_dnd(self):
        """Message with DND keyword sets DND for uid."""
        from core.scheduler.triggers.dnd import detect_and_set, is_dnd, clear_dnd

        uid = "test_dnd_user"
        clear_dnd(uid)
        detect_and_set(uid, "我现在在学习，等会再聊")
        assert is_dnd(uid), "DND keyword '学习' should set DND"
        clear_dnd(uid)

    def test_dnd_work_keyword_sets_dnd(self):
        """Message with '在忙' sets DND."""
        from core.scheduler.triggers.dnd import detect_and_set, is_dnd, clear_dnd

        uid = "test_dnd_user2"
        clear_dnd(uid)
        detect_and_set(uid, "现在在忙着呢")
        assert is_dnd(uid)
        clear_dnd(uid)

    def test_end_keyword_clears_dnd(self):
        """Message with end keyword clears existing DND."""
        from core.scheduler.triggers.dnd import detect_and_set, is_dnd, set_dnd, clear_dnd

        uid = "test_dnd_user3"
        set_dnd(uid)
        assert is_dnd(uid)
        detect_and_set(uid, "下课了终于")
        assert not is_dnd(uid), "End keyword '下课' should clear DND"

    def test_end_keyword_before_start_keyword(self):
        """When message contains both end and start keywords, end takes priority."""
        from core.scheduler.triggers.dnd import detect_and_set, is_dnd, set_dnd, clear_dnd

        uid = "test_dnd_user4"
        set_dnd(uid)  # DND is already active
        detect_and_set(uid, "搞定了，下午还要继续学习")  # end before start
        assert not is_dnd(uid), "End keyword takes priority over start keyword"

    def test_neutral_message_no_change(self):
        """Neutral message neither sets nor clears DND."""
        from core.scheduler.triggers.dnd import detect_and_set, is_dnd, clear_dnd

        uid = "test_dnd_neutral"
        clear_dnd(uid)
        detect_and_set(uid, "今天天气真好")
        assert not is_dnd(uid), "Neutral message must not set DND"

    def test_neutral_message_does_not_clear_dnd(self):
        """Neutral message doesn't clear an already-active DND."""
        from core.scheduler.triggers.dnd import detect_and_set, is_dnd, set_dnd, clear_dnd

        uid = "test_dnd_neutral2"
        set_dnd(uid)
        detect_and_set(uid, "随便说一句话")
        assert is_dnd(uid), "Neutral message must not clear DND"
        clear_dnd(uid)

    def test_multiple_uids_independent(self):
        """DND state is per-uid and independent."""
        from core.scheduler.triggers.dnd import detect_and_set, is_dnd, clear_dnd

        uid_a, uid_b = "dnd_uid_a", "dnd_uid_b"
        clear_dnd(uid_a)
        clear_dnd(uid_b)
        detect_and_set(uid_a, "开会中")
        assert is_dnd(uid_a)
        assert not is_dnd(uid_b)
        clear_dnd(uid_a)


# ─────────────────────────────────────────────────────────────────────────────
# 16–18. DND 与 scheduler 决策层集成
# ─────────────────────────────────────────────────────────────────────────────

class TestDNDSchedulerEffect:
    """DND blocks ordinary scheduler triggers; emergency exempt; maintenance unaffected."""

    def test_dnd_blocks_normal_trigger_in_gating(self, monkeypatch):
        """gating._decide returns dnd_filtered for normal trigger when DND active."""
        from core.scheduler.gating import _decide

        _patch_decide_env(monkeypatch, user_active=False, dnd_active=True)
        proposals = [_make_proposal("morning_greeting")]
        picked, reason, _ = _decide("u1", proposals)
        assert picked is None
        assert reason == "dnd_filtered"

    def test_dnd_allows_emergency(self, monkeypatch):
        """hr_critical (emergency) passes DND filter."""
        from core.scheduler.gating import _decide

        _patch_decide_env(monkeypatch, user_active=False, dnd_active=True)
        proposals = [_make_proposal("hr_critical", urgency=1.0)]
        picked, reason, _ = _decide("u1", proposals)
        assert picked is not None
        assert picked.trigger_name == "hr_critical"

    def test_dnd_blocks_defer_trigger(self, monkeypatch):
        """Defer trigger (hr_high) is also blocked by DND when user is inactive."""
        from core.scheduler.gating import _decide

        _patch_decide_env(monkeypatch, user_active=False, dnd_active=True)
        proposals = [_make_proposal("hr_high")]
        picked, reason, _ = _decide("u1", proposals)
        assert picked is None
        assert reason == "dnd_filtered"

    def test_maintenance_triggers_not_in_migrated_triggers(self):
        """Maintenance triggers are not in MIGRATED_TRIGGERS (not gated by speaking decision)."""
        from core.scheduler.gating import MIGRATED_TRIGGERS

        maintenance = {
            "log_maintenance", "episodic_sweep", "episodic_decay",
            "dlq_monitor", "diary_inject", "hidden_state_decay",
            "hidden_state_consolidate",
        }
        overlap = maintenance & MIGRATED_TRIGGERS
        assert not overlap, (
            f"Maintenance triggers must NOT be in MIGRATED_TRIGGERS: {overlap}"
        )

    def test_dnd_does_not_block_maintenance_structural(self):
        """Structural: maintenance triggers are excluded from gating speaking path.

        Maintenance _check_* functions do not call _pipeline_send; they interact
        directly with memory/state, so they are unaffected by DND at the gating level.
        This test verifies the architectural boundary is preserved by checking that
        no maintenance trigger uses legacy_tick_should_send (which would wrongly
        pause them when EXECUTE_MODE=live).
        """
        import ast

        maintenance_files = [
            ROOT / "core" / "scheduler" / "triggers" / "episodic_sweep.py",
            ROOT / "core" / "scheduler" / "triggers" / "hidden_state_decay.py",
        ]
        for fpath in maintenance_files:
            if not fpath.exists():
                continue
            src = fpath.read_text(encoding="utf-8")
            assert "legacy_tick_should_send" not in src, (
                f"Maintenance file {fpath.name} must not call legacy_tick_should_send"
            )


# ─────────────────────────────────────────────────────────────────────────────
# 19. execution 层不重新做 winner/block 决策
# ─────────────────────────────────────────────────────────────────────────────

class TestExecutionLayerDecisionFree:
    """_pipeline_send is execution-only: no active_window or DND logic inside."""

    def test_pipeline_send_has_no_active_window_check(self):
        """_pipeline_send source must not define active_window decision helpers."""
        loop_src = (ROOT / "core" / "scheduler" / "loop.py").read_text(encoding="utf-8")
        # These helpers were deleted in R2-C; they must not be re-defined as functions.
        # (A reference in a comment like "# _legacy_active_window_blocks 已删除" is OK.)
        forbidden_defs = [
            "def _legacy_active_window_blocks",
            "def _legacy_dnd_blocks",
        ]
        for pat in forbidden_defs:
            assert pat not in loop_src, (
                f"_pipeline_send must not define '{pat}' — execution layer must not re-gate"
            )

    def test_pipeline_send_has_no_dnd_filter(self):
        """_pipeline_send must not call dnd.is_dnd directly."""
        loop_src = (ROOT / "core" / "scheduler" / "loop.py").read_text(encoding="utf-8")
        # Only deferred import in _decide (via gating) is allowed; loop.py must not directly
        # call is_dnd inside _pipeline_send.
        # We check that 'is_dnd' does not appear in loop.py at all (R2-C contract).
        assert "is_dnd" not in loop_src, (
            "loop.py must not call is_dnd — DND decisions belong in gating._decide()"
        )

    def test_execute_prompt_only_marks_on_sent(self):
        """execute_prompt calls _mark only after sent=True, never for blocked/failed sends."""
        exec_src = (ROOT / "core" / "scheduler" / "execution.py").read_text(encoding="utf-8")
        # Confirm the pattern: marks are called inside the sent=True branch
        assert "for name in result.would_mark:" in exec_src
        # Confirm blocked path writes to log but does not call mark
        assert "write_execute_blocked" in exec_src

    def test_execute_prompt_blocked_does_not_mark(self):
        """execute_prompt returns sent=False when pipeline returns None; marks are not called."""
        import asyncio

        async def _run():
            import core.scheduler.execution as _exec
            from core.scheduler import loop as _loop

            marked = []
            original_mark = _loop._mark

            async def _fake_pipeline_send(*args, **kwargs):
                return None  # simulate blocked/failed send

            import unittest.mock as mock
            with mock.patch.object(_loop, "_pipeline_send", _fake_pipeline_send):
                with mock.patch.object(_loop, "_mark", lambda name: marked.append(name)):
                    result = await _exec.execute_prompt(
                        trigger_name="morning_greeting",
                        prompt_factory=lambda: "hello",
                        dry_run=False,
                        would_mark=["morning_greeting"],
                    )
            assert result.sent is False
            assert not marked, "_mark must not be called when send returns None"

        asyncio.get_event_loop().run_until_complete(_run())


# ─────────────────────────────────────────────────────────────────────────────
# 20. policy.py 被 gating runtime 路径使用
# ─────────────────────────────────────────────────────────────────────────────

class TestPolicyRuntimeWiring:
    """policy.py is imported and used by gating at runtime decision time."""

    def test_gating_imports_policy(self):
        """gating.py contains deferred import of core.scheduler.policy."""
        gating_src = (ROOT / "core" / "scheduler" / "gating.py").read_text(encoding="utf-8")
        assert "core.scheduler.policy" in gating_src

    def test_policy_table_used_in_active_window_decision(self, monkeypatch):
        """_policy_active_window_behavior reads from POLICY_TABLE at runtime."""
        from core.scheduler.gating import _policy_active_window_behavior
        from core.scheduler.policy import POLICY_TABLE

        for trigger_name, policy in POLICY_TABLE.items():
            result = _policy_active_window_behavior(trigger_name)
            assert result == policy.active_window_behavior, (
                f"{trigger_name}: gating returned {result!r}, "
                f"expected {policy.active_window_behavior!r}"
            )

    def test_policy_table_emergency_check_used_in_dnd_decision(self):
        """_policy_is_emergency correctly reflects POLICY_TABLE.priority."""
        from core.scheduler.gating import _policy_is_emergency
        from core.scheduler.policy import POLICY_TABLE

        for trigger_name, policy in POLICY_TABLE.items():
            result = _policy_is_emergency(trigger_name)
            expected = policy.priority == "emergency"
            assert result is expected, (
                f"{trigger_name}: _policy_is_emergency returned {result}, expected {expected}"
            )

    def test_loop_does_not_import_policy_directly(self):
        """loop.py accesses policy only through gating (R2-C contract)."""
        loop_src = (ROOT / "core" / "scheduler" / "loop.py").read_text(encoding="utf-8")
        assert "core.scheduler.policy" not in loop_src

    def test_loop_imports_gating(self):
        """loop.py imports core.scheduler.gating to run shadow tick."""
        loop_src = (ROOT / "core" / "scheduler" / "loop.py").read_text(encoding="utf-8")
        assert "core.scheduler.gating" in loop_src


# ─────────────────────────────────────────────────────────────────────────────
# 21. defer 触发器 block 时不 mark（execute_prompt 路径）
# ─────────────────────────────────────────────────────────────────────────────

class TestDeferNoMark:
    """When active_window or DND blocks a trigger, _mark is not called (execute_prompt path)."""

    def test_execute_prompt_not_called_when_gating_blocks(self, monkeypatch):
        """When gating returns None (blocked), run_shadow_tick does not call execute."""
        import asyncio
        import core.scheduler.gating as _gating

        execute_calls = []

        async def _fake_execute(*, dry_run):
            execute_calls.append(dry_run)
            from core.scheduler.execution import ExecuteResult
            return ExecuteResult(
                trigger_name="hr_high",
                would_send_prompt="test",
                dry_run=dry_run,
                sent=False,
            )

        from core.scheduler.gating import TriggerProposal, WATCH_EVENT_DRIVEN_TRIGGERS
        from core.scheduler.state_machine import TriggerState

        proposal = TriggerProposal(
            trigger_name="hr_high",
            urgency=0.5,
            topic_source="test",
            requires_state=[TriggerState.QUIET],
            execute=_fake_execute,
        )

        # Patch everything so gating returns None (active_window_filtered)
        import core.scheduler.loop as _loop
        import core.scheduler.triggers.dnd as _dnd

        monkeypatch.setattr(_loop, "_user_active_recently", lambda *a, **kw: True)
        monkeypatch.setattr(_dnd, "is_dnd", lambda uid: False)
        monkeypatch.setattr("core.scheduler.gating.get_current_state", lambda uid: TriggerState.QUIET)
        monkeypatch.setattr("core.scheduler.gating.is_trigger_ready", lambda name: True)
        monkeypatch.setattr(
            "core.scheduler.gating._collect_native_proposals", lambda ctx: [proposal]
        )
        # Also patch write functions to avoid filesystem I/O
        monkeypatch.setattr("core.scheduler.gating.safe_append_jsonl", lambda *a, **kw: None)
        monkeypatch.setattr("core.scheduler.gating.rotate_jsonl_if_needed", lambda *a, **kw: None)
        monkeypatch.setattr("core.scheduler.gating.get_paths", lambda: SimpleNamespace(gating_shadow_log=lambda: pathlib.Path("/dev/null")))

        async def _run():
            # write_shadow_tick calls _decide (which blocks hr_high) then checks execute
            from core.scheduler.gating import write_shadow_tick
            write_shadow_tick("u1")

        asyncio.get_event_loop().run_until_complete(_run())
        assert not execute_calls, "execute must not be called when gating blocks the trigger"


# ─────────────────────────────────────────────────────────────────────────────
# 22. main.py DND 接线审计
# ─────────────────────────────────────────────────────────────────────────────

class TestMainDNDWiring:
    """Structural audit: main.py calls detect_and_set in the owner message path."""

    def test_main_py_calls_detect_and_set(self):
        """main.py imports and calls dnd.detect_and_set (R2-D DND wiring)."""
        main_src = (ROOT / "main.py").read_text(encoding="utf-8")
        assert "detect_and_set" in main_src, (
            "main.py must call dnd.detect_and_set for DND wiring (R2-D)"
        )

    def test_main_py_dnd_in_owner_block(self):
        """detect_and_set call in main.py is inside the owner_id check block."""
        main_src = (ROOT / "main.py").read_text(encoding="utf-8")
        # The call must appear after the owner_id == user_id check
        owner_check_pos = main_src.find("str(user_id) == owner_id")
        detect_pos = main_src.find("detect_and_set")
        assert owner_check_pos != -1, "owner_id check must be present in main.py"
        assert detect_pos != -1, "detect_and_set must be present in main.py"
        assert detect_pos > owner_check_pos, (
            "detect_and_set call must appear AFTER the owner_id check"
        )

    def test_main_py_dnd_imports_from_dnd_module(self):
        """main.py imports detect_and_set from core.scheduler.triggers.dnd."""
        main_src = (ROOT / "main.py").read_text(encoding="utf-8")
        assert "core.scheduler.triggers.dnd" in main_src, (
            "main.py must import from core.scheduler.triggers.dnd"
        )

    def test_dnd_module_detect_and_set_exists(self):
        """core/scheduler/triggers/dnd.py exports detect_and_set function."""
        from core.scheduler.triggers.dnd import detect_and_set
        assert callable(detect_and_set)

    def test_dnd_module_is_dnd_exists(self):
        """core/scheduler/triggers/dnd.py exports is_dnd function."""
        from core.scheduler.triggers.dnd import is_dnd
        assert callable(is_dnd)


# ─────────────────────────────────────────────────────────────────────────────
# Additional: defer queue observable in shadow log (candidate fields)
# ─────────────────────────────────────────────────────────────────────────────

class TestCandidateSerializationR2D:
    """R2-D adds force_send and deferred_age_secs fields to candidate serialization."""

    def test_candidate_has_r2d_fields(self, monkeypatch):
        """Serialized candidate includes R2-D fields: force_send, deferred_age_secs."""
        from core.scheduler.gating import _decide

        _patch_decide_env(monkeypatch, user_active=False, dnd_active=False)
        proposals = [_make_proposal("morning_greeting")]
        _, _, candidates = _decide("u1", proposals)

        assert len(candidates) == 1
        c = candidates[0]
        assert "force_send" in c, "candidate must have force_send field"
        assert "deferred_age_secs" in c, "candidate must have deferred_age_secs field"

    def test_force_send_false_by_default(self, monkeypatch):
        """Non-expired trigger has force_send=False in candidate."""
        from core.scheduler.gating import _decide

        _patch_decide_env(monkeypatch, user_active=False, dnd_active=False)
        proposals = [_make_proposal("hr_high")]
        _, _, candidates = _decide("u1", proposals)
        assert candidates[0]["force_send"] is False
