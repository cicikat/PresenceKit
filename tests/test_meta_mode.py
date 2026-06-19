"""Global safe/danger mode gate and management endpoint contracts."""

import json
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from admin.auth import verify_token
from admin.routers.system import router as system_router
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


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(system_router)
    app.dependency_overrides[verify_token] = lambda: True
    return TestClient(app)


def _write_mode(sandbox, mode, expires_at=None):
    path = sandbox.meta_mode()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"mode": mode, "expires_at": expires_at}),
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_safe_mode_blocks_desktop_but_not_info(sandbox, monkeypatch):
    called = []

    async def fake_desktop(**kwargs):
        called.append(("desktop", kwargs))
        return "desktop-ok"

    async def fake_info(**kwargs):
        called.append(("info", kwargs))
        return "info-ok"

    monkeypatch.setitem(
        tool_dispatcher._TOOL_REGISTRY,
        "_test_desktop",
        {
            "func": fake_desktop,
            "description": "test",
            "dangerous": False,
            "category": "desktop",
            "parameters": {},
        },
    )
    monkeypatch.setitem(
        tool_dispatcher._TOOL_REGISTRY,
        "_test_info",
        {
            "func": fake_info,
            "description": "test",
            "dangerous": False,
            "category": "info",
            "parameters": {},
        },
    )
    monkeypatch.setattr(tool_dispatcher, "_is_tool_enabled", lambda _: True)

    session = _Session()
    result, confirm = await tool_dispatcher.execute(
        "_test_desktop", {}, "u1", "u1", False, session, origin="user_live"
    )
    assert "安全模式" in result
    assert confirm is None
    assert called == []

    result, confirm = await tool_dispatcher.execute(
        "_test_info", {}, "u1", "u1", False, session, origin="user_live"
    )
    assert result == "工具已执行：_test_info，结果：info-ok"
    assert confirm is None
    assert called == [("info", {})]


@pytest.mark.asyncio
async def test_danger_mode_allows_desktop_action(sandbox, monkeypatch):
    _write_mode(sandbox, "danger", time.time() + 60)
    called = []

    async def fake_desktop(**kwargs):
        called.append(kwargs)
        return "desktop-ok"

    monkeypatch.setitem(
        tool_dispatcher._TOOL_REGISTRY,
        "_test_desktop",
        {
            "func": fake_desktop,
            "description": "test",
            "dangerous": False,
            "category": "desktop",
            "parameters": {},
        },
    )
    monkeypatch.setattr(tool_dispatcher, "_is_tool_enabled", lambda _: True)

    result, confirm = await tool_dispatcher.execute(
        "_test_desktop", {}, "u1", "u1", False, _Session(), origin="user_live"
    )
    assert (result, confirm) == ("工具已执行：_test_desktop，结果：desktop-ok", None)
    assert called == [{}]


@pytest.mark.asyncio
async def test_expired_danger_mode_fails_closed(sandbox, monkeypatch):
    _write_mode(sandbox, "danger", time.time() - 1)
    monkeypatch.setattr(tool_dispatcher, "_is_tool_enabled", lambda _: True)

    result, confirm = await tool_dispatcher.execute(
        "desktop_open_url",
        {"url": "https://example.com"},
        "u1",
        "u1",
        False,
        _Session(),
        origin="user_live",
    )
    assert "安全模式" in result
    assert confirm is None


@pytest.mark.asyncio
async def test_shutdown_still_requires_confirmation_in_danger_mode(sandbox, monkeypatch):
    _write_mode(sandbox, "danger", time.time() + 60)
    monkeypatch.setattr(tool_dispatcher, "_is_tool_enabled", lambda _: True)
    monkeypatch.setattr("core.user_relation.has_permission", lambda *_: True)
    session = _Session()

    result, confirm = await tool_dispatcher.execute(
        "device_shutdown", {}, "u1", "u1", False, session, origin="user_live"
    )
    assert result is None
    assert confirm
    assert session.pending == ("device_shutdown", {})


def test_meta_mode_endpoints_default_patch_and_expiry(client, sandbox, monkeypatch):
    response = client.get("/system/meta-mode")
    assert response.status_code == 200
    assert response.json() == {"mode": "safe", "expires_at": None}

    now = 1_000_000.0
    monkeypatch.setattr(time, "time", lambda: now)
    response = client.patch("/system/meta-mode", json={"mode": "danger", "ttl_seconds": 30})
    assert response.status_code == 200
    assert response.json() == {"mode": "danger", "expires_at": now + 30}

    monkeypatch.setattr(time, "time", lambda: now + 31)
    response = client.get("/system/meta-mode")
    assert response.status_code == 200
    assert response.json() == {"mode": "safe", "expires_at": None}


@pytest.mark.parametrize("ttl", [0, -1, 1.5, True, "bad", [], {}])
def test_meta_mode_endpoint_rejects_invalid_ttl(client, sandbox, ttl):
    response = client.patch(
        "/system/meta-mode",
        json={"mode": "danger", "ttl_seconds": ttl},
    )
    assert response.status_code == 422
    assert not sandbox.meta_mode().exists()


def test_meta_mode_endpoints_require_auth(sandbox):
    app = FastAPI()
    app.include_router(system_router)
    client = TestClient(app, raise_server_exceptions=False)

    assert client.get("/system/meta-mode").status_code in (401, 403)
    assert client.patch("/system/meta-mode", json={"mode": "danger"}).status_code in (401, 403)
    assert not sandbox.meta_mode().exists()
