"""
施工单 03b 验收测试

1. 损坏 JSON → _load_memories 抛 EpisodicCorruptError，不返回 []
2. _save_memories([], ...) 对已有非空文件 → 不写入
3. safe_write_json：验证步骤失败 → 主文件保持原样，无 .tmp 残留
4. retrieve / retrieve_fallback 遇到 EpisodicCorruptError → 降级返回空，不崩溃
"""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

UID = "integrity_test_user"
CHAR_ID = "yexuan"


# ── helpers ──────────────────────────────────────────────────────────────────

def _write_corrupt_episodic(tmp_path, monkeypatch):
    """Put a truncated (unparseable) JSON file where episodic memory expects it."""
    import core.sandbox as _sandbox
    paths = _sandbox.DataPaths(mode="test", test_session_id="pytest_integrity")
    paths._base = tmp_path
    monkeypatch.setattr(_sandbox, "_instance", paths)

    from core.memory.path_resolver import resolve_path
    from core.memory.scope import MemoryScope
    from core.sandbox import safe_user_id

    uid = safe_user_id(UID)
    scope = MemoryScope.reality_scope(uid, CHAR_ID)
    p = resolve_path(scope, "episodic")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('[{"id": "ep1", "status": "open",', encoding="utf-8")  # truncated
    return p


def _write_large_episodic(tmp_path, monkeypatch):
    """Write a valid, non-trivially-sized episodic file (>1 KB)."""
    import core.sandbox as _sandbox
    paths = _sandbox.DataPaths(mode="test", test_session_id="pytest_integrity2")
    paths._base = tmp_path
    monkeypatch.setattr(_sandbox, "_instance", paths)

    from core.memory.path_resolver import resolve_path
    from core.memory.scope import MemoryScope
    from core.sandbox import safe_user_id

    uid = safe_user_id(UID)
    scope = MemoryScope.reality_scope(uid, CHAR_ID)
    p = resolve_path(scope, "episodic")
    p.parent.mkdir(parents=True, exist_ok=True)
    records = [{"id": f"ep_{i}", "timestamp": 1_700_000_000.0, "strength": 0.8} for i in range(30)]
    p.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    return p, records


# ── test 1: corrupt file raises EpisodicCorruptError ─────────────────────────

def test_load_memories_raises_on_corrupt_json(tmp_path, monkeypatch):
    _write_corrupt_episodic(tmp_path, monkeypatch)

    from core.memory.episodic_memory import EpisodicCorruptError, _load_memories

    with pytest.raises(EpisodicCorruptError):
        _load_memories(UID, char_id=CHAR_ID)


# ── test 2: _save_memories refuses empty list over large file ─────────────────

def test_save_memories_refuses_empty_over_large_file(tmp_path, monkeypatch):
    p, original = _write_large_episodic(tmp_path, monkeypatch)

    from core.memory.episodic_memory import _save_memories

    _save_memories(UID, [], char_id=CHAR_ID)

    # file must still contain the original records
    stored = json.loads(p.read_text(encoding="utf-8"))
    assert len(stored) == len(original), "Non-empty file must not be overwritten with []"


# ── test 3: safe_write_json verify failure → main file intact, no tmp ─────────

def test_safe_write_json_verify_failure_preserves_original(tmp_path):
    from core.safe_write import safe_write_json

    target = tmp_path / "state.json"
    original = {"key": "original"}
    target.write_text(json.dumps(original), encoding="utf-8")

    # Make json.loads raise on the verify read so the replace is aborted.
    real_loads = json.loads
    call_count = 0

    def patched_loads(s, **kw):
        nonlocal call_count
        call_count += 1
        # First call is the verify read from the tmp file — make it fail.
        if call_count == 1:
            raise ValueError("simulated corrupt tmp")
        return real_loads(s, **kw)

    with patch("core.safe_write.json.loads", side_effect=patched_loads):
        ok = safe_write_json(target, {"key": "new"})

    assert ok is False
    # Main file must be unchanged
    assert json.loads(target.read_text(encoding="utf-8")) == original
    # No .tmp leftover
    assert not target.with_suffix(".json.tmp").exists()


# ── test 4a: retrieve degrades gracefully on corrupt file ─────────────────────

def test_retrieve_degrades_on_corrupt_episodic(tmp_path, monkeypatch):
    _write_corrupt_episodic(tmp_path, monkeypatch)

    from core.memory.episodic_memory import retrieve

    result = retrieve(UID, topic="西瓜", top_k=3, char_id=CHAR_ID, allow_strengthen=False)
    assert result == [], "retrieve must return [] on corrupt file, not raise"


def test_retrieve_with_trace_degrades_on_corrupt_episodic(tmp_path, monkeypatch):
    _write_corrupt_episodic(tmp_path, monkeypatch)

    from core.memory.episodic_memory import retrieve

    result, trace = retrieve(
        UID, topic="西瓜", top_k=3, char_id=CHAR_ID,
        allow_strengthen=False, return_trace=True,
    )
    assert result == []
    assert trace == []


# ── test 4b: retrieve_fallback degrades gracefully on corrupt file ────────────

def test_retrieve_fallback_degrades_on_corrupt_episodic(tmp_path, monkeypatch):
    _write_corrupt_episodic(tmp_path, monkeypatch)

    from core.memory.episodic_memory import retrieve_fallback

    result = retrieve_fallback(UID, [], char_id=CHAR_ID)
    assert result == [], "retrieve_fallback must return [] on corrupt file, not raise"


# ── test 5: _load_index corrupt → rebuild, not silent {} ─────────────────────

def _setup_paths(tmp_path, monkeypatch, session_id: str):
    """Wire sandbox to tmp_path and return (uid, scope)."""
    import core.sandbox as _sandbox
    paths = _sandbox.DataPaths(mode="test", test_session_id=session_id)
    paths._base = tmp_path
    monkeypatch.setattr(_sandbox, "_instance", paths)

    from core.memory.scope import MemoryScope
    from core.sandbox import safe_user_id
    uid = safe_user_id(UID)
    scope = MemoryScope.reality_scope(uid, CHAR_ID)
    return uid, scope


def test_load_index_corrupt_triggers_rebuild(tmp_path, monkeypatch):
    """Corrupt memory_index.json → error is logged and index is rebuilt from memories."""
    uid, scope = _setup_paths(tmp_path, monkeypatch, "pytest_idx_corrupt")

    from core.memory.path_resolver import resolve_path

    # Write valid episodic memories
    ep_path = resolve_path(scope, "episodic")
    ep_path.parent.mkdir(parents=True, exist_ok=True)
    memories = [
        {
            "id": "ep_001", "timestamp": 1_700_000_000.0, "strength": 0.8,
            "topic_keywords": ["咖啡", "早晨"], "tags": [], "status": "open",
        },
        {
            "id": "ep_002", "timestamp": 1_700_100_000.0, "strength": 0.7,
            "topic_keywords": ["音乐", "吉他"], "tags": [], "status": "open",
        },
    ]
    ep_path.write_text(json.dumps(memories, ensure_ascii=False, indent=2), encoding="utf-8")

    # Write a corrupt index file (null-fill simulation)
    idx_path = resolve_path(scope, "memory_index")
    idx_path.parent.mkdir(parents=True, exist_ok=True)
    idx_path.write_bytes(b'{"key": "val"\x00\x00\x00')  # Extra data / null fill

    from core.memory.episodic_memory import _load_index

    result = _load_index(UID, char_id=CHAR_ID)

    # Must return rebuilt index, not empty dict
    assert result != {}, "Corrupt index must be rebuilt from memories, not return {}"
    assert "咖啡" in result or "早晨" in result or "音乐" in result or "吉他" in result, (
        f"Rebuilt index should contain memory keywords, got: {result}"
    )
    # ep_001 must appear under its keywords
    assert "ep_001" in result.get("咖啡", []) or "ep_001" in result.get("早晨", [])


def test_load_index_missing_triggers_rebuild(tmp_path, monkeypatch):
    """Missing memory_index.json with valid memories → index is rebuilt."""
    uid, scope = _setup_paths(tmp_path, monkeypatch, "pytest_idx_missing")

    from core.memory.path_resolver import resolve_path

    ep_path = resolve_path(scope, "episodic")
    ep_path.parent.mkdir(parents=True, exist_ok=True)
    memories = [
        {
            "id": "ep_003", "timestamp": 1_700_000_000.0, "strength": 0.9,
            "topic_keywords": ["生日", "蛋糕"], "tags": [], "status": "open",
        },
    ]
    ep_path.write_text(json.dumps(memories, ensure_ascii=False, indent=2), encoding="utf-8")
    # Ensure no index file exists
    idx_path = resolve_path(scope, "memory_index")
    if idx_path.exists():
        idx_path.unlink()

    from core.memory.episodic_memory import _load_index

    result = _load_index(UID, char_id=CHAR_ID)

    assert "ep_003" in result.get("生日", []) or "ep_003" in result.get("蛋糕", []), (
        "Missing index must be rebuilt from memories"
    )


def test_load_index_corrupt_with_corrupt_memories_returns_empty(tmp_path, monkeypatch):
    """Corrupt index AND corrupt memories → graceful {} return, no crash."""
    uid, scope = _setup_paths(tmp_path, monkeypatch, "pytest_idx_both_corrupt")

    from core.memory.path_resolver import resolve_path

    # Corrupt both files
    ep_path = resolve_path(scope, "episodic")
    ep_path.parent.mkdir(parents=True, exist_ok=True)
    ep_path.write_text('[{"id": "incomplete"', encoding="utf-8")  # truncated

    idx_path = resolve_path(scope, "memory_index")
    idx_path.write_bytes(b'\x00\x00\x00')

    from core.memory.episodic_memory import _load_index

    result = _load_index(UID, char_id=CHAR_ID)
    assert result == {}, "Both files corrupt → must return {} gracefully, not raise"
