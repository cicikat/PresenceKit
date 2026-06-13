"""
tests/test_char_name_provider.py

Contract tests for core/character_name_provider.get_active_char_name().

Rules verified:
- Explicit char_id resolves through the character asset registry.
- Returns character card .name when pipeline is registered with a character.
- Returns controlled placeholder "(角色未加载)" when pipeline is not registered.
- Never returns a hardcoded private character name.
- Hot-swap: changing pipeline.character updates the returned name.
"""

import pytest
from unittest.mock import MagicMock, patch


def _make_pipeline(char_name: str):
    char = MagicMock()
    char.name = char_name
    pl = MagicMock()
    pl.character = char
    return pl


class TestGetActiveCharName:
    def test_explicit_char_id_resolves_card_without_pipeline(self):
        from core.character_name_provider import get_char_name

        with patch("core.character_loader.load") as mock_load:
            mock_load.return_value.name = "红茶"
            assert get_char_name("hongcha") == "红茶"
            mock_load.assert_called_once_with("hongcha")

    def test_explicit_unknown_char_id_does_not_fall_back_to_active(self):
        from core.character_name_provider import get_char_name

        with patch("core.character_loader.load", side_effect=ValueError("unknown character id")):
            with pytest.raises(ValueError, match="unknown character id"):
                get_char_name("ghost")

    def test_returns_card_name_when_pipeline_registered(self):
        from core.character_name_provider import get_active_char_name
        pl = _make_pipeline("叶瑄")
        with patch("core.pipeline_registry.get", return_value=pl):
            assert get_active_char_name() == "叶瑄"

    def test_returns_placeholder_when_pipeline_none(self):
        from core.character_name_provider import get_active_char_name
        with patch("core.pipeline_registry.get", return_value=None):
            result = get_active_char_name()
        assert result == "(角色未加载)"
        # must not be any known private character name
        assert "叶瑄" not in result
        assert "yexuan" not in result
        assert "hongcha" not in result

    def test_returns_placeholder_when_character_is_none(self):
        from core.character_name_provider import get_active_char_name
        pl = MagicMock()
        pl.character = None
        with patch("core.pipeline_registry.get", return_value=pl):
            result = get_active_char_name()
        assert result == "(角色未加载)"

    def test_hotswap_reflects_new_card_name(self):
        from core.character_name_provider import get_active_char_name
        pl_a = _make_pipeline("叶瑄")
        pl_b = _make_pipeline("红茶")
        with patch("core.pipeline_registry.get", return_value=pl_a):
            assert get_active_char_name() == "叶瑄"
        with patch("core.pipeline_registry.get", return_value=pl_b):
            assert get_active_char_name() == "红茶"

    def test_does_not_read_config(self):
        """get_active_char_name must never fall back to config.character.name."""
        from core.character_name_provider import get_active_char_name
        with patch("core.pipeline_registry.get", return_value=None):
            with patch("core.config_loader.get_config") as mock_cfg:
                get_active_char_name()
                mock_cfg.assert_not_called()
