"""
Smoke test for core.scheduler.triggers.sensor_aware (signal-first + plan 映射)
Run from project root: python -X utf8 tests/manual/smoke_sensor_aware_v2.py

场景 A — tick() 返回 [] → 静默 return，不入 signal
场景 B — score=20 (< passive_speak 阈值 35) → BehaviorPlanner 返回 None，不入 signal
场景 C — LONG_FOCUS, score=40 → plan 为 passive_speak，入 signal，不直发
场景 D — LATE_NIGHT_ACTIVE, score=85 → plan 为 direct_act，入 signal，不构造 action
场景 E — PRESENCE_RETURNED, score=70 → 封顶 soft_hint，入 signal，不构造 pet_emote
"""
import asyncio
import os
import sys
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import core.scheduler.triggers.sensor_aware as sa


def _mk_event(event_type: str, **ctx_overrides) -> dict:
    ctx = {
        "local_hour": 15,
        "presence": "active",
        "focus_app": "Code.exe",
        "focus_title_hint": "ChatPanel.tsx",
        "continuous_at_desk_seconds": 5400,
        "minutes_since_last_chat": 70,
        "keystroke_density": "一般",
        **ctx_overrides,
    }
    narratives = {
        "LONG_FOCUS": "她已经连续工作了 26 分钟。",
        "LATE_NIGHT_ACTIVE": "已经凌晨 2 点了，她还醒着。",
        "PRESENCE_RETURNED": "她回来了，刚离开了 8 分钟。",
        "GENERIC": "她的状态发生了变化。",
    }
    return {
        "type": event_type,
        "narrative": narratives.get(event_type, narratives["GENERIC"]),
        "context": ctx,
    }


def _mk_judge(score: int) -> dict:
    def _tier(s: int) -> str:
        if s < 41:
            return "drop"
        if s <= 55:
            return "weak"
        if s <= 70:
            return "medium"
        if s <= 85:
            return "strong"
        return "must"
    return {"score": score, "reason": "smoke-test", "intent_tier": _tier(score)}


def ok(cond: bool, label: str):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")


def _eligible_stack(ev, score, emit):
    stack = ExitStack()
    stack.enter_context(patch("core.scheduler.sensor_events.tick", return_value=[ev]))
    stack.enter_context(
        patch("core.scheduler.sensor_judge.judge", new_callable=AsyncMock, return_value=_mk_judge(score))
    )
    stack.enter_context(patch("core.autonomy.signal_adapters.emit_trigger_signal", emit))
    mock_send = stack.enter_context(
        patch("core.scheduler.loop._pipeline_send", new_callable=AsyncMock)
    )
    stack.enter_context(patch("core.scheduler.proactive_ledger.can_send", return_value=(True, "ok")))
    stack.enter_context(patch("core.scheduler.triggers.dnd.is_dnd", return_value=False))
    stack.enter_context(patch.object(sa, "_owner_id", return_value="owner"))
    stack.enter_context(patch("core.scheduler.loop._owner_id", return_value="owner"))
    stack.enter_context(patch("core.scheduler.loop._active_char_id_or_none", return_value="char"))
    return stack, mock_send


async def scene_a():
    print("\n=== 场景 A: tick() 返回 [] → 静默 return ===")
    emit = MagicMock()

    with (
        patch("core.scheduler.sensor_events.tick", return_value=[]),
        patch("core.autonomy.signal_adapters.emit_trigger_signal", emit),
        patch("core.scheduler.loop._pipeline_send", new_callable=AsyncMock) as mock_send,
    ):
        await sa.handle_tick()

    ok(mock_send.call_count == 0, "_pipeline_send 未被调用")
    ok(emit.call_count == 0, "emit_trigger_signal 未被调用")


async def scene_b():
    print("\n=== 场景 B: score=20 < 35 → BehaviorPlanner.plan() 返回 None，不入 signal ===")
    ev = _mk_event("LONG_FOCUS")
    emit = MagicMock()

    with (
        patch("core.scheduler.sensor_events.tick", return_value=[ev]),
        patch("core.scheduler.sensor_judge.judge", new_callable=AsyncMock, return_value=_mk_judge(20)),
        patch("core.autonomy.signal_adapters.emit_trigger_signal", emit),
        patch("core.scheduler.loop._pipeline_send", new_callable=AsyncMock) as mock_send,
        patch("core.scheduler.proactive_ledger.can_send", return_value=(True, "ok")),
        patch("core.scheduler.triggers.dnd.is_dnd", return_value=False),
        patch.object(sa, "_owner_id", return_value="owner"),
        patch("core.scheduler.loop._owner_id", return_value="owner"),
        patch("core.scheduler.loop._active_char_id_or_none", return_value="char"),
    ):
        await sa.handle_tick()

    ok(mock_send.call_count == 0, "_pipeline_send 未被调用")
    ok(emit.call_count == 0, "emit_trigger_signal 未被调用")
    plan_result = sa.plan(ev, 20)
    ok(plan_result is None, "plan(score=20) 返回 None")


async def scene_c():
    print("\n=== 场景 C: LONG_FOCUS score=40 → passive_speak 入 signal，不直发 ===")
    ev = _mk_event("LONG_FOCUS")
    emit = MagicMock()

    stack, mock_send = _eligible_stack(ev, 40, emit)
    with stack:
        await sa.handle_tick()

    ok(mock_send.call_count == 0, "_pipeline_send 未被调用")
    ok(emit.call_count == 1, "emit_trigger_signal 被调用一次")
    behavior = sa.plan(ev, 40)
    ok(behavior is not None and behavior["level"] == "passive_speak",
       "plan(LONG_FOCUS, 40) → passive_speak")
    ok(behavior is not None and behavior["behavior_id"] == "casual_check_in",
       'behavior_id = "casual_check_in"')
    if emit.call_count == 1:
        evidence = emit.call_args.kwargs.get("evidence") or emit.call_args[0][3]
        ok(evidence[0].get("behavior_id") == "casual_check_in",
           "signal evidence 携带 casual_check_in")


async def scene_d():
    print("\n=== 场景 D: LATE_NIGHT_ACTIVE score=85 → direct_act 入 signal，不构造 action ===")
    ev = _mk_event(
        "LATE_NIGHT_ACTIVE",
        local_hour=2,
        continuous_at_desk_seconds=7500,
        minutes_since_last_chat=90,
    )
    emit = MagicMock()

    stack, mock_send = _eligible_stack(ev, 85, emit)
    with stack:
        await sa.handle_tick()

    ok(mock_send.call_count == 0, "_pipeline_send 未被调用")
    ok(emit.call_count == 1, "emit_trigger_signal 被调用一次")
    behavior = sa.plan(ev, 85)
    ok(behavior is not None and behavior["level"] == "direct_act",
       "plan(LATE_NIGHT_ACTIVE, 85) → direct_act")
    if emit.call_count == 1:
        evidence = emit.call_args.kwargs.get("evidence") or emit.call_args[0][3]
        ok(evidence[0].get("behavior_id") == "late_night_lock_hint",
           "signal evidence 携带 late_night_lock_hint，不执行 action")


async def scene_e():
    print("\n=== 场景 E: PRESENCE_RETURNED score=70 → 封顶 soft_hint，入 signal ===")
    ev = _mk_event("PRESENCE_RETURNED")
    emit = MagicMock()

    stack, _mock_send = _eligible_stack(ev, 70, emit)
    with stack:
        await sa.handle_tick()

    ok(emit.call_count == 1, "emit_trigger_signal 被调用一次")
    behavior = sa.plan(ev, 70)
    ok(behavior is not None and behavior["level"] == "soft_hint",
       "plan(PRESENCE_RETURNED, 70) → soft_hint（不升级到 attention_grab）")
    if emit.call_count == 1:
        evidence = emit.call_args.kwargs.get("evidence") or emit.call_args[0][3]
        ok(evidence[0].get("behavior_id") == "welcome_back_strong",
           "signal evidence 携带 welcome_back_strong，不构造 pet_emote")


async def main():
    await scene_a()
    await scene_b()
    await scene_c()
    await scene_d()
    await scene_e()
    print("\n=== Smoke 完成 ===\n")


if __name__ == "__main__":
    asyncio.run(main())
