"""Release-audit contracts for the public first-run configuration."""
from __future__ import annotations

import json
from pathlib import Path

import yaml


def _template() -> dict:
    return yaml.safe_load(Path("config.example.yaml").read_text(encoding="utf-8")) or {}


def test_public_template_is_conservative_until_explicit_opt_in():
    cfg = _template()

    assert cfg["notify"]["enabled"] is False
    assert cfg["scheduler"]["enabled"] is False
    assert cfg["qq"]["enabled"] is False
    assert cfg["standalone_mode"] is False
    assert cfg["hardware"]["enabled"] is False
    assert cfg["hardware"]["intiface_opt_in"] is False
    assert cfg["mcp_servers"]["enabled"] is False
    assert all(not server.get("enabled", True) for server in cfg["mcp_servers"]["servers"])
    assert cfg["tool_loop"]["enabled"] is True
    assert cfg["fs_access"]["enabled"] is True
    assert cfg["fs_access"]["external_read"] is False
    assert cfg["client_channels"] == {"desktop": False, "mobile": False}


def test_public_template_keeps_frozen_services_disabled_without_restricting_ordinary_tools():
    tools = _template()["tools"]
    for name in ("device_shutdown", "device_sleep", "web_search", "desktop_open_url"):
        assert tools[name]["enabled"] is True, name
    for name in (
        "toy_vibrate",
        "toy_stop",
        "toy_pattern",
        "write_toy_file",
    ):
        assert tools.get(name, {}).get("enabled", False) is False, name


def test_tool_defaults_are_at_the_consumed_config_path(monkeypatch):
    from core import tool_dispatcher
    from core.memory.embedding import connection_configured

    cfg = _template()
    monkeypatch.setattr(tool_dispatcher, "get_config", lambda: cfg)
    for name in ("get_time", "weather", "web_search", "read_diary", "device_shutdown"):
        assert name in cfg["tools"]
        assert tool_dispatcher._is_tool_enabled(name)
    assert not tool_dispatcher._is_tool_enabled("exit_yandere")
    assert not tool_dispatcher._is_tool_enabled("toy_vibrate")
    cfg["tools"]["web_search"]["enabled"] = False
    assert not tool_dispatcher._is_tool_enabled("web_search")
    assert set(cfg["xiaohongshu"]) == {"local_service", "reader_url", "max_comments", "max_images"}
    assert set(cfg["character_document_library"]) == {"retain_raw_uploads"}
    assert not connection_configured(cfg["embedding"])


def test_clean_install_has_a_loadable_neutral_character_seed():
    cfg = _template()
    char_id = str(cfg["character"]["default"])
    card = Path("bundled/characters") / char_id / "card.json"
    assert card.is_file(), f"missing bundled character seed for {char_id!r}"
    payload = json.loads(card.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    assert str(payload.get("name") or "").strip()


def test_cold_start_runbook_documents_recovery_and_readiness_contract():
    text = Path("docs/v1-cold-start-single-user-deployment.md").read_text(encoding="utf-8")
    sections = {
        "## 全新安装": ("config.example.yaml", "setup_auth.py", "python main.py"),
        "## 保守默认值": ("scheduler.enabled: false", "/settings/mcp"),
        "## 就绪检查表": ("/admin/autonomy/effective-state", "scoped panel token"),
        "## v0.2.2 迁移": ("--fail-on-diverged --fail-on-invalid", "preview source"),
        "## Backup、restore 与 retention": (
            "backup-state create",
            "backup-state restore",
            "<new-empty-directory>",
        ),
        "## 外部 MCP 失败行为": ("unavailable", "本地聊天继续运行"),
    }
    for heading, markers in sections.items():
        assert heading in text
        for marker in markers:
            assert marker in text

    assert "restart_miss_policy: skip" in text
    assert "轮换 token" in text
    assert "不要向公网暴露 plain HTTP" in text
