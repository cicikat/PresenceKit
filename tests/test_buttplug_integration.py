from tests.fixtures.public_assets import TEST_CHAR_ID
import asyncio
import json

import pytest
from fastapi.routing import APIRoute

from core.hardware import device_registry
from core.hardware import buttplug_client
from core.hardware import jobs


@pytest.fixture(autouse=True)
async def reset_buttplug_state():
    await jobs._reset_for_tests()
    await buttplug_client._reset_for_tests()
    yield
    await jobs._reset_for_tests()
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


async def test_vibration_command_clamps_values_and_returns_immediately(monkeypatch):
    _add_vibrating_device()
    calls = []

    async def fake_connected():
        return True

    async def fake_request(message_type, payload):
        calls.append((message_type, payload))
        return {}

    monkeypatch.setattr(buttplug_client, "ensure_connected", fake_connected)
    monkeypatch.setattr(buttplug_client, "is_connected", lambda: True)
    monkeypatch.setattr(buttplug_client, "_request", fake_request)
    assert await buttplug_client._start_vibration_command(7, intensity=9) == 7
    assert calls == [
        (
            "ScalarCmd",
            {
                "DeviceIndex": 7,
                "Scalars": [{"Index": 1, "Scalar": 1.0, "ActuatorType": "Vibrate"}],
            },
        ),
    ]


async def test_vibration_command_reports_command_failure(monkeypatch):
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

    assert await buttplug_client._start_vibration_command(7, intensity=0.5) is None
    assert calls == ["ScalarCmd"]


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

    status_spec = tool_dispatcher._TOOL_REGISTRY["toy_job_status"]
    assert status_spec["category"] == "info"
    assert status_spec["examples"]
    assert status_spec["keywords"]
    assert not tool_dispatcher.is_side_effect_tool("toy_job_status")


def test_intiface_is_frozen_from_all_default_surfaces(monkeypatch):
    from core import tool_dispatcher
    from core.self_management import registry as capability_registry
    from core.autonomy import policy as autonomy_policy

    monkeypatch.setattr(tool_dispatcher, "get_config", lambda: {
        "tools": {name: {"enabled": True} for name in tool_dispatcher._INTIFACE_TOOL_NAMES},
        "hardware": {"intiface_opt_in": False},
    })
    names = {item["function"]["name"] for item in tool_dispatcher.get_tools_schema()}
    assert names.isdisjoint(tool_dispatcher._INTIFACE_TOOL_NAMES)
    assert all(
        spec.tool_name not in tool_dispatcher._INTIFACE_TOOL_NAMES
        for spec in capability_registry.list_available()
    )
    assert all(
        item["function"]["name"] not in tool_dispatcher._INTIFACE_TOOL_NAMES
        for item in autonomy_policy.allowed_tools("owner", "char", {"config": {"tools": {}}})
    )


def test_hardware_routes_are_bearer_protected():
    from admin.admin_server import app

    routes = {
        route.path: route
        for route in app.routes
        if isinstance(route, APIRoute)
    }
    for path in (
        "/hardware/devices",
        "/hardware/connect",
        "/hardware/jobs",
        "/hardware/jobs/{job_id}",
        "/hardware/jobs/{job_id}/stop",
    ):
        assert path in routes
        assert any(
            hasattr(dependency.call, "_required_scopes")
            for dependency in routes[path].dependant.dependencies
        )


async def test_toy_tools_reject_non_owner_and_group(monkeypatch):
    from core import tool_dispatcher

    class FakeState:
        status = "idle"
        WAITING_CONFIRM = "waiting_confirm"

    monkeypatch.setattr(
        tool_dispatcher,
        "get_config",
        lambda: {"scheduler": {"owner_id": "owner"}, "hardware": {"intiface_opt_in": True}},
    )
    monkeypatch.setattr(tool_dispatcher, "_current_mode", lambda: "danger")

    for user_id, is_group in (("other", False), ("owner", True)):
        result = await tool_dispatcher.execute_structured(
            "toy_stop",
            {},
            user_id=user_id,
            target_id=user_id,
            is_group=is_group,
            session_state=FakeState(),
            origin="user_live",
            char_id=TEST_CHAR_ID,
        )
        assert result.result == "硬件控制只允许 owner 私聊触发"
        assert result.confirmation_request is None


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
        lambda: {"scheduler": {"owner_id": "owner"}, "hardware": {"intiface_opt_in": True}},
    )
    monkeypatch.setattr(tool_dispatcher, "_current_mode", lambda: "danger")
    monkeypatch.setitem(tool_dispatcher._TOOL_REGISTRY["toy_stop"], "func", fake_stop)

    result = await tool_dispatcher.execute_structured(
        "toy_stop",
        {},
        user_id="owner",
        target_id="owner",
        is_group=False,
        session_state=FakeState(),
        origin="user_live",
        char_id=TEST_CHAR_ID,
    )
    assert result.result == "工具已执行：toy_stop，结果：已停止"
    assert result.confirmation_request is None


async def test_toy_tool_direct_dispatch_rejected_while_frozen(monkeypatch):
    from core import tool_dispatcher

    class FakeState:
        status = "idle"
        WAITING_CONFIRM = "waiting_confirm"

    monkeypatch.setattr(tool_dispatcher, "get_config", lambda: {
        "scheduler": {"owner_id": "owner"},
        "hardware": {"intiface_opt_in": False},
    })
    result = await tool_dispatcher.execute_structured(
        "toy_stop", {}, user_id="owner", target_id="owner", is_group=False,
        session_state=FakeState(), origin="user_live", char_id=TEST_CHAR_ID,
    )
    assert result.result == "Intiface 硬件能力当前处于冻结状态，需要显式 opt-in"
    assert result.confirmation_request is None


async def _wait_for_terminal(job_id: str) -> dict:
    for _ in range(50):
        record = jobs.get_job(job_id)
        if record and record["status"] in jobs.TERMINAL_STATUSES:
            return record
        await asyncio.sleep(0)
    raise AssertionError(f"job did not finish: {job_id}")


async def test_long_vibration_returns_before_transport_finishes(monkeypatch):
    started = asyncio.Event()
    release = asyncio.Event()

    async def fake_start(device_index, intensity):
        started.set()
        await release.wait()
        return 7

    async def fake_stop(device_index):
        return True

    monkeypatch.setattr(jobs, "_start_command", fake_start)
    monkeypatch.setattr(jobs, "_stop_command", fake_stop)

    from core.tools.hardware_tools import toy_vibrate

    result = await asyncio.wait_for(toy_vibrate(duration_ms=900_000, device_index=7), timeout=0.2)
    assert "已受理" in result
    record = jobs.list_jobs(active_only=True)[0]
    assert record["status"] == "accepted"
    await started.wait()
    release.set()
    await jobs.cancel_job(record["job_id"])


async def test_job_stops_at_deadline(monkeypatch):
    calls = []

    async def fake_start(device_index, intensity):
        calls.append(("start", device_index, intensity))
        return 7

    async def fake_stop(device_index):
        calls.append(("stop", device_index))
        return True

    monkeypatch.setattr(jobs, "_start_command", fake_start)
    monkeypatch.setattr(jobs, "_stop_command", fake_stop)
    record, duplicate = await jobs.submit_vibration(duration_ms=0, device_index=7)
    assert not duplicate
    final = await _wait_for_terminal(record["job_id"])
    assert final["status"] == "completed"
    assert calls[-1] == ("stop", 7)


async def test_pattern_runs_in_worker_and_stops_once(monkeypatch):
    calls = []

    async def fake_start(device_index, intensity):
        calls.append(("start", device_index, intensity))
        return 7

    async def fake_stop(device_index):
        calls.append(("stop", device_index))
        return True

    monkeypatch.setattr(jobs, "_start_command", fake_start)
    monkeypatch.setattr(jobs, "_stop_command", fake_stop)
    record, _ = await jobs.submit_pattern(
        pattern_name="test",
        steps=[(0.2, 0), (0.8, 0)],
        device_index=7,
    )
    final = await _wait_for_terminal(record["job_id"])
    assert final["status"] == "completed"
    assert calls == [
        ("start", 7, 0.2),
        ("start", 7, 0.8),
        ("stop", 7),
    ]


async def test_disconnect_fails_job_and_freezes_remaining_time(monkeypatch):
    async def fake_start(device_index, intensity):
        return 7

    async def fake_stop(device_index):
        pytest.fail("disconnect path must not claim a confirmed stop")

    monkeypatch.setattr(jobs, "_start_command", fake_start)
    monkeypatch.setattr(jobs, "_stop_command", fake_stop)
    record, _ = await jobs.submit_vibration(duration_ms=60_000, device_index=7)
    for _ in range(20):
        await asyncio.sleep(0)
        if jobs.get_job(record["job_id"])["status"] == "started":
            break
    await jobs.handle_device_lost(7)
    failed = jobs.get_job(record["job_id"])
    assert failed["status"] == "failed"
    assert failed["outcome"] == "unknown"
    assert failed["remaining_seconds"] == 0


async def test_prompt_fragment_uses_system_remaining_time(monkeypatch):
    async def fake_start(device_index, intensity):
        return 7

    async def fake_stop(device_index):
        return True

    monkeypatch.setattr(jobs, "_start_command", fake_start)
    monkeypatch.setattr(jobs, "_stop_command", fake_stop)
    record, _ = await jobs.submit_vibration(duration_ms=120_000, device_index=7)
    for _ in range(20):
        await asyncio.sleep(0)
        if jobs.get_job(record["job_id"])["status"] == "started":
            break
    fragment = jobs.format_prompt()
    assert "当前硬件动作状态" in fragment
    assert "还剩 约 2 分钟" in fragment
    await jobs.cancel_job(record["job_id"])


async def test_duplicate_start_is_idempotent(monkeypatch):
    async def fake_start(device_index, intensity):
        return 7

    async def fake_stop(device_index):
        return True

    monkeypatch.setattr(jobs, "_start_command", fake_start)
    monkeypatch.setattr(jobs, "_stop_command", fake_stop)
    first, duplicate_first = await jobs.submit_vibration(duration_ms=10_000, device_index=7)
    second, duplicate_second = await jobs.submit_vibration(duration_ms=10_000, device_index=7)
    assert not duplicate_first
    assert duplicate_second
    assert second["job_id"] == first["job_id"]


async def test_cancel_confirms_stop(monkeypatch):
    stop_calls = []

    async def fake_start(device_index, intensity):
        return 7

    async def fake_stop(device_index):
        stop_calls.append(device_index)
        return True

    monkeypatch.setattr(jobs, "_start_command", fake_start)
    monkeypatch.setattr(jobs, "_stop_command", fake_stop)
    record, _ = await jobs.submit_vibration(duration_ms=60_000, device_index=7)
    for _ in range(20):
        await asyncio.sleep(0)
        if jobs.get_job(record["job_id"])["status"] == "started":
            break
    cancelled = await jobs.cancel_job(record["job_id"])
    assert cancelled["status"] == "cancelled"
    assert cancelled["stop_confirmed"] is True
    assert stop_calls == [7]


async def test_restart_expires_old_job_and_attempts_stop(monkeypatch):
    path = jobs._state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    old_job = {
        "job_id": "old-job",
        "kind": "vibration",
        "status": "started",
        "device_index": 7,
        "requested_device_index": 7,
        "intensity": 0.5,
        "duration_ms": 60_000,
        "accepted_at": 1.0,
        "started_at": 1.0,
        "deadline_at": 60_001.0,
    }
    path.write_text(json.dumps({"schema_version": 1, "jobs": [old_job]}), encoding="utf-8")
    stop_calls = []

    async def fake_stop(device_index):
        stop_calls.append(device_index)
        return True

    monkeypatch.setattr(jobs, "_stop_command", fake_stop)
    await jobs.startup()
    recovered = jobs.get_job("old-job")
    assert recovered["status"] == "expired"
    assert recovered["outcome"] == "expired"
    assert recovered["stop_confirmed"] is True
    assert stop_calls == [7]
