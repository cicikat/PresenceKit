"""Client settings reads stay reachable while management writes remain private."""
import re

from fastapi import Depends, FastAPI

from admin.auth import require_scopes
from scripts.build_public_api_allowlist import nginx_map, route_rules


def permitted(app, method, path):
    for verb, pattern, allowed in route_rules(app):
        if verb == method and re.fullmatch(pattern, path):
            return allowed
    return False


def test_client_setting_reads_and_history_are_not_blocked_by_directory_name():
    app = FastAPI()
    app.add_api_route("/settings/prompt-assets", lambda: {}, methods=["GET"],
                      dependencies=[Depends(require_scopes("persona"))])
    app.add_api_route("/settings/prompt-assets", lambda: {}, methods=["PUT"],
                      dependencies=[Depends(require_scopes("admin"))])
    app.add_api_route("/chat-log/{date}", lambda: {}, methods=["GET"],
                      dependencies=[Depends(require_scopes("memory.read"))])
    assert permitted(app, "GET", "/settings/prompt-assets")
    assert permitted(app, "GET", "/chat-log/2026-10-17")
    assert not permitted(app, "PUT", "/settings/prompt-assets")
    assert not permitted(app, "GET", "/")
    assert not permitted(app, "GET", "/openapi.json")


def test_private_static_route_cannot_leak_through_a_broad_client_route():
    app = FastAPI()
    app.add_api_route("/records/internal", lambda: {}, methods=["GET"],
                      dependencies=[Depends(require_scopes("admin"))])
    app.add_api_route("/records/{record_id}", lambda: {}, methods=["GET"],
                      dependencies=[Depends(require_scopes("memory.read"))])
    assert not permitted(app, "GET", "/records/internal")
    assert permitted(app, "GET", "/records/public-client-record")


def test_real_bootstrap_contracts_are_public_but_still_application_authenticated():
    from admin.admin_server import app
    for method, path in [("GET", "/auth/whoami"), ("GET", "/settings/prompt-assets"),
                         ("GET", "/settings/prompt-language"), ("POST", "/v1/sessions"),
                         ("GET", "/chat-log/dates"), ("GET", "/mobile/poll"),
                         ("POST", "/desktop/chat"), ("GET", "/ws/desktop")]:
        assert permitted(app, method, path), (method, path)
    for method, path in [("GET", "/settings/clients"), ("GET", "/auth/tokens"),
                         ("POST", "/auth/tokens"), ("GET", "/observability/api-calls"),
                         ("GET", "/system/data-path"), ("GET", "/")]:
        assert not permitted(app, method, path), (method, path)
    assert 'default 0;' in nginx_map(app)
