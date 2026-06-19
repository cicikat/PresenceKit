"""Whitelisted toy-file sandbox contracts."""

import json
import time

import pytest

from core import tool_dispatcher
from core.tools import toybox

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


def _write_danger_mode(sandbox):
    path = sandbox.meta_mode()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"mode": "danger", "expires_at": time.time() + 60}),
        encoding="utf-8",
    )


def _install_toy_tool_specs(monkeypatch):
    for name, spec in _TOY_TOOL_SPECS.items():
        monkeypatch.setitem(tool_dispatcher._TOOL_REGISTRY, name, spec)


def test_toybox_path_uses_data_paths(sandbox, tmp_path):
    assert sandbox.very_formal_project_dir() == tmp_path / "very_formal_project"


def test_toybox_write_append_and_read(sandbox):
    assert toybox.write_toy_file("diary", "第一行") == "玩具文件写好了。"
    assert toybox.write_toy_file("diary", "\n第二行", mode="append") == "玩具文件写好了。"

    target = sandbox.very_formal_project_dir() / "思考笔记.txt"
    assert target.read_text(encoding="utf-8") == "第一行\n第二行"
    assert toybox.read_toy_file("diary") == "第一行\n第二行"


@pytest.mark.parametrize("file_key", ["../escape", "unknown", "", None])
def test_toybox_rejects_invalid_file_key_without_writing(sandbox, file_key):
    with pytest.raises(ValueError, match="未知的玩具文件"):
        toybox.write_toy_file(file_key, "nope")
    assert not sandbox.very_formal_project_dir().exists()


def test_toybox_rejects_traversal_even_if_whitelist_is_tampered(sandbox, monkeypatch):
    monkeypatch.setitem(toybox._TOYBOX_FILES, "diary", "../escape.txt")

    with pytest.raises(ValueError, match="沙盒边界"):
        toybox.write_toy_file("diary", "nope")
    assert not (sandbox.very_formal_project_dir().parent / "escape.txt").exists()


def test_toybox_rejects_file_symlink_escape(sandbox, tmp_path):
    root = sandbox.very_formal_project_dir()
    root.mkdir(parents=True)
    outside = tmp_path / "outside.txt"
    try:
        (root / "思考笔记.txt").symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlink unavailable: {exc}")

    with pytest.raises(ValueError, match="沙盒边界"):
        toybox.write_toy_file("diary", "nope")
    assert not outside.exists()


def test_toybox_rejects_oversized_content(sandbox):
    with pytest.raises(ValueError, match="4000"):
        toybox.write_toy_file("doodle", "x" * 4001)
    assert not sandbox.very_formal_project_dir().exists()


@pytest.mark.asyncio
async def test_toybox_tools_follow_safe_and_danger_mode_gate(sandbox, monkeypatch):
    _install_toy_tool_specs(monkeypatch)
    monkeypatch.setattr(tool_dispatcher, "_is_tool_enabled", lambda _: True)
    args = {"file_key": "wishlist", "content": "一起去看海"}

    result, confirm = await tool_dispatcher.execute(
        "write_toy_file", args, "u1", "u1", False, _Session(), origin="user_live"
    )
    assert "安全模式" in result
    assert confirm is None
    assert not sandbox.very_formal_project_dir().exists()

    _write_danger_mode(sandbox)
    result, confirm = await tool_dispatcher.execute(
        "write_toy_file", args, "u1", "u1", False, _Session(), origin="user_live"
    )
    assert result == "工具已执行：write_toy_file，结果：玩具文件写好了。"
    assert confirm is None

    result, confirm = await tool_dispatcher.execute(
        "read_toy_file",
        {"file_key": "wishlist"},
        "u1",
        "u1",
        False,
        _Session(),
        origin="user_live",
    )
    assert result == "工具已执行：read_toy_file，结果：一起去看海"
    assert confirm is None


def test_toybox_registry_contract(monkeypatch):
    _install_toy_tool_specs(monkeypatch)
    for name in ("read_toy_file", "write_toy_file"):
        spec = tool_dispatcher._TOOL_REGISTRY[name]
        assert spec["category"] == "desktop"
        assert spec["dangerous"] is False
        assert spec["examples"]
        assert spec["keywords"]
        assert spec["parameters"]["properties"]["file_key"]["enum"] == [
            "diary",
            "wishlist",
            "doodle",
        ]
    assert tool_dispatcher.is_side_effect_tool("write_toy_file")
