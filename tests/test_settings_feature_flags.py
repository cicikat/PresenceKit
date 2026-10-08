import asyncio

import pytest
import yaml

from admin.routers import settings_feature_flags as mod


@pytest.fixture(autouse=True)
def _stub_screen_observation_state(monkeypatch):
    from core.perception import screen_observation

    monkeypatch.setattr(
        screen_observation,
        "state",
        lambda: {"enabled": False, "active_device": None},
    )


def test_feature_flags_update_is_allowlisted(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    path.write_text("practice:\n  enabled: false\n", encoding="utf-8")
    monkeypatch.setattr(mod, "CONFIG_FILE", path)
    monkeypatch.setattr(mod, "get_config", lambda: yaml.safe_load(path.read_text(encoding="utf-8")))
    from core import config_loader
    monkeypatch.setattr(config_loader, "reload_config", lambda: None)

    result = asyncio.run(mod.update_feature_flags(mod.FeatureFlagsUpdate(flags={"practice": True}), auth=None))
    assert result["flags"]["practice"]["enabled"] is True
    assert yaml.safe_load(path.read_text(encoding="utf-8"))["practice"]["enabled"] is True


def test_self_management_feature_flag_is_exposed_and_consumed(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    path.write_text("self_management:\n  enabled: true\n", encoding="utf-8")
    monkeypatch.setattr(mod, "CONFIG_FILE", path)
    read_config = lambda: yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    monkeypatch.setattr(mod, "get_config", read_config)
    from core import config_loader
    from core.perception import screen_observation
    from core.self_management import policy
    monkeypatch.setattr(config_loader, "get_config", read_config)
    monkeypatch.setattr(config_loader, "reload_config", lambda: None)
    monkeypatch.setattr(
        screen_observation,
        "state",
        lambda: {"enabled": False, "active_device": None},
    )

    snapshot = asyncio.run(mod.get_feature_flags(auth=None))["flags"]["self_management"]
    assert snapshot["enabled"] is True
    assert "不是关能力" in snapshot["label"]
    assert "不是能力总闸" in snapshot["description"]
    result = asyncio.run(mod.update_feature_flags(mod.FeatureFlagsUpdate(flags={"self_management": False}), auth=None))
    assert result["flags"]["self_management"]["enabled"] is False
    assert policy.feature_enabled() is False
    assert policy.effective("web_search", "owner", "char") == (True, None)


def test_feature_flags_reject_unknown(monkeypatch):
    import pytest
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        asyncio.run(mod.update_feature_flags(mod.FeatureFlagsUpdate(flags={"api_key": True}), auth=None))
    assert exc.value.status_code == 422


def test_qq_reply_scopes_roundtrip_and_effective_state(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    path.write_text("qq:\n  enabled: true\n", encoding="utf-8")
    monkeypatch.setattr(mod, "CONFIG_FILE", path)
    monkeypatch.setattr(mod, "get_config", lambda: yaml.safe_load(path.read_text(encoding="utf-8")))
    from core import config_loader
    monkeypatch.setattr(config_loader, "reload_config", lambda: None)
    initial = asyncio.run(mod.get_feature_flags(auth=None))["flags"]
    assert initial["qq_group"]["effective_state"] == "disabled"
    result = asyncio.run(mod.update_feature_flags(
        mod.FeatureFlagsUpdate(flags={"qq_group": True, "qq_other_users": False}), auth=None,
    ))
    assert result["restart_required"] == []
    assert result["flags"]["qq_group"]["effective_state"] == "enabled"
    assert result["flags"]["qq_other_users"]["enabled"] is False
    assert yaml.safe_load(path.read_text(encoding="utf-8"))["qq"]["group_enabled"] is True


def test_qq_and_mail_channel_toggles_are_allowlisted(tmp_path, monkeypatch):
    """Brief 93 §4：auth-tokens 页「通道开关」区复用本白名单读写 qq.enabled / mail.enabled。"""
    path = tmp_path / "config.yaml"
    path.write_text("qq:\n  enabled: false\nmail:\n  enabled: false\n", encoding="utf-8")
    monkeypatch.setattr(mod, "CONFIG_FILE", path)
    monkeypatch.setattr(mod, "get_config", lambda: yaml.safe_load(path.read_text(encoding="utf-8")))
    from core import config_loader
    monkeypatch.setattr(config_loader, "reload_config", lambda: None)

    result = asyncio.run(
        mod.update_feature_flags(mod.FeatureFlagsUpdate(flags={"qq": True, "mail": True}), auth=None)
    )
    assert result["flags"]["qq"]["enabled"] is True
    assert result["flags"]["mail"]["enabled"] is True
    assert result["flags"]["qq"]["apply_mode"] == "restart_required"
    assert result["flags"]["mail"]["apply_mode"] == "hot_reload"
    assert result["reload_status"] == "restart_required"
    assert result["restart_required"] == ["qq"]
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert cfg["qq"]["enabled"] is True
    assert cfg["mail"]["enabled"] is True


def test_private_exchange_toggle_is_exposed_and_consumed(tmp_path, monkeypatch):
    """Brief 92 §3：面板开关写回 private_exchange.enabled，trigger 下轮读取新值。"""
    path = tmp_path / "config.yaml"
    path.write_text("private_exchange:\n  enabled: true\n", encoding="utf-8")
    monkeypatch.setattr(mod, "CONFIG_FILE", path)

    from core import config_loader
    from core.scheduler.triggers import private_exchange

    read_config = lambda: yaml.safe_load(path.read_text(encoding="utf-8"))
    monkeypatch.setattr(mod, "get_config", read_config)
    monkeypatch.setattr(config_loader, "get_config", read_config)
    monkeypatch.setattr(config_loader, "reload_config", lambda: None)

    result = asyncio.run(
        mod.update_feature_flags(
            mod.FeatureFlagsUpdate(flags={"private_exchange": False}), auth=None
        )
    )

    assert result["flags"]["private_exchange"]["enabled"] is False
    assert result["flags"]["private_exchange"]["label"] == "角色私下往来"
    assert result["flags"]["private_exchange"]["apply_mode"] == "hot_reload"
    assert result["reload_status"] == "reloaded"
    assert private_exchange._cfg()["enabled"] is False


def test_unchanged_qq_flag_does_not_claim_restart_is_needed(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    path.write_text("qq:\n  enabled: true\n", encoding="utf-8")
    monkeypatch.setattr(mod, "CONFIG_FILE", path)
    monkeypatch.setattr(mod, "get_config", lambda: yaml.safe_load(path.read_text(encoding="utf-8")))
    from core import config_loader
    monkeypatch.setattr(config_loader, "reload_config", lambda: None)

    result = asyncio.run(
        mod.update_feature_flags(mod.FeatureFlagsUpdate(flags={"qq": True}), auth=None)
    )

    assert result["reload_status"] == "reloaded"
    assert result["restart_required"] == []


def test_admin_ui_consumes_feature_flag_restart_contract():
    from pathlib import Path

    source = (Path(__file__).parent.parent / "admin" / "static" / "js" / "settings.js").read_text(
        encoding="utf-8"
    )
    assert "item.restart_required" in source
    assert "result.reload_status === 'restart_required'" in source


def test_memory_event_flags_report_desired_and_effective_state(monkeypatch):
    monkeypatch.setattr(mod, "get_config", lambda: {
        "event_edge_proposer": {"enabled": False},
        "event_shadow_recall": {"enabled": False, "uids": ["scoped-owner"]},
    })

    result = asyncio.run(mod.get_feature_flags(auth=None))["flags"]
    assert result["event_edge_proposer"]["desired_enabled"] is False
    assert result["event_edge_proposer"]["effective_state"] == "disabled"
    assert result["event_shadow_recall"]["desired_enabled"] is False
    assert result["event_shadow_recall"]["effective_state"] == "allowlist-active"
    assert result["event_shadow_recall"]["apply_mode"] == "hot_reload"


def test_audio_music_flags_are_exposed_default_off_and_consumed(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    path.write_text("audio_music:\n  speech_analysis: false\n", encoding="utf-8")
    monkeypatch.setattr(mod, "CONFIG_FILE", path)
    read_config = lambda: yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    monkeypatch.setattr(mod, "get_config", read_config)
    from core import audio_perception, config_loader
    from core.audio_perception import speech_analysis_enabled
    from core.listening_store import music_analysis_enabled
    from core.music_playback_stimulus import music_autonomy_enabled
    from core.player_adapter import music_control_enabled
    monkeypatch.setattr(config_loader, "get_config", read_config)
    monkeypatch.setattr(audio_perception, "get_config", read_config)
    monkeypatch.setattr(config_loader, "reload_config", lambda: None)

    flags = asyncio.run(mod.get_feature_flags(auth=None))["flags"]
    for name in ("speech_analysis", "music_analysis", "music_control", "music_autonomy"):
        assert flags[name]["enabled"] is False
        assert flags[name]["desired_enabled"] is False
        assert flags[name]["effective_state"] == "disabled"
        assert flags[name]["apply_mode"] == "hot_reload"
    assert "STT" in flags["speech_analysis"]["description"]

    result = asyncio.run(mod.update_feature_flags(
        mod.FeatureFlagsUpdate(flags={
            "speech_analysis": True,
            "music_analysis": True,
            "music_control": True,
            "music_autonomy": True,
        }),
        auth=None,
    ))
    written = yaml.safe_load(path.read_text(encoding="utf-8"))["audio_music"]
    assert written == {
        "speech_analysis": True,
        "music_analysis": True,
        "music_control": True,
        "music_autonomy": True,
    }
    assert speech_analysis_enabled() is True
    assert music_analysis_enabled() is True
    assert music_control_enabled() is True
    assert music_autonomy_enabled() is True
    assert result["reload_status"] == "reloaded"
    assert result["flags"]["speech_analysis"]["desired_enabled"] is True
    assert result["flags"]["music_control"]["effective_state"] == "enabled"
    assert result["flags"]["music_autonomy"]["effective_state"] == "enabled"
    assert result["flags"]["speech_analysis"]["effective_state"] in {
        "stt-not-effective", "missing-dependency", "enabled",
    }


def test_state_authority_settings_roundtrip_and_validation(tmp_path, monkeypatch):
    import pytest
    from fastapi import HTTPException

    path = tmp_path / "config.yaml"
    path.write_text("memory_dossiers:\n  other: keep\n", encoding="utf-8")
    monkeypatch.setattr(mod, "CONFIG_FILE", path)
    monkeypatch.setattr(mod, "get_config", lambda: yaml.safe_load(path.read_text(encoding="utf-8")) or {})
    from core import config_loader
    monkeypatch.setattr(config_loader, "reload_config", lambda: None)

    defaults = asyncio.run(mod.get_state_authority_settings(auth=None))
    assert defaults["state_composer_shadow"]["effective_state"] == "disabled"
    assert defaults["memory_dossiers"] == {"suppression": "global", "prompt_injection": True}
    assert defaults["hidden_state"] == {"confidence_gating": False, "min_confidence": 0.3}
    assert defaults["recall"]["semantic_min_similarity"] == 0.0

    result = asyncio.run(mod.update_state_authority_settings(
        mod.StateAuthorityUpdate(
            shadow_uids=[" u1 ", "u1", ""], dossier_suppression="overlap",
            dossier_prompt_injection=False, hidden_confidence_gating=True,
            hidden_min_confidence=0.5, recall_semantic_min_similarity=0.2,
        ), auth=None))
    assert result["state_composer_shadow"]["uids"] == ["u1"]
    assert result["state_composer_shadow"]["effective_state"] == "allowlist-active"
    assert result["memory_dossiers"] == {"suppression": "overlap", "prompt_injection": False}
    assert result["hidden_state"] == {"confidence_gating": True, "min_confidence": 0.5}
    assert result["recall"]["semantic_min_similarity"] == 0.2
    assert yaml.safe_load(path.read_text(encoding="utf-8"))["memory_dossiers"]["other"] == "keep"

    for bad in ({"dossier_suppression": "x"}, {"hidden_min_confidence": 1.5},
                {"recall_semantic_min_similarity": -0.1}):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(mod.update_state_authority_settings(mod.StateAuthorityUpdate(**bad), auth=None))
        assert exc.value.status_code == 422
