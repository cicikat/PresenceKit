import asyncio

import pytest
from fastapi.routing import APIRoute

from core.hardware import device_registry
from core.hardware import buttplug_client


@pytest.fixture(autouse=True)
async def reset_buttplug_state():
    await buttplug_client._reset_for_tests()
    yield
    await buttplug_client._reset_for_tests()


def _add_vibrating_device(index: int = 7) -> None:
    buttplug_client._handle_message({
        "DeviceAdded": {
            "DeviceIndex": index,
            "DeviceName": "Test Toy",
            "DeviceMessages": {
                "ScalarCmd": [
                    {"ActuatorType": "Oscillate", "StepCount": 20},
                    {"ActuatorType": "Vibrate", "StepCount": 20},
                ],
                "StopDeviceCmd": {},
            },
        },
    })


def test_device_events_update_registry():
    _add_vibrating_device()
    assert buttplug_client.get_devices() == [{
        "index": 7,
        "name": "Test Toy",
        "display_name": "",
        "connected": True,
        "can_vibrate": True,
    }]
    assert device_registry.get(7, require_vibrate=True).vibration_indices == (1,)

    buttplug_client._handle_message({"DeviceRemoved": {"DeviceIndex": 7}})
    assert buttplug_client.get_devices() == []


async def test_vibrate_clamps_values_and_always_stops(monkeypatch):
    _add_vibrating_device()
    calls = []

    async def fake_connected():
        return True

    async def fake_request(message_type, payload):
        calls.append((message_type, payload))
        return {}

    async def fake_sleep(seconds):
        calls.append(("sleep", seconds))

    monkeypatch.setattr(buttplug_client, "ensure_connected", fake_connected)
    monkeypatch.setattr(buttplug_client, "is_connected", lambda: True)
    monkeypatch.setattr(buttplug_client, "_request", fake_request)
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    assert await buttplug_client.vibrate(7, intensity=9, duration_ms=100_000)
    assert calls == [
        (
            "ScalarCmd",
            {
                "DeviceIndex": 7,
                "Scalars": [{"Index": 1, "Scalar": 1.0, "ActuatorType": "Vibrate"}],
            },
        ),
        ("sleep", 30.0),
        ("StopDeviceCmd", {"DeviceIndex": 7}),
    ]


async def test_pattern_stops_after_command_failure(monkeypatch):
    _add_vibrating_device()
    calls = []

    async def fake_connected():
        return True

    async def fake_request(message_type, payload):
        calls.append(message_type)
        if message_type == "ScalarCmd":
            raise RuntimeError("transport failed")
        return {}

    monkeypatch.setattr(buttplug_client, "ensure_connected", fake_connected)
    monkeypatch.setattr(buttplug_client, "is_connected", lambda: True)
    monkeypatch.setattr(buttplug_client, "_request", fake_request)

    assert not await buttplug_client.pattern(7, [(0.5, 10)])
    assert calls == ["ScalarCmd", "StopDeviceCmd"]


async def test_hardware_disabled_fails_closed(monkeypatch):
    monkeypatch.setattr(buttplug_client, "_hardware_config", lambda: {"enabled": False})
    assert not await buttplug_client.ensure_connected()


def test_toy_tools_registered_as_desktop_side_effects():
    from core import tool_dispatcher

    for name in ("toy_vibrate", "toy_stop", "toy_pattern"):
        spec = tool_dispatcher._TOOL_REGISTRY[name]
        assert spec["category"] == "desktop"
        assert spec["examples"]
        assert spec["keywords"]
        assert tool_dispatcher.is_side_effect_tool(name)


def test_hardware_routes_are_bearer_protected():
    from admin.admin_server import app
    from admin.auth import verify_token

    routes = {
        route.path: route
        for route in app.routes
        if isinstance(route, APIRoute)
    }
    for path in ("/hardware/devices", "/hardware/connect"):
        assert path in routes
        assert verify_token in {
            dependency.call for dependency in routes[path].dependant.dependencies
        }


async def test_toy_tools_reject_non_owner_and_group(monkeypatch):
    from core import tool_dispatcher

    class FakeState:
        status = "idle"
        WAITING_CONFIRM = "waiting_confirm"

    monkeypatch.setattr(
        tool_dispatcher,
        "get_config",
        lambda: {"scheduler": {"owner_id": "owner"}},
    )
    monkeypatch.setattr(tool_dispatcher, "_current_mode", lambda: "danger")

    for user_id, is_group in (("other", False), ("owner", True)):
        result, confirm = await tool_dispatcher.execute(
            "toy_stop",
            {},
            user_id=user_id,
            target_id=user_id,
            is_group=is_group,
            session_state=FakeState(),
            origin="user_live",
        )
        assert result == "硬件控制只允许 owner 私聊触发"
        assert confirm is None


async def test_toy_tool_executes_for_owner_private_turn(monkeypatch):
    from core import tool_dispatcher

    class FakeState:
        status = "idle"
        WAITING_CONFIRM = "waiting_confirm"

    async def fake_stop(**kwargs):
        return "已停止"

    monkeypatch.setattr(
        tool_dispatcher,
        "get_config",
        lambda: {"scheduler": {"owner_id": "owner"}},
    )
    monkeypatch.setattr(tool_dispatcher, "_current_mode", lambda: "danger")
    monkeypatch.setitem(tool_dispatcher._TOOL_REGISTRY["toy_stop"], "func", fake_stop)

    result, confirm = await tool_dispatcher.execute(
        "toy_stop",
        {},
        user_id="owner",
        target_id="owner",
        is_group=False,
        session_state=FakeState(),
        origin="user_live",
    )
    assert result == "工具已执行：toy_stop，结果：已停止"
    assert confirm is None
