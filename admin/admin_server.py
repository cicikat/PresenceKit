"""
管理面板 FastAPI 服务
当 config.admin.enabled = true 时，由 main.py 启动
提供 REST API 用于远程管理机器人，并托管静态网页界面
"""

import logging
import mimetypes
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from admin.routers import jailbreak_entries

from core.config_loader import get_config

logger = logging.getLogger(__name__)

_STATIC_DIR = Path(__file__).parent / "static"

# Windows 的注册表可能把 .js 映射成 text/plain，现代浏览器会拒绝执行。
# 在挂载 StaticFiles 前显式覆盖严格/非严格 MIME 映射。
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/javascript", ".js", strict=False)

# ── 鉴权：从独立模块导入，避免与 routers 的循环导入 ──────────────────────────
from admin.auth import verify_token, security, get_admin_secret, authenticate_ws  # noqa: F401 (re-exported for legacy imports)

# Brief 93 §6：WS 鉴权失败 close reason 人话化（RFC 6455 reason 上限 123 字节，UTF-8 中文需精简）。
_WS_UNAUTHORIZED_REASON = "token 未配置或已失效，请到管理面板打开密钥本获取"

# ── FastAPI 应用 ──────────────────────────────────────────────────────────────
app = FastAPI(
    title="Emerald-Presence 运维控制台",
    description="Emerald-Presence 运维 / 调试 / 创作接口（密钥与部署配置见 config.yaml）",
    version="1.0.0",
)

# 注册业务路由（在 app 定义之后导入，避免循环）
from admin.routers import (
    users, memory, relations,
    system, lorebook,
    settings_proxy, settings_llm, settings_misc, settings_prompt_assets,
    settings_screen_peek, settings_tool_loop, settings_thinking, settings_relay, settings_feature_flags,
    character, chat,
    scheduler, watch, sensor,
    garden, mobile, diary, chat_log,
    mood, activity, dream,
    reading, gomoku, dream_seed,
    hidden_state_debug, hardware, observe,
    group, group_dream, relationship_facts,
    transcribe, provenance,
    auth_tokens, coplay, perception, spend, growth, observability,
)

# chess 路由依赖 python-chess（requirements-full.txt 的可选依赖，见 cc-tasks/92 §1），
# core-only 环境下缺失时该功能不可用而非拖垮整个 admin_server。
try:
    from admin.routers import chess
except ImportError as _e:
    chess = None
    logger.warning(f"[admin_server] 国际象棋活动路由未加载（缺少依赖: {_e}），该功能不可用")

app.include_router(users.router,          prefix="/users",     tags=["用户"])
app.include_router(memory.router,         prefix="/memory",    tags=["记忆"])
app.include_router(relations.router,      prefix="/relations", tags=["关系"])
app.include_router(system.router,         prefix="",           tags=["系统"])
app.include_router(lorebook.router,       prefix="",           tags=["世界书"])
app.include_router(settings_proxy.router, prefix="",           tags=["设置-代理"])
app.include_router(settings_llm.router,   prefix="",           tags=["设置-LLM"])
app.include_router(settings_misc.router,          prefix="", tags=["设置-杂项"])
app.include_router(settings_prompt_assets.router, prefix="", tags=["设置-Prompt资产"])
app.include_router(settings_screen_peek.router,   prefix="", tags=["设置-屏幕内容"])
app.include_router(settings_tool_loop.router,     prefix="", tags=["设置-工具循环"])
app.include_router(settings_thinking.router,      prefix="", tags=["设置-思考"])
app.include_router(settings_relay.router,         prefix="", tags=["设置-中继"])
app.include_router(settings_feature_flags.router, prefix="", tags=["设置-功能开关"])
app.include_router(character.router,      prefix="",           tags=["角色卡"])
app.include_router(chat.router,           prefix="",           tags=["对话"])
app.include_router(scheduler.router,      prefix="",           tags=["调度器"])
app.include_router(watch.router,          prefix="",           tags=["Watch"])
app.include_router(jailbreak_entries.router, prefix="",        tags=["破限条目"])
app.include_router(sensor.router, prefix="", tags=["手机传感器"])
app.include_router(garden.router,   prefix="/garden",   tags=["花园"])
app.include_router(mood.router,     prefix="/mood",     tags=["情绪状态"])
app.include_router(activity.router, prefix="/activity", tags=["活动状态"])
app.include_router(reading.router,  prefix="/activity", tags=["阅读活动"])
app.include_router(gomoku.router,   prefix="/activity", tags=["五子棋活动"])
if chess is not None:
    app.include_router(chess.router, prefix="/activity", tags=["国际象棋活动"])
app.include_router(dream_seed.router, prefix="/activity", tags=["梦境预构活动"])
app.include_router(diary.router,     prefix="/diary",     tags=["日记"])
app.include_router(chat_log.router,  prefix="/chat-log",  tags=["聊天日志"])
app.include_router(mobile.router,    prefix="",           tags=["手机端"])
app.include_router(dream.router,     prefix="",           tags=["梦境"])
app.include_router(hidden_state_debug.router, prefix="", tags=["观测"])
app.include_router(observe.router,            prefix="", tags=["观测"])
app.include_router(hardware.router, prefix="/hardware", tags=["硬件"])
app.include_router(group.router,    prefix="/group",    tags=["群聊"])
app.include_router(group_dream.router, prefix="/group", tags=["群聊梦境"])
app.include_router(relationship_facts.router, prefix="", tags=["关系事实"])
app.include_router(transcribe.router,          prefix="", tags=["语音转写"])
app.include_router(provenance.router,          prefix="", tags=["观测"])
app.include_router(auth_tokens.router,         prefix="", tags=["鉴权"])
app.include_router(coplay.router,              prefix="", tags=["陪玩"])
app.include_router(perception.router,           prefix="", tags=["视觉感知"])
app.include_router(spend.router,                prefix="", tags=["支出台账"])
app.include_router(growth.router,               prefix="", tags=["成长观测"])
app.include_router(observability.router,        prefix="", tags=["观测"])

# ── 桌宠端 WebSocket 端点 ─────────────────────────────────────────────────────
from fastapi import WebSocket as _WebSocket
from channels.desktop_ws import handle_connection as _ws_desktop_handler


@app.websocket("/ws/desktop")
async def ws_desktop_endpoint(websocket: _WebSocket):
    # WebSocket auth only accepts Authorization: Bearer. Query tokens are rejected.
    if authenticate_ws(websocket, "ws.desktop") is None:
        await websocket.close(code=1008, reason=_WS_UNAUTHORIZED_REASON)
        return
    await _ws_desktop_handler(websocket)


# ── 设备端（ESP32 等具身硬件）WebSocket 端点 ──────────────────────────────────
from channels.device_ws import handle_connection as _ws_device_handler


@app.websocket("/ws/device")
async def ws_device_endpoint(websocket: _WebSocket):
    # 与 /ws/desktop 相同鉴权：仅 Authorization: Bearer
    if authenticate_ws(websocket, "ws.device") is None:
        await websocket.close(code=1008, reason=_WS_UNAUTHORIZED_REASON)
        return
    await _ws_device_handler(websocket)


# 挂载静态资源
if _STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


@app.get("/", include_in_schema=False)
async def root():
    index_file = _STATIC_DIR / "index.html"
    if index_file.exists():
        return FileResponse(str(index_file))
    return {"status": "ok", "service": "Emerald-Presence Admin", "ui": "index.html not found"}


def _weak_password_warning(host: str, secret: str) -> None:
    """host 非本地回环 + secret 强度不足（长度 < 12）→ error 级横幅（Brief 33 §1.3，不阻断）。"""
    if host != "127.0.0.1" and len(secret) < 12:
        logger.error("=" * 60)
        logger.error("  [安全警告] admin.host=%s（非本地回环），但 admin secret 长度 < 12", host)
        logger.error("  管理面板可能暴露在局域网/公网，弱口令下存在被接管风险。")
        logger.error("  请立即修改 config.yaml 中 admin.secret_key 为足够长的随机值。")
        logger.error("=" * 60)


async def start_admin_server():
    """在当前事件循环中启动 uvicorn（由 main.py 以 asyncio.create_task 调用）"""
    import uvicorn
    from admin.log_filter import install_access_log_sanitizer, install_auth_failure_dedup_filter

    install_access_log_sanitizer()
    install_auth_failure_dedup_filter()

    from admin.log_filter import install_console_quiet_mode
    if get_config().get("logging", {}).get("console_quiet", True):
        install_console_quiet_mode()

    cfg = get_config().get("admin", {})
    host = cfg.get("host", "127.0.0.1")
    port = cfg.get("port", 8080)
    _weak_password_warning(host, get_admin_secret())

    config = uvicorn.Config(
        app=app,
        host=host,
        port=port,
        log_level="info",
        loop="asyncio",
    )
    server = uvicorn.Server(config)
    logger.info(f"[admin] 管理面板启动于 http://{host}:{port}")
    await server.serve()
