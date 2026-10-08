"""Owner-only Minecraft control plane and activity ingress."""
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from admin.auth import require_scopes
from admin.config_control import read_config_file, write_config_file
from core.activity.minecraft import MinecraftError, get_service
from core.activity.minecraft_bridge import BridgeError
from core.activity.minecraft_settings import MinecraftSettings, readiness, settings
from core.config_loader import get_config, get_config_path

router = APIRouter()
control_router = APIRouter()
CONFIG_FILE = get_config_path()


def service():
    try:
        return get_service()
    except MinecraftError as exc:
        raise HTTPException(503, detail=str(exc)) from None


def view():
    from core.activity.minecraft_routing import route_view
    cfg = settings(get_config())
    try:
        observed = get_service().observation()
    except MinecraftError:
        observed = {"worker_alive": False, "active": False, "connection_state": "unavailable"}
    return {"configured": cfg.model_dump(), "effective_state": readiness(cfg) if observed["worker_alive"] else "worker_offline",
            "apply_mode": "hot_reload", "runtime": observed,
            "defaults": MinecraftSettings().model_dump(), "routing": route_view()}


class ReactionRouting(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    profile: str = Field(min_length=1, max_length=128)
    preset: str = Field(max_length=128)


@control_router.put("/settings/minecraft/routing")
async def set_reaction_routing(body: ReactionRouting, auth=Depends(require_scopes("admin"))):
    from core.activity.minecraft_routing import route_view
    current = route_view()
    if body.profile != current["profile"]:
        raise HTTPException(409, detail="routing_profile_changed_refresh_required")
    full = read_config_file(CONFIG_FILE)
    mp = full.get("model_presets", {})
    if body.profile not in mp.get("routing_profiles", {}) or body.preset and body.preset not in mp.get("presets", {}):
        raise HTTPException(422, detail="unknown_model_route")
    await service().close("routing_changed")
    route = mp["routing_profiles"][body.profile]
    if body.preset:
        route["minecraft_reaction"] = body.preset
    else:
        route.pop("minecraft_reaction", None)
    write_config_file(CONFIG_FILE, full)
    from core.config_loader import reload_config
    from core import llm_client
    reload_config()
    await llm_client.reload_client()
    return route_view()


@control_router.get("/settings/minecraft")
async def get_settings(auth=Depends(require_scopes("admin"))):
    return view()


@control_router.put("/settings/minecraft")
async def put_settings(body: MinecraftSettings, auth=Depends(require_scopes("admin"))):
    old = settings(get_config())
    if body != old:
        try:
            await get_service().close("settings_changed")
        except MinecraftError:
            pass
    full = read_config_file(CONFIG_FILE)
    full["minecraft"] = body.model_dump()
    write_config_file(CONFIG_FILE, full)
    from core.config_loader import reload_config
    reload_config()
    return view()


@control_router.get("/observability/minecraft")
async def observation(session_id: str | None = Query(default=None, pattern=r"^[a-f0-9]{32}$"), auth=Depends(require_scopes("state.read"))):
    runtime = service()
    if session_id:
        from core.activity import store
        uid, char_id = runtime.principal()
        saved = store.load_session(char_id, uid, "minecraft", session_id)
        if not saved:
            raise HTTPException(404, detail="session_not_found")
        return {"session_id": saved.session_id, "status": saved.status, "state": saved.state}
    return runtime.observation()


async def execute(call):
    try:
        return await call
    except (MinecraftError, BridgeError) as exc:
        raise HTTPException(409, detail=str(exc)) from None
    except ValidationError:
        raise HTTPException(422, detail="invalid_minecraft_settings") from None
    except Exception:
        raise HTTPException(503, detail="minecraft_operation_failed") from None


@router.post("/minecraft/start")
async def start(auth=Depends(require_scopes("activity"))):
    return await execute(service().start())


@router.post("/minecraft/close")
async def close(auth=Depends(require_scopes("activity"))):
    await execute(service().close())
    return {"closed": True}


@router.get("/minecraft/state")
async def state(auth=Depends(require_scopes("activity"))):
    runtime = service()
    return {"session_id": runtime.binding.session_id if runtime.binding else None,
            "observation": runtime.observation(), "state": runtime.snapshot}


class Command(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    action: Literal["follow", "stop", "return", "pickup", "defend", "collect_iron", "approach", "accompany", "protect", "build_house"]
    params: dict = Field(default_factory=dict)
    command_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")


@router.post("/minecraft/command")
async def command(body: Command, auth=Depends(require_scopes("activity"))):
    return await execute(service().command(body.action, body.params, command_id=body.command_id))


class Chat(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    text: str = Field(min_length=1, max_length=500)


@router.post("/minecraft/chat")
async def chat(body: Chat, auth=Depends(require_scopes("activity"))):
    return await execute(service().chat(body.text))
