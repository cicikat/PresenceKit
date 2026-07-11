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


def test_since_until_filters_out_of_range_candidate(sandbox, monkeypatch):
    """Brief 48：同关键词两条记忆，只有 occurred_at 落在 [since, until) 内的应召回。"""
    now_dt = datetime(2026, 6, 17, 12, 0)  # 周三
    now = now_dt.timestamp()
    monkeypatch.setattr(episodic_memory.time, "time", lambda: now)

    recent_ts = now - 1 * 86400  # timestamp 保持新鲜，避免 decay 干扰，隔离时间过滤本身
    # 两条 narrative_summary 刻意选字面差异大的表述，避免撞上 write_episode 的
    # 文本近似去重（_is_similar 阈值 0.6，按字符集合重叠算，短句很容易误撞）。
    in_range = _episode(
        "ep_in_range", recent_ts,
        occurred_at=now - 8 * 86400,
        topic_keywords=["旅行"], raw_facts=["用户上周说起旅行计划"],
        narrative_summary="两人聊到暑假想去海边度假",
    )
    out_of_range = _episode(
        "ep_out_of_range", recent_ts,
        occurred_at=now,
        topic_keywords=["旅行"], raw_facts=["用户今天说旅行计划有变化"],
        narrative_summary="行程取消临时改去看电影",
    )
    episodic_memory.write_episode(UID, in_range, char_id=CHAR_ID)
    episodic_memory.write_episode(UID, out_of_range, char_id=CHAR_ID)

    from core.memory.temporal_query import parse_query_time_range
    since_ts, until_ts = parse_query_time_range("上周我们聊的旅行计划是什么来着", now)

    result = episodic_memory.retrieve(
        UID, topic="上周我们聊的旅行计划是什么来着", top_k=5, char_id=CHAR_ID,
        allow_strengthen=False, since_ts=since_ts, until_ts=until_ts,
    )
    assert [item["id"] for item in result] == ["ep_in_range"]


def test_time_only_query_recalls_without_keyword_match(sandbox, monkeypatch):
    """Brief 48：纯 time-only 查询（关键词对不上）时间范围内全量记忆参与评分。"""
    now_dt = datetime(2026, 6, 17, 12, 0)
    now = now_dt.timestamp()
    monkeypatch.setattr(episodic_memory.time, "time", lambda: now)

    entry = _episode(
        "ep_time_only", now - 1 * 86400,
        occurred_at=now - 8 * 86400,
        topic_keywords=["搬家"], raw_facts=["用户上周搬了家"],
        narrative_summary="用户上周搬了家",
    )
    episodic_memory.write_episode(UID, entry, char_id=CHAR_ID)

    from core.memory.temporal_query import parse_query_time_range
    since_ts, until_ts = parse_query_time_range("上周都聊了什么", now)

    result = episodic_memory.retrieve(
        UID, topic="上周都聊了什么", top_k=5, char_id=CHAR_ID,
        allow_strengthen=False, since_ts=since_ts, until_ts=until_ts,
    )
    assert [item["id"] for item in result] == ["ep_time_only"]


def test_time_range_with_no_memories_abstains(sandbox, monkeypatch):
    """Brief 48：关键词命中但 occurred_at 不在范围内 → 不越界兜底，返回空。"""
    now_dt = datetime(2026, 6, 17, 12, 0)
    now = now_dt.timestamp()
    monkeypatch.setattr(episodic_memory.time, "time", lambda: now)

    entry = _episode(
        "ep_old", now - 1 * 86400,
        occurred_at=now - 60 * 86400,
        topic_keywords=["旅行"], raw_facts=["用户两个月前说起旅行计划"],
        narrative_summary="用户两个月前聊到旅行计划",
    )
    episodic_memory.write_episode(UID, entry, char_id=CHAR_ID)

    from core.memory.temporal_query import parse_query_time_range
    since_ts, until_ts = parse_query_time_range("上周说的旅行计划怎么样了", now)

    result = episodic_memory.retrieve(
        UID, topic="上周说的旅行计划怎么样了", top_k=5, char_id=CHAR_ID,
        allow_strengthen=False, since_ts=since_ts, until_ts=until_ts,
    )
    assert result == []


def test_until_ts_boundary_is_exclusive(sandbox, monkeypatch):
    """until_ts 上界排他：occurred_at == until_ts 应被排除，until_ts - 1 应被保留。"""
    now = datetime(2026, 6, 17, 12, 0).timestamp()
    monkeypatch.setattr(episodic_memory.time, "time", lambda: now)
    since_ts, until_ts = now - 86400, now

    at_boundary = _episode(
        "ep_at_boundary", now - 1 * 3600, occurred_at=until_ts,
        topic_keywords=["咖啡"], raw_facts=["用户提到咖啡"],
        narrative_summary="同事推荐了一家新开的咖啡店",
    )
    just_inside = _episode(
        "ep_just_inside", now - 1 * 3600, occurred_at=until_ts - 1,
        topic_keywords=["咖啡"], raw_facts=["用户提到咖啡"],
        narrative_summary="早上多喝了一杯美式提神",
    )
    episodic_memory.write_episode(UID, at_boundary, char_id=CHAR_ID)
    episodic_memory.write_episode(UID, just_inside, char_id=CHAR_ID)

    result = episodic_memory.retrieve(
        UID, topic="咖啡的事", top_k=5, char_id=CHAR_ID,
        allow_strengthen=False, since_ts=since_ts, until_ts=until_ts,
    )
    assert [item["id"] for item in result] == ["ep_just_inside"]


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
