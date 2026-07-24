"""
core/mcp_client.py — MCP (Model Context Protocol) 客户端（Brief 29 · 4）

只接外部工具，不接 resources/prompts、不接外部记忆库（见 cc-tasks/29 定位说明：
外接记忆绕过 prompt 层注入与固化链，会裂成两套真相；MCP 只用于外部工具）。

生命周期：main.py 启动时调 init_mcp_servers()，为每个已启用 server 建立 ClientSession、
list_tools，动态注册进 core.tool_dispatcher._TOOL_REGISTRY（name="mcp__{server}__{tool}"，
category="mcp"）。单 server 初始化失败只跳过该 server（log + 继续），不影响其他 server 与主流程。

工具只经 tool loop（Path C）暴露：角色卡 presence_ext.tool_categories 不含 "mcp" 就永远看不到
这些工具（探针 prompt 只拼 info/desktop，不覆盖 mcp 类），这就是"本我接 MCP、角色扮演不受影响"
的实现方式。

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
import logging
import os
import re
import time
from contextlib import AsyncExitStack
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

_RESULT_CHAR_CAP = 2000
_ENV_REF_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")

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


@dataclass
class _ServerOwner:
    """一个 server 专属的常驻 task：唯一允许打开/关闭其 AsyncExitStack 的执行体。"""
    task: "asyncio.Task | None"
    queue: "asyncio.Queue[tuple]"
    ready: asyncio.Event  # 首次连接尝试（成功或失败）完成后 set


def _get_mcp_config() -> dict:
    from core.config_loader import get_config
    return get_config().get("mcp_servers", {}) or {}


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

        def _replace(match: re.Match[str]) -> str:
            env_name = match.group(1)
            env_value = os.environ.get(env_name)
            if env_value is None:
                raise ValueError(f"headers 环境变量未设置: {env_name}")
            return env_value

        resolved[key.strip()] = _ENV_REF_RE.sub(_replace, value)
    return resolved


def _tool_details(listed) -> list[dict]:
    return [
        {"name": str(tool.name), "description": str(tool.description or "")}
        for tool in (getattr(listed, "tools", None) or [])
        if getattr(tool, "name", None)
    ]


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
    else:
        state.setdefault("tools", [])
        state["registered_tools"] = []
    return state


async def init_mcp_servers() -> None:
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


async def _send_command(name: str, cmd: str, payload: dict | None = None) -> object:
    """把一条指令丢进 server 专属 task 的队列，等它在自己的 task 里处理完并返回结果。

    这一步只是 put + await 一个 Future，不涉及跨 task 关闭 cancel scope，调用方可以是
    任意 task（HTTP 请求 task、总开关同步等）——真正的 aclose()/连接动作发生在
    _owner_loop 所在的专属 task 里。
    """
    owner = _owners.get(name)
    if owner is None:
        return None
    done = asyncio.get_running_loop().create_future()
    await owner.queue.put((cmd, payload, done))
    return await done


async def _open_transport(stack: AsyncExitStack, server_cfg: dict):
    transport = server_cfg.get("transport", "stdio")
    if transport == "stdio":
        from mcp import StdioServerParameters
        from mcp.client.stdio import stdio_client
        command = server_cfg.get("command") or []
        if not command:
            raise ValueError("stdio transport 需要非空 command 数组")
        params = StdioServerParameters(command=command[0], args=list(command[1:]))
        read, write = await stack.enter_async_context(stdio_client(params))
        return read, write
    if transport == "http":
        from mcp.client.streamable_http import streamablehttp_client
        url = server_cfg.get("url")
        if not url:
            raise ValueError("http transport 需要 url")
        headers = _expand_headers(server_cfg.get("headers"))
        read, write, _get_session_id = await stack.enter_async_context(
            streamablehttp_client(url, headers=headers)
        )
        return read, write
    raise ValueError(f"未知 transport: {transport!r}（只支持 stdio | http）")


async def _connect_server(name: str, server_cfg: dict) -> None:
    from mcp import ClientSession
    from core.tool_dispatcher import _TOOL_REGISTRY

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
    details = _tool_details(listed)
    handle = _ServerHandle(
        name=name, cfg=server_cfg, stack=stack, session=session, tool_details=details,
    )

    for tool in listed.tools:
        if allow and tool.name not in allow:
            continue
        reg_name = f"mcp__{name}__{tool.name}"
        if reg_name in _TOOL_REGISTRY:
            logger.warning("[mcp_client] 工具名与已注册工具冲突，MCP 侧让位: %s", reg_name)
            continue
        _TOOL_REGISTRY[reg_name] = {
            "func": _make_tool_func(name, tool.name, timeout_s),
            "description": tool.description or "",
            "dangerous": False,
            "category": "mcp",
            "parameters": tool.inputSchema or {"type": "object", "properties": {}},
            "mcp_server": name,
            "mcp_tool": tool.name,
        }
        handle.tool_names.append(reg_name)

    _servers[name] = handle
    _record_init(name, ok=True, tools=details)
    logger.info("[mcp_client] server '%s' 已连接，注册 %d 个工具", name, len(handle.tool_names))


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
        return _tool_details(await session.list_tools())
    except BaseException as exc:
        # 同 _connect_server：初始化阶段被取消可能抛非 Exception 子类，统一收窄，
        # 让 admin 路由的 `except Exception` 能正常转成 400 而不是让请求本身崩掉。
        raise RuntimeError(f"连接或初始化未完成: {exc}") from exc
    finally:
        try:
            await stack.aclose()
        except BaseException as close_exc:
            logger.debug("[mcp_client] test_server_config 关闭 stack 出错: %s", close_exc)


def _make_tool_func(server_name: str, tool_name: str, timeout_s: float):
    async def _call(**kwargs) -> str:
        return await _call_tool(server_name, tool_name, kwargs, timeout_s)
    return _call


async def _call_tool(server_name: str, tool_name: str, arguments: dict, timeout_s: float) -> str:
    handle = _servers.get(server_name)
    if handle is None:
        raise RuntimeError(f"MCP server '{server_name}' 未连接")
    started = time.perf_counter()
    try:
        try:
            result = await asyncio.wait_for(
                handle.session.call_tool(tool_name, arguments), timeout=timeout_s
            )
        except BaseException as exc:
            # 同 _connect_server：真正的工具调用最常经过这条路径（每次 execute() 都走
            # 这里），wait_for 超时自己内部取消底层 anyio 流读写时，同样可能抛
            # CancelledError / BaseExceptionGroup，必须用 BaseException 才接得住，
            # 否则重连都走不到就已经把请求捅穿了。
            logger.warning("[mcp_client] 调用 %s.%s 失败，尝试重连一次: %s", server_name, tool_name, exc)
            try:
                await _reconnect_server(server_name)
            except BaseException as reconnect_exc:
                logger.warning("[mcp_client] 重连 %s 失败: %s", server_name, reconnect_exc)
            handle = _servers.get(server_name)
            if handle is None:
                raise RuntimeError(f"MCP 工具调用失败且重连未恢复: {exc}") from exc
            try:
                result = await asyncio.wait_for(
                    handle.session.call_tool(tool_name, arguments), timeout=timeout_s
                )
            except BaseException as retry_exc:
                raise RuntimeError(f"MCP 工具调用失败: {retry_exc}") from retry_exc
        text = _format_result(result)
    except BaseException as exc:
        _record_call(server_name, tool_name, ok=False, error=str(exc))
        from core.api_call_log import append as append_api_call
        append_api_call(
            caller=f"mcp__{server_name}__{tool_name}", purpose="mcp_tool", provider=server_name,
            model=tool_name, duration_ms=int((time.perf_counter() - started) * 1000), ok=False,
            output_hint="MCP call failed",
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


async def reload_server_from_config(name: str) -> bool:
    """按当前配置重载一个 server，避免扰动其他 MCP session。

    只发信号给专属常驻 task；如果这个 server 之前还没有专属 task（比如刚导入、
    从未连接过），先起一个空壳 task 再发信号。返回是否成功建立了新连接。
    """
    cfg = _get_mcp_config()
    server_cfg = next((item for item in (cfg.get("servers") or []) if item.get("name") == name), None)
    should_run = bool(cfg.get("enabled", False) and server_cfg and server_cfg.get("enabled", True))
    if name not in _owners:
        if not should_run:
            return False
        owner = _spawn_owner(name, None)
        await owner.ready.wait()
    result = await _send_command(name, "reload", server_cfg if should_run else None)
    return bool(result)


async def sync_mcp_servers() -> None:
    """按当前配置同步运行态，供总开关热切换使用。

    多出来的 server 发 shutdown 信号退场；仍存在的发 reload 信号刷新配置；新增的现起
    专属 task。全部只发信号、等专属 task 自己处理，不跨 task 直接碰 AsyncExitStack。
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
            await _send_command(name, "reload", server_cfg)
        else:
            owner = _spawn_owner(name, server_cfg)
            await owner.ready.wait()


async def shutdown_mcp_servers() -> None:
    """进程退出时清理全部 server session（main.py finally 块调用）。"""
    for name in list(_owners):
        await _send_command(name, "shutdown")
