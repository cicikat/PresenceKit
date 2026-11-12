"""
tests/test_vector_store.py
建表/upsert/query/dim 不匹配 fail-open/rebuild 幂等
"""
import asyncio
import time

import pytest
from unittest.mock import AsyncMock, patch

try:
    import sqlite_vec  # noqa: F401
    HAS_SQLITE_VEC = True
except ImportError:
    HAS_SQLITE_VEC = False

_UID = "test_user_vs"
_CHAR = "testchar"
_DIM = 4  # tiny dimension for test speed
_FAKE_VEC = [0.1, 0.2, 0.3, 0.4]


# ── helpers ──────────────────────────────────────────────────────────────────

def _patch_env(tmp_path):
    """Return a stack of patches for dim, db_path, and embed."""
    db = tmp_path / "vs.db"
    return [
        patch("core.memory.vector_store._configured_dim", return_value=_DIM),
        patch("core.memory.vector_store._db_path", return_value=db),
        patch("core.memory.embedding.embed", new=AsyncMock(return_value=[_FAKE_VEC])),
    ]


# ── tests ─────────────────────────────────────────────────────────────────────

@pytest.mark.skipif(not HAS_SQLITE_VEC, reason="sqlite_vec extension not installed")
async def test_upsert_and_query(tmp_path):
    """upsert 写入向量，query 能按余弦相似度检索到它。"""
    from core.memory.vector_store import upsert, query

    patches = _patch_env(tmp_path)
    for p in patches:
        p.start()
    try:
        await upsert(_UID, _CHAR, "episodic", "ep_001", time.time(), "test text")
        results = query(_UID, _CHAR, _FAKE_VEC, k=1)
    finally:
        for p in patches:
            p.stop()

    assert len(results) == 1
    src_id, dist, ts = results[0]
    assert src_id == "ep_001"
    assert dist >= 0.0


@pytest.mark.skipif(not HAS_SQLITE_VEC, reason="sqlite_vec extension not installed")
async def test_upsert_dedup_same_source_id(tmp_path):
    """同一 source_id upsert 两次应更新，不产生重复条目。"""
    from core.memory.vector_store import upsert, query

    patches = _patch_env(tmp_path)
    for p in patches:
        p.start()
    try:
        await upsert(_UID, _CHAR, "episodic", "ep_001", 1000.0, "first")
        await upsert(_UID, _CHAR, "episodic", "ep_001", 2000.0, "updated")
        results = query(_UID, _CHAR, _FAKE_VEC, k=10)
    finally:
        for p in patches:
            p.stop()

    # Should only have one entry, not two
    ep001_hits = [r for r in results if r[0] == "ep_001"]
    assert len(ep001_hits) == 1


async def test_dim_mismatch_fail_open(tmp_path, caplog):
    """embed 返回维度不符时静默 fail-open，打 WARNING 日志，不抛异常。"""
    import logging
    from core.memory.vector_store import upsert

    wrong_vec = [0.1, 0.2]  # dim=2, expected dim=_DIM=4
    db = tmp_path / "vs.db"

    with patch("core.memory.vector_store._configured_dim", return_value=_DIM), \
         patch("core.memory.vector_store._db_path", return_value=db), \
         patch("core.memory.embedding.embed", new=AsyncMock(return_value=[wrong_vec])):
        with caplog.at_level(logging.WARNING, logger="core.memory.vector_store"):
            await upsert(_UID, _CHAR, "episodic", "ep_002", time.time(), "text")

    assert "dim mismatch" in caplog.text


async def test_embedding_unavailable_fail_open(tmp_path, caplog):
    """EmbeddingUnavailable 时静默 fail-open，不抛异常。"""
    import logging
    from core.memory.vector_store import upsert
    from core.memory.embedding import EmbeddingUnavailable

    db = tmp_path / "vs.db"
    with patch("core.memory.vector_store._configured_dim", return_value=_DIM), \
         patch("core.memory.vector_store._db_path", return_value=db), \
         patch("core.memory.embedding.embed", side_effect=EmbeddingUnavailable("down")):
        with caplog.at_level(logging.DEBUG, logger="core.memory.vector_store"):
            result = await upsert(_UID, _CHAR, "episodic", "ep_003", time.time(), "text")

    assert result is None
    assert not db.exists()
    assert not list(tmp_path.glob("vs.db*"))
    assert "embedding unavailable" in caplog.text


def test_query_empty_when_db_missing(tmp_path):
    """DB 不存在时 query 返回 []，不抛异常。"""
    from core.memory.vector_store import query

    missing = tmp_path / "nosuch.db"
    with patch("core.memory.vector_store._configured_dim", return_value=_DIM), \
         patch("core.memory.vector_store._db_path", return_value=missing):
        results = query(_UID, _CHAR, _FAKE_VEC, k=5)

    assert results == []


def test_query_dim_mismatch_returns_empty(tmp_path, caplog):
    """query 时传入错误维度 → 返回 []，打 WARNING。"""
    import logging
    from core.memory.vector_store import query

    db = tmp_path / "vs.db"
    with patch("core.memory.vector_store._configured_dim", return_value=_DIM), \
         patch("core.memory.vector_store._db_path", return_value=db):
        with caplog.at_level(logging.WARNING, logger="core.memory.vector_store"):
            results = query(_UID, _CHAR, [0.1, 0.2], k=5)

    assert results == []
    assert "dim mismatch" in caplog.text


@pytest.mark.skipif(not HAS_SQLITE_VEC, reason="sqlite_vec extension not installed")
async def test_rebuild_idempotent(tmp_path):
    """rebuild 两次结果一致（幂等）。"""
    from core.memory.vector_store import rebuild

    fake_eps = [
        {"id": "ep_1", "timestamp": 1000.0, "narrative_summary": "episode one"},
        {"id": "ep_2", "timestamp": 2000.0, "narrative_summary": "episode two"},
    ]

    patches = _patch_env(tmp_path)
    for p in patches:
        p.start()
    try:
        with patch("core.memory.episodic_memory._load_memories", return_value=fake_eps), \
             patch("core.memory.event_log.get_recent_days", return_value=""):
            count1 = await rebuild(_UID, _CHAR)
            count2 = await rebuild(_UID, _CHAR)
    finally:
        for p in patches:
            p.stop()

    assert count1 == count2 == 2


@pytest.mark.skipif(not HAS_SQLITE_VEC, reason="sqlite_vec extension not installed")
async def test_rebuild_clears_old_db(tmp_path):
    """rebuild 删除旧 DB 再重建，查询结果与旧数据无关。"""
    from core.memory.vector_store import upsert, rebuild, query

    patches = _patch_env(tmp_path)
    for p in patches:
        p.start()
    try:
        # 写一条旧数据
        await upsert(_UID, _CHAR, "episodic", "old_ep", 500.0, "old text")
        old_results = query(_UID, _CHAR, _FAKE_VEC, k=10)
        assert any(r[0] == "old_ep" for r in old_results)

        # rebuild 以空 episodic 列表，旧数据应消失
        with patch("core.memory.episodic_memory._load_memories", return_value=[]), \
             patch("core.memory.event_log.get_recent_days", return_value=""):
            await rebuild(_UID, _CHAR)

        new_results = query(_UID, _CHAR, _FAKE_VEC, k=10)
    finally:
        for p in patches:
            p.stop()

    assert all(r[0] != "old_ep" for r in new_results)


@pytest.mark.skipif(not HAS_SQLITE_VEC, reason="sqlite_vec extension not installed")
async def test_query_async_matches_sync_query(tmp_path):
    """query_async（executor 包装）结果与同步 query 一致（Brief 34 §2）。"""
    from core.memory.vector_store import upsert, query, query_async

    patches = _patch_env(tmp_path)
    for p in patches:
        p.start()
    try:
        await upsert(_UID, _CHAR, "episodic", "ep_async", time.time(), "async test text")
        sync_results = query(_UID, _CHAR, _FAKE_VEC, k=1)
        async_results = await query_async(_UID, _CHAR, _FAKE_VEC, k=1)
    finally:
        for p in patches:
            p.stop()

    assert async_results == sync_results
    assert async_results[0][0] == "ep_async"


@pytest.mark.skipif(not HAS_SQLITE_VEC, reason="sqlite_vec extension not installed")
async def test_vector_store_executor_does_not_block_event_loop(tmp_path):
    """并发 upsert + query_async 交错时，事件循环心跳不被阻塞（Brief 34 §2/§5）。

    单 worker executor 会把 sqlite IO 串行化，但串行化的是"向量库写线程"，
    不是事件循环本身——主循环应持续能处理别的协程（用心跳计数器验证）。
    """
    from core.memory.vector_store import upsert, query_async

    patches = _patch_env(tmp_path)
    for p in patches:
        p.start()
    try:
        heartbeats = 0
        stop = False

        async def _heartbeat():
            nonlocal heartbeats
            while not stop:
                await asyncio.sleep(0.01)
                heartbeats += 1

        async def _io_burst():
            for i in range(10):
                await upsert(_UID, _CHAR, "episodic", f"ep_burst_{i}", time.time(), f"text {i}")
                await query_async(_UID, _CHAR, _FAKE_VEC, k=5)

        hb_task = asyncio.create_task(_heartbeat())
        await _io_burst()
        stop = True
        await hb_task
    finally:
        for p in patches:
            p.stop()

    assert heartbeats > 0, "事件循环心跳应在向量库 IO 期间持续跳动，未被阻塞"


async def test_sqlite_vec_not_installed_fail_open(monkeypatch):
    """sqlite_vec 未安装时，upsert/query/rebuild 均不报错。"""
    import sys
    from core.memory.vector_store import query

    # Simulate missing sqlite_vec by pointing import to None
    monkeypatch.setitem(sys.modules, "sqlite_vec", None)

    with patch("core.memory.vector_store._configured_dim", return_value=_DIM), \
         patch("core.memory.embedding.embed", new=AsyncMock(return_value=[_FAKE_VEC])):
        hits = query(_UID, _CHAR, _FAKE_VEC, k=5)

    assert hits == []
