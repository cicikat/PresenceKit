"""Sandbox chat artifacts: write/read/list, payload strip, download scopes."""

from tests.fixtures.public_assets import TEST_CHAR_ID

import json

import pytest
from fastapi.testclient import TestClient

from core import tool_dispatcher
from core.tools import chat_artifacts


_ARTIFACT_TOOL_SPECS = {
    name: dict(tool_dispatcher._TOOL_REGISTRY[name])
    for name in ("write_artifact", "read_artifact", "list_artifacts")
}


class _Session:
    WAITING_CONFIRM = "waiting_confirm"
    IDLE = "idle"
    status = IDLE

    def set_waiting_confirm(self, tool_name, tool_args):
        self.status = self.WAITING_CONFIRM


def _install_artifact_tool_specs(monkeypatch):
    for name, spec in _ARTIFACT_TOOL_SPECS.items():
        monkeypatch.setitem(tool_dispatcher._TOOL_REGISTRY, name, spec)


def _write(filename="note.md", content="# hi", uid="u1", char_id=TEST_CHAR_ID):
    return json.loads(chat_artifacts.write_artifact(
        filename, content, user_id=uid, char_id=char_id,
    ))


def test_write_read_list_roundtrip(sandbox):
    written = _write()
    assert written["status"] == "written"
    assert written["filename"] == "note.md"
    assert "content" not in written
    assert "\\" not in json.dumps(written, ensure_ascii=False)
    assert "/" not in written["id"]

    listed = json.loads(chat_artifacts.list_artifacts(user_id="u1", char_id=TEST_CHAR_ID))
    assert listed["count"] == 1
    assert listed["items"][0]["id"] == written["id"]
    assert "content" not in listed["items"][0]

    read = json.loads(chat_artifacts.read_artifact(
        written["id"], user_id="u1", char_id=TEST_CHAR_ID,
    ))
    assert read["content"] == "# hi"
    assert read["truncated"] is False


def test_public_payload_strips_body_and_absolute_path(sandbox):
    written = _write()
    record = chat_artifacts.get_artifact_record(
        written["id"], uid="u1", char_id=TEST_CHAR_ID,
    )
    payload = chat_artifacts.public_payload(record)
    assert set(payload) >= {"id", "filename", "mime", "size", "download_url", "previewable"}
    assert "content" not in payload
    blob = json.dumps(payload)
    assert str(sandbox.chat_artifacts_dir("u1", char_id=TEST_CHAR_ID)) not in blob
    assert payload["download_url"] == f"/chat/artifacts/{written['id']}"
    assert payload["preview_url"] == f"/chat/artifacts/{written['id']}/preview"
    assert payload["previewable"] is True


def test_rejects_traversal_and_binary_and_oversize(sandbox, tmp_path):
    with pytest.raises(chat_artifacts.ArtifactError, match="文件名不合法"):
        chat_artifacts.write_artifact("../escape.md", "nope", user_id="u1", char_id=TEST_CHAR_ID)
    with pytest.raises(chat_artifacts.ArtifactError, match="文件名不合法"):
        chat_artifacts.write_artifact("..\\escape.md", "nope", user_id="u1", char_id=TEST_CHAR_ID)
    with pytest.raises(chat_artifacts.ArtifactError, match="只支持这些文本扩展名"):
        chat_artifacts.write_artifact("pic.png", "nope", user_id="u1", char_id=TEST_CHAR_ID)
    with pytest.raises(chat_artifacts.ArtifactError, match="单次写入不能超过"):
        chat_artifacts.write_artifact(
            "big.txt", "x" * (chat_artifacts.MAX_CONTENT_CHARS + 1),
            user_id="u1", char_id=TEST_CHAR_ID,
        )
    assert not (tmp_path / "escape.md").exists()
    root = sandbox.chat_artifacts_dir("u1", char_id=TEST_CHAR_ID)
    if root.exists():
        assert list(root.glob("*")) in ([], [root / "index.json"]) or all(
            p.name in {"index.json"} or p.suffix in chat_artifacts.ALLOWED_EXTENSIONS
            for p in root.iterdir()
        )


def test_collector_drains_on_empty_and_does_not_leak(sandbox):
    chat_artifacts.begin_turn_collection()
    written = _write()
    pending = chat_artifacts.peek_turn_artifacts()
    assert pending and pending[0]["id"] == written["id"]
    drained = chat_artifacts.drain_turn_artifacts()
    assert drained[0]["id"] == written["id"]
    assert chat_artifacts.drain_turn_artifacts() == []
    chat_artifacts.begin_turn_collection()
    assert chat_artifacts.peek_turn_artifacts() == []
    chat_artifacts.drain_turn_artifacts()


def test_js_is_writable_but_not_previewable(sandbox):
    written = _write("script.js", "console.log(1)")
    record = chat_artifacts.get_artifact_record(
        written["id"], uid="u1", char_id=TEST_CHAR_ID,
    )
    payload = chat_artifacts.public_payload(record)
    assert payload["previewable"] is False
    assert "preview_url" not in payload


@pytest.mark.asyncio
async def test_artifact_tools_are_path_c_not_probe(sandbox, monkeypatch):
    _install_artifact_tool_specs(monkeypatch)
    monkeypatch.setattr(tool_dispatcher, "_is_tool_enabled", lambda _: True)
    prompt = tool_dispatcher.get_probe_prompt("nowhere")
    assert "write_artifact" not in prompt
    assert "read_artifact" not in prompt
    result, confirm = await tool_dispatcher.execute(
        "write_artifact",
        {"filename": "ok.md", "content": "hello"},
        "u1",
        "u1",
        False,
        _Session(),
        origin="assistant_loop",
        char_id=TEST_CHAR_ID,
    )
    assert confirm is None
    assert "written" in result
    assert tool_dispatcher.is_side_effect_tool("write_artifact")
    spec = tool_dispatcher._TOOL_REGISTRY["write_artifact"]
    assert spec["category"] == "artifacts"
    assert spec["examples"] and spec["keywords"]


def _client(monkeypatch):
    monkeypatch.setattr("admin.auth.get_admin_secret", lambda: "artifact-admin-secret")
    from admin.admin_server import app
    return TestClient(app, raise_server_exceptions=False)


def _scoped(sandbox, raw, scopes):
    from admin import token_registry
    token_registry._records = None
    token_registry._mtime = None
    path = sandbox.auth_tokens_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    import yaml
    path.write_text(yaml.safe_dump({
        "tokens": [{
            "label": "t-artifact",
            "hash": token_registry.hash_token(raw),
            "scopes": scopes,
        }],
    }), encoding="utf-8")


def test_download_preview_and_observability_scopes(sandbox, monkeypatch):
    written = _write("page.html", "<p>hi</p>")
    client = _client(monkeypatch)

    assert client.get(f"/chat/artifacts/{written['id']}").status_code == 401
    assert client.get("/observability/chat-artifacts").status_code == 401

    _scoped(sandbox, "emt_chat", ["chat"])
    chat_headers = {"Authorization": "Bearer emt_chat"}
    download = client.get(f"/chat/artifacts/{written['id']}", headers=chat_headers)
    assert download.status_code == 200
    assert download.content == b"<p>hi</p>"
    assert download.headers.get("x-content-type-options") == "nosniff"
    preview = client.get(f"/chat/artifacts/{written['id']}/preview", headers=chat_headers)
    assert preview.status_code == 200
    assert "default-src 'none'" in preview.headers.get("content-security-policy", "")
    assert "script-src 'none'" in preview.headers.get("content-security-policy", "")
    assert client.get(
        "/observability/chat-artifacts?uid=u1&char_id=" + TEST_CHAR_ID,
        headers=chat_headers,
    ).status_code == 403

    _scoped(sandbox, "emt_state", ["state.read"])
    state_headers = {"Authorization": "Bearer emt_state"}
    obs = client.get(
        f"/observability/chat-artifacts?uid=u1&char_id={TEST_CHAR_ID}",
        headers=state_headers,
    )
    assert obs.status_code == 200
    body = obs.json()
    assert body["count"] == 1
    assert body["items"][0]["id"] == written["id"]
    assert "content" not in body["items"][0]
    assert client.get(f"/chat/artifacts/{written['id']}", headers=state_headers).status_code == 403

    js = _write("app.js", "alert(1)")
    _scoped(sandbox, "emt_chat2", ["chat"])
    denied = client.get(
        f"/chat/artifacts/{js['id']}/preview",
        headers={"Authorization": "Bearer emt_chat2"},
    )
    assert denied.status_code == 415


def test_admin_observe_page_uses_authenticated_fetch():
    from pathlib import Path
    source = (Path(__file__).parents[1] / "admin" / "static" / "js" / "observability.js").read_text(encoding="utf-8")
    assert "downloadObserveChatArtifact" in source
    assert "previewObserveChatArtifact" in source
    assert "Authorization: `Bearer ${TOKEN}`" in source
    assert 'href="/chat/artifacts/' not in source
    page = (Path(__file__).parents[1] / "admin" / "static" / "pages" / "observe-chat-artifacts.html").read_text(encoding="utf-8")
    assert 'id="obs-artifacts-preview"' in page
