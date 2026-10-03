import pytest
import sys
import wave
from io import BytesIO
from types import SimpleNamespace

from core.output import voice_adapter


def _pcm_wav(*, frames: int = 20, framerate: int = 100, channels: int = 1, sample_width: int = 2) -> bytes:
    output = BytesIO()
    with wave.open(output, "wb") as target:
        target.setnchannels(channels)
        target.setsampwidth(sample_width)
        target.setframerate(framerate)
        target.writeframes(b"\x01" * frames * channels * sample_width)
    return output.getvalue()


def test_legacy_gsv_fields_are_mapped_without_new_provider_block():
    provider, cfg = voice_adapter.get_provider_config({
        "api_url": "http://127.0.0.1:9872",
        "ref_audio": "voice.wav",
        "speed": 1.1,
    })

    assert provider == "gsv"
    assert cfg == {
        "api_url": "http://127.0.0.1:9872",
        "ref_audio": "voice.wav",
        "speed": 1.1,
    }


def test_new_provider_block_overrides_legacy_gsv_fields():
    provider, cfg = voice_adapter.get_provider_config({
        "provider": "gpt_sovits",
        "api_url": "http://legacy",
        "ref_audio": "legacy.wav",
        "providers": {"gsv": {"api_url": "http://new", "ref_audio": "new.wav"}},
    })

    assert provider == "gsv"
    assert cfg["api_url"] == "http://new"
    assert cfg["ref_audio"] == "new.wav"


def test_gsv_model_paths_use_explicit_values_or_default_base_models():
    assert voice_adapter._gsv_model_target(
        {"gpt_model_path": "custom.ckpt"},
        "gpt_model_path",
        "gpt_model_fallback",
        voice_adapter._DEFAULT_GPT_MODEL,
    ) == "custom.ckpt"
    assert voice_adapter._gsv_model_target(
        {},
        "sovits_model_path",
        "sovits_model_fallback",
        voice_adapter._DEFAULT_SOVITS_MODEL,
    ) == voice_adapter._DEFAULT_SOVITS_MODEL


def test_local_gsv_model_paths_are_absolute_for_external_server(tmp_path, monkeypatch):
    monkeypatch.setattr(voice_adapter, "_PROJECT_ROOT", tmp_path)
    model = tmp_path / "userdata" / "characters" / "authored" / "sample.ckpt"
    model.parent.mkdir(parents=True, exist_ok=True)
    model.write_bytes(b"weight")
    assert voice_adapter._resolve_gsv_model_path(
        r"userdata\characters\authored\sample.ckpt"
    ) == str(model.resolve())


def test_logical_gsv_model_path_is_absolute_for_external_server(tmp_path, monkeypatch):
    from core import userdata_assets

    model = tmp_path / "sample.ckpt"
    model.write_bytes(b"weight")
    monkeypatch.setattr(userdata_assets, "resolve_asset_path", lambda **kwargs: model.relative_to(tmp_path))
    monkeypatch.chdir(tmp_path)
    assert voice_adapter._resolve_gsv_model_path(
        "sample", char_id="example", category="gpt_model"
    ) == str(model.resolve())


def test_gsv_version_routes_require_a_complete_pair_and_keep_legacy_auto():
    cfg = {
        "api_url": "http://127.0.0.1:9872", "ref_audio": "sample.wav",
        "gpt_model_path": "old.ckpt", "sovits_model_path": "old.pth",
        "gsv_version": "v2ProPlus",
        "version_models": {"v2ProPlus": {"gpt_model_path": "plus.ckpt", "sovits_model_path": "plus.pth"}},
    }
    assert voice_adapter.selected_gsv_models(cfg) == ("plus.ckpt", "plus.pth")
    assert voice_adapter.get_provider_status(cfg)["ready"] is True
    cfg["gsv_version"] = "v2"
    assert voice_adapter.selected_gsv_models(cfg) is None
    assert voice_adapter.get_provider_status(cfg)["ready"] is False
    cfg["gsv_version"] = "auto"
    assert voice_adapter.selected_gsv_models(cfg) == ("old.ckpt", "old.pth")


def test_gsv_segments_clean_newline_forms_invalid_characters_and_route_languages():
    segments = voice_adapter.split_gsv_segments(
        "第一句。\\nSecond line! /n第三句\u200b",
        {},
    )

    assert segments == [
        ("第一句。", "中文"),
        ("Second line!", "英文"),
        ("第三句", "中文"),
    ]


def test_gsv_segments_only_use_comma_or_dash_when_a_sentence_is_too_long():
    short = voice_adapter.split_gsv_segments("这一句，有停顿——但并不需要被拆开。", {})
    long = voice_adapter.split_gsv_segments("甲，乙，丙，丁，戊，己，庚，辛，壬，癸。", {"segment_max_chars": 12})

    assert short == [("这一句，有停顿——但并不需要被拆开。", "中文")]
    assert len(long) > 1
    assert "，" in long[0][0]


def test_pcm_wav_segments_are_joined_with_silence_and_incompatible_audio_is_rejected():
    joined = voice_adapter._join_pcm_wavs([_pcm_wav(), _pcm_wav()], 0.25)

    assert joined is not None
    with wave.open(BytesIO(joined), "rb") as source:
        assert source.getnframes() == 20 + 25 + 20
    assert voice_adapter._join_pcm_wavs([_pcm_wav(), b"not-a-wav"], 0.25) is None


@pytest.mark.asyncio
async def test_gsv_switches_models_before_synthesis(tmp_path, monkeypatch):
    reference = tmp_path / "reference.wav"
    output = tmp_path / "output.wav"
    reference.write_bytes(b"reference")
    output.write_bytes(b"wav")
    calls = []

    class FakeClient:
        def __init__(self, api_url):
            assert api_url == "http://gsv-test"

        def predict(self, **kwargs):
            calls.append(kwargs)
            return str(output) if kwargs["api_name"] == "/get_tts_wav" else None

    monkeypatch.setitem(sys.modules, "gradio_client", SimpleNamespace(Client=FakeClient, handle_file=lambda path: path))
    voice_adapter._GSV_ACTIVE_MODELS.pop("http://gsv-test", None)

    audio = await voice_adapter.GsvProvider().synthesize(
        "你好。",
        "neutral",
        {
            "api_url": "http://gsv-test",
            "ref_audio": str(reference),
            "gpt_model_path": "custom.ckpt",
            "sovits_model_path": "custom.pth",
        },
    )

    assert audio == b"wav"
    assert calls[0] == {"sovits_path": "custom.pth", "api_name": "/change_sovits_weights"}
    assert calls[1] == {"gpt_path": "custom.ckpt", "api_name": "/change_gpt_weights"}
    assert calls[2]["api_name"] == "/get_tts_wav"

    calls.clear()
    await voice_adapter.GsvProvider().synthesize("你好。", "neutral", {
        "api_url": "http://gsv-test", "ref_audio": str(reference),
        "gpt_model_path": "custom.ckpt", "sovits_model_path": "custom.pth",
        "prompt_text": "旧参考句", "ref_free": True,
    })
    synthesis = next(call for call in calls if call["api_name"] == "/get_tts_wav")
    assert synthesis["ref_free"] is True
    assert synthesis["prompt_text"] == ""

    calls.clear()
    audio = await voice_adapter.GsvProvider().synthesize("你好。", "neutral", {
        "api_url": "http://gsv-test", "ref_audio": str(reference),
        "gpt_model_path": "custom.ckpt", "sovits_model_path": "custom.pth",
        "gsv_version": "v2Pro", "version_models": {
            "v2Pro": {"gpt_model_path": "pro.ckpt", "sovits_model_path": "pro.pth"},
        },
    })
    assert audio == b"wav"
    assert calls[0] == {"sovits_path": "pro.pth", "api_name": "/change_sovits_weights"}
    assert calls[1] == {"gpt_path": "pro.ckpt", "api_name": "/change_gpt_weights"}


@pytest.mark.asyncio
async def test_gsv_synthesizes_each_language_segment_without_internal_cutting(tmp_path, monkeypatch):
    reference = tmp_path / "reference.wav"
    output = tmp_path / "output.wav"
    reference.write_bytes(b"reference")
    output.write_bytes(_pcm_wav())
    calls = []

    class FakeClient:
        def __init__(self, api_url):
            assert api_url == "http://gsv-segments"

        def predict(self, **kwargs):
            calls.append(kwargs)
            return str(output) if kwargs["api_name"] == "/get_tts_wav" else None

    monkeypatch.setitem(sys.modules, "gradio_client", SimpleNamespace(Client=FakeClient, handle_file=lambda path: path))
    voice_adapter._GSV_ACTIVE_MODELS.pop("http://gsv-segments", None)

    audio = await voice_adapter.GsvProvider().synthesize(
        "中文 hello。再见。",
        "neutral",
        {"api_url": "http://gsv-segments", "ref_audio": str(reference)},
    )

    assert audio is not None
    synthesis = [call for call in calls if call["api_name"] == "/get_tts_wav"]
    assert [(call["text"], call["text_language"]) for call in synthesis] == [
        ("中文", "中文"), ("hello.", "英文"), ("再见。", "中文"),
    ]
    assert all(call["how_to_cut"] == "不切" for call in synthesis)


@pytest.mark.asyncio
async def test_local_tts_hands_shared_lock_to_vision_between_segments(tmp_path, monkeypatch):
    import asyncio
    from core import video_call

    reference = tmp_path / "reference.wav"
    output = tmp_path / "output.wav"
    reference.write_bytes(b"reference")
    output.write_bytes(_pcm_wav())
    monkeypatch.setattr(video_call, "_local_resource", asyncio.Lock())
    order = []

    class FakeClient:
        def __init__(self, api_url):
            pass

        def predict(self, **kwargs):
            if kwargs["api_name"] == "/get_tts_wav":
                order.append("tts:" + kwargs["text"])
                return str(output)
            return None

    monkeypatch.setitem(sys.modules, "gradio_client", SimpleNamespace(Client=FakeClient, handle_file=lambda path: path))
    voice_adapter._GSV_ACTIVE_MODELS.pop("http://gsv-yield", None)

    async def vision_frame():
        async with video_call.local_resource():
            order.append("vision")

    async def tts():
        async with voice_adapter._hold_local_resource():
            return await voice_adapter.GsvProvider().synthesize(
                "第一句。第二句。第三句。", "neutral",
                {"api_url": "http://gsv-yield", "ref_audio": str(reference)})

    speaking = asyncio.create_task(tts())
    await asyncio.sleep(0)
    frame = asyncio.create_task(vision_frame())
    assert await speaking is not None
    await frame
    # The frame ran in a sentence gap rather than after the whole reply.
    assert order.index("vision") < len(order) - 1
    assert sum(item.startswith("tts:") for item in order) == 3


def test_openai_compatible_without_base_url_not_ready_and_does_not_leak_secret():
    cfg = {
        "provider": "openai_compatible",
        "providers": {"openai_compatible": {"api_key": "secret", "model": "voice-model"}},
    }

    status = voice_adapter.get_provider_status(cfg)

    assert status["provider"] == "openai_compatible"
    assert not status["ready"]
    assert status["api_key_configured"]
    assert "base_url" in status["reason"]
    assert "api_key" not in voice_adapter.get_safe_provider_params(cfg)


def test_openai_compatible_with_base_url_is_ready():
    cfg = {
        "provider": "openai_compatible",
        "providers": {"openai_compatible": {"base_url": "https://example.com/v1", "api_key": "secret"}},
    }

    status = voice_adapter.get_provider_status(cfg)

    assert status["ready"]
    assert status["reason"] == ""


@pytest.mark.asyncio
async def test_openai_compatible_synthesize_posts_expected_payload(monkeypatch):
    from core.output.voice_adapter import OpenAICompatibleProvider

    captured = {}

    class FakeResp:
        status = 200

        async def read(self):
            return b"audio-bytes"

        async def text(self):
            return ""

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    class FakeSession:
        def post(self, url, json=None, headers=None):
            captured.update(url=url, json=json, headers=headers)
            return FakeResp()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    import aiohttp
    monkeypatch.setattr(aiohttp, "ClientSession", lambda timeout=None: FakeSession())

    provider = OpenAICompatibleProvider()
    cfg = {
        "base_url": "https://example.com/v1",
        "api_key": "secret",
        "model": "tts-1",
        "voice": "alloy",
        "emotion_enabled": True,
        "emotions": {"happy": {"voice": "shimmer", "speed": 1.1}},
    }
    audio = await provider.synthesize("你好", "happy", cfg)

    assert audio == b"audio-bytes"
    assert captured["url"] == "https://example.com/v1/audio/speech"
    assert captured["json"]["voice"] == "shimmer"
    assert captured["json"]["speed"] == 1.1
    assert captured["headers"]["Authorization"] == "Bearer secret"


@pytest.mark.asyncio
async def test_openai_compatible_synthesize_without_base_url_returns_none():
    from core.output.voice_adapter import OpenAICompatibleProvider

    provider = OpenAICompatibleProvider()
    assert await provider.synthesize("hi", "neutral", {}) is None


@pytest.mark.asyncio
async def test_synthesize_dispatches_to_selected_provider_and_records_result(monkeypatch):
    captured = {}

    class FakeProvider:
        async def synthesize(self, text, emotion, cfg):
            captured.update(text=text, emotion=emotion, cfg=cfg)
            return b"wav"

    monkeypatch.setattr(voice_adapter, "get_provider_config", lambda cfg=None: ("gsv", {"ref_audio": "x.wav"}))
    monkeypatch.setitem(voice_adapter._PROVIDERS, "gsv", FakeProvider())
    monkeypatch.setattr("core.api_call_log.append", lambda **kwargs: captured.update(log=kwargs))

    audio = await voice_adapter.synthesize("hello", "gentle")

    assert audio == b"wav"
    assert captured["text"] == "hello"
    assert captured["emotion"] == "gentle"
    assert captured["log"]["caller"] == "tts"
    assert captured["log"]["ok"] is True


@pytest.mark.asyncio
async def test_unknown_provider_is_recorded_as_failed_call(monkeypatch):
    captured = {}
    monkeypatch.setattr(voice_adapter, "get_provider_config", lambda cfg=None: ("unknown", {}))
    monkeypatch.setattr("core.api_call_log.append", lambda **kwargs: captured.update(kwargs))

    assert await voice_adapter.synthesize("hello") is None
    assert captured["ok"] is False
    assert captured["output_hint"] == "unsupported_provider"


def _reset_runtime():
    voice_adapter._TTS_RUNTIME.update(pending=0, dropped=0, current=None, last_error=None, last_error_at=None)
    voice_adapter._TTS_RUNTIME["unhealthy_urls"] = {}


@pytest.mark.asyncio
async def test_gsv_hang_times_out_releases_lock_and_recovers(tmp_path, monkeypatch):
    import threading
    from core import video_call
    import asyncio

    _reset_runtime()
    reference = tmp_path / "reference.wav"
    output = tmp_path / "output.wav"
    reference.write_bytes(b"reference")
    output.write_bytes(_pcm_wav())
    monkeypatch.setattr(video_call, "_local_resource", asyncio.Lock())
    release = threading.Event()
    state = {"hang": True}

    class FakeClient:
        def __init__(self, api_url):
            pass

        def predict(self, **kwargs):
            if kwargs["api_name"] == "/get_tts_wav":
                if state["hang"]:
                    release.wait(5)
                return str(output)
            return None

    monkeypatch.setitem(sys.modules, "gradio_client", SimpleNamespace(Client=FakeClient, handle_file=lambda p: p))
    monkeypatch.setattr(voice_adapter, "_probe_gsv_sync", lambda url: True)
    url = "http://127.0.0.1:59999"
    voice_adapter._GSV_ACTIVE_MODELS[url] = ("a", "b")
    cfg = {"api_url": url, "ref_audio": str(reference), "segment_timeout_seconds": 0.2}

    with pytest.raises(voice_adapter.GsvTimeoutError):
        async with voice_adapter._hold_local_resource():
            await voice_adapter.GsvProvider().synthesize("你好。", "neutral", cfg)
    release.set()
    assert not voice_adapter._GSV_SYNTHESIS_LOCK.locked()
    assert not video_call.local_resource().locked()
    assert url not in voice_adapter._GSV_ACTIVE_MODELS
    status = voice_adapter.get_tts_runtime_status()
    assert status["healthy"] is False and "GsvTimeoutError" in status["last_error"]

    state["hang"] = False
    async with voice_adapter._hold_local_resource():
        assert await voice_adapter.GsvProvider().synthesize("你好。", "neutral", cfg) is not None
    assert voice_adapter.get_tts_runtime_status()["healthy"] is True


@pytest.mark.asyncio
async def test_cancel_during_yield_does_not_release_foreign_lock(tmp_path, monkeypatch):
    import asyncio
    from core import video_call

    monkeypatch.setattr(video_call, "_local_resource", asyncio.Lock())
    lock = video_call.local_resource()

    async def speaker():
        async with voice_adapter._hold_local_resource():
            await asyncio.sleep(0.05)
            await voice_adapter._yield_shared_local_resource()

    async def vision_holder():
        async with lock:
            await asyncio.sleep(0.3)

    task = asyncio.create_task(speaker())
    await asyncio.sleep(0)
    vision = asyncio.create_task(vision_holder())
    await asyncio.sleep(0.15)  # speaker yielded, vision holds the lock
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert lock.locked()  # vision's lock was not released by the cancelled speaker
    await vision
    assert not lock.locked()


@pytest.mark.asyncio
async def test_full_queue_drops_request_without_hanging(monkeypatch):
    _reset_runtime()
    voice_adapter._TTS_RUNTIME["pending"] = 4
    called = []

    class Never:
        async def synthesize(self, *a):
            called.append(1)

    monkeypatch.setitem(voice_adapter._PROVIDERS, "gsv", Never())
    monkeypatch.setattr(voice_adapter, "resolve_tts_config", lambda c=None: {"provider": "gsv"})
    assert await voice_adapter.synthesize("你好。") is None
    assert not called and voice_adapter._TTS_RUNTIME["dropped"] == 1
    _reset_runtime()
