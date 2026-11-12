"""
tests/test_sec_auth2_scopes.py — SEC-AUTH-2 P1+P2+P3: scoped token registry + 路由映射 + 管理/审计/限速

覆盖范围：
  - admin.scopes: profile 展开
  - admin.token_registry: 加载 / 热重载 / disabled / expired 过滤 / create+rotate+delete
  - admin.auth.resolve_token: legacy secret 兼容 + registry token 校验
  - admin.auth.require_scopes: 401 (无效/缺失 token) vs 403 (scope 不足) vs 429 (限速)
  - admin.auth.authenticate_ws: scope 参数化
  - guard: 全量 APIRoute default-deny 扫描（新增 router 忘记声明 scope → CI 失败）
  - P2: 真实 admin_server.app 路由映射的 scope 语义（§8 items 2-5）
  - P3: /auth/tokens 管理 API + 限速 429（§8 items 6-7）
"""

import pytest
from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from admin.scopes import expand_scopes, PROFILES
from admin import token_registry
from admin.auth import (
    TokenInfo, resolve_token, require_scopes, verify_token,
    authenticate_ws, extract_ws_token,
)

VALID_SECRET = "sec-auth2-legacy-secret"


@pytest.fixture(autouse=True)
def _reset_registry_cache():
    """token_registry 是 mtime 热重载的模块级单例缓存，测试间必须清零。"""
    token_registry._records = None
    token_registry._mtime = None
    yield
    token_registry._records = None
    token_registry._mtime = None


@pytest.fixture(autouse=True)
def _patch_legacy_secret(monkeypatch):
    monkeypatch.setattr("admin.auth.get_admin_secret", lambda: VALID_SECRET)


def _write_tokens_yaml(sandbox, entries: list[dict]):
    import yaml
    path = sandbox.auth_tokens_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"tokens": entries}), encoding="utf-8")


# ═══════════════════════════════════════════════════════════════════════════════
# admin.scopes: profile 展开
# ═══════════════════════════════════════════════════════════════════════════════

def test_expand_scopes_profile():
    assert expand_scopes(["profile:mobile"]) == PROFILES["mobile"]


def test_expand_scopes_explicit():
    assert expand_scopes(["chat", "state.read"]) == frozenset({"chat", "state.read"})


def test_expand_scopes_mixed():
    out = expand_scopes(["profile:watch", "state.read"])
    assert out == frozenset({"sensor.write", "state.read"})


def test_expand_scopes_unknown_profile_raises():
    with pytest.raises(ValueError):
        expand_scopes(["profile:nonexistent"])


def test_expand_scopes_unknown_scope_raises():
    with pytest.raises(ValueError):
        expand_scopes(["not-a-real-scope"])


# ═══════════════════════════════════════════════════════════════════════════════
# admin.token_registry: 加载 / 热重载 / 过滤
# ═══════════════════════════════════════════════════════════════════════════════

def test_registry_empty_when_file_missing(sandbox):
    assert token_registry.get_records() == []


def test_registry_loads_valid_entries(sandbox):
    raw = "emt_" + "a" * 32
    _write_tokens_yaml(sandbox, [{
        "label": "desktop-main",
        "hash": token_registry.hash_token(raw),
        "scopes": ["profile:desktop"],
        "created_at": "2026-07-03T12:00:00+08:00",
        "expires_at": None,
        "disabled": False,
    }])
    records = token_registry.get_records()
    assert len(records) == 1
    assert records[0].label == "desktop-main"
    assert records[0].scopes == PROFILES["desktop"]


def test_registry_skips_malformed_entry_without_crashing(sandbox):
    _write_tokens_yaml(sandbox, [
        {"label": "bad", "hash": "sha256:x", "scopes": ["not-a-scope"]},
        {"label": "good", "hash": "sha256:y", "scopes": ["chat"]},
    ])
    records = token_registry.get_records()
    assert [r.label for r in records] == ["good"]


def test_registry_hot_reloads_on_mtime_change(sandbox):
    _write_tokens_yaml(sandbox, [{"label": "a", "hash": "sha256:a", "scopes": ["chat"]}])
    first = token_registry.get_records()
    assert len(first) == 1

    import time
    time.sleep(0.01)
    _write_tokens_yaml(sandbox, [
        {"label": "a", "hash": "sha256:a", "scopes": ["chat"]},
        {"label": "b", "hash": "sha256:b", "scopes": ["state.read"]},
    ])
    # force a distinct mtime on filesystems with coarse resolution
    import os
    path = sandbox.auth_tokens_file()
    st = path.stat()
    os.utime(path, (st.st_atime, st.st_mtime + 1))

    second = token_registry.get_records()
    assert len(second) == 2


def test_registry_find_by_hash_skips_disabled(sandbox):
    raw = "emt_disabled"
    h = token_registry.hash_token(raw)
    _write_tokens_yaml(sandbox, [{"label": "d", "hash": h, "scopes": ["chat"], "disabled": True}])
    assert token_registry.find_by_hash(h) is None


def test_registry_find_by_hash_skips_expired(sandbox):
    raw = "emt_expired"
    h = token_registry.hash_token(raw)
    _write_tokens_yaml(sandbox, [{
        "label": "e", "hash": h, "scopes": ["chat"], "expires_at": "2000-01-01T00:00:00",
    }])
    assert token_registry.find_by_hash(h) is None


# ═══════════════════════════════════════════════════════════════════════════════
# admin.auth.resolve_token
# ═══════════════════════════════════════════════════════════════════════════════

def test_resolve_token_legacy_secret_is_admin(sandbox):
    info = resolve_token(VALID_SECRET)
    assert info is not None
    assert info.label == "legacy-admin"
    assert info.scopes == frozenset({"admin"})


def test_resolve_token_registry_token(sandbox):
    raw = "emt_mobile_token"
    _write_tokens_yaml(sandbox, [{
        "label": "mobile-1", "hash": token_registry.hash_token(raw), "scopes": ["profile:mobile"],
    }])
    info = resolve_token(raw)
    assert info is not None
    assert info.label == "mobile-1"
    assert info.scopes == PROFILES["mobile"]


def test_resolve_token_invalid_returns_none(sandbox):
    assert resolve_token("garbage-token") is None


def test_resolve_token_empty_returns_none(sandbox):
    assert resolve_token("") is None


# ═══════════════════════════════════════════════════════════════════════════════
# admin.auth.require_scopes: 401 vs 403
# ═══════════════════════════════════════════════════════════════════════════════

def _build_app():
    app = FastAPI()

    @app.get("/admin-only")
    async def _admin_only(info: TokenInfo = Depends(require_scopes("admin"))):
        return {"label": info.label}

    @app.get("/chat-only")
    async def _chat_only(info: TokenInfo = Depends(require_scopes("chat"))):
        return {"label": info.label}

    return app


@pytest.fixture()
def client():
    return TestClient(_build_app())


def test_missing_credentials_401(client, sandbox):
    resp = client.get("/admin-only")
    assert resp.status_code == 401
    assert "打开密钥本" in resp.json()["detail"]["hint"]


def test_invalid_token_401(client, sandbox):
    resp = client.get("/admin-only", headers={"Authorization": "Bearer nonsense"})
    assert resp.status_code == 401
    assert "打开密钥本" in resp.json()["detail"]["hint"]


def test_insufficient_scope_403(client, sandbox):
    raw = "emt_chat_only"
    _write_tokens_yaml(sandbox, [{"label": "mobile-1", "hash": token_registry.hash_token(raw), "scopes": ["chat"]}])
    resp = client.get("/admin-only", headers={"Authorization": f"Bearer {raw}"})
    assert resp.status_code == 403
    assert "admin" in resp.json()["detail"]


def test_sufficient_scope_passes(client, sandbox):
    raw = "emt_chat_only"
    _write_tokens_yaml(sandbox, [{"label": "mobile-1", "hash": token_registry.hash_token(raw), "scopes": ["chat"]}])
    resp = client.get("/chat-only", headers={"Authorization": f"Bearer {raw}"})
    assert resp.status_code == 200
    assert resp.json()["label"] == "mobile-1"


def test_admin_scope_implies_all(client, sandbox):
    resp = client.get("/chat-only", headers={"Authorization": f"Bearer {VALID_SECRET}"})
    assert resp.status_code == 200
    assert resp.json()["label"] == "legacy-admin"


def test_verify_token_is_require_scopes_admin_alias():
    assert getattr(verify_token, "_required_scopes", None) == ("admin",)


# ═══════════════════════════════════════════════════════════════════════════════
# admin.auth.authenticate_ws: scope 参数化
# ═══════════════════════════════════════════════════════════════════════════════

class _FakeWS:
    def __init__(self, token: str | None):
        self.headers = {"authorization": f"Bearer {token}"} if token else {}


def test_ws_admin_token_satisfies_any_scope(sandbox):
    assert authenticate_ws(_FakeWS(VALID_SECRET), "ws.desktop") is not None
    assert authenticate_ws(_FakeWS(VALID_SECRET), "ws.device") is not None


def test_ws_device_token_rejected_for_desktop_scope(sandbox):
    raw = "emt_device_token"
    _write_tokens_yaml(sandbox, [{"label": "esp32", "hash": token_registry.hash_token(raw), "scopes": ["profile:device"]}])
    assert authenticate_ws(_FakeWS(raw), "ws.device") is not None
    assert authenticate_ws(_FakeWS(raw), "ws.desktop") is None


def test_ws_missing_token_rejected(sandbox):
    assert authenticate_ws(_FakeWS(None), "ws.desktop") is None


# ═══════════════════════════════════════════════════════════════════════════════
# Guard: default-deny 全量路由扫描
# ═══════════════════════════════════════════════════════════════════════════════

_PUBLIC_ALLOWLIST = {
    "/", "/docs", "/openapi.json", "/redoc",
    # These two GETs use short-lived OAuth tickets and a browser-bound state cookie.
    "/settings/gmail/oauth/authorize", "/settings/gmail/oauth/callback",
}


def _dependant_has_scope_marker(dependant) -> bool:
    if hasattr(dependant.call, "_required_scopes"):
        return True
    for sub in getattr(dependant, "dependencies", None) or []:
        if _dependant_has_scope_marker(sub):
            return True
    return False


def test_guard_all_http_routes_declare_scopes():
    from admin.admin_server import app as _app
    from fastapi.routing import APIRoute

    unprotected = []
    for route in _app.routes:
        if not isinstance(route, APIRoute):
            continue
        if route.path in _PUBLIC_ALLOWLIST or route.path.startswith("/static"):
            continue
        if not _dependant_has_scope_marker(route.dependant):
            unprotected.append(route.path)

    assert unprotected == [], f"路由缺少 scope 声明（default-deny 漏洞）: {unprotected}"


# ═══════════════════════════════════════════════════════════════════════════════
# P2: 真实 admin_server.app 路由映射 scope 语义（brief §8 items 2/3/4/5）
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.fixture()
def real_client(sandbox):
    from admin.admin_server import app as real_app
    return TestClient(real_app, raise_server_exceptions=False)


def test_p2_chat_token_denied_on_diary(real_client, sandbox):
    """item 2: chat token 访问 /diary/list → 403"""
    raw = "emt_p2_chat"
    _write_tokens_yaml(sandbox, [{"label": "t-chat", "hash": token_registry.hash_token(raw), "scopes": ["chat"]}])
    resp = real_client.get("/diary/list", headers={"Authorization": f"Bearer {raw}"})
    assert resp.status_code == 403
    assert "chat" not in resp.json()["detail"] or "memory.read" in resp.json()["detail"]


def test_p2_chat_token_passes_desktop_chat_auth_layer(real_client, sandbox):
    """item 2: chat token 访问 /desktop/chat → 过鉴权层（不是 401/403，即便业务层因 pipeline 未初始化而 503）"""
    raw = "emt_p2_chat2"
    _write_tokens_yaml(sandbox, [{"label": "t-chat2", "hash": token_registry.hash_token(raw), "scopes": ["chat"]}])
    resp = real_client.post("/desktop/chat", json={"message": "hi"}, headers={"Authorization": f"Bearer {raw}"})
    assert resp.status_code not in (401, 403)


def test_p2_sensor_write_token_denied_get_realtime(real_client, sandbox):
    """item 2: sensor.write token GET /sensor/realtime → 403（只写不读）"""
    raw = "emt_p2_sensor"
    _write_tokens_yaml(sandbox, [{"label": "t-sensor", "hash": token_registry.hash_token(raw), "scopes": ["sensor.write"]}])
    resp = real_client.get("/sensor/realtime", headers={"Authorization": f"Bearer {raw}"})
    assert resp.status_code == 403


def test_p2_sensor_write_token_passes_post_realtime(real_client, sandbox):
    raw = "emt_p2_sensor2"
    _write_tokens_yaml(sandbox, [{"label": "t-sensor2", "hash": token_registry.hash_token(raw), "scopes": ["sensor.write"]}])
    resp = real_client.post(
        "/sensor/realtime",
        json={
            "window_seconds": 30, "ts": 0, "sensor_version": "1",
            "input": {"keystrokes": 0, "mouse_clicks": 0, "mouse_distance_px": 0, "idle_seconds": 0},
            "focus": {"app": "", "title_hint": "", "switch_count": 0},
        },
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code not in (401, 403)


def test_p2_admin_token_passes_every_scope(real_client):
    """item 2/3: admin token（此处走 legacy secret）全通，任意 scope 端点不被拒"""
    for path in ("/diary/list", "/sensor/realtime", "/mood/state"):
        resp = real_client.get(path, headers={"Authorization": f"Bearer {VALID_SECRET}"})
        assert resp.status_code not in (401, 403), f"{path} 被 admin/legacy token 拒绝"


def test_p2_legacy_secret_hits_arbitrary_endpoint(real_client):
    """item 3: legacy 兼容 — config.admin.secret_key 的值命中任意端点均放行（等价 admin）"""
    resp = real_client.get("/scheduler/status", headers={"Authorization": f"Bearer {VALID_SECRET}"})
    assert resp.status_code not in (401, 403)


def test_p2_bad_token_401_with_no_leak(real_client):
    """item 4: 坏 token → 401，且 token 值不回显"""
    bad = "totally-bogus-token-xyz"
    resp = real_client.get("/diary/list", headers={"Authorization": f"Bearer {bad}"})
    assert resp.status_code == 401
    assert bad not in resp.text


def test_p2_insufficient_scope_403_detail_lists_needed_scope(real_client, sandbox):
    """item 4: scope 不足 → 403 且 detail 含所需 scope"""
    raw = "emt_p2_detail"
    _write_tokens_yaml(sandbox, [{"label": "t-detail", "hash": token_registry.hash_token(raw), "scopes": ["chat"]}])
    resp = real_client.get("/diary/list", headers={"Authorization": f"Bearer {raw}"})
    assert resp.status_code == 403
    assert "memory.read" in resp.json()["detail"]


def test_p2_ws_desktop_token_rejected_on_ws_device(real_client, sandbox):
    """item 5: ws.desktop token 连 /ws/device 被拒（close 1008）"""
    raw = "emt_p2_wsdesktop"
    _write_tokens_yaml(sandbox, [{"label": "t-wsd", "hash": token_registry.hash_token(raw), "scopes": ["ws.desktop"]}])
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with real_client.websocket_connect(
            "/ws/device", headers={"Authorization": f"Bearer {raw}"}
        ) as ws:
            ws.receive_text()
    assert exc_info.value.code == 1008


def test_p2_ws_device_token_rejected_on_ws_desktop(real_client, sandbox):
    """item 5: ws.device token 连 /ws/desktop 被拒（反之亦然）"""
    raw = "emt_p2_wsdevice"
    _write_tokens_yaml(sandbox, [{"label": "t-wsdev", "hash": token_registry.hash_token(raw), "scopes": ["ws.device"]}])
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with real_client.websocket_connect(
            "/ws/desktop", headers={"Authorization": f"Bearer {raw}"}
        ) as ws:
            ws.receive_text()
    assert exc_info.value.code == 1008


# ═══════════════════════════════════════════════════════════════════════════════
# admin.token_registry: create_token / rotate_token / delete_token (P3)
# ═══════════════════════════════════════════════════════════════════════════════

def test_create_token_returns_usable_plaintext(sandbox):
    raw = token_registry.create_token("t1", scopes=["profile:mobile"])
    assert raw.startswith("emt_")
    info = resolve_token(raw)
    assert info is not None
    assert info.label == "t1"
    assert info.scopes == PROFILES["mobile"]


def test_create_token_rejects_bad_label(sandbox):
    with pytest.raises(token_registry.TokenLabelError):
        token_registry.create_token("Not Valid!", scopes=["chat"])


def test_create_token_rejects_reserved_label(sandbox):
    with pytest.raises(token_registry.TokenLabelError):
        token_registry.create_token(token_registry.RESERVED_LABEL, scopes=["chat"])


def test_create_token_rejects_duplicate_label(sandbox):
    token_registry.create_token("dup", scopes=["chat"])
    with pytest.raises(token_registry.TokenLabelError):
        token_registry.create_token("dup", scopes=["chat"])


def test_create_token_rejects_unknown_scope(sandbox):
    with pytest.raises(ValueError):
        token_registry.create_token("t2", scopes=["not-a-scope"])


def test_rotate_token_invalidates_old_value(sandbox):
    old = token_registry.create_token("t3", scopes=["chat"])
    new = token_registry.rotate_token("t3")
    assert new != old
    assert resolve_token(old) is None
    info = resolve_token(new)
    assert info is not None
    assert info.label == "t3"
    assert info.scopes == frozenset({"chat"})


def test_rotate_token_missing_label_raises_keyerror(sandbox):
    with pytest.raises(KeyError):
        token_registry.rotate_token("does-not-exist")


def test_rotate_token_rejects_legacy_admin(sandbox):
    with pytest.raises(token_registry.TokenLabelError):
        token_registry.rotate_token(token_registry.RESERVED_LABEL)


def test_delete_token_removes_it(sandbox):
    raw = token_registry.create_token("t4", scopes=["chat"])
    assert resolve_token(raw) is not None
    assert token_registry.delete_token("t4") is True
    assert resolve_token(raw) is None


def test_delete_token_missing_label_returns_false(sandbox):
    assert token_registry.delete_token("does-not-exist") is False


def test_delete_token_rejects_legacy_admin(sandbox):
    with pytest.raises(token_registry.TokenLabelError):
        token_registry.delete_token(token_registry.RESERVED_LABEL)


# ═══════════════════════════════════════════════════════════════════════════════
# P3: /auth/tokens 管理 API（brief §8 item 7）
# ═══════════════════════════════════════════════════════════════════════════════

def _admin_headers():
    return {"Authorization": f"Bearer {VALID_SECRET}"}


def test_p3_auth_tokens_requires_admin_scope(real_client, sandbox):
    raw = "emt_p3_notadmin"
    _write_tokens_yaml(sandbox, [{"label": "na", "hash": token_registry.hash_token(raw), "scopes": ["chat"]}])
    resp = real_client.get("/auth/tokens", headers={"Authorization": f"Bearer {raw}"})
    assert resp.status_code == 403


def test_p3_list_tokens_no_plaintext(real_client, sandbox):
    token_registry.create_token("listed", scopes=["chat"])
    resp = real_client.get("/auth/tokens", headers=_admin_headers())
    assert resp.status_code == 200
    data = resp.json()["tokens"]
    assert any(t["label"] == "listed" for t in data)
    body_text = resp.text
    # no hash and no plaintext leak — only hash_prefix (8 hex chars) is exposed
    assert "hash_prefix" in body_text
    assert '"hash":' not in body_text


def test_p3_list_tokens_includes_created_at(real_client, sandbox):
    """管理面板 Token 页 Created 列的数据源。"""
    token_registry.create_token("with-created-at", scopes=["chat"])
    resp = real_client.get("/auth/tokens", headers=_admin_headers())
    entry = next(t for t in resp.json()["tokens"] if t["label"] == "with-created-at")
    assert entry["created_at"]


def test_p3_full_lifecycle_create_use_rotate_delete(real_client, sandbox):
    """item 7: 创建→用新 token 访问对应 scope 端点→rotate 后旧值失效→delete 后 401"""
    create_resp = real_client.post(
        "/auth/tokens",
        json={"label": "lifecycle", "profile": "mobile"},
        headers=_admin_headers(),
    )
    assert create_resp.status_code == 200
    token_v1 = create_resp.json()["token"]

    # new token can access a scope in its profile (mobile → chat, state.read, ...)
    resp = real_client.get("/mood/state", headers={"Authorization": f"Bearer {token_v1}"})
    assert resp.status_code not in (401, 403)

    rotate_resp = real_client.post(
        "/auth/tokens/lifecycle/rotate", headers=_admin_headers(),
    )
    assert rotate_resp.status_code == 200
    token_v2 = rotate_resp.json()["token"]
    assert token_v2 != token_v1

    # old value now invalid
    resp = real_client.get("/mood/state", headers={"Authorization": f"Bearer {token_v1}"})
    assert resp.status_code == 401
    # new value works
    resp = real_client.get("/mood/state", headers={"Authorization": f"Bearer {token_v2}"})
    assert resp.status_code not in (401, 403)

    delete_resp = real_client.delete("/auth/tokens/lifecycle", headers=_admin_headers())
    assert delete_resp.status_code == 200

    resp = real_client.get("/mood/state", headers={"Authorization": f"Bearer {token_v2}"})
    assert resp.status_code == 401


def test_p3_create_token_rejects_reserved_label(real_client, sandbox):
    resp = real_client.post(
        "/auth/tokens",
        json={"label": "legacy-admin", "profile": "panel"},
        headers=_admin_headers(),
    )
    assert resp.status_code == 422


def test_p3_delete_token_missing_returns_404(real_client, sandbox):
    resp = real_client.delete("/auth/tokens/does-not-exist", headers=_admin_headers())
    assert resp.status_code == 404


def test_p3_rotate_token_missing_returns_404(real_client, sandbox):
    resp = real_client.post("/auth/tokens/does-not-exist/rotate", headers=_admin_headers())
    assert resp.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════════
# Brief 22 §1: whoami / profiles / disable-enable
# ═══════════════════════════════════════════════════════════════════════════════

def test_whoami_works_with_non_admin_token(real_client, sandbox):
    raw = "emt_whoami_chat"
    _write_tokens_yaml(sandbox, [{"label": "t-whoami", "hash": token_registry.hash_token(raw), "scopes": ["chat"]}])
    resp = real_client.get("/auth/whoami", headers={"Authorization": f"Bearer {raw}"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["label"] == "t-whoami"
    assert body["scopes"] == ["chat"]


def test_whoami_admin_token(real_client):
    resp = real_client.get("/auth/whoami", headers=_admin_headers())
    assert resp.status_code == 200
    assert resp.json()["label"] == "legacy-admin"


def test_whoami_requires_valid_token(real_client, sandbox):
    resp = real_client.get("/auth/whoami", headers={"Authorization": "Bearer bogus"})
    assert resp.status_code == 401


def test_whoami_missing_credentials_401(real_client, sandbox):
    resp = real_client.get("/auth/whoami")
    assert resp.status_code == 401


def test_list_profiles_returns_constant_table(real_client, sandbox):
    resp = real_client.get("/auth/profiles", headers=_admin_headers())
    assert resp.status_code == 200
    profiles = resp.json()["profiles"]
    assert profiles["mobile"] == sorted(PROFILES["mobile"])


def test_list_profiles_requires_admin_scope(real_client, sandbox):
    raw = "emt_profiles_notadmin"
    _write_tokens_yaml(sandbox, [{"label": "na2", "hash": token_registry.hash_token(raw), "scopes": ["chat"]}])
    resp = real_client.get("/auth/profiles", headers={"Authorization": f"Bearer {raw}"})
    assert resp.status_code == 403


def test_patch_disable_token_immediately_401s(real_client, sandbox):
    """§1.4 守卫：disabled token → 401（PATCH 生效之后立即失效）"""
    create_resp = real_client.post(
        "/auth/tokens", json={"label": "toggle-me", "profile": "mobile"}, headers=_admin_headers(),
    )
    raw = create_resp.json()["token"]
    resp = real_client.get("/mood/state", headers={"Authorization": f"Bearer {raw}"})
    assert resp.status_code not in (401, 403)

    patch_resp = real_client.patch(
        "/auth/tokens/toggle-me", json={"disabled": True}, headers=_admin_headers(),
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json() == {"label": "toggle-me", "disabled": True}

    resp = real_client.get("/mood/state", headers={"Authorization": f"Bearer {raw}"})
    assert resp.status_code == 401


def test_patch_enable_restores_access(real_client, sandbox):
    create_resp = real_client.post(
        "/auth/tokens", json={"label": "toggle-back", "profile": "mobile"}, headers=_admin_headers(),
    )
    raw = create_resp.json()["token"]
    real_client.patch("/auth/tokens/toggle-back", json={"disabled": True}, headers=_admin_headers())
    assert real_client.get("/mood/state", headers={"Authorization": f"Bearer {raw}"}).status_code == 401

    patch_resp = real_client.patch(
        "/auth/tokens/toggle-back", json={"disabled": False}, headers=_admin_headers(),
    )
    assert patch_resp.status_code == 200
    resp = real_client.get("/mood/state", headers={"Authorization": f"Bearer {raw}"})
    assert resp.status_code not in (401, 403)


def test_patch_rejects_legacy_admin_reserved_label(real_client, sandbox):
    resp = real_client.patch(
        "/auth/tokens/legacy-admin", json={"disabled": True}, headers=_admin_headers(),
    )
    assert resp.status_code == 422


def test_patch_missing_label_returns_404(real_client, sandbox):
    resp = real_client.patch(
        "/auth/tokens/does-not-exist", json={"disabled": True}, headers=_admin_headers(),
    )
    assert resp.status_code == 404


def test_patch_requires_admin_scope(real_client, sandbox):
    raw = "emt_patch_notadmin"
    _write_tokens_yaml(sandbox, [{"label": "np", "hash": token_registry.hash_token(raw), "scopes": ["chat"]}])
    resp = real_client.patch(
        "/auth/tokens/np", json={"disabled": True}, headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 403


# ═══════════════════════════════════════════════════════════════════════════════
# P3: 限速（brief §8 item 6）
# ═══════════════════════════════════════════════════════════════════════════════

def test_p3_rate_limit_blocks_after_threshold(real_client, sandbox):
    """item 6: 同 IP 11 次坏 token 后 → 429"""
    for _ in range(10):
        resp = real_client.get("/diary/list", headers={"Authorization": "Bearer bad-token"})
        assert resp.status_code == 401
    resp = real_client.get("/diary/list", headers={"Authorization": "Bearer bad-token"})
    assert resp.status_code == 429


def test_p3_rate_limit_does_not_block_valid_requests_before_threshold(real_client, sandbox):
    for _ in range(5):
        resp = real_client.get("/diary/list", headers={"Authorization": "Bearer bad-token"})
        assert resp.status_code == 401
    resp = real_client.get("/mood/state", headers=_admin_headers())
    assert resp.status_code not in (401, 403, 429)


def test_p3_rate_limit_403_does_not_count_toward_401_threshold(real_client, sandbox):
    """scope_denied (403) 不应计入限速窗口——限速只针对认证失败（401），不针对权限不足。"""
    raw = "emt_p3_ratelimit_scope"
    _write_tokens_yaml(sandbox, [{"label": "rl", "hash": token_registry.hash_token(raw), "scopes": ["chat"]}])
    for _ in range(15):
        resp = real_client.get("/diary/list", headers={"Authorization": f"Bearer {raw}"})
        assert resp.status_code == 403
