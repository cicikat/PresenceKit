"""
tests/test_activity_contract.py

Activity Contract Smoke Tests

每当 registry 声明与实际代码/前端漂移时，这组测试会报错。

覆盖：
 A. FastAPI 路由存在性
    - 每个 activity 的 start / state / close 路由存在
    - has_companion_chat=True 的 activity 有 /chat 路由
    - GET /activity/list 路由存在
 B. /activity/list 端点内容
    - 返回 enabled activities
    - id / frontend_key / route_prefix 与 registry 一致
    - memory_policy 字段存在
 C. Tauri lib.rs contract
    - registry.tauri_commands 里每个名称在 lib.rs 有对应 async fn 声明
 D. activity-api.ts contract
    - 每个 tauri command 名称出现在 activity-api.ts 中

注意：
 - C / D 需要前端仓库 ../Emerald-client 可访问；若不存在则 skip。
 - FastAPI 路由检查通过导入个别 router 对象完成，无需启动服务器。
 - /activity/list 内容检查通过 FastAPI TestClient + dependency override 完成。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.activity.registry import (
    ACTIVITY_REGISTRY,
    get_activity_meta,
    list_enabled_activities,
)

# ── 路径常量 ──────────────────────────────────────────────────────────────────

_PROJECT_ROOT = Path(__file__).parent.parent
_FRONTEND_ROOT = _PROJECT_ROOT.parent / "Emerald-client"
_LIB_RS = _FRONTEND_ROOT / "src-tauri" / "src" / "lib.rs"
_ACTIVITY_API_TS = _FRONTEND_ROOT / "src" / "shared" / "api" / "activity-api.ts"


# ═══════════════════════════════════════════════════════════════════════════════
# A. FastAPI 路由存在性
# ═══════════════════════════════════════════════════════════════════════════════

def _router_route_set(router):
    """Return set of (METHOD, path) from an APIRouter object."""
    result: set[tuple[str, str]] = set()
    for route in router.routes:
        if hasattr(route, "methods") and hasattr(route, "path"):
            for method in route.methods:
                result.add((method.upper(), route.path))
    return result


@pytest.fixture(scope="module")
def reading_routes():
    from admin.routers.reading import router
    return _router_route_set(router)


@pytest.fixture(scope="module")
def gomoku_routes():
    from admin.routers.gomoku import router
    return _router_route_set(router)


@pytest.fixture(scope="module")
def chess_routes():
    from admin.routers.chess import router
    return _router_route_set(router)


@pytest.fixture(scope="module")
def dream_seed_routes():
    from admin.routers.dream_seed import router
    return _router_route_set(router)


@pytest.fixture(scope="module")
def activity_routes():
    from admin.routers.activity import router
    return _router_route_set(router)


# reading ── start / state / close
def test_reading_start_route(reading_routes):
    assert ("POST", "/reading/start") in reading_routes


def test_reading_state_route(reading_routes):
    assert ("GET", "/reading/state") in reading_routes


def test_reading_close_route(reading_routes):
    assert ("POST", "/reading/close") in reading_routes


# gomoku ── start / state / close / chat (has_companion_chat=True)
def test_gomoku_start_route(gomoku_routes):
    assert ("POST", "/gomoku/start") in gomoku_routes


def test_gomoku_state_route(gomoku_routes):
    assert ("GET", "/gomoku/state") in gomoku_routes


def test_gomoku_close_route(gomoku_routes):
    assert ("POST", "/gomoku/close") in gomoku_routes


def test_gomoku_chat_route_because_has_companion_chat(gomoku_routes):
    meta = get_activity_meta("gomoku")
    assert meta.has_companion_chat is True
    assert ("POST", "/gomoku/chat") in gomoku_routes


# chess ── start / state / close
def test_chess_start_route(chess_routes):
    assert ("POST", "/chess/start") in chess_routes


def test_chess_state_route(chess_routes):
    assert ("GET", "/chess/state") in chess_routes


def test_chess_close_route(chess_routes):
    assert ("POST", "/chess/close") in chess_routes


def test_dream_seed_routes(dream_seed_routes):
    assert ("POST", "/dream_seed/start") in dream_seed_routes
    assert ("GET", "/dream_seed/state") in dream_seed_routes
    assert ("POST", "/dream_seed/chat") in dream_seed_routes
    assert ("POST", "/dream_seed/close") in dream_seed_routes


# /activity/list
def test_activity_list_route_registered(activity_routes):
    assert ("GET", "/list") in activity_routes


# ── 所有 has_companion_chat=True activity 都有 /chat 路由 ─────────────────────
# 这个测试会在新增带 companion 的 activity 时自动报错（如果忘记加路由）

def test_companion_chat_activities_have_chat_route(gomoku_routes, dream_seed_routes):
    for meta in ACTIVITY_REGISTRY:
        if not meta.has_companion_chat:
            continue
        if meta.id == "gomoku":
            assert ("POST", "/gomoku/chat") in gomoku_routes, (
                f"{meta.id} has_companion_chat=True but /chat route missing"
            )
        elif meta.id == "dream_seed":
            assert ("POST", "/dream_seed/chat") in dream_seed_routes, (
                f"{meta.id} has_companion_chat=True but /chat route missing"
            )


# ═══════════════════════════════════════════════════════════════════════════════
# B. /activity/list 端点内容
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.fixture(scope="module")
def activity_list_client():
    from admin.routers.activity import router

    app = FastAPI()
    app.include_router(router, prefix="/activity")
    for route in router.routes:
        for dep in route.dependant.dependencies:
            if hasattr(dep.call, "_required_scopes"):
                app.dependency_overrides[dep.call] = lambda: "test"
    return TestClient(app)


def test_activity_list_status_200(activity_list_client):
    resp = activity_list_client.get("/activity/list")
    assert resp.status_code == 200


def test_activity_list_returns_list(activity_list_client):
    data = activity_list_client.get("/activity/list").json()
    assert isinstance(data, list)


def test_activity_list_contains_enabled_ids(activity_list_client):
    data = activity_list_client.get("/activity/list").json()
    returned_ids = {item["id"] for item in data}
    expected_ids = {m.id for m in list_enabled_activities()}
    assert returned_ids == expected_ids


def test_activity_list_frontend_key_matches_registry(activity_list_client):
    data = activity_list_client.get("/activity/list").json()
    for item in data:
        meta = get_activity_meta(item["id"])
        assert meta is not None
        assert item["frontend_key"] == meta.frontend_key


def test_activity_list_route_prefix_matches_registry(activity_list_client):
    data = activity_list_client.get("/activity/list").json()
    for item in data:
        meta = get_activity_meta(item["id"])
        assert item["route_prefix"] == meta.route_prefix


def test_activity_list_memory_policy_field_present(activity_list_client):
    data = activity_list_client.get("/activity/list").json()
    for item in data:
        assert "memory_policy" in item
        policy = item["memory_policy"]
        assert "transcript" in policy
        assert "summary_threshold" in policy
        assert "main_memory" in policy


def test_activity_list_gomoku_summary_threshold_12(activity_list_client):
    data = activity_list_client.get("/activity/list").json()
    gomoku = next(item for item in data if item["id"] == "gomoku")
    assert gomoku["memory_policy"]["summary_threshold"] == 12


def test_activity_list_reading_summary_threshold_2(activity_list_client):
    data = activity_list_client.get("/activity/list").json()
    reading = next(item for item in data if item["id"] == "reading")
    assert reading["memory_policy"]["summary_threshold"] == 2


def test_activity_list_does_not_expose_session_store(activity_list_client):
    # session_store は内部 Python モジュール名 — 公開 API に含まれてはいけない
    data = activity_list_client.get("/activity/list").json()
    for item in data:
        assert "session_store" not in item


def test_activity_list_does_not_expose_tauri_commands(activity_list_client):
    # tauri_commands は Rust 実装詳細 — 公開 API に含まれてはいけない
    data = activity_list_client.get("/activity/list").json()
    for item in data:
        assert "tauri_commands" not in item


# ═══════════════════════════════════════════════════════════════════════════════
# C. Tauri lib.rs contract
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.fixture(scope="module")
def lib_rs_text():
    if not _LIB_RS.exists():
        pytest.skip(f"Tauri lib.rs not found: {_LIB_RS}")
    return _LIB_RS.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "command",
    [cmd for m in ACTIVITY_REGISTRY for cmd in m.tauri_commands],
    ids=[cmd for m in ACTIVITY_REGISTRY for cmd in m.tauri_commands],
)
def test_tauri_command_fn_exists_in_lib_rs(command, lib_rs_text):
    # regex excludes comment-only matches (e.g. "// async fn foo(") that string-in
    # would accept; also tolerates whitespace variations between tokens
    pattern = rf"async\s+fn\s+{re.escape(command)}\s*\("
    assert re.search(pattern, lib_rs_text), (
        f"Tauri command not declared in lib.rs: async fn {command}("
    )


# ═══════════════════════════════════════════════════════════════════════════════
# D. activity-api.ts contract
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.fixture(scope="module")
def activity_api_ts_text():
    if not _ACTIVITY_API_TS.exists():
        pytest.skip(f"activity-api.ts not found: {_ACTIVITY_API_TS}")
    return _ACTIVITY_API_TS.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "command",
    [cmd for m in ACTIVITY_REGISTRY for cmd in m.tauri_commands],
    ids=[cmd for m in ACTIVITY_REGISTRY for cmd in m.tauri_commands],
)
def test_tauri_command_name_in_activity_api_ts(command, activity_api_ts_text):
    assert f"'{command}'" in activity_api_ts_text, (
        f"Tauri command string not found in activity-api.ts: '{command}'"
    )


@pytest.mark.parametrize("activity_id", ["reading", "gomoku", "chess", "dream_seed"])
def test_tauri_command_prefix_in_activity_api_ts(activity_id, activity_api_ts_text):
    meta = get_activity_meta(activity_id)
    assert meta.tauri_command_prefix in activity_api_ts_text, (
        f"Command prefix not found in activity-api.ts: {meta.tauri_command_prefix!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# E. registry route_prefix → real FastAPI route cross-check
#
# For each activity, mount its router at the mount prefix derived from
# route_prefix (e.g. "/activity/gomoku" → mount at "/activity"), then verify
# that the full paths route_prefix+"/start" etc. actually exist in the app.
#
# This catches route_prefix drift that naming-convention tests miss — e.g.
# route_prefix="/activity/gomoku-v2" still contains "/gomoku" so the naming
# test passes, but no real route matches /activity/gomoku-v2/start.
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.fixture(scope="module")
def all_activity_route_sets():
    """
    Build one FastAPI app per activity by importing admin.routers.{id} and
    mounting it at the prefix derived from registry route_prefix.  Returns a
    dict mapping activity_id → set of (METHOD, full_path) tuples.
    """
    from importlib import import_module

    result: dict[str, set[tuple[str, str]]] = {}
    for meta in ACTIVITY_REGISTRY:
        router = import_module(f"admin.routers.{meta.id}").router
        # "/activity/gomoku" -> mount prefix "/activity"
        mount_prefix = meta.route_prefix.rsplit("/", 1)[0]
        app = FastAPI()
        app.include_router(router, prefix=mount_prefix)
        result[meta.id] = {
            (method.upper(), route.path)
            for route in app.routes
            if hasattr(route, "methods") and hasattr(route, "path")
            for method in route.methods
        }
    return result


# Build parametrize cases at collection time from the live registry.
_REGISTRY_ROUTE_CASES: list[tuple] = []
for _m in ACTIVITY_REGISTRY:
    for _verb, _ep in [("POST", "/start"), ("GET", "/state"), ("POST", "/close")]:
        _REGISTRY_ROUTE_CASES.append((_m, _verb, _ep))
    if _m.has_companion_chat:
        _REGISTRY_ROUTE_CASES.append((_m, "POST", "/chat"))


@pytest.mark.parametrize(
    "meta,verb,endpoint",
    _REGISTRY_ROUTE_CASES,
    ids=[f"{c[0].id}-{c[2].lstrip('/')}" for c in _REGISTRY_ROUTE_CASES],
)
def test_registry_route_prefix_maps_to_real_route(meta, verb, endpoint, all_activity_route_sets):
    """route_prefix in registry must correspond to an actual FastAPI route path."""
    routes = all_activity_route_sets[meta.id]
    full_path = meta.route_prefix + endpoint
    assert (verb, full_path) in routes, (
        f"{meta.id}: expected {verb} {full_path!r} in app routes. "
        f"Registry route_prefix={meta.route_prefix!r} is out of sync with the router."
    )
