"""MCP 外部工具的管理面配置、连接测试与热重载（Brief 110）。"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import yaml
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from admin.auth import require_scopes
from core.config_loader import get_config

router = APIRouter()
CONFIG_FILE = Path("config.yaml")
_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")


class McpServerDraft(BaseModel):
    name: str
    url: str
    headers: dict[str, str] = Field(default_factory=dict)
    allow_tools: list[str] = Field(default_factory=list)
    enabled: bool = True
    tool_timeout_s: float = 30


class McpSettingsUpdate(BaseModel):
    enabled: Optional[bool] = None


class McpServerUpdate(BaseModel):
    enabled: Optional[bool] = None
    allow_tools: Optional[list[str]] = None
    headers: Optional[dict[str, str]] = None
    tool_timeout_s: Optional[float] = None


def _validate_draft(draft: McpServerDraft) -> dict:
    name = draft.name.strip()
    if not _NAME_RE.fullmatch(name):
        raise HTTPException(status_code=422, detail="name 只能含字母、数字、_、-，且必须以字母开头")
    parsed = urlparse(draft.url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(status_code=422, detail="URL 必须是完整的 http(s) MCP endpoint")
    if not all(key.strip() and value for key, value in draft.headers.items()):
        raise HTTPException(status_code=422, detail="headers 的键和值都必须是非空字符串")
    if len(draft.allow_tools) > 200:
        raise HTTPException(status_code=422, detail="allow_tools 最多 200 项")
    return {
        "name": name,
        "transport": "http",
        "url": draft.url.strip(),
        "headers": dict(draft.headers),
        "allow_tools": list(dict.fromkeys(draft.allow_tools)),
        "enabled": bool(draft.enabled),
        "tool_timeout_s": max(1, min(300, float(draft.tool_timeout_s))),
    }


def _read_config() -> dict:
    try:
        with CONFIG_FILE.open("r", encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"读取配置文件失败: {exc}") from exc


def _write_config(cfg: dict) -> None:
    try:
        with CONFIG_FILE.open("w", encoding="utf-8") as fh:
            yaml.dump(cfg, fh, allow_unicode=True, default_flow_style=False, sort_keys=False)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"写入配置文件失败: {exc}") from exc


def _safe_headers(headers: object) -> dict[str, str]:
    """管理面永不回显字面 token；${ENV_VAR} 可安全显示以便排查绑定关系。"""
    if not isinstance(headers, dict):
        return {}
    return {
        str(key): value if isinstance(value, str) and "${" in value else "••••已配置"
        for key, value in headers.items()
    }


def _server_view(server_cfg: dict) -> dict:
    from core.mcp_client import server_runtime

    name = str(server_cfg.get("name") or "")
    return {
        "name": name,
        "transport": server_cfg.get("transport", "stdio"),
        "url": server_cfg.get("url", ""),
        "headers": _safe_headers(server_cfg.get("headers")),
        "enabled": bool(server_cfg.get("enabled", True)),
        "tool_timeout_s": float(server_cfg.get("tool_timeout_s", 30)),
        "allow_tools": list(server_cfg.get("allow_tools") or []),
        "runtime": server_runtime(name),
    }


@router.get("/settings/mcp", summary="读取 MCP server 配置与运行状态")
async def get_mcp_settings(_auth=Depends(require_scopes("admin"))):
    cfg = get_config().get("mcp_servers", {}) or {}
    servers = [item for item in (cfg.get("servers") or []) if isinstance(item, dict)]
    return {
        "enabled": bool(cfg.get("enabled", False)),
        "servers": [_server_view(item) for item in servers],
        "warning": "外部 MCP 的工具描述与结果均为不可信输入；不要把密钥写进角色卡、prompt 或文档。",
    }


@router.patch("/settings/mcp", summary="更新 MCP 总开关（写配置，重启后生效）")
async def update_mcp_settings(body: McpSettingsUpdate, _auth=Depends(require_scopes("admin"))):
    # 止血（2026-07-23）：sync_mcp_servers() 会在这次 HTTP 请求自己的 task 里对一个
    # 可能在别的 task（服务启动时）建立的 AsyncExitStack 调 aclose()。MCP 的
    # streamable-http transport 内部用 anyio task group 撑着连接，跨 task 关闭会让
    # cancel scope 传播到不该传播的地方——观察到的实况是直接把 uvicorn 主循环的
    # cancel scope 一起取消，整个进程退出，不是"这一个请求报错"这么轻。在把
    # MCP session 生命周期改造成"专属常驻 task + 请求方只发信号"之前，这里先不做
    # 热同步，只写配置文件，要求重启生效，避免同一个坑再次带崩全服务。
    if body.enabled is None:
        raise HTTPException(status_code=422, detail="至少提供 enabled")
    full_cfg = _read_config()
    full_cfg.setdefault("mcp_servers", {})["enabled"] = body.enabled
    _write_config(full_cfg)
    from core import config_loader
    config_loader.reload_config()
    result = await get_mcp_settings(_auth)
    result["message"] = "已写入配置；MCP 连接的热同步暂时禁用（已知 async 生命周期问题排查中），需重启进程后生效"
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


@router.post("/settings/mcp/import", summary="测试后导入 HTTP MCP server")
async def import_mcp_server(body: McpServerDraft, _auth=Depends(require_scopes("admin"))):
    from core import config_loader, mcp_client

    server_cfg = _validate_draft(body)
    try:
        tools = await mcp_client.test_server_config(server_cfg)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"MCP 连接测试失败，未写入配置: {exc}") from exc
    discovered_names = {item["name"] for item in tools}
    unknown = sorted(set(server_cfg["allow_tools"]) - discovered_names)
    if unknown:
        raise HTTPException(status_code=422, detail=f"allow_tools 含未发现工具: {unknown}")

    full_cfg = _read_config()
    mcp_cfg = full_cfg.setdefault("mcp_servers", {})
    servers = [item for item in (mcp_cfg.get("servers") or []) if item.get("name") != server_cfg["name"]]
    servers.append(server_cfg)
    mcp_cfg["servers"] = servers
    _write_config(full_cfg)
    config_loader.reload_config()
    # 止血：不在这个请求的 task 里热重载已存在的旧连接（跨 task 关 AsyncExitStack 会带崩
    # 整个进程，见 update_mcp_settings 的说明）。测试探测本身（test_server_config）用的是
    # 独立、当次即开即关的 stack，不受影响，仍然安全。
    return {
        "message": "MCP server 已导入配置；需重启进程后才会真正连接生效（热重载暂时禁用）",
        "tools": tools,
        "server": _server_view(server_cfg),
    }


@router.patch("/settings/mcp/{name}", summary="更新一个 MCP server 的启停或工具白名单")
async def update_mcp_server(name: str, body: McpServerUpdate, _auth=Depends(require_scopes("admin"))):
    if not _NAME_RE.fullmatch(name):
        raise HTTPException(status_code=422, detail="非法 server name")
    if all(value is None for value in (body.enabled, body.allow_tools, body.headers, body.tool_timeout_s)):
        raise HTTPException(status_code=422, detail="没有可更新字段")
    full_cfg = _read_config()
    servers = full_cfg.setdefault("mcp_servers", {}).setdefault("servers", [])
    server = next((item for item in servers if item.get("name") == name), None)
    if server is None:
        raise HTTPException(status_code=404, detail="MCP server 不存在")
    if body.enabled is not None:
        server["enabled"] = body.enabled
    if body.allow_tools is not None:
        server["allow_tools"] = list(dict.fromkeys(body.allow_tools))
    if body.headers is not None:
        if not all(key.strip() and value for key, value in body.headers.items()):
            raise HTTPException(status_code=422, detail="headers 的键和值都必须是非空字符串")
        server["headers"] = dict(body.headers)
    if body.tool_timeout_s is not None:
        server["tool_timeout_s"] = max(1, min(300, float(body.tool_timeout_s)))
    _write_config(full_cfg)
    from core import config_loader
    config_loader.reload_config()
    # 止血：同上，不在请求 task 里热重载，避免跨 task 关闭已有连接的 AsyncExitStack。
    return {"message": "MCP server 配置已更新；需重启进程后生效（热重载暂时禁用）", "server": _server_view(server)}
