from __future__ import annotations

import pytest


def _config(monkeypatch, *, enabled=True, domains=("example.test",)):
    monkeypatch.setattr("core.config_loader.get_config", lambda: {"browser": {"enabled": enabled, "allowed_domains": list(domains), "worker_timeout_seconds": 1}})
    monkeypatch.setattr("core.agent_runtime.browser.is_remote_server", lambda: False)


@pytest.mark.asyncio
async def test_browser_worker_isolated_redacted_and_bounded(monkeypatch, sandbox):
    _config(monkeypatch)
    from core.agent_runtime import TaskPrincipal
    from core.agent_runtime import browser

    seen = {}

    async def adapter(**kwargs):
        seen.update(kwargs)
        return {"text": "x" * 20000, "cookie": "secret", "links": ["a"] * 50, "fields": {"ok": 1}}

    browser.set_adapter(adapter)
    principal = TaskPrincipal.reality("browser-owner", "browser-character")
    task = browser.create_task(principal, url="https://example.test/page#secret", operation="read_page", idempotency_key="read-1")
    result = await browser.run_task(principal, task["task_id"], url="https://example.test/page", operation="read_page")
    assert result["receipt"]["status"] == "succeeded"
    assert "cookie" not in result["result"]
    assert len(result["result"]["text"]) <= 12000
    assert len(result["result"]["links"]) <= 30
    assert seen["profile_dir"] != sandbox.root_dir()
    assert "secret" not in str(result)


def test_high_risk_operation_waits_for_confirmation(monkeypatch, sandbox):
    _config(monkeypatch)
    from core.agent_runtime import TaskPrincipal
    from core.agent_runtime.browser import create_task

    receipt = create_task(TaskPrincipal.reality("browser-owner", "browser-character"), url="https://example.test/pay", operation="pay", idempotency_key="pay-1")
    assert receipt["status"] == "waiting_confirm"


@pytest.mark.asyncio
async def test_browser_disconnect_becomes_outcome_unknown(monkeypatch, sandbox):
    _config(monkeypatch)
    from core.agent_runtime import TaskPrincipal
    from core.agent_runtime import browser

    async def adapter(**kwargs):
        raise ConnectionError("disconnected")

    browser.set_adapter(adapter)
    principal = TaskPrincipal.reality("browser-owner", "browser-character")
    task = browser.create_task(principal, url="https://example.test/page", operation="navigate", idempotency_key="nav-1")
    result = await browser.run_task(principal, task["task_id"], url="https://example.test/page", operation="navigate")
    assert result["receipt"]["status"] == "outcome_unknown"
    assert result["receipt"]["error_code"] == "browser_disconnected"
