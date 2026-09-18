from tests.fixtures.public_assets import TEST_CHAR_ID
"""phone_control_start 工具的门禁行为：安全模式拦截、危险模式下仍需二次确认。"""
import json
import time

import pytest

from core import tool_dispatcher


class _Session:
    WAITING_CONFIRM = "waiting_confirm"
    IDLE = "idle"

    def __init__(self):
        self.status = self.IDLE
        self.pending = None

    def set_waiting_confirm(self, tool_name, tool_args):
        self.status = self.WAITING_CONFIRM
        self.pending = (tool_name, tool_args)


def _write_mode(sandbox, mode, expires_at=None):
    path = sandbox.meta_mode()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"mode": mode, "expires_at": expires_at}), encoding="utf-8")


def test_phone_control_start_registered_as_dangerous_and_gated():
    spec = tool_dispatcher._TOOL_REGISTRY["phone_control_start"]
    assert spec["dangerous"] is True
    assert spec["category"] == "phone_control"
    assert "phone_control" in tool_dispatcher._MODE_RESTRICTED_CATEGORIES


@pytest.mark.asyncio
async def test_safe_mode_blocks_phone_control_with_phone_specific_message(sandbox, monkeypatch):
    monkeypatch.setattr(tool_dispatcher, "_is_tool_enabled", lambda _: True)
    result = await tool_dispatcher.execute_structured(
        "phone_control_start",
        {"task": "帮我点杯奶茶"},
        "u1",
        "u1",
        False,
        _Session(),
        origin="user_live",
        char_id=TEST_CHAR_ID,
    )
    assert "安全模式" in result.result
    assert "手机" in result.result
    assert "电脑" not in result.result
    assert result.confirmation_request is None


@pytest.mark.asyncio
async def test_danger_mode_still_asks_confirmation_before_executing(sandbox, monkeypatch):
    _write_mode(sandbox, "danger", time.time() + 60)
    monkeypatch.setattr(tool_dispatcher, "_is_tool_enabled", lambda _: True)
    session = _Session()

    # dangerous=True 的确认闸在 execute_structured() 里发生在读 tool_info["func"] 之前
    # （session_state.status != WAITING_CONFIRM 直接 return，不会真的调用 wrapper），
    # 所以这里不需要也不应该替身 wrapper——真替换了反而会污染 _TOOL_REGISTRY 全局状态。
    result = await tool_dispatcher.execute_structured(
        "phone_control_start",
        {"task": "帮我点杯奶茶"},
        "u1",
        "u1",
        False,
        session,
        origin="user_live",
        char_id=TEST_CHAR_ID,
    )
    # dangerous=True 工具第一次调用永远先要求确认，不管危险模式是否已开——两道闸独立，不能互相替代。
    assert result.result is None
    assert result.confirmation_request is not None
    assert "帮我点杯奶茶" in result.confirmation_request
    assert session.pending == ("phone_control_start", {"task": "帮我点杯奶茶"})


@pytest.mark.asyncio
async def test_wrapper_rejects_empty_task(sandbox):
    result = await tool_dispatcher._phone_control_start_wrapper("", user_id="u1", char_id=TEST_CHAR_ID)
    assert "没听清楚" in result or "没法" in result


@pytest.mark.asyncio
async def test_wrapper_starts_task_and_queues_mobile_behavior(sandbox, monkeypatch):
    from channels import registry
    from channels.mobile import MobileChannel

    registry._channels = {}
    mobile = MobileChannel()
    registry.register(mobile)

    result = await tool_dispatcher._phone_control_start_wrapper(
        "帮我点杯奶茶", user_id="u1", char_id=TEST_CHAR_ID,
    )
    assert "已经把任务派给手机了" in result

    queue = json.loads(sandbox.mobile_queue().read_text(encoding="utf-8"))
    assert len(queue) == 1
    assert queue[0]["behavior"]["behavior_id"] == "phone_control_task"
    assert queue[0]["behavior"]["task"] == "帮我点杯奶茶"

    from core.phone_control import task_state
    task_id = queue[0]["behavior"]["task_id"]
    entry = task_state.get_task(task_id)
    assert entry is not None
    assert entry["task"] == "帮我点杯奶茶"
    assert entry["user_id"] == "u1"
