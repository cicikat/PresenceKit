from __future__ import annotations

import asyncio
import time

import pytest
from pydantic import ValidationError

from core.activity import store
from core.activity.minecraft import MinecraftError, MinecraftService
from core.activity.minecraft_companion import Plan
from core.activity.minecraft_settings import MinecraftSettings


class FakeBridge:
    def __init__(self):
        self.calls = []
        self.session = None
        self.events = []

    async def request(self, method, path, payload=None):
        self.calls.append((path, payload))
        if path == "/v1/connect":
            self.session = payload["session_id"]
        if path.startswith("/v1/events"):
            return {"connection_epoch": "fixture-epoch", "events": self.events}
        if path == "/v1/commands":
            return {"command_id": payload["command_id"], "status": "succeeded"}
        return {"protocol_version": 1, "connection_epoch": "fixture-epoch", "session_id": self.session,
                "status": "connected", "observed_at": int(time.time() * 1000), "game": {"health": 20}}


@pytest.fixture
def runtime(sandbox, monkeypatch):
    monkeypatch.setattr("core.activity.minecraft.readiness", lambda cfg: "ready" if cfg.enabled else "disabled")
    cfg = {"minecraft": MinecraftSettings(enabled=True, host="localhost", version="1.21.1",
                                             username="FixtureBody", auth="offline",
                                             owner_uuid="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa").model_dump()}
    bridge = FakeBridge()
    principal = ["test_owner", "fixture_character"]
    async def planner(*args):
        return Plan(reply="我会跟上你。", action="follow")
    service = MinecraftService(config=lambda: cfg, principal=lambda: tuple(principal),
                               bridge_factory=lambda _: bridge, planner=planner)
    service.worker_alive = True
    return service, bridge, cfg, principal


@pytest.mark.asyncio
async def test_activity_chat_does_not_write_main_memory(runtime, sandbox):
    service, bridge, _, _ = runtime
    before = {p for p in sandbox._base.rglob("*") if p.is_file()}
    started = await service.start()
    await service.chat("跟着我")
    paths = [str(p.relative_to(sandbox._base)).replace("\\", "/") for p in sandbox._base.rglob("*") if p.is_file() and p not in before]
    assert paths and all(p.startswith("runtime/activity/") for p in paths)
    assert any(p.endswith("transcript.jsonl") for p in paths)
    assert [p[1]["action"] for p in bridge.calls if p[0] == "/v1/commands"] == ["follow", "say"]
    await service.close()
    assert store.load_session("fixture_character", "test_owner", "minecraft", started["session_id"]).status == "closed"


@pytest.mark.asyncio
async def test_settings_and_principal_changes_revoke_before_next_action(runtime):
    service, bridge, cfg, principal = runtime
    await service.start()
    principal[1] = "other_character"
    await service.tick()
    assert service.binding is None
    assert any(path == "/v1/disconnect" for path, _ in bridge.calls)
    with pytest.raises(MinecraftError, match="no_active_session"):
        await service.command("follow")


@pytest.mark.asyncio
async def test_late_model_plan_cannot_restart_after_owner_stop(runtime):
    service, bridge, _, _ = runtime
    entered, release = asyncio.Event(), asyncio.Event()
    async def planner(*args):
        entered.set(); await release.wait()
        return Plan(reply="我来了。", action="follow")
    service.planner = planner
    await service.start()
    task = asyncio.create_task(service.chat("跟随"))
    await entered.wait()
    await service.command("stop")
    release.set()
    with pytest.raises(MinecraftError, match="stale_plan"):
        await task
    assert [p[1]["action"] for p in bridge.calls if p[0] == "/v1/commands"] == ["stop"]


@pytest.mark.asyncio
async def test_capability_and_budget_gates_preserve_emergency_stop(runtime):
    service, bridge, cfg, _ = runtime
    await service.start()
    with pytest.raises(MinecraftError, match="capability_disabled"):
        await service.command("defend")
    service.binding.model_calls = 120
    with pytest.raises(MinecraftError, match="model_budget_unavailable"):
        await service.chat("你好")
    assert (await service.chat("停下"))["receipt"]["status"] == "succeeded"


@pytest.mark.asyncio
async def test_concurrent_start_and_chat_are_bounded(runtime):
    service, _, _, _ = runtime
    results = await asyncio.gather(service.start(), service.start(), return_exceptions=True)
    assert sum(isinstance(r, dict) for r in results) == 1
    assert sum(isinstance(r, MinecraftError) for r in results) == 1
    service.model_busy = True
    with pytest.raises(MinecraftError, match="model_busy"):
        await service.chat("你好")
    assert (await service.chat("停止"))["receipt"]


@pytest.mark.asyncio
async def test_stale_bridge_epoch_closes_session(runtime):
    service, bridge, _, _ = runtime
    await service.start()
    service.binding.epoch = "old-epoch"
    await service.tick()
    assert service.binding is None


def test_settings_reject_remote_bridge_unknown_authority_and_invalid_uuid():
    for bad in ({"bridge_url": "http://example.com:80"}, {"shell": "x"}, {"owner_uuid": "player-name"}):
        with pytest.raises(ValidationError):
            MinecraftSettings(**bad)


def test_registry_backend_only_no_client_commands_or_main_memory():
    from core.activity.registry import get_activity_meta
    meta = get_activity_meta("minecraft")
    assert meta and not meta.enabled and not meta.frontend_key and not meta.tauri_commands
    assert meta.memory_policy.main_memory == "none"
    assert not meta.memory_policy.writes_hidden_state


def test_plan_rejects_arbitrary_action_and_authority():
    with pytest.raises(ValidationError):
        Plan(reply="ok", action="shell")
    with pytest.raises(ValidationError):
        Plan(reply="ok", token="x")
    with pytest.raises(ValidationError):
        Plan(reply="/op player", action="follow")
    with pytest.raises(ValidationError):
        Plan(reply="ok", action="follow", params={"host": "bad"})


def test_offline_name_rejects_invalid_java_login_packet():
    from core.activity.minecraft_settings import MinecraftSettings
    for name in ("a" * 17, "invalid name", "角色"):
        with pytest.raises(ValidationError):
            MinecraftSettings(auth="offline", username=name)
    assert MinecraftSettings(auth="offline", username="PresenceTest").username == "PresenceTest"


def test_model_json_fence_keeps_strict_action_authority():
    from core.activity.minecraft_companion import parse_plan
    assert parse_plan('```json\n{"reply":"好。","action":"follow"}\n```').action == "follow"
    for raw in ('我来了 {"reply":"好。"}', '```json\n{"reply":"好。","action":"shell"}\n```'):
        with pytest.raises(ValidationError):
            parse_plan(raw)


@pytest.mark.asyncio
async def test_failed_action_never_sends_claimed_model_reply(runtime):
    service, bridge, _, _ = runtime
    original = bridge.request
    async def failed(method, path, payload=None):
        if path == "/v1/commands" and payload["action"] == "follow":
            bridge.calls.append((path, payload))
            return {"status": "failed", "error": "path_unavailable"}
        return await original(method, path, payload)
    bridge.request = failed
    await service.start()
    with pytest.raises(MinecraftError, match="action_failed"):
        await service.chat("跟着我")
    assert not any(p == "/v1/commands" and c["action"] == "say" for p, c in bridge.calls)


def test_http_scopes_reject_mutation_before_bridge_or_storage(runtime, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from admin.auth import TokenInfo
    from admin.routers import minecraft
    import admin.auth
    service, bridge, _, _ = runtime
    monkeypatch.setattr(minecraft, "get_service", lambda: service)
    monkeypatch.setattr(admin.auth, "resolve_token", lambda raw: TokenInfo(label="fixture", scopes=frozenset({"state.read"}), profile=None))
    app = FastAPI()
    app.include_router(minecraft.router, prefix="/activity")
    app.include_router(minecraft.control_router)
    with TestClient(app) as client:
        assert client.post("/activity/minecraft/start").status_code == 401
        assert client.post("/activity/minecraft/start", headers={"Authorization": "Bearer fixture"}).status_code == 403
        assert client.get("/observability/minecraft", headers={"Authorization": "Bearer fixture"}).status_code == 200
        assert client.get("/settings/minecraft", headers={"Authorization": "Bearer fixture"}).status_code == 403
    assert not bridge.calls
