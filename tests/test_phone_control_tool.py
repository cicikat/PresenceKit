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


@pytest.mark.asyncio
async def test_wrapper_rejects_empty_task(sandbox):
    result = await tool_dispatcher._phone_control_start_wrapper("", user_id="u1", char_id=TEST_CHAR_ID)
    assert "没听清楚" in result or "没法" in result


@pytest.mark.asyncio
async def test_video_call_mistaken_for_phone_control_routes_to_desktop(sandbox, monkeypatch):
    calls = []

    async def fake_invite(user_id, char_id):
        calls.append((user_id, char_id))
        return '{"status": "desktop_offline"}'

    monkeypatch.setattr(tool_dispatcher, "_invite_video_call_wrapper", fake_invite)
    monkeypatch.setitem(tool_dispatcher._TOOL_REGISTRY["invite_video_call"], "func", fake_invite)
    monkeypatch.setattr(tool_dispatcher, "_is_tool_enabled", lambda _: True)
    result = await tool_dispatcher.execute_structured(
        "phone_control_start",
        {"task": "发起与角色的视频通话邀请，打开视频通话界面"},
        "u1", "u1", False, _Session(), origin="user_live", char_id=TEST_CHAR_ID,
    )
    assert result.confirmation_request is None
    assert calls == [("u1", TEST_CHAR_ID)]
    assert not sandbox.mobile_queue().exists()


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
