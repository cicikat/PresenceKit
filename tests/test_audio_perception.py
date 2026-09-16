import asyncio
from copy import deepcopy
import io
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.datastructures import UploadFile

from core import audio_perception as audio

CFG = {"scheduler": {"owner_id": "owner"}, "stt_presets": {
    "enabled": True, "presets": {"speech": {"base_url": "https://stt.example/v1",
    "model": "transcriber", "api_key": "fixture-secret", "timeout_seconds": 1}},
    "routes": {"voice_message": "speech"}}}


@pytest.fixture
def configured(monkeypatch):
    cfg = deepcopy(CFG)
    monkeypatch.setattr(audio, "get_config", lambda: cfg)
    monkeypatch.setattr("core.config_loader.get_config", lambda: cfg)
    monkeypatch.setattr("core.scheduler.loop._active_char_id_or_none", lambda: "fixture_character")
    audio._receipts.clear()
    return cfg


@pytest.mark.asyncio
async def test_no_audio_disabled_and_binary_boundaries(configured, monkeypatch):
    request = AsyncMock()
    monkeypatch.setattr(audio, "_request", request)
    assert await audio.ingest_audio_bytes(b"", "x.wav") is None
    assert await audio.ingest_audio_bytes(b"x", "x.txt") is None
    configured["stt_presets"]["enabled"] = False
    assert await audio.ingest_audio_bytes(b"x", "x.wav") is None
    request.assert_not_called()
    assert audio.prompt_hint() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("tone", ["calm", "tired", "bright", "tense", "unclear", "angry", {"injection": True}])
async def test_tone_enum_and_context(configured, monkeypatch, tone):
    monkeypatch.setattr(audio, "_request", AsyncMock(return_value={"text": " hello ", "tone": tone}))
    result = await audio.ingest_audio_bytes(b"fixture", "x.ogg")
    assert result["text"] == "hello"
    assert result["tone"] == (tone if isinstance(tone, str) and tone in audio.TONES else "unclear")
    with audio.impression(result):
        hint = audio.prompt_hint()
        assert hint["_layer"] == "3.8_audio_impression"
        assert "不是情绪" in hint["content"]
    assert audio.prompt_hint() is None


@pytest.mark.asyncio
async def test_failure_and_timeout_fail_open(configured, monkeypatch):
    monkeypatch.setattr(audio, "_request", AsyncMock(side_effect=RuntimeError("private-provider-error")))
    assert await audio.ingest_audio_bytes(b"x", "x.wav") is None
    original = asyncio.wait_for
    async def bounded(awaitable, timeout):
        return await original(awaitable, .01)
    async def slow(*args):
        await asyncio.sleep(10)
    monkeypatch.setattr(audio, "_request", slow)
    monkeypatch.setattr(audio.asyncio, "wait_for", bounded)
    assert await audio.ingest_audio_bytes(b"x", "x.wav") is None


def test_receipt_scope_text_ttl_and_single_use(configured, monkeypatch):
    result = {"text": "hello", "tone": "calm"}
    key = audio.issue_receipt(result, "desktop")
    assert audio.consume_receipt(key, "hello", "mobile") is None
    key = audio.issue_receipt(result, "desktop")
    assert audio.consume_receipt(key, "edited", "desktop") is None
    key = audio.issue_receipt(result, "desktop")
    assert audio.consume_receipt(key, "hello", "desktop") == {"tone": "calm"}
    assert audio.consume_receipt(key, "hello", "desktop") is None
    key = audio.issue_receipt(result, "desktop")
    monkeypatch.setattr(audio.time, "monotonic", lambda: 10**20)
    assert audio.consume_receipt(key, "hello", "desktop") is None


def test_snapshot_masks_secrets(configured):
    state = audio.snapshot()
    assert state["effective"]
    assert "fixture-secret" not in str(state)
    assert state["presets"]["speech"]["api_key_configured"]


@pytest.mark.asyncio
@pytest.mark.parametrize("oversize", [False, True])
async def test_transport_collects_chunked_json_with_size_limit(configured, monkeypatch, oversize):
    class Content:
        async def iter_chunked(self, size):
            yield b'{"text": "hello", '
            yield b'x' * (256 * 1024) if oversize else b'"tone": "calm"}'

    class Response:
        content = Content()

        def raise_for_status(self):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    class Session(Response):
        def __init__(self, **kwargs):
            pass

        def post(self, url, **kwargs):
            assert url == "https://stt.example/v1/audio/transcriptions"
            assert kwargs["allow_redirects"] is False
            return Response()

    monkeypatch.setattr(audio.aiohttp, "ClientSession", Session)
    result = await audio.ingest_audio_bytes(b"fixture", "x.wav")
    assert result == (None if oversize else {"text": "hello", "tone": "calm"})


def test_disabling_audio_revokes_pending_receipt(configured):
    key = audio.issue_receipt({"text": "hello", "tone": "calm"}, "desktop")
    configured["stt_presets"]["enabled"] = False
    assert audio.consume_receipt(key, "hello", "desktop") is None


@pytest.mark.asyncio
async def test_transcribe_compatible_text_and_receipt(configured, monkeypatch):
    from admin.routers.transcribe import transcribe_audio
    monkeypatch.setattr(audio, "_request", AsyncMock(return_value={"text": "hello", "tone": "tired"}))
    value = await transcribe_audio(UploadFile(io.BytesIO(b"x"), filename="x.wav"), "desktop", {})
    assert value["text"] == "hello"
    assert audio.consume_receipt(value["audio_perception_id"], "hello", "desktop") == {"tone": "tired"}
    configured["stt_presets"]["enabled"] = False
    with pytest.raises(HTTPException) as error:
        await transcribe_audio(UploadFile(io.BytesIO(b"x"), filename="x.wav"), "desktop", {})
    assert error.value.status_code == 422


@pytest.mark.asyncio
async def test_voice_decorator_plain_text_and_reset(configured):
    @audio.voice_context("desktop")
    async def endpoint(body):
        return audio.prompt_hint()
    assert await endpoint({"message": "hello"}) is None
    key = audio.issue_receipt({"text": "hello", "tone": "bright"}, "desktop")
    assert (await endpoint({"message": "hello", "audio_perception_id": key}))["_layer"] == "3.8_audio_impression"
    assert audio.prompt_hint() is None


@pytest.mark.asyncio
async def test_settings_save_route_key_preservation(configured, monkeypatch):
    from admin.routers import settings_llm as admin
    monkeypatch.setattr(admin, "read_config_file", lambda _: deepcopy(configured))
    def save(_, cfg):
        configured.clear()
        configured.update(cfg)
    monkeypatch.setattr(admin, "write_config_file", save)
    monkeypatch.setattr("core.config_loader.reload_config", lambda: None)
    await admin.save_stt_preset("speech", admin.SttConnection(base_url="https://stt.example/v1", model="other"), {})
    assert configured["stt_presets"]["presets"]["speech"]["api_key"] == "fixture-secret"
    assert "fixture-secret" not in str(await admin.get_stt_presets({}))
    with pytest.raises(HTTPException):
        await admin.save_stt_route(admin.SttPurpose(enabled=True, voice_message="missing"), {})


def test_http_scopes_and_legacy_chat_signatures(configured):
    from admin.routers.chat import router as chat
    from admin.routers.mobile import router as mobile
    from admin.routers.settings_llm import router as settings
    app = FastAPI()
    for router in (chat, mobile, settings):
        app.include_router(router)
    client = TestClient(app)
    for path in ("/desktop/chat", "/mobile/chat"):
        assert client.post(path, json={"message": "hello"}).status_code in (401, 403)
    assert client.get("/stt-presets").status_code in (401, 403)


def test_qq_voice_only_is_accepted(monkeypatch):
    from core import qq_adapter
    monkeypatch.setattr(qq_adapter, "is_blacklisted", lambda _: False)
    value = qq_adapter._parse_event({"post_type": "message", "message_type": "private", "user_id": "fixture",
                                    "message": [{"type": "record", "data": {"url": "https://example.test/voice.amr"}}]})
    assert value["audio_url"].endswith("voice.amr")


@pytest.mark.asyncio
async def test_audio_upload_reaches_chat_without_tone_on_failure(configured, monkeypatch):
    from admin.routers import chat
    async def turn(message, channel, **kwargs):
        return {"message": message, "hint": audio.prompt_hint()}
    monkeypatch.setattr(chat, "run_owner_chat_turn", turn)
    monkeypatch.setattr(audio, "_request", AsyncMock(side_effect=TimeoutError()))
    response = await chat.upload_ingest(file=UploadFile(io.BytesIO(b"x"), filename="x.wav"),
                                       files=None, message="hi", channel="desktop", _auth={})
    assert "未能听清" in response["message"]
    assert response["hint"] is None
    monkeypatch.setattr(audio, "_request", AsyncMock(return_value={"text": "hello", "tone": "calm"}))
    response = await chat.upload_ingest(file=UploadFile(io.BytesIO(b"x"), filename="x.wav"),
                                       files=None, message="", channel="desktop", _auth={})
    assert response["message"] == "hello"
    assert response["hint"]["_layer"] == "3.8_audio_impression"
