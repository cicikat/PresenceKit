"""tests/test_birthday_ledger_exempt.py — 生日系列触发器不被 ProactiveLedger 顶掉（补遗审计）

生日窗口极窄（零点告白只有 00:00-00:05），一旦被全局最小间隔/每日预算挡下就是一整年的错过。
policy.py 给四个生日触发器加了 ledger_exempt=True：豁免 ledger 这道闸，但刻意不豁免 DND
（priority 仍是 high/normal，不是 emergency）。这里直接测 gating._decide 的行为，
不依赖真实 ProactiveLedger 状态。
"""
from core.scheduler.gating import TriggerProposal, _decide
from core.scheduler.state_machine import TriggerState


def _proposal(trigger_name: str, urgency: float = 1.0) -> TriggerProposal:
    return TriggerProposal(
        trigger_name=trigger_name,
        urgency=urgency,
        topic_source="test",
        requires_state=[TriggerState.QUIET],
        bypass_state_machine=True,
    )


def _patch_env(monkeypatch, *, user_active: bool = False, dnd_active: bool = False):
    import core.scheduler.loop as _loop
    import core.scheduler.triggers.dnd as _dnd

    monkeypatch.setattr(_loop, "_user_active_recently", lambda: user_active)
    monkeypatch.setattr(_dnd, "is_dnd", lambda uid: dnd_active)
    monkeypatch.setattr("core.scheduler.gating.get_current_state", lambda uid: TriggerState.QUIET)
    monkeypatch.setattr("core.scheduler.gating.is_trigger_ready", lambda name: True)


def _patch_ledger_gap_exhausted(monkeypatch):
    """模拟"全局间隔未到"：normal 优先级一律拒绝，emergency 优先级放行（真实 ledger 语义）。"""
    def fake_can_send(trigger_name, *, priority="normal"):
        if priority == "emergency":
            return True, "emergency_exempt"
        return False, "gap_not_elapsed"

    monkeypatch.setattr("core.scheduler.proactive_ledger.can_send", fake_can_send)


def test_random_message_is_crowded_out_by_ledger_gap(monkeypatch):
    """对照组：非豁免触发器在全局间隔未到时应被顶掉。"""
    _patch_env(monkeypatch)
    _patch_ledger_gap_exhausted(monkeypatch)
    picked, reason, _ = _decide("u1", [_proposal("random_message")])
    assert picked is None
    assert reason == "global_gap_filtered"


def test_birthday_midnight_bypasses_ledger_gap(monkeypatch):
    _patch_env(monkeypatch)
    _patch_ledger_gap_exhausted(monkeypatch)
    picked, reason, _ = _decide("u1", [_proposal("birthday_midnight")])
    assert picked is not None
    assert picked.trigger_name == "birthday_midnight"
    assert reason == "picked_highest_urgency"


def test_birthday_eve_afternoon_night_all_bypass_ledger_gap(monkeypatch):
    _patch_env(monkeypatch)
    _patch_ledger_gap_exhausted(monkeypatch)
    for name in ("birthday_eve", "birthday_afternoon", "birthday_night"):
        picked, reason, _ = _decide("u1", [_proposal(name)])
        assert picked is not None and picked.trigger_name == name, name
        assert reason == "picked_highest_urgency", name


def test_birthday_midnight_still_blocked_by_dnd(monkeypatch):
    """ledger_exempt 不等于 emergency：免打扰时生日消息依旧被拦（不像 hr_critical 那样穿透 DND）。"""
    _patch_env(monkeypatch, dnd_active=True)
    picked, reason, _ = _decide("u1", [_proposal("birthday_midnight")])
    assert picked is None
    assert reason == "dnd_filtered"


def test_birthday_picked_over_normal_trigger_when_both_pass_ledger(monkeypatch):
    """生日豁免 ledger、random_message 未豁免且被拒时，生日单独入选。"""
    _patch_env(monkeypatch)
    _patch_ledger_gap_exhausted(monkeypatch)
    picked, reason, _ = _decide("u1", [_proposal("random_message", urgency=0.9), _proposal("birthday_night")])
    assert picked is not None
    assert picked.trigger_name == "birthday_night"
