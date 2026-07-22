import pytest

from core.output import voice_adapter


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


def test_reserved_provider_is_visible_but_not_claimed_ready_or_leaks_secret():
    cfg = {
        "provider": "openai_compatible",
        "providers": {"openai_compatible": {"api_key": "secret", "model": "voice-model"}},
    }

    status = voice_adapter.get_provider_status(cfg)

    assert status["provider"] == "openai_compatible"
    assert not status["ready"]
    assert status["api_key_configured"]
    assert "reserved" in status["reason"]
    assert "api_key" not in voice_adapter.get_safe_provider_params(cfg)


@pytest.mark.asyncio
async def test_synthesize_dispatches_to_selected_provider_and_records_result(monkeypatch):
    captured = {}

    class FakeProvider:
        async def synthesize(self, text, emotion, cfg):
            captured.update(text=text, emotion=emotion, cfg=cfg)
            return b"wav"

    monkeypatch.setattr(voice_adapter, "get_provider_config", lambda: ("gsv", {"ref_audio": "x.wav"}))
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
    monkeypatch.setattr(voice_adapter, "get_provider_config", lambda: ("unknown", {}))
    monkeypatch.setattr("core.api_call_log.append", lambda **kwargs: captured.update(kwargs))

    assert await voice_adapter.synthesize("hello") is None
    assert captured["ok"] is False
    assert captured["output_hint"] == "unsupported_provider"
