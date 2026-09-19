"""Read-only fs browsing: backend/external split and sensitive export (256 B)."""

import json

import pytest
from fastapi.testclient import TestClient

from core import tool_dispatcher
from core.tools import fs_browse
from tests.fixtures.public_assets import TEST_CHAR_ID


_UID = "owner-one"
_CHAR = TEST_CHAR_ID


def test_fs_read_default_limit_is_12k():
    assert fs_browse._DEFAULT_MAX_READ_CHARS == 12000

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
            "backend_read": True,
            "external_read": True,
            "allow_roots": allow_roots,
            "max_read_chars": 4000,
            "max_list_entries": 100,
        }
    }
    cfg["fs_access"].update(overrides)
    monkeypatch.setattr("core.config_loader.get_config", lambda: cfg)
    fake_data_dir = (tmp_path / "internal-data").resolve()
    fake_data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(fs_browse, "_project_data_dir", lambda: fake_data_dir)
    monkeypatch.setattr(fs_browse, "_repo_root", lambda: (tmp_path / "internal-repo").resolve())
    return allow_root, fake_data_dir


def _bind_sandbox_backend(sandbox, data_dir):
    """Keep scoped backend files inside the patched data root.

    pytest's sandbox fixture uses tmp_path as DataPaths._base.  If fs_browse
    also treats that directory as the project data root, ordinary files such
    as tmp_path/plain.txt are misclassified as backend.  Point the sandbox
    at the sibling internal-data directory instead so tmp_path peers stay
    external.
    """
    sandbox._base = data_dir


def _read(path, **kwargs):
    return fs_browse.fs_read(str(path), user_id=kwargs.get("user_id", _UID), char_id=kwargs.get("char_id", _CHAR))


def _list(path=None, **kwargs):
    return fs_browse.fs_list(None if path is None else str(path), user_id=kwargs.get("user_id", _UID), char_id=kwargs.get("char_id", _CHAR), depth=kwargs.get("depth", 1))


# ── 1. 外部普通文件不再因跨 allow_roots 拒绝 ──────────────────────────────────

def test_fs_read_allows_ordinary_file_outside_allow_roots(monkeypatch, tmp_path):
    allow_root, _ = _patch_fs_config(monkeypatch, tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("hello outside", encoding="utf-8")
    assert _read(outside) == "hello outside"
    assert "hello outside" in _read(allow_root / ".." / "outside.txt")


def test_fs_read_rejects_symlink_even_inside_allow_root(monkeypatch, tmp_path):
    allow_root, _ = _patch_fs_config(monkeypatch, tmp_path)
    target = allow_root / "real.txt"
    target.write_text("real content", encoding="utf-8")
    link = allow_root / "link.txt"
    try:
        link.symlink_to(target)
    except OSError as exc:
        pytest.skip(f"symlink unavailable: {exc}")
    assert _read(link) == "reparse_denied"


def test_fs_read_rejects_symlink_pointing_outside(monkeypatch, tmp_path):
    allow_root, _ = _patch_fs_config(monkeypatch, tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("secret stuff", encoding="utf-8")
    link = allow_root / "link.txt"
    try:
        link.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlink unavailable: {exc}")
    assert _read(link) == "reparse_denied"


# ── 2. 高风险凭据按类型/内容拒绝，不再用 token 子串 ──────────────────────────

def test_fs_read_allows_tokenizer_source(monkeypatch, tmp_path):
    allow_root, _ = _patch_fs_config(monkeypatch, tmp_path)
    source = allow_root / "tokenizer.py"
    source.write_text("token_count = 3\n", encoding="utf-8", newline="\n")
    assert _read(source) == "token_count = 3\n"


def test_fs_read_rejects_env_and_private_key(monkeypatch, tmp_path):
    allow_root, _ = _patch_fs_config(monkeypatch, tmp_path)
    env = allow_root / ".env"
    env.write_text("API_KEY=should-not-leak", encoding="utf-8")
    assert _read(env) == "credential_store_denied"
    key = allow_root / "notes.md"
    key.write_text("-----BEGIN PRIVATE KEY-----\nMIIHIDE\n-----END PRIVATE KEY-----\n", encoding="utf-8")
    assert _read(key) == "high_risk_secret_denied"


def test_fs_read_redacts_secrets_before_truncate(monkeypatch, tmp_path):
    allow_root, _ = _patch_fs_config(monkeypatch, tmp_path, max_read_chars=20)
    f = allow_root / "config.json"
    secret = "sk-live-this-must-not-appear-even-across-pages"
    f.write_text('{"model":"demo","api_key":"%s"}' % secret, encoding="utf-8")
    result = _read(f)
    assert secret not in result
    assert "demo" in result or "api_key" in result
    page2 = fs_browse.fs_read(str(f), offset=20, user_id=_UID, char_id=_CHAR)
    assert secret not in page2


def test_model_routing_config_remains_explainable_after_redaction(monkeypatch, tmp_path):
    allow_root, _ = _patch_fs_config(monkeypatch, tmp_path)
    config = allow_root / "routing.yaml"
    secret = "sk-live-routing-secret-value"
    config.write_text(
        "model_presets:\n  active_routing: balanced\n  presets:\n    chat-main:\n"
        f"      model: example-chat\n      api_key: {secret}\n",
        encoding="utf-8",
        newline="\n",
    )
    result = _read(config)
    assert "active_routing: balanced" in result
    assert "model: example-chat" in result
    assert secret not in result


# ── 3. backend 可读；其他角色桶隔离 ───────────────────────────────────────────

def test_fs_read_backend_sandbox_file_when_same_scope(monkeypatch, tmp_path, sandbox):
    _, data_dir = _patch_fs_config(monkeypatch, tmp_path)
    _bind_sandbox_backend(sandbox, data_dir)
    target = sandbox.user_memory_root(_UID, char_id=_CHAR) / "note.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("own memory note", encoding="utf-8")
    assert "own memory note" in fs_browse.fs_read(str(target), user_id=_UID, char_id=_CHAR)


def test_fs_read_rejects_other_character_memory(monkeypatch, tmp_path, sandbox):
    _, data_dir = _patch_fs_config(monkeypatch, tmp_path)
    _bind_sandbox_backend(sandbox, data_dir)
    other = sandbox.user_memory_root(_UID, char_id="other-char") / "secret.md"
    other.parent.mkdir(parents=True, exist_ok=True)
    other.write_text("other char private", encoding="utf-8")
    assert fs_browse.fs_read(str(other), user_id=_UID, char_id=_CHAR) == "cross_char_denied"


def test_fs_read_rejects_dream_tree(monkeypatch, tmp_path, sandbox):
    _, data_dir = _patch_fs_config(monkeypatch, tmp_path)
    _bind_sandbox_backend(sandbox, data_dir)
    dream = sandbox.dream_state_path(_UID, char_id=_CHAR)
    dream.parent.mkdir(parents=True, exist_ok=True)
    dream.write_text("{}", encoding="utf-8")
    assert fs_browse.fs_read(str(dream), user_id=_UID, char_id=_CHAR) == "dream_isolation_denied"


def test_fs_list_hides_isolated_children(monkeypatch, tmp_path, sandbox):
    _, data_dir = _patch_fs_config(monkeypatch, tmp_path)
    _bind_sandbox_backend(sandbox, data_dir)
    memory_root = sandbox.user_memory_root(_UID, char_id=_CHAR)
    memory_root.mkdir(parents=True, exist_ok=True)
    (memory_root / "visible.md").write_text("ok", encoding="utf-8")
    other = sandbox.user_memory_root(_UID, char_id="other-char")
    other.mkdir(parents=True, exist_ok=True)
    (other / "hidden.md").write_text("nope", encoding="utf-8")
    parent = memory_root.parent.parent  # runtime/memory
    listed = fs_browse.fs_list(str(parent), user_id=_UID, char_id=_CHAR)
    assert _CHAR in listed
    assert "other-char" not in listed


# ── 4. 截断 / 限额 ────────────────────────────────────────────────────────────

def test_fs_read_truncates_long_file(monkeypatch, tmp_path):
    allow_root, _ = _patch_fs_config(monkeypatch, tmp_path, max_read_chars=5)
    f = allow_root / "long.txt"
    f.write_text("0123456789", encoding="utf-8")
    result = _read(f)
    assert result.startswith("01234")
    assert "已截断" in result
    assert "共 10 字" in result


def test_fs_list_truncates_long_directory(monkeypatch, tmp_path):
    allow_root, _ = _patch_fs_config(monkeypatch, tmp_path, max_list_entries=2)
    for i in range(5):
        (allow_root / f"file{i}.txt").write_text("x", encoding="utf-8")
    result = _list(allow_root)
    assert "已达 2 条上限" in result
    assert "list_limit_exceeded" in result
    assert len([line for line in result.splitlines() if "file" in line]) == 2


def test_fs_read_rejects_oversized_file(monkeypatch, tmp_path):
    allow_root, _ = _patch_fs_config(monkeypatch, tmp_path)
    monkeypatch.setattr(fs_browse, "_max_file_bytes", lambda: 10)
    f = allow_root / "big.txt"
    f.write_text("x" * 20, encoding="utf-8")
    assert _read(f) == "file_size_limit_exceeded"


# ── 5. 编码 / 非文本 ──────────────────────────────────────────────────────────

def test_fs_read_decodes_gbk_file(monkeypatch, tmp_path):
    allow_root, _ = _patch_fs_config(monkeypatch, tmp_path)
    f = allow_root / "gbk.txt"
    f.write_bytes("你好世界".encode("gbk"))
    assert _read(f) == "你好世界"


def test_fs_read_binary_extension_returns_unsupported(monkeypatch, tmp_path):
    allow_root, _ = _patch_fs_config(monkeypatch, tmp_path)
    f = allow_root / "image.bin"
    f.write_bytes(bytes(range(256)))
    assert _read(f) == "unsupported_file_type"


def test_fs_read_undecodable_text_extension_returns_unsupported(monkeypatch, tmp_path):
    allow_root, _ = _patch_fs_config(monkeypatch, tmp_path)
    f = allow_root / "garbled.txt"
    f.write_bytes(b"\xff\xfe\x00\x01\x02\x03")
    assert _read(f) == "unsupported_file_type"


def test_fs_read_unknown_extension_text_detect(monkeypatch, tmp_path):
    allow_root, _ = _patch_fs_config(monkeypatch, tmp_path)
    f = allow_root / "notes.unknown"
    f.write_text("plain unknown", encoding="utf-8")
    assert _read(f) == "plain unknown"


def test_fs_read_redaction_failure_does_not_return_raw(monkeypatch, tmp_path):
    allow_root, _ = _patch_fs_config(monkeypatch, tmp_path)
    secret = "sk-live-must-not-leak-on-failure"
    f = allow_root / "cfg.json"
    f.write_text('{"api_key":"%s"}' % secret, encoding="utf-8")

    def boom(_text):
        raise RuntimeError("broken")

    monkeypatch.setattr("core.tools.fs_browse.redact_for_export", boom)
    result = _read(f)
    assert result == "sensitive_redaction_failed"
    assert secret not in result


def test_fs_read_concurrent_replace_is_denied(monkeypatch, tmp_path):
    from pathlib import Path

    allow_root, _ = _patch_fs_config(monkeypatch, tmp_path)
    f = allow_root / "swap.txt"
    f.write_text("hello", encoding="utf-8")
    original = Path.read_bytes

    def swap(self, *args, **kwargs):
        data = original(self, *args, **kwargs)
        try:
            same = Path(self).resolve() == f.resolve()
        except OSError:
            same = False
        if same:
            f.write_bytes(data + b"x" * 99)
        return data

    monkeypatch.setattr(Path, "read_bytes", swap)
    assert _read(f) == "path_not_found"


def test_fs_list_respects_depth(monkeypatch, tmp_path):
    allow_root, _ = _patch_fs_config(monkeypatch, tmp_path)
    nested = allow_root / "sub"
    nested.mkdir()
    (nested / "inner.txt").write_text("x", encoding="utf-8")
    shallow = _list(allow_root, depth=1)
    assert "sub/" in shallow
    assert "inner.txt" not in shallow
    deep = fs_browse.fs_list(str(allow_root), user_id=_UID, char_id=_CHAR, depth=2)
    assert "inner.txt" in deep


# ── 6. 独立开关 / 发现提示 / remote ───────────────────────────────────────────

def test_backend_disabled_does_not_block_external(monkeypatch, tmp_path, sandbox):
    _, data_dir = _patch_fs_config(monkeypatch, tmp_path, backend_read=False, external_read=True)
    _bind_sandbox_backend(sandbox, data_dir)
    note = sandbox.user_memory_root(_UID, char_id=_CHAR) / "ok.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text("backend hidden", encoding="utf-8")
    assert fs_browse.fs_read(str(note), user_id=_UID, char_id=_CHAR) == "backend_read_disabled"
    outside = tmp_path / "plain.txt"
    outside.write_text("ext ok", encoding="utf-8")
    assert fs_browse.fs_read(str(outside), user_id=_UID, char_id=_CHAR) == "ext ok"


def test_backend_repo_source_readable_when_external_off(monkeypatch, tmp_path):
    _patch_fs_config(monkeypatch, tmp_path, external_read=False, backend_read=True)
    repo = tmp_path / "repo"
    src = repo / "core" / "tokenizer.py"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text("token_count = 3\n", encoding="utf-8", newline="\n")
    monkeypatch.setattr(fs_browse, "_repo_root", lambda: repo.resolve())
    assert fs_browse.fs_read(str(src), user_id=_UID, char_id=_CHAR) == "token_count = 3\n"


def test_external_disabled_does_not_block_backend(monkeypatch, tmp_path, sandbox):
    _, data_dir = _patch_fs_config(monkeypatch, tmp_path, enabled=False, external_read=False, backend_read=True)
    _bind_sandbox_backend(sandbox, data_dir)
    outside = tmp_path / "plain.txt"
    outside.write_text("ext", encoding="utf-8")
    assert fs_browse.fs_read(str(outside), user_id=_UID, char_id=_CHAR) == "external_read_disabled"
    note = sandbox.user_memory_root(_UID, char_id=_CHAR) / "ok.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text("backend ok", encoding="utf-8")
    assert "backend ok" in fs_browse.fs_read(str(note), user_id=_UID, char_id=_CHAR)


def test_legacy_enabled_false_disables_external_only(monkeypatch, tmp_path):
    allow_root, _ = _patch_fs_config(monkeypatch, tmp_path, enabled=False)
    cfg = {"fs_access": {"enabled": False, "allow_roots": [str(allow_root)]}}
    monkeypatch.setattr("core.config_loader.get_config", lambda: cfg)
    f = allow_root / "file.txt"
    f.write_text("hi", encoding="utf-8")
    assert _read(f) == "external_read_disabled"


def test_fs_list_without_path_returns_discovery_map(monkeypatch, tmp_path):
    allow_root, _ = _patch_fs_config(monkeypatch, tmp_path)
    result = _list(None)
    assert "发现提示" in result
    assert str(allow_root) in result


def test_unc_and_ads_denied(monkeypatch, tmp_path):
    _patch_fs_config(monkeypatch, tmp_path)
    assert fs_browse.fs_read(r"\\server\share\file.txt", user_id=_UID, char_id=_CHAR) == "unc_network_denied"
    ads = str(tmp_path / "allow" / "file.txt") + ":secret"
    assert fs_browse.fs_read(ads, user_id=_UID, char_id=_CHAR) == "ads_denied"


def test_missing_principal_is_mismatch(monkeypatch, tmp_path):
    allow_root, _ = _patch_fs_config(monkeypatch, tmp_path)
    f = allow_root / "a.txt"
    f.write_text("x", encoding="utf-8")
    assert fs_browse.fs_read(str(f)) == "grant_principal_mismatch"


def test_remote_server_allows_backend_denies_external(monkeypatch, tmp_path, sandbox):
    _, data_dir = _patch_fs_config(monkeypatch, tmp_path)
    _bind_sandbox_backend(sandbox, data_dir)
    monkeypatch.setattr("core.deployment_capabilities.is_remote_server", lambda: True)
    outside = tmp_path / "pc.txt"
    outside.write_text("user-pc", encoding="utf-8")
    assert fs_browse.fs_read(str(outside), user_id=_UID, char_id=_CHAR) == "disabled_remote_server_local_capability"
    note = sandbox.user_memory_root(_UID, char_id=_CHAR) / "ok.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text("server backend", encoding="utf-8")
    assert "server backend" in fs_browse.fs_read(str(note), user_id=_UID, char_id=_CHAR)
    state = fs_browse.effective_state()
    assert state["backend_read"]["effective"] is True
    assert state["external_read"]["blocking_reason"] == "disabled_remote_server_local_capability"
    assert state["redaction"]["version"]


# ── 7. schema / execute ───────────────────────────────────────────────────────

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


@pytest.mark.asyncio
async def test_model_cannot_inject_principal(monkeypatch, tmp_path):
    _install_fs_tool_specs(monkeypatch)
    monkeypatch.setattr(tool_dispatcher, "_is_tool_enabled", lambda _: True)
    allow_root, _ = _patch_fs_config(monkeypatch, tmp_path)
    f = allow_root / "note.txt"
    f.write_text("hello", encoding="utf-8")
    result = await tool_dispatcher.execute_structured(
        "fs_read",
        {"path": str(f), "user_id": "other", "char_id": "other-char"},
        _UID, _UID, False, _Session(),
        origin="user_live", char_id=_CHAR,
    )
    assert "grant_principal_mismatch" in result.result


def test_backend_read_observability_is_metadata_only(monkeypatch, tmp_path):
    secret = "backend-read-obs-secret"
    monkeypatch.setattr("admin.auth.get_admin_secret", lambda: secret)
    _patch_fs_config(monkeypatch, tmp_path)
    from admin.admin_server import app

    client = TestClient(app, raise_server_exceptions=False)
    assert client.get("/observability/backend-read").status_code == 401
    response = client.get(
        "/observability/backend-read",
        headers={"Authorization": f"Bearer {secret}"},
    )
    assert response.status_code == 200
    payload = response.json()
    blob = json.dumps(payload)
    assert payload["capability"] == "backend-read.v1"
    assert payload["backend_read"]["effective"] is True
    assert payload["redaction"]["version"]
    assert "allow_roots_count" in payload["external_read"]
    assert str(tmp_path) not in blob
    assert "sk-" not in blob
    assert "api_key" not in blob
