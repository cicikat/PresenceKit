"""error.log: redaction, UTC-day rotation, shared retention/caps, one-time legacy archive (工单 E)."""
from __future__ import annotations

import gzip
import os
from datetime import datetime, timedelta, timezone

import pytest

from core import error_log

SECRET_URL = "https://api.example.test/v1/chat?api_key=sk-live-123456&model=m"
LEGACY = (
    "[2026-08-08 10:00:00] [tool.weather] SSLError: certificate has expired\n"
    "Traceback (most recent call last):\n"
    f"  GET {SECRET_URL}\n"
    "Authorization: Bearer abcdef.ghijkl.mnopqr\n"
    "普通中文行，必须原样保留\n"
)


@pytest.fixture
def ledger(sandbox):
    error_log.reset_for_tests()
    sandbox.error_log().parent.mkdir(parents=True, exist_ok=True)
    yield sandbox
    error_log.reset_for_tests()


def _utc(year, month, day, hour=12):
    return datetime(year, month, day, hour, tzinfo=timezone.utc)


def _set_mtime(path, moment):
    stamp = moment.timestamp()
    os.utime(path, (stamp, stamp))


def test_append_writes_header_once_and_redacts_secrets(ledger):
    error_log.append(f"[t] [m] RuntimeError: boom {SECRET_URL}\nAuthorization: Bearer abcdef.ghijkl.mnopqr\n")
    error_log.append("[t] [m] second entry\n")
    text = ledger.error_log().read_text(encoding="utf-8")
    assert text.startswith(error_log.HEADER_MARKER)
    assert text.count(error_log.HEADER_MARKER) == 1
    assert "runtime_warnings" in text.splitlines()[0]          # 指向另一台账
    assert "sk-live-123456" not in text and "abcdef.ghijkl.mnopqr" not in text
    assert "api_key=***" in text and "second entry" in text


def test_shared_limits_are_the_runtime_warning_numbers(ledger):
    from core import runtime_warning_log as rw
    snap = error_log.snapshot()
    assert (snap["retention_days"], snap["max_day_bytes"], snap["max_total_bytes"]) == (
        rw.retention_days(), rw.max_day_bytes(), rw.max_total_bytes()) == (14, 8 * 1024 * 1024, 48 * 1024 * 1024)


def test_stale_active_file_is_rotated_to_its_dated_name(ledger):
    error_log.append("[t] [m] yesterday\n", now=_utc(2026, 9, 30))
    active = ledger.error_log()
    _set_mtime(active, _utc(2026, 9, 30))
    error_log.append("[t] [m] today\n", now=_utc(2026, 10, 1))
    dated = error_log.dated_path("2026-09-30")
    assert dated.exists() and "yesterday" in dated.read_text(encoding="utf-8")
    assert dated.read_text(encoding="utf-8").startswith(error_log.HEADER_MARKER)
    fresh = active.read_text(encoding="utf-8")
    assert fresh.startswith(error_log.HEADER_MARKER) and "today" in fresh and "yesterday" not in fresh


def test_rotation_never_overwrites_an_existing_dated_file(ledger):
    error_log.append("[t] [m] first\n", now=_utc(2026, 9, 30))
    _set_mtime(ledger.error_log(), _utc(2026, 9, 30))
    error_log.dated_path("2026-09-30").write_text("existing\n", encoding="utf-8")
    error_log.append("[t] [m] later\n", now=_utc(2026, 10, 1))
    assert error_log.dated_path("2026-09-30").read_text(encoding="utf-8") == "existing\n"
    assert any("first" in p.read_text(encoding="utf-8") for p in ledger.error_log().parent.glob("error-2026-09-30.1.log"))


def test_per_day_cap_drops_and_counts_instead_of_growing(ledger, monkeypatch):
    monkeypatch.setattr("core.runtime_warning_log.max_day_bytes", lambda: 400)
    now = _utc(2026, 10, 1)
    for i in range(10):
        error_log.append(f"[t] [m] entry-{i} " + "x" * 80 + "\n", now=now)
    assert ledger.error_log().stat().st_size <= 400
    snap = error_log.snapshot()
    assert snap["dropped_records"] > 0 and snap["truncated_days"] == ["2026-10-01"]
    # 下一个 UTC 日恢复写入
    _set_mtime(ledger.error_log(), now)
    error_log.append("[t] [m] next-day\n", now=_utc(2026, 10, 2))
    assert "next-day" in ledger.error_log().read_text(encoding="utf-8")


def test_prune_removes_expired_days_then_oldest_over_capacity(ledger, monkeypatch):
    for day in ("2026-09-01", "2026-09-25", "2026-09-28", "2026-09-30"):
        error_log.dated_path(day).write_text("x" * 100, encoding="utf-8")
    error_log.dated_path("2026-09-02", gz=True).write_bytes(b"gz")
    result = error_log.prune(now=_utc(2026, 10, 1))
    names = set(result["removed"])
    assert {"error-2026-09-01.log", "error-2026-09-02.log.gz"} <= names
    assert error_log.dated_path("2026-09-25").exists()
    monkeypatch.setattr("core.runtime_warning_log.max_total_bytes", lambda: 250)
    result = error_log.prune(now=_utc(2026, 10, 1))
    assert "error-2026-09-25.log" in result["removed"] and error_log.dated_path("2026-09-30").exists()


def test_prune_never_deletes_the_active_file(ledger):
    error_log.append("[t] [m] keep me\n")
    error_log.prune(now=_utc(2030, 1, 1))
    assert "keep me" in ledger.error_log().read_text(encoding="utf-8")


def test_legacy_error_log_is_archived_redacted_and_idempotent(ledger):
    active = ledger.error_log()
    active.parent.mkdir(parents=True, exist_ok=True)
    active.write_text(LEGACY * 50, encoding="utf-8")
    _set_mtime(active, _utc(2026, 10, 1))
    original_lines = (LEGACY * 50).count("\n")

    error_log.ensure_migration_started()
    error_log.wait_for_migration()
    archive = error_log.dated_path("2026-10-01", gz=True)
    assert archive.exists()
    assert not active.exists()                                  # 旧明文不再裸躺
    assert not active.with_name(active.name + ".legacy-migrating").exists()
    body = gzip.open(archive, "rt", encoding="utf-8").read()
    assert "sk-live-123456" not in body and "abcdef.ghijkl.mnopqr" not in body
    assert body.count("普通中文行，必须原样保留") == 50          # 内容没丢，只脱敏
    assert body.count("certificate has expired") == 50
    assert len(body.splitlines()) == original_lines + 1         # +1 迁移说明头

    mtime = archive.stat().st_mtime_ns
    error_log.reset_for_tests()                                 # 模拟再次启动
    error_log.ensure_migration_started()
    error_log.wait_for_migration()
    assert archive.stat().st_mtime_ns == mtime
    assert len(list(active.parent.glob("error-*.log.gz"))) == 1  # 不重复归档


def test_legacy_migration_does_not_block_or_corrupt_new_writes(ledger):
    active = ledger.error_log()
    active.parent.mkdir(parents=True, exist_ok=True)
    active.write_text(LEGACY, encoding="utf-8")
    error_log.append("[t] [m] new entry after upgrade\n")
    error_log.wait_for_migration()
    assert "new entry after upgrade" in active.read_text(encoding="utf-8")
    assert "certificate has expired" not in active.read_text(encoding="utf-8")
    assert error_log.dated_path(error_log._utc_day(datetime.now(timezone.utc)), gz=True).exists() or \
        list(active.parent.glob("error-*.log.gz"))


def test_interrupted_migration_resumes_from_the_renamed_file(ledger):
    active = ledger.error_log()
    active.parent.mkdir(parents=True, exist_ok=True)
    pending = active.with_name(active.name + ".legacy-migrating")
    pending.write_text(LEGACY, encoding="utf-8")
    _set_mtime(pending, _utc(2026, 9, 20))
    error_log.ensure_migration_started()
    error_log.wait_for_migration()
    assert not pending.exists()
    body = gzip.open(error_log.dated_path("2026-09-20", gz=True), "rt", encoding="utf-8").read()
    assert "certificate has expired" in body and "sk-live-123456" not in body


def test_header_marked_active_file_is_not_treated_as_legacy(ledger):
    error_log.append("[t] [m] already new-format\n")
    error_log.reset_for_tests()
    error_log.ensure_migration_started()
    error_log.wait_for_migration()
    assert list(ledger.error_log().parent.glob("error-*.log.gz")) == []
    assert "already new-format" in ledger.error_log().read_text(encoding="utf-8")


def test_read_tail_spans_dated_and_active_files_and_skips_archives(ledger):
    error_log.dated_path("2026-09-30").write_text("old-1\nold-2\n", encoding="utf-8")
    with gzip.open(error_log.dated_path("2026-09-01", gz=True), "wt", encoding="utf-8") as handle:
        handle.write("archived-only\n")
    ledger.error_log().write_text(error_log.HEADER + "new-1\n", encoding="utf-8")
    text, total = error_log.read_tail(3)
    assert text.splitlines() == ["old-2", error_log.HEADER.rstrip("\n"), "new-1"]
    assert "archived-only" not in text and total == 4


def test_clear_removes_active_dated_and_archives(ledger):
    error_log.append("[t] [m] x\n")
    error_log.dated_path("2026-09-30").write_text("old\n", encoding="utf-8")
    error_log.dated_path("2026-09-01", gz=True).write_bytes(b"gz")
    assert error_log.clear() == 3
    assert error_log.read_tail(10) == ("", 0)


def test_append_is_fail_open_and_records_the_fault(ledger, monkeypatch):
    def boom(*_a, **_k):
        raise OSError("disk full")
    monkeypatch.setattr("builtins.open", boom)
    error_log.append("[t] [m] cannot persist\n")                # 不抛
    monkeypatch.undo()
    assert error_log.snapshot()["recent_faults"][-1]["kind"] == "append"


def test_log_error_goes_through_the_ledger_with_redaction(ledger):
    from core.error_handler import log_error
    try:
        raise RuntimeError(f"upstream failed {SECRET_URL}")
    except RuntimeError as exc:
        log_error("ledger_regression", exc)
    text = ledger.error_log().read_text(encoding="utf-8")
    assert "ledger_regression" in text and "Traceback" in text
    assert "sk-live-123456" not in text


def test_admin_logs_endpoints_read_and_clear_the_ledger(ledger, monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.setattr("admin.auth.get_admin_secret", lambda: "ledger-secret")
    from admin.admin_server import app
    headers = {"Authorization": "Bearer ledger-secret"}
    error_log.append("[t] [m] visible-in-admin\n")
    client = TestClient(app, raise_server_exceptions=False)
    assert client.get("/logs?lines=20").status_code == 401
    body = client.get("/logs?lines=20", headers=headers).json()
    assert "visible-in-admin" in body["logs"] and body["ledger"]["retention_days"] == 14
    assert client.delete("/logs", headers=headers).status_code == 200
    assert not ledger.error_log().exists()
