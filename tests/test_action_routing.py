import pytest
from pathlib import Path


def test_action_ownership_is_static_and_fail_closed():
    from core.tool_dispatcher import resolve_action_target

    assert resolve_action_target("show_heart") == "device"
    assert resolve_action_target("minimize_window") == "desktop"
    assert resolve_action_target("not_registered") is None


@pytest.mark.asyncio
async def test_device_action_only_uses_device_transport(monkeypatch):
    from channels import desktop_ws, device_ws
    from core.tool_dispatcher import _push_desktop_action

    calls = []
    monkeypatch.setattr(desktop_ws, "is_connected", lambda: True)
    monkeypatch.setattr(device_ws, "is_connected", lambda: True)

    async def desktop_push(*args, **kwargs):
        calls.append("desktop")
        return True, None

    async def device_push(*args, **kwargs):
        calls.append("device")
        return True, None

    monkeypatch.setattr(desktop_ws, "push_action_and_wait", desktop_push)
    monkeypatch.setattr(device_ws, "push_action_and_wait", device_push)

    assert await _push_desktop_action({"type": "show_heart"}) == "ok"
    assert calls == ["device"]


@pytest.mark.asyncio
async def test_device_failures_do_not_use_desktop_queue(monkeypatch):
    from channels import device_ws
    from core import tool_dispatcher as dispatcher

    monkeypatch.setattr(device_ws, "is_connected", lambda: False)
    monkeypatch.setattr(dispatcher, "_is_desktop_active", lambda: True)
    assert await dispatcher._push_desktop_action({"type": "show_heart"}) == "设备端离线，动作未执行"


@pytest.mark.asyncio
async def test_device_nack_is_explicit_failure(monkeypatch):
    from channels import device_ws
    from core.tool_dispatcher import _push_desktop_action

    monkeypatch.setattr(device_ws, "is_connected", lambda: True)

    async def device_push(*args, **kwargs):
        return False, "unsupported action type"

    monkeypatch.setattr(device_ws, "push_action_and_wait", device_push)
    assert await _push_desktop_action({"type": "show_heart"}) == "设备动作未执行: unsupported action type"


@pytest.mark.asyncio
async def test_desktop_action_only_uses_desktop_transport(monkeypatch):
    from channels import desktop_ws, device_ws
    from core.tool_dispatcher import _push_desktop_action

    calls = []
    monkeypatch.setattr(desktop_ws, "is_connected", lambda: True)
    monkeypatch.setattr(device_ws, "is_connected", lambda: True)

    async def desktop_push(*args, **kwargs):
        calls.append("desktop")
        return True, None

    async def device_push(*args, **kwargs):
        calls.append("device")
        return True, None

    monkeypatch.setattr(desktop_ws, "push_action_and_wait", desktop_push)
    monkeypatch.setattr(device_ws, "push_action_and_wait", device_push)

    assert await _push_desktop_action({"type": "toy_invite"}) == "ok"
    assert calls == ["desktop"]


@pytest.mark.asyncio
async def test_unknown_action_is_not_sent(monkeypatch):
    from channels import desktop_ws, device_ws
    from core.tool_dispatcher import _push_desktop_action

    monkeypatch.setattr(desktop_ws, "is_connected", lambda: True)
    monkeypatch.setattr(device_ws, "is_connected", lambda: True)
    assert await _push_desktop_action({"type": "not_registered"}) == "未注册动作，未执行"


def test_firmware_unknown_action_sends_negative_ack():
    source = Path("firmware/presence-device/src/ws_client.cpp").read_text(encoding="utf-8")
    assert 'wsSendAck(msgId, false, "unsupported action type")' in source
    assert 'doc["error"] = error' in source


@pytest.mark.asyncio
async def test_heart_success_log_requires_device_ack(monkeypatch, caplog):
    from core.embodiment import heart

    heart._LAST_SENT.clear()

    async def affectionate(_reply):
        return True

    async def rejected(_action):
        return "设备端离线，动作未执行"

    monkeypatch.setattr(heart.llm_client, "detect_affection_checked", affectionate)
    monkeypatch.setattr("core.tool_dispatcher._push_desktop_action", rejected)
    monkeypatch.setattr(heart.config_loader, "get_config", lambda: {
        "embodiment": {"heart": {"enabled": True, "cooldown_sec": 45}}
    })

    with caplog.at_level("INFO"):
        await heart.maybe_draw_heart("warm reply", "test_char")

    assert "设备已确认画爱心" not in caplog.text
    assert "test_char" not in heart._LAST_SENT
