from __future__ import annotations

import asyncio

import yaml

from admin.routers import settings_llm as mod


def test_admin_debug_request_settings_are_opt_in_and_persisted(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("model_presets:\n  presets: {}\n", encoding="utf-8")
    monkeypatch.setattr(mod, "CONFIG_FILE", config_path)
    read = lambda: yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    monkeypatch.setattr(mod, "get_config", read)
    from core import config_loader
    monkeypatch.setattr(config_loader, "reload_config", lambda: read())

    initial = asyncio.run(mod.get_llm_debug_requests(_auth=None))
    assert initial == {"enabled": False, "keep_days": 1}

    updated = asyncio.run(mod.update_llm_debug_requests(
        mod.LlmDebugRequestsUpdate(enabled=True, keep_days=3), _auth=None,
    ))

    assert updated == {"enabled": True, "keep_days": 3}
    assert read()["llm_debug_requests"] == {"enabled": True, "keep_days": 3}
