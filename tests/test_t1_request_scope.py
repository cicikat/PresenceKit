"""T1 preserves HTTP admission errors while the selected role is owned by Core."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.fixtures.public_assets import TEST_CHAR_ID


@pytest.mark.parametrize("contents,status,detail", [
    ('{"active_character":""}', 503, "active_character missing"),
    ('{broken', 503, "active character unavailable"),
    ('{"active_character":"unavailable_fixture"}', 422, "unknown character id"),
    ('{"active_character":"' + TEST_CHAR_ID + '"}', 200, ""),
])
def test_activity_http_preserves_domain_scope_and_errors(sandbox, monkeypatch, contents, status, detail):
    from admin import auth
    from admin.routers import activity
    path = sandbox.active_prompt_assets()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(contents.encode())
    monkeypatch.setattr(auth, "resolve_token", lambda token: auth.TokenInfo(
        "fixture-state", frozenset({"state.read"})) if token == "fixture-state" else None)
    app = FastAPI()
    app.include_router(activity.router, prefix="/activity")
    with TestClient(app) as client:
        assert client.get("/activity/current").status_code == 401
        response = client.get("/activity/current", headers={"Authorization": "Bearer fixture-state"})
    assert response.status_code == status
    if detail:
        assert detail in response.json()["detail"]
    else:
        assert response.json()["char_id"] == TEST_CHAR_ID
