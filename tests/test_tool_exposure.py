from __future__ import annotations

from types import SimpleNamespace


def _schema(name: str) -> dict:
    return {"type": "function", "function": {"name": name, "parameters": {}}}


def test_path_a_is_channel_neutral_and_can_use_explicit_tool_allowlist(monkeypatch):
    from core import tool_exposure

    monkeypatch.setattr(
        "core.config_loader.get_config",
        lambda: {
            "tool_exposure": {
                "path_a": {
                    "categories": ["info", "fs"],
                    "tools": ["get_time", "fs_list"],
                    "exclude_tools": ["fs_list"],
                },
            },
        },
    )

    exposure = tool_exposure.resolve("path_a")
    assert exposure.categories == ("info", "fs", "self", "schedule", "life")
    assert exposure.tools == frozenset({"get_time", "fs_list"})
    assert [item["function"]["name"] for item in tool_exposure.filter_schemas(
        [_schema("get_time"), _schema("fs_list"), _schema("weather")], exposure,
    )] == ["get_time"]


def test_character_can_override_each_path_without_changing_other_channels(monkeypatch):
    from core import tool_exposure

    monkeypatch.setattr("core.config_loader.get_config", lambda: {"tool_loop": {"categories": ["info", "memory"]}})
    monkeypatch.setattr(
        "core.character_loader.load",
        lambda _char_id: SimpleNamespace(presence_ext={
            "tool_categories_path_a": ["fs"],
            "tool_tools_path_a": ["fs_read"],
            "tool_categories_path_c": ["mcp"],
        }),
    )

    path_a = tool_exposure.resolve("path_a", char_id="char")
    path_c = tool_exposure.resolve("path_c", char_id="char")

    assert path_a.categories == ("fs",)
    assert path_a.tools == frozenset({"fs_read"})
    assert path_a.source == "presence_ext.tool_tools_a"
    assert path_c.categories == ("mcp",)
    assert path_c.source == "presence_ext.tool_categories_path_c"


def test_path_c_preserves_legacy_category_override(monkeypatch):
    from core import tool_exposure

    monkeypatch.setattr("core.config_loader.get_config", lambda: {"tool_loop": {"categories": ["info"]}})
    monkeypatch.setattr(
        "core.character_loader.load",
        lambda _char_id: SimpleNamespace(presence_ext={"tool_categories": ["mcp", "fs"]}),
    )

    exposure = tool_exposure.resolve("path_c", char_id="char")
    assert exposure.categories == ("mcp", "fs")
    assert exposure.source == "presence_ext.tool_categories"


def test_legacy_info_whitelist_expands_to_split_categories(monkeypatch):
    from core import tool_exposure

    monkeypatch.setattr("core.config_loader.get_config", lambda: {})
    monkeypatch.setattr(
        "core.character_loader.load",
        lambda _char_id: SimpleNamespace(presence_ext={"tool_categories": ["info", "life"]}),
    )
    exposure = tool_exposure.resolve("path_c", char_id="char")
    assert exposure.categories == ("info", "life", "self", "schedule")

    monkeypatch.setattr(
        "core.character_loader.load",
        lambda _char_id: SimpleNamespace(presence_ext={"tool_categories": ["memory", "fs"]}),
    )
    exposure = tool_exposure.resolve("path_c", char_id="char")
    assert exposure.categories == ("memory", "fs")
    assert not {"self", "schedule", "life"} & set(exposure.categories)


def test_expand_legacy_categories_for_caller_allowlist():
    from core.tool_exposure import expand_legacy_categories

    assert set(expand_legacy_categories(frozenset({"info", "memory"}))) == {
        "info", "memory", "self", "schedule", "life",
    }
