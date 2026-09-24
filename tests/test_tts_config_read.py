"""TTS control plane keeps existing settings readable for legacy role IDs."""

import pytest

from admin.routers import settings_misc


def test_invalid_character_id_only_blocks_authored_resource_listing():
    options = settings_misc._tts_resource_options("角色甲")
    assert options == {
        "reference_audio": [], "gpt_model": [], "sovits_model": [],
        "blocking_reason": "character_id_not_supported_for_assets",
    }


def test_valid_character_id_still_lists_authored_resources(monkeypatch):
    from core import userdata_assets

    calls = []
    monkeypatch.setattr(userdata_assets, "list_assets", lambda *, category, char_id: calls.append((category, char_id)) or [])
    options = settings_misc._tts_resource_options("role_1")
    assert options == {"reference_audio": [], "gpt_model": [], "sovits_model": []}
    assert calls == [(kind, "role_1") for kind in ("reference_audio", "gpt_model", "sovits_model")]


@pytest.mark.asyncio
async def test_tts_config_read_succeeds_with_legacy_default_character(monkeypatch):
    monkeypatch.setattr(settings_misc, "DEFAULT_CHAR_ID", "角色甲")
    monkeypatch.setattr(settings_misc, "get_config", lambda: {
        "tts": {"enabled": True, "provider": "gsv", "api_url": "http://127.0.0.1:9872",
                "ref_audio": "existing-reference.wav"},
    })
    result = await settings_misc.get_tts_config(char_id=None, auth=None)
    assert result["enabled"] is True
    assert result["ref_audio"] == "existing-reference.wav"
    assert result["resource_options"]["blocking_reason"] == "character_id_not_supported_for_assets"
