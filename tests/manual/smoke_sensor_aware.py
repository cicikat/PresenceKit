"""
Smoke test for core.scheduler.triggers.sensor_aware (signal-first)
Run from project root: python -X utf8 tests/manual/smoke_sensor_aware.py

场景 A — sensor_events.tick() 返回 []
          → handle_tick() 静默返回，不入 signal、不调 _pipeline_send

场景 B — tick 返回一个事件，judge 返回低于开口阈值
          → 不入 signal、不调 _pipeline_send

场景 C — tick 返回一个事件，judge 返回可开口分数
          → emit_trigger_signal 一次，不调 _pipeline_send

场景 D — tick 返回两个事件，judge 给出 score=40 和 75
          → 选 score=75 那个入 signal，40 那个不进 evidence
"""
import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import core.scheduler.triggers.sensor_aware as sa


_EVENT_MEDIUM = {
    "type": "LONG_FOCUS",
    "narrative": "她已经连续工作了 26 分钟。",
    "context": {
        "local_hour": 15,
        "presence": "active",
        "focus_app": "Code.exe",
        "focus_title_hint": "ChatPanel.tsx",
        "continuous_at_desk_seconds": 5400,
        "minutes_since_last_chat": 70,
        "keystroke_density": "一般",
    },
}

_JUDGE_MEDIUM = {"score": 65, "reason": "适合开口", "intent_tier": "medium"}
_JUDGE_DROP = {"score": 25, "reason": "无价值", "intent_tier": "drop"}


def ok(cond: bool, label: str):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")


async def scene_a():
    print("\n=== 场景 A: tick() 返回 [] → 静默返回，无副作用 ===")
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
    print("\n=== 场景 B: judge 低于开口阈值 → 不入 signal ===")
    emit = MagicMock()

    with (
        patch("core.scheduler.sensor_events.tick", return_value=[_EVENT_MEDIUM]),
        patch("core.scheduler.sensor_judge.judge", new_callable=AsyncMock, return_value=_JUDGE_DROP),
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


async def scene_c():
    print("\n=== 场景 C: 可开口分数 → 入 signal，不直发 ===")
    emit = MagicMock()

    with (
        patch("core.scheduler.sensor_events.tick", return_value=[_EVENT_MEDIUM]),
        patch("core.scheduler.sensor_judge.judge", new_callable=AsyncMock, return_value=_JUDGE_MEDIUM),
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
    ok(emit.call_count == 1, "emit_trigger_signal 被调用一次")
    if emit.call_count == 1:
        evidence = emit.call_args.kwargs.get("evidence") or emit.call_args[0][3]
        ok(evidence[0]["event_type"] == "LONG_FOCUS", 'evidence event_type="LONG_FOCUS"')
        ok(evidence[0]["fact"] == "sensor_candidate", 'evidence fact="sensor_candidate"')


async def scene_d():
    print("\n=== 场景 D: 两个事件 score=40/75 → 选 75 入 signal ===")

    event_low = {
        "type": "FOCUS_SCATTERED",
        "narrative": "她在 5 分钟内切换了 20 次窗口。",
        "context": {
            "local_hour": 14,
            "presence": "active",
            "focus_app": "chrome.exe",
            "focus_title_hint": "",
            "continuous_at_desk_seconds": 3700,
            "minutes_since_last_chat": 40,
            "keystroke_density": "稀疏",
        },
    }
    event_high = {
        "type": "LATE_NIGHT_ACTIVE",
        "narrative": "已经凌晨 2 点了，她还醒着。",
        "context": {
            "local_hour": 2,
            "presence": "active",
            "focus_app": "chrome.exe",
            "focus_title_hint": "",
            "continuous_at_desk_seconds": 7500,
            "minutes_since_last_chat": 8,
            "keystroke_density": "一般",
        },
    }

    judge_table = {
        "FOCUS_SCATTERED": {"score": 40, "reason": "普通", "intent_tier": "drop"},
        "LATE_NIGHT_ACTIVE": {"score": 75, "reason": "深夜活跃", "intent_tier": "strong"},
    }

    async def fake_judge(ev):
        return judge_table[ev["type"]]

    emit = MagicMock()

    with (
        patch("core.scheduler.sensor_events.tick", return_value=[event_low, event_high]),
        patch("core.scheduler.sensor_judge.judge", side_effect=fake_judge),
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
    ok(emit.call_count == 1, "emit_trigger_signal 被调用一次（不是两次）")
    if emit.call_count == 1:
        evidence = emit.call_args.kwargs.get("evidence") or emit.call_args[0][3]
        ok(evidence[0]["event_type"] == "LATE_NIGHT_ACTIVE", "选中高分 LATE_NIGHT_ACTIVE")
        ok(evidence[0]["event_type"] != "FOCUS_SCATTERED", "低分事件不进 evidence")


async def main():
    await scene_a()
    await scene_b()
    await scene_c()
    await scene_d()
    print("\n=== Smoke 完成 ===\n")


if __name__ == "__main__":
    asyncio.run(main())
