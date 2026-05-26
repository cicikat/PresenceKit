"""
管理面板 FastAPI 服务
当 config.admin.enabled = true 时，由 main.py 启动
提供 REST API 用于远程管理机器人，并托管静态网页界面
"""

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from admin.routers import jailbreak_entries

from core.config_loader import get_config

logger = logging.getLogger(__name__)

_STATIC_DIR = Path(__file__).parent / "static"

# ── 鉴权：从独立模块导入，避免与 routers 的循环导入 ──────────────────────────
from admin.auth import verify_token, security  # noqa: F401 (re-exported for legacy imports)

# ── FastAPI 应用 ──────────────────────────────────────────────────────────────
app = FastAPI(
    title="QQ-ST-Bot 管理面板",
    description="QQ SillyTavern Bot 远程管理接口",
    version="1.0.0",
)

# 注册业务路由（在 app 定义之后导入，避免循环）
from admin.routers import (
    users, memory, relations,
    system, lorebook,
    settings_proxy, settings_llm, settings_misc,
    character, chat,
    scheduler, watch, agent, sensor,
    garden, mobile, diary, chat_log,
    mood, activity, dream,
)

app.include_router(users.router,          prefix="/users",     tags=["用户"])
app.include_router(memory.router,         prefix="/memory",    tags=["记忆"])
app.include_router(relations.router,      prefix="/relations", tags=["关系"])
app.include_router(system.router,         prefix="",           tags=["系统"])
app.include_router(lorebook.router,       prefix="",           tags=["世界书"])
app.include_router(settings_proxy.router, prefix="",           tags=["设置-代理"])
app.include_router(settings_llm.router,   prefix="",           tags=["设置-LLM"])
app.include_router(settings_misc.router,  prefix="",           tags=["设置-杂项"])
app.include_router(character.router,      prefix="",           tags=["角色卡"])
app.include_router(chat.router,           prefix="",           tags=["对话"])
app.include_router(scheduler.router,      prefix="",           tags=["调度器"])
app.include_router(watch.router,          prefix="",           tags=["Watch"])
app.include_router(jailbreak_entries.router, prefix="",        tags=["破限条目"])
app.include_router(agent.router,            prefix="",           tags=["Agent"])
app.include_router(sensor.router, prefix="", tags=["手机传感器"])
app.include_router(garden.router,   prefix="/garden",   tags=["花园"])
app.include_router(mood.router,     prefix="/mood",     tags=["情绪状态"])
app.include_router(activity.router, prefix="/activity", tags=["活动状态"])
app.include_router(diary.router,     prefix="/diary",     tags=["日记"])
app.include_router(chat_log.router,  prefix="/chat-log",  tags=["聊天日志"])
app.include_router(mobile.router,    prefix="",           tags=["手机端"])
app.include_router(dream.router,     prefix="",           tags=["梦境"])

# ── 桌宠端 WebSocket 端点 ─────────────────────────────────────────────────────
from fastapi import WebSocket as _WebSocket
from channels.desktop_ws import handle_connection as _ws_desktop_handler


@app.websocket("/ws/desktop")
async def ws_desktop_endpoint(websocket: _WebSocket):
    await _ws_desktop_handler(websocket)


# 挂载静态资源
if _STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


@app.get("/", include_in_schema=False)
async def root():
    index_file = _STATIC_DIR / "index.html"
    if index_file.exists():
        return FileResponse(str(index_file))
    return {"status": "ok", "service": "QQ-ST-Bot Admin", "ui": "index.html not found"}


async def start_admin_server():
    """在当前事件循环中启动 uvicorn（由 main.py 以 asyncio.create_task 调用）"""
    import uvicorn

    cfg = get_config().get("admin", {})
    host = cfg.get("host", "127.0.0.1")
    port = cfg.get("port", 8080)

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
