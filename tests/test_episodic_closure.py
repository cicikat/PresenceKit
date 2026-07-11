import json
import time

import pytest

from core.memory import episodic_memory
from core.memory.fixation_pipeline import (
    _resolve_matching_open_episodes,
    _validate_episode,
    reflect_to_episodic,
)


UID = "episodic_closure_user"
CHAR_ID = "yexuan"


def _episode(
    ep_id: str,
    *,
    keyword: str = "西瓜",
    timestamp: float | None = None,
    strength: float = 0.8,
    is_core: bool = False,
) -> dict:
    return {
        "id": ep_id,
        "timestamp": timestamp if timestamp is not None else time.time(),
        "raw_facts": [f"用户提到了{keyword}"],
        "topic_keywords": [keyword],
        "emotion_peak": "gentle",
        "narrative_summary": f"用户正在处理{keyword}",
        "strength": strength,
        "is_core": is_core,
    }


def _append_mid_term(mid_id: str) -> None:
    from core.memory import mid_term

    mid_term.append(
        UID,
        "用户说吃完西瓜了",
        tags=["西瓜"],
        mid_id=mid_id,
        source_turn_id=f"turn_{mid_id}",
        char_id=CHAR_ID,
    )


def _closure_response(*, strength: float = 0.2) -> str:
    return json.dumps(
        {
            "raw_facts": ["用户说西瓜已经吃完了"],
            "topic_keywords": ["西瓜"],
            "emotion_peak": "neutral",
            "narrative_summary": "用户吃完了西瓜",
            "strength": strength,
            "is_closure": True,
            "closure_keywords": ["西瓜"],
        },
        ensure_ascii=False,
    )


@pytest.mark.asyncio
async def test_reflect_closure_resolves_matching_open_episode(sandbox, monkeypatch):
    episodic_memory.write_episode(UID, _episode("ep_open"), char_id=CHAR_ID)
    _append_mid_term("mt_closure")

    async def _fake_chat(*args, **kwargs):
        return _closure_response()

    monkeypatch.setattr("core.llm_client.chat", _fake_chat)

    result = await reflect_to_episodic(
        UID,
        ["mt_closure"],
        trigger="eager",
        char_id=CHAR_ID,
    )

    assert result is None, "neutral low-strength closure itself should still be skipped"
    stored = episodic_memory._load_memories(UID, char_id=CHAR_ID)
    old = next(mem for mem in stored if mem["id"] == "ep_open")
    assert old["status"] == "resolved"
    assert old["strength"] <= 0.2
    assert old["resolved_by"]
    assert old["resolved_at"] is not None


def test_resolved_episode_is_excluded_from_retrieve_and_fallback(sandbox):
    episodic_memory.write_episode(UID, _episode("ep_resolved"), char_id=CHAR_ID)
    memories = episodic_memory._load_memories(UID, char_id=CHAR_ID)
    memories[0]["status"] = "resolved"
    episodic_memory._save_memories(UID, memories, char_id=CHAR_ID)

    assert episodic_memory.retrieve(
        UID,
        topic="西瓜",
        top_k=3,
        char_id=CHAR_ID,
        allow_strengthen=False,
    ) == []
    assert episodic_memory.retrieve_fallback(
        UID,
        [],
        char_id=CHAR_ID,
    ) == []


def test_closure_does_not_resolve_episode_older_than_72_hours(sandbox):
    episodic_memory.write_episode(
        UID,
        _episode("ep_old", timestamp=time.time() - 73 * 3600),
        char_id=CHAR_ID,
    )

    closed = _resolve_matching_open_episodes(
        UID,
        ["西瓜"],
        "ep_closure",
        char_id=CHAR_ID,
    )

    assert closed == []
    stored = episodic_memory._load_memories(UID, char_id=CHAR_ID)
    assert stored[0].get("status", "open") == "open"


def test_closure_does_not_resolve_core_episode(sandbox):
    episodic_memory.write_episode(
        UID,
        _episode("ep_core", is_core=True),
        char_id=CHAR_ID,
    )

    closed = _resolve_matching_open_episodes(
        UID,
        ["西瓜"],
        "ep_closure",
        char_id=CHAR_ID,
    )

    assert closed == []
    stored = episodic_memory._load_memories(UID, char_id=CHAR_ID)
    assert stored[0].get("status", "open") == "open"


def test_legacy_episode_output_and_memory_remain_compatible(sandbox):
    legacy_output = {
        "raw_facts": ["用户提到考试"],
        "topic_keywords": ["考试"],
        "emotion_peak": "gentle",
        "strength": 0.6,
    }
    assert _validate_episode(legacy_output)
    assert legacy_output["is_closure"] is False
    assert legacy_output["closure_keywords"] == []

    episodic_memory._save_memories(
        UID,
        [_episode("ep_legacy", keyword="考试")],
        char_id=CHAR_ID,
    )
    result = episodic_memory.retrieve(
        UID,
        topic="考试",
        top_k=1,
        char_id=CHAR_ID,
        allow_strengthen=False,
    )
    assert [mem["id"] for mem in result] == ["ep_legacy"]


def test_invalid_closure_signal_types_are_tolerated():
    data = {
        "raw_facts": ["用户提到考试"],
        "topic_keywords": ["考试"],
        "emotion_peak": "neutral",
        "strength": 0.2,
        "is_closure": "yes",
        "closure_keywords": "考试",
    }
    assert _validate_episode(data)
    assert data["is_closure"] is False
    assert data["closure_keywords"] == []


def _state_change_closure_response(*, strength: float = 0.2) -> str:
    return json.dumps(
        {
            "raw_facts": ["用户说入职了新公司"],
            "topic_keywords": ["工作", "新公司"],
            "emotion_peak": "neutral",
            "narrative_summary": "用户入职了新公司",
            "strength": strength,
            "is_closure": True,
            "closure_keywords": ["工作"],
            "is_state_change": True,
        },
        ensure_ascii=False,
    )


@pytest.mark.asyncio
async def test_reflect_state_change_closes_open_episode_older_than_72_hours(sandbox, monkeypatch):
    episodic_memory.write_episode(
        UID,
        _episode("ep_old_job", keyword="工作", timestamp=time.time() - 5 * 86400),
        char_id=CHAR_ID,
    )
    _append_mid_term("mt_state_change")

    async def _fake_chat(*args, **kwargs):
        return _state_change_closure_response()

    monkeypatch.setattr("core.llm_client.chat", _fake_chat)

    await reflect_to_episodic(
        UID,
        ["mt_state_change"],
        trigger="eager",
        char_id=CHAR_ID,
    )

    stored = episodic_memory._load_memories(UID, char_id=CHAR_ID)
    old = next(mem for mem in stored if mem["id"] == "ep_old_job")
    assert old["status"] == "resolved", "is_state_change=True 应越过 72 小时窗口关闭旧记忆"
    assert old["strength"] <= 0.2


def test_state_change_does_not_resolve_core_episode(sandbox):
    episodic_memory.write_episode(
        UID,
        _episode("ep_core_job", keyword="工作", timestamp=time.time() - 5 * 86400, is_core=True),
        char_id=CHAR_ID,
    )

    closed = _resolve_matching_open_episodes(
        UID,
        ["工作"],
        "ep_closure",
        char_id=CHAR_ID,
        state_change=True,
    )

    assert closed == [], "is_core=True 记忆即使 state_change 也不自动关闭"
    stored = episodic_memory._load_memories(UID, char_id=CHAR_ID)
    assert stored[0].get("status", "open") == "open"

    from core.memory import provenance_log
    records = provenance_log.query(UID, CHAR_ID, artifact="episodic")
    assert any(r.get("trigger_signal") == "conflict_with_core" for r in records), (
        "is_core 冲突应留观测日志，而非静默丢弃"
    )


def test_non_state_change_closure_still_respects_72_hour_window(sandbox):
    episodic_memory.write_episode(
        UID,
        _episode("ep_old_no_state_change", keyword="西瓜", timestamp=time.time() - 5 * 86400),
        char_id=CHAR_ID,
    )

    closed = _resolve_matching_open_episodes(
        UID,
        ["西瓜"],
        "ep_closure",
        char_id=CHAR_ID,
        state_change=False,
    )

    assert closed == [], "非 state_change 场景仍受 72 小时窗口限制"


def test_write_defaults_and_resolved_prompt_wording(sandbox):
    episodic_memory.write_episode(UID, _episode("ep_defaults"), char_id=CHAR_ID)
    stored = episodic_memory._load_memories(UID, char_id=CHAR_ID)[0]
    assert stored["status"] == "open"
    assert stored["resolved_at"] is None
    assert stored["resolved_by"] is None

    stored["status"] = "resolved"
    text = episodic_memory.format_for_prompt([stored], char_name="叶瑄")
    assert "这件事已经结束了" in text
