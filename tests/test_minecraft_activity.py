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


def test_default_bridge_credential_survives_normal_backend_restart(tmp_path, monkeypatch):
    from core.activity.minecraft_settings import bridge_token
    monkeypatch.delenv("PRESENCEKIT_MINECRAFT_TOKEN_FILE", raising=False)
    monkeypatch.setattr("core.config_loader.get_config_path", lambda: tmp_path / "config.yaml")
    assert bridge_token() == ""
    (tmp_path / ".env.minecraft.bridge-token").write_bytes(b"x" * 32)
    assert bridge_token() == "x" * 32


def test_model_json_fence_keeps_strict_action_authority():
    from core.activity.minecraft_companion import parse_plan
    assert parse_plan('```json\n{"reply":"好。","action":"follow"}\n```').action == "follow"
    for raw in ('我来了 {"reply":"好。"}', '```json\n{"reply":"好。","action":"shell"}\n```'):
        with pytest.raises(ValidationError):
            parse_plan(raw)


@pytest.mark.asyncio
async def test_collection_grant_and_bounds_checked_before_bridge(runtime):
    service, bridge, cfg, _ = runtime
    await service.start()
    with pytest.raises(MinecraftError, match="capability_disabled"):
        await service.command("collect_iron", {"count": 1, "radius": 4})
    cfg["minecraft"]["allow_mining"] = True
    # Config changes revoke the previous session; new authority requires a new session.
    await service.close(); await service.start()
    with pytest.raises(MinecraftError, match="invalid_params"):
        await service.command("collect_iron", {"count": 9, "radius": 4})
    assert (await service.command("collect_iron", {"count": 1, "radius": 4}))["status"] == "succeeded"


@pytest.mark.asyncio
async def test_fast_judgement_executes_before_slow_persona_and_has_separate_budget(runtime):
    from core.activity.minecraft_reaction import Decision
    service, bridge, cfg, _ = runtime
    cfg["minecraft"]["reaction_enabled"] = True
    seen = []
    async def reactor(*args):
        seen.append("reaction"); return Decision(action="approach")
    async def planner(*args):
        seen.append("persona")
        assert args[-1]["accepted_fast_action"] == "approach"
        assert any(p == "/v1/commands" and c["action"] == "approach" for p, c in bridge.calls)
        return Plan(reply="我过来陪你。", action="none")
    service.reactor = reactor; service.planner = planner
    await service.start(); result = await service.chat("靠近我")
    assert seen == ["reaction", "persona"]
    assert result["receipt"]["status"] == "succeeded"
    assert service.binding.reaction_calls == 1


@pytest.mark.asyncio
async def test_late_fast_result_cannot_undo_stop_and_never_interrupts_work(runtime):
    from core.activity.minecraft_reaction import Decision
    service, bridge, cfg, _ = runtime
    cfg["minecraft"]["reaction_enabled"] = True
    entered, release = asyncio.Event(), asyncio.Event()
    async def reactor(*args):
        entered.set(); await release.wait(); return Decision(action="follow")
    service.reactor = reactor
    await service.start()
    task = asyncio.create_task(service.react(service.binding, "跟着我"))
    await entered.wait(); await service.command("stop"); release.set()
    assert await task is None
    assert service.reaction_error == "stale_reaction"
    assert not any(p == "/v1/commands" and c["action"] == "follow" for p, c in bridge.calls)
    service.binding.last_reaction_at = 0
    service.snapshot["current"] = {"action": "collect_iron"}
    assert await service.react(service.binding, "靠近我") is None
    assert service.binding.reaction_calls == 1


@pytest.mark.asyncio
async def test_route_edit_revokes_old_game_authority(runtime):
    service, _, cfg, _ = runtime
    await service.start()
    cfg["model_presets"] = {"active_routing": "changed"}
    await service.tick()
    assert service.binding is None


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


@pytest.mark.asyncio
async def test_building_is_separately_authorized_and_has_strict_coordinates(runtime):
    service, bridge, cfg, _ = runtime
    await service.start()
    params = {"material": "oak_planks", "x": 4, "y": 64, "z": 0}
    with pytest.raises(MinecraftError, match="capability_disabled"):
        await service.command("build_house", params)
    cfg["minecraft"]["allow_building"] = True
    await service.close()
    await service.start()
    with pytest.raises(ValidationError):
        await service.command("build_house", {**params, "material": "tnt"})
    with pytest.raises(ValidationError):
        await service.command("build_house", {**params, "x": True})
    with pytest.raises(ValidationError):
        Plan(reply="可以开始。", action="build_house", params={"material": "oak_planks"})
    await service.command("build_house", params)
    assert bridge.calls[-1][1]["params"] == params
    assert service.binding.companion_goal is None
    await service.close()


@pytest.mark.asyncio
async def test_local_game_stop_revokes_pending_model_before_chat_event_poll(runtime):
    service, bridge, _, _ = runtime
    entered, release = asyncio.Event(), asyncio.Event()
    async def planner(*args):
        entered.set()
        await release.wait()
        return Plan(reply="开始陪你走。", action="accompany")
    service.planner = planner
    await service.start()
    task = asyncio.create_task(service.chat("陪我走"))
    await entered.wait()
    request = bridge.request
    async def stopped_snapshot(method, path, payload=None):
        result = await request(method, path, payload)
        if path == "/v1/heartbeat":
            result["owner_stop_revision"] = 1
        return result
    bridge.request = stopped_snapshot
    await service.tick()
    release.set()
    with pytest.raises(MinecraftError, match="stale_plan"):
        await task
    assert not any(path == "/v1/commands" for path, _ in bridge.calls)
    await service.close()
