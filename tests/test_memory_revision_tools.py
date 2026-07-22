import pytest


class _Session:
    WAITING_CONFIRM = "waiting_confirm"
    status = "idle"

    def set_waiting_confirm(self, *args):
        raise AssertionError("memory revision must not request dangerous-tool confirmation")


@pytest.mark.asyncio
async def test_revise_memory_weakens_old_episode_adds_correction_and_records_audit(sandbox):
    from core.memory.episodic_memory import list_episodes, write_episode
    from core.memory.provenance_log import query
    from core.tool_dispatcher import execute

    write_episode("owner", {
        "id": "ep-old", "timestamp": 1.0, "summary": "用户喜欢熬夜", "narrative_summary": "用户喜欢熬夜",
        "strength": 0.8, "tags": ["sleep"], "topic_keywords": ["睡眠"],
    })
    result, confirm = await execute(
        "revise_memory", {"episode_id": "ep-old", "correction": "用户其实在规律早睡。"},
        "owner", "owner", False, _Session(), origin="assistant_loop", char_id="yexuan",
    )

    assert confirm is None
    assert "已更正" in result
    entries = list_episodes("owner")
    old = next(item for item in entries if item["id"] == "ep-old")
    correction = next(item for item in entries if item.get("corrects_episode_id") == "ep-old")
    assert old["strength"] == 0.1 and old["correction"] == "用户其实在规律早睡。"
    assert correction["is_correction"] is True
    assert len(query("owner", "yexuan", artifact="episodic")) >= 2


@pytest.mark.asyncio
async def test_revise_user_profile_writes_identity_provenance_and_action_trace(sandbox):
    from core.memory.action_trace import recent
    from core.memory.provenance_log import query
    from core.tool_dispatcher import execute

    result, confirm = await execute(
        "revise_user_profile", {"field": "sleep_pattern", "correction": "用户通常在23点前休息。"},
        "owner", "owner", False, _Session(), origin="assistant_loop", char_id="yexuan",
    )

    assert confirm is None
    assert "已更新" in result
    records = query("owner", "yexuan", artifact="user_identity", field="sleep_pattern")
    assert records[0]["origin"]["tool"] == "revise_user_profile"
    assert any(item["tool"] == "revise_user_profile" and item["status"] == "ok" for item in recent("owner", "yexuan"))


@pytest.mark.asyncio
async def test_forget_episodic_downgrades_topic_excludes_recall_and_records_audit(sandbox):
    from core.memory.episodic_memory import list_episodes, retrieve, write_episode
    from core.memory.provenance_log import query
    from core.tool_dispatcher import execute

    write_episode("owner", {
        "id": "ep-forget", "timestamp": 1.0, "summary": "用户为考试焦虑", "narrative_summary": "用户为考试焦虑",
        "strength": 0.8, "tags": ["考试"], "topic_keywords": ["考试"], "is_core": True,
    })
    result, confirm = await execute(
        "forget_episodic", {"topic": "考试"},
        "owner", "owner", False, _Session(), origin="assistant_loop", char_id="yexuan",
    )

    assert confirm is None
    assert "降级" in result
    episode = next(item for item in list_episodes("owner") if item["id"] == "ep-forget")
    assert episode["strength"] == 0.1
    assert episode["status"] == "forgotten"
    assert episode["is_core"] is False
    assert retrieve("owner", topic="考试", char_id="yexuan", allow_strengthen=False) == []
    records = query("owner", "yexuan", artifact="episodic", field="ep-forget")
    assert records[0]["origin"]["tool"] == "forget_episodic"


@pytest.mark.asyncio
async def test_clear_midterm_clears_current_bucket_and_records_audit(sandbox):
    from core.memory import mid_term
    from core.memory.provenance_log import query
    from core.tool_dispatcher import execute

    mid_term.append("owner", "临时的近况", char_id="yexuan")
    result, confirm = await execute(
        "clear_midterm", {},
        "owner", "owner", False, _Session(), origin="assistant_loop", char_id="yexuan",
    )

    assert confirm is None
    assert "已清空当前 1 条" in result
    assert mid_term.load("owner", char_id="yexuan") == []
    records = query("owner", "yexuan", artifact="mid_term", field="all")
    assert records[0]["origin"]["tool"] == "clear_midterm"
