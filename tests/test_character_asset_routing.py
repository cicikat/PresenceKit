from tests.fixtures.public_assets import TEST_CHAR_ID
"""
tests/test_character_asset_routing.py

角色资产路由（presence_ext 扩展）：presence_ext.tts_preset 叠加命名 TTS 预设、
presence_ext.sticker_pack 提供角色专属表情包池（缺图回落通用池）。与既有
presence_ext.model_routing 同构，未声明时零行为变化。
"""

import pytest
from unittest.mock import MagicMock, patch


class TestTtsPresetRouting:
    def test_admin_role_route_overrides_card_binding(self):
        from core.output import voice_adapter

        base = {
            "ref_audio": "global.wav",
            "role_routes": {"role_one": "role_voice"},
            "presets": {"role_voice": {"ref_audio": "role.wav"}, "card_voice": {"ref_audio": "card.wav"}},
        }
        char = MagicMock()
        char.presence_ext = {"tts_preset": "card_voice"}
        with patch("core.output.voice_adapter.get_config", return_value={"tts": base}), \
             patch("core.character_loader.load", return_value=char):
            assert voice_adapter.resolve_tts_config("role_one")["ref_audio"] == "role.wav"
            assert voice_adapter.resolve_tts_config("role_two")["ref_audio"] == "card.wav"

    def test_no_char_id_returns_base_config_unchanged(self):
        from core.output import voice_adapter

        base = {"provider": "gsv", "ref_audio": "x.wav"}
        with patch("core.output.voice_adapter.get_config", return_value={"tts": base}):
            resolved = voice_adapter.resolve_tts_config(None)
        assert resolved == base

    def test_char_without_preset_declaration_falls_back(self):
        from core.output import voice_adapter

        base = {"provider": "gsv", "ref_audio": "x.wav"}
        char = MagicMock()
        char.presence_ext = {}
        with patch("core.output.voice_adapter.get_config", return_value={"tts": base}), \
             patch("core.character_loader.load", return_value=char):
            resolved = voice_adapter.resolve_tts_config("some_char")
        assert resolved == base

    def test_preset_overlays_on_top_of_base(self):
        from core.output import voice_adapter

        base = {
            "provider": "gsv", "ref_audio": "base.wav", "speed": 1.0,
            "presets": {"cheerful": {"ref_audio": "cheerful.wav", "speed": 1.2}},
        }
        char = MagicMock()
        char.presence_ext = {"tts_preset": "cheerful"}
        with patch("core.output.voice_adapter.get_config", return_value={"tts": base}), \
             patch("core.character_loader.load", return_value=char):
            resolved = voice_adapter.resolve_tts_config("some_char")
        assert resolved["ref_audio"] == "cheerful.wav"
        assert resolved["speed"] == 1.2
        assert resolved["provider"] == "gsv"  # untouched field survives the overlay

    def test_unknown_preset_name_falls_back_with_warning(self, caplog):
        from core.output import voice_adapter

        base = {"provider": "gsv", "ref_audio": "base.wav", "presets": {}}
        char = MagicMock()
        char.presence_ext = {"tts_preset": "does_not_exist"}
        with patch("core.output.voice_adapter.get_config", return_value={"tts": base}), \
             patch("core.character_loader.load", return_value=char):
            with caplog.at_level("WARNING", logger="core.output.voice_adapter"):
                resolved = voice_adapter.resolve_tts_config("some_char")
        assert resolved == base
        assert "未在 tts.presets 中找到" in caplog.text

    def test_character_loader_failure_is_fail_soft(self):
        from core.output import voice_adapter

        base = {"provider": "gsv"}
        with patch("core.output.voice_adapter.get_config", return_value={"tts": base}), \
             patch("core.character_loader.load", side_effect=RuntimeError("boom")):
            resolved = voice_adapter.resolve_tts_config("broken_char")
        assert resolved == base

    @pytest.mark.asyncio
    async def test_synthesize_threads_char_id_into_resolution(self):
        from core.output import voice_adapter

        captured = {}

        def _fake_resolve(char_id=None):
            captured["char_id"] = char_id
            return {"provider": "gsv", "ref_audio": "x.wav"}

        with patch("core.output.voice_adapter.resolve_tts_config", side_effect=_fake_resolve), \
             patch("core.output.voice_adapter.get_provider_config", return_value=("gsv", {})), \
             patch.dict("core.output.voice_adapter._PROVIDERS", {"gsv": MagicMock(synthesize=_async_return(b"wav"))}), \
             patch("core.api_call_log.append", lambda **kw: None):
            await voice_adapter.synthesize("hi", "happy", char_id=TEST_CHAR_ID)

        assert captured["char_id"] == TEST_CHAR_ID


def _async_return(value):
    async def _inner(*a, **kw):
        return value
    return _inner


class TestStickerPackRouting:
    def test_pack_image_preferred_over_shared_pool(self, tmp_path, monkeypatch):
        from core.output import sticker

        pack_dir = tmp_path / "packs" / "cute" / "开心"
        pack_dir.mkdir(parents=True)
        (pack_dir / "a.png").write_bytes(b"x")
        shared_dir = tmp_path / "shared" / "开心"
        shared_dir.mkdir(parents=True)
        (shared_dir / "b.png").write_bytes(b"y")

        paths = MagicMock()
        paths.sticker_pack_dir.return_value = tmp_path / "packs" / "cute"
        paths.stickers_dir.return_value = tmp_path / "shared"

        char = MagicMock()
        char.presence_ext = {"sticker_pack": "cute"}

        with patch("core.sandbox.get_paths", return_value=paths), \
             patch("core.character_loader.load", return_value=char):
            picked = sticker._pick_sticker("开心", char_id=TEST_CHAR_ID)

        assert picked is not None
        assert "packs" in picked and "cute" in picked

    def test_pack_missing_emotion_falls_back_to_shared_pool(self, tmp_path):
        from core.output import sticker

        shared_dir = tmp_path / "shared" / "开心"
        shared_dir.mkdir(parents=True)
        (shared_dir / "b.png").write_bytes(b"y")

        paths = MagicMock()
        paths.sticker_pack_dir.return_value = tmp_path / "packs" / "cute"  # doesn't exist
        paths.stickers_dir.return_value = tmp_path / "shared"

        char = MagicMock()
        char.presence_ext = {"sticker_pack": "cute"}

        with patch("core.sandbox.get_paths", return_value=paths), \
             patch("core.character_loader.load", return_value=char):
            picked = sticker._pick_sticker("开心", char_id=TEST_CHAR_ID)

        assert picked is not None
        assert "shared" in picked

    def test_no_char_id_uses_shared_pool_only(self, tmp_path):
        from core.output import sticker

        shared_dir = tmp_path / "shared" / "开心"
        shared_dir.mkdir(parents=True)
        (shared_dir / "b.png").write_bytes(b"y")

        paths = MagicMock()
        paths.stickers_dir.return_value = tmp_path / "shared"

        with patch("core.sandbox.get_paths", return_value=paths):
            picked = sticker._pick_sticker("开心", char_id=None)

        assert picked is not None
        paths.sticker_pack_dir.assert_not_called()

    def test_char_without_pack_declaration_uses_shared_pool(self, tmp_path):
        from core.output import sticker

        shared_dir = tmp_path / "shared" / "开心"
        shared_dir.mkdir(parents=True)
        (shared_dir / "b.png").write_bytes(b"y")

        paths = MagicMock()
        paths.stickers_dir.return_value = tmp_path / "shared"

        char = MagicMock()
        char.presence_ext = {}

        with patch("core.sandbox.get_paths", return_value=paths), \
             patch("core.character_loader.load", return_value=char):
            picked = sticker._pick_sticker("开心", char_id=TEST_CHAR_ID)

        assert picked is not None
        paths.sticker_pack_dir.assert_not_called()
