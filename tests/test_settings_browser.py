import asyncio

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
    import pytest
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        asyncio.run(mod.update_browser_settings(mod.BrowserSettingsUpdate(allowed_domains=["https://bad.example"]), None))
    assert exc.value.status_code == 422
