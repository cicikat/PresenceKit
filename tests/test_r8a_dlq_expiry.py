"""
R8-A: DLQ legacy task 30-day expiry policy.

Coverage:
1.  failed_at > 30 days → is_dlq_task_expired returns True
2.  failed_at < 30 days → is_dlq_task_expired returns False
3.  Exactly at TTL boundary → not expired (strictly greater-than)
4.  No time fields → safe fallback (False)
5.  Filename ms-prefix fallback when failed_at absent
6.  File mtime fallback when failed_at + filename absent
7.  failed_at takes priority over filename (recent failed_at wins)
8.  LEGACY_TASK_TYPES contains all known zombie types
9.  Live task types are NOT in LEGACY_TASK_TYPES
10. DLQ monitor sweep archives expired legacy task to expired/
11. DLQ monitor does NOT archive recent legacy task
12. DLQ monitor does NOT archive live task even if old
13. Old record missing failed_at uses filename prefix fallback
14. Two LEGACY_COMPAT types (mid_term_append / episodic_compress) still expire via sweep
15. Mixed file set: only expired legacy file archived
16. Legacy handler functions still exist (no accidental deletion)
"""

import json
import time
from pathlib import Path

import pytest


# ── helpers ──────────────────────────────────────────────────────────────────

def _write_dlq_file(
    dlq_dir: Path,
    task_type: str,
    failed_at: float,
    *,
    prefix_ms: int | None = None,
    omit_failed_at: bool = False,
) -> Path:
    ms = prefix_ms if prefix_ms is not None else int(failed_at * 1000)
    filename = f"{ms}_{task_type}.json"
    record: dict = {"task": {"task_type": task_type, "payload": {}}, "error": "test error"}
    if not omit_failed_at:
        record["failed_at"] = failed_at
    p = dlq_dir / filename
    p.write_text(json.dumps(record), encoding="utf-8")
    return p


def _reset_dlq_cooldown():
    """Ensure _is_ready("dlq_monitor") returns True for the next call."""
    from core.scheduler.loop import _last_trigger
    _last_trigger.pop("dlq_monitor", None)


# ── unit: is_dlq_task_expired ─────────────────────────────────────────────────

def test_expired_by_failed_at():
    from core.post_process.slow_queue import is_dlq_task_expired
    now = time.time()
    assert is_dlq_task_expired({"failed_at": now - 31 * 86400}, now=now) is True


def test_not_expired_by_failed_at():
    from core.post_process.slow_queue import is_dlq_task_expired
    now = time.time()
    assert is_dlq_task_expired({"failed_at": now - 5 * 86400}, now=now) is False


def test_boundary_exact_ttl_not_expired():
    # Exactly at 30 days: not expired (strictly greater-than)
    from core.post_process.slow_queue import is_dlq_task_expired
    now = time.time()
    assert is_dlq_task_expired({"failed_at": now - 30 * 86400}, now=now) is False


def test_no_time_fields_safe_fallback():
    from core.post_process.slow_queue import is_dlq_task_expired
    assert is_dlq_task_expired({}) is False


def test_expired_by_filename_prefix():
    from core.post_process.slow_queue import is_dlq_task_expired
    now = time.time()
    old_ms = int((now - 40 * 86400) * 1000)
    filename = f"{old_ms}_mid_term_append.json"
    assert is_dlq_task_expired({}, filename=filename, now=now) is True


def test_not_expired_by_filename_prefix():
    from core.post_process.slow_queue import is_dlq_task_expired
    now = time.time()
    recent_ms = int((now - 5 * 86400) * 1000)
    filename = f"{recent_ms}_mid_term_append.json"
    assert is_dlq_task_expired({}, filename=filename, now=now) is False


def test_expired_by_file_mtime_fallback():
    from core.post_process.slow_queue import is_dlq_task_expired
    now = time.time()
    assert is_dlq_task_expired({}, file_mtime=now - 45 * 86400, now=now) is True


def test_failed_at_priority_over_filename():
    # failed_at says recent → not expired, even if filename says old
    from core.post_process.slow_queue import is_dlq_task_expired
    now = time.time()
    old_ms = int((now - 40 * 86400) * 1000)
    filename = f"{old_ms}_mid_term_append.json"
    assert is_dlq_task_expired({"failed_at": now - 5 * 86400}, filename=filename, now=now) is False


def test_custom_ttl_days():
    from core.post_process.slow_queue import is_dlq_task_expired
    now = time.time()
    # 10 days old with ttl_days=7 → expired
    assert is_dlq_task_expired({"failed_at": now - 10 * 86400}, now=now, ttl_days=7) is True
    # 10 days old with ttl_days=14 → not expired
    assert is_dlq_task_expired({"failed_at": now - 10 * 86400}, now=now, ttl_days=14) is False


# ── LEGACY_TASK_TYPES ────────────────────────────────────────────────────────

def test_legacy_task_types_contains_zombie_handlers():
    from core.post_process.slow_queue import LEGACY_TASK_TYPES
    assert "mid_term_append" in LEGACY_TASK_TYPES
    assert "episodic_compress" in LEGACY_TASK_TYPES
    # R8-E1: consolidate_to_growth removed from LEGACY_TASK_TYPES (DEAD name-only residue)
    assert "consolidate_to_growth" not in LEGACY_TASK_TYPES


def test_live_task_types_not_in_legacy():
    from core.post_process.slow_queue import LEGACY_TASK_TYPES
    live = (
        "capture_turn_retry",
        "summarize_to_midterm",
        "reflect_to_episodic",
        "consolidate_to_identity",
        "consistency_check",
        "user_profile_update",
    )
    for t in live:
        assert t not in LEGACY_TASK_TYPES, f"{t!r} must not be in LEGACY_TASK_TYPES"


def test_legacy_task_types_is_frozenset():
    from core.post_process.slow_queue import LEGACY_TASK_TYPES
    assert isinstance(LEGACY_TASK_TYPES, frozenset)


# ── DLQ monitor sweep (filesystem) ───────────────────────────────────────────

async def test_sweep_archives_expired_legacy_task(sandbox, caplog):
    import logging
    from core.scheduler.triggers.time_based import _check_dlq_monitor

    dlq_dir = sandbox.dead_letter_queue()
    dlq_dir.mkdir(parents=True, exist_ok=True)
    _write_dlq_file(dlq_dir, "mid_term_append", failed_at=time.time() - 35 * 86400)

    _reset_dlq_cooldown()
    with caplog.at_level(logging.INFO, logger="core.scheduler.triggers.time_based"):
        await _check_dlq_monitor()

    active = list(dlq_dir.glob("*.json"))
    archived = list((dlq_dir / "expired").glob("*.json"))
    assert len(active) == 0, f"Expired file must be moved; still active: {[f.name for f in active]}"
    assert len(archived) == 1, f"Expired file must be in expired/: {archived}"
    assert any("slow_queue_dlq_expired" in r.message for r in caplog.records)
    assert any("mid_term_append" in r.message for r in caplog.records)


async def test_sweep_does_not_archive_recent_legacy_task(sandbox):
    from core.scheduler.triggers.time_based import _check_dlq_monitor

    dlq_dir = sandbox.dead_letter_queue()
    dlq_dir.mkdir(parents=True, exist_ok=True)
    _write_dlq_file(dlq_dir, "mid_term_append", failed_at=time.time() - 5 * 86400)

    _reset_dlq_cooldown()
    await _check_dlq_monitor()

    active = list(dlq_dir.glob("*.json"))
    assert len(active) == 1, "Recent legacy task must remain in active DLQ"
    expired_files = list((dlq_dir / "expired").glob("*.json")) if (dlq_dir / "expired").exists() else []
    assert len(expired_files) == 0


async def test_sweep_does_not_archive_live_task_even_if_old(sandbox):
    from core.scheduler.triggers.time_based import _check_dlq_monitor

    dlq_dir = sandbox.dead_letter_queue()
    dlq_dir.mkdir(parents=True, exist_ok=True)
    _write_dlq_file(dlq_dir, "consolidate_to_identity", failed_at=time.time() - 35 * 86400)

    _reset_dlq_cooldown()
    await _check_dlq_monitor()

    active = list(dlq_dir.glob("*.json"))
    assert len(active) == 1, "Live task type must never be archived by legacy sweep"


async def test_sweep_handles_missing_failed_at_via_filename(sandbox):
    """Old record with no failed_at field uses filename ms-prefix as fallback."""
    from core.scheduler.triggers.time_based import _check_dlq_monitor

    dlq_dir = sandbox.dead_letter_queue()
    dlq_dir.mkdir(parents=True, exist_ok=True)
    now = time.time()
    old_ms = int((now - 40 * 86400) * 1000)
    filename = f"{old_ms}_mid_term_append.json"
    (dlq_dir / filename).write_text(
        json.dumps({"task": {"task_type": "mid_term_append"}, "error": "old"}),
        encoding="utf-8",
    )

    _reset_dlq_cooldown()
    await _check_dlq_monitor()  # must not raise

    archived = list((dlq_dir / "expired").glob("*.json")) if (dlq_dir / "expired").exists() else []
    assert len(archived) == 1, "Task with no failed_at should be archived via filename fallback"


async def test_sweep_episodic_compress_is_legacy(sandbox):
    from core.scheduler.triggers.time_based import _check_dlq_monitor

    dlq_dir = sandbox.dead_letter_queue()
    dlq_dir.mkdir(parents=True, exist_ok=True)
    _write_dlq_file(dlq_dir, "episodic_compress", failed_at=time.time() - 35 * 86400)

    _reset_dlq_cooldown()
    await _check_dlq_monitor()

    archived = list((dlq_dir / "expired").glob("*.json")) if (dlq_dir / "expired").exists() else []
    assert len(archived) == 1


def test_consolidate_to_growth_not_in_legacy_task_types():
    """R8-E1: consolidate_to_growth removed from LEGACY_TASK_TYPES.
    It was DEAD (name-only residue): never registered as handler, never enqueued,
    no DLQ files. Removing it prevents the sweep from expiring any hypothetical
    stale DLQ file with this type.
    """
    from core.post_process.slow_queue import LEGACY_TASK_TYPES
    assert "consolidate_to_growth" not in LEGACY_TASK_TYPES


async def test_sweep_does_not_archive_consolidate_to_growth_dlq_file(sandbox):
    """R8-E1: A hypothetical DLQ file with type consolidate_to_growth is NOT swept.
    Since it is no longer in LEGACY_TASK_TYPES, the monitor treats it as unknown
    and leaves it in the active DLQ (same as any live task type).
    """
    from core.scheduler.triggers.time_based import _check_dlq_monitor

    dlq_dir = sandbox.dead_letter_queue()
    dlq_dir.mkdir(parents=True, exist_ok=True)
    _write_dlq_file(dlq_dir, "consolidate_to_growth", failed_at=time.time() - 35 * 86400)

    _reset_dlq_cooldown()
    await _check_dlq_monitor()

    active = list(dlq_dir.glob("*.json"))
    archived = list((dlq_dir / "expired").glob("*.json")) if (dlq_dir / "expired").exists() else []
    assert len(active) == 1, "consolidate_to_growth DLQ file must not be swept (not in LEGACY_TASK_TYPES)"
    assert len(archived) == 0


async def test_sweep_mixed_files(sandbox):
    """
    3 files:
      - expired legacy   (mid_term_append, 35 days)  → archived
      - recent legacy    (episodic_compress, 5 days) → stays
      - old live task    (consolidate_to_identity, 35 days) → stays
    """
    from core.scheduler.triggers.time_based import _check_dlq_monitor

    dlq_dir = sandbox.dead_letter_queue()
    dlq_dir.mkdir(parents=True, exist_ok=True)
    now = time.time()

    _write_dlq_file(dlq_dir, "mid_term_append",
                    failed_at=now - 35 * 86400,
                    prefix_ms=int((now - 35 * 86400) * 1000))
    _write_dlq_file(dlq_dir, "episodic_compress",
                    failed_at=now - 5 * 86400,
                    prefix_ms=int((now - 5 * 86400) * 1000))
    _write_dlq_file(dlq_dir, "consolidate_to_identity",
                    failed_at=now - 35 * 86400,
                    prefix_ms=int((now - 35 * 86400) * 1000) + 1)

    _reset_dlq_cooldown()
    await _check_dlq_monitor()

    active = list(dlq_dir.glob("*.json"))
    archived = list((dlq_dir / "expired").glob("*.json")) if (dlq_dir / "expired").exists() else []
    assert len(archived) == 1, f"Only the expired legacy file should be archived, got {[f.name for f in archived]}"
    assert len(active) == 2, f"Recent legacy + live task must remain, got {[f.name for f in active]}"


# ── handler registration smoke ────────────────────────────────────────────────

def test_legacy_handler_functions_still_exist():
    """Legacy handler functions must not be accidentally deleted (DLQ compat)."""
    import inspect
    from core.pipeline import _handler_mid_term_append, _handler_episodic_compress

    assert inspect.iscoroutinefunction(_handler_mid_term_append)
    assert inspect.iscoroutinefunction(_handler_episodic_compress)


def test_legacy_handlers_are_registered_at_startup(monkeypatch):
    """register_slow_handlers() still registers mid_term_append and episodic_compress."""
    import core.post_process.slow_queue as sq

    # Minimal stubs so register_slow_handlers doesn't need fixation_pipeline fully imported
    async def _stub_handler(_payload): ...

    monkeypatch.setattr(
        "core.memory.fixation_pipeline.handler_capture_turn_retry", _stub_handler, raising=False
    )
    monkeypatch.setattr(
        "core.memory.fixation_pipeline.handler_summarize_to_midterm", _stub_handler, raising=False
    )
    monkeypatch.setattr(
        "core.memory.fixation_pipeline.handler_reflect_to_episodic", _stub_handler, raising=False
    )
    monkeypatch.setattr(
        "core.memory.fixation_pipeline.handler_consolidate_to_identity", _stub_handler, raising=False
    )

    sq._handlers = {}
    from core.pipeline import register_slow_handlers
    register_slow_handlers()

    assert "mid_term_append" in sq._handlers, "mid_term_append handler must stay registered"
    assert "episodic_compress" in sq._handlers, "episodic_compress handler must stay registered"
    assert "consolidate_to_identity" in sq._handlers
    assert "summarize_to_midterm" in sq._handlers
