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


def test_non_allowlisted_domain_is_rejected_before_task_creation(monkeypatch, sandbox):
    _config(monkeypatch)
    from core.agent_runtime import TaskPrincipal, browser

    with pytest.raises(browser.BrowserError, match="domain_not_allowed"):
        browser.create_task(
            TaskPrincipal.reality("browser-owner", "browser-character"),
            url="https://not-allowed.example/",
            operation="read_page",
            idempotency_key="not-allowed",
        )


def test_high_risk_confirmed_flag_cannot_bypass_waiting_state(monkeypatch, sandbox):
    _config(monkeypatch)
    from core.agent_runtime import TaskPrincipal
    from core.agent_runtime.browser import create_task

    receipt = create_task(
        TaskPrincipal.reality("browser-owner", "browser-character"),
        url="https://example.test/post",
        operation="post",
        idempotency_key="post-flag",
        confirmed=True,
    )
    assert receipt["status"] == "waiting_confirm"
    assert receipt["request_summary"]["confirmed"] is False


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


@pytest.mark.asyncio
async def test_browser_request_binding_rejects_substitution_before_claim(monkeypatch, sandbox):
    _config(monkeypatch)
    from core.agent_runtime import TaskPrincipal, browser

    calls = 0

    async def adapter(**kwargs):
        nonlocal calls
        calls += 1
        return {"text": "ok"}

    browser.set_adapter(adapter)
    principal = TaskPrincipal.reality("browser-owner", "browser-character")
    task = browser.create_task(
        principal,
        url="https://example.test/page?a=1",
        operation="fill",
        params={"selector": "#name", "value": "Ada"},
        idempotency_key="bind-1",
    )
    before = task["attempt_count"]
    for changed in (
        {"url": "https://example.test/other?a=1"},
        {"operation": "click"},
        {"params": {"selector": "#other", "value": "Ada"}},
        {"params": {"selector": "#name", "value": "Eve"}},
    ):
        with pytest.raises(browser.BrowserError, match="task_request_mismatch"):
            await browser.run_task(
                principal,
                task["task_id"],
                url=changed.get("url", "https://example.test/page?a=1"),
                operation=changed.get("operation", "fill"),
                params=changed.get("params", {"selector": "#name", "value": "Ada"}),
            )
        current = browser.task_manager.get_task(principal, task["task_id"])
        assert current["attempt_count"] == before == 0
    result = await browser.run_task(
        principal,
        task["task_id"],
        url="https://example.test/page?a=1",
        operation="fill",
        params={"selector": "#name", "value": "Ada"},
    )
    assert result["receipt"]["status"] == "succeeded"
    assert calls == 1


@pytest.mark.asyncio
async def test_high_risk_confirmation_is_one_shot_and_bound(monkeypatch, sandbox):
    _config(monkeypatch)
    from core.agent_runtime import TaskPrincipal, browser

    calls = 0

    async def adapter(**kwargs):
        nonlocal calls
        calls += 1
        return {"ok": True}

    browser.set_adapter(adapter)
    principal = TaskPrincipal.reality("browser-owner", "browser-character")
    task = browser.create_task(
        principal,
        url="https://example.test/pay",
        operation="pay",
        idempotency_key="confirm-1",
    )
    with pytest.raises(browser.BrowserError, match="confirmation_required"):
        await browser.run_task(principal, task["task_id"], url="https://example.test/pay", operation="pay")
    confirmed = browser.confirm_task(principal, task["task_id"])
    assert confirmed["status"] == "queued"
    assert browser.confirm_task(principal, task["task_id"])["status"] == "queued"
    with pytest.raises(browser.BrowserError, match="task_request_mismatch"):
        await browser.run_task(principal, task["task_id"], url="https://example.test/pay", operation="pay", confirmed=False)
    result = await browser.run_task(principal, task["task_id"], url="https://example.test/pay", operation="pay", confirmed=True)
    assert result["receipt"]["status"] == "succeeded"
    assert calls == 1
    with pytest.raises(browser.BrowserError, match="task_not_queued"):
        await browser.run_task(principal, task["task_id"], url="https://example.test/pay", operation="pay", confirmed=True)
    assert calls == 1


def test_redirect_domain_is_fail_closed(monkeypatch):
    _config(monkeypatch)
    from core.agent_runtime import browser

    with pytest.raises(browser.BrowserError, match="redirect_domain_not_allowed"):
        browser._validate_final_url("https://evil.example/landing?token=redacted", browser.policy())


def test_reality_only_and_disabled_modes_fail_closed(monkeypatch, sandbox):
    from core.agent_runtime import TaskPrincipal, browser

    _config(monkeypatch, enabled=False)
    with pytest.raises(browser.BrowserError, match="browser_disabled"):
        browser.create_task(TaskPrincipal.reality("owner", "character"), url="https://example.test", operation="read_page", idempotency_key="disabled")

    _config(monkeypatch, enabled=True)
    with pytest.raises(browser.BrowserError, match="realm_forbidden"):
        browser.create_task(TaskPrincipal(uid="owner", char_id="character", realm="dream"), url="https://example.test", operation="read_page", idempotency_key="dream")

    monkeypatch.setattr("core.agent_runtime.browser.is_remote_server", lambda: True)
    with pytest.raises(browser.BrowserError, match="disabled_remote_server_local_capability"):
        browser.create_task(TaskPrincipal.reality("owner", "character"), url="https://example.test", operation="read_page", idempotency_key="remote")


@pytest.mark.asyncio
async def test_adapter_unavailable_fails_closed(monkeypatch, sandbox):
    _config(monkeypatch)
    from core.agent_runtime import TaskPrincipal, browser

    browser.set_adapter(None)
    principal = TaskPrincipal.reality("browser-owner", "browser-character")
    task = browser.create_task(principal, url="https://example.test", operation="read_page", idempotency_key="no-adapter")
    result = await browser.run_task(principal, task["task_id"], url="https://example.test", operation="read_page")
    assert result["receipt"]["status"] == "failed"
    assert result["receipt"]["error_code"] == "browser_adapter_unavailable"
