"""工单 G：DLQ 积压只读观测与监控可见性（不改记忆管线、不删除、不重放）。"""
from __future__ import annotations

import json
import logging
import os
import time

import pytest

from core import dlq_inspect
# Bind modules that capture get_config at import time before any test patches it.
from core.scheduler.triggers import time_based
from admin.routers import observe

TIMEOUT_TB = (
    "Traceback (most recent call last):\n  File \"x.py\", line 1, in handler\n"
    "openai.APITimeoutError: Request timed out.\n"
)
BLOCKED_TB = (
    "Traceback (most recent call last):\n  File \"x.py\", line 1\n"
    "openai.PermissionDeniedError: Error code: 403 - {'error': {'code': 'geo_blocked'}}\n"
)
CODE_TB = "Traceback (most recent call last):\nImportError: cannot import name 'X' from 'core.y'\n"
SECRET_TB = "Traceback (most recent call last):\nRuntimeError: failed https://h.example/v1?api_key=sk-secret-1 uid=401\n"


def _write(dlq, task_type, error, *, ts_ms, payload="PRIVATE-MEMORY-TEXT"):
    path = dlq / f"{ts_ms}_{task_type}.json"
    path.write_text(json.dumps({
        "task": {"task_type": task_type, "payload": {"text": payload}},
        "error": error, "failed_at": ts_ms / 1000,
    }, ensure_ascii=False), encoding="utf-8")
    return path


@pytest.fixture
def dlq(sandbox):
    path = sandbox.dead_letter_queue()
    path.mkdir(parents=True, exist_ok=True)
    return path


def test_classify_reason_separates_outages_from_code_defects():
    assert dlq_inspect.classify_reason("openai.APITimeoutError: Request timed out.") == "timeout"
    assert dlq_inspect.classify_reason("TimeoutError") == "timeout"
    assert dlq_inspect.classify_reason("PermissionDeniedError: Error code: 403 - geo_blocked") == "upstream_blocked"
    assert dlq_inspect.classify_reason("InternalServerError: Upstream access forbidden") == "upstream_blocked"
    assert dlq_inspect.classify_reason("openai.APIConnectionError: Connection error.") == "connection"
    assert dlq_inspect.classify_reason("BadRequestError: Error code: 400 - 请求异常") == "bad_request"
    assert dlq_inspect.classify_reason("RuntimeError: consolidate_to_identity LLM 合成失败: uid=7") == "synthesis_failed"
    assert dlq_inspect.classify_reason("ImportError: cannot import name 'X'") == "code_error"
    assert dlq_inspect.classify_reason("something odd") == "other"
    # 裸数字（如 uid）不能被当成 HTTP 状态码
    assert dlq_inspect.classify_reason("RuntimeError: boom uid=401 port=503") == "other"


def test_scan_groups_by_type_and_reason_with_oldest_and_never_returns_payload(dlq):
    base = 1_780_000_000_000
    _write(dlq, "consolidate_to_identity", TIMEOUT_TB, ts_ms=base)
    _write(dlq, "consolidate_to_identity", TIMEOUT_TB, ts_ms=base + 1000)
    _write(dlq, "reflect_to_episodic", BLOCKED_TB, ts_ms=base + 2000)
    _write(dlq, "toy_autogrow", CODE_TB, ts_ms=base + 3000)
    summary = dlq_inspect.scan(dlq, max_files=200)
    assert summary["count"] == 4 and summary["cap"] == 200
    assert list(summary["by_task_type"]) == ["consolidate_to_identity", "reflect_to_episodic", "toy_autogrow"]
    ci = summary["by_task_type"]["consolidate_to_identity"]
    assert ci["count"] == 2 and ci["reasons"] == {"timeout": 2}
    assert ci["oldest_failed_at"] < ci["newest_failed_at"]
    assert summary["by_task_type"]["reflect_to_episodic"]["reasons"] == {"upstream_blocked": 1}
    assert summary["reasons"] == {"timeout": 2, "upstream_blocked": 1, "code_error": 1}
    assert summary["oldest_failed_at"].startswith("2026-")
    newest = summary["recent_samples"][0]
    assert newest["task_type"] == "toy_autogrow" and newest["reason"] == "code_error"
    assert "ImportError" in newest["error"]                       # 末行真实错误，不是 "Traceback ..."
    assert "PRIVATE-MEMORY-TEXT" not in json.dumps(summary, ensure_ascii=False)


def test_scan_redacts_and_truncates_error_samples_and_tolerates_bad_files(dlq):
    _write(dlq, "practice_session", SECRET_TB, ts_ms=1_780_000_000_000)
    (dlq / "1780000000001_reflect_to_episodic.json").write_text("{not json", encoding="utf-8")
    (dlq / "1780000000002_other.json").write_text("[]", encoding="utf-8")
    summary = dlq_inspect.scan(dlq)
    assert summary["count"] == 3 and summary["unreadable"] == 2
    joined = json.dumps(summary, ensure_ascii=False)
    assert "sk-secret-1" not in joined and "api_key=***" in joined
    assert all(len(item["error"]) <= 120 for item in summary["recent_samples"])


def test_scan_of_missing_or_empty_directory_is_empty_and_does_not_create_it(sandbox):
    summary = dlq_inspect.scan(sandbox.dead_letter_queue() / "nope")
    assert summary["count"] == 0 and summary["by_task_type"] == {} and summary["oldest_failed_at"] == ""
    assert not (sandbox.dead_letter_queue() / "nope").exists()


def test_scan_never_modifies_the_directory(dlq):
    paths = [_write(dlq, "consolidate_to_identity", TIMEOUT_TB, ts_ms=1_780_000_000_000 + i) for i in range(3)]
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in paths}
    dlq_inspect.scan(dlq)
    assert {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in dlq.glob("*.json")} == before


def test_observe_runtime_dlq_state_keeps_old_fields_and_adds_breakdown(dlq):
    _write(dlq, "reflect_to_episodic", BLOCKED_TB, ts_ms=1_780_000_000_000)
    state = observe._dlq_state()
    assert state["count"] == 1
    assert state["recent"] == [{"filename": "1780000000000_reflect_to_episodic.json",
                                "task_type": "reflect_to_episodic", "failed_at": state["recent"][0]["failed_at"]}]
    assert state["by_task_type"]["reflect_to_episodic"]["reasons"] == {"upstream_blocked": 1}
    assert state["cap"] == 200 and state["oldest_failed_at"]
    assert "PRIVATE-MEMORY-TEXT" not in json.dumps(state, ensure_ascii=False)


def test_runtime_endpoint_requires_auth_and_never_leaks_dlq_payload(dlq, monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.setattr("admin.auth.get_admin_secret", lambda: "dlq-secret")
    from admin.admin_server import app
    _write(dlq, "reflect_to_episodic", BLOCKED_TB, ts_ms=1_780_000_000_000)
    client = TestClient(app, raise_server_exceptions=False)
    assert client.get("/observe/runtime").status_code == 401
    body = client.get("/observe/runtime", headers={"Authorization": "Bearer dlq-secret"})
    assert body.status_code == 200
    state = body.json()["dead_letter_queue"]
    assert state["by_task_type"]["reflect_to_episodic"]["count"] == 1
    assert "PRIVATE-MEMORY-TEXT" not in body.text


# ── monitor ────────────────────────────────────────────────────────────────────

@pytest.fixture
def monitor(sandbox, monkeypatch):
    monkeypatch.setattr(time_based, "_is_ready", lambda name: True)
    marks = []
    monkeypatch.setattr(time_based, "_mark", lambda name: marks.append(name))
    monkeypatch.setattr(time_based, "_DLQ_LAST_COUNT", None)
    monkeypatch.setattr(time_based, "_DLQ_LAST_WARN_AT", 0.0)
    path = sandbox.dead_letter_queue()
    path.mkdir(parents=True, exist_ok=True)
    return path, marks


def _levels(caplog):
    return [(r.levelno, r.getMessage()) for r in caplog.records if r.name == time_based.logger.name and "DLQ" in r.getMessage()]


@pytest.mark.asyncio
async def test_monitor_reports_groups_reasons_oldest_and_real_error_lines(monitor, caplog):
    dlq, marks = monitor
    _write(dlq, "consolidate_to_identity", TIMEOUT_TB, ts_ms=1_780_000_000_000)
    _write(dlq, "reflect_to_episodic", BLOCKED_TB, ts_ms=1_780_000_001_000)
    with caplog.at_level(logging.INFO, logger=time_based.logger.name):
        await time_based._check_dlq_monitor()
    (level, message), = _levels(caplog)
    assert level == logging.WARNING                                   # 首次检查必为 WARNING
    assert "2 个未处理失败任务" in message and "consolidate_to_identity: 1" in message
    assert "timeout: 1" in message and "upstream_blocked: 1" in message
    assert "最早积压: 2026-" in message and "APITimeoutError" in message
    assert "Traceback (most recent call last)" not in message         # 不再是无信息量的首行
    assert marks == ["dlq_monitor"]


@pytest.mark.asyncio
async def test_monitor_is_loud_on_growth_quiet_when_flat_and_loud_again_after_a_day(monitor, caplog, monkeypatch):
    dlq, _ = monitor
    _write(dlq, "consolidate_to_identity", TIMEOUT_TB, ts_ms=1_780_000_000_000)
    with caplog.at_level(logging.INFO, logger=time_based.logger.name):
        await time_based._check_dlq_monitor()                         # 首次：WARNING
        await time_based._check_dlq_monitor()                         # 无变化：INFO
        _write(dlq, "consolidate_to_identity", TIMEOUT_TB, ts_ms=1_780_000_002_000)
        await time_based._check_dlq_monitor()                         # 增长：WARNING + 增量
        await time_based._check_dlq_monitor()                         # 又无变化：INFO
        monkeypatch.setattr(time_based, "_DLQ_LAST_WARN_AT", time.time() - time_based._DLQ_REPEAT_WARN_SECONDS - 1)
        await time_based._check_dlq_monitor()                         # 满 24h：再次 WARNING
    levels = [level for level, _ in _levels(caplog)]
    assert levels == [logging.WARNING, logging.INFO, logging.WARNING, logging.INFO, logging.WARNING]
    assert "较上次检查 +1" in _levels(caplog)[2][1]


@pytest.mark.asyncio
async def test_monitor_does_not_delete_below_the_existing_cap_and_does_not_replay(monitor, monkeypatch):
    dlq, _ = monitor
    for i in range(5):
        _write(dlq, "reflect_to_episodic", TIMEOUT_TB, ts_ms=1_780_000_000_000 + i)
    before = sorted(p.name for p in dlq.glob("*.json"))
    await time_based._check_dlq_monitor()
    assert sorted(p.name for p in dlq.glob("*.json")) == before


@pytest.mark.asyncio
async def test_monitor_existing_cap_prunes_oldest_only_and_says_so_at_warning(monitor, caplog, monkeypatch):
    dlq, _ = monitor
    monkeypatch.setattr("core.config_loader.get_config",
                        lambda: {"retention": {"dead_letter_queue": {"max_files": 3}}})
    for i in range(5):
        _write(dlq, "reflect_to_episodic", TIMEOUT_TB, ts_ms=1_780_000_000_000 + i)
    with caplog.at_level(logging.INFO, logger=time_based.logger.name):
        await time_based._check_dlq_monitor()
    assert sorted(p.name for p in dlq.glob("*.json")) == [f"{1_780_000_000_000 + i}_reflect_to_episodic.json" for i in (2, 3, 4)]
    assert any(r.levelno == logging.WARNING and "已删除 2 个最旧 DLQ 文件" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_monitor_empty_dlq_is_silent_and_resets_the_baseline(monitor, caplog):
    dlq, marks = monitor
    with caplog.at_level(logging.INFO, logger=time_based.logger.name):
        await time_based._check_dlq_monitor()
    assert _levels(caplog) == [] and marks == ["dlq_monitor"] and time_based._DLQ_LAST_COUNT == 0


def test_dlq_monitor_runs_every_six_hours():
    from core.scheduler import loop
    assert loop._COOLDOWNS["dlq_monitor"] == 6 * 3600
