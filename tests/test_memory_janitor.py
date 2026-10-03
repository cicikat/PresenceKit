"""
tests/test_memory_janitor.py — Brief 49 memory_janitor 调度触发器测试

覆盖（Brief 49 §4）：
  1. 3 组近似重复（其中 1 组一方 is_core）→ 合并 2 组、核心组不动；血缘并集、
     retrieval_count 求和、occurred_at 取较早、provenance 落条目正确。
  2. 连跑两遍：第二遍零合并、零 rebuild（幂等）。
  3. 合并上限：种子 15 组重复 → 首轮只合 10 组。
  4. 孤儿向量 25 条 → 触发 rebuild；5 条（占比 <10%）→ 只记日志不 rebuild。
"""
from __future__ import annotations
from tests.fixtures.public_assets import TEST_CHAR_ID

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

from core.memory.episodic_memory import _load_memories, _save_memories

_CHAR = TEST_CHAR_ID


# ── 辅助 fixture / helper ─────────────────────────────────────────────────────

def _fake_datetime(*ymdhms):
    class FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(*ymdhms, tzinfo=tz)

    return FakeDatetime


def _make_registry(*char_ids: str) -> MagicMock:
    reg = MagicMock()
    entries = []
    for cid in char_ids:
        e = MagicMock()
        e.id = cid
        entries.append(e)
    reg.list_all.return_value = entries
    return reg


def _ep(ep_id, **kw):
    ep = {
        "id": ep_id,
        "timestamp": 1_700_000_000.0,
        "occurred_at": 1_700_000_000.0,
        "narrative_summary": f"记忆 {ep_id}",
        "summary": f"记忆 {ep_id}",
        "strength": 0.5,
        "status": "open",
        "is_core": False,
        "temporal_ref": "none",
        "emotion_peak": "neutral",
        "tags": ["测试"],
        "topic_keywords": ["测试"],
        "raw_facts": [],
        "retrieval_count": 0,
        "last_retrieved": None,
        "resolved_at": None,
        "resolved_by": None,
        "event_time": None,
        "expires_at": None,
        "source_mid_ids": [],
    }
    ep.update(kw)
    return ep


def _seed(uid, episodes):
    _save_memories(uid, episodes, char_id=_CHAR)


def _run_janitor(monkeypatch):
    from core.scheduler.triggers import memory_janitor as mj

    fake_dt = _fake_datetime(2024, 1, 1, 23, 30, 0)
    monkeypatch.setattr(mj, "datetime", fake_dt)
    with patch("core.scheduler.loop._is_ready", return_value=True), \
         patch("core.scheduler.loop._mark"), \
         patch("core.asset_registry.get_registry", return_value=_make_registry(_CHAR)):
        asyncio.run(mj._check_memory_janitor())


def _fake_vec_entries(source: str, valid_ids: list[str], n_orphan: int) -> list[dict]:
    entries = [
        {"source": source, "source_id": sid, "ts": 0.0, "text_preview": ""}
        for sid in valid_ids
    ]
    entries += [
        {"source": source, "source_id": f"ghost_{i}", "ts": 0.0, "text_preview": ""}
        for i in range(n_orphan)
    ]
    return entries


# ── 1. 近似重复合并 + 核心记忆豁免 ────────────────────────────────────────────

def test_merge_near_duplicates_excludes_core(sandbox, monkeypatch):
    uid = "u_merge_core"
    episodes = [
        _ep("g1_a", narrative_summary="今天聊了西瓜的事", summary="今天聊了西瓜的事",
            strength=0.5, retrieval_count=2, source_mid_ids=["m1"], occurred_at=500.0),
        _ep("g1_b", narrative_summary="今天聊了西瓜的事", summary="今天聊了西瓜的事",
            strength=0.8, retrieval_count=3, source_mid_ids=["m2"], occurred_at=2000.0),
        _ep("g2_a", narrative_summary="用户提到工作压力大", summary="用户提到工作压力大", strength=0.4),
        _ep("g2_b", narrative_summary="用户提到工作压力大", summary="用户提到工作压力大", strength=0.6),
        _ep("g3_core", narrative_summary="生日那天一起吃了蛋糕", summary="生日那天一起吃了蛋糕",
            strength=0.9, is_core=True),
        _ep("g3_b", narrative_summary="生日那天一起吃了蛋糕", summary="生日那天一起吃了蛋糕",
            strength=0.3),
    ]
    _seed(uid, episodes)

    with patch("core.memory.vector_store.list_entries", return_value=[]):
        _run_janitor(monkeypatch)

    memories = _load_memories(uid, char_id=_CHAR)
    ids = {m["id"] for m in memories}
    assert ids == {"g1_b", "g2_b", "g3_core", "g3_b"}, f"实际剩余: {ids}"

    survivor = next(m for m in memories if m["id"] == "g1_b")
    assert set(survivor["source_mid_ids"]) == {"m1", "m2"}
    assert survivor["retrieval_count"] == 5
    assert survivor["occurred_at"] == 500.0, "occurred_at 应取两者中较早的（来自被并方 g1_a）"

    from core.memory import provenance_log
    records = provenance_log.query(uid, _CHAR, artifact="episodic")
    merge_records = [r for r in records if r.get("trigger_signal") == "janitor_merge"]
    assert any(r.get("field") == "g1_b" for r in merge_records)
    assert any(r.get("field") == "g2_b" for r in merge_records)
    assert not any(r.get("field") in ("g3_core", "g3_b") for r in merge_records)


# ── 2. 幂等：连跑两遍第二遍零合并、零 rebuild ─────────────────────────────────

def test_second_run_is_idempotent(sandbox, monkeypatch):
    uid = "u_merge_idem"
    episodes = [
        _ep("d_a", narrative_summary="用户说想养一只猫", summary="用户说想养一只猫", strength=0.5),
        _ep("d_b", narrative_summary="用户说想养一只猫", summary="用户说想养一只猫", strength=0.7),
    ]
    _seed(uid, episodes)

    with patch("core.memory.vector_store.list_entries", return_value=[]):
        _run_janitor(monkeypatch)
    memories = _load_memories(uid, char_id=_CHAR)
    assert len(memories) == 1

    with patch("core.memory.vector_store.list_entries", return_value=[]), \
         patch("core.memory.vector_store.rebuild", new=AsyncMock(return_value=0)) as fake_rebuild:
        _run_janitor(monkeypatch)
        fake_rebuild.assert_not_awaited()

    memories_after = _load_memories(uid, char_id=_CHAR)
    assert len(memories_after) == 1, "第二遍不应再产生任何合并"


# ── 3. 合并上限：15 组重复 → 首轮只合 10 组 ───────────────────────────────────

def test_merge_cap_at_ten_pairs_per_run(sandbox, monkeypatch):
    uid = "u_merge_cap"
    episodes = []
    for i in range(15):
        episodes.append(_ep(
            f"cap_{i}_a", narrative_summary=f"重复事件{i}号", summary=f"重复事件{i}号", strength=0.4,
        ))
        episodes.append(_ep(
            f"cap_{i}_b", narrative_summary=f"重复事件{i}号", summary=f"重复事件{i}号", strength=0.6,
        ))
    _seed(uid, episodes)

    with patch("core.memory.vector_store.list_entries", return_value=[]):
        _run_janitor(monkeypatch)

    memories = _load_memories(uid, char_id=_CHAR)
    assert len(memories) == 20, f"15 组种子应只合并 10 组（30 条剩 20 条），实际剩 {len(memories)} 条"


# ── 4. 向量库孤儿核对：超阈值 rebuild / 未超阈值只记日志 ──────────────────────

def test_orphan_vectors_above_threshold_triggers_rebuild(sandbox, monkeypatch):
    uid = "u_orphan_high"
    _seed(uid, [_ep("ep_keep", strength=0.5)])

    entries = _fake_vec_entries("episodic", ["ep_keep"], n_orphan=25)
    with patch("core.memory.vector_store.list_entries", return_value=entries), \
         patch("core.memory.vector_store.rebuild", new=AsyncMock(return_value=1)) as fake_rebuild:
        _run_janitor(monkeypatch)
        fake_rebuild.assert_awaited_once()


def test_orphan_vectors_below_threshold_only_logs(sandbox, monkeypatch):
    uid = "u_orphan_low"
    _seed(uid, [_ep("ep_keep", strength=0.5)])

    valid_ids = ["ep_keep"] * 95  # 大量指向同一真实条目的有效向量，撑大分母
    entries = _fake_vec_entries("episodic", valid_ids, n_orphan=5)
    with patch("core.memory.vector_store.list_entries", return_value=entries), \
         patch("core.memory.vector_store.rebuild", new=AsyncMock(return_value=0)) as fake_rebuild:
        _run_janitor(monkeypatch)
        fake_rebuild.assert_not_awaited()


def test_merge_unions_source_event_ids(sandbox, monkeypatch):
    uid = "u_merge_events"
    _seed(uid, [
        _ep("e_a", narrative_summary="今天聊了西瓜的事", summary="今天聊了西瓜的事",
            strength=0.5, source_event_ids=["ev1", "ev2"]),
        _ep("e_b", narrative_summary="今天聊了西瓜的事", summary="今天聊了西瓜的事",
            strength=0.8, source_event_ids=["ev2", "ev3"]),
    ])
    with patch("core.memory.vector_store.list_entries", return_value=[]):
        _run_janitor(monkeypatch)
    memories = _load_memories(uid, char_id=_CHAR)
    assert [m["id"] for m in memories] == ["e_b"]
    assert sorted(memories[0]["source_event_ids"]) == ["ev1", "ev2", "ev3"]


def test_forget_episodes_does_not_feed_storyline(sandbox):
    from core.memory.episodic_memory import forget_episodes
    from core.post_process import slow_queue
    uid = "u_forget_story"
    _seed(uid, [_ep("f1")])
    with patch.object(slow_queue, "enqueue") as enq:
        assert forget_episodes(uid, episode_id="f1", char_id=_CHAR) == ["f1"]
    assert not any(c.args and c.args[0] == "storyline_evicted_input" for c in enq.call_args_list)
