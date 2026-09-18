"""Admin tool-control API: registry observation and model-specific exposure."""
from unittest.mock import patch

import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

VALID_TOKEN = "tools-test-secret"


def _auth():
    return {"Authorization": f"Bearer {VALID_TOKEN}"}


def _config():
    return {
        "tools": {},
        "tool_loop": {"tool_presets": []},
        "model_presets": {
            "presets": {
                "claude-pig": {"provider_kind": "anthropic_compat", "model": "claude"},
                "gpt": {"provider_kind": "openai", "model": "gpt"},
            },
        },
    }


def test_tool_control_saves_preset_binding_and_builtin_execution(tmp_path, monkeypatch):
    import admin.routers.settings_tools as st

    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(_config(), allow_unicode=True), encoding="utf-8")
    monkeypatch.setattr(st, "CONFIG_FILE", path)
    monkeypatch.setattr("admin.auth.get_admin_secret", lambda: VALID_TOKEN)
    monkeypatch.setattr(st, "get_config", lambda: yaml.safe_load(path.read_text(encoding="utf-8")))
    monkeypatch.setattr("core.tools.fs_browse.effective_state", lambda: {"enabled": False})

    with patch("core.config_loader.reload_config", return_value=None):
        app = FastAPI()
        app.include_router(st.router)
        client = TestClient(app)
        discovered = client.get("/settings/tools", headers=_auth())
        assert discovered.status_code == 200
        builtin = discovered.json()["tools"][0]["name"]

        updated = client.put(
            "/settings/tools",
            headers=_auth(),
            json={
                "tool_presets": [{"name": "claude-minimal", "tools": [builtin]}],
                "model_bindings": {"claude-pig": "claude-minimal"},
                "execution_enabled": {builtin: False},
            },
        )
        assert updated.status_code == 200
        saved = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert saved["tool_loop"]["tool_presets"] == [{"name": "claude-minimal", "tools": [builtin]}]
        assert saved["model_presets"]["presets"]["claude-pig"]["tool_preset"] == "claude-minimal"
        assert saved["tools"][builtin]["enabled"] is False


def test_deleting_tool_preset_clears_existing_model_bindings(tmp_path, monkeypatch):
    import admin.routers.settings_tools as st

    cfg = _config()
    cfg["tool_loop"]["tool_presets"] = [{"name": "old", "tools": ["get_time"]}]
    cfg["model_presets"]["presets"]["claude-pig"]["tool_preset"] = "old"
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    monkeypatch.setattr(st, "CONFIG_FILE", path)
    monkeypatch.setattr("admin.auth.get_admin_secret", lambda: VALID_TOKEN)
    monkeypatch.setattr(st, "get_config", lambda: yaml.safe_load(path.read_text(encoding="utf-8")))
    monkeypatch.setattr("core.tools.fs_browse.effective_state", lambda: {"enabled": False})

    with patch("core.config_loader.reload_config", return_value=None):
        app = FastAPI()
        app.include_router(st.router)
        response = TestClient(app).put("/settings/tools", headers=_auth(), json={"tool_presets": []})

    assert response.status_code == 200
    saved = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert "tool_preset" not in saved["model_presets"]["presets"]["claude-pig"]


def test_tool_page_hides_transient_mcp_tools_and_reports_only_global_state(tmp_path, monkeypatch):
    import admin.routers.settings_tools as st
    from core import tool_dispatcher

    cfg = _config()
    cfg["mcp_servers"] = {"enabled": True}
    cfg["tool_loop"].update({
        "tool_presets": [{"name": "mixed", "tools": ["builtin", "dynamic"]}],
        "categories": ["info", "mcp"],
        "exclude_tools": [],
    })
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    monkeypatch.setattr(st, "CONFIG_FILE", path)
    monkeypatch.setattr("admin.auth.get_admin_secret", lambda: VALID_TOKEN)
    monkeypatch.setattr(st, "get_config", lambda: yaml.safe_load(path.read_text(encoding="utf-8")))
    monkeypatch.setattr(tool_dispatcher, "_TOOL_REGISTRY", {
        "builtin": {"category": "info", "description": "built in"},
        "dynamic": {"category": "mcp", "description": "transient"},
    })

    app = FastAPI()
    app.include_router(st.router)
    result = TestClient(app).get("/settings/tools", headers=_auth()).json()

    assert result["mcp_enabled"] is True
    assert [tool["name"] for tool in result["tools"]] == ["builtin"]
    assert result["tool_presets"] == [{"name": "mixed", "tools": ["builtin"]}]
    assert result["global_default_tools"] == ["builtin"]


def test_global_default_tools_compile_to_categories_and_exclusions_without_touching_unmanaged_categories(tmp_path, monkeypatch):
    import admin.routers.settings_tools as st
    from core import tool_dispatcher

    cfg = _config()
    cfg["tool_loop"].update({"categories": ["mcp", "info"], "exclude_tools": ["legacy_dynamic"]})
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    monkeypatch.setattr(st, "CONFIG_FILE", path)
    monkeypatch.setattr("admin.auth.get_admin_secret", lambda: VALID_TOKEN)
    monkeypatch.setattr(st, "get_config", lambda: yaml.safe_load(path.read_text(encoding="utf-8")))
    monkeypatch.setattr(tool_dispatcher, "_TOOL_REGISTRY", {
        "read_one": {"category": "info", "description": "read one"},
        "read_two": {"category": "info", "description": "read two"},
        "dynamic": {"category": "mcp", "description": "dynamic"},
    })

    app = FastAPI()
    app.include_router(st.router)
    with patch("core.config_loader.reload_config", return_value=None):
        response = TestClient(app).put("/settings/tools", headers=_auth(), json={"global_default_tools": ["read_one"]})

    assert response.status_code == 200
    saved = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert saved["tool_loop"]["categories"] == ["mcp", "info"]
    assert saved["tool_loop"]["exclude_tools"] == ["legacy_dynamic", "read_two"]


def test_weather_defaults_to_direct_and_can_enable_proxy(tmp_path, monkeypatch):
    import admin.routers.settings_tools as st

    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(_config(), allow_unicode=True), encoding="utf-8")
    monkeypatch.setattr(st, "CONFIG_FILE", path)
    monkeypatch.setattr("admin.auth.get_admin_secret", lambda: VALID_TOKEN)
    monkeypatch.setattr(st, "get_config", lambda: yaml.safe_load(path.read_text(encoding="utf-8")))
    monkeypatch.setattr("core.tools.fs_browse.effective_state", lambda: {"enabled": False})

    with patch("core.config_loader.reload_config", return_value=None):
        app = FastAPI()
        app.include_router(st.router)
        client = TestClient(app)
        discovered = client.get("/settings/tools", headers=_auth())
        assert discovered.status_code == 200
        assert discovered.json()["weather"] == {"use_proxy": False}

        updated = client.put("/settings/tools", headers=_auth(), json={"weather": {"use_proxy": True}})
        assert updated.status_code == 200
        assert updated.json()["weather"] == {"use_proxy": True}

    saved = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert saved["tools"]["weather"]["use_proxy"] is True
    assert saved["tools"]["weather"]["enabled"] is True


def test_path_exposure_can_be_saved_independently_for_a_and_c(tmp_path, monkeypatch):
    import admin.routers.settings_tools as st
    from core import tool_dispatcher

    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(_config(), allow_unicode=True), encoding="utf-8")
    monkeypatch.setattr(st, "CONFIG_FILE", path)
    monkeypatch.setattr("admin.auth.get_admin_secret", lambda: VALID_TOKEN)
    monkeypatch.setattr(st, "get_config", lambda: yaml.safe_load(path.read_text(encoding="utf-8")))
    monkeypatch.setattr(tool_dispatcher, "_TOOL_REGISTRY", {
        "get_time": {"category": "info", "description": "time"},
        "fs_list": {"category": "fs", "description": "files"},
    })

    app = FastAPI()
    app.include_router(st.router)
    with patch("core.config_loader.reload_config", return_value=None):
        response = TestClient(app).put(
            "/settings/tools",
            headers=_auth(),
            json={"exposure": {
                "path_a": {"categories": ["info"], "tools": ["get_time"]},
                "path_c": {"categories": ["fs"], "exclude_tools": ["fs_list"]},
            }},
        )

    assert response.status_code == 200
    saved = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert saved["tool_exposure"]["path_a"] == {"categories": ["info"], "tools": ["get_time"]}
    assert saved["tool_exposure"]["path_c"] == {"categories": ["fs"], "exclude_tools": ["fs_list"]}
    assert response.json()["path_exposure"]["path_a"]["tools"] == ["get_time"]
