"""TTS control plane keeps existing settings readable for legacy role IDs."""

import pytest

from admin.routers import settings_misc


def test_unsafe_character_id_only_blocks_authored_resource_listing():
    options = settings_misc._tts_resource_options("../角色甲")
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
async def test_tts_config_read_succeeds_with_unsafe_default_character(monkeypatch):
    monkeypatch.setattr(settings_misc, "DEFAULT_CHAR_ID", "../角色甲")
    monkeypatch.setattr(settings_misc, "_current_tts_character_id", lambda: "../角色甲")
    monkeypatch.setattr(settings_misc, "get_config", lambda: {
        "tts": {"enabled": True, "provider": "gsv", "api_url": "http://127.0.0.1:9872",
                "ref_audio": "existing-reference.wav"},
    })
    result = await settings_misc.get_tts_config(char_id=None, auth=None)
    assert result["enabled"] is True
    assert result["ref_audio"] == "existing-reference.wav"
    assert result["resource_options"]["blocking_reason"] == "character_id_not_supported_for_assets"


@pytest.mark.asyncio
async def test_role_save_creates_separate_route_without_changing_global(monkeypatch):
    from core import asset_registry
    from core.output import voice_adapter
    original = {"tts": {"enabled": True, "ref_audio": "global.wav", "providers": {"gsv": {"api_url": "http://127.0.0.1:9872"}}}}
    written = []
    class Registry:
        def resolve(self, char_id, category):
            assert (char_id, category) == ("role_one", "character")
            return object()
    monkeypatch.setattr(asset_registry, "get_registry", lambda: Registry())
    monkeypatch.setattr(settings_misc, "read_config_file", lambda _: original)
    monkeypatch.setattr(settings_misc, "write_config_file", lambda _, cfg: written.append(cfg))
    monkeypatch.setattr("core.config_loader.reload_config", lambda: None)
    await settings_misc.update_tts_config(settings_misc.TtsConfigUpdate(
        char_id="role_one", ref_audio="中文参考", provider="gsv",
        reference_texts={"中文参考": "这是一段参考文本。", "另一份": "另一个文本。"},
        provider_params={"ref_audio": "中文参考"}), auth=None)
    tts = written[0]["tts"]
    name = voice_adapter.role_tts_generated_name("role_one")
    assert tts["ref_audio"] == "global.wav"
    assert tts["role_routes"]["role_one"] == name
    assert tts["presets"][name]["providers"]["gsv"]["ref_audio"] == "中文参考"
    assert tts["presets"][name]["reference_texts"]["另一份"] == "另一个文本。"


def test_reference_text_matches_selected_audio_and_preserves_legacy_fallback():
    from core.output.voice_adapter import reference_prompt_text

    cfg = {"prompt_text": "旧文本", "reference_texts": {"念诗": "念诗对应文字", "温柔": "温柔对应文字"}}
    assert reference_prompt_text(cfg, "念诗") == "念诗对应文字"
    assert reference_prompt_text(cfg, "userdata/characters/authored/role_one/voice/温柔.mp3") == "温柔对应文字"
    assert reference_prompt_text(cfg, "其他音频") == ""
    assert reference_prompt_text(cfg, "念诗", emotion_text="情绪专用文字") == "情绪专用文字"
    assert reference_prompt_text({"prompt_text": "旧文本"}, "念诗") == "旧文本"


def test_listed_unicode_reference_resolves_only_inside_role_voice_dir(monkeypatch, tmp_path):
    from core import userdata_assets
    voice = tmp_path / "authored" / "role_one" / "voice"
    voice.mkdir(parents=True)
    expected = voice / "中文参考.MP3"
    expected.write_bytes(b"audio")
    class Paths:
        def character_voice_dirs(self, *, char_id):
            assert char_id == "role_one"
            return voice, tmp_path / "missing"
    monkeypatch.setattr(userdata_assets, "_paths", lambda: Paths())
    rows = userdata_assets.list_assets(category="reference_audio", char_id="role_one")
    assert any(row["logical_id"] == "中文参考" and row["valid"] for row in rows)
    assert userdata_assets.resolve_asset_path(category="reference_audio", logical_id="中文参考", char_id="role_one") == expected
    assert userdata_assets.resolve_asset_path(category="reference_audio", logical_id="../secret", char_id="role_one") is None


def test_unicode_character_id_can_index_its_own_voice_directory(monkeypatch, tmp_path):
    from core import userdata_assets
    voice = tmp_path / "角色甲" / "voice"
    voice.mkdir(parents=True)
    (voice / "参考.wav").write_bytes(b"audio")
    class Paths:
        def character_voice_dirs(self, *, char_id):
            assert char_id == "角色甲"
            return voice, tmp_path / "missing"
        def user_authored_character_dir(self, *, char_id):
            assert char_id == "角色甲"
            return voice.parent
        def legacy_authored_character_dir(self, *, char_id):
            return tmp_path / "missing"
    monkeypatch.setattr(userdata_assets, "_paths", lambda: Paths())
    options = settings_misc._tts_resource_options("角色甲")
    assert [row["logical_id"] for row in options["reference_audio"]] == ["参考"]
    assert options["gpt_model"] == []


def test_role_model_weights_are_listed_by_suffix_and_resolve_within_role(monkeypatch, tmp_path):
    from core import userdata_assets

    role_root = tmp_path / "authored" / "role_one"
    role_root.mkdir(parents=True)
    gpt = role_root / "example-e15.ckpt"
    gpt.write_bytes(b"gpt")
    sovits = role_root / "weights" / "example_e4_s84.pth"
    sovits.parent.mkdir()
    sovits.write_bytes(b"sovits")
    (tmp_path / "authored" / "role_two").mkdir()

    class Paths:
        def user_authored_character_dir(self, *, char_id):
            return tmp_path / "authored" / char_id
        def legacy_authored_character_dir(self, *, char_id):
            return tmp_path / "legacy" / char_id
        def character_voice_dirs(self, *, char_id):
            return self.user_authored_character_dir(char_id=char_id) / "voice", self.legacy_authored_character_dir(char_id=char_id) / "voice"

    monkeypatch.setattr(userdata_assets, "_paths", lambda: Paths())
    options = settings_misc._tts_resource_options("role_one")
    assert [row["name"] for row in options["gpt_model"]] == [gpt.name]
    assert [row["name"] for row in options["sovits_model"]] == [sovits.name]
    assert userdata_assets.resolve_asset_path(category="gpt_model", logical_id=gpt.stem, char_id="role_one") == gpt
    assert userdata_assets.resolve_asset_path(category="sovits_model", logical_id=sovits.stem, char_id="role_one") == sovits
    assert userdata_assets.resolve_asset_path(category="gpt_model", logical_id=gpt.stem, char_id="role_two") is None
    assert userdata_assets.resolve_asset_path(category="sovits_model", logical_id="../example_e4_s84", char_id="role_one") is None
