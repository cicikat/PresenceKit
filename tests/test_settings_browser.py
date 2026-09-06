import asyncio

import pytest
import yaml

from admin.routers import settings_browser as mod


def test_browser_settings_update_is_allowlisted_and_reloads(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    path.write_text("browser:\n  enabled: false\n", encoding="utf-8")
    monkeypatch.setattr(mod, "get_config_path", lambda: path)
    monkeypatch.setattr(mod, "get_config", lambda: yaml.safe_load(path.read_text(encoding="utf-8")))
    from core import config_loader
    monkeypatch.setattr(config_loader, "reload_config", lambda: None)
    calls = []
    import core.agent_runtime.browser as browser
    async def stop_worker():
        calls.append("stop")
    async def start_worker():
        calls.append("start")
    monkeypatch.setattr(browser, "stop_worker", stop_worker)
    monkeypatch.setattr(browser, "start_worker", start_worker)
    monkeypatch.setattr(mod, "_snapshot", lambda: {"effective_state": "enabled"})

    result = asyncio.run(mod.update_browser_settings(mod.BrowserSettingsUpdate(enabled=True, allowed_domains=["Example.COM."]), None))
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert cfg["browser"]["enabled"] is True
    assert cfg["browser"]["allowed_domains"] == ["example.com"]
    assert calls == ["stop", "start"]
    assert result["effective_state"] == "enabled"


def test_browser_settings_reject_invalid_domain(monkeypatch):
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        asyncio.run(mod.update_browser_settings(mod.BrowserSettingsUpdate(allowed_domains=["https://bad.example"]), None))
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_admin_task_surface_uses_shared_service_and_confirms_before_mutation(monkeypatch, sandbox):
    monkeypatch.setattr(mod, "_scope", lambda: ("browser-owner", "browser-character"))
    monkeypatch.setattr(
        "core.config_loader.get_config",
        lambda: {"browser": {"enabled": True, "allowed_domains": ["example.test"], "worker_timeout_seconds": 1}},
    )
    monkeypatch.setattr("core.agent_runtime.browser.is_remote_server", lambda: False)
    from core.agent_runtime import browser

    async def adapter(**kwargs):
        return {"text": "fixture page", "links": []}

    browser.set_adapter(adapter)
    safe = await mod.create_browser_task(
        mod.BrowserTaskRequest(url="https://example.test/page", operation="read_page", idempotency_key="admin-safe"),
        None,
    )
    assert safe["receipt"]["status"] == "succeeded"
    high_risk = await mod.create_browser_task(
        mod.BrowserTaskRequest(url="https://example.test/post", operation="post", idempotency_key="admin-post"),
        None,
    )
    assert high_risk["receipt"]["status"] == "waiting_confirm"
    from core.agent_runtime.models import TaskPrincipal
    from fastapi import HTTPException
    current = browser.task_manager.get_task(TaskPrincipal.reality("browser-owner", "browser-character"), high_risk["receipt"]["task_id"])
    with pytest.raises(HTTPException) as exc:
        await mod.confirm_browser_task(
            high_risk["receipt"]["task_id"],
            mod.BrowserTaskRequest(url="https://example.test/other", operation="post", idempotency_key="admin-post"),
            None,
        )
    assert exc.value.status_code == 422
    unchanged = browser.task_manager.get_task(TaskPrincipal.reality("browser-owner", "browser-character"), high_risk["receipt"]["task_id"])
    assert unchanged["status"] == current["status"] == "waiting_confirm"


def test_browser_routes_openapi_exposes_only_admin_canonical_surface():
    from admin.admin_server import app

    paths = app.openapi()["paths"]
    assert "/agent-runtime-browser/tasks" not in paths
    assert "/observability/agent-runtime-browser" not in paths
    assert paths["/settings/agent-runtime-browser"]["put"].get("deprecated") is not True


def test_browser_observability_projection_is_metadata_only(monkeypatch):
    monkeypatch.setattr(
        "core.config_loader.get_config",
        lambda: {"browser": {"enabled": True, "allowed_domains": ["example.test"]}},
    )
    from core.agent_runtime.browser import observability_snapshot

    payload = observability_snapshot()
    assert payload["credentials_exposed"] is False
    assert payload["profile_exposed"] is False
    assert payload["url_exposed"] is False
    assert "url" not in payload and "query" not in payload and "params" not in payload


def test_retired_browser_observability_route_is_not_published(monkeypatch):
    secret = "brief-240-browser-observability-secret"
    monkeypatch.setattr("admin.auth.get_admin_secret", lambda: secret)
    from fastapi.testclient import TestClient
    from admin.admin_server import app

    response = TestClient(app, raise_server_exceptions=False).get(
        "/observability/agent-runtime-browser",
        headers={"Authorization": f"Bearer {secret}"},
    )
    assert response.status_code == 404
