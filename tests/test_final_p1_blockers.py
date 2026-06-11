"""Final P1 blocker contracts: HTTP Bearer-only auth and Watch unified gating."""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from admin.routers.sensor import router as sensor_router
from admin.routers.watch import router as watch_router


VALID_TOKEN = "final-p1-valid-token"
WRONG_TOKEN = "final-p1-wrong-token"


@pytest.fixture(autouse=True)
def _admin_secret(monkeypatch):
    monkeypatch.setattr("admin.auth.get_admin_secret", lambda: VALID_TOKEN)


@pytest.fixture
def auth_app():
    app = FastAPI()
    app.include_router(sensor_router)
    app.include_router(watch_router)
    return app


@pytest.fixture
def activity_paths(tmp_path):
    paths = MagicMock()
    active = tmp_path / "active_prompt_assets.json"
    active.write_text('{"active_character":"yexuan"}', encoding="utf-8")
    snapshot = tmp_path / "activity_snapshot.json"
    paths.active_prompt_assets.return_value = active
    paths.activity_snapshot.return_value = snapshot
    paths._p.return_value = tmp_path / "legacy_activity_snapshot.json"
    return paths, snapshot


class TestSensorActivityBearerOnly:
    def test_no_token_rejected_without_side_effect(
        self, auth_app, activity_paths, monkeypatch
    ):
        import admin.routers.sensor as sensor

        paths, snapshot = activity_paths
        monkeypatch.setattr(sensor, "get_paths", lambda: paths)
        response = TestClient(auth_app).post("/sensor/activity", json={"app": "x"})

        assert response.status_code in (401, 403)
        assert not snapshot.exists()
        paths.activity_snapshot.assert_not_called()

    def test_wrong_token_rejected_without_side_effect(
        self, auth_app, activity_paths, monkeypatch
    ):
        import admin.routers.sensor as sensor

        paths, snapshot = activity_paths
        monkeypatch.setattr(sensor, "get_paths", lambda: paths)
        response = TestClient(auth_app).post(
            "/sensor/activity",
            json={"app": "x"},
            headers={"Authorization": f"Bearer {WRONG_TOKEN}"},
        )

        assert response.status_code in (401, 403)
        assert not snapshot.exists()
        paths.activity_snapshot.assert_not_called()

    def test_correct_bearer_preserves_write_behavior(
        self, auth_app, activity_paths, monkeypatch
    ):
        import admin.routers.sensor as sensor

        paths, snapshot = activity_paths
        monkeypatch.setattr(sensor, "get_paths", lambda: paths)
        monkeypatch.setattr(sensor, "_TRANSITION_CHARACTER_INNER", False)
        response = TestClient(auth_app).post(
            "/sensor/activity",
            json={"app": "editor"},
            headers={"Authorization": f"Bearer {VALID_TOKEN}"},
        )

        assert response.status_code == 200
        assert response.json()["status"] == "ok"
        assert snapshot.exists()
        assert "editor" in snapshot.read_text(encoding="utf-8")

    def test_wrong_token_not_in_response_or_logs(
        self, auth_app, activity_paths, monkeypatch, caplog
    ):
        import admin.routers.sensor as sensor

        paths, _ = activity_paths
        monkeypatch.setattr(sensor, "get_paths", lambda: paths)
        caplog.set_level(logging.DEBUG)
        response = TestClient(auth_app).post(
            "/sensor/activity",
            json={"app": "x"},
            headers={"Authorization": f"Bearer {WRONG_TOKEN}"},
        )

        assert WRONG_TOKEN not in response.text
        assert all(WRONG_TOKEN not in record.getMessage() for record in caplog.records)


class TestWatchEventBearerOnly:
    def test_no_token_rejected(self, auth_app):
        response = TestClient(auth_app).post(
            "/watch/event", json={"type": "heart_rate", "value": 90}
        )
        assert response.status_code in (401, 403)

    def test_wrong_bearer_rejected(self, auth_app):
        response = TestClient(auth_app).post(
            "/watch/event",
            json={"type": "heart_rate", "value": 90},
            headers={"Authorization": f"Bearer {WRONG_TOKEN}"},
        )
        assert response.status_code in (401, 403)

    def test_correct_bearer_accepted(self, auth_app, monkeypatch):
        import admin.routers.watch as watch_router_module
        import core.scheduler as scheduler

        monkeypatch.setattr(
            watch_router_module, "_append_heart_rate_event", lambda *a, **kw: None
        )
        on_event = AsyncMock()
        monkeypatch.setattr(scheduler, "on_watch_event", on_event)

        response = TestClient(auth_app).post(
            "/watch/event",
            json={"type": "heart_rate", "value": 90},
            headers={"Authorization": f"Bearer {VALID_TOKEN}"},
        )
        assert response.status_code == 200

    def test_correct_query_secret_is_rejected(self, auth_app, monkeypatch):
        monkeypatch.setattr(
            "core.config_loader.get_config",
            lambda: {
                "admin": {"secret_key": VALID_TOKEN},
                "scheduler": {"watch_secret": "correct-query-secret"},
            },
        )
        response = TestClient(auth_app).post(
            "/watch/event?secret=correct-query-secret",
            json={"type": "heart_rate", "value": 90},
        )
        assert response.status_code in (401, 403)

    def test_openapi_has_no_secret_query_parameter(self, auth_app):
        parameters = (
            auth_app.openapi()["paths"]["/watch/event"]["post"].get("parameters", [])
        )
        assert not [
            p for p in parameters
            if p.get("in") == "query" and p.get("name", "").lower() == "secret"
        ]

    def test_secret_not_in_response_or_logs(self, auth_app, caplog):
        secret = "query-secret-must-not-leak"
        caplog.set_level(logging.DEBUG, logger="admin.routers.watch")
        response = TestClient(auth_app).post(
            f"/watch/event?secret={secret}",
            json={"type": "heart_rate", "value": 90},
        )
        assert secret not in response.text
        assert all(
            secret not in record.getMessage()
            for record in caplog.records
            if record.name == "admin.routers.watch"
        )


def _patch_watch_decision_env(
    monkeypatch,
    *,
    user_active: bool,
    dnd_active: bool,
    state,
):
    monkeypatch.setattr("core.scheduler.gating.get_current_state", lambda uid: state)
    monkeypatch.setattr(
        "core.scheduler.loop._user_active_recently", lambda *a, **kw: user_active
    )
    monkeypatch.setattr(
        "core.scheduler.triggers.dnd.is_dnd", lambda uid: dnd_active
    )
    monkeypatch.setattr("core.scheduler.gating.is_trigger_ready", lambda name: True)


class TestWatchUnifiedGating:
    @pytest.fixture(autouse=True)
    def _watch_setup(self, monkeypatch):
        from core.scheduler.defer_queue import clear_all
        from core.scheduler.triggers import watch

        clear_all()
        monkeypatch.setattr(watch, "WATCH_EXECUTE_MODE", "live")
        monkeypatch.setattr(watch, "_cfg", lambda: {"enabled": True})
        monkeypatch.setattr(watch, "_owner_id", lambda: "u1")

    @pytest.mark.asyncio
    async def test_hr_high_dnd_blocked_without_send_or_mark(self, monkeypatch):
        from core.scheduler import loop
        from core.scheduler.state_machine import TriggerState
        from core.scheduler.triggers import watch

        _patch_watch_decision_env(
            monkeypatch,
            user_active=False,
            dnd_active=True,
            state=TriggerState.QUIET,
        )
        send = AsyncMock(return_value="reply")
        marks = []
        monkeypatch.setattr(loop, "_pipeline_send", send)
        monkeypatch.setattr(loop, "_mark", lambda name: marks.append(name))

        await watch.on_watch_event("heart_rate", {"value": 110})

        send.assert_not_called()
        assert marks == []

    @pytest.mark.asyncio
    async def test_hr_high_active_window_defers_without_send_or_mark(self, monkeypatch):
        from core.scheduler import loop
        from core.scheduler.defer_queue import get_queue_snapshot
        from core.scheduler.state_machine import TriggerState
        from core.scheduler.triggers import watch

        _patch_watch_decision_env(
            monkeypatch,
            user_active=True,
            dnd_active=False,
            state=TriggerState.QUIET,
        )
        send = AsyncMock(return_value="reply")
        marks = []
        monkeypatch.setattr(loop, "_pipeline_send", send)
        monkeypatch.setattr(loop, "_mark", lambda name: marks.append(name))

        await watch.on_watch_event("heart_rate", {"value": 110})

        send.assert_not_called()
        assert marks == []
        assert [x["trigger_name"] for x in get_queue_snapshot("u1")] == ["hr_high"]

    @pytest.mark.asyncio
    async def test_hr_critical_emergency_exempt_passes(self, monkeypatch):
        from core.scheduler import loop
        from core.scheduler.state_machine import TriggerState
        from core.scheduler.triggers import watch

        _patch_watch_decision_env(
            monkeypatch,
            user_active=True,
            dnd_active=True,
            state=TriggerState.CHATTING,
        )
        send = AsyncMock(return_value="reply")
        marks = []
        monkeypatch.setattr(loop, "_pipeline_send", send)
        monkeypatch.setattr(loop, "_mark", lambda name: marks.append(name))

        await watch.on_watch_event("heart_rate", {"value": 130})

        send.assert_awaited_once()
        assert marks == ["hr_critical"]

    @pytest.mark.asyncio
    async def test_sleep_end_state_filter_blocks(self, monkeypatch):
        from core.scheduler import loop
        from core.scheduler.state_machine import TriggerState
        from core.scheduler.triggers import watch

        _patch_watch_decision_env(
            monkeypatch,
            user_active=False,
            dnd_active=False,
            state=TriggerState.CHATTING,
        )
        send = AsyncMock(return_value="reply")
        marks = []
        monkeypatch.setattr(loop, "_pipeline_send", send)
        monkeypatch.setattr(loop, "_mark", lambda name: marks.append(name))

        await watch.on_watch_event("sleep_end", {"duration_minutes": 420})

        send.assert_not_called()
        assert marks == []

    def test_no_direct_live_execute_bypass(self):
        source = Path("core/scheduler/triggers/watch.py").read_text(encoding="utf-8")
        assert "_execute_watch_event(proposal, dry_run=False)" not in source
        assert "decide_and_execute_event" in source

    def test_tick_no_longer_excludes_watch_speaking_proposals(self):
        source = Path("core/scheduler/gating.py").read_text(encoding="utf-8")
        assert "WATCH_EVENT_DRIVEN_TRIGGERS" not in source

    def test_non_speaking_watch_snapshot_logic_remains(self):
        from core.scheduler.triggers import watch

        watch._remember_heart_rate(80, 14)
        assert watch.get_last_heart_rate_event()["value"] == 80


class TestFullRouteAuthInventory:
    PUBLIC_HTTP_ALLOWLIST = {
        "/": "Static admin UI entry; it performs no management operation.",
    }

    def test_all_http_management_routes_require_bearer(self):
        from admin.admin_server import app
        from admin.auth import verify_token

        violations = []
        for route in app.routes:
            if not isinstance(route, APIRoute):
                continue
            if route.path in self.PUBLIC_HTTP_ALLOWLIST:
                continue
            dependency_calls = {dep.call for dep in route.dependant.dependencies}
            if verify_token not in dependency_calls:
                violations.append(f"{sorted(route.methods)} {route.path}")

        assert not violations, (
            "HTTP management routes without verify_token dependency:\n"
            + "\n".join(violations)
        )

    def test_high_risk_routes_present_and_bearer_protected(self):
        from admin.admin_server import app
        from admin.auth import verify_token

        required = {
            "/desktop/chat",
            "/desktop/activate",
            "/desktop/deactivate",
            "/desktop/wake",
            "/upload/ingest",
            "/dream/enter",
            "/dream/chat",
            "/dream/exit",
            "/dream/state",
            "/dream/settings",
            "/agent/think",
            "/sensor/activity",
            "/watch/event",
            "/system/data-path",
        }
        routes = {
            route.path: route
            for route in app.routes
            if isinstance(route, APIRoute)
        }
        assert not (required - routes.keys())
        for path in required:
            assert verify_token in {
                dep.call for dep in routes[path].dependant.dependencies
            }, path

    def test_openapi_has_no_sensitive_query_auth_parameters(self):
        from admin.admin_server import app

        violations = []
        for path, path_item in app.openapi()["paths"].items():
            for method, operation in path_item.items():
                if not isinstance(operation, dict):
                    continue
                for parameter in operation.get("parameters", []):
                    if (
                        parameter.get("in") == "query"
                        and parameter.get("name", "").lower() in {"token", "secret"}
                    ):
                        violations.append(f"{method.upper()} {path}: {parameter['name']}")
        assert not violations, "Sensitive query auth parameters exposed:\n" + "\n".join(
            violations
        )
