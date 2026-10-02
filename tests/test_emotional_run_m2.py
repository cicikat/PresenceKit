"""工单 M2：情绪段落缓冲、冲突修复挂回、format、janitor 保留修复字段。"""
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.fixtures.public_assets import TEST_CHAR_ID

CHAR = TEST_CHAR_ID


@pytest.fixture(autouse=True)
def _llm():
    llm = MagicMock()
    llm.summarize_turn = AsyncMock(return_value="摘要")
    llm.chat = AsyncMock(return_value="{}")
    with patch("core.llm_client", llm, create=True):
        yield llm


async def _turn(uid, i, emotion, enq):
    from core.memory.fixation_pipeline import summarize_to_midterm
    import core.post_process.slow_queue as sq
    with patch.object(sq, "enqueue", side_effect=lambda t, p: enq.append((t, p))):
        return await summarize_to_midterm(f"{uid}_{1000 + i}", uid, f"u{i}", f"r{i}", [], emotion, char_id=CHAR)


def _reflects(enq):
    return [p for t, p in enq if t == "reflect_to_episodic"]


@pytest.mark.asyncio
async def test_six_angry_then_three_calm_enqueue_single_run(sandbox):
    enq = []
    mids = []
    for i in range(6):
        mids.append(await _turn("m2a", i, "angry", enq))
    assert _reflects(enq) == []
    for i in range(6, 9):
        mids.append(await _turn("m2a", i, "gentle", enq))
    refl = _reflects(enq)
    assert len(refl) == 1
    assert refl[0]["trigger"] == "emotional_run"
    assert refl[0]["mid_ids"] == mids and len(mids) == 9
    from core.memory.fixation_pipeline import _load_fixation_state
    assert _load_fixation_state("m2a", char_id=CHAR)["open_emotional_run"] is None


@pytest.mark.asyncio
async def test_run_closes_at_max_length(sandbox):
    enq = []
    for i in range(12):
        await _turn("m2b", i, "sad", enq)
    refl = _reflects(enq)
    assert len(refl) == 1 and len(refl[0]["mid_ids"]) == 12


@pytest.mark.asyncio
async def test_run_closes_on_idle_via_sweep_helper(sandbox):
    from core.memory.fixation_pipeline import close_stale_emotional_run
    import core.post_process.slow_queue as sq
    enq = []
    await _turn("m2c", 0, "angry", enq)
    await _turn("m2c", 1, "sad", enq)
    assert _reflects(enq) == []
    with patch.object(sq, "enqueue", side_effect=lambda t, p: enq.append((t, p))):
        assert await close_stale_emotional_run("m2c", CHAR, now=time.time() + 10 * 60) is False
        assert await close_stale_emotional_run("m2c", CHAR, now=time.time() + 31 * 60) is True
    refl = _reflects(enq)
    assert len(refl) == 1 and len(refl[0]["mid_ids"]) == 2 and refl[0]["trigger"] == "emotional_run"


@pytest.mark.asyncio
async def test_idle_gap_closes_old_run_when_next_turn_arrives(sandbox):
    from core.memory.fixation_pipeline import _advance_emotional_run, _load_fixation_state
    now = time.time()
    closed, _ = _advance_emotional_run("m2d", CHAR, "mt1", "angry", now)
    assert closed == []
    closed, in_run = _advance_emotional_run("m2d", CHAR, "mt2", "neutral", now + 31 * 60)
    assert closed == [["mt1"]] and in_run is False
    assert _load_fixation_state("m2d", char_id=CHAR)["open_emotional_run"] is None


@pytest.mark.asyncio
async def test_happy_still_eager_without_open_run(sandbox):
    enq = []
    await _turn("m2e", 0, "happy", enq)
    refl = _reflects(enq)
    assert len(refl) == 1 and refl[0]["trigger"] == "eager"


def _ep(eid, **kw):
    base = {
        "id": eid, "timestamp": time.time(), "raw_facts": [eid], "topic_keywords": ["吵架"],
        "emotion_peak": "angry", "narrative_summary": f"叙述{eid}", "strength": 0.5,
    }
    base.update(kw)
    return base


@pytest.mark.asyncio
async def test_repairs_conflict_marks_prior_conflict_and_stays_retrievable(sandbox, _llm):
    from core.memory import mid_term as _mt
    from core.memory.episodic_memory import write_episode, _load_memories, retrieve
    from core.memory.fixation_pipeline import reflect_to_episodic

    uid = "m2f"
    write_episode(uid, _ep("ep_conf", episode_kind="conflict", outcome="unresolved",
                           timestamp=time.time() - 3600, narrative_summary="因为迟到发生争执"), char_id=CHAR)
    _mt.append(uid, "道歉并解释清楚", tags=[], mid_id="mt_fix", source_turn_id=f"{uid}_9", char_id=CHAR)
    _llm.chat = AsyncMock(return_value=json.dumps({
        "raw_facts": ["道歉并解释"], "topic_keywords": ["和好"], "emotion_peak": "gentle",
        "strength": 0.5, "repairs_conflict": True, "repair_note": "对方道歉并解释了迟到原因",
        "narrative_summary": "解释并和好",
    }, ensure_ascii=False))
    new_id = await reflect_to_episodic(uid, ["mt_fix"], trigger="sweep", char_id=CHAR)
    got = {m["id"]: m for m in _load_memories(uid, char_id=CHAR)}
    c = got["ep_conf"]
    assert c["outcome"] == "repaired" and c["repaired_by"] == new_id and c["repaired_at"]
    assert c["repair_note"] == "对方道歉并解释了迟到原因"
    assert c.get("status", "open") == "open"
    res = retrieve(uid, "吵架", char_id=CHAR, allow_strengthen=False)
    assert any(m["id"] == "ep_conf" for m in res)


@pytest.mark.asyncio
async def test_emotional_run_prompt_and_fields(sandbox, _llm):
    from core.memory import mid_term as _mt
    from core.memory.episodic_memory import _load_memories
    from core.memory.fixation_pipeline import reflect_to_episodic
    uid = "m2g"
    for i in range(3):
        _mt.append(uid, f"争执{i}", tags=[], mid_id=f"mt{i}", source_turn_id=f"{uid}_{i}", char_id=CHAR)
    _llm.chat = AsyncMock(return_value=json.dumps({
        "raw_facts": ["争执后和好"], "topic_keywords": ["吵架"], "emotion_peak": "angry",
        "strength": 0.6, "episode_kind": "conflict", "outcome": "repaired",
        "repair_note": "互相道歉", "narrative_summary": "吵架后和好",
    }, ensure_ascii=False))
    await reflect_to_episodic(uid, ["mt0", "mt1", "mt2"], trigger="emotional_run", char_id=CHAR)
    prompt = _llm.chat.call_args.kwargs["messages"][0]["content"]
    assert "episode_kind" in prompt and "同一段连续" in prompt
    ep = _load_memories(uid, char_id=CHAR)[0]
    assert ep["episode_kind"] == "conflict" and ep["outcome"] == "repaired" and ep["repair_note"] == "互相道歉"


def test_format_renders_conflict_with_repair():
    from core.memory.episodic_memory import format_for_prompt
    now = time.time()
    text = format_for_prompt([{
        "narrative_summary": "因迟到争执", "timestamp": now - 86400 * 2, "emotion_peak": "angry",
        "episode_kind": "conflict", "outcome": "repaired", "repair_note": "互相道歉",
    }], char_name="X", current_emotion="neutral")
    assert "因迟到争执 → 后来：互相道歉（已和好）" in text


def test_janitor_merge_keeps_repair_fields():
    from core.scheduler.triggers.memory_janitor import _merge_repair_fields
    survivor = {"id": "a", "strength": 0.9, "episode_kind": "conflict", "outcome": "unresolved"}
    loser = {"id": "b", "strength": 0.2, "episode_kind": "conflict", "outcome": "repaired",
             "repair_note": "和好", "repaired_by": "ep_x", "repaired_at": 1.0}
    _merge_repair_fields(survivor, loser)
    assert survivor["outcome"] == "repaired" and survivor["repaired_by"] == "ep_x"
    s2 = {"outcome": "repaired", "repair_note": "k", "repaired_by": "e"}
    _merge_repair_fields(s2, {"outcome": "unresolved", "episode_kind": "conflict"})
    assert s2["outcome"] == "repaired" and s2["repaired_by"] == "e"
