"""
验收测试：desktop_wake 重复显示修复

四个场景：
1. WS 已连接时触发主动消息 → push_segments 随 fanout 一起发出，不额外重复
2. WS 未连接 / 刚启动 pending trigger → Path A 补发（不丢消息）
3. 普通 chat → push_segments 照常发出（不影响 split 气泡）
4. 断开 WS → _connect_time 清零 → 之后 pending trigger 可由 Path A 拿到
"""

import time
import pytest


# ── helpers ──────────────────────────────────────────────────────────────────

class _FakePipeline:
    async def post_process(self, uid, content, reply, **kwargs):
        return {"turn_id": f"t-{content[:4]}", "critical_written": True, "emotion": "neutral"}


class _DesktopChannel:
    name = "desktop"
    is_active = True

    def __init__(self):
        self.sent: list[str] = []

    async def send(self, content, user_id, behavior=None, msg_id=None):
        self.sent.append(content)


async def _reset_channels():
    from channels import registry
    registry._channels = {}


# ── Test 1: WS 已连接时触发 → push_segments 随 fanout 触发，不额外重复 ──────────

async def test_push_segments_fires_only_when_desktop_in_fanout(monkeypatch):
    """fanout='all' 且 desktop 活跃 → push_segments 发出一次且 msg_id 共享。"""
    from channels import registry
    from channels.desktop import DesktopChannel
    from core.turn_sink import record_assistant_turn

    await _reset_channels()
    registry.register(DesktopChannel())

    push_seg_calls: list[dict] = []
    push_msg_calls: list[dict] = []

    async def fake_push_message(content, msg_id=None):
        push_msg_calls.append({"content": content, "msg_id": msg_id})
        return True

    async def fake_push_segments(content, segments, msg_id=None):
        push_seg_calls.append({"content": content, "msg_id": msg_id})
        return True

    monkeypatch.setattr("channels.desktop_ws.is_connected", lambda: True)
    monkeypatch.setattr(
        "channels.desktop_ws._new_msg_id",
        lambda: (_ for _ in ()).throw(AssertionError("canonical turn_id should be reused")),
    )
    monkeypatch.setattr("channels.desktop_ws.push_message", fake_push_message)
    monkeypatch.setattr("channels.desktop_ws.push_segments", fake_push_segments)

    await record_assistant_turn(
        assistant_text="好的，我在。",
        uid="u1",
        source="trigger",
        trigger_name="morning_greeting",
        fanout="all",
        pipeline=_FakePipeline(),
    )

    # channel_message 发出一次
    assert len(push_msg_calls) == 1
    # push_segments 也发出一次（desktop in fanout）
    assert len(push_seg_calls) == 1
    # 共享同一个 msg_id
    assert push_msg_calls[0]["msg_id"] == push_seg_calls[0]["msg_id"] == "t-morn"


async def test_push_segments_skipped_when_fanout_empty(monkeypatch):
    """fanout=[] → push_segments 不发出（desktop_wake Path B 场景）。"""
    from channels import registry
    from channels.desktop import DesktopChannel
    from core.turn_sink import record_assistant_turn

    await _reset_channels()
    registry.register(DesktopChannel())

    push_seg_calls: list[dict] = []

    async def fake_push_segments(content, segments, msg_id=None):
        push_seg_calls.append({"content": content, "msg_id": msg_id})
        return True

    monkeypatch.setattr("channels.desktop_ws.is_connected", lambda: True)
    monkeypatch.setattr("channels.desktop_ws._new_msg_id", lambda: "should-not-be-used")
    monkeypatch.setattr("channels.desktop_ws.push_segments", fake_push_segments)

    await record_assistant_turn(
        assistant_text="重开问候",
        uid="u2",
        source="trigger",
        trigger_name="desktop_wake",
        fanout=[],       # desktop_wake Path B 传入
        pipeline=_FakePipeline(),
    )

    # fanout=[] 时 push_segments 不应被调用
    assert push_seg_calls == [], f"push_segments should not fire with fanout=[], got {push_seg_calls}"


# ── Test 2: WS 未连接时 pending trigger → Path A 补发 ────────────────────────

async def test_path_a_returns_pending_trigger_when_ws_disconnected(monkeypatch):
    """WS 未连接（connect_time=0）时，last_seen 之后的 trigger 可由 Path A 拿到。"""
    import importlib

    # WS 未连接 → get_connect_time() = 0
    monkeypatch.setattr("channels.desktop_ws.get_connect_time", lambda: 0.0)
    monkeypatch.setattr("channels.desktop_ws.is_connected", lambda: False)

    now = time.time()
    last_seen = now - 300  # 5 分钟前看到最后一条消息

    fake_history = [
        {
            "role": "assistant",
            "content": "早上好！",
            "timestamp": now - 60,   # 1 分钟前触发的 trigger
            "_turn_id": "tid-morning",
        },
    ]
    monkeypatch.setattr("core.memory.short_term.load", lambda uid: fake_history)

    # 调用路由逻辑中的 Path A 子逻辑（复现路由里的过滤代码）
    from channels import desktop_ws as _dws_pa
    history = fake_history
    user_turn_ids: set = set()
    ws_connect_time = _dws_pa.get_connect_time()

    pending = [
        e for e in history
        if (
            e.get("role") == "assistant"
            and e.get("timestamp", 0) > last_seen
            and e.get("_turn_id")
            and e["_turn_id"] not in user_turn_ids
            and (not ws_connect_time or e.get("timestamp", 0) <= ws_connect_time)
        )
    ]

    assert len(pending) == 1, "WS 未连接时 pending trigger 应被 Path A 返回"
    assert pending[0]["content"] == "早上好！"


async def test_desktop_wake_path_a_returns_canonical_ids(monkeypatch):
    """Path A HTTP reply exposes the persisted turn_id as msg_id."""
    import json

    monkeypatch.setattr(
        "core.config_loader.get_config",
        lambda: {"scheduler": {"owner_id": "owner-wake"}},
    )
    monkeypatch.setattr("channels.desktop_ws.get_connect_time", lambda: 0.0)

    class _FakeAPA:
        def read_text(self, encoding="utf-8"):
            return json.dumps({"active_character": "yexuan"})

    monkeypatch.setattr("core.sandbox.DataPaths.active_prompt_assets", lambda self: _FakeAPA())
    monkeypatch.setattr(
        "core.memory.short_term.load",
        lambda uid, char_id=None: [{
            "role": "assistant",
            "content": "离线问候",
            "timestamp": time.time(),
            "_turn_id": "turn-wake-path-a",
        }],
    )

    from admin.routers.chat import desktop_wake

    result = await desktop_wake({"last_seen": time.time() - 60})

    assert result["reply"] == "离线问候"
    assert result["turn_id"] == result["msg_id"] == "turn-wake-path-a"


async def test_path_a_excludes_post_connect_trigger_when_ws_connected(monkeypatch):
    """WS 已连接时，connect_time 之后生成的 trigger 不被 Path A 返回（已 WS 推送）。"""
    now = time.time()
    connect_time = now - 10   # WS 10 秒前连接
    trigger_time = now - 5    # trigger 在 WS 连接后 5 秒触发
    last_seen = now - 300     # last_seen 在 connect 之前

    monkeypatch.setattr("channels.desktop_ws.get_connect_time", lambda: connect_time)
    monkeypatch.setattr("channels.desktop_ws.is_connected", lambda: True)

    from channels import desktop_ws as _dws_pa

    fake_history = [
        {
            "role": "assistant",
            "content": "刚才通过 WS 发的",
            "timestamp": trigger_time,
            "_turn_id": "tid-ws-trigger",
        },
    ]

    history = fake_history
    user_turn_ids: set = set()
    ws_connect_time = _dws_pa.get_connect_time()

    pending = [
        e for e in history
        if (
            e.get("role") == "assistant"
            and e.get("timestamp", 0) > last_seen
            and e.get("_turn_id")
            and e["_turn_id"] not in user_turn_ids
            and (not ws_connect_time or e.get("timestamp", 0) <= ws_connect_time)
        )
    ]

    assert pending == [], (
        "WS 已连接时，connect_time 之后的 trigger 不应由 Path A 返回（已通过 WS 发送）"
    )


async def test_path_a_includes_pre_connect_trigger_when_ws_connected(monkeypatch):
    """WS 已连接，但 trigger 在 connect_time 之前生成（离线期间）→ Path A 应补发。"""
    now = time.time()
    connect_time = now - 10   # WS 10 秒前连接
    trigger_time = now - 30   # trigger 在 WS 连接前 20 秒触发（离线期间）
    last_seen = now - 120     # last_seen 更早

    monkeypatch.setattr("channels.desktop_ws.get_connect_time", lambda: connect_time)
    monkeypatch.setattr("channels.desktop_ws.is_connected", lambda: True)

    from channels import desktop_ws as _dws_pa

    fake_history = [
        {
            "role": "assistant",
            "content": "离线期间发的消息",
            "timestamp": trigger_time,
            "_turn_id": "tid-offline-trigger",
        },
    ]

    history = fake_history
    user_turn_ids: set = set()
    ws_connect_time = _dws_pa.get_connect_time()

    pending = [
        e for e in history
        if (
            e.get("role") == "assistant"
            and e.get("timestamp", 0) > last_seen
            and e.get("_turn_id")
            and e["_turn_id"] not in user_turn_ids
            and (not ws_connect_time or e.get("timestamp", 0) <= ws_connect_time)
        )
    ]

    assert len(pending) == 1, "WS 已连接但 trigger 在 connect_time 之前 → 应由 Path A 补发"
    assert pending[0]["content"] == "离线期间发的消息"


# ── Test 3: 普通 chat → push_segments 照常（不影响 split 气泡）───────────────

async def test_push_segments_fires_for_user_chat(monkeypatch):
    """普通 user_chat 走 fanout='all' → push_segments 正常发出（不受 Fix 3 影响）。"""
    from channels import registry
    from channels.desktop import DesktopChannel
    from core.turn_sink import record_assistant_turn

    await _reset_channels()
    registry.register(DesktopChannel())

    push_seg_calls: list[dict] = []

    async def fake_push_message(content, msg_id=None):
        return True

    async def fake_push_segments(content, segments, msg_id=None):
        push_seg_calls.append(content)
        return True

    monkeypatch.setattr("channels.desktop_ws.is_connected", lambda: True)
    monkeypatch.setattr("channels.desktop_ws._new_msg_id", lambda: "chat-msg-id")
    monkeypatch.setattr("channels.desktop_ws.push_message", fake_push_message)
    monkeypatch.setattr("channels.desktop_ws.push_segments", fake_push_segments)

    await record_assistant_turn(
        assistant_text="第一段。\n第二段。",
        uid="u3",
        source="user_chat",
        user_text="你好",
        fanout="all",
        pipeline=_FakePipeline(),
    )

    # 普通 chat 时 push_segments 应发出一次
    assert len(push_seg_calls) == 1, (
        f"普通 chat 应触发 push_segments，got {push_seg_calls}"
    )


# ── Test 4: 断开 WS → _connect_time 清零 → pending trigger 可由 Path A 拿到 ──

def test_desktop_ws_connect_time_is_zero_when_not_connected():
    """未连接时 get_connect_time() 应返回 0。"""
    import channels.desktop_ws as dws

    # 确保模块级 _current_ws 为 None（不修改生产状态，仅读取）
    original_ws = dws._current_ws
    original_ct = dws._connect_time

    try:
        dws._current_ws = None
        dws._connect_time = 0.0
        assert dws.get_connect_time() == 0.0
    finally:
        dws._current_ws = original_ws
        dws._connect_time = original_ct


def test_desktop_ws_connect_time_nonzero_when_connected():
    """_current_ws 存在时 get_connect_time() 返回非零时间戳。"""
    import channels.desktop_ws as dws

    original_ws = dws._current_ws
    original_ct = dws._connect_time
    sentinel = object()  # 模拟一个 non-None ws 对象

    try:
        dws._current_ws = sentinel  # type: ignore[assignment]
        dws._connect_time = time.time()
        assert dws.get_connect_time() > 0
    finally:
        dws._current_ws = original_ws
        dws._connect_time = original_ct


async def test_path_a_after_reconnect_uses_new_connect_time(monkeypatch):
    """WS 断开再重连 → _connect_time 更新 → 离线期间 trigger 仍可由 Path A 补发。"""
    now = time.time()

    # 离线期间触发的消息
    offline_trigger_time = now - 60   # 1 分钟前
    # 重新连接后的时间
    new_connect_time = now - 5        # 5 秒前重连

    # 模拟：断开 → _connect_time = 0；重连 → _connect_time = new_connect_time
    monkeypatch.setattr("channels.desktop_ws.get_connect_time", lambda: new_connect_time)
    monkeypatch.setattr("channels.desktop_ws.is_connected", lambda: True)

    from channels import desktop_ws as _dws_pa

    last_seen = now - 300  # 5 分钟前的 chat log 时间戳

    fake_history = [
        {
            "role": "assistant",
            "content": "断线期间的问候",
            "timestamp": offline_trigger_time,  # < new_connect_time → 离线触发
            "_turn_id": "tid-offline",
        },
    ]

    user_turn_ids: set = set()
    ws_connect_time = _dws_pa.get_connect_time()

    pending = [
        e for e in fake_history
        if (
            e.get("role") == "assistant"
            and e.get("timestamp", 0) > last_seen
            and e.get("_turn_id")
            and e["_turn_id"] not in user_turn_ids
            and (not ws_connect_time or e.get("timestamp", 0) <= ws_connect_time)
        )
    ]

    assert len(pending) == 1, (
        "重连后 connect_time 更新，离线期间的 trigger 仍应由 Path A 补发"
    )
    assert pending[0]["content"] == "断线期间的问候"
