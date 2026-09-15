"""
core/mcp_client.py — MCP (Model Context Protocol) 客户端（Brief 29 · 4）

只接外部工具，不接 resources/prompts、不接外部记忆库（见 cc-tasks/29 定位说明：
外接记忆绕过 prompt 层注入与固化链，会裂成两套真相；MCP 只用于外部工具）。

生命周期：main.py 启动时调 init_mcp_servers()，为每个已启用 server 建立 ClientSession、
list_tools，动态注册进 core.tool_dispatcher._TOOL_REGISTRY（name="mcp__{server}__{tool}"，
category="mcp"）。单 server 初始化失败只跳过该 server（log + 继续），不影响其他 server 与主流程。

MCP 默认只在 Path C tool loop 暴露。管理员也可显式把 ``mcp`` 加入共享的
``tool_exposure.path_a``，让 QQ、desktop、mobile 的预探针一致看到同一批经过
本地 policy/proficiency/allowlist 过滤后的 schema；这不会绕过任一执行闸门。

action_trace 落痕在 tool_dispatcher.execute() 的收口埋点自动生效，本模块不新增记账代码；
MCP 工具注册时不声明 trace_args，参数不落痕（防外部 server 的敏感入参入盘）。

连接生命周期（Brief 115 根治）：每个 server 的连接只由它专属的常驻 task（_owner_loop）
打开和关闭——anyio 的结构化并发规则要求 cancel scope 必须在打开它的同一个 task 里关闭，
跨 task 调用 AsyncExitStack.aclose() 会误伤祖先 scope，曾经把整个 uvicorn 主循环一起
取消带崩进程。管理面 / 总开关触发的 disconnect_server / reload_server_from_config /
sync_mcp_servers 现在都只是把信号丢进对应 server 的队列（_send_command），立即返回，
真正的 aclose()/重连都在专属 task 里执行，调用方可以是任何 task。
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
import ipaddress
import json
import logging
import os
import re
import time
import uuid
from typing import Awaitable, Callable
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_RESULT_CHAR_CAP = 2000
_ENV_REF_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_SUPPORTED_TRANSPORTS = ("stdio", "sse", "streamable-http")
_TRANSPORT_ALIASES = {"http": "streamable-http"}
_MCP_METADATA_MAX_DOMAINS = 8
_MCP_METADATA_DOMAIN_MAX_CHARS = 48
_MCP_METADATA_DOMAINS_TOTAL_MAX_CHARS = 256
_MCP_METADATA_INTERACTIONS = frozenset({"read", "write", "mixed"})
_MCP_METADATA_OVERRIDE_MODES = frozenset({"remote", "override", "ignore"})

# server_name → handle；进程内单例，_connect_server 填充，_close_server 清空。
_servers: dict[str, "_ServerHandle"] = {}
# 管理面读取的运行态；不落盘，进程重启后自然回到“尚未尝试”。
_server_status: dict[str, dict] = {}
# server_name → 专属常驻 task 的句柄；只有它自己允许 open/close 该 server 的 AsyncExitStack。
_owners: dict[str, "_ServerOwner"] = {}


@dataclass
class _ServerHandle:
    name: str
    cfg: dict
    stack: AsyncExitStack
    session: object  # mcp.ClientSession，延迟导入避免模块级依赖未安装时报错
    tool_names: list[str] = field(default_factory=list)
    tool_details: list[dict] = field(default_factory=list)
    pending_tool_names: list[str] = field(default_factory=list)


@dataclass
class _ServerOwner:
    """一个 server 专属的常驻 task：唯一允许打开/关闭其 AsyncExitStack 的执行体。"""
    task: "asyncio.Task | None"
    queue: "asyncio.Queue[tuple]"
    ready: asyncio.Event  # 首次连接尝试（成功或失败）完成后 set


def _get_mcp_config() -> dict:
    from core.config_loader import get_config
    return get_config().get("mcp_servers", {}) or {}


_LOCAL_EFFECTS = frozenset({"read", "write", "actuate", "emergency", "unrestricted"})
_READ_TOOL_WORDS = frozenset({"get", "list", "read", "search", "status"})
_WRITE_TOOL_WORDS = frozenset({
    "create", "update", "set", "send", "delete", "remove", "stop",
})


_MCP_AUDIT_ID: ContextVar[str] = ContextVar("mcp_audit_id", default="")


@contextmanager
def audit_context(audit_id: str):
    """Attach an admin-console audit id to MCP API-call ledger entries."""
    token = _MCP_AUDIT_ID.set(str(audit_id)[:128])
    try:
        yield
    finally:
        _MCP_AUDIT_ID.reset(token)


class McpOutcomeUnknown(RuntimeError):
    """A physical action may have reached the server, but its result was lost."""

    def __init__(self, *, tool_name: str, request_id: str):
        self.payload = {
            "outcome": "outcome_unknown",
            "tool": tool_name,
            "request_id": request_id,
            "message": "动作可能已经送达，禁止自动重放；请调用 hardware_status 或人工确认设备状态。",
        }
        super().__init__("MCP actuation outcome is unknown")

    def safe_summary(self) -> str:
        return json.dumps(self.payload, ensure_ascii=False, separators=(",", ":"))


def _require_local_policy() -> bool:
    return bool(_get_mcp_config().get("require_local_policy", False))


def validate_local_tool_policy(
    server_cfg: dict, *, required: bool | None = None
) -> dict[str, dict] | None:
    """Return normalized trusted local policy, or None for legacy operation.

    MCP annotations describe a remote server's claim.  They never supply this
    local authorization data, so strict mode deliberately validates policy
    before any session or dynamic tool registration is created.
    """
    if required is None:
        required = _require_local_policy()
    if not required:
        return None

    name = str(server_cfg.get("name") or "<unnamed>")
    allow_tools = server_cfg.get("allow_tools")
    if not isinstance(allow_tools, list):
        raise ValueError(
            f"MCP server '{name}' require_local_policy=true 时 allow_tools 必须是列表"
        )
    if not all(isinstance(tool_name, str) and tool_name for tool_name in allow_tools):
        raise ValueError(f"MCP server '{name}' 的 allow_tools 必须全部是非空工具名")

    raw_policy = server_cfg.get("tool_policy") or {}
    if not isinstance(raw_policy, dict):
        raise ValueError(f"MCP server '{name}' 的 tool_policy 必须是对象")

    normalized: dict[str, dict] = {}
    for tool_name, entry in raw_policy.items():
        if tool_name not in allow_tools:
            continue
        if not isinstance(entry, dict):
            raise ValueError(f"MCP server '{name}' 的 tool_policy.{tool_name} 必须是对象")
        effect = entry.get("effect")
        if effect not in _LOCAL_EFFECTS:
            allowed = ", ".join(sorted(_LOCAL_EFFECTS))
            raise ValueError(
                f"MCP server '{name}' 工具 '{tool_name}' 的 effect 无效；仅允许 {allowed}"
            )
        configured_confirm = entry.get("require_confirm")
        if configured_confirm is not None and not isinstance(configured_confirm, bool):
            raise ValueError(
                f"MCP server '{name}' 工具 '{tool_name}' 的 require_confirm 必须是 bool"
            )
        configured_idempotent = entry.get("idempotent", False)
        if not isinstance(configured_idempotent, bool):
            raise ValueError(
                f"MCP server '{name}' 工具 '{tool_name}' 的 idempotent 必须是 bool"
            )
        configured_ui_label = entry.get("ui_label", "")
        if configured_ui_label is not None and not isinstance(configured_ui_label, str):
            raise ValueError(
                f"MCP server '{name}' 工具 '{tool_name}' 的 ui_label 必须是字符串"
            )
        ui_label = str(configured_ui_label or "").strip()
        if len(ui_label) > 48:
            raise ValueError(
                f"MCP server '{name}' 工具 '{tool_name}' 的 ui_label 最多 48 字符"
            )
        if effect == "unrestricted" and not configured_idempotent:
            raise ValueError(
                f"MCP server '{name}' 工具 '{tool_name}' 的 unrestricted 模式必须显式 idempotent: true"
            )
        if effect in {"emergency", "unrestricted"}:
            if configured_confirm is True:
                logger.warning(
                    "[mcp_client] MCP %s 工具忽略 require_confirm=true: server=%s tool=%s",
                    effect,
                    name, tool_name,
                )
            require_confirm = False
        elif configured_confirm is not None:
            require_confirm = configured_confirm
        else:
            require_confirm = False
        normalized[tool_name] = {
            "effect": effect,
            "require_confirm": require_confirm,
            "idempotent": configured_idempotent,
            "ui_label": ui_label,
        }

    extras = sorted(set(raw_policy) - set(allow_tools))
    if extras:
        logger.warning(
            "[mcp_client] MCP tool_policy 含未白名单工具，已忽略: server=%s tools=%s",
            name, extras,
        )
    return normalized


def _validated_local_tool_policy(server_cfg: dict) -> dict[str, dict] | None:
    """Use the configured strict-policy setting for runtime registration."""
    return validate_local_tool_policy(server_cfg)


def _expand_env_refs(value: str, *, field: str) -> str:
    """Expand local environment references without ever persisting their value."""
    def _replace(match: re.Match[str]) -> str:
        env_name = match.group(1)
        env_value = os.environ.get(env_name)
        if env_value is None:
            raise ValueError(f"{field} 环境变量未设置: {env_name}")
        return env_value

    return _ENV_REF_RE.sub(_replace, value)


def _expand_headers(raw_headers: object) -> dict[str, str] | None:
    """展开 HTTP MCP headers 中的 ${ENV_VAR}，缺失变量 fail-closed。"""
    if raw_headers is None:
        return None
    if not isinstance(raw_headers, dict):
        raise ValueError("headers 必须是字符串键值对象")
    resolved: dict[str, str] = {}
    for key, value in raw_headers.items():
        if not isinstance(key, str) or not key.strip() or not isinstance(value, str):
            raise ValueError("headers 的键和值都必须是非空字符串")

        resolved[key.strip()] = _expand_env_refs(value, field="headers")
    return resolved


def _annotation_bool(annotations: object, *names: str) -> bool | None:
    if isinstance(annotations, dict):
        for name in names:
            if name in annotations:
                return annotations[name] if isinstance(annotations[name], bool) else None
        return None
    for name in names:
        value = getattr(annotations, name, None)
        if isinstance(value, bool):
            return value
    return None


def suggest_tool_policy(
    tool_name: str, description: str = "", annotations: object = None,
) -> dict:
    """Produce a non-authoritative local policy suggestion for an MCP tool.

    The result is display-only until an administrator writes a matching local
    ``tool_policy`` entry.  In particular, remote annotations never authorize
    registration or execution on their own.
    """
    read_only = _annotation_bool(annotations, "readOnlyHint", "read_only_hint")
    destructive = _annotation_bool(annotations, "destructiveHint", "destructive_hint")
    if read_only is True:
        return {
            "effect": "read", "source": "annotation.readOnlyHint",
            "status": "suggested", "high_risk": False, "require_confirm": False,
        }
    if destructive is True:
        return {
            "effect": "write", "source": "annotation.destructiveHint",
            "status": "suggested", "high_risk": True, "require_confirm": True,
        }
    if read_only is False:
        return {
            "effect": "write", "source": "annotation.readOnlyHint=false",
            "status": "suggested", "high_risk": False, "require_confirm": False,
        }

    words = set(re.findall(r"[a-z]+", f"{tool_name} {description}".lower()))
    if words & _WRITE_TOOL_WORDS:
        return {
            "effect": "write", "source": "name_description",
            "status": "suggested", "high_risk": False, "require_confirm": False,
        }
    if words & _READ_TOOL_WORDS:
        return {
            "effect": "read", "source": "name_description",
            "status": "suggested", "high_risk": False, "require_confirm": False,
        }
    return {
        "effect": None, "source": "unknown", "status": "confirmation_required",
        "high_risk": False, "require_confirm": False,
    }


def _contains_control_chars(value: str) -> bool:
    return any(ord(char) < 32 or 127 <= ord(char) < 160 for char in value)


def normalize_mcp_domains(
    raw: object, *, field: str = "domains", allow_empty: bool = True,
) -> list[str]:
    """Validate authored domain configuration and return a bounded stable list."""
    if not isinstance(raw, list):
        raise ValueError(f"{field} 必须是字符串数组")
    if len(raw) > _MCP_METADATA_MAX_DOMAINS:
        raise ValueError(f"{field} 最多 {_MCP_METADATA_MAX_DOMAINS} 项")
    domains: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            raise ValueError(f"{field} 必须全部是字符串")
        domain = item.strip()
        if not domain:
            raise ValueError(f"{field} 不能包含空字符串")
        if len(domain) > _MCP_METADATA_DOMAIN_MAX_CHARS:
            raise ValueError(
                f"{field} 单项最多 {_MCP_METADATA_DOMAIN_MAX_CHARS} 字符"
            )
        if _contains_control_chars(domain):
            raise ValueError(f"{field} 不能包含控制字符")
        domains.append(domain)
    domains = sorted(set(domains))
    if not allow_empty and not domains:
        raise ValueError(f"{field} 不能为空")
    if sum(len(domain) for domain in domains) > _MCP_METADATA_DOMAINS_TOTAL_MAX_CHARS:
        raise ValueError(
            f"{field} 总长度最多 {_MCP_METADATA_DOMAINS_TOTAL_MAX_CHARS} 字符"
        )
    return domains


def normalize_metadata_mapping(raw: object) -> dict | None:
    """Validate one server's generic metadata mapping without vendor knowledge."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("metadata_mapping 必须是对象")
    namespace = str(raw.get("namespace") or "").strip()
    if not namespace or len(namespace) > 160 or _contains_control_chars(namespace):
        raise ValueError("metadata_mapping.namespace 必须是 1-160 字符的安全字符串")
    raw_versions = raw.get("schema_versions")
    if not isinstance(raw_versions, list) or not raw_versions or len(raw_versions) > 16:
        raise ValueError("metadata_mapping.schema_versions 必须是 1-16 项数组")
    versions: list[int | str] = []
    for value in raw_versions:
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            raise ValueError("metadata_mapping.schema_versions 只允许整数或字符串")
        normalized = value.strip() if isinstance(value, str) else value
        if normalized == "" or (isinstance(normalized, str) and len(normalized) > 32):
            raise ValueError("metadata_mapping.schema_versions 含无效版本")
        if normalized not in versions:
            versions.append(normalized)

    def _field_name(key: str, default: str) -> str:
        value = str(raw.get(key) or default).strip()
        if not value or len(value) > 64 or _contains_control_chars(value):
            raise ValueError(f"metadata_mapping.{key} 必须是 1-64 字符的安全字符串")
        return value

    return {
        "namespace": namespace,
        "schema_versions": versions,
        "schema_version_field": _field_name("schema_version_field", "schema_version"),
        "domains_field": _field_name("domains_field", "domains"),
        "interaction_field": _field_name("interaction_field", "interaction"),
    }


def normalize_metadata_overrides(raw: object) -> dict[str, dict]:
    """Validate exact ``remote tool name -> local metadata mode`` overrides."""
    if raw is None:
        return {}
    if not isinstance(raw, dict) or len(raw) > 200:
        raise ValueError("metadata_overrides 必须是不超过 200 项的对象")
    normalized: dict[str, dict] = {}
    for tool_name, entry in raw.items():
        if (
            not isinstance(tool_name, str)
            or not tool_name
            or len(tool_name) > 128
            or _contains_control_chars(tool_name)
        ):
            raise ValueError("metadata_overrides 的工具名必须是安全的非空字符串")
        if not isinstance(entry, dict):
            raise ValueError(f"metadata_overrides.{tool_name} 必须是对象")
        mode = str(entry.get("mode") or "remote")
        if mode not in _MCP_METADATA_OVERRIDE_MODES:
            raise ValueError(
                f"metadata_overrides.{tool_name}.mode 仅允许 remote、override、ignore"
            )
        if mode == "override":
            domains = normalize_mcp_domains(
                entry.get("domains"),
                field=f"metadata_overrides.{tool_name}.domains",
                allow_empty=False,
            )
            normalized[tool_name] = {"mode": mode, "domains": domains}
        else:
            normalized[tool_name] = {"mode": mode}
    return normalized


def normalize_domain_selector(raw: object) -> dict | None:
    """Validate a per-server selector that can only narrow MCP schema exposure."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("domain_selector 必须是对象")
    include_unclassified = raw.get("include_unclassified", True)
    if not isinstance(include_unclassified, bool):
        raise ValueError("domain_selector.include_unclassified 必须是 bool")
    return {
        "domains": normalize_mcp_domains(
            raw.get("domains"), field="domain_selector.domains", allow_empty=False,
        ),
        "include_unclassified": include_unclassified,
    }


def _remote_tool_meta(tool) -> tuple[bool, object]:
    if isinstance(tool, dict):
        return ("_meta" in tool or "meta" in tool), tool.get("_meta", tool.get("meta"))
    sentinel = object()
    value = getattr(tool, "meta", sentinel)
    if value is sentinel:
        value = getattr(tool, "_meta", sentinel)
    return value is not sentinel and value is not None, None if value is sentinel else value


def _safe_schema_version(value: object) -> int | str | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        value = value.strip()
        if value and len(value) <= 32 and not _contains_control_chars(value):
            return value
    return None


def _sanitize_remote_domains(raw: object) -> tuple[list[str], bool]:
    """Return bounded valid values plus whether the field had the expected array shape."""
    if not isinstance(raw, list):
        return [], False
    domains: list[str] = []
    total_chars = 0
    for item in raw:
        if not isinstance(item, str):
            continue
        domain = item.strip()
        if (
            not domain
            or len(domain) > _MCP_METADATA_DOMAIN_MAX_CHARS
            or _contains_control_chars(domain)
            or domain in domains
            or len(domains) >= _MCP_METADATA_MAX_DOMAINS
            or total_chars + len(domain) > _MCP_METADATA_DOMAINS_TOTAL_MAX_CHARS
        ):
            continue
        domains.append(domain)
        total_chars += len(domain)
    return sorted(domains), True


def summarize_tool_metadata(tool, server_cfg: dict) -> dict:
    """Parse optional remote classification into a bounded, fail-soft summary."""
    summary = {
        "remote_domains": [],
        "remote_interaction": "unknown",
        "metadata_source": "none",
        "metadata_status": "absent",
        "metadata_schema_version": None,
        "final_domains": [],
    }
    present, raw_meta = _remote_tool_meta(tool)
    if present:
        if not isinstance(raw_meta, dict):
            summary["metadata_status"] = "invalid"
        else:
            try:
                mapping = normalize_metadata_mapping(server_cfg.get("metadata_mapping"))
            except ValueError as exc:
                logger.warning(
                    "[mcp_client] metadata_mapping 无效，按未识别处理: server=%s error=%s",
                    server_cfg.get("name"), exc,
                )
                mapping = None
            if mapping is None:
                summary["metadata_status"] = "unrecognized"
            elif mapping["namespace"] not in raw_meta:
                summary["metadata_status"] = "unrecognized"
            else:
                payload = raw_meta.get(mapping["namespace"])
                if not isinstance(payload, dict):
                    summary["metadata_status"] = "invalid"
                else:
                    version = _safe_schema_version(payload.get(mapping["schema_version_field"]))
                    summary["metadata_schema_version"] = version
                    if version not in mapping["schema_versions"]:
                        summary["metadata_status"] = "unrecognized"
                    else:
                        domains, valid_shape = _sanitize_remote_domains(
                            payload.get(mapping["domains_field"])
                        )
                        if not valid_shape:
                            summary["metadata_status"] = "invalid"
                        else:
                            interaction = payload.get(mapping["interaction_field"])
                            summary["remote_domains"] = domains
                            summary["remote_interaction"] = (
                                interaction
                                if interaction in _MCP_METADATA_INTERACTIONS
                                else "unknown"
                            )
                            summary["metadata_source"] = "remote"
                            summary["metadata_status"] = "recognized"
                            summary["final_domains"] = list(domains)

    tool_name = str(
        (tool.get("name") if isinstance(tool, dict) else getattr(tool, "name", "")) or ""
    )
    raw_overrides = server_cfg.get("metadata_overrides")
    if isinstance(raw_overrides, dict) and tool_name in raw_overrides:
        try:
            override = normalize_metadata_overrides({tool_name: raw_overrides[tool_name]})[
                tool_name
            ]
        except ValueError as exc:
            logger.warning(
                "[mcp_client] metadata override 无效，已忽略: server=%s tool=%s error=%s",
                server_cfg.get("name"), tool_name, exc,
            )
        else:
            if override["mode"] == "override":
                summary["final_domains"] = list(override["domains"])
                summary["metadata_source"] = "local_override"
                summary["metadata_status"] = "overridden"
            elif override["mode"] == "ignore":
                summary["final_domains"] = []
                summary["metadata_source"] = "local_override"
                summary["metadata_status"] = "overridden"
    return summary


def _tool_details(listed, server_cfg: dict) -> list[dict]:
    details: list[dict] = []
    for tool in (getattr(listed, "tools", None) or []):
        try:
            if not getattr(tool, "name", None):
                continue
            name = str(tool.name)
            description = str(tool.description or "")
            suggestion = suggest_tool_policy(
                name, description, getattr(tool, "annotations", None)
            )
            details.append({
                "name": name,
                "suggestion": suggestion,
                **summarize_tool_metadata(tool, server_cfg),
            })
        except Exception as exc:
            logger.warning(
                "[mcp_client] 单个工具摘要失败，继续处理其他工具: server=%s error=%s",
                server_cfg.get("name"), exc,
            )
    return details


def _record_init(name: str, *, ok: bool, error: str = "", tools: list[dict] | None = None) -> None:
    state = _server_status.setdefault(name, {})
    state.update({
        "last_init_ts": time.time(),
        "last_init_ok": bool(ok),
        "last_init_error": str(error)[:300],
    })
    if tools is not None:
        state["tools"] = tools


def _record_call(name: str, tool_name: str, *, ok: bool, error: str = "") -> None:
    _server_status.setdefault(name, {}).update({
        "last_call_ts": time.time(),
        "last_call_ok": bool(ok),
        "last_call_tool": tool_name,
        "last_call_error": str(error)[:300],
    })


def server_runtime(name: str) -> dict:
    """返回不含配置密钥的单 server 运行状态，供 settings_mcp 只读展示。"""
    state = dict(_server_status.get(name, {}))
    handle = _servers.get(name)
    state["connected"] = handle is not None
    if handle is not None:
        state["tools"] = list(handle.tool_details)
        state["registered_tools"] = list(handle.tool_names)
        state["pending_confirmation_tools"] = list(handle.pending_tool_names)
    else:
        state.setdefault("tools", [])
        state["registered_tools"] = []
        state["pending_confirmation_tools"] = []
    return state


async def init_mcp_servers() -> None:
    from core.no_outbound import assert_outbound_allowed
    assert_outbound_allowed("mcp_init")
    """启动时为每个已启用 server 起专属常驻 task，各自建立 session + list_tools 并注册工具。

    单 server 失败隔离：某个 server 连不上只跳过它，log warning，不影响其他 server 或主流程。
    mcp_servers.enabled=false（默认）时整体跳过，零开销、零行为变化。等待每个专属 task
    的首次连接尝试完成后再返回，行为上与旧版"同步逐个 connect"一致，只是连接动作已经
    转移到各自的专属 task 里执行（Brief 115 根治：后续 reload/disconnect 才能安全地在
    同一个 task 里关闭）。
    """
    cfg = _get_mcp_config()
    if not cfg.get("enabled", False):
        return
    try:
        import mcp  # noqa: F401 — 依赖存在性检查，SDK 未安装时 fail-soft 跳过
    except ImportError:
        logger.warning("[mcp_client] mcp_servers.enabled=true 但未安装 mcp SDK（pip install mcp），跳过全部 MCP server")
        return

    servers = cfg.get("servers") or []
    owners: list[_ServerOwner] = []
    for server_cfg in servers:
        name = server_cfg.get("name")
        if not name:
            logger.warning("[mcp_client] server 配置缺少 name，跳过: %s", server_cfg)
            continue
        if not server_cfg.get("enabled", True):
            continue
        owners.append(_spawn_owner(name, server_cfg))
    for owner in owners:
        await owner.ready.wait()


def _spawn_owner(name: str, initial_cfg: dict | None) -> _ServerOwner:
    """为一个 server 起专属常驻 task，独占持有它的连接生命周期。

    initial_cfg 非 None 时，task 起来后立即尝试连接一次；为 None 时只起一个空壳 task，
    等着队列里的第一条指令（用于"配置存在但当前未连接"的占位场景）。
    """
    queue: "asyncio.Queue[tuple]" = asyncio.Queue()
    ready = asyncio.Event()
    owner = _ServerOwner(task=None, queue=queue, ready=ready)
    _owners[name] = owner
    owner.task = asyncio.create_task(
        _owner_loop(name, queue, ready, initial_cfg), name=f"mcp-owner-{name}",
    )
    return owner


async def _owner_loop(
    name: str, queue: "asyncio.Queue[tuple]", ready: asyncio.Event, initial_cfg: dict | None,
) -> None:
    """server 专属常驻 task 的主体：本 task 是唯一允许 open/close 该 server AsyncExitStack
    的执行体。外部一律通过 queue 发信号，不跨 task 直接碰 stack（Brief 115 根因见模块docstring）。
    """
    if initial_cfg is not None:
        try:
            await _connect_server(name, initial_cfg)
        except Exception as exc:
            logger.warning("[mcp_client] server '%s' 初始化失败，跳过（不影响其他 server）: %s", name, exc)
    ready.set()
    while True:
        cmd, payload, done = await queue.get()
        try:
            if cmd == "shutdown":
                await _close_server(name)
                _owners.pop(name, None)
                _resolve(done, None)
                return
            if cmd == "reload":
                await _close_server(name)
                ok = True
                if payload is not None:
                    try:
                        await _connect_server(name, payload)
                    except Exception as exc:
                        logger.warning("[mcp_client] server '%s' 热重载失败: %s", name, exc)
                        ok = False
                _resolve(done, ok)
        except BaseException as exc:
            logger.error("[mcp_client] server '%s' 专属 task 处理指令 '%s' 异常: %s", name, cmd, exc)
            _resolve(done, False)


def _resolve(done: "asyncio.Future | None", value: object) -> None:
    if done is not None and not done.done():
        done.set_result(value)


async def _send_command(name: str, cmd: str, payload: dict | None = None, *, wait: bool = True) -> object:
    """把一条指令丢进 server 专属 task 的队列；默认等它在自己的 task 里处理完。

    这一步只是 put + 可选 await 一个 Future，不涉及跨 task 关闭 cancel scope，调用方可以是
    任意 task（HTTP 请求 task、总开关同步等）——真正的 aclose()/连接动作发生在
    _owner_loop 所在的专属 task 里。``wait=False`` 只保证信号入队，不等连接完成。
    """
    owner = _owners.get(name)
    if owner is None:
        return None
    done = asyncio.get_running_loop().create_future() if wait else None
    await owner.queue.put((cmd, payload, done))
    if not wait:
        return None
    return await done


def _normalized_transport(server_cfg: dict) -> str:
    """Return the canonical transport name, retaining the old ``http`` alias."""
    transport = server_cfg.get("transport", "stdio")
    if not isinstance(transport, str):
        raise ValueError(
            "transport 必须是字符串（支持 stdio | sse | streamable-http；http 为兼容别名）"
        )
    transport = _TRANSPORT_ALIASES.get(transport, transport)
    if transport not in _SUPPORTED_TRANSPORTS:
        raise ValueError(
            f"未知 transport: {transport!r}（支持 stdio | sse | streamable-http；http 为兼容别名）"
        )
    return transport


def _transport_url(server_cfg: dict, transport: str) -> str:
    url = server_cfg.get("url")
    if not isinstance(url, str) or not url.strip():
        raise ValueError(f"{transport} transport 需要非空 url")
    # Some compatible gateways authenticate with a path segment instead of an
    # HTTP header.  Keep that protocol shape, but resolve the secret only at
    # connection time from the protected local environment.
    return _expand_env_refs(url.strip(), field=f"{transport} transport URL")


def is_local_mcp_url(url: str) -> bool:
    """Whether an HTTP MCP endpoint is loopback-local and must bypass proxies."""
    host = urlparse(url).hostname
    if not host:
        return False
    host = host.rstrip(".").lower()
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_loopback or address.is_unspecified


def _mcp_proxy_url(server_cfg: dict, url: str) -> str | None:
    """Resolve an explicitly opted-in remote MCP proxy; never inherit env proxies."""
    if is_local_mcp_url(url) or not server_cfg.get("use_proxy", False):
        return None

    from core.config_loader import get_config

    proxy_cfg = get_config().get("proxy", {}) or {}
    if not proxy_cfg.get("enabled", False):
        raise ValueError("MCP server 已启用代理，但全局 proxy.enabled=false")
    proxy_key = "https" if urlparse(url).scheme == "https" else "http"
    proxy_url = proxy_cfg.get(proxy_key)
    if not isinstance(proxy_url, str) or not proxy_url.strip():
        raise ValueError(f"MCP server 已启用代理，但全局 proxy.{proxy_key} 未配置")
    return proxy_url.strip()


def _create_mcp_http_client(
    *, headers: dict[str, str] | None = None, timeout=None, auth=None, proxy_url: str | None = None,
):
    """Create an MCP-compatible HTTPX client without ambient environment proxies."""
    import httpx
    from mcp.shared._httpx_utils import MCP_DEFAULT_SSE_READ_TIMEOUT, MCP_DEFAULT_TIMEOUT

    if timeout is None:
        timeout = httpx.Timeout(MCP_DEFAULT_TIMEOUT, read=MCP_DEFAULT_SSE_READ_TIMEOUT)
    kwargs = {
        "follow_redirects": True,
        "timeout": timeout,
        "trust_env": False,
    }
    if headers is not None:
        kwargs["headers"] = headers
    if auth is not None:
        kwargs["auth"] = auth
    if proxy_url is not None:
        kwargs["proxy"] = proxy_url
    return httpx.AsyncClient(**kwargs)


def _mcp_http_client_factory(server_cfg: dict, url: str):
    """Return the SDK factory shape used by both SSE and Streamable HTTP clients."""
    proxy_url = _mcp_proxy_url(server_cfg, url)

    def _factory(headers=None, timeout=None, auth=None):
        return _create_mcp_http_client(
            headers=headers, timeout=timeout, auth=auth, proxy_url=proxy_url,
        )

    return _factory


async def _open_stdio_transport(stack: AsyncExitStack, server_cfg: dict):
    from mcp import StdioServerParameters
    from mcp.client.stdio import stdio_client

    command = server_cfg.get("command") or []
    if not command:
        raise ValueError("stdio transport 需要非空 command 数组")
    params = StdioServerParameters(command=command[0], args=list(command[1:]))
    return await stack.enter_async_context(stdio_client(params))


async def _open_sse_transport(stack: AsyncExitStack, server_cfg: dict):
    from mcp.client.sse import sse_client

    url = _transport_url(server_cfg, "sse")
    headers = _expand_headers(server_cfg.get("headers"))
    return await stack.enter_async_context(
        sse_client(
            url,
            headers=headers,
            httpx_client_factory=_mcp_http_client_factory(server_cfg, url),
        )
    )


async def _open_streamable_http_transport(stack: AsyncExitStack, server_cfg: dict):
    from mcp.client import streamable_http

    url = _transport_url(server_cfg, "streamable-http")
    headers = _expand_headers(server_cfg.get("headers"))
    http_client_factory = _mcp_http_client_factory(server_cfg, url)

    # Current MCP SDKs expose streamable_http_client(), whose headers belong on
    # a caller-owned HTTP client.  Keep the older helper as a fallback for the
    # project's declared mcp>=1.9 compatibility range.
    current_client = getattr(streamable_http, "streamable_http_client", None)
    if current_client is not None:
        http_client = await stack.enter_async_context(http_client_factory(headers=headers))
        read, write, _get_session_id = await stack.enter_async_context(
            current_client(url, http_client=http_client)
        )
        return read, write

    legacy_client = streamable_http.streamablehttp_client
    read, write, _get_session_id = await stack.enter_async_context(
        legacy_client(url, headers=headers, httpx_client_factory=http_client_factory)
    )
    return read, write


async def _open_transport(stack: AsyncExitStack, server_cfg: dict):
    """Open one configured MCP transport inside the caller-owned exit stack."""
    transport = _normalized_transport(server_cfg)
    if transport == "stdio":
        return await _open_stdio_transport(stack, server_cfg)
    if transport == "sse":
        return await _open_sse_transport(stack, server_cfg)
    return await _open_streamable_http_transport(stack, server_cfg)


async def _connect_server(name: str, server_cfg: dict) -> None:
    from mcp import ClientSession
    from core.tool_dispatcher import _TOOL_REGISTRY

    try:
        local_policy = _validated_local_tool_policy(server_cfg)
    except Exception as exc:
        _record_init(name, ok=False, error=str(exc))
        raise RuntimeError(f"MCP server '{name}' 初始化失败: {exc}") from exc
    if local_policy is None:
        logger.warning(
            "[mcp_client] MCP server 正运行于 legacy unclassified policy: server=%s",
            name,
        )
    stack = AsyncExitStack()
    try:
        read, write = await _open_transport(stack, server_cfg)
        session = await stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        listed = await session.list_tools()
    except BaseException as exc:
        # HTTP transport 内部用 anyio task group 实现 streaming；初始化阶段被取消
        # （超时/请求中断/重载竞态）时，anyio 可能抛 CancelledError 或
        # BaseExceptionGroup，两者都不是 Exception 子类，callers 的 `except Exception`
        # 兜底接不住，会一路崩到 ASGI 应用层。这里统一收窄成普通 Exception 再上抛，
        # 让现有的 fail-soft 调用方（init/sync/reload）都能正常捕获。
        try:
            await stack.aclose()
        except BaseException as close_exc:
            logger.debug("[mcp_client] server '%s' 初始化失败后关闭 stack 出错: %s", name, close_exc)
        _record_init(name, ok=False, error=str(exc))
        raise RuntimeError(f"MCP server '{name}' 初始化失败: {exc}") from exc

    allow = set(server_cfg.get("allow_tools") or [])
    timeout_s = float(server_cfg.get("tool_timeout_s", 30))
    configured_timeouts = server_cfg.get("tool_timeouts_s") or {}
    if not isinstance(configured_timeouts, dict):
        logger.warning("[mcp_client] tool_timeouts_s 必须是对象，已忽略: server=%s", name)
        configured_timeouts = {}
    details = _tool_details(listed, server_cfg)
    metadata_by_name = {
        str(item.get("name") or ""): item for item in details if item.get("name")
    }
    handle = _ServerHandle(
        name=name, cfg=server_cfg, stack=stack, session=session, tool_details=details,
    )

    for tool in listed.tools:
        if local_policy is not None:
            if tool.name not in allow:
                continue
        elif allow and tool.name not in allow:
            continue
        if local_policy is not None and tool.name not in local_policy:
            # Strict local policy intentionally treats an allowlisted tool
            # without an explicit local confirmation as unavailable.
            handle.pending_tool_names.append(str(tool.name))
            continue
        reg_name = f"mcp__{name}__{tool.name}"
        if reg_name in _TOOL_REGISTRY:
            logger.warning("[mcp_client] 工具名与已注册工具冲突，MCP 侧让位: %s", reg_name)
            continue
        claimed_read_only = _tool_is_read_only(tool)
        suggestion = suggest_tool_policy(
            str(tool.name), str(tool.description or ""), getattr(tool, "annotations", None),
        )
        if local_policy is None:
            # Legacy keeps the old no-confirm behavior.  The conservative
            # write label is explicitly marked unclassified; it is never
            # inferred from a remote tool name or annotation.
            effect = "write"
            require_confirm = False
            ui_label = "外部工具"
        else:
            policy = local_policy[tool.name]
            effect = policy["effect"]
            require_confirm = policy["require_confirm"]
            idempotent = policy["idempotent"]
            ui_label = policy["ui_label"] or "外部工具"
        if local_policy is None:
            idempotent = False
        if claimed_read_only and effect != "read":
            logger.warning(
                "[mcp_client] MCP readOnlyHint 与本地 effect 冲突，本地策略优先: "
                "server=%s tool=%s effect=%s",
                name, tool.name, effect,
            )
        high_risk = bool(suggestion["high_risk"])
        metadata = metadata_by_name.get(str(tool.name)) or {
            "remote_domains": [],
            "remote_interaction": "unknown",
            "metadata_source": "none",
            "metadata_status": "invalid",
            "metadata_schema_version": None,
            "final_domains": [],
        }
        # Local allow_tools + a valid local policy are the authorization
        # boundary.  Remote annotations and effect labels remain observable but
        # cannot silently restore per-call confirmation over an explicit local
        # false.  Admins can still opt a specific tool into confirmation.
        effective_require_confirm = (
            False if effect in {"emergency", "unrestricted"} else require_confirm
        )
        _TOOL_REGISTRY[reg_name] = {
            "func": _make_tool_func(
                name,
                tool.name,
                _tool_timeout_s(configured_timeouts, str(tool.name), timeout_s),
                effect,
                idempotent,
            ),
            "description": tool.description or "",
            "dangerous": effective_require_confirm,
            "category": "mcp",
            "parameters": tool.inputSchema or {"type": "object", "properties": {}},
            "mcp_server": name,
            "mcp_tool": tool.name,
            "effect": effect,
            "require_confirm": effective_require_confirm,
            "mcp_claimed_read_only": claimed_read_only,
            "mcp_policy_legacy": local_policy is None,
            "mcp_idempotent": idempotent,
            "mcp_high_risk": high_risk,
            "ui_label": ui_label,
            "mcp_remote_domains": list(metadata["remote_domains"]),
            "mcp_remote_interaction": metadata["remote_interaction"],
            "mcp_metadata_source": metadata["metadata_source"],
            "mcp_metadata_status": metadata["metadata_status"],
            "mcp_metadata_schema_version": metadata["metadata_schema_version"],
            "mcp_domains": list(metadata["final_domains"]),
            # MCP annotations are optional.  Keep the read-only declaration so
            # the tool-loop can safely point models at server-provided docs /
            # inspection tools without guessing from a tool's name.
            "mcp_read_only": claimed_read_only,
        }
        handle.tool_names.append(reg_name)

    _servers[name] = handle
    _record_init(name, ok=True, tools=details)
    logger.info(
        "[mcp_client] server '%s' 已连接，注册 %d 个工具，待确认 %d 个",
        name, len(handle.tool_names), len(handle.pending_tool_names),
    )


def _tool_is_read_only(tool) -> bool:
    """Read the optional MCP ``readOnlyHint`` from dict or SDK-model annotations."""
    annotations = getattr(tool, "annotations", None)
    if isinstance(annotations, dict):
        return annotations.get("readOnlyHint") is True or annotations.get("read_only_hint") is True
    return (
        getattr(annotations, "readOnlyHint", None) is True
        or getattr(annotations, "read_only_hint", None) is True
    )


def _tool_timeout_s(configured_timeouts: dict, tool_name: str, default_timeout_s: float) -> float:
    raw_timeout = configured_timeouts.get(tool_name, default_timeout_s)
    try:
        timeout_s = float(raw_timeout)
    except (TypeError, ValueError):
        logger.warning("[mcp_client] 工具超时无效，回退 server 默认值: tool=%s", tool_name)
        return default_timeout_s
    if not 1 <= timeout_s <= 660:
        logger.warning("[mcp_client] 工具超时超出 1-660 秒，回退 server 默认值: tool=%s", tool_name)
        return default_timeout_s
    return timeout_s


async def test_server_config(server_cfg: dict) -> list[dict]:
    """连接并 list_tools 后立即关闭，供 URL 导入的提交前探测使用。"""
    try:
        import mcp  # noqa: F401 — 与 init 保持同一 SDK 前置条件
    except ImportError as exc:
        raise RuntimeError("未安装 mcp SDK，无法测试 MCP server") from exc

    stack = AsyncExitStack()
    try:
        read, write = await _open_transport(stack, server_cfg)
        from mcp import ClientSession
        session = await stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        return _tool_details(await session.list_tools(), server_cfg)
    except BaseException as exc:
        # 同 _connect_server：初始化阶段被取消可能抛非 Exception 子类，统一收窄，
        # 让 admin 路由的 `except Exception` 能正常转成 400 而不是让请求本身崩掉。
        raise RuntimeError(f"连接或初始化未完成: {exc}") from exc
    finally:
        try:
            await stack.aclose()
        except BaseException as close_exc:
            logger.debug("[mcp_client] test_server_config 关闭 stack 出错: %s", close_exc)


def _make_tool_func(
    server_name: str, tool_name: str, timeout_s: float, effect: str, idempotent: bool,
):
    async def _call(
        *,
        _tool_status_observer: Callable[..., Awaitable[None] | None] | None = None,
        **kwargs,
    ) -> str:
        return await _call_tool(
            server_name, tool_name, kwargs, timeout_s,
            effect=effect, idempotent=idempotent,
            status_observer=_tool_status_observer,
        )
    return _call


def _retry_allowed(*, effect: str, idempotent: bool, tool_name: str) -> bool:
    if effect == "read":
        return True
    if effect == "write":
        return idempotent
    return effect == "emergency" and idempotent and tool_name == "hardware_stop"


def _retry_limit(*, effect: str, idempotent: bool, tool_name: str) -> int:
    if effect == "unrestricted" and idempotent:
        return 3
    return 1 if _retry_allowed(effect=effect, idempotent=idempotent, tool_name=tool_name) else 0


async def _call_tool(
    server_name: str,
    tool_name: str,
    arguments: dict,
    timeout_s: float,
    *,
    effect: str = "",
    idempotent: bool = False,
    status_observer: Callable[..., Awaitable[None] | None] | None = None,
) -> str:
    from core.no_outbound import assert_outbound_allowed
    assert_outbound_allowed("mcp_tool")
    handle = _servers.get(server_name)
    if handle is None:
        raise RuntimeError(f"MCP server '{server_name}' 未连接")
    started = time.perf_counter()
    request_id = str(uuid.uuid4()) if effect in {"actuate", "emergency", "unrestricted"} else ""
    meta = {"request_id": request_id} if request_id else None

    async def _call_once(session):
        kwargs = {"meta": meta} if meta is not None else {}
        return await asyncio.wait_for(
            session.call_tool(tool_name, arguments, **kwargs), timeout=timeout_s
        )

    async def _notify_waiting(attempt: int) -> None:
        if status_observer is None:
            return
        result = status_observer("waiting", attempt=attempt)
        if hasattr(result, "__await__"):
            await result

    try:
        retry_limit = _retry_limit(effect=effect, idempotent=idempotent, tool_name=tool_name)
        for attempt in range(retry_limit + 1):
            try:
                result = await _call_once(handle.session)
                break
            except BaseException as exc:
                if attempt >= retry_limit:
                    if effect in {"actuate", "emergency", "unrestricted"}:
                        raise McpOutcomeUnknown(tool_name=tool_name, request_id=request_id) from exc
                    raise RuntimeError(f"MCP 工具调用失败: {exc}") from exc
                logger.warning(
                    "[mcp_client] 调用 %s.%s 失败，按幂等策略重连后重试（%d/%d）: %s request_id=%s",
                    server_name, tool_name, attempt + 1, retry_limit, exc, request_id or "-",
                )
                # This is a reconnect retry, not a separate action. The caller
                # retains one status id and updates only the attempt count.
                await _notify_waiting(attempt + 2)
                try:
                    await _reconnect_server(server_name)
                except BaseException as reconnect_exc:
                    logger.warning("[mcp_client] 重连 %s 失败: %s", server_name, reconnect_exc)
                handle = _servers.get(server_name)
                if handle is None:
                    if effect in {"actuate", "emergency", "unrestricted"}:
                        raise McpOutcomeUnknown(tool_name=tool_name, request_id=request_id) from exc
                    raise RuntimeError(f"MCP 工具调用失败且重连未恢复: {exc}") from exc
        text = _format_result(result)
    except BaseException as exc:
        outcome_unknown = isinstance(exc, McpOutcomeUnknown)
        _record_call(server_name, tool_name, ok=False, error=str(exc))
        from core.api_call_log import append as append_api_call
        append_api_call(
            caller=f"mcp__{server_name}__{tool_name}", purpose="mcp_tool", provider=server_name,
            model=tool_name, duration_ms=int((time.perf_counter() - started) * 1000), ok=False,
            output_hint="MCP action outcome unknown" if outcome_unknown else "MCP call failed",
            request_id=request_id,
            audit_id=_MCP_AUDIT_ID.get(),
        )
        if isinstance(exc, Exception):
            raise
        raise RuntimeError(f"MCP 工具调用未完成: {exc}") from exc
    _record_call(server_name, tool_name, ok=True)
    from core.api_call_log import append as append_api_call
    append_api_call(
        caller=f"mcp__{server_name}__{tool_name}", purpose="mcp_tool", provider=server_name,
        model=tool_name, duration_ms=int((time.perf_counter() - started) * 1000), ok=True,
        output_hint="MCP call succeeded",
        request_id=request_id,
        audit_id=_MCP_AUDIT_ID.get(),
    )
    return text


def _format_result(result) -> str:
    parts = []
    for item in getattr(result, "content", None) or []:
        text = getattr(item, "text", None)
        if text:
            parts.append(text)
    text = "\n".join(parts) if parts else "(无文本结果)"
    if len(text) > _RESULT_CHAR_CAP:
        text = text[:_RESULT_CHAR_CAP] + "…"
    if getattr(result, "isError", False):
        raise RuntimeError(f"MCP 工具返回错误: {text}")
    return text


async def _reconnect_server(name: str) -> None:
    """断线重连一次（不做后台心跳）：先摘除旧 handle 与其注册的工具条目，再重新连接。

    范围说明（Brief 115 §3）：这条路径由 _call_tool 在工具调用失败时触发，运行在触发
    调用的那个请求 task 里，不经过专属常驻 task 的信号队列——到这里时连接已经出错/死掉，
    关闭一个已经坏掉的 transport 不会像"跨 task 关闭一个健康连接"那样牵连祖先 cancel
    scope；本轮根治的范围是 disconnect_server / reload_server_from_config /
    sync_mcp_servers 这条管理面触发的线，这里维持原样。
    """
    handle = _servers.get(name)
    if handle is None:
        return
    cfg = handle.cfg
    await _close_server(name)
    try:
        await _connect_server(name, cfg)
    except Exception as e:
        logger.warning("[mcp_client] server '%s' 重连失败: %s", name, e)


async def _close_server(name: str) -> None:
    """在当前 task 里实际关闭一个 server 的连接、摘除其动态工具；配置文件不受影响。

    只应由该 server 专属的常驻 task（_owner_loop）调用——它是打开这个 AsyncExitStack
    的 task，关闭也必须在同一个 task 里做（anyio 结构化并发要求）。外部代码一律走
    disconnect_server()/reload_server_from_config() 发信号，不要直接调这个函数；唯一
    的例外是 _reconnect_server()（工具调用失败后的重连，见其 docstring 里的范围说明）。
    """
    handle = _servers.pop(name, None)
    if handle is None:
        return
    from core.tool_dispatcher import _TOOL_REGISTRY
    for reg_name in handle.tool_names:
        _TOOL_REGISTRY.pop(reg_name, None)
    try:
        await handle.stack.aclose()
    except BaseException as exc:
        # 关闭 HTTP transport 的 task group 时仍可能抛 CancelledError /
        # BaseExceptionGroup（比如连接本身正在超时/出错），不是 Exception 子类，
        # 用 BaseException 兜住，纯 best-effort 清理，吞掉即可。
        logger.debug("[mcp_client] server '%s' 关闭时出错: %s", name, exc)


async def disconnect_server(name: str) -> None:
    """摘除一个运行中 server 及其动态工具；配置文件不受影响。

    只把"reload(cfg=None)"信号丢进该 server 专属 task 的队列，立即返回；真正的
    aclose() 由专属 task 自己执行，调用方可以是任意 task（Brief 115 根治）。
    """
    if name not in _owners:
        return
    await _send_command(name, "reload", None)


async def reload_server_from_config(name: str) -> bool | None:
    """按当前配置重载一个 server，避免扰动其他 MCP session。

    只发信号给专属常驻 task；如果这个 server 之前还没有专属 task（比如刚导入、
    从未连接过），先起一个空壳 task 再发信号。返回是否成功建立了新连接。
    True=已连接或无需运行；False=热重载已执行但未连上；None=信号未能交给 owner。
    """
    cfg = _get_mcp_config()
    server_cfg = next((item for item in (cfg.get("servers") or []) if item.get("name") == name), None)
    should_run = bool(cfg.get("enabled", False) and server_cfg and server_cfg.get("enabled", True))
    if name not in _owners:
        if not should_run:
            return True
        owner = _spawn_owner(name, None)
        await owner.ready.wait()
    result = await _send_command(name, "reload", server_cfg if should_run else None)
    if result is None:
        return None
    return bool(result)


async def sync_mcp_servers(*, wait: bool = True) -> None:
    """按当前配置同步运行态，供总开关热切换使用。

    多出来的 server 发 shutdown 信号退场；仍存在的发 reload 信号刷新配置；新增的现起
    专属 task。shutdown 始终等处理完，避免连发启停时把后续 reload 丢在已退出的 owner 队列里。
    ``wait=False`` 时 reload/首次连接只入队，不把连上当作调用完成条件。
    """
    cfg = _get_mcp_config()
    desired = {
        item.get("name"): item
        for item in (cfg.get("servers") or [])
        if item.get("name") and item.get("enabled", True) and cfg.get("enabled", False)
    }
    for name in list(_owners):
        if name not in desired:
            await _send_command(name, "shutdown")
    for name, server_cfg in desired.items():
        if name in _owners:
            await _send_command(name, "reload", server_cfg, wait=wait)
        else:
            owner = _spawn_owner(name, server_cfg)
            if wait:
                await owner.ready.wait()


async def shutdown_mcp_servers() -> None:
    """进程退出时清理全部 server session（main.py finally 块调用）。"""
    for name in list(_owners):
        await _send_command(name, "shutdown")
