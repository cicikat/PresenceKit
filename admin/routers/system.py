"""
系统状态接口：运行状态、错误日志、热重载
其余设置接口已拆分至：
  settings_proxy.py  — 代理
  settings_llm.py    — LLM 参数
  settings_misc.py   — 工具开关 / 上下文 / 破限 / TTS
"""

import os
from pathlib import Path

from fastapi import APIRouter, Depends, Request

from admin.auth import require_scopes
from core.config_loader import get_config
from core.migration import get_fallback_stats
from core.sandbox import get_paths

router = APIRouter()


@router.get("/status", summary="获取机器人运行状态")
async def get_status(auth=Depends(require_scopes("state.read"))):
    from core import message_queue
    from core.test_data_guard import is_test_identifier

    cfg = get_config()
    paths = get_paths()
    # v1: 从 memory_char_root 枚举用户子目录；legacy: 扫 history/ *.json
    char_root = paths.memory_char_root()
    if char_root.exists():
        user_ids = {item.name for item in char_root.iterdir() if item.is_dir()}
    else:
        history_dir = paths.history()
        user_ids = {item.stem for item in history_dir.glob("*.json")} if history_dir.exists() else set()
    test_user_ids = sorted(uid for uid in user_ids if is_test_identifier(uid))
    user_count = sum(1 for uid in user_ids if not is_test_identifier(uid))

    return {
        "status": "running",
        "active_sessions":      message_queue.active_sessions(),
        "active_session_count": len(message_queue.active_sessions()),
        "known_user_count":     user_count,
        "data_mode": paths.mode,
        "test_session_id": paths.test_session_id,
        "data_root": str(paths.root_dir()).replace("\\", "/"),
        "test_user_ids": test_user_ids,
        "config_summary": {
            "llm_model":        cfg.get("llm", {}).get("model",    "unknown"),
            "llm_provider":     cfg.get("llm", {}).get("provider", "unknown"),
            "short_term_rounds": cfg.get("memory", {}).get("short_term_rounds", 20),
            "admin_host":       cfg.get("admin", {}).get("host",   "127.0.0.1"),
            "admin_port":       cfg.get("admin", {}).get("port",   8080),
        },
        "fallback_migration": get_fallback_stats(),
    }


@router.get("/logs", summary="获取最近错误日志")
async def get_logs(lines: int = 200, auth=Depends(require_scopes("admin"))):
    log_file = get_paths().error_log()
    if not log_file.exists():
        return {"logs": "", "message": "日志文件不存在"}
    try:
        with open(log_file, "r", encoding="utf-8") as f:
            all_lines = f.readlines()
        return {"logs": "".join(all_lines[-lines:]), "total_lines": len(all_lines)}
    except Exception as e:
        return {"error": str(e)}


@router.delete("/logs", summary="清空错误日志")
async def clear_logs(auth=Depends(require_scopes("admin"))):
    try:
        log_file = get_paths().error_log()
        log_file.parent.mkdir(parents=True, exist_ok=True)
        log_file.write_text("", encoding="utf-8")
        return {"message": "错误日志已清空"}
    except Exception as e:
        return {"error": str(e)}


@router.post("/reload", summary="热重载所有配置")
async def reload_config(auth=Depends(require_scopes("admin"))):
    from core import config_loader, user_relation, qq_adapter
    config_loader.reload_config()
    user_relation.reload()
    qq_adapter.reload_blacklist()
    return {"message": "config.yaml / relations.yaml / blacklist.yaml 已全部热重载"}


@router.post("/group-distill", summary="对指定群的聊天记录进行 LLM 蒸馏")
async def group_distill(body: dict, auth=Depends(require_scopes("admin"))):
    """读取群消息记录，调用 LLM 生成摘要"""
    group_id = (body.get("group_id") or "").strip()
    if not group_id:
        from fastapi import HTTPException
        raise HTTPException(status_code=422, detail="group_id 不能为空")
    from core.tools.group_distill import distill
    result = await distill(group_id)
    return {"group_id": group_id, "summary": result}


@router.get("/system/data-path", summary="获取数据根目录路径")
async def get_data_path(auth=Depends(require_scopes("admin"))):
    cfg = get_config()
    prefix = cfg.get("data_prefix", "data")
    return {"data_prefix": prefix}


# ── 密钥本快捷入口（Brief 93 §2）───────────────────────────────────────────────

_SECRETS_LOCAL_PATH = Path("secrets.local.yaml")


def _is_localhost_request(request: Request) -> bool:
    host = request.client.host if request.client else ""
    return host in ("127.0.0.1", "::1", "localhost")


@router.get("/system/secrets-book", summary="密钥本快捷入口是否可用（本机判定，供面板悬浮按钮显隐）")
async def get_secrets_book_status(request: Request, auth=Depends(require_scopes("admin"))):
    return {
        "available": _is_localhost_request(request),
        "exists": _SECRETS_LOCAL_PATH.exists(),
    }


@router.post("/system/secrets-book/open", summary="用系统默认程序打开 secrets.local.yaml（仅本机请求）")
async def open_secrets_book(request: Request, auth=Depends(require_scopes("admin"))):
    from fastapi import HTTPException

    if not _is_localhost_request(request):
        raise HTTPException(status_code=403, detail="仅本机可用")
    if not _SECRETS_LOCAL_PATH.exists():
        raise HTTPException(
            status_code=404,
            detail="secrets.local.yaml 不存在，请先运行 python scripts/setup_auth.py 完成鉴权初始化",
        )

    resolved = str(_SECRETS_LOCAL_PATH.resolve())
    try:
        import sys
        if sys.platform == "win32":
            os.startfile(resolved)  # type: ignore[attr-defined]
        else:
            import subprocess
            opener = "open" if sys.platform == "darwin" else "xdg-open"
            subprocess.Popen([opener, resolved])
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"打开失败: {e}")

    from admin.audit import log_event
    log_event("secrets_book_opened", label=auth.label)
    return {"message": "已用系统默认程序打开 secrets.local.yaml"}


@router.get("/system/health", summary="静默失败计数 + 进程启动时间")
async def get_health(auth=Depends(require_scopes("state.read"))):
    from core import silent_failure
    return {
        "started_at": silent_failure.process_started_at(),
        "silent_failures": silent_failure.snapshot(),
    }


@router.get("/system/meta-mode", summary="获取当前安全/危险模式")
async def get_meta_mode(auth=Depends(require_scopes("state.read"))):
    import json
    p = get_paths().meta_mode()
    if not p.exists():
        return {"mode": "safe", "expires_at": None}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        mode = data.get("mode", "safe")
        if mode != "danger":
            return {"mode": "safe", "expires_at": None}
        return {"mode": "danger", "expires_at": None}
    except Exception:
        return {"mode": "safe", "expires_at": None}


@router.patch("/system/meta-mode", summary="切换安全/危险模式")
async def patch_meta_mode(body: dict, request: Request, auth=Depends(require_scopes("hardware"))):
    from core.safe_write import safe_write_json
    from fastapi import HTTPException

    mode = body.get("mode", "safe")
    if mode not in ("safe", "danger"):
        raise HTTPException(status_code=422, detail="mode 只接受 'safe' 或 'danger'")

    # Danger stays on until the owner turns it off. Leftover ttl_seconds in
    # mobile/desktop payloads is ignored and never written back.
    expires_at = None

    p = get_paths().meta_mode()
    p.parent.mkdir(parents=True, exist_ok=True)
    safe_write_json(p, {"mode": mode, "expires_at": expires_at})

    if mode == "danger":
        from admin.audit import log_event
        log_event(
            "meta_mode_danger",
            label=auth.label,
            path="/system/meta-mode",
            ip=request.client.host if request.client else None,
        )

    return {"mode": mode, "expires_at": expires_at}
