from __future__ import annotations

import json

from fastapi.testclient import TestClient


SECRET = "observability-test-admin-secret"


def _headers():
    return {"Authorization": f"Bearer {SECRET}"}


def _client(monkeypatch):
    monkeypatch.setattr("admin.auth.get_admin_secret", lambda: SECRET)
    from admin.admin_server import app
    return TestClient(app, raise_server_exceptions=False)


def _active(sandbox):
    sandbox.active_prompt_assets().parent.mkdir(parents=True, exist_ok=True)
    sandbox.active_prompt_assets().write_text(json.dumps({"active_character": "yexuan"}), encoding="utf-8")


def test_growth_endpoints_empty_and_auth(sandbox, monkeypatch):
    _active(sandbox)
    client = _client(monkeypatch)
    assert client.get("/growth/interests").status_code == 401
    assert client.get("/growth/interests", headers=_headers()).json()["interests"] == []
    assert client.get("/growth/works/not_real", headers=_headers()).json()["entries"] == []
    assert client.get("/growth/notes/not_real", headers=_headers()).json()["entries"] == []
    assert client.get("/growth/practice-log", headers=_headers()).json()["entries"] == []


def test_work_reader_is_index_bounded(sandbox, monkeypatch):
    _active(sandbox)
    client = _client(monkeypatch)
    root = sandbox.growth_works_dir("int_test", char_id="yexuan")
    root.mkdir(parents=True)
    (root / "index.json").write_text(json.dumps([{"file": "20260712_1.md", "date": "2026-07-12"}]), encoding="utf-8")
    (root / "20260712_1.md").write_text("作品正文", encoding="utf-8")
    ok = client.get("/growth/works/int_test/20260712_1.md", headers=_headers())
    assert ok.status_code == 200 and ok.json()["content"] == "作品正文"
    assert client.get("/growth/works/int_test/not-in-index.md", headers=_headers()).status_code == 404
    assert client.get("/growth/works/int_test/..%2Fsecret.md", headers=_headers()).status_code in (404, 422)


def test_visual_spend_digest_and_group_empty_views(sandbox, monkeypatch):
    _active(sandbox)
    client = _client(monkeypatch)
    assert client.get("/perception/visual-trace", headers=_headers()).json()["entries"] == []
    assert client.get("/perception/visual-trace?date=bad", headers=_headers()).status_code == 422
    assert client.get("/spend/mandates", headers=_headers()).json()["entries"] == []
    assert client.get("/memory/digest/u1", headers=_headers()).json()["content"] == ""


def test_debug_recall_alias_is_authenticated_and_empty_safe(sandbox, monkeypatch):
    _active(sandbox)
    client = _client(monkeypatch)
    assert client.get("/debug/recall?uid=u1").status_code == 401
    response = client.get("/debug/recall?uid=u1&char_id=yexuan", headers=_headers())
    assert response.status_code == 200
    payload = response.json()
    assert payload["uid"] == "u1"
    assert payload["char_id"] == "yexuan"
    assert payload["records"] == []
