from tests.fixtures.public_assets import TEST_CHAR_ID
"""Read-only fs browsing tool contracts (Brief 31 · fs_list / fs_read)."""

import pytest

from core import tool_dispatcher
from core.tools import fs_browse


def test_fs_read_default_limit_is_10k():
    assert fs_browse._DEFAULT_MAX_READ_CHARS == 10000

_FS_TOOL_SPECS = {
    name: dict(tool_dispatcher._TOOL_REGISTRY[name])
    for name in ("fs_list", "fs_read")
}


def _install_fs_tool_specs(monkeypatch):
    for name, spec in _FS_TOOL_SPECS.items():
        monkeypatch.setitem(tool_dispatcher._TOOL_REGISTRY, name, spec)


class _Session:
    WAITING_CONFIRM = "waiting_confirm"
    IDLE = "idle"
    status = IDLE

    def set_waiting_confirm(self, tool_name, tool_args):
        self.status = self.WAITING_CONFIRM


def _patch_fs_config(monkeypatch, tmp_path, allow_roots=None, **overrides):
    if allow_roots is None:
        allow_root = tmp_path / "allow"
        allow_root.mkdir(parents=True, exist_ok=True)
        allow_roots = [str(allow_root)]
    else:
        allow_root = None
    cfg = {
        "fs_access": {
            "enabled": True,
            "allow_roots": allow_roots,
            "deny_names": [],
            "max_read_chars": 4000,
            "max_list_entries": 100,
        }
    }
    cfg["fs_access"].update(overrides)
    monkeypatch.setattr("core.config_loader.get_config", lambda: cfg)
    # 隔离项目 data/ 沙盒判定，避免测试碰真实仓库 data 目录
    fake_data_dir = (tmp_path / "data").resolve()
    monkeypatch.setattr(fs_browse, "_project_data_dir", lambda: fake_data_dir)
    return allow_root, fake_data_dir


# ── 1. 越界：全拒 ────────────────────────────────────────────────────────────

def test_fs_read_rejects_outside_allow_roots(monkeypatch, tmp_path):
    allow_root, _ = _patch_fs_config(monkeypatch, tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("secret stuff", encoding="utf-8")

    assert "不在允许浏览的范围内" in fs_browse.fs_read(str(outside))


def test_fs_read_rejects_dotdot_traversal(monkeypatch, tmp_path):
    allow_root, _ = _patch_fs_config(monkeypatch, tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("secret stuff", encoding="utf-8")
    traversal_path = str(allow_root / ".." / "outside.txt")

    assert "不在允许浏览的范围内" in fs_browse.fs_read(traversal_path)


def test_fs_read_rejects_symlink_pointing_outside(monkeypatch, tmp_path):
    allow_root, _ = _patch_fs_config(monkeypatch, tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("secret stuff", encoding="utf-8")
    link = allow_root / "link.txt"
    try:
        link.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlink unavailable: {exc}")

    result = fs_browse.fs_read(str(link))
    assert "不在允许浏览的范围内" in result or "软链接" in result


def test_fs_read_rejects_symlink_pointing_inside_allow_root(monkeypatch, tmp_path):
    allow_root, _ = _patch_fs_config(monkeypatch, tmp_path)
    target = allow_root / "real.txt"
    target.write_text("real content", encoding="utf-8")
    link = allow_root / "link.txt"
    try:
        link.symlink_to(target)
    except OSError as exc:
        pytest.skip(f"symlink unavailable: {exc}")

    assert "软链接" in fs_browse.fs_read(str(link))


# ── 2. deny_names：拒绝 + 底线集不可清空 ─────────────────────────────────────

def test_fs_read_rejects_deny_name_segment(monkeypatch, tmp_path):
    allow_root, _ = _patch_fs_config(monkeypatch, tmp_path)
    secret_dir = allow_root / "secrets"
    secret_dir.mkdir()
    secret_file = secret_dir / "data.txt"
    secret_file.write_text("nope", encoding="utf-8")

    assert "被禁止访问的名称" in fs_browse.fs_read(str(secret_file))


def test_fs_deny_baseline_survives_config_wipe_attempt(monkeypatch, tmp_path):
    allow_root, _ = _patch_fs_config(monkeypatch, tmp_path, deny_names=[])
    git_dir = allow_root / ".git"
    git_dir.mkdir()
    git_file = git_dir / "config"
    git_file.write_text("nope", encoding="utf-8")

    assert "被禁止访问的名称" in fs_browse.fs_read(str(git_file))


# ── 3. data/ 隐式拒绝 ─────────────────────────────────────────────────────────

def test_fs_read_rejects_project_data_dir_even_if_allowed(monkeypatch, tmp_path):
    fake_data_dir = (tmp_path / "data").resolve()
    fake_data_dir.mkdir(parents=True)
    target = fake_data_dir / "secret.txt"
    target.write_text("nope", encoding="utf-8")
    _patch_fs_config(monkeypatch, tmp_path, allow_roots=[str(fake_data_dir)])

    assert "项目内部沙盒目录" in fs_browse.fs_read(str(target))


def test_fs_list_hides_project_data_dir_from_listing(monkeypatch, tmp_path):
    allow_root, fake_data_dir = _patch_fs_config(monkeypatch, tmp_path)
    (allow_root / "data").mkdir()
    (allow_root / "data" / "secret.txt").write_text("nope", encoding="utf-8")
    (allow_root / "visible.txt").write_text("hi", encoding="utf-8")
    monkeypatch.setattr(fs_browse, "_project_data_dir", lambda: (allow_root / "data").resolve())

    result = fs_browse.fs_list(str(allow_root))
    assert "visible.txt" in result
    assert "data/" not in result


# ── 4. 截断 ──────────────────────────────────────────────────────────────────

def test_fs_read_truncates_long_file(monkeypatch, tmp_path):
    allow_root, _ = _patch_fs_config(monkeypatch, tmp_path, max_read_chars=5)
    f = allow_root / "long.txt"
    f.write_text("0123456789", encoding="utf-8")

    result = fs_browse.fs_read(str(f))
    assert result.startswith("01234")
    assert "已截断" in result
    assert "共 10 字" in result


def test_fs_list_truncates_long_directory(monkeypatch, tmp_path):
    allow_root, _ = _patch_fs_config(monkeypatch, tmp_path, max_list_entries=2)
    for i in range(5):
        (allow_root / f"file{i}.txt").write_text("x", encoding="utf-8")

    result = fs_browse.fs_list(str(allow_root))
    assert "已达 2 条上限" in result
    assert len([line for line in result.splitlines() if "file" in line]) == 2


def test_fs_read_rejects_oversized_file(monkeypatch, tmp_path):
    allow_root, _ = _patch_fs_config(monkeypatch, tmp_path)
    monkeypatch.setattr(fs_browse, "_MAX_READ_FILE_BYTES", 10)
    f = allow_root / "big.txt"
    f.write_text("x" * 20, encoding="utf-8")

    result = fs_browse.fs_read(str(f))
    assert "5MB" in result


# ── 5. 编码 ──────────────────────────────────────────────────────────────────

def test_fs_read_decodes_gbk_file(monkeypatch, tmp_path):
    allow_root, _ = _patch_fs_config(monkeypatch, tmp_path)
    f = allow_root / "gbk.txt"
    f.write_bytes("你好世界".encode("gbk"))

    assert fs_browse.fs_read(str(f)) == "你好世界"


def test_fs_read_binary_extension_returns_type_hint_without_raising(monkeypatch, tmp_path):
    allow_root, _ = _patch_fs_config(monkeypatch, tmp_path)
    f = allow_root / "image.bin"
    f.write_bytes(bytes(range(256)))

    result = fs_browse.fs_read(str(f))
    assert "二进制" in result or "不支持的文件类型" in result


def test_fs_read_undecodable_text_extension_returns_type_hint(monkeypatch, tmp_path):
    allow_root, _ = _patch_fs_config(monkeypatch, tmp_path)
    f = allow_root / "garbled.txt"
    f.write_bytes(b"\xff\xfe\x00\x01\x02\x03")

    result = fs_browse.fs_read(str(f))
    assert "二进制" in result or "不支持的文件类型" in result


# ── 6. 开关关 ─────────────────────────────────────────────────────────────────

def test_fs_tools_return_disabled_message_when_off(monkeypatch, tmp_path):
    allow_root, _ = _patch_fs_config(monkeypatch, tmp_path, enabled=False)
    f = allow_root / "file.txt"
    f.write_text("hi", encoding="utf-8")

    assert fs_browse.fs_list(str(allow_root)) == "文件浏览未开启"
    assert fs_browse.fs_read(str(f)) == "文件浏览未开启"


def test_fs_list_without_path_returns_allow_roots(monkeypatch, tmp_path):
    allow_root, _ = _patch_fs_config(monkeypatch, tmp_path)

    result = fs_browse.fs_list()
    assert str(allow_root) in result


# ── 7. schema：per-char tool_categories 暴露面 ────────────────────────────────

def test_fs_tools_only_visible_with_fs_category(monkeypatch):
    _install_fs_tool_specs(monkeypatch)
    monkeypatch.setattr(tool_dispatcher, "_is_tool_enabled", lambda _: True)

    with_fs = {s["function"]["name"] for s in tool_dispatcher.get_tools_schema(categories=["info", "fs"])}
    without_fs = {s["function"]["name"] for s in tool_dispatcher.get_tools_schema(categories=["info", "desktop", "memory"])}

    assert {"fs_list", "fs_read"} <= with_fs
    assert not ({"fs_list", "fs_read"} & without_fs)


def test_probe_prompt_never_covers_fs_category(monkeypatch):
    _install_fs_tool_specs(monkeypatch)
    prompt = tool_dispatcher.get_probe_prompt("测试位置")
    assert "fs_list" not in prompt
    assert "fs_read" not in prompt


def test_fs_registry_contract(monkeypatch):
    _install_fs_tool_specs(monkeypatch)
    for name in ("fs_list", "fs_read"):
        spec = tool_dispatcher._TOOL_REGISTRY[name]
        assert spec["category"] == "fs"
        assert spec["dangerous"] is False
        assert spec["examples"]
        assert spec["keywords"]
        assert spec["trace_args"] == ["path"]
    assert not tool_dispatcher.is_side_effect_tool("fs_list")
    assert not tool_dispatcher.is_side_effect_tool("fs_read")


# ── execute_structured() 集成：fs 类不受 desktop/system 安全模式闸约束 ──────

@pytest.mark.asyncio
async def test_fs_tools_execute_without_danger_mode(monkeypatch, tmp_path):
    _install_fs_tool_specs(monkeypatch)
    monkeypatch.setattr(tool_dispatcher, "_is_tool_enabled", lambda _: True)
    allow_root, _ = _patch_fs_config(monkeypatch, tmp_path)
    f = allow_root / "note.txt"
    f.write_text("hello", encoding="utf-8")

    result = await tool_dispatcher.execute_structured(
        "fs_read", {"path": str(f)}, "u1", "u1", False, _Session(),
        origin="user_live", char_id=TEST_CHAR_ID,
    )
    assert "hello" in result.result
    assert result.confirmation_request is None
