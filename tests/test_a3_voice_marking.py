"""工单 A3：语音输入标记、ASR 置信度提示、多回执合并、记忆打标。"""
import io
from copy import deepcopy

import pytest
from starlette.datastructures import UploadFile

from core import audio_perception as audio

LEGACY_CFG = {"scheduler": {"owner_id": "owner"}}


@pytest.fixture
def legacy(monkeypatch):
    cfg = deepcopy(LEGACY_CFG)
    monkeypatch.setattr(audio, "get_config", lambda: cfg)
    monkeypatch.setattr("core.config_loader.get_config", lambda: cfg)
    monkeypatch.setattr("core.scheduler.loop._active_char_id_or_none", lambda: "fixture_character")
    audio._receipts.clear()
    return cfg


@pytest.mark.asyncio
async def test_local_stt_issues_marker_receipt_with_quality(legacy, monkeypatch):
    from admin.routers import transcribe as endpoint
    quality = {"avg_logprob_mean": -1.0, "avg_logprob_min": -1.2, "no_speech_max": 0.1, "dropped_segments": 0}
    monkeypatch.setattr(endpoint, "_transcribe_with_quality", lambda _p: ("你好", quality))
    value = await endpoint.transcribe_audio(UploadFile(io.BytesIO(b"x"), filename="x.wav"), "desktop", {})
    assert value["text"] == "你好" and value["asr_quality"] == quality
    got = audio.consume_receipt(value["audio_perception_id"], "你好", "desktop")
    assert got["voice_only"] is True and got["asr_quality"] == quality


def test_prompt_hint_base_and_low_confidence(legacy):
    with audio.impression({"tone": "unclear", "voice_only": True}):
        content = audio.prompt_hint()["content"]
    assert "语音转写" in content and "同音错字" in content
    assert "识别质量偏低" not in content and "听觉印象" not in content
    low = {"avg_logprob_mean": -0.9, "avg_logprob_min": -1.0, "no_speech_max": 0.0, "dropped_segments": 0}
    with audio.impression({"tone": "unclear", "voice_only": True, "asr_quality": low}):
        assert "识别质量偏低" in audio.prompt_hint()["content"]
    dropped = {"avg_logprob_mean": -0.1, "avg_logprob_min": -0.1, "no_speech_max": 0.0, "dropped_segments": 1}
    with audio.impression({"tone": "unclear", "voice_only": True, "asr_quality": dropped}):
        assert "识别质量偏低" in audio.prompt_hint()["content"]


def test_low_confidence_threshold_configurable(legacy):
    q = {"avg_logprob_mean": -0.5, "dropped_segments": 0}
    assert audio.is_low_confidence(q) is False
    legacy["audio_music"] = {"asr_low_confidence_logprob": -0.3}
    assert audio.is_low_confidence(q) is True


@pytest.mark.asyncio
async def test_multi_receipts_merge_worst_quality_and_audit_extras(legacy):
    good = audio.issue_receipt({"text": "前半句", "tone": "unclear", "voice_only": True,
                                "asr_quality": {"avg_logprob_mean": -0.2, "avg_logprob_min": -0.3,
                                                "no_speech_max": 0.1, "dropped_segments": 0}}, "desktop")
    bad = audio.issue_receipt({"text": "后半句", "tone": "unclear", "voice_only": True,
                               "asr_quality": {"avg_logprob_mean": -1.1, "avg_logprob_min": -1.4,
                                               "no_speech_max": 0.4, "dropped_segments": 1}}, "desktop")
    seen = {}

    @audio.voice_context("desktop")
    async def endpoint(body):
        seen["extras"] = audio.current_voice_extras()
        seen["hint"] = audio.prompt_hint()
        return {}

    result = await endpoint({"message": "前半句后半句", "voice_receipt_ids": [good, bad],
                             "voice_receipt_texts": ["前半句", "后半句"]})
    assert result["audio_perception_applied"] is True
    assert seen["extras"] == {"input_modality": "voice", "asr_low_confidence": True}
    assert "识别质量偏低" in seen["hint"]["content"]


@pytest.mark.asyncio
async def test_plain_text_has_no_voice_extras(legacy):
    @audio.voice_context("desktop")
    async def endpoint(body):
        return audio.current_voice_extras()
    assert await endpoint({"message": "hi"}) is None


def test_merge_quality_worst():
    merged = audio.merge_quality([
        {"avg_logprob_mean": -0.2, "avg_logprob_min": -0.3, "no_speech_max": 0.1, "dropped_segments": 0},
        {"avg_logprob_mean": -1.0, "avg_logprob_min": -1.5, "no_speech_max": 0.5, "dropped_segments": 2},
    ])
    assert merged == {"avg_logprob_mean": -1.0, "avg_logprob_min": -1.5, "no_speech_max": 0.5,
                      "dropped_segments": 2}


def test_capture_turn_marks_voice_in_short_term_and_event_log(monkeypatch):
    from core.memory import fixation_pipeline as fp
    calls = {}
    from core.memory import short_term, event_log

    def st_append(uid, role, content, turn_id=None, **kw):
        calls.setdefault("st", []).append((role, kw))
        return True

    def el_append(uid, role, content, *a, **kw):
        calls.setdefault("el", []).append((role, kw))
        return True

    monkeypatch.setattr(short_term, "append", st_append)
    monkeypatch.setattr(event_log, "append", el_append)
    monkeypatch.setattr(fp, "_append_event_ledger", lambda **kw: calls.setdefault("ledger", kw) or {})
    from core.write_envelope import stamp_user_chat
    fp.capture_turn("u", "你好", "嗯", envelope=stamp_user_chat(), audit_extras={"input_modality": "voice", "asr_low_confidence": True},
                    char_id="c")
    user_st = [kw for role, kw in calls["st"] if role == "user"][0]
    user_el = [kw for role, kw in calls["el"] if role == "user"][0]
    assert user_st["input_modality"] == "voice" and user_st["asr_low_confidence"] is True
    assert user_el["input_modality"] == "voice"
    assert calls["ledger"]["input_modality"] == "voice"
    assert all("input_modality" not in kw for role, kw in calls["st"] if role == "assistant")


def test_short_term_and_mid_term_store_marker(monkeypatch, tmp_path):
    from core.memory import short_term
    saved = {}
    monkeypatch.setattr(short_term, "load", lambda *a, **k: [])
    monkeypatch.setattr(short_term, "_save", lambda uid, history, **k: saved.setdefault("h", history) or True)
    short_term.append("u", "user", "语音句", turn_id="t1", char_id="c",
                      input_modality="voice", asr_low_confidence=True)
    assert saved["h"][0]["input_modality"] == "voice" and saved["h"][0]["asr_low_confidence"] is True
