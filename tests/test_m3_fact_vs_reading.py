"""工单 M3：用户原话事实 vs 角色解读分开存。"""
import json
import time
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.fixtures.public_assets import TEST_CHAR_ID

CHAR = TEST_CHAR_ID
DAY = 86400.0


@pytest.fixture(autouse=True)
def _llm():
    llm = MagicMock()
    llm.summarize_turn = AsyncMock(return_value="摘要")
    llm.chat = AsyncMock(return_value="{}")
    with patch("core.llm_client", llm, create=True):
        yield llm


def test_midterm_prompt_interpolates_char_name_and_drops_user_subject_rule():
    from core.llm_client import _build_summarize_system
    text = _build_summarize_system("某某角色")
    assert "某某角色" in text
    assert "主语用「用户」" not in text
    assert "用户：" in text and "40" in text


def test_important_facts_prompt_has_no_inference_categories():
    import asyncio
    from core.memory import user_profile
    captured = {}

    async def fake_chat(messages, **kw):
        captured["system"] = messages[0]["content"]
        captured["user"] = messages[1]["content"]
        return "{}"

    llm = MagicMock()
    llm.chat = fake_chat
    with patch("core.llm_client", llm, create=True):
        asyncio.run(user_profile.extract_and_update("m3u", [
            {"role": "user", "content": "我养了一只猫"},
            {"role": "user", "content": "语音转写错字", "input_modality": "voice", "asr_low_confidence": True},
            {"role": "assistant", "content": "好呀"},
        ], char_id=CHAR))
    assert "性格特点" not in captured["system"]
    assert "精神状态" not in captured["system"]
    assert "明确说出" in captured["system"]
    assert "养了一只猫" in captured["user"]
    assert "语音转写错字" not in captured["user"]  # 低置信语音不参与抽取


def _ep(i, day_offset, **kw):
    ts = time.time() - day_offset * DAY
    base = {"id": f"e{i}", "timestamp": ts, "occurred_at": ts, "narrative_summary": f"解读{i}",
            "user_said": [f"原话{i}"], "char_reading": [f"理解{i}"], "emotion_peak": "neutral", "strength": 0.7}
    base.update(kw)
    return base


def _synth(episodes, response):
    import asyncio
    from core.memory.fixation_pipeline import _synthesize_identity
    llm = MagicMock()
    llm.chat = AsyncMock(return_value=json.dumps(response, ensure_ascii=False))
    out = asyncio.run(_synthesize_identity("m3i", {}, episodes, {}, llm, char_id=CHAR))
    return out, llm


def test_identity_evidence_counts_distinct_days_not_llm_claim():
    same_day = [_ep(i, 0) for i in (1, 2, 3)]
    resp = {"trust_pattern": {"text": "她信任较慢", "confidence": 0.9,
                              "evidence_episodes": [1, 2, 3], "evidence_count": 99,
                              "counter_evidence_count": 0}}
    (result, _), llm = _synth(same_day, resp)
    assert result["trust_pattern"]["evidence_count"] == 1
    diff_days = [_ep(i, i) for i in (1, 2, 3)]
    (result, _), _ = _synth(diff_days, resp)
    assert result["trust_pattern"]["evidence_count"] == 3


def test_identity_input_has_user_said_and_dates_but_no_interpretation():
    eps = [_ep(1, 1), _ep(2, 2)]
    resp = {"trust_pattern": {"text": "t", "confidence": 0.5, "evidence_episodes": [1], "counter_evidence_count": 0}}
    _, llm = _synth(eps, resp)
    user_content = llm.chat.call_args.args[0][1]["content"]
    assert "原话1" in user_content and "原话2" in user_content
    assert "理解1" not in user_content and "解读1" not in user_content
    assert datetime.fromtimestamp(eps[0]["occurred_at"]).strftime("%Y-%m-%d") in user_content


def test_identity_skips_low_confidence_voice_episodes():
    eps = [_ep(1, 1, voice_low_confidence=True), _ep(2, 2)]
    resp = {"trust_pattern": {"text": "t", "confidence": 0.5, "evidence_episodes": [1], "counter_evidence_count": 0}}
    _, llm = _synth(eps, resp)
    user_content = llm.chat.call_args.args[0][1]["content"]
    assert "原话1" not in user_content and "原话2" in user_content
    # 全部不合格 → 不调用 LLM
    out, llm2 = _synth([_ep(1, 1, voice_low_confidence=True)], resp)
    assert out == ({}, []) and llm2.chat.await_count == 0


def test_consolidate_trigger_needs_three_distinct_days():
    from core.memory.fixation_pipeline import _should_consolidate
    base = {"strength_accumulated": 0.0, "last_consolidated_at": time.time(), "episodic_since_last": 0}
    assert not _should_consolidate({**base, "high_strength_since_last": 9, "high_strength_days": ["2026-01-01"]})
    assert _should_consolidate({**base, "high_strength_days": ["2026-01-01", "2026-01-02", "2026-01-03"]})


def test_validate_episode_accepts_legacy_raw_facts_only_and_new_fields():
    from core.memory.fixation_pipeline import _validate_episode
    legacy = {"raw_facts": ["a"], "topic_keywords": ["k"], "emotion_peak": "neutral", "strength": 0.5}
    assert _validate_episode(legacy) and legacy["user_said"] == [] and legacy["raw_facts"] == ["a"]
    new = {"user_said": ["u"], "char_reading": ["c"], "topic_keywords": ["k"],
           "emotion_peak": "neutral", "strength": 0.5}
    assert _validate_episode(new)
    assert new["raw_facts"] == ["u", "c"]  # 合并回填，兼容老读者
    bad = {"topic_keywords": ["k"], "emotion_peak": "neutral", "strength": 0.5}
    assert not _validate_episode(bad)


@pytest.mark.asyncio
async def test_reflect_stores_user_said_char_reading_and_sends_quotes(sandbox, _llm):
    from core.memory import mid_term as _mt
    from core.memory.episodic_memory import _load_memories
    from core.memory.fixation_pipeline import reflect_to_episodic
    uid = "m3r"
    _mt.append(uid, "用户：说累了；角色：安慰", tags=[], mid_id="mt1", source_turn_id=f"{uid}_1",
               source_event_ids=[f"{uid}_1:user", f"{uid}_1:assistant"], char_id=CHAR)
    _llm.chat = AsyncMock(return_value=json.dumps({
        "user_said": ["我今天好累"], "char_reading": ["他可能压力大"], "topic_keywords": ["累"],
        "emotion_peak": "sad", "strength": 0.6, "narrative_summary": "说累了",
    }, ensure_ascii=False))
    fake_event = {"memory_text": "我今天好累啊真的" + "长" * 200, "tombstoned": False}
    with patch("core.memory.event_query.get_event", return_value=fake_event):
        await reflect_to_episodic(uid, ["mt1"], trigger="eager", char_id=CHAR)
    prompt = _llm.chat.call_args.kwargs["messages"][0]["content"]
    assert "用户原话" in prompt and "我今天好累啊真的" in prompt
    assert "长" * 121 not in prompt  # 每条截断到 120 字
    ep = _load_memories(uid, char_id=CHAR)[0]
    assert ep["user_said"] == ["我今天好累"] and ep["char_reading"] == ["他可能压力大"]
    assert ep["raw_facts"] == ["我今天好累", "他可能压力大"]
