"""Character self file space: CRUD, isolation, grant, quota, redaction (256 C)."""

from __future__ import annotations

import json
import os

import pytest
from fastapi.testclient import TestClient

from core import character_self as self_mod
from core import tool_dispatcher
from core.autonomy.policy import tool_eligibility
from tests.fixtures.public_assets import TEST_CHAR_ID


_UID = "owner-one"
_CHAR = TEST_CHAR_ID
_OTHER_UID = "owner-two"
_OTHER_CHAR = "other_character"


class _Session:
    WAITING_CONFIRM = "waiting_confirm"
    IDLE = "idle"
    status = IDLE

    def set_waiting_confirm(self, tool_name, tool_args):
        self.status = self.WAITING_CONFIRM


def _create(path, content="hello", *, uid=_UID, char=_CHAR):
    return self_mod.create_self(path, content, user_id=uid, char_id=char)


def _read(path, *, uid=_UID, char=_CHAR, offset=0):
    return self_mod.read_self(path, offset=offset, user_id=uid, char_id=char)


def _list(path=None, *, uid=_UID, char=_CHAR, depth=1):
    return self_mod.list_self(path=path, depth=depth, user_id=uid, char_id=char)


def test_accessors_are_scoped(sandbox):
    a = sandbox.character_self_root(_UID, char_id=_CHAR)
    b = sandbox.character_self_root(_OTHER_UID, char_id=_CHAR)
    c = sandbox.character_self_root(_UID, char_id=_OTHER_CHAR)
    meta = sandbox.character_self_meta_root(_UID, char_id=_CHAR)
    audit = sandbox.character_self_audit(_UID, char_id=_CHAR)
    assert a != b and a != c
    assert str(a).replace("\\", "/").endswith(f"runtime/self/{_CHAR}/{_UID}")
    assert str(meta).replace("\\", "/").endswith(f"runtime/self_meta/{_CHAR}/{_UID}")
    assert audit.parent == meta
    assert "self_management" not in str(a)


def test_first_use_creates_space_and_default_grant(sandbox):
    created = _create("notes/ledger.md", "day 1")
    assert created["ok"] is True
    assert created["revision"] == 1
    assert created["principal"]["uid"] == _UID
    assert created["principal"]["char_id"] == _CHAR
    assert created["causation"]["kind"] == "tool_request"
    listed = _list(depth=2)
    assert any(item["path"] == "notes/ledger.md" for item in listed["entries"])
    read = _read("notes/ledger.md")
    assert read["content"] == "day 1"
    assert sandbox.character_self_root(_UID, char_id=_CHAR).joinpath("notes", "ledger.md").is_file()
    grant = self_mod.load_grant(_UID, _CHAR)
    assert grant["allowed"] is True
    assert grant["source"] == "default"


def test_update_move_delete_restore_and_restart(sandbox):
    created = _create("habits/morning.md", "tea")
    rev = created["revision"]
    conflict = self_mod.update_self(
        "habits/morning.md", "coffee", expected_revision=rev + 1, user_id=_UID, char_id=_CHAR,
    )
    assert conflict["ok"] is False
    assert conflict["code"] == "revision_conflict"
    updated = self_mod.update_self(
        "habits/morning.md", "coffee", expected_revision=rev, user_id=_UID, char_id=_CHAR,
    )
    assert updated["ok"] is True
    dest_conflict = self_mod.move_self(
        "habits/morning.md", "notes/morning.md", expected_revision=rev, user_id=_UID, char_id=_CHAR,
    )
    assert dest_conflict["code"] == "revision_conflict"
    moved = self_mod.move_self(
        "habits/morning.md", "notes/morning.md",
        expected_revision=updated["revision"], user_id=_UID, char_id=_CHAR,
    )
    assert moved["ok"] is True
    assert _read("notes/morning.md")["content"] == "coffee"
    missing = _read("habits/morning.md")
    assert missing["ok"] is False
    assert missing["code"] == "path_not_found"
    deleted = self_mod.delete_self(
        "notes/morning.md", expected_revision=moved["revision"], user_id=_UID, char_id=_CHAR,
    )
    assert deleted["ok"] is True
    assert deleted["result"] == "deleted"
    restored = self_mod.restore_self(
        "notes/morning.md", revision=deleted["revision"], user_id=_UID, char_id=_CHAR,
    )
    assert restored["ok"] is True
    assert _read("notes/morning.md")["content"] == "coffee"
    # Persistence after "restart": objects still read from disk via accessors.
    again = _read("notes/morning.md")
    assert again["ok"] is True
    assert again["content"] == "coffee"


def test_move_overwrite_requires_explicit_flag(sandbox):
    src = _create("a.md", "src")
    dst = _create("b.md", "dst")
    denied = self_mod.move_self(
        "a.md", "b.md", expected_revision=src["revision"], user_id=_UID, char_id=_CHAR,
    )
    assert denied["code"] == "already_exists"
    ok = self_mod.move_self(
        "a.md", "b.md",
        expected_revision=src["revision"],
        overwrite=True,
        dest_expected_revision=dst["revision"],
        user_id=_UID, char_id=_CHAR,
    )
    assert ok["ok"] is True
    assert _read("b.md")["content"] == "src"


def test_cross_char_and_cross_owner_isolation(sandbox):
    _create("notes/private.md", "mine")
    other_char = _read("notes/private.md", uid=_UID, char=_OTHER_CHAR)
    other_owner = _read("notes/private.md", uid=_OTHER_UID, char=_CHAR)
    assert other_char["ok"] is False
    assert other_owner["ok"] is False
    assert other_char["code"] == "path_not_found"
    assert other_owner["code"] == "path_not_found"


def test_escape_and_meta_denied(sandbox):
    _create("ok.md", "ok")
    for raw, code in (
        ("../ok.md", "self_escape_denied"),
        ("notes/../../ok.md", "self_escape_denied"),
        ("C:/Windows/notepad.exe", "self_escape_denied"),
        ("revisions/1.bin", "self_path_denied"),
        ("audit.jsonl", "self_path_denied"),
        ("quota.json", "self_path_denied"),
        ("scratch.tmp", "self_path_denied"),
        ("\\\\server\\share\\x.md", "unc_network_denied"),
        ("notes:secret.md", "ads_denied"),
        ("CON.md", "device_path_denied"),
    ):
        result = _create(raw, "nope")
        assert result["ok"] is False, raw
        assert result["code"] == code, (raw, result)
    meta = sandbox.character_self_meta_root(_UID, char_id=_CHAR)
    aliased = self_mod.create_self(str(meta / "stolen.md"), "no", user_id=_UID, char_id=_CHAR)
    assert aliased["ok"] is False
    assert aliased["code"] in {"audit_store_denied", "self_escape_denied", "self_path_denied"}


def test_symlink_escape_denied(sandbox):
    created = _create("inside.md", "inside")
    assert created["ok"] is True
    root = sandbox.character_self_root(_UID, char_id=_CHAR)
    outside = sandbox.root_dir() / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    link = root / "link.md"
    try:
        link.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlink unavailable: {exc}")
    listed = _list(depth=2)
    assert all(item["path"] != "link.md" for item in listed.get("entries") or [])
    result = _read("link.md")
    assert result["ok"] is False
    assert result["code"] in {"reparse_denied", "self_escape_denied", "path_not_found"}


def test_hardlink_denied(sandbox):
    _create("live.md", "live")
    root = sandbox.character_self_root(_UID, char_id=_CHAR)
    outside = sandbox.root_dir() / "alias-target.txt"
    outside.write_text("alias", encoding="utf-8")
    link = root / "alias.md"
    try:
        os.link(outside, link)
    except OSError as exc:
        pytest.skip(f"hardlink unavailable: {exc}")
    result = _read("alias.md")
    assert result["ok"] is False
    assert result["code"] in {"self_escape_denied", "reparse_denied", "path_not_found"}


def test_workspace_root_not_writable_via_self(sandbox, monkeypatch, tmp_path):
    workspace = tmp_path / "workspace-root"
    workspace.mkdir()
    monkeypatch.setattr(
        "core.agent_runtime.workspace._roots",
        lambda: [workspace.resolve()],
    )
    created = _create("notes/ok.md", "ok")
    assert created["ok"] is True
    # Absolute workspace path is rejected at normalize time.
    escaped = _create(str(workspace / "stolen.md"), "no")
    assert escaped["ok"] is False
    assert escaped["code"] in {"self_escape_denied", "self_path_denied"}


def test_quota_exhausted_does_not_delete_live_file(sandbox, monkeypatch):
    monkeypatch.setattr(self_mod, "_quota_limits", lambda _p: {
        "max_file_bytes": 20,
        "max_total_bytes": 40,
        "max_files": 2,
        "max_revisions": 20,
        "revision_ttl_days": 14,
        "max_trash": 50,
        "trash_ttl_days": 30,
        "max_list_entries": 100,
        "max_list_depth": 2,
        "max_read_chars": 12000,
        "agent_md_chars": 2000,
    })
    first = _create("a.md", "12345")
    second = _create("b.md", "12345")
    assert first["ok"] and second["ok"]
    third = _create("c.md", "12345")
    assert third["ok"] is False
    assert third["code"] == "quota_exhausted"
    assert _read("a.md")["content"] == "12345"
    too_big = self_mod.update_self(
        "a.md", "x" * 50, expected_revision=first["revision"], user_id=_UID, char_id=_CHAR,
    )
    assert too_big["code"] == "quota_exhausted"
    assert _read("a.md")["content"] == "12345"


def test_corrupt_revision_state_fails_loudly(sandbox):
    created = _create("notes/x.md", "x")
    meta = sandbox.character_self_meta_root(_UID, char_id=_CHAR)
    digest = self_mod._rel_digest("notes/x.md")
    (meta / "revisions" / digest / "state.json").write_text("{not json", encoding="utf-8")
    result = self_mod.update_self(
        "notes/x.md", "y", expected_revision=created["revision"], user_id=_UID, char_id=_CHAR,
    )
    assert result["ok"] is False
    assert result["code"] == "self_path_denied"
    assert _read("notes/x.md")["content"] == "x"


def test_disk_write_failure_preserves_original(sandbox, monkeypatch):
    created = _create("keep.md", "original")
    real_write = self_mod.safe_write_bytes
    monkeypatch.setattr(self_mod, "safe_write_bytes", lambda *_a, **_k: False)
    failed = self_mod.update_self(
        "keep.md", "new", expected_revision=created["revision"], user_id=_UID, char_id=_CHAR,
    )
    assert failed["ok"] is False
    assert failed["code"] == "atomic_write_failed"
    monkeypatch.setattr(self_mod, "safe_write_bytes", real_write)
    assert _read("keep.md")["content"] == "original"
    retried = self_mod.update_self(
        "keep.md", "new", expected_revision=created["revision"], user_id=_UID, char_id=_CHAR,
    )
    assert retried["ok"] is True
    assert _read("keep.md")["content"] == "new"


def test_revoke_blocks_all_ops(sandbox):
    created = _create("n.md", "n")
    self_mod.set_grant(_UID, _CHAR, False)
    listed = _list()
    read = _read("n.md")
    updated = self_mod.update_self(
        "n.md", "m", expected_revision=created["revision"], user_id=_UID, char_id=_CHAR,
    )
    assert listed["code"] == "self_revoked"
    assert read["code"] == "self_revoked"
    assert updated["code"] == "self_revoked"
    self_mod.set_grant(_UID, _CHAR, True)
    assert _read("n.md")["content"] == "n"


def test_redaction_before_model_export(sandbox):
    secret = "sk-self-redaction-secret-aaaaaaaa"
    created = _create("secrets.md", f"token={secret}")
    assert created["ok"] is True
    read = _read("secrets.md")
    assert read["ok"] is True
    assert secret not in read["content"]
    assert "[REDACTED]" in read["content"]


def test_creating_python_is_not_a_process_grant(sandbox):
    created = _create("script.py", "print('hi')")
    assert created["ok"] is True
    from core.tool_dispatcher import _SIDE_EFFECT_TOOLS, _TOOL_REGISTRY, is_side_effect_tool
    assert _TOOL_REGISTRY["self_create"]["effect"] == "write"
    assert "process_run" not in created
    for name in ("self_create", "self_update", "self_move", "self_delete", "self_restore"):
        assert name in _SIDE_EFFECT_TOOLS
        assert is_side_effect_tool(name) is True
    assert is_side_effect_tool("self_list") is False
    assert is_side_effect_tool("self_read") is False


@pytest.mark.asyncio
async def test_tools_are_discoverable_and_not_danger_gated(sandbox, monkeypatch):
    monkeypatch.setattr(tool_dispatcher, "_is_tool_enabled", lambda _: True)
    monkeypatch.setattr(tool_dispatcher, "_current_mode", lambda: "safe")
    schemas = tool_dispatcher.get_tools_schema(categories=["info"])
    names = {(item.get("function") or {}).get("name") for item in schemas}
    for name in (
        "self_list", "self_read", "self_create", "self_update",
        "self_move", "self_delete", "self_restore",
    ):
        spec = tool_dispatcher._TOOL_REGISTRY[name]
        assert spec["category"] == "info"
        assert spec["dangerous"] is False
        assert spec.get("require_confirm") is not True
        assert spec["examples"]
        assert spec["keywords"]
        assert name in names
        eligible, reason = tool_eligibility(
            name, {"enabled": True}, registry=tool_dispatcher._TOOL_REGISTRY,
            effect=spec["effect"],
        )
        assert eligible is True, (name, reason)
    result = await tool_dispatcher.execute_structured(
        "self_create",
        {"path": "notes/from-chat.md", "content": "from chat"},
        _UID, _UID, False, _Session(),
        origin="user_live", char_id=_CHAR,
    )
    assert result.confirmation_request is None
    assert "notes/from-chat.md" in (result.result or "")
    assert '"ok": true' in (result.result or "") or '"ok":true' in (result.result or "")
    listed = await tool_dispatcher.execute_structured(
        "self_list", {"path": "notes", "depth": 1}, _UID, _UID, False, _Session(),
        origin="autonomy_loop", char_id=_CHAR,
    )
    assert "from-chat.md" in (listed.result or "")
    read_back = await tool_dispatcher.execute_structured(
        "self_read", {"path": "notes/from-chat.md"}, _UID, _UID, False, _Session(),
        origin="autonomy_loop", char_id=_CHAR,
    )
    assert "from chat" in (read_back.result or "")


@pytest.mark.asyncio
async def test_model_cannot_inject_principal(sandbox, monkeypatch):
    monkeypatch.setattr(tool_dispatcher, "_is_tool_enabled", lambda _: True)
    _create("notes/x.md", "x")
    result = await tool_dispatcher.execute_structured(
        "self_read",
        {"path": "notes/x.md", "user_id": "other", "char_id": "other-char"},
        _UID, _UID, False, _Session(),
        origin="user_live", char_id=_CHAR,
    )
    assert "grant_principal_mismatch" in (result.result or "")


def test_observability_is_metadata_only(sandbox, monkeypatch):
    secret = "character-self-obs-secret"
    monkeypatch.setattr("admin.auth.get_admin_secret", lambda: secret)
    _create("notes/private.md", "do not leak this body")
    from admin.admin_server import app

    client = TestClient(app, raise_server_exceptions=False)
    assert client.get("/observability/character-self").status_code == 401
    response = client.get(
        "/observability/character-self",
        params={"uid": _UID, "char_id": _CHAR},
        headers={"Authorization": f"Bearer {secret}"},
    )
    assert response.status_code == 200
    payload = response.json()
    blob = json.dumps(payload)
    assert payload["capability"] == "character-self.v1"
    assert payload["file_count"] >= 1
    assert "grant_revision" in payload
    assert "quota" in payload
    assert "do not leak this body" not in blob
    assert str(sandbox.character_self_root(_UID, char_id=_CHAR)) not in blob
    assert "sk-" not in blob


def test_restore_wrong_revision_and_realm_isolation(sandbox):
    created = _create("notes/keep.md", "keep")
    deleted = self_mod.delete_self(
        "notes/keep.md", expected_revision=created["revision"], user_id=_UID, char_id=_CHAR,
    )
    missing = self_mod.restore_self(
        "notes/keep.md", revision=deleted["revision"] + 9, user_id=_UID, char_id=_CHAR,
    )
    assert missing["ok"] is False
    assert missing["code"] == "path_not_found"
    restored = self_mod.restore_self(
        "notes/keep.md", revision=deleted["revision"], user_id=_UID, char_id=_CHAR,
    )
    assert restored["ok"] is True
    assert restored["principal"]["realm"] == "reality"
    assert _read("notes/keep.md")["content"] == "keep"


@pytest.mark.asyncio
async def test_self_delete_does_not_ask_user_confirm(sandbox, monkeypatch):
    monkeypatch.setattr(tool_dispatcher, "_is_tool_enabled", lambda _: True)
    created = _create("notes/tmp.md", "tmp")
    result = await tool_dispatcher.execute_structured(
        "self_delete",
        {"path": "notes/tmp.md", "expected_revision": created["revision"]},
        _UID, _UID, False, _Session(),
        origin="user_live", char_id=_CHAR,
    )
    assert result.confirmation_request is None
    assert result.status == "tool_executed"
    listed = _list()
    assert all(item["path"] != "notes/tmp.md" for item in listed.get("entries") or [])


def test_remote_server_keeps_self_tools(monkeypatch):
    from core import deployment_capabilities as dep

    monkeypatch.setattr(dep, "is_remote_server", lambda config=None: True)
    for name in (
        "self_list", "self_read", "self_create", "self_update",
        "self_move", "self_delete", "self_restore",
    ):
        allowed, reason = dep.tool_allowed(name)
        assert allowed is True, (name, reason)
    projection = {item.logical_name: item for item in dep.capability_projection()}
    assert projection["self_create"].status == "enabled"
