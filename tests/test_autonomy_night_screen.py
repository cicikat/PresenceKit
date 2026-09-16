from datetime import datetime
from types import SimpleNamespace

import pytest

from core.autonomy import policy, runner
from core.perception import screen_observation as screen


@pytest.fixture
def clock(monkeypatch):
    current = SimpleNamespace(hour=2)
    monkeypatch.setattr(policy, "datetime", SimpleNamespace(now=lambda: datetime(2026, 9, 16, current.hour)))
    monkeypatch.setattr(screen, "devices", {})
    monkeypatch.setattr(screen.time, "monotonic", lambda: 1000.0)
    monkeypatch.setattr(screen, "enabled", lambda: True)
    return current


@pytest.mark.parametrize("hour,device,age,idle,available,hidden", [
    (0, None, 0, 0, True, True),
    (7, None, 0, 0, True, True),
    (8, None, 0, 0, True, False),
    (23, None, 0, 0, True, False),
    (2, "desktop", 29, 299, True, False),
    (2, "mobile", 29, 299, True, False),
    (2, "desktop", 30, 0, True, True),
    (2, "mobile", 0, 300, True, True),
    (2, "mobile", 0, 0, False, True),
])
def test_night_activity_boundaries(clock, hour, device, age, idle, available, hidden):
    clock.hour = hour
    if device:
        screen.devices[device] = {"seen_at": 1000-age, "interaction_at": 1000-idle, "available": available}
    assert policy.screen_observation_suppressed() is hidden


def test_autonomy_schema_and_observation_preserve_chat_tools(sandbox, monkeypatch, clock):
    from core import tool_dispatcher as dispatcher
    from core.self_management import policy as capability_policy

    names = ("observe_user_screen", "peek_screen_content")
    schemas = [{"type": "function", "function": {"name": name}} for name in names]
    monkeypatch.setattr(dispatcher, "get_tools_schema", lambda **kwargs: schemas)
    monkeypatch.setattr(dispatcher, "_is_tool_enabled", lambda name: True)
    monkeypatch.setattr(capability_policy, "effective", lambda *args: (True, None))
    state = {"config": {"tools": {name: {"enabled": True} for name in names}}}
    rows = {row["name"]: row for row in policy.tool_decisions("owner", "char", state)}
    assert rows["observe_user_screen"]["denial_reason"] == "night_no_active_device"
    assert not rows["observe_user_screen"]["final_schema"]
    assert rows["peek_screen_content"]["final_schema"]
    assert dispatcher.get_tools_schema() == schemas
    assert [item["function"]["name"] for item in policy.allowed_tools("owner", "char", state)] == ["peek_screen_content"]
    screen.poll("desktop", "fixture", False, 0)
    screen.poll("mobile", "fixture", True, 1)
    assert len(policy.allowed_tools("owner", "char", state)) == 2
    screen.devices.clear()
    clock.hour = 8
    assert len(policy.allowed_tools("owner", "char", state)) == 2


def test_prompt_omits_screenshot_nudge_but_preserves_talk():
    prompt = runner._system_prompt(talk_available=True, screen_available=False)
    assert "observe_user_screen" not in prompt
    assert "fresh view" not in prompt
    assert "talk_owner is available" in prompt
    assert "observe_user_screen" in runner._system_prompt(talk_available=True, screen_available=True)


@pytest.mark.asyncio
async def test_activity_expiring_during_model_call_denies_capture(clock):
    result, outcome = await runner._execute_tool("observe_user_screen", {}, None, None, {}, None)
    assert (result, outcome) == ("night_no_active_device", "denied")
