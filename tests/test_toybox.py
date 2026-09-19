"""Thin toy-file mapping onto per-character self files (256 D)."""

from __future__ import annotations

import pytest

from core import character_self as self_mod
from core import tool_dispatcher
from core.autonomy.policy import tool_eligibility
from core.tools import toybox
from tests.fixtures.public_assets import TEST_CHAR_ID, TEST_PEER_CHAR_ID


_UID = "u1"
_CHAR = TEST_CHAR_ID
_OTHER = TEST_PEER_CHAR_ID

_TOY_TOOL_SPECS = {
    name: dict(tool_dispatcher._TOOL_REGISTRY[name])
    for name in ("read_toy_file", "write_toy_file")
}


class _Session:
    WAITING_CONFIRM = "waiting_confirm"
    IDLE = "idle"
    status = IDLE

    def set_waiting_confirm(self, tool_name, tool_args):
        self.status = self.WAITING_CONFIRM


def _install_toy_tool_specs(monkeypatch):
    for name, spec in _TOY_TOOL_SPECS.items():
        monkeypatch.setitem(tool_dispatcher._TOOL_REGISTRY, name, spec)


def test_toybox_path_uses_data_paths(sandbox, tmp_path):
    assert sandbox.very_formal_project_dir() == tmp_path / "very_formal_project"


def test_toybox_write_append_and_read_goes_to_self(sandbox):
    assert toybox.write_toy_file("diary", "第一行", user_id=_UID, char_id=_CHAR) == "玩具文件写好了。"
    assert toybox.write_toy_file("diary", "\n第二行", mode="append", user_id=_UID, char_id=_CHAR) == "玩具文件写好了。"
    assert toybox.read_toy_file("diary", user_id=_UID, char_id=_CHAR) == "第一行\n第二行"
    live = sandbox.character_self_root(_UID, char_id=_CHAR) / "notes" / "思考笔记.txt"
    assert live.read_text(encoding="utf-8") == "第一行\n第二行"
    assert not (sandbox.very_formal_project_dir() / "思考笔记.txt").exists()
    other = toybox.read_toy_file("diary", user_id=_UID, char_id=_OTHER)
    assert other == "这个玩具文件还是空的。"


def test_toybox_requires_scope(sandbox):
    with pytest.raises(ValueError, match="用户与角色范围"):
        toybox.write_toy_file("diary", "第一行")
    assert not sandbox.very_formal_project_dir().exists()
    assert not sandbox.character_self_root(_UID, char_id=_CHAR).exists()


@pytest.mark.parametrize("file_key", ["../escape", "unknown", "", None])
def test_toybox_rejects_invalid_file_key_without_writing(sandbox, file_key):
    with pytest.raises(ValueError, match="未知的玩具文件"):
        toybox.write_toy_file(file_key, "nope", user_id=_UID, char_id=_CHAR)
    assert not sandbox.very_formal_project_dir().exists()
    listed = self_mod.list_self(user_id=_UID, char_id=_CHAR)
    assert listed.get("ok") is True
    assert listed.get("entries") == []


def test_toybox_rejects_traversal_even_if_whitelist_is_tampered(sandbox, monkeypatch):
    monkeypatch.setitem(toybox.TOY_KEY_TO_SELF_PATH, "diary", "../escape.txt")
    monkeypatch.setitem(toybox._TOYBOX_FILES, "diary", "../escape.txt")
    result = toybox.write_toy_file("diary", "nope", user_id=_UID, char_id=_CHAR)
    assert "self_escape_denied" in result
    assert not (sandbox.character_self_root(_UID, char_id=_CHAR).parent / "escape.txt").exists()
    assert not (sandbox.very_formal_project_dir().parent / "escape.txt").exists()


def test_toybox_rejects_oversized_content(sandbox):
    with pytest.raises(ValueError, match="4000"):
        toybox.write_toy_file("doodle", "x" * 4001, user_id=_UID, char_id=_CHAR)
    assert not sandbox.very_formal_project_dir().exists()


def test_empty_read_is_normal(sandbox):
    assert toybox.read_toy_file("wishlist", user_id=_UID, char_id=_CHAR) == "这个玩具文件还是空的。"


@pytest.mark.asyncio
async def test_toybox_tools_are_info_and_not_danger_gated(sandbox, monkeypatch):
    _install_toy_tool_specs(monkeypatch)
    monkeypatch.setattr(tool_dispatcher, "_is_tool_enabled", lambda _: True)
    monkeypatch.setattr(tool_dispatcher, "_current_mode", lambda: "safe")
    args = {"file_key": "wishlist", "content": "一起去看海"}

    result = await tool_dispatcher.execute_structured(
        "write_toy_file", args, _UID, _UID, False, _Session(), origin="user_live", char_id=_CHAR,
    )
    assert result.confirmation_request is None
    assert result.result == "工具已执行：write_toy_file，结果：玩具文件写好了。"
    assert not sandbox.very_formal_project_dir().exists()
    live = sandbox.character_self_root(_UID, char_id=_CHAR) / "notes" / "愿望清单.md"
    assert live.read_text(encoding="utf-8") == "一起去看海"

    result = await tool_dispatcher.execute_structured(
        "read_toy_file",
        {"file_key": "wishlist"},
        _UID,
        _UID,
        False,
        _Session(),
        origin="user_live",
        char_id=_CHAR,
    )
    assert result.result == "工具已执行：read_toy_file，结果：一起去看海"
    assert result.confirmation_request is None


def test_toybox_registry_contract(monkeypatch):
    _install_toy_tool_specs(monkeypatch)
    for name in ("read_toy_file", "write_toy_file"):
        spec = tool_dispatcher._TOOL_REGISTRY[name]
        assert spec["category"] == "info"
        assert spec["dangerous"] is False
        assert spec.get("require_confirm") is not True
        assert spec["examples"]
        assert spec["keywords"]
        assert spec["parameters"]["properties"]["file_key"]["enum"] == [
            "diary",
            "wishlist",
            "doodle",
        ]
        eligible, reason = tool_eligibility(
            name, {"enabled": True}, registry=tool_dispatcher._TOOL_REGISTRY, effect=spec["effect"],
        )
        assert eligible is True, (name, reason)
    assert tool_dispatcher.is_side_effect_tool("write_toy_file")
    assert toybox.frozen_legacy_writer_message()
