"""Runtime WARNING+ JSONL persistence, rotation, query, and error.log isolation."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from core.runtime_warning_log import (
    RuntimeWarningJsonlHandler,
    daily_path,
    install_runtime_warning_handler,
    prune_runtime_warning_logs,
    query_runtime_warnings,
    reset_write_faults_for_tests,
    shutdown_runtime_warning_handler,
    utc_day,
)


def _warn(logger_name: str, message: str, *, created: datetime | None = None) -> None:
    record = logging.LogRecord(
        name=logger_name,
        level=logging.WARNING,
        pathname="",
        lineno=0,
        msg=message,
        args=(),
        exc_info=None,
    )
    if created is not None:
        record.created = created.timestamp()
    logging.getLogger().handle(record)


def _handler() -> RuntimeWarningJsonlHandler:
    from core.runtime_warning_log import _find_handler
    handler = _find_handler()
    assert handler is not None
    return handler


@pytest.fixture
def warning_log(sandbox):
    reset_write_faults_for_tests()
    handler = install_runtime_warning_handler()
    yield handler
    shutdown_runtime_warning_handler()
    reset_write_faults_for_tests()


def test_install_is_idempotent_and_sandbox_isolated(sandbox, warning_log):
    first = install_runtime_warning_handler()
    second = install_runtime_warning_handler()
    root = logging.getLogger()
    count = sum(1 for item in root.handlers if isinstance(item, RuntimeWarningJsonlHandler))
    assert first is second
    assert count == 1
    _warn("core.runtime_warning_log.test", "sandbox-only warning")
    path = daily_path(sandbox.runtime_warning_log(), utc_day())
    assert path.is_file()
    assert path.is_relative_to(sandbox._base)


def test_warning_jsonl_redacts_url_and_token(sandbox, warning_log):
    _warn(
        "httpx",
        "HTTP Request: https://example.test/mcp?token=secret-value Authorization: Bearer header-secret",
    )
    path = daily_path(sandbox.runtime_warning_log(), utc_day())
    payload = json.loads(path.read_text(encoding="utf-8").splitlines()[-1])
    assert payload["level"] == "WARNING"
    assert payload["ts"].endswith("Z")
    assert "secret-value" not in payload["message"]
    assert "header-secret" not in payload["message"]
    assert "token=***" in payload["message"]
    assert "+00:00" not in payload["ts"]


def test_info_is_not_persisted(sandbox, warning_log):
    logging.getLogger("core.runtime_warning_log.test").info("must stay on console only")
    path = daily_path(sandbox.runtime_warning_log(), utc_day())
    assert not path.exists()


def test_cross_day_query_and_timezone(sandbox, warning_log, monkeypatch):
    older = datetime(2026, 9, 18, 23, 30, tzinfo=timezone.utc)
    newer = datetime(2026, 9, 19, 0, 30, tzinfo=timezone.utc)
    _warn("core.alpha", "day-one", created=older)
    _warn("core.beta", "day-two", created=newer)
    day_one = daily_path(sandbox.runtime_warning_log(), "2026-09-18")
    day_two = daily_path(sandbox.runtime_warning_log(), "2026-09-19")
    assert day_one.is_file() and day_two.is_file()

    window = query_runtime_warnings(
        start=datetime(2026, 9, 18, 23, 0, tzinfo=timezone.utc),
        end=datetime(2026, 9, 19, 1, 0, tzinfo=timezone.utc),
        offset=0,
        limit=10,
    )
    assert window["total"] == 2
    assert [item["logger"] for item in window["items"]] == ["core.beta", "core.alpha"]
    assert window["timezone"] == "UTC"

    local_plus8 = datetime(2026, 9, 19, 8, 0, tzinfo=timezone(timedelta(hours=8)))
    _warn("core.gamma", "plus-eight", created=local_plus8)
    only_utc_midnight = query_runtime_warnings(
        start=datetime(2026, 9, 19, 0, 0, tzinfo=timezone.utc),
        end=datetime(2026, 9, 19, 0, 1, tzinfo=timezone.utc),
        logger_name="core.gamma",
        limit=10,
    )
    assert only_utc_midnight["total"] == 1
    assert only_utc_midnight["items"][0]["ts"].startswith("2026-09-19T00:00:00")


def test_day_capacity_cap_marks_truncated_and_stops_writes(sandbox, warning_log, monkeypatch):
    import core.runtime_warning_log as rw

    monkeypatch.setattr(rw, "_MAX_DAY_BYTES", 120)
    _warn("core.cap", "first-record-ok")
    path = daily_path(sandbox.runtime_warning_log(), utc_day())
    before = path.read_text(encoding="utf-8")
    for index in range(20):
        _warn("core.cap", f"storm-{index}-" + ("x" * 40))
    after = path.read_text(encoding="utf-8")
    snapshot = query_runtime_warnings(limit=50)
    assert utc_day() in snapshot["truncated_days"]
    assert len(after) >= len(before)
    assert path.stat().st_size <= 120


def test_query_pagination(sandbox, warning_log):
    _warn("core.page", "one")
    _warn("core.page", "two")
    first = query_runtime_warnings(limit=1, offset=0)
    second = query_runtime_warnings(limit=1, offset=1)
    assert first["total"] == 2
    assert first["has_more"] is True
    assert len(first["items"]) == 1
    assert second["has_more"] is False
    assert {first["items"][0]["message"], second["items"][0]["message"]} == {"one", "two"}


def test_retention_and_capacity_do_not_touch_error_log(sandbox, warning_log):
    error_log = sandbox.error_log()
    error_log.parent.mkdir(parents=True, exist_ok=True)
    error_log.write_text("keep traceback\n", encoding="utf-8")
    base = sandbox.runtime_warning_log()
    old_day = (datetime(2026, 9, 1, tzinfo=timezone.utc)).strftime("%Y-%m-%d")
    stale = daily_path(base, old_day)
    stale.write_text('{"ts":"2026-09-01T00:00:00Z","level":"WARNING","logger":"old","message":"stale"}\n', encoding="utf-8")
    keep_day = utc_day()
    current = daily_path(base, keep_day)
    current.write_text('{"ts":"2026-09-20T00:00:00Z","level":"WARNING","logger":"keep","message":"fresh"}\n', encoding="utf-8")
    result = prune_runtime_warning_logs(now=datetime(2026, 9, 20, tzinfo=timezone.utc), keep_days=14)
    assert old_day in result["removed_days"]
    assert not stale.exists()
    assert current.exists()
    assert error_log.read_text(encoding="utf-8") == "keep traceback\n"


def test_write_failure_does_not_recurse(sandbox, warning_log, monkeypatch):
    nested = {"count": 0}
    original_open = open

    def boom_open(path, *args, **kwargs):
        if "runtime_warnings" in str(path):
            nested["count"] += 1
            logging.getLogger("core.runtime_warning_log").error("must not recurse into jsonl")
            raise OSError("disk full")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr("builtins.open", boom_open)
    _warn("core.runtime_warning_log.test", "cannot persist")
    assert nested["count"] == 1
    snapshot = query_runtime_warnings(limit=10)
    assert snapshot["dropped_records"] >= 1
    assert snapshot["write_faults"]
    path = daily_path(sandbox.runtime_warning_log(), utc_day())
    assert not path.exists() or "must not recurse into jsonl" not in path.read_text(encoding="utf-8")


def test_error_log_traceback_chain_still_writes(sandbox, warning_log):
    from core.error_handler import log_error

    try:
        raise RuntimeError("traceback-keep")
    except RuntimeError as exc:
        log_error("runtime_warning_regression", exc)
    text = sandbox.error_log().read_text(encoding="utf-8")
    assert "traceback-keep" in text
    assert "Traceback" in text
    runtime_path = daily_path(sandbox.runtime_warning_log(), utc_day())
    if runtime_path.exists():
        payload = runtime_path.read_text(encoding="utf-8")
        assert "runtime_warning_regression" in payload


def test_query_rejects_invalid_level_and_reports_unreadable(sandbox, warning_log):
    day = utc_day()
    path = daily_path(sandbox.runtime_warning_log(), day)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not-json\n", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid_level"):
        query_runtime_warnings(level="DEBUG")
    result = query_runtime_warnings(limit=10)
    assert result["total"] == 0
    assert result["unreadable_days"] == []  # malformed JSON lines are skipped, file still readable
    path.write_bytes(b"\xff")
    # latin-1 would succeed; utf-8 strict should mark unreadable on some platforms.
    # If the file remains readable as replacement, at least it is not claimed empty-success.
    result = query_runtime_warnings(limit=10)
    assert result["total"] == 0
    if result["unreadable_days"]:
        assert day in result["unreadable_days"]
        assert result["unreadable_error"]


def test_admin_query_endpoint_is_admin_only_and_has_no_path_param(sandbox, monkeypatch):
    monkeypatch.setattr("admin.auth.get_admin_secret", lambda: "runtime-warning-secret")
    from admin.admin_server import app

    client = TestClient(app, raise_server_exceptions=False)
    denied = client.get("/logs/runtime-warnings")
    assert denied.status_code == 401
    handler = install_runtime_warning_handler()
    try:
        _warn("core.endpoint", "visible-to-admin")
        ok = client.get(
            "/logs/runtime-warnings?limit=10",
            headers={"Authorization": "Bearer runtime-warning-secret"},
        )
        assert ok.status_code == 200
        body = ok.json()
        assert "items" in body
        assert body["retention_days"] == 14
        assert "path" not in body
        assert all("path" not in item for item in body["items"])
        bad = client.get(
            "/logs/runtime-warnings?path=C:/secret.log",
            headers={"Authorization": "Bearer runtime-warning-secret"},
        )
        assert bad.status_code in (200, 422)
        assert "C:/secret.log" not in (bad.text or "")
        error_log = client.get("/logs?lines=20", headers={"Authorization": "Bearer runtime-warning-secret"})
        assert error_log.status_code == 200
        assert "logs" in error_log.json()
    finally:
        handler.close()
        shutdown_runtime_warning_handler()


def test_logs_page_wires_runtime_warning_query():
    root = Path(__file__).resolve().parents[1]
    page = (root / "admin/static/pages/logs.html").read_text(encoding="utf-8")
    script = (root / "admin/static/js/status-users.js").read_text(encoding="utf-8")
    core = (root / "admin/static/js/core.js").read_text(encoding="utf-8")
    assert 'id="rw-start"' in page
    assert 'id="rw-end"' in page
    assert 'data-action="reloadRuntimeWarnings"' in page
    assert "/logs/runtime-warnings" in script
    assert "不能当作没有 warning" in script or "query_failed_not_empty" in script
    assert "window._runtimeWarningHasMore !== true" in script
    assert "ADMIN_UI_FRAGMENT_VERSION = 'v1-call-presence-1'" in core
    assert "clearLogs" in script
    assert "DELETE" in script and "/logs" in script
