"""Sandbox chat artifacts: write/read/list, payload strip, download scopes."""

from tests.fixtures.public_assets import TEST_CHAR_ID

import json

import pytest
from fastapi.testclient import TestClient

from core import tool_dispatcher
from core.tools import chat_artifacts


_ARTIFACT_TOOL_SPECS = {
    name: dict(tool_dispatcher._TOOL_REGISTRY[name])
    for name in ("write_artifact", "update_artifact", "read_artifact", "list_artifacts")
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


def test_update_artifact_in_place_keeps_prev_and_revision(sandbox):
    written = _write("plan.md", "v1")
    chat_artifacts.begin_turn_collection()
    updated = json.loads(chat_artifacts.update_artifact(
        written["id"], "v2 内容", user_id="u1", char_id=TEST_CHAR_ID,
    ))
    pending = chat_artifacts.drain_turn_artifacts()
    assert updated["status"] == "updated"
    assert updated["id"] == written["id"]
    assert updated["revision"] == 2
    assert pending and pending[0]["id"] == written["id"] and pending[0]["updated"] is True
    read = json.loads(chat_artifacts.read_artifact(
        written["id"], user_id="u1", char_id=TEST_CHAR_ID,
    ))
    assert read["content"] == "v2 内容"
    assert read["revision"] == 2
    root = sandbox.chat_artifacts_dir("u1", char_id=TEST_CHAR_ID)
    assert (root / f"{written['id']}.prev.md").read_text(encoding="utf-8") == "v1"
    record = chat_artifacts.get_artifact_record(written["id"], uid="u1", char_id=TEST_CHAR_ID)
    assert record["size"] == len("v2 内容".encode("utf-8"))
    assert record["updated_at"] >= record["created_at"]
    listed = json.loads(chat_artifacts.list_artifacts(user_id="u1", char_id=TEST_CHAR_ID))
    assert listed["count"] == 1
    json.loads(chat_artifacts.update_artifact(
        written["id"], "v3", user_id="u1", char_id=TEST_CHAR_ID,
    ))
    assert (root / f"{written['id']}.prev.md").read_text(encoding="utf-8") == "v2 内容"


def test_update_artifact_rejects_cross_scope_and_missing(sandbox):
    written = _write("plan.md", "v1")
    with pytest.raises(chat_artifacts.ArtifactError, match="找不到"):
        chat_artifacts.update_artifact(written["id"], "x", user_id="u2", char_id=TEST_CHAR_ID)
    with pytest.raises(chat_artifacts.ArtifactError, match="找不到"):
        chat_artifacts.update_artifact(written["id"], "x", user_id="u1", char_id="other_char")
    with pytest.raises(chat_artifacts.ArtifactError, match="找不到"):
        chat_artifacts.update_artifact("f" * 32, "x", user_id="u1", char_id=TEST_CHAR_ID)
    read = json.loads(chat_artifacts.read_artifact(written["id"], user_id="u1", char_id=TEST_CHAR_ID))
    assert read["content"] == "v1"


def test_update_artifact_sha_conflict(sandbox):
    written = _write("plan.md", "v1")
    sha = json.loads(chat_artifacts.read_artifact(
        written["id"], user_id="u1", char_id=TEST_CHAR_ID,
    ))["sha256"]
    ok = json.loads(chat_artifacts.update_artifact(
        written["id"], "v2", expected_sha256=sha, user_id="u1", char_id=TEST_CHAR_ID,
    ))
    assert ok["revision"] == 2
    with pytest.raises(chat_artifacts.ArtifactError, match="sha256"):
        chat_artifacts.update_artifact(
            written["id"], "v3", expected_sha256=sha, user_id="u1", char_id=TEST_CHAR_ID,
        )
    read = json.loads(chat_artifacts.read_artifact(written["id"], user_id="u1", char_id=TEST_CHAR_ID))
    assert read["content"] == "v2"


def test_update_artifact_concurrent_sha_only_one_wins(sandbox):
    from concurrent.futures import ThreadPoolExecutor
    written = _write("plan.md", "v1")
    sha = json.loads(chat_artifacts.read_artifact(
        written["id"], user_id="u1", char_id=TEST_CHAR_ID,
    ))["sha256"]

    def attempt(text):
        try:
            chat_artifacts.update_artifact(
                written["id"], text, expected_sha256=sha, user_id="u1", char_id=TEST_CHAR_ID,
            )
            return True
        except chat_artifacts.ArtifactError:
            return False

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(attempt, [f"w{i}" for i in range(4)]))
    assert results.count(True) == 1


def test_overflow_eviction_logs_info(sandbox, caplog, monkeypatch):
    monkeypatch.setattr(chat_artifacts, "MAX_FILES_PER_SCOPE", 2)
    with caplog.at_level("INFO", logger=chat_artifacts.logger.name):
        for i in range(3):
            _write(f"n{i}.md", "x")
    assert any("淘汰最旧产物" in r.getMessage() for r in caplog.records)


def test_read_artifact_redacts_secrets_before_truncate(sandbox):
    secret = "sk-live-artifact-secret-value"
    written = _write(content='{"model":"demo","api_key":"%s"}' % secret)
    read = json.loads(chat_artifacts.read_artifact(
        written["id"], user_id="u1", char_id=TEST_CHAR_ID,
    ))
    assert "demo" in read["content"]
    assert "api_key" in read["content"]
    assert secret not in read["content"]


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
    result = await tool_dispatcher.execute_structured(
        "write_artifact",
        {"filename": "ok.md", "content": "hello"},
        "u1",
        "u1",
        False,
        _Session(),
        origin="assistant_loop",
        char_id=TEST_CHAR_ID,
    )
    assert result.confirmation_request is None
    assert "written" in result.result
    written_id = json.loads(result.result[result.result.index("{"):])["id"]
    updated = await tool_dispatcher.execute_structured(
        "update_artifact",
        {"artifact_id": written_id, "content": "hello again"},
        "u1",
        "u1",
        False,
        _Session(),
        origin="assistant_loop",
        char_id=TEST_CHAR_ID,
    )
    assert "updated" in updated.result
    assert tool_dispatcher.is_side_effect_tool("update_artifact")
    assert tool_dispatcher._TOOL_REGISTRY["update_artifact"]["category"] == "artifacts"
    assert tool_dispatcher._TOOL_REGISTRY["update_artifact"]["examples"]
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
    chat_artifacts.update_artifact(written["id"], "<p>new</p>", user_id="u1", char_id=TEST_CHAR_ID)
    assert client.get(f"/chat/artifacts/{written['id']}", headers=chat_headers).content == b"<p>new</p>"
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
    assert "window.loadObserveChatIdentity = loadObserveChatIdentity" in source
    assert "window.loadObserveChatMedia = loadObserveChatMedia" in source
    assert "/observability/chat-identity" in source
    assert "/observability/chat-media" in source
    assert "Authorization: `Bearer ${TOKEN}`" in source
    assert 'href="/chat/artifacts/' not in source
    page = (Path(__file__).parents[1] / "admin" / "static" / "pages" / "observe-chat-artifacts.html").read_text(encoding="utf-8")
    assert 'id="obs-artifacts-preview"' in page


def test_turn_links_persist_metadata_only(sandbox):
    written = _write()
    payload = chat_artifacts.public_payload(written | {"mime": "text/markdown", "size": 4})
    assert chat_artifacts.link_turn_artifacts(
        "turn-1", [payload], uid="u1", char_id=TEST_CHAR_ID)
    got = chat_artifacts.artifacts_for_turns(
        ["turn-1", "turn-x"], uid="u1", char_id=TEST_CHAR_ID)
    assert list(got) == ["turn-1"]
    assert got["turn-1"][0]["id"] == written["id"]
    assert got["turn-1"][0]["download_url"].endswith(written["id"])
    snap = chat_artifacts.observability_snapshot(uid="u1", char_id=TEST_CHAR_ID)
    assert snap["turn_links"] == 1


def test_turn_links_fifo_cap(sandbox, monkeypatch):
    monkeypatch.setattr(chat_artifacts, "MAX_TURN_LINKS", 2)
    item = {"id": "a" * 32, "filename": "a.txt"}
    for n in range(3):
        chat_artifacts.link_turn_artifacts(f"t{n}", [item], uid="u1", char_id=TEST_CHAR_ID)
    got = chat_artifacts.artifacts_for_turns(["t0", "t1", "t2"], uid="u1", char_id=TEST_CHAR_ID)
    assert sorted(got) == ["t1", "t2"]
