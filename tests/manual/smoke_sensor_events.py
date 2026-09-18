"""
Smoke test for core.scheduler.sensor_events
Run from project root: python tests/manual/smoke_sensor_events.py

场景 A — 无 sensor 数据，tick() 应返回 []
场景 B — Code.exe active + 高键击率，模拟 26min → LONG_FOCUS
场景 C — Code.exe → chrome.exe → APP_CATEGORY_CHANGED
场景 D — idle 从 10 跳到 400 → PRESENCE_LEFT；再变回 → PRESENCE_RETURNED
场景 E — 相同事件在 cooldown 内不重复触发
"""

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

# Monkey-patch activity_manager.get_current 在 sensor_events 被引入前生效，
# 避免 sandbox / YAML 文件依赖。
import unittest.mock as mock
import core.activity_manager as _am
_am.get_current = mock.Mock(return_value={"current": "在读书"})

from core.memory import realtime_state
import core.scheduler.sensor_events as se


# ── 工具函数 ─────────────────────────────────────────────────────────────────

def _snap(app="Code.exe", title="ChatPanel.tsx", idle=10,
          keystrokes=60, window_secs=30, switch_count=0) -> dict:
    """构造一个合法的 realtime snapshot dict，received_at = now。"""
    now = time.time()
    return {
        "window_seconds": window_secs,
        "ts":             now,
        "sensor_version": "1.0",
        "received_at":    now,
        "input": {
            "keystrokes":        keystrokes,
            "mouse_clicks":      5,
            "mouse_distance_px": 500,
            "idle_seconds":      idle,
        },
        "focus": {
            "app":          app,
            "title_hint":   title,
            "switch_count": switch_count,
        },
    }


def _reset():
    """各场景前重置 sensor_events 和 realtime_state 的所有模块级状态。"""
    se._cooldowns.clear()
    se._last_presence            = None
    se._last_presence_changed_at = None
    se._last_app                 = None
    se._last_app_category        = None
    se._last_chat_at             = None
    se._focus_window_in_app_started_at = None
    se._focus_window_in_app_name       = None
    se._recent_switch_events.clear()
    realtime_state._snapshot                  = None
    realtime_state._continuous_at_desk_seconds = 0


def ok(cond: bool, label: str):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")


def types_of(events):
    return [e["type"] for e in events]


# ─────────────────────────────────────────────────────────────────────────────
print("\n=== 场景 A: 无 sensor 数据 ===")
_reset()

result = se.tick()
print(f"  tick() → {result}")
ok(result == [], "无 snapshot 时返回 []")


# ─────────────────────────────────────────────────────────────────────────────
print("\n=== 场景 B: Code.exe 高键击率持续 26 分钟 → LONG_FOCUS ===")
_reset()

# tick #1: 初始化状态。keystrokes=60, window=30 → rate=2.0 (密集)
realtime_state._snapshot = _snap(app="Code.exe", keystrokes=60, window_secs=30)
r1 = se.tick()
print(f"  tick #1 (初始化) → {types_of(r1)}")
ok("LONG_FOCUS" not in types_of(r1), "tick#1 不触发 LONG_FOCUS（时间窗口刚开始）")

# 手动把聚焦窗口起点拨到 26 分钟前
se._focus_window_in_app_started_at = time.time() - 26 * 60

# tick #2: 同 app，窗口 ≥25min + 密集键击 → 触发
realtime_state._snapshot = _snap(app="Code.exe", keystrokes=60, window_secs=30)
r2 = se.tick()
print(f"  tick #2 (窗口=26min) → {types_of(r2)}")
for e in r2:
    if e["type"] == "LONG_FOCUS":
        print(f"  narrative : {e['narrative']}")
        print(f"  focus_app : {e['context']['focus_app']}")
        print(f"  density   : {e['context']['keystroke_density']}")
ok("LONG_FOCUS" in types_of(r2), "tick#2 应触发 LONG_FOCUS")


# ─────────────────────────────────────────────────────────────────────────────
print("\n=== 场景 C: Code.exe → chrome.exe → APP_CATEGORY_CHANGED ===")
_reset()

realtime_state._snapshot = _snap(app="Code.exe")
se.tick()
print(f"  tick #1 app=Code.exe → {types_of(se.tick.__wrapped__() if hasattr(se.tick, '__wrapped__') else [])}")

realtime_state._snapshot = _snap(app="chrome.exe")
rc = se.tick()
print(f"  tick #2 app=chrome.exe → {types_of(rc)}")
for e in rc:
    if e["type"] == "APP_CATEGORY_CHANGED":
        print(f"  narrative : {e['narrative']}")
ok("APP_CATEGORY_CHANGED" in types_of(rc), "切换 work→leisure 应触发 APP_CATEGORY_CHANGED")


# ─────────────────────────────────────────────────────────────────────────────
print("\n=== 场景 D: PRESENCE_LEFT → PRESENCE_RETURNED ===")
_reset()

# tick #1: idle=10 (active)，初始化 _last_presence = "active"
realtime_state._snapshot = _snap(idle=10)
se.tick()
# 模拟已在 active 状态 11 分钟
se._last_presence_changed_at = time.time() - 11 * 60
print(f"  tick #1 idle=10 → presence=active，手动置 +11min")

# tick #2: idle=400 (away) → PRESENCE_LEFT
realtime_state._snapshot = _snap(idle=400)
rd1 = se.tick()
print(f"  tick #2 idle=400 → {types_of(rd1)}")
for e in rd1:
    if e["type"] == "PRESENCE_LEFT":
        print(f"  narrative : {e['narrative']}")
ok("PRESENCE_LEFT" in types_of(rd1), "idle≥300 且在 active 11min → PRESENCE_LEFT")

# 模拟已在 away 状态 6 分钟
se._last_presence_changed_at = time.time() - 6 * 60

# tick #3: idle=10 (active) → PRESENCE_RETURNED
realtime_state._snapshot = _snap(idle=10)
rd2 = se.tick()
print(f"  tick #3 idle=10 → {types_of(rd2)}")
for e in rd2:
    if e["type"] == "PRESENCE_RETURNED":
        print(f"  narrative : {e['narrative']}")
ok("PRESENCE_RETURNED" in types_of(rd2), "idle<60 且在 away 6min → PRESENCE_RETURNED")


# ─────────────────────────────────────────────────────────────────────────────
print("\n=== 场景 E: cooldown 内相同事件不重复触发 ===")
_reset()

# Code.exe → chrome.exe：触发 APP_CATEGORY_CHANGED
realtime_state._snapshot = _snap(app="Code.exe")
se.tick()
realtime_state._snapshot = _snap(app="chrome.exe")
re1 = se.tick()
fired_first = "APP_CATEGORY_CHANGED" in types_of(re1)
print(f"  Code→chrome 首次触发 APP_CATEGORY_CHANGED = {fired_first}")

# cooldown 期间再切回 Code.exe
realtime_state._snapshot = _snap(app="Code.exe")
re2 = se.tick()
fired_second = "APP_CATEGORY_CHANGED" in types_of(re2)
print(f"  chrome→Code cooldown 内触发 APP_CATEGORY_CHANGED = {fired_second}")
ok(fired_first and not fired_second, "首次触发，cooldown 内不重复")


# ─────────────────────────────────────────────────────────────────────────────
print("\n=== Smoke 完成 ===\n")
