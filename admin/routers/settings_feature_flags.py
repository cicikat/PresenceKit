"""Allowlisted runtime feature flags for the admin panel.

Only boolean switches with an implemented config consumer are exposed. Secrets and
deployment paths deliberately stay out of this generic endpoint.
"""
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from admin.auth import require_scopes
from admin.config_control import read_config_file, write_config_file
from core.config_loader import get_config

router = APIRouter()
CONFIG_FILE = Path("config.yaml")

FLAGS = {
    "qq":   ("qq",   "enabled", "QQ 通道"),
    "mail": ("mail", "enabled", "邮件通道"),
    "visual_perception": ("visual_perception", "enabled", "视觉感知"),
    "spend": ("spend", "enabled", "支出意向"),
    "practice": ("practice", "enabled", "自主练习"),
    "action_trace": ("action_trace", "enabled", "行为痕迹"),
    "self_management": ("self_management", "enabled", "Self Capability"),
    "mcp_servers": ("mcp_servers", "enabled", "MCP 外部工具"),
    "fs_access": ("fs_access", "enabled", "文件只读访问"),
    "workspace_access": ("workspace_access", "enabled", "Workspace 文件能力"),
    "anti_collapse": ("anti_collapse", "enabled", "输出防坍缩"),
    "coplay": ("coplay", "enabled", "陪玩部署"),
    "toy_autogrow": ("toy_autogrow", "enabled", "玩具自主生长"),
    "web_autosearch": ("web_autosearch", "enabled", "自主联网搜索"),
    "performance_mapping": ("performance_mapping", "enabled", "表演标注映射"),
    "private_exchange": ("private_exchange", "enabled", "角色私下往来"),
    "event_edge_proposer": ("event_edge_proposer", "enabled", "Memory Event 候选关联边"),
    "event_shadow_recall": ("event_shadow_recall", "enabled", "Memory Event shadow recall"),
}
RESTART_REQUIRED_FLAGS = frozenset({"qq"})
_DEFAULT_ENABLED_FLAGS = frozenset({"self_management"})


class FeatureFlagsUpdate(BaseModel):
    flags: dict[str, bool]


class EventShadowRecallUpdate(BaseModel):
    enabled: bool | None = None
    uids: list[str] | None = None
    char_ids: list[str] | None = None


class EventContextObserverUpdate(BaseModel):
    mode: str


@router.get("/settings/feature-flags", summary="读取功能开关白名单")
async def get_feature_flags(auth=Depends(require_scopes("admin"))):
    cfg = get_config()
    flags = {}
    for name, (section, key, label) in FLAGS.items():
        enabled = bool(cfg.get(section, {}).get(key, name in _DEFAULT_ENABLED_FLAGS))
        item = {
            "enabled": bool(cfg.get(section, {}).get(key, name in _DEFAULT_ENABLED_FLAGS)),
            "desired_enabled": enabled,
            "label": label,
            "apply_mode": "restart_required" if name in RESTART_REQUIRED_FLAGS else "hot_reload",
            "restart_required": name in RESTART_REQUIRED_FLAGS,
        }
        if name == "event_edge_proposer":
            from core.scheduler.triggers.event_edge_proposer import discovery_observability_snapshot
            discovery = discovery_observability_snapshot()
            if not enabled:
                item["effective_state"] = "disabled"
            elif discovery.get("runs") and not discovery.get("eligible_scopes"):
                item["effective_state"] = "enabled-but-no-scope"
            elif discovery.get("completed_scopes"):
                item["effective_state"] = "enabled-and-running"
            else:
                item["effective_state"] = "enabled-not-run"
            item["description"] = "只生成未采纳的候选关系；不改变正式记忆或确定性关系边"
        elif name == "event_shadow_recall":
            shadow = _shadow_settings()
            item["effective_state"] = shadow["effective_state"]
            item["description"] = "只比较新旧召回并写脱敏观测；不进入正式 prompt"
        else:
            item["effective_state"] = "enabled" if enabled else "disabled"
        flags[name] = item
    return {"flags": flags}


@router.put("/settings/feature-flags", summary="批量更新功能开关并返回实际生效方式")
async def update_feature_flags(body: FeatureFlagsUpdate, auth=Depends(require_scopes("admin"))):
    unknown = sorted(set(body.flags) - set(FLAGS))
    if unknown:
        raise HTTPException(status_code=422, detail=f"未知功能开关: {unknown}")
    full_cfg = read_config_file(CONFIG_FILE)
    changed = set()
    for name, enabled in body.flags.items():
        section, key, _ = FLAGS[name]
        if bool(full_cfg.get(section, {}).get(key, name in _DEFAULT_ENABLED_FLAGS)) != enabled:
            changed.add(name)
        full_cfg.setdefault(section, {})[key] = enabled
    write_config_file(CONFIG_FILE, full_cfg)
    from core import config_loader
    config_loader.reload_config()
    if "mcp_servers" in body.flags:
        from core import mcp_client
        await mcp_client.sync_mcp_servers()
    result = await get_feature_flags(auth)
    restart_required = sorted(changed & RESTART_REQUIRED_FLAGS)
    result.update({
        "reload_status": "restart_required" if restart_required else "reloaded",
        "restart_required": restart_required,
        "message": (
            "设置已保存；QQ 通道需要重启后端后生效"
            if restart_required
            else "设置已保存并热生效"
        ),
    })
    return result


def _shadow_settings() -> dict:
    cfg = get_config().get("event_shadow_recall") or {}
    enabled = bool(cfg.get("enabled", False))
    uids = [str(value) for value in (cfg.get("uids") or []) if str(value)]
    char_ids = [str(value) for value in (cfg.get("char_ids") or []) if str(value)]
    return {
        "enabled": enabled,
        "desired_enabled": enabled,
        "uids": uids,
        "char_ids": char_ids,
        "apply_mode": "hot_reload",
        "effective_state": (
            "enabled-for-all" if enabled
            else "allowlist-active" if uids or char_ids
            else "disabled"
        ),
    }


def _event_context_observer_settings() -> dict:
    from core.event_context_observer import snapshot
    return snapshot()


@router.get("/settings/event-shadow-recall", summary="读取 Memory Event shadow recall 灰度设置")
async def get_event_shadow_recall_settings(auth=Depends(require_scopes("admin"))):
    return _shadow_settings()


@router.put("/settings/event-shadow-recall", summary="更新 Memory Event shadow recall 灰度设置")
async def update_event_shadow_recall_settings(
    body: EventShadowRecallUpdate,
    auth=Depends(require_scopes("admin")),
):
    full_cfg = read_config_file(CONFIG_FILE)
    section = full_cfg.setdefault("event_shadow_recall", {})
    if body.enabled is not None:
        section["enabled"] = bool(body.enabled)
    for key, values in (("uids", body.uids), ("char_ids", body.char_ids)):
        if values is None:
            continue
        cleaned = []
        for value in values[:100]:
            value = str(value).strip()
            if value and len(value) <= 128:
                cleaned.append(value)
        section[key] = sorted(set(cleaned))
    write_config_file(CONFIG_FILE, full_cfg)
    from core import config_loader
    config_loader.reload_config()
    return {**_shadow_settings(), "reload_status": "reloaded"}


@router.get("/settings/event-context-observer", summary="读取 EventContext 旁路观测设置")
async def get_event_context_observer_settings(auth=Depends(require_scopes("admin"))):
    return _event_context_observer_settings()


@router.put("/settings/event-context-observer", summary="更新 EventContext 旁路观测设置")
async def update_event_context_observer_settings(
    body: EventContextObserverUpdate,
    auth=Depends(require_scopes("admin")),
):
    # Enforcing is available only after the durable S1 sample gate passes.
    mode = str(body.mode).strip().lower()
    if mode not in {"disabled", "observe", "enforcing"}:
        raise HTTPException(status_code=422, detail="mode must be disabled, observe, or enforcing")
    if mode == "enforcing":
        from core.event_context_observer import enforcing_readiness
        readiness = enforcing_readiness()
        if not readiness["ready"]:
            raise HTTPException(status_code=409, detail={"code": "event_context_soak_not_ready", "readiness": readiness})
    if mode == "observe":
        from core.memory.event_store import initialize_existing_ledgers
        readiness = initialize_existing_ledgers()
        if readiness.get("status") != "ok":
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "event_ledger_not_ready",
                    "startup_readiness": readiness,
                },
            )
    full_cfg = read_config_file(CONFIG_FILE)
    full_cfg.setdefault("event_context_observer", {})["mode"] = mode
    write_config_file(CONFIG_FILE, full_cfg)
    from core import config_loader
    config_loader.reload_config()
    return {**_event_context_observer_settings(), "reload_status": "reloaded"}
