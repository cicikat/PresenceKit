import json
from datetime import datetime

import pytest

from core.memory import episodic_memory
from core.memory.fixation_pipeline import (
    _parse_event_time_hint,
    _validate_episode,
    reflect_to_episodic,
)


UID = "episodic_temporal_user"
CHAR_ID = "yexuan"


def _episode(ep_id: str, timestamp: float, **overrides) -> dict:
    episode = {
        "id": ep_id,
        "timestamp": timestamp,
        "raw_facts": [f"用户提到考试事项{ep_id}"],
        "topic_keywords": ["考试"],
        "emotion_peak": "gentle",
        "narrative_summary": f"用户的考试事项{ep_id}",
        "strength": 0.8,
    }
    episode.update(overrides)
    return episode


def test_expired_future_episode_is_downweighted_and_rendered_as_elapsed(sandbox, monkeypatch):
    now = datetime.now().replace(hour=12, minute=0, second=0, microsecond=0).timestamp()
    episodic_memory.write_episode(
        UID,
        _episode(
            "ep_expired",
            now - 3600,
            temporal_ref="future",
            event_time=now - 86400,
            expires_at=now - 1,
            narrative_summary="用户明天要考试",
            strength=0.6,
        ),
        char_id=CHAR_ID,
    )
    episodic_memory.write_episode(
        UID,
        _episode(
            "ep_normal",
            now - 3600,
            narrative_summary="另一段完全不同的复习进展",
            strength=0.3,
        ),
        char_id=CHAR_ID,
    )
    monkeypatch.setattr(episodic_memory.time, "time", lambda: now)

    result = episodic_memory.retrieve(
        UID, topic="考试", top_k=2, char_id=CHAR_ID, allow_strengthen=False
    )

    assert [item["id"] for item in result] == ["ep_normal", "ep_expired"]
    rendered = episodic_memory.format_for_prompt([result[1]], char_name="叶瑄")
    assert "她那天要考试" in rendered
    assert "明天要考试" not in rendered
    assert "应该已经发生了" in rendered


def test_unexpired_future_episode_remains_normal(sandbox, monkeypatch):
    now = datetime.now().replace(hour=12, minute=0, second=0, microsecond=0).timestamp()
    future = now + 86400
    episodic_memory.write_episode(
        UID,
        _episode(
            "ep_future",
            now,
            temporal_ref="future",
            event_time=future,
            expires_at=future + 86400,
        ),
        char_id=CHAR_ID,
    )
    monkeypatch.setattr(episodic_memory.time, "time", lambda: now)

    result = episodic_memory.retrieve(
        UID, topic="考试", top_k=1, char_id=CHAR_ID, allow_strengthen=False
    )
    assert [item["id"] for item in result] == ["ep_future"]
    assert "应该已经发生了" not in episodic_memory.format_for_prompt(result, char_name="叶瑄")


def test_persisted_elapsed_episode_is_excluded_from_retrieve(sandbox):
    now = datetime.now().timestamp()
    episodic_memory.write_episode(
        UID,
        _episode("ep_elapsed", now, status="elapsed"),
        char_id=CHAR_ID,
    )
    assert episodic_memory.retrieve(
        UID, topic="考试", top_k=1, char_id=CHAR_ID, allow_strengthen=False
    ) == []


@pytest.mark.asyncio
async def test_unparseable_hint_falls_back_to_none(sandbox, monkeypatch):
    from core.memory import mid_term

    mid_term.append(
        UID,
        "用户改天可能去考试",
        tags=["考试"],
        mid_id="mt_temporal_unknown",
        source_turn_id="turn_temporal_unknown",
        char_id=CHAR_ID,
    )

    async def _fake_chat(*args, **kwargs):
        return json.dumps(
            {
                "raw_facts": ["用户改天可能去考试"],
                "topic_keywords": ["考试"],
                "emotion_peak": "gentle",
                "narrative_summary": "用户改天可能去考试",
                "strength": 0.6,
                "temporal_ref": "future",
                "event_time_hint": "改天",
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr("core.llm_client.chat", _fake_chat)
    ep_id = await reflect_to_episodic(
        UID, ["mt_temporal_unknown"], trigger="eager", char_id=CHAR_ID
    )
    stored = next(
        item
        for item in episodic_memory._load_memories(UID, char_id=CHAR_ID)
        if item["id"] == ep_id
    )
    assert stored["event_time"] is None
    assert stored["expires_at"] is None


@pytest.mark.asyncio
async def test_reflect_stores_future_event_time_and_expiry(sandbox, monkeypatch):
    from core.memory import mid_term

    mid_term.append(
        UID,
        "用户明天要考试",
        tags=["考试"],
        mid_id="mt_temporal_future",
        source_turn_id="turn_temporal_future",
        char_id=CHAR_ID,
    )

    async def _fake_chat(*args, **kwargs):
        return json.dumps(
            {
                "raw_facts": ["用户明天要考试"],
                "topic_keywords": ["考试"],
                "emotion_peak": "gentle",
                "narrative_summary": "用户明天要考试",
                "strength": 0.6,
                "temporal_ref": "future",
                "event_time_hint": "明天",
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr("core.llm_client.chat", _fake_chat)
    ep_id = await reflect_to_episodic(
        UID, ["mt_temporal_future"], trigger="eager", char_id=CHAR_ID
    )
    stored = next(
        item
        for item in episodic_memory._load_memories(UID, char_id=CHAR_ID)
        if item["id"] == ep_id
    )
    assert stored["temporal_ref"] == "future"
    assert stored["event_time"] is not None
    assert stored["expires_at"] == stored["event_time"] + 86400


def test_event_time_parser_handles_supported_hints():
    base = datetime(2026, 6, 14, 15, 30).timestamp()
    tomorrow = _parse_event_time_hint("明天考试", now=base)
    next_wednesday = _parse_event_time_hint("下周三", now=base)
    next_weekend = _parse_event_time_hint("下周末", now=base)
    assert tomorrow is not None
    assert next_wednesday is not None
    assert next_weekend is not None
    assert datetime.fromtimestamp(tomorrow).date() == datetime(2026, 6, 15).date()
    assert datetime.fromtimestamp(next_wednesday).date() == datetime(2026, 6, 17).date()
    assert datetime.fromtimestamp(next_weekend).date() == datetime(2026, 6, 20).date()
    assert _parse_event_time_hint("以后再说", now=base) is None


def test_near_term_time_wording(monkeypatch):
    now_dt = datetime.now().replace(hour=20, minute=0, second=0, microsecond=0)
    now = now_dt.timestamp()
    monkeypatch.setattr(episodic_memory.time, "time", lambda: now)

    just_now = _episode("ep_just", now - 30 * 60)
    hours_ago = _episode("ep_hours", now - 3 * 3600)
    earlier_today = _episode("ep_earlier", now_dt.replace(hour=9).timestamp())

    assert "刚刚" in episodic_memory.format_for_prompt([just_now], char_name="叶瑄")
    assert "几小时前" in episodic_memory.format_for_prompt([hours_ago], char_name="叶瑄")
    assert "今天上午" in episodic_memory.format_for_prompt([earlier_today], char_name="叶瑄")


def test_legacy_episode_and_llm_output_remain_compatible(sandbox):
    legacy_output = {
        "raw_facts": ["用户提到考试"],
        "topic_keywords": ["考试"],
        "emotion_peak": "gentle",
        "strength": 0.6,
    }
    assert _validate_episode(legacy_output)
    assert legacy_output["temporal_ref"] == "none"
    assert legacy_output["event_time_hint"] == ""

    episodic_memory.write_episode(
        UID, _episode("ep_legacy", datetime.now().timestamp()), char_id=CHAR_ID
    )
    stored = episodic_memory._load_memories(UID, char_id=CHAR_ID)[0]
    assert stored["temporal_ref"] == "none"
    assert stored["event_time"] is None
    assert stored["expires_at"] is None
