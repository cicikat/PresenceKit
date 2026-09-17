"""
tests/test_event_log_resolver_integration.py — P1-2H

Verifies that event_log now routes ALL path computation through
MemoryScope + resolve_path, not get_paths().user_memory_root() directly.

Covers:
 1.  append() writes to resolve_path(reality_scope, "event_log") day file
 2.  search() reads from resolve_path(reality_scope, "event_log") directory
 3.  get_recent_days() reads from resolver event_log directory
 4.  event_log directory path == sandbox.user_memory_root / event_log (P0 parity)
 5.  event_log day file path identity
 6.  char_id=None → ValueError (fail-loud, no fallback yexuan)
 7.  char_id="" → ValueError (fail-loud, no fallback yexuan)
 8.  yexuan / character_b event_log buckets are isolated
 9.  character_b search does not return yexuan-exclusive content
10.  30-day union: new dir takes precedence; uid-only is frozen-owner only, never leaked to unread characters
11.  path_resolver "event_log" returns directory (not a file)
12.  resolve_path event_log exact layout: runtime/memory/{char_id}/{uid}/event_log
13.  get_recent_days yexuan and character_b return different content
"""
from __future__ import annotations
from tests.fixtures.public_assets import TEST_CHAR_ID

import asyncio
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from core.memory.scope import MemoryScope
from core.memory.path_resolver import resolve_path

_UID = "p1_2h_integ_u1"
_UID2 = "p1_2h_integ_u2"


def _s(p: Path) -> str:
    return str(p).replace("\\", "/")


# ---------------------------------------------------------------------------
# 1. append() writes to resolver path day file
# ---------------------------------------------------------------------------

def test_append_writes_to_resolver_day_file(sandbox):
    import core.memory.event_log as el

    scope = MemoryScope.reality_scope(_UID, "character_b")
    el_dir = resolve_path(scope, "event_log")

    ok = el.append(_UID, "user", "DemoUser专属内容", char_id="character_b")
    assert ok is True

    today = datetime.now().strftime("%Y-%m-%d")
    expected = el_dir / f"{today}.md"
    assert expected.exists(), f"append() must write to {expected}"
    text = expected.read_text(encoding="utf-8")
    assert "DemoUser专属内容" in text


# ---------------------------------------------------------------------------
# 2. search() reads from resolver event_log directory
# ---------------------------------------------------------------------------

async def test_search_reads_from_resolver_dir(sandbox):
    import core.memory.event_log as el

    el.append(_UID, "user", "搜索关键词DemoUser测试", char_id="character_b")

    result = await el.search(_UID, "DemoUser测试", char_id="character_b")
    assert "DemoUser测试" in result or result != ""


# ---------------------------------------------------------------------------
# 3. get_recent_days() reads from resolver directory
# ---------------------------------------------------------------------------

def test_get_recent_days_reads_from_resolver_dir(sandbox):
    import core.memory.event_log as el

    el.append(_UID, "user", "最近N天内容验证", char_id="character_b")

    text = el.get_recent_days(_UID, days=1, char_id="character_b")
    assert "最近N天内容验证" in text


# ---------------------------------------------------------------------------
# 4. event_log directory path identity: resolver == sandbox.user_memory_root / event_log
# ---------------------------------------------------------------------------

def test_event_log_dir_path_equals_legacy_sandbox_path(sandbox):
    scope = MemoryScope.reality_scope(_UID, "character_b")
    resolver_dir = resolve_path(scope, "event_log")
    legacy_dir = sandbox.user_memory_root(_UID, char_id="character_b") / "event_log"
    assert resolver_dir == legacy_dir, (
        f"Resolver event_log dir diverged from legacy:\n"
        f"  resolver: {resolver_dir}\n"
        f"  legacy:   {legacy_dir}"
    )


def test_event_log_dir_path_equals_legacy_sandbox_path_yexuan(sandbox):
    scope = MemoryScope.reality_scope(_UID, TEST_CHAR_ID)
    resolver_dir = resolve_path(scope, "event_log")
    legacy_dir = sandbox.user_memory_root(_UID, char_id=TEST_CHAR_ID) / "event_log"
    assert resolver_dir == legacy_dir


# ---------------------------------------------------------------------------
# 5. event_log day file path identity
# ---------------------------------------------------------------------------

def test_event_log_day_file_path_identity(sandbox):
    import core.memory.event_log as el

    scope = MemoryScope.reality_scope(_UID, "character_b")
    el_dir = resolve_path(scope, "event_log")

    el.append(_UID, "user", "日文件路径测试", char_id="character_b")
    today = datetime.now().strftime("%Y-%m-%d")
    expected_day = el_dir / f"{today}.md"

    # verify file exists at exactly that path
    assert expected_day.exists()
    text = expected_day.read_text(encoding="utf-8")
    assert "日文件路径测试" in text


# ---------------------------------------------------------------------------
# 6. char_id=None → fail-loud, no yexuan fallback
# append() catches all exceptions internally and returns False;
# get_recent_days() and search() propagate the ValueError directly.
# ---------------------------------------------------------------------------

def test_append_char_id_none_returns_false_no_yexuan_write(sandbox):
    import core.memory.event_log as el

    result = el.append(_UID, "user", "test", char_id=None)  # type: ignore[arg-type]
    assert result is False, "append() with char_id=None must return False"
    # verify nothing was written to yexuan bucket
    y_dir = resolve_path(MemoryScope.reality_scope(_UID, TEST_CHAR_ID), "event_log")
    today = datetime.now().strftime("%Y-%m-%d")
    assert not (y_dir / f"{today}.md").exists(), (
        f'no {TEST_CHAR_ID} fallback write must happen when char_id=None'
    )


def test_get_recent_days_char_id_none_raises(sandbox):
    import core.memory.event_log as el
    with pytest.raises((ValueError, TypeError)):
        el.get_recent_days(_UID, char_id=None)  # type: ignore[arg-type]


async def test_search_char_id_none_raises(sandbox):
    import core.memory.event_log as el
    with pytest.raises((ValueError, TypeError)):
        await el.search(_UID, "query", char_id=None)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 7. char_id="" → fail-loud, no yexuan fallback
# ---------------------------------------------------------------------------

def test_append_empty_char_id_returns_false_no_yexuan_write(sandbox):
    import core.memory.event_log as el

    result = el.append(_UID, "user", "test", char_id="")
    assert result is False, "append() with char_id='' must return False"
    y_dir = resolve_path(MemoryScope.reality_scope(_UID, TEST_CHAR_ID), "event_log")
    today = datetime.now().strftime("%Y-%m-%d")
    assert not (y_dir / f"{today}.md").exists(), (
        f"no {TEST_CHAR_ID} fallback write must happen when char_id=''"
    )


def test_get_recent_days_empty_char_id_raises(sandbox):
    import core.memory.event_log as el
    with pytest.raises(ValueError):
        el.get_recent_days(_UID, char_id="")


async def test_search_empty_char_id_raises(sandbox):
    import core.memory.event_log as el
    with pytest.raises(ValueError):
        await el.search(_UID, "query", char_id="")


# ---------------------------------------------------------------------------
# 8. yexuan / character_b buckets are isolated
# ---------------------------------------------------------------------------

def test_yexuan_character_b_event_log_isolated(sandbox):
    import core.memory.event_log as el

    el.append(_UID, "user", "Companion专属词YEXUAN_ONLY", char_id=TEST_CHAR_ID)
    el.append(_UID, "user", "DemoUser专属词CHARACTER_B_ONLY", char_id="character_b")

    y_dir = resolve_path(MemoryScope.reality_scope(_UID, TEST_CHAR_ID), "event_log")
    h_dir = resolve_path(MemoryScope.reality_scope(_UID, "character_b"), "event_log")

    assert y_dir != h_dir, f'{TEST_CHAR_ID} and character_b must have different event_log dirs'
    assert y_dir.exists()
    assert h_dir.exists()

    today = datetime.now().strftime("%Y-%m-%d")
    y_text = (y_dir / f"{today}.md").read_text(encoding="utf-8")
    h_text = (h_dir / f"{today}.md").read_text(encoding="utf-8")

    assert "Companion专属词YEXUAN_ONLY" in y_text
    assert "Companion专属词YEXUAN_ONLY" not in h_text
    assert "DemoUser专属词CHARACTER_B_ONLY" in h_text
    assert "DemoUser专属词CHARACTER_B_ONLY" not in y_text


# ---------------------------------------------------------------------------
# 9. character_b search does not return yexuan exclusive content
# ---------------------------------------------------------------------------

async def test_search_character_b_excludes_yexuan_content(sandbox):
    import core.memory.event_log as el

    el.append(_UID2, "user", "Companion独有词YEXUAN_SENTINEL_XYZ", char_id=TEST_CHAR_ID)
    el.append(_UID2, "user", "DemoUser普通内容", char_id="character_b")

    result = await el.search(_UID2, "YEXUAN_SENTINEL_XYZ", char_id="character_b")
    assert "YEXUAN_SENTINEL_XYZ" not in result, (
        f'character_b search must not find {TEST_CHAR_ID}-bucket content'
    )


# ---------------------------------------------------------------------------
# 10. 30-day union: new dir used when present; uid-only only for frozen owner
# ---------------------------------------------------------------------------

def test_get_recent_days_reads_new_dir_when_present(sandbox):
    import core.memory.event_log as el

    el.append(_UID, "user", "新目录写入内容", char_id="character_b")

    text = el.get_recent_days(_UID, days=1, char_id="character_b")
    assert "新目录写入内容" in text


def test_get_recent_days_union_does_not_leak_uid_only_to_non_owner(sandbox):
    """Unread / non-historical characters must not union uid-only logs."""
    import core.memory.event_log as el
    from core.sandbox import get_paths
    from core.sandbox import safe_user_id

    uid = safe_user_id(_UID)
    old_dir = get_paths()._p("event_log") / uid
    old_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    (old_dir / f"{today}.md").write_text(
        "## 10:00\n**用户**：旧目录专属内容OLD_ONLY\n> emotion:neutral intensity:0\n---\n",
        encoding="utf-8",
    )

    scope = MemoryScope.reality_scope(uid, "character_b")
    new_dir = resolve_path(scope, "event_log")
    assert not new_dir.exists(), "new dir must not exist for this isolation test"

    text = el.get_recent_days(_UID, days=1, char_id="character_b")
    assert "旧目录专属内容OLD_ONLY" not in text
    assert not el.may_read_legacy_event_log(_UID, "character_b")


# ---------------------------------------------------------------------------
# 11. path_resolver "event_log" resolves to a directory (no file extension)
# ---------------------------------------------------------------------------

def test_event_log_resolver_returns_directory_path(sandbox):
    scope = MemoryScope.reality_scope(_UID, "character_b")
    p = resolve_path(scope, "event_log")
    assert p.suffix == "", f"event_log must resolve to a directory path, got: {p}"
    assert p.name == "event_log"


# ---------------------------------------------------------------------------
# 12. resolve_path exact layout: runtime/memory/{char_id}/{uid}/event_log
# ---------------------------------------------------------------------------

def test_event_log_resolver_exact_layout(sandbox):
    scope = MemoryScope.reality_scope(_UID, "character_b")
    p = _s(resolve_path(scope, "event_log"))
    assert f"runtime/memory/character_b/{_UID}/event_log" in p, (
        f"event_log resolver path wrong layout: {p}"
    )


# ---------------------------------------------------------------------------
# 13. get_recent_days yexuan and character_b return different content
# ---------------------------------------------------------------------------

def test_get_recent_days_yexuan_character_b_isolated(sandbox):
    import core.memory.event_log as el

    el.append(_UID, "user", "Companion日志内容YEXUAN_LOG", char_id=TEST_CHAR_ID)
    el.append(_UID, "user", "DemoUser日志内容CHARACTER_B_LOG", char_id="character_b")

    y_text = el.get_recent_days(_UID, days=1, char_id=TEST_CHAR_ID)
    h_text = el.get_recent_days(_UID, days=1, char_id="character_b")

    assert "Companion日志内容YEXUAN_LOG" in y_text
    assert "Companion日志内容YEXUAN_LOG" not in h_text
    assert "DemoUser日志内容CHARACTER_B_LOG" in h_text
    assert "DemoUser日志内容CHARACTER_B_LOG" not in y_text
