"""
core/memory/vector_store.py
===========================
Per-user sqlite-vec semantic index. Derived data — JSON/MD files are the source of truth;
this DB can be deleted and rebuilt at any time via rebuild().

Fail-open contract: every public function swallows exceptions and logs them.
The main reply path is never blocked by vector store failures.
"""
from __future__ import annotations

import asyncio
import functools
import logging
import struct
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# 向量库本就低频，单 worker 把所有 sqlite IO 串行化即可：既避免默认线程池多路并发
# 写同一 db 文件撞 "database is locked"，又不必额外上 WAL / busy_timeout。
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="vector_store")


# ── internals ────────────────────────────────────────────────────────────────

def _configured_dim() -> int:
    try:
        from core.config_loader import get_config
        return int(get_config().get("embedding", {}).get("dim", 1024))
    except Exception:
        return 1024


def _db_path(uid: str, char_id: str) -> Path:
    from core.sandbox import get_paths, safe_user_id
    return get_paths().user_memory_root(safe_user_id(uid), char_id=char_id) / "vector_store.db"


def _pack(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def _open_db(uid: str, char_id: str):
    """Open sqlite connection with vec0 extension. Returns None (never raises) on failure."""
    import sqlite3
    try:
        import sqlite_vec
    except ImportError:
        logger.debug("[vector_store] sqlite_vec not installed; semantic recall disabled")
        return None

    path = _db_path(uid, char_id)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(str(path))
        db.enable_load_extension(True)
        sqlite_vec.load(db)
        db.enable_load_extension(False)
        return db
    except Exception as e:
        logger.warning("[vector_store] cannot open db uid=%s: %s", uid, e)
        return None


def _ensure_tables(db, dim: int) -> None:
    db.execute(
        f"CREATE VIRTUAL TABLE IF NOT EXISTS vec_items USING vec0(embedding float[{dim}])"
    )
    db.execute(
        """CREATE TABLE IF NOT EXISTS vec_meta (
            rowid    INTEGER PRIMARY KEY,
            source   TEXT,
            source_id TEXT,
            ts       REAL,
            text_preview TEXT
        )"""
    )
    db.commit()


# ── public API ────────────────────────────────────────────────────────────────

async def upsert(
    uid: str,
    char_id: str,
    source: str,
    source_id: str,
    ts: float,
    text: str,
) -> None:
    """Embed text and store in the vector DB. Fail-open: log and return on any error."""
    from core.memory.embedding import embed, EmbeddingUnavailable
    from core.conversation_recovery import blocked
    from core.memory.scope import MemoryScope
    if blocked(MemoryScope.reality_scope(str(uid), char_id), source_id):
        return

    dim = _configured_dim()
    try:
        vecs = await embed([text])
        vec = vecs[0]
    except EmbeddingUnavailable as e:
        logger.debug(
            "[vector_store] embedding unavailable, skip upsert source_id=%s: %s", source_id, e
        )
        return
    except Exception as e:
        logger.warning("[vector_store] embed error source_id=%s: %s", source_id, e)
        return

    if len(vec) != dim:
        logger.warning(
            "[vector_store] dim mismatch: got %d expected %d; skip upsert (check embedding.dim in config)",
            len(vec), dim,
        )
        return

    loop = asyncio.get_running_loop()
    await loop.run_in_executor(
        _executor,
        functools.partial(_upsert_sync, uid, char_id, source, source_id, ts, text, vec),
    )


from core.conversation_recovery import vector_write_guard


@vector_write_guard
def _upsert_sync(
    uid: str, char_id: str, source: str, source_id: str, ts: float, text: str, vec: list[float]
) -> None:
    """Blocking sqlite write. Runs inside the single-worker executor — never call directly
    from the event loop thread."""
    db = _open_db(uid, char_id)
    if db is None:
        return
    try:
        _ensure_tables(db, len(vec))
        row = db.execute(
            "SELECT rowid FROM vec_meta WHERE source=? AND source_id=?",
            (source, source_id),
        ).fetchone()
        blob = _pack(vec)
        if row:
            rowid = row[0]
            db.execute("DELETE FROM vec_items WHERE rowid=?", (rowid,))
            db.execute(
                "INSERT INTO vec_items(rowid, embedding) VALUES (?, ?)", (rowid, blob)
            )
            db.execute(
                "UPDATE vec_meta SET ts=?, text_preview=? WHERE rowid=?",
                (ts, text[:200], rowid),
            )
        else:
            cur = db.execute(
                "INSERT INTO vec_meta(source, source_id, ts, text_preview) VALUES (?, ?, ?, ?)",
                (source, source_id, ts, text[:200]),
            )
            rowid = cur.lastrowid
            db.execute(
                "INSERT INTO vec_items(rowid, embedding) VALUES (?, ?)", (rowid, blob)
            )
        db.commit()
        logger.debug(
            "[vector_store] upsert ok source=%s source_id=%s uid=%s", source, source_id, uid
        )
    except Exception as e:
        logger.warning(
            "[vector_store] db write error uid=%s source_id=%s: %s", uid, source_id, e
        )
    finally:
        db.close()


def query(
    uid: str,
    char_id: str,
    query_vec: list[float],
    k: int,
    *,
    sources: Optional[list[str]] = None,
    since_ts: Optional[float] = None,
) -> list[tuple[str, float, float]]:
    """
    Return top-k nearest neighbours as (source_id, distance, ts).
    Fail-open: return [] on any error.
    recency filter: pass since_ts to restrict results to ts >= since_ts.
    source filter: pass sources=['episodic'] to restrict by source kind.
    """
    dim = _configured_dim()
    if len(query_vec) != dim:
        logger.warning(
            "[vector_store] query dim mismatch: got %d expected %d", len(query_vec), dim
        )
        return []

    db = _open_db(uid, char_id)
    if db is None:
        return []
    try:
        _ensure_tables(db, dim)
        # Over-fetch to allow post-filter by source / since_ts.
        fetch_k = k * 4 if (sources or since_ts is not None) else k
        rows = db.execute(
            "SELECT vi.rowid, vi.distance"
            " FROM vec_items vi"
            " WHERE vi.embedding MATCH ? AND vi.k = ?",
            (_pack(query_vec), fetch_k),
        ).fetchall()

        if not rows:
            return []

        rowids = [r[0] for r in rows]
        dist_map = {r[0]: r[1] for r in rows}

        placeholders = ",".join("?" * len(rowids))
        meta_rows = db.execute(
            f"SELECT rowid, source, source_id, ts FROM vec_meta WHERE rowid IN ({placeholders})",
            rowids,
        ).fetchall()

        results: list[tuple[str, float, float]] = []
        for rowid, src, src_id, ts in meta_rows:
            from core.conversation_recovery import blocked
            from core.memory.scope import MemoryScope
            if blocked(MemoryScope.reality_scope(str(uid), char_id), src_id):
                continue
            if sources is not None and src not in sources:
                continue
            if since_ts is not None and ts < since_ts:
                continue
            results.append((src_id, dist_map[rowid], ts))

        results.sort(key=lambda x: x[1])
        return results[:k]
    except Exception as e:
        logger.warning("[vector_store] query error uid=%s: %s", uid, e)
        return []
    finally:
        db.close()


async def rebuild(uid: str, char_id: str) -> int:
    """
    Drop and rebuild the entire vector DB from episodic.json and recent event_log.
    Idempotent. Returns the number of items inserted.
    """
    path = _db_path(uid, char_id)
    if path.exists():
        try:
            path.unlink()
            logger.info("[vector_store] rebuild: dropped old db uid=%s", uid)
        except Exception as e:
            logger.warning("[vector_store] rebuild: cannot remove old db: %s", e)

    count = 0
    try:
        from core.memory.episodic_memory import _load_memories
        memories = _load_memories(uid, char_id=char_id)
        for ep in memories:
            ep_id = str(ep.get("id", ""))
            ts = float(ep.get("timestamp") or ep.get("occurred_at") or 0)
            text = (
                ep.get("narrative_summary")
                or ep.get("summary")
                or " ".join(ep.get("raw_facts") or [])
            ).strip()
            if not text or not ep_id:
                continue
            await upsert(uid, char_id, "episodic", ep_id, ts, text)
            count += 1

        from core.memory.event_log import get_recent_days as _get_recent
        from core.memory.event_log_source import filter_recallable_text
        import time as _time
        recent_text = _get_recent(uid, days=30, char_id=char_id)
        recent_text, _source_filtered = filter_recallable_text(recent_text)
        if recent_text:
            await upsert(
                uid, char_id, "event_log", f"recent-safe-v2:{uid}",
                _time.time(), recent_text[:2000],
            )
            count += 1
    except Exception as e:
        logger.warning("[vector_store] rebuild error uid=%s: %s", uid, e)

    logger.info("[vector_store] rebuild complete uid=%s count=%d", uid, count)
    return count


def query_with_preview(
    uid: str,
    char_id: str,
    query_vec: list[float],
    k: int,
    *,
    sources: Optional[list[str]] = None,
) -> list[tuple[str, str, float]]:
    """
    Return top-k nearest neighbours as (source_id, text_preview, distance).
    Useful for web recall where display text is needed alongside the URL.
    Fail-open: return [] on any error.
    source filter: pass sources=['web'] to restrict by source kind.
    """
    dim = _configured_dim()
    if len(query_vec) != dim:
        logger.warning(
            "[vector_store] query_with_preview dim mismatch: got %d expected %d", len(query_vec), dim
        )
        return []

    db = _open_db(uid, char_id)
    if db is None:
        return []
    try:
        _ensure_tables(db, dim)
        fetch_k = k * 4 if sources else k
        rows = db.execute(
            "SELECT vi.rowid, vi.distance"
            " FROM vec_items vi"
            " WHERE vi.embedding MATCH ? AND vi.k = ?",
            (_pack(query_vec), fetch_k),
        ).fetchall()

        if not rows:
            return []

        rowids = [r[0] for r in rows]
        dist_map = {r[0]: r[1] for r in rows}

        placeholders = ",".join("?" * len(rowids))
        meta_rows = db.execute(
            f"SELECT rowid, source, source_id, text_preview"
            f" FROM vec_meta WHERE rowid IN ({placeholders})",
            rowids,
        ).fetchall()

        results: list[tuple[str, str, float]] = []
        for rowid, src, src_id, preview in meta_rows:
            from core.conversation_recovery import blocked
            from core.memory.scope import MemoryScope
            if blocked(MemoryScope.reality_scope(str(uid), char_id), src_id):
                continue
            if sources is not None and src not in sources:
                continue
            results.append((src_id, preview or "", dist_map[rowid]))

        results.sort(key=lambda x: x[2])
        return results[:k]
    except Exception as e:
        logger.warning("[vector_store] query_with_preview error uid=%s: %s", uid, e)
        return []
    finally:
        db.close()


def delete(uid: str, char_id: str, source: str, source_id: str) -> bool:
    """Delete a single vector entry by (source, source_id). Fail-open: returns False on error."""
    db = _open_db(uid, char_id)
    if db is None:
        return False
    try:
        _ensure_tables(db, _configured_dim())
        row = db.execute(
            "SELECT rowid FROM vec_meta WHERE source=? AND source_id=?",
            (source, source_id),
        ).fetchone()
        if row is None:
            return False
        rowid = row[0]
        db.execute("DELETE FROM vec_items WHERE rowid=?", (rowid,))
        db.execute("DELETE FROM vec_meta WHERE rowid=?", (rowid,))
        db.commit()
        logger.debug(
            "[vector_store] delete ok source=%s source_id=%s uid=%s", source, source_id, uid
        )
        return True
    except Exception as e:
        logger.warning(
            "[vector_store] delete error uid=%s source_id=%s: %s", uid, source_id, e
        )
        return False
    finally:
        db.close()


def stats(uid: str, char_id: str) -> dict:
    """向量库概览：总条数 + 按 source 分组计数。fail-open → 全 0。"""
    db = _open_db(uid, char_id)
    if db is None:
        return {"total": 0, "by_source": {}, "dim": _configured_dim()}
    try:
        _ensure_tables(db, _configured_dim())
        rows = db.execute(
            "SELECT source, COUNT(*) FROM vec_meta GROUP BY source"
        ).fetchall()
        by_source = {(r[0] or "unknown"): r[1] for r in rows}
        return {"total": sum(by_source.values()), "by_source": by_source, "dim": _configured_dim()}
    except Exception as e:
        logger.warning("[vector_store] stats error uid=%s: %s", uid, e)
        return {"total": 0, "by_source": {}, "dim": _configured_dim()}
    finally:
        db.close()


def list_entries(uid: str, char_id: str, *, source: str | None = None,
                 limit: int = 100, offset: int = 0) -> list[dict]:
    """浏览 vec_meta，按 ts 倒序（新→旧）。fail-open → []。"""
    db = _open_db(uid, char_id)
    if db is None:
        return []
    try:
        _ensure_tables(db, _configured_dim())
        if source:
            rows = db.execute(
                "SELECT rowid, source, source_id, ts, text_preview FROM vec_meta"
                " WHERE source = ? ORDER BY ts DESC LIMIT ? OFFSET ?",
                (source, limit, offset),
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT rowid, source, source_id, ts, text_preview FROM vec_meta"
                " ORDER BY ts DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [{"rowid": r[0], "source": r[1], "source_id": r[2],
                 "ts": r[3], "text_preview": r[4] or ""} for r in rows]
    except Exception as e:
        logger.warning("[vector_store] list_entries error uid=%s: %s", uid, e)
        return []
    finally:
        db.close()


def read_projection_keys(uid: str, char_id: str) -> set[tuple[str, str]]:
    """Read vector metadata without creating a database or loading sqlite-vec."""
    import sqlite3
    from contextlib import closing

    path = _db_path(uid, char_id)
    if not path.exists():
        return set()
    if path.with_name(path.name + "-wal").exists() and not path.with_name(path.name + "-shm").exists():
        # Don't manufacture a sidecar or ignore uncheckpointed rows in a dry run.
        raise RuntimeError("cannot inspect WAL without its existing shared-memory file")
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        return {(source, str(source_id)) for source, source_id in db.execute("SELECT source, source_id FROM vec_meta")}


# Async wrappers keep blocking sqlite access in the existing executor.
async def query_async(
    uid: str,
    char_id: str,
    query_vec: list[float],
    k: int,
    *,
    sources: Optional[list[str]] = None,
    since_ts: Optional[float] = None,
) -> list[tuple[str, float, float]]:
    """Async wrapper for query(): same fail-open contract, runs off the event loop thread."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        _executor,
        functools.partial(query, uid, char_id, query_vec, k, sources=sources, since_ts=since_ts),
    )


async def query_with_preview_async(
    uid: str,
    char_id: str,
    query_vec: list[float],
    k: int,
    *,
    sources: Optional[list[str]] = None,
) -> list[tuple[str, str, float]]:
    """Async wrapper for query_with_preview()."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        _executor,
        functools.partial(query_with_preview, uid, char_id, query_vec, k, sources=sources),
    )


async def delete_async(uid: str, char_id: str, source: str, source_id: str) -> bool:
    """Async wrapper for delete()."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_executor, delete, uid, char_id, source, source_id)


async def stats_async(uid: str, char_id: str) -> dict:
    """Async wrapper for stats()."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_executor, stats, uid, char_id)


async def list_entries_async(uid: str, char_id: str, *, source: str | None = None,
                              limit: int = 100, offset: int = 0) -> list[dict]:
    """Async wrapper for list_entries()."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        _executor,
        functools.partial(list_entries, uid, char_id, source=source, limit=limit, offset=offset),
    )


def dist_to_sim(dist: float) -> float:
    """Convert L2 distance to similarity ∈ (0, 1]. Smaller distance → larger similarity."""
    return 1.0 / (1.0 + dist)


def _recall_weights() -> tuple[float, float, float]:
    try:
        from core.config_loader import get_config
        w = get_config().get("recall", {}).get("weights", {})
        return (
            float(w.get("sem", 0.4)),
            float(w.get("kw", 0.3)),
            float(w.get("strength", 0.3)),
        )
    except Exception:
        return 0.4, 0.3, 0.3


def score_recall(
    semantic_sim: float,
    keyword_relevance: float,
    strength: float = 0.5,
    decay: float = 1.0,
) -> float:
    """
    Fused recall score: w_sem*semantic_sim + w_kw*keyword_relevance + w_str*(strength*decay).
    Weights from config recall.weights.{sem,kw,strength} (defaults 0.4/0.3/0.3).
    When embedding is unavailable, pass semantic_sim=0.0 — the w_sem term drops out
    and the formula degrades gracefully to keyword + strength (fail-open).
    """
    w_sem, w_kw, w_str = _recall_weights()
    return w_sem * semantic_sim + w_kw * keyword_relevance + w_str * (strength * decay)
