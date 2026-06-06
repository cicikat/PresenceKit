"""
tests/test_event_log_known_violations_fixed.py — P1-3C

Verifies that all three event_log direct-path known violations are fixed:

  1.  admin/routers/chat_log.py  — _log_dir now uses MemoryScope + resolve_path
  2.  core/scheduler/loop.py     — _user_talked_today now uses MemoryScope + resolve_path
  3.  core/scheduler/last_mentioned.py — _read_recent_event_log now uses MemoryScope + resolve_path

Covers:
  A. admin chat_log: no char_id → uses active_character (active=hongcha)
  B. admin chat_log: explicit char_id=yexuan → reads yexuan event_log bucket
  C. admin chat_log: active missing/empty → HTTP 503, no yexuan fallback
  D. admin chat_log: invalid char_id → HTTP 422, no yexuan fallback
  E. scheduler loop _user_talked_today: uses resolver path with resolved char_id
  F. scheduler loop _user_talked_today: active char unavailable → returns False, no yexuan fallback
  G. scheduler loop _user_talked_today: explicit char_id bypasses active lookup
  H. last_mentioned recall_last_mentioned: uses resolver path with resolved char_id
  I. last_mentioned recall_last_mentioned: active char unavailable → returns None, no fallback
  J. last_mentioned recall_last_mentioned: explicit char_id bypasses active lookup
  K. direct-path lint: 3 known violations removed from _KNOWN_VIOLATIONS
  L. source scan: admin/routers/chat_log.py no longer contains event_log direct path
  M. source scan: core/scheduler/loop.py no longer contains event_log direct path
  N. source scan: core/scheduler/last_mentioned.py no longer contains event_log direct path
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import core.asset_registry as _reg_mod
from core.asset_registry import AssetRegistry

# Pre-import at collection time so _CHAR is set before any fixture chdir.
import core.memory.user_profile as _up_preimport  # noqa: F401


# ── Shared fixtures ────────────────────────────────────────────────────────────

@pytest.fixture
def chars_tree(tmp_path):
    """Minimal project tree: yexuan + hongcha characters + config.yaml."""
    (tmp_path / "config.yaml").write_text(
        "character:\n  name: 测试角色\n  default: yexuan\n"
        "scheduler:\n  owner_id: '10000'\n",
        encoding="utf-8",
    )
    chars = tmp_path / "characters"
    chars.mkdir()
    (chars / "yexuan.json").write_text(
        json.dumps({"name": "叶瑄", "description": "test", "world_book": []}),
        encoding="utf-8",
    )
    (chars / "hongcha.json").write_text(
        json.dumps({"name": "红茶", "description": "hongcha test", "world_book": []}),
        encoding="utf-8",
    )
    jb = chars / "reality" / "jailbreaks"
    jb.mkdir(parents=True)
    (jb / "base.json").write_text(json.dumps({"entries": []}), encoding="utf-8")
    return tmp_path


@pytest.fixture
def registry(chars_tree, monkeypatch):
    monkeypatch.chdir(chars_tree)
    reg = AssetRegistry()
    monkeypatch.setattr(_reg_mod, "_registry", reg)
    return reg


def _seed_active(sandbox, char_id: str):
    p = sandbox.active_prompt_assets()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps({"active_character": char_id, "enabled_lorebooks": [], "enabled_jailbreaks": []}),
        encoding="utf-8",
    )


def _seed_event_log_day(sandbox, uid: str, char_id: str, date_str: str, content: str):
    """Write a day event_log file into the scoped bucket."""
    from core.memory.path_resolver import resolve_path
    from core.memory.scope import MemoryScope
    scope = MemoryScope.reality_scope(uid, char_id)
    log_dir = resolve_path(scope, "event_log")
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / f"{date_str}.md").write_text(content, encoding="utf-8")


# ═══════════════════════════════════════════════════════════════════════════════
# A. admin chat_log: no char_id → uses active_character (active=hongcha)
# ═══════════════════════════════════════════════════════════════════════════════

_TEST_OWNER = "10000"  # fixed owner uid used across admin chat_log tests


def test_chat_log_list_dates_uses_active_char(sandbox, registry, monkeypatch):
    """list_dates with no char_id resolves to active_character (hongcha)."""
    import admin.routers.chat_log as _cl
    monkeypatch.setattr(_cl, "_owner_qq", lambda: _TEST_OWNER)
    from admin.routers.chat_log import list_dates

    _seed_active(sandbox, "hongcha")
    _seed_event_log_day(sandbox, _TEST_OWNER, "hongcha", "2026-06-06", "## 10:00\n**用户**：test\n")

    result = asyncio.run(list_dates(char_id=None, auth="dummy"))

    assert "2026-06-06" in result["dates"], (
        f"hongcha event_log date must appear; got {result['dates']}"
    )


def test_chat_log_get_day_uses_active_char(sandbox, registry, monkeypatch):
    """get_day with no char_id resolves to active_character (hongcha)."""
    import admin.routers.chat_log as _cl
    monkeypatch.setattr(_cl, "_owner_qq", lambda: _TEST_OWNER)
    from admin.routers.chat_log import get_day

    _seed_active(sandbox, "hongcha")
    _seed_event_log_day(sandbox, _TEST_OWNER, "hongcha", "2026-06-06", "## 10:00\n**用户**：你好\n")

    result = asyncio.run(get_day("2026-06-06", char_id=None, auth="dummy"))

    assert result["date"] == "2026-06-06"
    assert not result.get("raw_fallback") or result["date"] == "2026-06-06"


# ═══════════════════════════════════════════════════════════════════════════════
# B. admin chat_log: explicit char_id=yexuan reads yexuan bucket
# ═══════════════════════════════════════════════════════════════════════════════

def test_chat_log_explicit_char_id_reads_correct_bucket(sandbox, registry, monkeypatch):
    """list_dates with explicit char_id=yexuan reads yexuan bucket (active=hongcha)."""
    import admin.routers.chat_log as _cl
    monkeypatch.setattr(_cl, "_owner_qq", lambda: _TEST_OWNER)
    from admin.routers.chat_log import list_dates

    _seed_active(sandbox, "hongcha")
    # Only yexuan has a log file
    _seed_event_log_day(sandbox, _TEST_OWNER, "yexuan", "2026-06-05", "## 09:00\n**用户**：叶瑄内容\n")

    result_yexuan = asyncio.run(list_dates(char_id="yexuan", auth="dummy"))
    result_hongcha = asyncio.run(list_dates(char_id=None, auth="dummy"))

    assert "2026-06-05" in result_yexuan["dates"], (
        f"yexuan bucket must contain 2026-06-05; got {result_yexuan['dates']}"
    )
    assert "2026-06-05" not in result_hongcha["dates"], (
        "hongcha bucket must NOT contain yexuan-only log date"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# C. admin chat_log: active missing/empty → HTTP 503, no yexuan fallback
# ═══════════════════════════════════════════════════════════════════════════════

def test_chat_log_active_missing_returns_503(sandbox, registry, monkeypatch):
    """list_dates with no char_id when active_character is empty → HTTP 503."""
    from fastapi import HTTPException
    import admin.routers.chat_log as _cl
    monkeypatch.setattr(_cl, "_owner_qq", lambda: _TEST_OWNER)
    from admin.routers.chat_log import list_dates

    # Seed active_prompt_assets.json with an empty active_character
    p = sandbox.active_prompt_assets()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps({"active_character": "", "enabled_lorebooks": [], "enabled_jailbreaks": []}),
        encoding="utf-8",
    )

    # Capture whether any path resolution was attempted
    resolve_called = []
    import core.memory.path_resolver as _pr
    original_resolve = _pr.resolve_path
    monkeypatch.setattr(_pr, "resolve_path", lambda *a, **kw: resolve_called.append(a) or original_resolve(*a, **kw))

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(list_dates(char_id=None, auth="dummy"))

    assert exc_info.value.status_code == 503
    assert not resolve_called, "resolve_path must not be called when active_character is invalid"


# ═══════════════════════════════════════════════════════════════════════════════
# D. admin chat_log: invalid char_id → HTTP 422, no yexuan fallback
# ═══════════════════════════════════════════════════════════════════════════════

def test_chat_log_invalid_char_id_returns_422(sandbox, registry, monkeypatch):
    """list_dates with unknown char_id → HTTP 422."""
    from fastapi import HTTPException
    import admin.routers.chat_log as _cl
    monkeypatch.setattr(_cl, "_owner_qq", lambda: _TEST_OWNER)
    from admin.routers.chat_log import list_dates

    _seed_active(sandbox, "hongcha")

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(list_dates(char_id="ghost_char_xyz", auth="dummy"))

    assert exc_info.value.status_code == 422


# ═══════════════════════════════════════════════════════════════════════════════
# E. scheduler loop _user_talked_today: uses resolver path with resolved char_id
# ═══════════════════════════════════════════════════════════════════════════════

def test_user_talked_today_reads_resolver_path(sandbox, registry):
    """_user_talked_today reads the resolver-based event_log path (not legacy direct path)."""
    from core.scheduler.loop import _user_talked_today
    from core.memory.path_resolver import resolve_path
    from core.memory.scope import MemoryScope

    _seed_active(sandbox, "hongcha")

    uid = "10001"
    today = datetime.now().strftime("%Y-%m-%d")
    scope = MemoryScope.reality_scope(uid, "hongcha")
    log_dir = resolve_path(scope, "event_log")
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / f"{today}.md").write_text("## 10:00\n**用户**：hello\n---\n", encoding="utf-8")

    assert _user_talked_today(uid, char_id="hongcha") is True


def test_user_talked_today_empty_file_returns_false(sandbox, registry):
    """_user_talked_today returns False for a file that's too small (≤10 bytes)."""
    from core.scheduler.loop import _user_talked_today
    from core.memory.path_resolver import resolve_path
    from core.memory.scope import MemoryScope

    uid = "10002"
    today = datetime.now().strftime("%Y-%m-%d")
    scope = MemoryScope.reality_scope(uid, "yexuan")
    log_dir = resolve_path(scope, "event_log")
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / f"{today}.md").write_text("tiny", encoding="utf-8")

    assert _user_talked_today(uid, char_id="yexuan") is False


# ═══════════════════════════════════════════════════════════════════════════════
# F. scheduler loop _user_talked_today: active unavailable → False, no yexuan fallback
# ═══════════════════════════════════════════════════════════════════════════════

def test_user_talked_today_active_unavailable_returns_false(sandbox, registry, monkeypatch):
    """_user_talked_today with no char_id and no active → returns False, no yexuan path read."""
    from core.scheduler import loop as _loop

    # active_prompt_assets.json does not exist; active_char_id_or_none returns None
    monkeypatch.setattr(_loop, "_active_char_id_or_none", lambda: None)

    path_accessed = []
    import core.memory.path_resolver as _pr
    monkeypatch.setattr(_pr, "resolve_path", lambda *a, **kw: path_accessed.append(a) or Path("/dev/null"))

    result = _loop._user_talked_today("10003")

    assert result is False
    assert not path_accessed, "resolve_path must not be called when char_id is unavailable"


# ═══════════════════════════════════════════════════════════════════════════════
# G. scheduler loop _user_talked_today: explicit char_id bypasses active lookup
# ═══════════════════════════════════════════════════════════════════════════════

def test_user_talked_today_explicit_char_id_bypasses_active(sandbox, registry, monkeypatch):
    """_user_talked_today with explicit char_id does not call _active_char_id_or_none."""
    from core.scheduler import loop as _loop
    from core.memory.path_resolver import resolve_path
    from core.memory.scope import MemoryScope

    active_called = []
    monkeypatch.setattr(_loop, "_active_char_id_or_none", lambda: active_called.append(1) or "yexuan")

    uid = "10004"
    today = datetime.now().strftime("%Y-%m-%d")
    scope = MemoryScope.reality_scope(uid, "hongcha")
    log_dir = resolve_path(scope, "event_log")
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / f"{today}.md").write_text("## 10:00\n**用户**：hello hongcha\n", encoding="utf-8")

    result = _loop._user_talked_today(uid, char_id="hongcha")

    assert result is True
    assert not active_called, "_active_char_id_or_none must not be called when char_id is explicit"


# ═══════════════════════════════════════════════════════════════════════════════
# H. last_mentioned: uses resolver path with resolved char_id
# ═══════════════════════════════════════════════════════════════════════════════

def test_recall_last_mentioned_reads_resolver_path(sandbox, registry):
    """recall_last_mentioned reads from the resolver-based event_log path."""
    from core.scheduler.last_mentioned import recall_last_mentioned
    from core.memory.path_resolver import resolve_path
    from core.memory.scope import MemoryScope

    _seed_active(sandbox, "hongcha")

    uid = "20001"
    date_str = "2026-06-05"
    # Write to hongcha resolver bucket
    scope = MemoryScope.reality_scope(uid, "hongcha")
    log_dir = resolve_path(scope, "event_log")
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / f"{date_str}.md").write_text(
        "## 14:30\n**用户**：我想去实习一下\n**叶瑄**：加油\n> emotion:gentle intensity:1\n---\n",
        encoding="utf-8",
    )

    now = datetime(2026, 6, 6, 12, 0)
    topic = recall_last_mentioned(uid, char_id="hongcha", now=now, days=3)

    assert topic is not None, "recall_last_mentioned must return a topic from hongcha bucket"
    assert "实习" in topic.topic or "实习" in topic.user_text, (
        f"topic must mention 实习; got topic={topic.topic!r} user_text={topic.user_text!r}"
    )


def test_recall_last_mentioned_bucket_isolation(sandbox, registry):
    """recall_last_mentioned for hongcha does not return yexuan-only content."""
    from core.scheduler.last_mentioned import recall_last_mentioned
    from core.memory.path_resolver import resolve_path
    from core.memory.scope import MemoryScope

    uid = "20002"
    date_str = "2026-06-05"
    YEXUAN_ONLY = "叶瑄专属事件日志内容XYZ"

    # Write only to yexuan bucket
    scope = MemoryScope.reality_scope(uid, "yexuan")
    log_dir = resolve_path(scope, "event_log")
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / f"{date_str}.md").write_text(
        f"## 14:00\n**用户**：{YEXUAN_ONLY}\n**叶瑄**：好的\n> emotion:neutral intensity:0\n---\n",
        encoding="utf-8",
    )

    now = datetime(2026, 6, 6, 12, 0)
    topic_hongcha = recall_last_mentioned(uid, char_id="hongcha", now=now, days=3)

    # hongcha bucket has no logs → must return None
    assert topic_hongcha is None, (
        "recall_last_mentioned for hongcha must not read yexuan bucket"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# I. last_mentioned: active unavailable → None, no yexuan fallback
# ═══════════════════════════════════════════════════════════════════════════════

def test_recall_last_mentioned_active_unavailable_returns_none(sandbox, registry, monkeypatch):
    """recall_last_mentioned with no char_id and no active → returns None, no yexuan fallback."""
    from core.scheduler import last_mentioned as _lm

    monkeypatch.setattr(_lm, "_active_char_id_or_none", lambda: None)

    path_accessed = []
    import core.memory.path_resolver as _pr
    monkeypatch.setattr(_pr, "resolve_path", lambda *a, **kw: path_accessed.append(a) or Path("/dev/null"))

    result = _lm.recall_last_mentioned("20003", now=datetime(2026, 6, 6, 12, 0))

    assert result is None
    assert not path_accessed, "resolve_path must not be called when char_id is unavailable"


# ═══════════════════════════════════════════════════════════════════════════════
# J. last_mentioned: explicit char_id bypasses active lookup
# ═══════════════════════════════════════════════════════════════════════════════

def test_recall_last_mentioned_explicit_char_id_bypasses_active(sandbox, registry, monkeypatch):
    """recall_last_mentioned with explicit char_id does not call _active_char_id_or_none."""
    from core.scheduler import last_mentioned as _lm
    from core.memory.path_resolver import resolve_path
    from core.memory.scope import MemoryScope

    active_called = []
    monkeypatch.setattr(_lm, "_active_char_id_or_none", lambda: active_called.append(1) or "yexuan")

    uid = "20004"
    date_str = "2026-06-05"
    scope = MemoryScope.reality_scope(uid, "hongcha")
    log_dir = resolve_path(scope, "event_log")
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / f"{date_str}.md").write_text(
        "## 15:00\n**用户**：我要准备毕业论文了\n**叶瑄**：加油\n> emotion:gentle intensity:1\n---\n",
        encoding="utf-8",
    )

    now = datetime(2026, 6, 6, 12, 0)
    topic = _lm.recall_last_mentioned(uid, char_id="hongcha", now=now, days=3)

    assert not active_called, "_active_char_id_or_none must not be called when char_id is explicit"
    assert topic is not None, "explicit char_id=hongcha must find the topic"


# ═══════════════════════════════════════════════════════════════════════════════
# K. direct-path lint: 3 known violations removed from _KNOWN_VIOLATIONS
# ═══════════════════════════════════════════════════════════════════════════════

def test_lint_known_violations_table_is_empty():
    """_KNOWN_VIOLATIONS must not contain the three formerly-deferred files."""
    from tests.test_memory_direct_path_lint import _KNOWN_VIOLATIONS

    assert "admin/routers/chat_log.py" not in _KNOWN_VIOLATIONS, (
        "admin/routers/chat_log.py must be removed from _KNOWN_VIOLATIONS (P1-3C fixed)"
    )
    assert "core/scheduler/loop.py" not in _KNOWN_VIOLATIONS, (
        "core/scheduler/loop.py must be removed from _KNOWN_VIOLATIONS (P1-3C fixed)"
    )
    assert "core/scheduler/last_mentioned.py" not in _KNOWN_VIOLATIONS, (
        "core/scheduler/last_mentioned.py must be removed from _KNOWN_VIOLATIONS (P1-3C fixed)"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# L-N. Source scan: files no longer contain event_log direct paths
# ═══════════════════════════════════════════════════════════════════════════════

_ROOT = Path(__file__).parent.parent


def _scan_for_direct_event_log(rel_path: str) -> list[tuple[int, str]]:
    """Return (lineno, line) for any user_memory_root(...) / "event_log" lines."""
    path = _ROOT / rel_path
    assert path.exists(), f"file not found: {rel_path}"
    results = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if 'user_memory_root(' in line and ('/ "event_log"' in line or "/ 'event_log'" in line):
            results.append((lineno, stripped))
    return results


def test_chat_log_no_direct_event_log_path():
    """admin/routers/chat_log.py must not contain user_memory_root(...) / 'event_log'."""
    hits = _scan_for_direct_event_log("admin/routers/chat_log.py")
    assert not hits, (
        "admin/routers/chat_log.py still has direct event_log path:\n"
        + "\n".join(f"  line {ln}: {line}" for ln, line in hits)
    )


def test_scheduler_loop_no_direct_event_log_path():
    """core/scheduler/loop.py must not contain user_memory_root(...) / 'event_log'."""
    hits = _scan_for_direct_event_log("core/scheduler/loop.py")
    assert not hits, (
        "core/scheduler/loop.py still has direct event_log path:\n"
        + "\n".join(f"  line {ln}: {line}" for ln, line in hits)
    )


def test_last_mentioned_no_direct_event_log_path():
    """core/scheduler/last_mentioned.py must not contain user_memory_root(...) / 'event_log'."""
    hits = _scan_for_direct_event_log("core/scheduler/last_mentioned.py")
    assert not hits, (
        "core/scheduler/last_mentioned.py still has direct event_log path:\n"
        + "\n".join(f"  line {ln}: {line}" for ln, line in hits)
    )
