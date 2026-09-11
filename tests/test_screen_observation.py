import asyncio
import io
import base64
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from core.perception import screen_observation as service
from admin.routers import screen_observation as api


@pytest.fixture(autouse=True)
def reset(monkeypatch):
    service.devices.clear()
    service.receipts.clear()
    monkeypatch.setattr(service, "_pending", None)
    monkeypatch.setattr(service, "_last_request", -100000.0)
    monkeypatch.setattr(service, "enabled", lambda: True)
    monkeypatch.setattr(service, "get_config", lambda: {"scheduler": {"owner_id": "owner"}})


def test_active_device_needs_fresh_consent_and_recent_input():
    service.poll("desktop", "d", True, 30)
    service.poll("mobile", "m", True, 3)
    assert service.active_device() == "mobile"
    service.poll("mobile", "m", False, 0)
    assert service.active_device() == "desktop"
    assert service.active_device(service.time.monotonic() + 31) is None


@pytest.mark.asyncio
async def test_request_correlated_single_claim_and_private_receipt(monkeypatch):
    from core.perception import vlm_client
    async def describe(image):
        assert image == b"fresh"
        return vlm_client.VisualObservation("desk", "working", .9, False, "editing"), None
    monkeypatch.setattr(vlm_client, "describe_with_status", describe)
    service.poll("desktop", "d", True, 1)
    task = asyncio.create_task(service.observe("owner", "char"))
    await asyncio.sleep(0)
    assert service.poll("mobile", "m", True, 0)["request"] is None
    request = service.poll("desktop", "d", True, 1)["request"]
    assert service.poll("desktop", "d", True, 1)["request"] is None
    assert not service.accept("desktop", "wrong", request["request_id"], b"fresh", "ok")
    assert service.accept("desktop", "d", request["request_id"], b"fresh", "ok")
    assert not service.accept("desktop", "d", request["request_id"], b"fresh", "ok")
    assert 'editing' in await task
    assert 'editing' not in str(service.state())
    assert 'fresh' not in str(service.state())
    assert 'cooldown' in await service.observe("owner", "char")


@pytest.mark.asyncio
async def test_revoke_and_timeout_release_pending(monkeypatch):
    service.poll("mobile", "m", True, 1)
    task = asyncio.create_task(service.observe("owner", "char"))
    await asyncio.sleep(0)
    service.poll("mobile", "m", False, 1)
    assert 'consent_revoked' in await task
    assert service._pending is None
    monkeypatch.setattr(service, "_last_request", -100000.0)
    monkeypatch.setattr(service, "REQUEST_TTL", .01)
    service.poll("mobile", "m", True, 1)
    assert 'timeout' in await service.observe("owner", "char")
    assert service._pending is None


@pytest.mark.asyncio
async def test_owner_and_disabled_gates(monkeypatch):
    assert 'owner_only' in await service.observe("other", "char")
    monkeypatch.setattr(service, "enabled", lambda: False)
    assert 'disabled' in await service.observe("owner", "char")
    assert service._pending is None


@pytest.mark.asyncio
async def test_sensitive_never_reaches_role(monkeypatch):
    from core.perception import vlm_client
    async def describe(image):
        return vlm_client.VisualObservation("desk", "working", .9, True, "secret"), None
    monkeypatch.setattr(vlm_client, "describe_with_status", describe)
    service.poll("desktop", "d", True, 1)
    task = asyncio.create_task(service.observe("owner", "char"))
    await asyncio.sleep(0)
    request = service.poll("desktop", "d", True, 1)["request"]
    service.accept("desktop", "d", request["request_id"], b"new", "ok")
    assert await task == '{"status": "sensitive"}'
    assert "secret" not in str(service.state())


def test_profile_cannot_impersonate_another_device():
    from admin.auth import TokenInfo
    from starlette.requests import Request
    from fastapi import HTTPException
    request = Request({"type": "http", "headers": [(b"authorization", b"Bearer test")]})
    with pytest.raises(HTTPException) as error:
        api.identity(request, TokenInfo("phone", frozenset({"sensor.write"}), "mobile"), "desktop")
    assert error.value.status_code == 403


def test_http_capture_contract_and_stale_result(monkeypatch):
    from admin.auth import TokenInfo
    app = FastAPI()
    app.include_router(api.router)
    for route in api.router.routes:
        for dependency in route.dependant.dependencies:
            app.dependency_overrides[dependency.call] = lambda: TokenInfo("phone", frozenset({"sensor.write", "state.read"}), "mobile")
    client = TestClient(app)
    headers = {"authorization": "Bearer fixture"}
    assert client.post("/perception/screen/poll", headers=headers, json={"device": "desktop", "available": True, "idle_seconds": 0}).status_code == 403
    assert client.post("/perception/screen/poll", headers=headers, json={"device": "mobile", "available": True, "idle_seconds": 0}).json()["request"] is None
    image = io.BytesIO()
    Image.new("RGB", (8, 8)).save(image, format="JPEG")
    body = {"device": "mobile", "request_id": "a"*32, "status": "ok", "image_base64": base64.b64encode(image.getvalue()).decode()}
    assert client.post("/perception/screen/result", headers=headers, json=body).status_code == 409
    received = []
    monkeypatch.setattr(service, "accept", lambda *args: received.append(args) or True)
    assert client.post("/perception/screen/result", headers=headers, json=body).json() == {"accepted": True}
    assert received[-1][3] == image.getvalue()
    body["image_base64"] = "not an image"
    assert client.post("/perception/screen/result", headers=headers, json=body).status_code == 422
    assert len(received) == 1


def test_screen_summary_never_enters_action_trace():
    from core.memory.action_trace import build_result_digest
    assert "secret" not in build_result_digest("observe_user_screen", '{"caption":"secret"}')


def test_global_feature_and_explicit_tool_deny_both_apply(monkeypatch):
    from core import tool_dispatcher
    monkeypatch.setattr(tool_dispatcher, 'get_config', lambda: {'tools': {'observe_user_screen': {'enabled': False}}})
    assert not tool_dispatcher._is_tool_enabled('observe_user_screen')
    monkeypatch.setattr(tool_dispatcher, 'get_config', lambda: {})
    assert tool_dispatcher._is_tool_enabled('observe_user_screen')
    monkeypatch.setattr(service, 'enabled', lambda: False)
    assert not tool_dispatcher._is_tool_enabled('observe_user_screen')
