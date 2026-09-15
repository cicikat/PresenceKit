"""MCP 外部工具的管理面配置、连接测试与热重载（Brief 110）。"""
from __future__ import annotations

import re
import secrets
import time
import uuid
from pathlib import Path
from typing import Any, Literal, Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from admin.auth import require_scopes
from admin.config_control import read_config_file, write_config_file
from core.config_loader import get_config, get_config_path

router = APIRouter()
# Tests may override this path; production resolves the runtime loader's path.
CONFIG_FILE: Path | None = None
_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
_REMOTE_TRANSPORTS = ("sse", "streamable-http")
_CONSOLE_CONFIRM_TTL_S = 120
_CONSOLE_CONFIRM_LIMIT = 100
_console_confirmations: dict[str, dict[str, Any]] = {}


class McpMetadataMapping(BaseModel):
    namespace: str = Field(min_length=1, max_length=160)
    schema_versions: list[int | str] = Field(min_length=1, max_length=16)
    schema_version_field: str = Field(default="schema_version", min_length=1, max_length=64)
    domains_field: str = Field(default="domains", min_length=1, max_length=64)
    interaction_field: str = Field(default="interaction", min_length=1, max_length=64)


class McpMetadataOverride(BaseModel):
    mode: Literal["remote", "override", "ignore"] = "remote"
    domains: list[str] = Field(default_factory=list)


class McpDomainSelector(BaseModel):
    domains: list[str] = Field(min_length=1)
    include_unclassified: bool = True


class McpServerDraft(BaseModel):
    name: str
    url: str
    transport: Literal["sse", "streamable-http", "http"] = "streamable-http"
    use_proxy: bool = False
    headers: dict[str, str] = Field(default_factory=dict)
    allow_tools: list[str] = Field(default_factory=list)
    tool_policy: Optional[dict[str, "McpToolPolicy"]] = None
    enabled: bool = True
    tool_timeout_s: float = 30
    tool_timeouts_s: dict[str, float] = Field(default_factory=dict)
    metadata_mapping: Optional[McpMetadataMapping] = None
    domain_selector: Optional[McpDomainSelector] = None


class McpSettingsUpdate(BaseModel):
    enabled: Optional[bool] = None


class McpToolPolicy(BaseModel):
    effect: Literal["read", "write", "actuate", "emergency", "unrestricted"]
    require_confirm: Optional[bool] = None
    idempotent: Optional[bool] = None
    ui_label: Optional[str] = Field(default=None, min_length=1, max_length=48)


class McpServerUpdate(BaseModel):
    enabled: Optional[bool] = None
    allow_tools: Optional[list[str]] = None
    tool_policy: Optional[dict[str, McpToolPolicy]] = None
    bulk_authorize: Optional[Literal["default", "unrestricted"]] = None
    headers: Optional[dict[str, str]] = None
    tool_timeout_s: Optional[float] = None
    tool_timeouts_s: Optional[dict[str, float]] = None
    use_proxy: Optional[bool] = None
    tool_presets: Optional[list[dict]] = None
    active_tool_preset: Optional[str] = None
    metadata_mapping: Optional[McpMetadataMapping] = None
    metadata_overrides: Optional[dict[str, McpMetadataOverride]] = None
    domain_selector: Optional[McpDomainSelector] = None


class McpConsoleInvoke(BaseModel):
    server: str = Field(min_length=1, max_length=64)
    tool: str = Field(min_length=1, max_length=128)
    arguments: dict[str, Any] = Field(default_factory=dict)


class McpConsoleConfirm(BaseModel):
    confirmation_id: str = Field(min_length=16, max_length=256)


class _ConsoleSessionState:
    """Small adapter that lets the admin console use dispatcher confirmation."""

    WAITING_CONFIRM = "waiting_confirm"

    def __init__(self, *, confirmed: bool = False):
        self.status = self.WAITING_CONFIRM if confirmed else "idle"
        self.pending_tool: str | None = None
        self.pending_args: dict[str, Any] | None = None

    def set_waiting_confirm(self, tool_name: str, tool_args: dict[str, Any]) -> None:
        self.status = self.WAITING_CONFIRM
        self.pending_tool = tool_name
        self.pending_args = dict(tool_args)


def _validate_draft(draft: McpServerDraft) -> dict:
    from core.mcp_client import normalize_domain_selector, normalize_metadata_mapping

    name = draft.name.strip()
    if not _NAME_RE.fullmatch(name):
        raise HTTPException(status_code=422, detail="name 只能含字母、数字、_、-，且必须以字母开头")
    parsed = urlparse(draft.url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(status_code=422, detail="URL 必须是完整的 http(s) MCP endpoint")
    if draft.transport not in {*_REMOTE_TRANSPORTS, "http"}:
        raise HTTPException(
            status_code=422,
            detail="transport 仅支持 sse 或 streamable-http（http 仅为兼容别名）",
        )
    if not all(key.strip() and value for key, value in draft.headers.items()):
        raise HTTPException(status_code=422, detail="headers 的键和值都必须是非空字符串")
    if len(draft.allow_tools) > 200:
        raise HTTPException(status_code=422, detail="allow_tools 最多 200 项")
    try:
        metadata_mapping = (
            normalize_metadata_mapping(draft.metadata_mapping.model_dump(exclude_none=True))
            if draft.metadata_mapping is not None else None
        )
        domain_selector = (
            normalize_domain_selector(draft.domain_selector.model_dump(exclude_none=True))
            if draft.domain_selector is not None else None
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "name": name,
        "transport": draft.transport,
        "use_proxy": bool(draft.use_proxy),
        "url": draft.url.strip(),
        "headers": dict(draft.headers),
        "allow_tools": list(dict.fromkeys(draft.allow_tools)),
        **(
            {"tool_policy": {
                tool_name: policy.model_dump(exclude_none=True)
                for tool_name, policy in draft.tool_policy.items()
            }}
            if draft.tool_policy is not None else {}
        ),
        "enabled": bool(draft.enabled),
        "tool_timeout_s": max(1, min(660, float(draft.tool_timeout_s))),
        "tool_timeouts_s": _normalize_tool_timeouts(draft.tool_timeouts_s),
        **({"metadata_mapping": metadata_mapping} if metadata_mapping is not None else {}),
        **({"domain_selector": domain_selector} if domain_selector is not None else {}),
    }


def _normalize_tool_timeouts(raw: object) -> dict[str, float]:
    if not isinstance(raw, dict) or len(raw) > 200:
        raise HTTPException(status_code=422, detail="tool_timeouts_s 必须是不超过 200 项的对象")
    normalized: dict[str, float] = {}
    for tool_name, timeout_s in raw.items():
        if not isinstance(tool_name, str) or not tool_name:
            raise HTTPException(status_code=422, detail="tool_timeouts_s 的工具名必须是非空字符串")
        try:
            value = float(timeout_s)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail="tool_timeouts_s 的值必须是秒数") from exc
        if not 1 <= value <= 660:
            raise HTTPException(status_code=422, detail="tool_timeouts_s 的值必须在 1-660 秒")
        normalized[tool_name] = value
    return normalized


def _read_config() -> dict:
    return read_config_file(_config_file())


def _write_config(cfg: dict) -> None:
    write_config_file(_config_file(), cfg)


def _config_file() -> Path:
    """Use the same base config file as the runtime loader."""
    return CONFIG_FILE if CONFIG_FILE is not None else get_config_path()


def _safe_headers(headers: object) -> dict[str, str]:
    """管理面永不回显字面 token；${ENV_VAR} 可安全显示以便排查绑定关系。"""
    if not isinstance(headers, dict):
        return {}
    return {
        str(key): value if isinstance(value, str) and "${" in value else "••••已配置"
        for key, value in headers.items()
    }


def _safe_url(url: object) -> str:
    """Expose only an endpoint identity, never a literal URL-path credential."""
    if not isinstance(url, str) or not url:
        return ""
    # A variable reference is safe and useful to show: it lets the operator
    # confirm the binding without disclosing the value.
    if "${" in url:
        return url
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return "••••已配置"
    host = parsed.hostname
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    if parsed.port:
        host = f"{host}:{parsed.port}"
    # An arbitrary gateway path can itself be its credential.  The MCP page
    # does not edit it after import, so retaining only the host is sufficient.
    return f"{parsed.scheme}://{host}" + ("/••••已配置" if parsed.path or parsed.query else "")


def _normalize_tool_presets(raw: object) -> list[dict[str, object]]:
    """Validate the per-server named allowlist presets stored in config.yaml."""
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > 50:
        raise HTTPException(status_code=422, detail="tool_presets 最多 50 个，且必须是列表")
    normalized: list[dict[str, object]] = []
    names: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            raise HTTPException(status_code=422, detail="每个工具预设必须是对象")
        name = str(item.get("name") or "").strip()
        tools = item.get("tools")
        if not name or len(name) > 64:
            raise HTTPException(status_code=422, detail="工具预设名称不能为空，且最多 64 个字符")
        if name in names:
            raise HTTPException(status_code=422, detail=f"工具预设名称重复: {name}")
        if not isinstance(tools, list) or len(tools) > 200 or not all(isinstance(tool, str) and tool for tool in tools):
            raise HTTPException(status_code=422, detail="工具预设的 tools 必须是最多 200 项的非空字符串列表")
        names.add(name)
        normalized.append({"name": name, "tools": list(dict.fromkeys(tools))})
    return normalized


def _prune_tool_policy(server: dict) -> None:
    """Keep persisted policy entries only for currently allowlisted tools."""
    policy = server.get("tool_policy")
    if not isinstance(policy, dict):
        server.pop("tool_policy", None)
        return
    allowed = set(server.get("allow_tools") or [])
    filtered = {name: value for name, value in policy.items() if name in allowed}
    if filtered:
        server["tool_policy"] = filtered
    else:
        server.pop("tool_policy", None)


def _default_tool_policy_entries(tools: list[dict]) -> dict[str, dict[str, object]]:
    """Build convenient local policies from one administrator-reviewed snapshot."""
    from core.mcp_client import suggest_tool_policy

    entries: dict[str, dict[str, object]] = {}
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        tool_name = str(tool.get("name") or "").strip()
        if not tool_name or tool_name in entries:
            continue
        suggestion = tool.get("suggestion")
        if not isinstance(suggestion, dict):
            suggestion = suggest_tool_policy(
                tool_name,
                str(tool.get("description") or ""),
            )
        effect = "read" if suggestion.get("effect") == "read" else "write"
        entries[tool_name] = {"effect": effect, "require_confirm": False}
    return entries


def _fill_missing_tool_policy_defaults(server: dict, tools: list[dict]) -> None:
    """Fill only missing entries; explicit local policy always wins."""
    allowed = set(server.get("allow_tools") or [])
    policy = dict(server.get("tool_policy") or {})
    for tool_name, entry in _default_tool_policy_entries(tools).items():
        if tool_name in allowed and tool_name not in policy:
            policy[tool_name] = entry
    if policy:
        server["tool_policy"] = policy
    else:
        server.pop("tool_policy", None)


def _fill_import_tool_policy_defaults(server: dict, tools: list[dict]) -> None:
    """Persist no-repeat-confirm local policies for newly imported tools.

    Discovery metadata is advisory only. Unknown tools retain the ``write``
    effect, while administrator allowlisting is sufficient for direct use.
    Explicit policies from a re-import are never overwritten.
    """
    _fill_missing_tool_policy_defaults(server, tools)


def _runtime_tool_snapshot(name: str, mcp_client) -> list[dict]:
    """Return the current connected ``list_tools`` snapshot for one server."""
    runtime = mcp_client.server_runtime(name)
    if not bool(runtime.get("connected", False)):
        raise HTTPException(status_code=409, detail="MCP server 当前未连接，无法授权")
    tools = runtime.get("tools")
    if not isinstance(tools, list):
        raise HTTPException(status_code=409, detail="MCP server 当前没有有效工具目录")
    snapshot = [
        tool for tool in tools
        if isinstance(tool, dict) and str(tool.get("name") or "").strip()
    ]
    if not snapshot:
        raise HTTPException(status_code=409, detail="MCP server 当前没有发现工具")
    return snapshot


def _validate_local_policy_before_write(server: dict, mcp_cfg: dict, mcp_client) -> None:
    """Reject strict writes that would leave an allowlisted tool unregistered."""
    if not bool(mcp_cfg.get("require_local_policy", False)) or not bool(server.get("enabled", True)):
        return
    try:
        mcp_client.validate_local_tool_policy(server, required=True)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    allow_tools = list(server.get("allow_tools") or [])
    policy = server.get("tool_policy") or {}
    missing = [tool_name for tool_name in allow_tools if not isinstance(policy.get(tool_name), dict)]
    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"严格模式下 allow_tools 缺少本地 policy: {missing}",
        )


async def _ensure_local_policy_before_write(server: dict, mcp_cfg: dict, mcp_client) -> None:
    """Backfill policy for legacy allowlists before a strict reconnect/save.

    Older configurations may have an allowlist without the newer local policy
    entries.  A disconnected server has no runtime snapshot, so discover its
    tools through the isolated test connection before applying the existing
    default-policy generator.  Explicit local entries always win.
    """
    if not bool(mcp_cfg.get("require_local_policy", False)) or not bool(server.get("enabled", True)):
        return

    allow_tools = list(server.get("allow_tools") or [])
    policy = server.get("tool_policy") or {}
    missing = [tool_name for tool_name in allow_tools if not isinstance(policy.get(tool_name), dict)]
    if missing:
        runtime = mcp_client.server_runtime(str(server.get("name") or ""))
        tools = runtime.get("tools") if bool(runtime.get("connected", False)) else None
        if not isinstance(tools, list) or not tools:
            try:
                tools = await mcp_client.test_server_config(dict(server))
            except Exception as exc:
                raise HTTPException(
                    status_code=400,
                    detail=f"MCP 连接测试失败，无法为旧 allowlist 补齐本地 policy: {exc}",
                ) from exc
        discovered_names = {
            str(item.get("name") or "").strip()
            for item in tools
            if isinstance(item, dict) and str(item.get("name") or "").strip()
        }
        unknown = sorted(set(allow_tools) - discovered_names)
        if unknown:
            raise HTTPException(status_code=422, detail=f"allow_tools 含未发现工具: {unknown}")
        _fill_missing_tool_policy_defaults(server, tools)
        _prune_tool_policy(server)

    _validate_local_policy_before_write(server, mcp_cfg, mcp_client)


def _safe_parameter_summary(schema: object) -> dict:
    """Return bounded argument hints without exposing the remote JSON Schema."""
    if not isinstance(schema, dict):
        return {"properties": [], "required": []}
    raw_properties = schema.get("properties")
    properties: list[dict[str, str]] = []
    if isinstance(raw_properties, dict):
        for name, definition in list(raw_properties.items())[:32]:
            safe_name = str(name)[:64]
            raw_type = definition.get("type") if isinstance(definition, dict) else None
            if isinstance(raw_type, list):
                safe_type = "|".join(str(item)[:16] for item in raw_type[:4])
            elif isinstance(raw_type, str):
                safe_type = raw_type[:32]
            else:
                safe_type = "unknown"
            properties.append({"name": safe_name, "type": safe_type})
    required = [
        str(name)[:64] for name in (schema.get("required") or [])[:32]
        if isinstance(name, str)
    ] if isinstance(schema.get("required"), list) else []
    return {"properties": properties, "required": required}


def _current_mcp_exposure_names() -> set[str]:
    """Resolve the active Path C schema once for the admin three-state view."""
    from core.config_loader import get_config
    from core.data_paths import DEFAULT_CHAR_ID
    from core.tool_dispatcher import get_tools_schema, tool_loop_active

    cfg = get_config() or {}
    owner_id = str(cfg.get("scheduler", {}).get("owner_id") or "")
    if not owner_id or not tool_loop_active(owner_id):
        return set()
    try:
        from core import pipeline_registry

        pipeline = pipeline_registry.get()
        character = getattr(pipeline, "character", None) if pipeline is not None else None
        char_id = str(getattr(pipeline, "_active_character_id", None) or DEFAULT_CHAR_ID)
        from core.tool_exposure import resolve as resolve_exposure
        exposure = resolve_exposure("path_c", char_id=char_id)
        categories = list(exposure.categories)
        if "mcp" not in categories:
            return set()
        excluded = set(exposure.exclude_tools)
        if exposure.tools is not None:
            excluded.update(
                str(name)
                for name in get_tools_schema(categories=categories, char_id=char_id, uid=owner_id)
                if str((name.get("function") or name).get("name") or "") not in exposure.tools
            )
        return {
            str((schema.get("function") or schema).get("name") or "")
            for schema in get_tools_schema(
                categories=list(categories), char_id=char_id, uid=owner_id,
            )
            if str((schema.get("function") or schema).get("name") or "") not in excluded
        }
    except Exception:
        return set()


def _safe_metadata_config(server_cfg: dict) -> tuple[dict | None, dict[str, dict], dict | None]:
    from core.mcp_client import (
        normalize_domain_selector,
        normalize_metadata_mapping,
        normalize_metadata_overrides,
    )

    try:
        mapping = normalize_metadata_mapping(server_cfg.get("metadata_mapping"))
    except ValueError:
        mapping = None
    try:
        overrides = normalize_metadata_overrides(server_cfg.get("metadata_overrides"))
    except ValueError:
        overrides = {}
    try:
        selector = normalize_domain_selector(server_cfg.get("domain_selector"))
    except ValueError:
        selector = None
    return mapping, overrides, selector


def _mcp_reload_result(
    name: str,
    reload_ok,
    *,
    success_message: str,
    connection_failed_message: str,
    restart_message: str,
) -> dict:
    """Hot-reload ran in-process. Connection failure is not a process restart."""
    from core import mcp_client

    runtime = mcp_client.server_runtime(name)
    error = str(runtime.get("last_init_error") or "").strip()
    if reload_ok is None:
        status, message = "restart_required", restart_message
    elif reload_ok is False:
        status = "connection_failed"
        message = connection_failed_message + (f"：{error}" if error else "")
    else:
        status, message = "reloaded", success_message
    return {
        "reload_status": status,
        "message": message,
        "last_init_error": error,
    }


def _server_view(
    server_cfg: dict,
    *,
    require_local_policy: bool = False,
    session_exposed_names: set[str] | None = None,
) -> dict:
    from core.mcp_client import is_local_mcp_url, server_runtime, suggest_tool_policy
    from core.tool_dispatcher import _TOOL_REGISTRY

    name = str(server_cfg.get("name") or "")
    runtime = server_runtime(name)
    allowed = set(server_cfg.get("allow_tools") or [])
    policy = dict(server_cfg.get("tool_policy") or {})
    metadata_mapping, metadata_overrides, domain_selector = _safe_metadata_config(server_cfg)
    session_exposed_names = session_exposed_names or set()
    tool_states: list[dict] = []
    for tool in runtime.get("tools", []):
        tool_name = str(tool.get("name") or "")
        if not tool_name:
            continue
        suggestion = tool.get("suggestion") or suggest_tool_policy(
            tool_name, str(tool.get("description") or ""),
        )
        allowlisted = tool_name in allowed if require_local_policy else not allowed or tool_name in allowed
        confirmed = isinstance(policy.get(tool_name), dict)
        if not allowlisted:
            policy_status = "not_allowlisted"
        elif confirmed:
            policy_status = "confirmed"
        elif require_local_policy:
            policy_status = "pending_confirmation"
        else:
            policy_status = "legacy_allowed"
        registry_info = _TOOL_REGISTRY.get(f"mcp__{name}__{tool_name}") or {}
        registered_name = f"mcp__{name}__{tool_name}"
        registered = registry_info.get("category") == "mcp" and registry_info.get("mcp_server") == name
        authorized = bool(allowlisted and (confirmed or not require_local_policy))
        tool_states.append({
            "name": tool_name,
            "discovered": True,
            "authorized": authorized,
            "session_exposed": registered and registered_name in session_exposed_names,
            "allowlisted": allowlisted,
            "policy_status": policy_status,
            "suggestion": suggestion,
            "policy": policy.get(tool_name),
            "registered": registered,
            "parameter_summary": _safe_parameter_summary(registry_info.get("parameters")),
            "effect": registry_info.get("effect") or "",
            "require_confirm": bool(registry_info.get("require_confirm", False)),
            "ui_label": str(registry_info.get("ui_label") or "外部工具"),
            "remote_domains": list(tool.get("remote_domains") or []),
            "remote_interaction": str(tool.get("remote_interaction") or "unknown"),
            "metadata_source": str(tool.get("metadata_source") or "none"),
            "metadata_status": str(tool.get("metadata_status") or "absent"),
            "metadata_schema_version": tool.get("metadata_schema_version"),
            "final_domains": list(tool.get("final_domains") or []),
            "metadata_override": metadata_overrides.get(tool_name),
        })
    return {
        "name": name,
        "transport": server_cfg.get("transport", "stdio"),
        "url": _safe_url(server_cfg.get("url")),
        "use_proxy": bool(server_cfg.get("use_proxy", False)),
        "is_local_url": is_local_mcp_url(str(server_cfg.get("url") or "")),
        "headers": _safe_headers(server_cfg.get("headers")),
        "enabled": bool(server_cfg.get("enabled", True)),
        "tool_timeout_s": float(server_cfg.get("tool_timeout_s", 30)),
        "tool_timeouts_s": dict(server_cfg.get("tool_timeouts_s") or {}),
        "allow_tools": list(server_cfg.get("allow_tools") or []),
        "tool_policy": policy,
        "metadata_mapping": metadata_mapping,
        "metadata_overrides": metadata_overrides,
        "domain_selector": domain_selector,
        "require_local_policy": bool(require_local_policy),
        "tool_states": tool_states,
        "tool_presets": _normalize_tool_presets(server_cfg.get("tool_presets")),
        "active_tool_preset": str(server_cfg.get("active_tool_preset") or ""),
        "runtime": runtime,
    }


@router.get("/settings/mcp", summary="读取 MCP server 配置与运行状态")
async def get_mcp_settings(_auth=Depends(require_scopes("admin"))):
    cfg = get_config().get("mcp_servers", {}) or {}
    servers = [item for item in (cfg.get("servers") or []) if isinstance(item, dict)]
    session_exposed_names = _current_mcp_exposure_names()
    return {
        "enabled": bool(cfg.get("enabled", False)),
        "require_local_policy": bool(cfg.get("require_local_policy", False)),
        "servers": [
            _server_view(
                item,
                require_local_policy=bool(cfg.get("require_local_policy", False)),
                session_exposed_names=session_exposed_names,
            )
            for item in servers
        ],
        "warning": "外部 MCP 的工具描述与结果均为不可信输入；不要把密钥写进角色卡、prompt 或文档。",
    }


def _prune_console_confirmations() -> None:
    expires_before = time.time()
    expired = [key for key, entry in _console_confirmations.items() if entry["expires_at"] <= expires_before]
    for key in expired:
        _console_confirmations.pop(key, None)
    while len(_console_confirmations) >= _CONSOLE_CONFIRM_LIMIT:
        oldest = min(_console_confirmations, key=lambda key: _console_confirmations[key]["expires_at"])
        _console_confirmations.pop(oldest, None)


def _resolve_console_tool(server_name: str, tool_name: str) -> tuple[str, dict]:
    """Resolve only a connected, currently allowed dynamic MCP registry entry."""
    from core import mcp_client
    from core.tool_dispatcher import _TOOL_REGISTRY

    if not _NAME_RE.fullmatch(server_name) or not tool_name:
        raise HTTPException(status_code=422, detail="非法 MCP server 或工具名")
    mcp_cfg = get_config().get("mcp_servers", {}) or {}
    if not bool(mcp_cfg.get("enabled", False)):
        raise HTTPException(status_code=409, detail="MCP 总开关未启用")
    server_cfg = next(
        (item for item in mcp_cfg.get("servers", []) if isinstance(item, dict) and item.get("name") == server_name),
        None,
    )
    if server_cfg is None or not bool(server_cfg.get("enabled", True)):
        raise HTTPException(status_code=409, detail="MCP server 未启用")
    if not bool(mcp_client.server_runtime(server_name).get("connected", False)):
        raise HTTPException(status_code=409, detail="MCP server 当前未连接")

    allow_tools = server_cfg.get("allow_tools") or []
    strict_policy = bool(mcp_cfg.get("require_local_policy", False))
    if (strict_policy and tool_name not in allow_tools) or (
        not strict_policy and allow_tools and tool_name not in allow_tools
    ):
        raise HTTPException(status_code=403, detail="工具不在当前 allowlist 中")
    if bool(mcp_cfg.get("require_local_policy", False)) and not isinstance(
        (server_cfg.get("tool_policy") or {}).get(tool_name), dict,
    ):
        raise HTTPException(status_code=409, detail="工具仍待本地 policy 确认")

    registered_name = f"mcp__{server_name}__{tool_name}"
    info = _TOOL_REGISTRY.get(registered_name)
    if (
        not isinstance(info, dict)
        or info.get("category") != "mcp"
        or info.get("mcp_server") != server_name
        or info.get("mcp_tool") != tool_name
    ):
        raise HTTPException(status_code=409, detail="工具未注册为可调用的 MCP 工具")
    return registered_name, info


def _validate_console_arguments(arguments: dict[str, Any], schema: object) -> None:
    try:
        import json
        import jsonschema

        if len(json.dumps(arguments, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > 64 * 1024:
            raise HTTPException(status_code=422, detail="参数 JSON 不能超过 64 KiB")
        validator = jsonschema.Draft202012Validator(schema if isinstance(schema, dict) else {})
        error = next(iter(validator.iter_errors(arguments)), None)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=422, detail="工具 schema 无效，无法校验参数") from exc
    if error is not None:
        path = ".".join(str(part) for part in error.absolute_path) or "根对象"
        raise HTTPException(
            status_code=422,
            detail=f"参数不符合工具 schema（{path}，规则：{error.validator}）",
        )


def _console_actor() -> tuple[str, str]:
    from core.data_paths import DEFAULT_CHAR_ID

    owner_id = str(get_config().get("scheduler", {}).get("owner_id") or "").strip()
    if not owner_id:
        raise HTTPException(status_code=409, detail="未配置 owner，无法建立受控工具调用上下文")
    return owner_id, DEFAULT_CHAR_ID


async def _run_console_tool(
    *, registered_name: str, arguments: dict[str, Any], audit_id: str, confirmed: bool,
) -> tuple[str | None, str | None]:
    from core.mcp_client import audit_context
    from core.tool_dispatcher import execute

    owner_id, char_id = _console_actor()
    session = _ConsoleSessionState(confirmed=confirmed)
    with audit_context(audit_id):
        return await execute(
            registered_name,
            arguments,
            owner_id,
            owner_id,
            False,
            session,
            origin="admin_console",
            char_id=char_id,
        )


@router.post("/settings/mcp/console/invoke", summary="受控调用已连接 MCP 的 allowlisted 工具")
async def invoke_mcp_console(body: McpConsoleInvoke, _auth=Depends(require_scopes("admin"))):
    registered_name, info = _resolve_console_tool(body.server, body.tool)
    _validate_console_arguments(body.arguments, info.get("parameters"))
    audit_id = uuid.uuid4().hex
    result, ask_confirm = await _run_console_tool(
        registered_name=registered_name,
        arguments=body.arguments,
        audit_id=audit_id,
        confirmed=False,
    )
    if ask_confirm:
        _prune_console_confirmations()
        confirmation_id = secrets.token_urlsafe(24)
        _console_confirmations[confirmation_id] = {
            "server": body.server,
            "tool": body.tool,
            "arguments": dict(body.arguments),
            "audit_id": audit_id,
            "expires_at": time.time() + _CONSOLE_CONFIRM_TTL_S,
        }
        return {
            "status": "confirmation_required",
            "audit_id": audit_id,
            "confirmation_id": confirmation_id,
            "confirmation_message": ask_confirm,
            "expires_in_s": _CONSOLE_CONFIRM_TTL_S,
        }
    return {"status": "completed", "audit_id": audit_id, "result": result or ""}


@router.post("/settings/mcp/console/confirm", summary="确认并执行受控 MCP 控制台调用")
async def confirm_mcp_console(body: McpConsoleConfirm, _auth=Depends(require_scopes("admin"))):
    _prune_console_confirmations()
    ticket = _console_confirmations.pop(body.confirmation_id, None)
    if ticket is None:
        raise HTTPException(status_code=409, detail="确认已失效、已使用或不存在")
    registered_name, info = _resolve_console_tool(ticket["server"], ticket["tool"])
    _validate_console_arguments(ticket["arguments"], info.get("parameters"))
    result, ask_confirm = await _run_console_tool(
        registered_name=registered_name,
        arguments=ticket["arguments"],
        audit_id=ticket["audit_id"],
        confirmed=True,
    )
    if ask_confirm:
        raise HTTPException(status_code=409, detail="工具策略在确认期间已改变")
    return {"status": "completed", "audit_id": ticket["audit_id"], "result": result or ""}


@router.patch("/settings/mcp", summary="更新 MCP 总开关（写配置并热同步）")
async def update_mcp_settings(body: McpSettingsUpdate, _auth=Depends(require_scopes("admin"))):
    # Brief 115 根治：sync_mcp_servers() 现在只把信号丢进各 server 专属常驻 task 的
    # 队列，真正的 AsyncExitStack.aclose()/重连都在那个专属 task 自己的上下文里执行，
    # 不再从这次 HTTP 请求的 task 里跨 task 直接碰 stack，热同步可以安全恢复。
    if body.enabled is None:
        raise HTTPException(status_code=422, detail="至少提供 enabled")
    full_cfg = _read_config()
    full_cfg.setdefault("mcp_servers", {})["enabled"] = body.enabled
    _write_config(full_cfg)
    from core import config_loader, mcp_client
    config_loader.reload_config()
    await mcp_client.sync_mcp_servers()
    result = await get_mcp_settings(_auth)
    result["message"] = "MCP 总开关已更新并热同步"
    return result


@router.post("/settings/mcp/test", summary="测试 MCP URL 并列出工具（不写配置）")
async def test_mcp_server(body: McpServerDraft, _auth=Depends(require_scopes("admin"))):
    from core.mcp_client import test_server_config

    server_cfg = _validate_draft(body)
    try:
        tools = await test_server_config(server_cfg)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"MCP 连接测试失败: {exc}") from exc
    return {"ok": True, "tools": tools}


@router.post("/settings/mcp/import", summary="测试后导入远程 MCP server")
async def import_mcp_server(body: McpServerDraft, _auth=Depends(require_scopes("admin"))):
    from core import config_loader, mcp_client

    server_cfg = _validate_draft(body)
    full_cfg = _read_config()
    mcp_cfg = full_cfg.setdefault("mcp_servers", {})
    existing = next(
        (
            item
            for item in (mcp_cfg.get("servers") or [])
            if item.get("name") == server_cfg["name"]
        ),
        None,
    )
    # The import form does not edit trusted local tool policy. Retain an
    # existing strict policy when re-importing the same server.
    if isinstance(existing, dict):
        if not server_cfg["allow_tools"] and isinstance(
            existing.get("allow_tools"), list
        ):
            server_cfg["allow_tools"] = list(existing["allow_tools"])
        if isinstance(existing.get("tool_policy"), dict):
            server_cfg["tool_policy"] = dict(existing["tool_policy"])
        for key in ("metadata_mapping", "metadata_overrides", "domain_selector"):
            if key not in server_cfg and key in existing:
                server_cfg[key] = existing[key]
    if bool(mcp_cfg.get("require_local_policy", False)):
        try:
            mcp_client.validate_local_tool_policy(server_cfg, required=True)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    try:
        tools = await mcp_client.test_server_config(dict(server_cfg))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"MCP 连接测试失败，未写入配置: {exc}") from exc
    discovered_names = {item["name"] for item in tools}
    unknown = sorted(set(server_cfg["allow_tools"]) - discovered_names)
    if unknown:
        raise HTTPException(status_code=422, detail=f"allow_tools 含未发现工具: {unknown}")

    _fill_import_tool_policy_defaults(server_cfg, tools)
    _prune_tool_policy(server_cfg)
    await _ensure_local_policy_before_write(server_cfg, mcp_cfg, mcp_client)

    servers = [item for item in (mcp_cfg.get("servers") or []) if item.get("name") != server_cfg["name"]]
    servers.append(server_cfg)
    mcp_cfg["servers"] = servers
    _write_config(full_cfg)
    config_loader.reload_config()
    # Brief 115 根治：热重载已恢复，走 reload_server_from_config 的信号队列，由该
    # server 专属常驻 task 自己 aclose()/重连，不跨 task。测试探测（test_server_config）
    # 用的是独立、当次即开即关的 stack，本来就不受这条根因影响。
    reload_ok = await mcp_client.reload_server_from_config(server_cfg["name"])
    payload = _mcp_reload_result(
        server_cfg["name"],
        reload_ok,
        success_message="MCP server 已导入并连接",
        connection_failed_message="MCP server 已导入并热重载，但未能连接",
        restart_message="配置已保存，但 MCP 热重载失败，需要重启服务",
    )
    payload.update({
        "tools": tools,
        "server": _server_view(
            server_cfg,
            require_local_policy=bool(mcp_cfg.get("require_local_policy", False)),
            session_exposed_names=_current_mcp_exposure_names(),
        ),
    })
    return payload


@router.patch("/settings/mcp/{name}", summary="更新一个 MCP server 的启停或工具白名单")
async def update_mcp_server(name: str, body: McpServerUpdate, _auth=Depends(require_scopes("admin"))):
    if not _NAME_RE.fullmatch(name):
        raise HTTPException(status_code=422, detail="非法 server name")
    if not body.model_fields_set:
        raise HTTPException(status_code=422, detail="没有可更新字段")
    full_cfg = _read_config()
    mcp_cfg = full_cfg.setdefault("mcp_servers", {})
    servers = mcp_cfg.setdefault("servers", [])
    server = next((item for item in servers if item.get("name") == name), None)
    if server is None:
        raise HTTPException(status_code=404, detail="MCP server 不存在")
    from core import config_loader, mcp_client

    if body.bulk_authorize is not None:
        other_fields = (
            body.enabled, body.allow_tools, body.tool_policy, body.headers,
            body.tool_timeout_s, body.tool_timeouts_s, body.use_proxy,
            body.tool_presets, body.active_tool_preset, body.metadata_mapping,
            body.metadata_overrides, body.domain_selector,
        )
        if any(value is not None for value in other_fields):
            raise HTTPException(status_code=422, detail="批量授权不能与其他设置同时提交")
        snapshot = _runtime_tool_snapshot(name, mcp_client)
        tool_names = list(dict.fromkeys(str(tool["name"]).strip() for tool in snapshot))
        server["allow_tools"] = tool_names
        if body.bulk_authorize == "default":
            _fill_missing_tool_policy_defaults(server, snapshot)
        else:
            server["tool_policy"] = {
                tool_name: {
                    "effect": "unrestricted",
                    "idempotent": True,
                    "require_confirm": False,
                }
                for tool_name in tool_names
            }
        _prune_tool_policy(server)
        _validate_local_policy_before_write(server, mcp_cfg, mcp_client)
        _write_config(full_cfg)
        config_loader.reload_config()
        reload_ok = await mcp_client.reload_server_from_config(name)
        payload = _mcp_reload_result(
            name,
            reload_ok,
            success_message="MCP 批量授权已完成",
            connection_failed_message="配置已保存并热重载，但未能连接",
            restart_message="配置已保存，但 MCP 热重载失败，需要重启服务",
        )
        payload.update({
            "action": body.bulk_authorize,
            "processed_count": len(tool_names),
            "allow_tools": list(server.get("allow_tools") or []),
            "tool_policy": dict(server.get("tool_policy") or {}),
            "server": _server_view(
                server,
                require_local_policy=bool(mcp_cfg.get("require_local_policy", False)),
                session_exposed_names=_current_mcp_exposure_names(),
            ),
        })
        return payload
    if body.enabled is not None:
        server["enabled"] = body.enabled
    if body.allow_tools is not None:
        server["allow_tools"] = list(dict.fromkeys(body.allow_tools))
        # A manual checkbox edit is deliberately a custom selection, not a silent
        # mutation of whichever named preset happened to be active.
        server.pop("active_tool_preset", None)
    if body.tool_policy is not None:
        server["tool_policy"] = {
            tool_name: policy.model_dump(exclude_none=True)
            for tool_name, policy in body.tool_policy.items()
        }
    if body.tool_timeouts_s is not None:
        server["tool_timeouts_s"] = _normalize_tool_timeouts(body.tool_timeouts_s)
    if body.tool_presets is not None:
        server["tool_presets"] = _normalize_tool_presets(body.tool_presets)
        current = str(server.get("active_tool_preset") or "")
        if current and current not in {item["name"] for item in server["tool_presets"]}:
            server.pop("active_tool_preset", None)
    if body.active_tool_preset is not None:
        selected = body.active_tool_preset.strip()
        if not selected:
            server.pop("active_tool_preset", None)
        else:
            presets = _normalize_tool_presets(server.get("tool_presets"))
            preset = next((item for item in presets if item["name"] == selected), None)
            if preset is None:
                raise HTTPException(status_code=422, detail=f"工具预设不存在: {selected}")
            server["active_tool_preset"] = selected
            server["allow_tools"] = list(preset["tools"])
    if body.headers is not None:
        if not all(key.strip() and value for key, value in body.headers.items()):
            raise HTTPException(status_code=422, detail="headers 的键和值都必须是非空字符串")
        server["headers"] = dict(body.headers)
    if body.tool_timeout_s is not None:
        server["tool_timeout_s"] = max(1, min(660, float(body.tool_timeout_s)))
    if body.use_proxy is not None:
        server["use_proxy"] = bool(body.use_proxy)
    try:
        if "metadata_mapping" in body.model_fields_set:
            if body.metadata_mapping is None:
                server.pop("metadata_mapping", None)
            else:
                server["metadata_mapping"] = mcp_client.normalize_metadata_mapping(
                    body.metadata_mapping.model_dump(exclude_none=True)
                )
        if "metadata_overrides" in body.model_fields_set:
            if body.metadata_overrides is None:
                server.pop("metadata_overrides", None)
            else:
                normalized_overrides = mcp_client.normalize_metadata_overrides({
                    tool_name: entry.model_dump(exclude_none=True)
                    for tool_name, entry in body.metadata_overrides.items()
                })
                if normalized_overrides:
                    server["metadata_overrides"] = normalized_overrides
                else:
                    server.pop("metadata_overrides", None)
        if "domain_selector" in body.model_fields_set:
            if body.domain_selector is None:
                server.pop("domain_selector", None)
            else:
                server["domain_selector"] = mcp_client.normalize_domain_selector(
                    body.domain_selector.model_dump(exclude_none=True)
                )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _prune_tool_policy(server)
    if bool(mcp_cfg.get("require_local_policy", False)) and bool(server.get("enabled", True)):
        runtime = mcp_client.server_runtime(name)
        runtime_tools = runtime.get("tools") if bool(runtime.get("connected", False)) else []
        if isinstance(runtime_tools, list):
            _fill_missing_tool_policy_defaults(server, runtime_tools)
            _prune_tool_policy(server)
    await _ensure_local_policy_before_write(server, mcp_cfg, mcp_client)
    _write_config(full_cfg)
    config_loader.reload_config()
    # Brief 115 根治：同上，走信号队列热重载，由 server 专属常驻 task 自己关闭/重连。
    reload_ok = await mcp_client.reload_server_from_config(name)
    payload = _mcp_reload_result(
        name,
        reload_ok,
        success_message="MCP server 配置已更新并热重载",
        connection_failed_message="配置已保存并热重载，但未能连接",
        restart_message="配置已保存，但 MCP 热重载失败，需要重启服务",
    )
    payload["server"] = _server_view(
        server,
        require_local_policy=bool(mcp_cfg.get("require_local_policy", False)),
        session_exposed_names=_current_mcp_exposure_names(),
    )
    return payload


@router.post("/settings/mcp/{name}/reconnect", summary="按当前配置重新连接一个 MCP server")
async def reconnect_mcp_server(name: str, _auth=Depends(require_scopes("admin"))):
    if not _NAME_RE.fullmatch(name):
        raise HTTPException(status_code=422, detail="非法 server name")
    full_cfg = _read_config()
    mcp_cfg = full_cfg.get("mcp_servers") or {}
    server = next(
        (item for item in (mcp_cfg.get("servers") or []) if isinstance(item, dict) and item.get("name") == name),
        None,
    )
    if server is None:
        raise HTTPException(status_code=404, detail="MCP server 不存在")
    from core import mcp_client

    reload_ok = await mcp_client.reload_server_from_config(name)
    payload = _mcp_reload_result(
        name,
        reload_ok,
        success_message="MCP server 已重新连接",
        connection_failed_message="已尝试重新连接，但未能连上",
        restart_message="重新连接失败，需要重启服务",
    )
    payload["server"] = _server_view(
        server,
        require_local_policy=bool(mcp_cfg.get("require_local_policy", False)),
        session_exposed_names=_current_mcp_exposure_names(),
    )
    return payload


@router.delete("/settings/mcp/{name}", summary="删除一个 MCP server 并断开其运行态")
async def delete_mcp_server(name: str, _auth=Depends(require_scopes("admin"))):
    if not _NAME_RE.fullmatch(name):
        raise HTTPException(status_code=422, detail="非法 server name")
    full_cfg = _read_config()
    mcp_cfg = full_cfg.setdefault("mcp_servers", {})
    servers = mcp_cfg.setdefault("servers", [])
    remaining = [item for item in servers if not isinstance(item, dict) or item.get("name") != name]
    if len(remaining) == len(servers):
        raise HTTPException(status_code=404, detail="MCP server 不存在")
    mcp_cfg["servers"] = remaining
    _write_config(full_cfg)

    from core import config_loader, mcp_client

    config_loader.reload_config()
    # sync 会向已被删除的 server owner 发 shutdown，摘除动态工具并关闭连接。
    await mcp_client.sync_mcp_servers()
    return {"message": "MCP server 已删除并断开", "deleted": name}
