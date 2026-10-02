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


@pytest.mark.asyncio
async def test_request_sends_bare_hotwords_not_template(configured, monkeypatch):
    from core import stt_vocabulary
    monkeypatch.setattr(stt_vocabulary, "settings", lambda config=None: {"enabled": True, "entries": [
        {"heard": "mu xing", "canonical": "暮星"}]})
    captured = {}
    async def fake_request(data, filename, preset):
        return {"text": "hello"}
    monkeypatch.setattr(audio, "aiohttp", audio.aiohttp)
    class FormData:
        def __init__(self):
            self.fields = []
        def add_field(self, name, value, **kwargs):
            self.fields.append((name, value))
    monkeypatch.setattr(audio.aiohttp, "FormData", FormData)
    class Response:
        def raise_for_status(self):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            pass
        content = type("C", (), {"iter_chunked": lambda self, size: _chunks()})()
    async def _chunks():
        yield b'{"text": "hello"}'
    class Session:
        def __init__(self, **kwargs):
            pass
        def post(self, url, data=None, **kwargs):
            captured["fields"] = data.fields
            return Response()
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            pass
    monkeypatch.setattr(audio.aiohttp, "ClientSession", Session)
    await audio.ingest_audio_bytes(b"fixture", "x.wav")
    prompt_fields = [value for name, value in captured["fields"] if name == "prompt"]
    assert prompt_fields == ["暮星"]
    assert "以下是语音中的专有名词" not in str(prompt_fields)


@pytest.mark.asyncio
async def test_remote_prompt_echo_is_treated_as_unheard(configured, monkeypatch):
    from core import stt_vocabulary
    monkeypatch.setattr(stt_vocabulary, "settings", lambda config=None: {"enabled": True, "entries": [
        {"heard": "mu xing", "canonical": "暮星"}]})
    monkeypatch.setattr(audio, "_request", AsyncMock(return_value={"text": "暮星（读音或常见误写：mu xing）"}))
    assert await audio.ingest_audio_bytes(b"fixture", "x.wav") is None


@pytest.mark.asyncio
async def test_remote_clear_speech_is_not_mistaken_for_echo(configured, monkeypatch):
    from core import stt_vocabulary
    monkeypatch.setattr(stt_vocabulary, "settings", lambda config=None: {"enabled": True, "entries": [
        {"heard": "mu xing", "canonical": "暮星"}]})
    monkeypatch.setattr(audio, "_request", AsyncMock(return_value={"text": "今天我们去爬山吧", "tone": "calm"}))
    result = await audio.ingest_audio_bytes(b"fixture", "x.wav")
    assert result["text"] == "今天我们去爬山吧"


@pytest.mark.asyncio
async def test_remote_stt_success_is_logged_to_api_call_log(configured, monkeypatch):
    calls = []
    monkeypatch.setattr("core.api_call_log.append", lambda **kw: calls.append(kw))
    monkeypatch.setattr(audio, "_request", AsyncMock(return_value={"text": "hello", "tone": "calm"}))
    result = await audio.ingest_audio_bytes(b"fixture", "x.wav")
    assert result["text"] == "hello"
    assert len(calls) == 1
    assert calls[0]["caller"] == "stt"
    assert calls[0]["purpose"] == "transcribe_remote"
    assert calls[0]["ok"] is True
    assert calls[0]["duration_ms"] >= 0
    assert "fixture-secret" not in str(calls)


@pytest.mark.asyncio
async def test_remote_stt_failure_is_logged_and_warned_without_leaking_details(configured, monkeypatch, caplog):
    calls = []
    monkeypatch.setattr("core.api_call_log.append", lambda **kw: calls.append(kw))
    monkeypatch.setattr(audio, "_request", AsyncMock(side_effect=RuntimeError("private-provider-error")))
    with caplog.at_level("WARNING"):
        assert await audio.ingest_audio_bytes(b"x", "x.wav") is None
    assert len(calls) == 1
    assert calls[0]["ok"] is False
    assert calls[0]["output_hint"] == "RuntimeError"
    assert "private-provider-error" not in str(calls)
    assert "private-provider-error" not in caplog.text


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
async def test_legacy_transcribe_reports_backend_and_empty_audio(monkeypatch):
    from admin.routers import transcribe as endpoint

    monkeypatch.setattr("core.config_loader.get_config", lambda: {})
    def unavailable(_path):
        raise RuntimeError("STT 未安装")
    monkeypatch.setattr(endpoint, "_transcribe_with_quality", unavailable)
    with pytest.raises(HTTPException) as error:
        await endpoint.transcribe_audio(UploadFile(io.BytesIO(b"audio"), filename="x.wav"), "desktop", {})
    assert error.value.status_code == 503
    monkeypatch.setattr(endpoint, "_transcribe_with_quality", lambda _path: ("", None))
    with pytest.raises(HTTPException) as error:
        await endpoint.transcribe_audio(UploadFile(io.BytesIO(b"audio"), filename="x.wav"), "desktop", {})
    assert error.value.status_code == 422
    assert "没有识别到语音" in error.value.detail


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
    response = await chat.upload_ingest(
        file=UploadFile(io.BytesIO(b"x"), filename="x.wav"),
        files=None, message="hi", channel="desktop", request_id="", _auth={},
    )
    assert "未能听清" in response["message"]
    assert response["hint"] is None
    monkeypatch.setattr(audio, "_request", AsyncMock(return_value={"text": "hello", "tone": "calm"}))
    response = await chat.upload_ingest(
        file=UploadFile(io.BytesIO(b"x"), filename="x.wav"),
        files=None, message="", channel="desktop", request_id="", _auth={},
    )
    assert response["message"] == "hello"
    assert response["hint"]["_layer"] == "3.8_audio_impression"


def _enable_speech_analysis(configured):
    configured.setdefault("audio_music", {})["speech_analysis"] = True


def _pcm_tone_wav(freq_hz=180.0, duration_s=0.8, sr=16000):
    import math
    import struct
    import wave

    n = int(sr * duration_s)
    frames = b"".join(
        struct.pack("<h", int(0.35 * 32767 * math.sin(2 * math.pi * freq_hz * i / sr)))
        for i in range(n)
    )
    buf = io.BytesIO()
    with wave.open(buf, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sr)
        handle.writeframes(frames)
    return buf.getvalue()


@pytest.mark.asyncio
async def test_stt_success_analysis_failure_keeps_text(configured, monkeypatch):
    _enable_speech_analysis(configured)
    monkeypatch.setattr(audio, "_request", AsyncMock(return_value={"text": "hello", "tone": "calm"}))
    async def boom(*args, **kwargs):
        raise RuntimeError("analyzer-down")
    monkeypatch.setattr("core.audio_analysis.analyze_audio", boom)
    result = await audio.ingest_audio_bytes(b"fixture", "x.wav")
    assert result["text"] == "hello"
    assert result["tone"] == "unclear"
    assert result["acoustic"]["analysis_status"] == "failed"
    assert result["provider_tone_hint"] == "calm"
    with audio.impression(result):
        hint = audio.prompt_hint()
        assert hint["_layer"] == "3.8_audio_impression"
        assert "hello" not in hint["content"]
        assert "unclear" in hint["content"]
        assert "供应商旁路" in hint["content"]
        assert "完整音高曲线未注入" in hint["content"]


@pytest.mark.asyncio
async def test_speech_analysis_off_keeps_provider_tone(configured, monkeypatch):
    configured.setdefault("audio_music", {})["speech_analysis"] = False
    monkeypatch.setattr(audio, "_request", AsyncMock(return_value={"text": "hello", "tone": "tired"}))
    called = AsyncMock()
    monkeypatch.setattr("core.audio_analysis.analyze_audio", called)
    result = await audio.ingest_audio_bytes(b"fixture", "x.wav")
    assert result == {"text": "hello", "tone": "tired"}
    called.assert_not_called()


@pytest.mark.asyncio
async def test_speech_analysis_overrides_provider_on_ok_or_unclear(configured, monkeypatch, sandbox):
    _enable_speech_analysis(configured)
    monkeypatch.setattr(audio, "_request", AsyncMock(return_value={"text": "hello", "tone": "bright"}))
    wav = _pcm_tone_wav()
    result = await audio.ingest_audio_bytes(wav, "clip.wav")
    assert result["text"] == "hello"
    assert result["tone"] in audio.TONES
    assert result["acoustic"]["analysis_status"] in {"ok", "partial", "failed", "timeout"}
    assert result["acoustic"]["provider_tone_hint"] == "bright"
    with audio.impression(result):
        content = audio.prompt_hint()["content"]
        assert "不是情绪" in content
        assert "完整音高曲线未注入" in content
        assert "pitch_curve" not in content


def test_receipt_does_not_follow_edited_or_cross_channel(configured):
    result = {
        "text": "hello",
        "tone": "unclear",
        "acoustic": {"analysis_status": "failed", "quality": "insufficient",
                     "impression": "unclear", "analysis_version": "audio-analysis.v0"},
    }
    key = audio.issue_receipt(result, "desktop")
    assert audio.consume_receipt(key, "hello", "mobile") is None
    key = audio.issue_receipt(result, "desktop")
    assert audio.consume_receipt(key, "edited hello", "desktop") is None
    key = audio.issue_receipt(result, "desktop")
    consumed = audio.consume_receipt(key, "hello", "desktop")
    assert consumed["tone"] == "unclear"
    assert consumed["acoustic"]["analysis_status"] == "failed"
    assert audio.consume_receipt(key, "hello", "desktop") is None


def test_receipt_drops_on_char_switch_and_disable(configured, monkeypatch):
    result = {"text": "hello", "tone": "calm"}
    key = audio.issue_receipt(result, "desktop")
    monkeypatch.setattr("core.scheduler.loop._active_char_id_or_none", lambda: "other_character")
    assert audio.consume_receipt(key, "hello", "desktop") is None
    key = audio.issue_receipt(result, "desktop")
    configured["stt_presets"]["enabled"] = False
    assert audio.consume_receipt(key, "hello", "desktop") is None


@pytest.mark.asyncio
async def test_plain_text_has_no_audio_layer(configured):
    @audio.voice_context("desktop")
    async def endpoint(body):
        return audio.prompt_hint()
    assert await endpoint({"message": "just text"}) is None


@pytest.mark.asyncio
async def test_transcribe_hides_acoustic_payload(configured, monkeypatch):
    from admin.routers.transcribe import transcribe_audio
    _enable_speech_analysis(configured)
    monkeypatch.setattr(audio, "_request", AsyncMock(return_value={"text": "hello", "tone": "tired"}))
    monkeypatch.setattr(
        "core.audio_analysis.analyze_audio",
        AsyncMock(return_value={"analysis_status": "failed", "quality": "insufficient",
                                "pitch_summary": {}, "pace": {}, "energy": {}, "voiced_ratio": {}}),
    )
    value = await transcribe_audio(UploadFile(io.BytesIO(b"x"), filename="x.wav"), "desktop", {})
    assert set(value) == {"text", "tone", "audio_perception_id"}
    assert value["text"] == "hello"
    assert value["tone"] == "unclear"
    stored = audio.consume_receipt(value["audio_perception_id"], "hello", "desktop")
    assert stored["acoustic"]["analysis_status"] == "failed"


# ── v1 labels in the perception layer (video-call work order E) ─────────────

def _acoustic(tone, **extra):
    base = {"analysis_status": "ok", "quality": "ok", "median_hz": 200.0, "pace": 3.0,
            "energy_dbfs": -22.0, "voiced_ratio": 0.6, "hf_ratio": None, "variation": None,
            "impression": tone, "impression_quality": "ok", "analysis_version": "audio-analysis.v1"}
    base.update(extra)
    return base


def test_tones_include_v1_labels_and_keep_the_v0_ones():
    assert {"calm", "tired", "bright", "tense", "unclear"} <= audio.TONES
    assert {"unsteady", "breathy", "low_toned"} <= audio.TONES


def test_prompt_explains_new_labels_without_claiming_emotion_or_a_baseline(configured):
    for tone in ("unsteady", "breathy", "low_toned"):
        result = {"text": "hello", "tone": tone, "acoustic": _acoustic(tone, hf_ratio=0.22, variation=0.35)}
        with audio.impression(result):
            hint = audio.prompt_hint()
        content = hint["content"]
        assert hint["_layer"] == "3.8_audio_impression"               # still the one existing layer
        assert audio.TONE_GLOSS[tone] in content
        assert "不是情绪、健康或人格事实" in content
        assert "比平时" not in content
        assert "2.5 kHz 以上能量占比约 22%" in content               # readable-feature line, not an assertion line


def test_low_toned_wording_states_absolute_judgement_only(configured):
    result = {"text": "hello", "tone": "low_toned", "acoustic": _acoustic("low_toned")}
    with audio.impression(result):
        content = audio.prompt_hint()["content"]
    assert "没有个人基线" in content and "比平时" not in content


def test_receipt_carries_the_new_readable_features(configured):
    result = {"text": "hello", "tone": "breathy", "acoustic": _acoustic("breathy", hf_ratio=0.21, variation=0.1)}
    key = audio.issue_receipt(result, "desktop")
    consumed = audio.consume_receipt(key, "hello", "desktop")
    assert consumed["tone"] == "breathy"
    assert consumed["acoustic"]["hf_ratio"] == 0.21 and consumed["acoustic"]["variation"] == 0.1


def test_provider_tone_with_a_new_label_still_cannot_override_failed_analysis():
    compact = audio._compact_acoustic({"analysis_status": "failed", "quality": "noisy"}, "breathy")
    assert compact["impression"] == "unclear"
    assert compact["provider_tone_hint"] == "breathy"      # recorded as a hint only


def test_snapshot_reports_voice_analysis_stats(configured):
    stats = audio.snapshot()["voice_analysis_stats"]
    assert {"impressions", "feature_calls", "feature_ms_avg", "feature_skipped_budget"} <= set(stats)
