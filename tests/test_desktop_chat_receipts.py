import asyncio
import time
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI, HTTPException

from admin.routers import chat
from core.memory import short_term
from tests.fixtures.public_assets import TEST_CHAR_ID


@pytest.fixture
def app(monkeypatch, sandbox):
    from admin import auth

    def token_info(token):
        if token in {"fixture-desktop", "fixture-other"}:
            return auth.TokenInfo(label=token, scopes=frozenset({"chat"}))
        return None

    monkeypatch.setattr(auth, "resolve_token", token_info)
    app = FastAPI()
    app.include_router(chat.router)
    return app


def request_id():
    return f"dc-{int(time.time() * 1000)}-{uuid4().hex}"


def headers(rid, token="fixture-desktop"):
    return {"Authorization": f"Bearer {token}", "X-Presence-Chat-Request": rid}


async def test_disconnect_recovery_and_duplicate_post_keep_one_effect(app, monkeypatch):
    started = asyncio.Event()
    release = asyncio.Event()
    finished = asyncio.Event()
    effects = []
    rid = request_id()
    owner, _ = chat._owner_media_scope()

    async def execute(message, channel, *, request_id, **kwargs):
        effects.append((message, channel, request_id))
        started.set()
        await release.wait()
        short_term.append(owner, "assistant", "Fixture canonical reply.", "canonical-1",
                          char_id=TEST_CHAR_ID)
        finished.set()
        return {"reply": "Fixture canonical reply.", "msg_id": "wire-1", "turn_id": "canonical-1",
                "char_id": TEST_CHAR_ID}

    monkeypatch.setattr(chat, "run_owner_chat_turn", execute)
    path = f"/desktop/chat/requests/{rid}"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        waiter = asyncio.create_task(client.post("/desktop/chat", headers=headers(rid),
                                                 json={"message": "virtual send"}))
        try:
            await asyncio.wait_for(started.wait(), 2)
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
            assert (await client.get(path, headers=headers(rid))).json()["status"] == "running"
            duplicate = await client.post("/desktop/chat", headers=headers(rid), json={"message": "virtual send"})
            assert (duplicate.status_code, duplicate.json()["detail"]) == (409, "in_flight")
        finally:
            release.set()
        await asyncio.wait_for(finished.wait(), 2)
        receipt = (await client.get(path, headers=headers(rid))).json()
        assert receipt["status"] == "completed"
        assert receipt["result"]["msg_id"] == "wire-1"
        assert receipt["result"]["reply"] == "Fixture canonical reply."
        assert receipt["result"]["request_id"] == rid
        assert (await client.get(path, headers=headers(rid, "fixture-other"))).json()["status"] == "unknown"
        assert (await client.get(path)).status_code == 401
        replay = await client.post("/desktop/chat", headers=headers(rid), json={"message": "virtual send"})
        assert replay.json()["msg_id"] == "wire-1"
        conflict = await client.post("/desktop/chat", headers=headers(rid), json={"message": "different request"})
        assert conflict.status_code == 409
    assert effects == [("virtual send", "desktop", rid)]


async def test_final_reply_failure_does_not_replay_executed_tool(app, monkeypatch):
    effects = []
    rid = request_id()

    async def execute(message, channel, **kwargs):
        effects.append(message)
        raise HTTPException(502, "fixture_final_generation_failed")

    monkeypatch.setattr(chat, "run_owner_chat_turn", execute)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        path = f"/desktop/chat/requests/{rid}"
        assert (await client.get(path, headers=headers(rid))).json()["status"] == "unknown"
        args = {"headers": headers(rid), "json": {"message": "virtual send"}}
        assert (await client.post("/desktop/chat", **args)).status_code == 502
        receipt = (await client.get(path, headers=headers(rid))).json()
        assert receipt == {"request_id": rid, "status": "failed"}
        assert (await client.post("/desktop/chat", **args)).status_code == 409
    assert effects == ["virtual send"]


async def test_only_a_persisted_preflight_rejection_authorizes_new_request(app, monkeypatch):
    rid = request_id()
    blocked = True
    calls = []

    def preflight(owner):
        if blocked:
            raise HTTPException(409, "fixture_dream_active")

    async def execute(message, channel, **kwargs):
        calls.append(message)
        return {"reply": "ok", "turn_id": "confirmed"}

    monkeypatch.setattr(chat, "_check_reality_not_in_dream", preflight)
    monkeypatch.setattr(chat, "run_owner_chat_turn", execute)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        args = {"headers": headers(rid), "json": {"message": "virtual send"}}
        rejected = await client.post("/desktop/chat", **args)
        assert rejected.headers["X-Presence-Chat-Rejected"] == "1"
        assert (await client.get(f"/desktop/chat/requests/{rid}", headers=headers(rid))).json()["status"] == "not_executed"
        blocked = False
        assert (await client.post("/desktop/chat", **args)).status_code == 409
        args["headers"] = headers(request_id())
        assert (await client.post("/desktop/chat", **args)).status_code == 200
    assert calls == ["virtual send"]


async def test_receipt_write_failure_after_reply_keeps_execution_unknown(app, monkeypatch):
    from core import owner_turn_receipts

    rid = request_id()
    effects = []
    write = owner_turn_receipts.safe_write_json

    def fail_completion(path, row):
        return False if row["status"] == "completed" else write(path, row)

    async def execute(message, channel, **kwargs):
        effects.append(message)
        return {"reply": "Delivered.", "turn_id": "already-delivered"}

    monkeypatch.setattr(owner_turn_receipts, "safe_write_json", fail_completion)
    monkeypatch.setattr(chat, "run_owner_chat_turn", execute)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        args = {"headers": headers(rid), "json": {"message": "virtual write"}}
        assert (await client.post("/desktop/chat", **args)).status_code == 500
        status = await client.get(f"/desktop/chat/requests/{rid}", headers=headers(rid))
        assert status.json()["status"] == "interrupted_unknown"
        assert (await client.post("/desktop/chat", **args)).status_code == 409
    assert effects == ["virtual write"]


async def test_upload_reserves_before_ingesting_file(app, monkeypatch):
    from core import media_processor

    rid = request_id()
    seen = []

    async def ingest(data, filename, **kwargs):
        seen.append((data, filename))
        return "fixture document", None

    async def turn(message, channel, *, request_id, **kwargs):
        assert request_id == rid
        return {"reply": "Read it.", "turn_id": "upload-turn", "msg_id": "upload-wire"}

    monkeypatch.setattr(media_processor, "ingest_file_bytes", ingest)
    monkeypatch.setattr(media_processor, "document_upload_context", lambda *a, **kw: "fixture document")
    monkeypatch.setattr(chat, "run_owner_chat_turn", turn)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        args = {"headers": headers(rid), "files": {"file": ("fixture.txt", b"public fixture")}}
        assert (await client.post("/upload/ingest", **args)).status_code == 200
        assert (await client.post("/upload/ingest", **args)).status_code == 409
    assert seen == [(b"public fixture", "fixture.txt")]


async def test_legacy_text_ingress_still_works_without_receipt_header(app, monkeypatch):
    async def execute(message, channel, **kwargs):
        assert "request_id" not in kwargs
        return {"reply": message, "turn_id": "legacy-turn"}

    monkeypatch.setattr(chat, "run_owner_chat_turn", execute)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/desktop/chat", headers={"Authorization": "Bearer fixture-desktop"},
                                     json={"message": "Legacy fixture"})
    assert response.json()["reply"] == "Legacy fixture"
