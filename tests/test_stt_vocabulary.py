import pytest

from core import stt_vocabulary as vocabulary


CFG = {"stt_vocabulary": {"enabled": True, "entries": [
    {"heard": "mu xing", "canonical": "暮星"},
    {"heard": "木星", "canonical": "暮星"},
]}}


def test_vocabulary_hints_and_exact_corrections():
    assert "暮星" in vocabulary.prompt(CFG)
    assert vocabulary.hotwords(CFG) == "暮星"
    assert vocabulary.correct("我叫 mu xing，也不是木星", CFG) == "我叫 暮星，也不是暮星"
    assert vocabulary.correct("amuxing", CFG) == "amuxing"
    assert vocabulary.correct("木星", {"stt_vocabulary": {"enabled": False, "entries": CFG["stt_vocabulary"]["entries"]}}) == "木星"


def test_vocabulary_rejects_unbounded_or_injectable_entries():
    with pytest.raises(ValueError):
        vocabulary.validate({"enabled": True, "entries": [{"heard": "a", "canonical": "暮星"}]})
    with pytest.raises(ValueError):
        vocabulary.validate({"enabled": True, "entries": [{"heard": "mu\nxing", "canonical": "暮星"}]})
    with pytest.raises(ValueError):
        vocabulary.validate({"enabled": True, "entries": [{"heard": "mu xing", "canonical": "暮星"}] * 33})


def test_local_whisper_receives_hints_and_keeps_model_name_out_of_internal_key(monkeypatch):
    from admin.routers import transcribe
    from core import config_loader

    monkeypatch.setattr(config_loader, "get_config", lambda: CFG)
    class Model:
        def transcribe(self, _path, **kwargs):
            assert "暮星" in kwargs["initial_prompt"]
            assert kwargs["hotwords"] == "暮星"
            return ([type("Segment", (), {"text": "我叫 mu xing"})()], None)
    monkeypatch.setattr(transcribe, "_stt_backend", ("faster_whisper", Model()))
    assert transcribe._transcribe_sync("fixture.wav") == "我叫 暮星"


def test_local_whisper_keeps_short_speech_with_moderate_confidence(monkeypatch):
    from admin.routers import transcribe

    class Model:
        def transcribe(self, _path, **_kwargs):
            segment = type("Segment", (), {
                "text": "你好", "no_speech_prob": 0.65, "avg_logprob": -1.2,
            })()
            return ([segment], None)

    monkeypatch.setattr(transcribe, "_stt_backend", ("faster_whisper", Model()))
    assert transcribe._transcribe_sync("fixture.wav") == "你好"


@pytest.mark.asyncio
async def test_continuous_voice_receipt_uses_only_transcript_contained_in_message(monkeypatch):
    from core import audio_perception

    checked = []
    monkeypatch.setattr(audio_perception, "consume_receipt", lambda key, text, channel: checked.append((key, text, channel)) or None)

    @audio_perception.voice_context("desktop")
    async def endpoint(*, body):
        return True

    assert await endpoint(body={"message": "先前一句 新识别的语句 补充文字", "audio_perception_id": "one", "audio_perception_text": "新识别的语句"})
    assert await endpoint(body={"message": "先前一句", "audio_perception_id": "two", "audio_perception_text": "伪造的语句"})
    assert checked == [("one", "新识别的语句", "desktop"), ("two", "先前一句", "desktop")]


@pytest.mark.asyncio
async def test_audio_receipt_reports_prompt_injection_only_when_built(monkeypatch):
    from core import audio_perception

    monkeypatch.setattr(audio_perception, "consume_receipt", lambda *_args: {"tone": "unclear", "acoustic": {"quality": "insufficient"}})

    @audio_perception.voice_context("desktop")
    async def endpoint(*, body):
        assert audio_perception.prompt_hint()["_layer"] == "3.8_audio_impression"
        return {"reply": "ok"}

    response = await endpoint(body={"message": "转写文字", "audio_perception_id": "receipt"})
    assert response["audio_perception_applied"] is True
