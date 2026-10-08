from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core import qzone_service as service, tool_dispatcher
from core.tools import qzone_tools
from integrations.qzone import transport
from tests.fixtures.public_assets import TEST_CHAR_ID


@pytest.fixture
def configured(monkeypatch):
    cfg = {"scheduler": {"owner_id": "test_owner"}, "qzone": {
        "enabled": True, "write_enabled": True, "account_id": "12345",
        "allowed_char_ids": [TEST_CHAR_ID], "access_token": "local-test-bridge-secret",
    }}
    monkeypatch.setattr(service, "get_config", lambda: cfg)
    return cfg


@pytest.mark.asyncio
async def test_authority_gate_and_account_replacement(configured, monkeypatch):
    calls = []
    async def request(url, token, action, params):
        calls.append(action)
        return {"ok": True, "data": {"user_id": "99999"}}
    monkeypatch.setattr(service, "request", request)
    assert not service.allowed("other", TEST_CHAR_ID)
    assert not service.allowed("test_owner", "other")
    assert (await service.call("send_like", {}, uid="other", char_id=TEST_CHAR_ID))["code"] == "qzone_not_authorized"
    assert calls == []
    result = await service.call("send_like", {}, uid="test_owner", char_id=TEST_CHAR_ID)
    assert result["code"] == "account_mismatch"
    assert calls == ["get_login_info"]
    configured["qzone"]["write_enabled"] = False
    assert not service.allowed("test_owner", TEST_CHAR_ID, write=True)
    assert service.allowed("test_owner", TEST_CHAR_ID)


@pytest.mark.asyncio
async def test_tools_map_to_space_actions_without_private_chat(configured, monkeypatch):
    calls = []
    async def call(action, params, **scope):
        calls.append((action, params, scope))
        return {"ok": True, "data": {"tid": "hex-post-id"}}
    monkeypatch.setattr(service, "call", call)
    scope = {"user_id": "test_owner", "char_id": TEST_CHAR_ID}
    await qzone_tools.publish("hello [CQ:image,file=bad]", **scope)
    assert calls[-1][0] == "send_private_msg"
    assert calls[-1][1] == {"message": [{"type": "text", "data": {"text": "hello [CQ:image,file=bad]"}}]}
    await qzone_tools.comment("98765", "hex-post-id", "评论", "comment1", "98765", **scope)
    assert calls[-1][1]["target_tid"] == "hex-post-id"
    assert calls[-1][1]["reply_comment_id"] == "comment1"
    await qzone_tools.set_like("98765", "hex-post-id", False, 1700000000, **scope)
    assert calls[-1][0] == "unlike"
    assert calls[-1][1]["user_id"] == "98765"
    await qzone_tools.get_posts(limit=100, cursor="next", **scope)
    assert calls[-1][1]["num"] == 20
    assert calls[-1][1]["include_image_data"] is False
    assert calls[-1][1]["max_pages"] == 1


@pytest.mark.asyncio
async def test_failed_write_is_not_a_successful_receipt(configured, monkeypatch):
    calls = []
    async def call(*args, **kwargs):
        calls.append(args)
        return {"ok": False, "code": "outcome_unknown"}
    monkeypatch.setattr(service, "call", call)
    result = await qzone_tools.publish("文字", user_id="test_owner", char_id=TEST_CHAR_ID)
    assert result.meta["validity"] == "outcome_unknown"
    assert len(calls) == 1
    assert json.loads(result.safe_summary)["ok"] is False


def test_bounded_untrusted_data_and_urls():
    data = transport.bounded_data({"content": "text", "cookie": "secret", "base64": "huge", "posts": list(range(100))})
    assert "cookie" not in data and "base64" not in data
    assert len(data["posts"]) == 20
    for url in ["file:///tmp/a", "http://user:pass@host", "http://host/?token=x", "http://host/#x"]:
        with pytest.raises(ValueError):
            service.QzoneSettings(base_url=url)
    with pytest.raises(ValueError):
        service.QzoneSettings(allowed_char_ids=["../other"])


def test_api_credentials_are_write_only_and_scope_protected(configured):
    from admin.routers import qzone
    from admin.auth import require_scopes
    app = FastAPI()
    app.include_router(qzone.router)
    client = TestClient(app)
    assert client.get("/settings/qzone").status_code in {401, 403}
    # Test the exact redacted read projection independently of auth fixtures.
    import asyncio
    body = asyncio.run(qzone.get_settings(None))
    assert body["credential_configured"]
    assert "local-test-bridge-secret" not in json.dumps(body)
    assert "access_token" not in body


@pytest.mark.asyncio
async def test_save_preserves_unrelated_config_and_blank_token(configured, monkeypatch):
    from admin.routers import qzone
    cfg = {"qzone": {"access_token": "keep-token"}, "unrelated": {"keep": 1}}
    monkeypatch.setattr(qzone, "read_config_file", lambda path: cfg)
    monkeypatch.setattr(qzone, "write_config_file", lambda path, value: None)
    monkeypatch.setattr(qzone, "reload_config", lambda: None)
    await qzone.put_settings(service.QzoneSettings(base_url="http://bridge:5700"), None)
    assert cfg["qzone"]["access_token"] == "keep-token"
    assert cfg["unrelated"] == {"keep": 1}


@pytest.mark.asyncio
async def test_dispatcher_rejects_group_and_injects_frozen_scope(configured, monkeypatch, sandbox):
    from core.session_state import SessionState
    async def request(url, token, action, params):
        return {"ok": True, "data": {"user_id": "12345"}} if action == "get_login_info" else {"ok": True, "data": {"tid": "hex-post-id"}}
    monkeypatch.setattr(service, "request", request)
    rejected = await tool_dispatcher.execute_structured("qzone_publish", {"content": "text"}, "test_owner", "test_owner", True, SessionState(), origin="assistant_loop", char_id=TEST_CHAR_ID)
    assert rejected.status == "tool_failed"
    result = await tool_dispatcher.execute_structured("qzone_publish", {"content": "text"}, "test_owner", "test_owner", False, SessionState(), origin="assistant_loop", char_id=TEST_CHAR_ID)
    assert result.status == "tool_executed"
    assert "hex-post-id" in result.result
    forbidden = await tool_dispatcher.execute_structured("qzone_publish", {"content": "text", "user_id": "other"}, "test_owner", "test_owner", False, SessionState(), origin="assistant_loop", char_id=TEST_CHAR_ID)
    assert forbidden.status != "tool_executed"


def test_schema_exposure_follows_binding_and_write_switch(configured):
    names = {s["function"]["name"] for s in tool_dispatcher.get_tools_schema(uid="test_owner", char_id=TEST_CHAR_ID)}
    assert "qzone_publish" in names
    configured["qzone"]["write_enabled"] = False
    names = {s["function"]["name"] for s in tool_dispatcher.get_tools_schema(uid="test_owner", char_id=TEST_CHAR_ID)}
    assert "qzone_publish" not in names
    assert "qzone_get_posts" in names


def test_qzone_category_is_discoverable_without_overloading_info(configured):
    from core.tool_exposure import resolve
    from core.tool_discovery import ToolDiscovery
    exposure = resolve("path_c", char_id=TEST_CHAR_ID)
    assert "qzone" in exposure.categories
    schemas = tool_dispatcher.get_tools_schema(categories=list(exposure.categories), uid="test_owner", char_id=TEST_CHAR_ID)
    discovery = ToolDiscovery(schemas, tool_dispatcher._TOOL_REGISTRY)
    entries = {s["function"]["name"] for s in discovery.schemas()}
    assert "load_tools_qzone" in entries
    assert len(discovery.groups["qzone"]) == 7


@pytest.mark.asyncio
async def test_transport_streams_bounded_response_and_does_not_retry_writes():
    from aiohttp import web
    from aiohttp.test_utils import TestServer
    calls = []
    async def handle(req):
        calls.append(req.path)
        if req.path == "/send_comment":
            return web.Response(status=503)
        body = json.dumps({"status": "ok", "retcode": 0, "data": {"tid": "exact-id", "cookie": "secret"}}).encode()
        response = web.StreamResponse()
        await response.prepare(req)
        await response.write(body[:20])
        await response.write(body[20:])
        await response.write_eof()
        return response
    app = web.Application()
    app.router.add_post('/{action}', handle)
    async with TestServer(app) as server:
        base = str(server.make_url('')).rstrip('/')
        result = await transport.request(base, "", "get_emotion_list", {})
        assert result == {"ok": True, "data": {"tid": "exact-id"}}
        result = await transport.request(base, "", "send_comment", {})
        assert result["code"] == "outcome_unknown"
        assert calls == ["/get_emotion_list", "/send_comment"]


@pytest.mark.asyncio
async def test_cookie_account_mismatch_never_contacts_bridge(configured, monkeypatch):
    from admin.routers import qzone
    from fastapi import HTTPException
    async def unexpected(*a, **kw):
        pytest.fail("mismatched cookie was sent to bridge")
    monkeypatch.setattr(qzone, "request", unexpected)
    with pytest.raises(HTTPException) as failure:
        await qzone.login_cookie(qzone.CookieLogin(cookie="uin=o99999; p_skey=secret"), None)
    assert failure.value.status_code == 400


@pytest.mark.asyncio
async def test_invalid_content_never_contacts_bridge(configured, monkeypatch):
    async def unexpected(*a, **kw):
        pytest.fail("invalid text made an external request")
    monkeypatch.setattr(service, "call", unexpected)
    result = await qzone_tools.publish(" " * 10, user_id="test_owner", char_id=TEST_CHAR_ID)
    assert result.meta["validity"] == "execution_failed"
