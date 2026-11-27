"""
tests/test_tool_dispatcher_char_name.py

Contract tests for the P0 char-name-source fix in core/tool_dispatcher.py.

Rules verified:
- No module-level _CHAR cache: _TOOL_REGISTRY descriptions contain "{char}" literal
  not a baked-in character name.
- get_tools_schema()/get_probe_prompt() substitute the explicitly supplied card.
- Changing active runtime does not replace the scoped description owner.
- _get_episodic_wrapper uses get_active_char_name(), not a cached value.
"""

import importlib

import pytest
from unittest.mock import MagicMock, patch, AsyncMock


@pytest.fixture(autouse=True)
def _ensure_tool_registry_populated():
    """Some tests in the suite blank _TOOL_REGISTRY without restoring.
    Reload the module before each test here so registry is always populated.
    """
    import core.tool_dispatcher as td
    if not td._TOOL_REGISTRY:
        importlib.reload(td)


def _make_pipeline(char_name: str):
    char = MagicMock()
    char.name = char_name
    pl = MagicMock()
    pl.character = char
    return pl


class TestToolRegistryDescriptions:
    def test_descriptions_contain_char_placeholder_not_baked_name(self):
        """Registry must store {char} literal, not a resolved character name."""
        from core.tool_dispatcher import _TOOL_REGISTRY
        # desktop_minimize/desktop_open_url/desktop_play_pause/desktop_notify 不在这里：
        # 2026-07-22 的 schema 澄清把这 4 个桌面控制工具的触发条件从"{char}可自主判断触发"
        # 收紧为"仅在用户明确要求时调用"，不再需要按角色区分描述。
        char_dependent = [
            "read_diary", "read_watch", "search_diary",
            "get_profile", "get_episodic",
            "exit_yandere", "water_garden",
        ]
        for tool_name in char_dependent:
            desc = _TOOL_REGISTRY[tool_name]["description"]
            assert "{char}" in desc, (
                f"_TOOL_REGISTRY[{tool_name!r}]['description'] must contain "
                f"literal '{{char}}' placeholder, got: {desc!r}"
            )
            # No private names baked in
            assert "叶瑄" not in desc, f"{tool_name}: 叶瑄 baked into description"
            assert "红茶" not in desc, f"{tool_name}: 红茶 baked into description"

    def test_get_tools_schema_substitutes_char_name(self):
        from core.tool_dispatcher import get_tools_schema
        from tests.fixtures.public_assets import TEST_CHAR_ID
        with patch("core.character_loader.load", return_value=_make_pipeline("Fixture A").character):
            schemas = get_tools_schema(char_id=TEST_CHAR_ID)
        for schema in schemas:
            desc = schema["function"]["description"]
            assert "{char}" not in desc, f"Unresolved {{char}} in schema for {schema['function']['name']!r}"
            assert "Fixture A" in desc or "{char}" not in _get_raw_desc(schema["function"]["name"])

    def test_get_tools_schema_hotswap(self):
        from core.tool_dispatcher import get_tools_schema
        from tests.fixtures.public_assets import TEST_CHAR_ID, TEST_PEER_CHAR_ID
        cards = {TEST_CHAR_ID: _make_pipeline("Fixture A").character, TEST_PEER_CHAR_ID: _make_pipeline("Fixture B").character}
        with patch("core.character_loader.load", side_effect=cards.__getitem__):
            schemas_a = {s["function"]["name"]: s["function"]["description"] for s in get_tools_schema(char_id=TEST_CHAR_ID)}
            schemas_b = {s["function"]["name"]: s["function"]["description"] for s in get_tools_schema(char_id=TEST_PEER_CHAR_ID)}

        # A char-dependent tool must differ between characters
        for tool_name in ("read_watch", "get_profile"):
            if tool_name in schemas_a and tool_name in schemas_b:
                assert "Fixture A" in schemas_a[tool_name]
                assert "Fixture B" in schemas_b[tool_name]
                assert "Fixture A" not in schemas_b[tool_name]

    def test_get_probe_prompt_substitutes_char_name(self):
        from core.tool_dispatcher import get_probe_prompt
        from tests.fixtures.public_assets import TEST_CHAR_ID
        with patch("core.character_loader.load", return_value=_make_pipeline("Fixture A").character):
            prompt = get_probe_prompt("Fixture location", char_id=TEST_CHAR_ID)
        assert "{char}" not in prompt
        # At least one tool description with {char} must have been resolved
        assert "Fixture A" in prompt


def _get_raw_desc(tool_name: str) -> str:
    from core.tool_dispatcher import _TOOL_REGISTRY
    return _TOOL_REGISTRY.get(tool_name, {}).get("description", "")


class TestExitYandereWrapper:
    @pytest.mark.asyncio
    async def test_uses_active_char_name(self):
        from core.tools.memory_definitions import _exit_yandere_wrapper
        pl = _make_pipeline("红茶")
        with patch("core.pipeline_registry.get", return_value=pl):
            with patch("core.config_loader.get_config", return_value={"emerald_desktop": {"path": "/tmp/x"}}):
                with patch("pathlib.Path.mkdir"), patch("pathlib.Path.write_text"):
                    result = await _exit_yandere_wrapper()
        assert "红茶" in result
        assert "叶瑄" not in result
