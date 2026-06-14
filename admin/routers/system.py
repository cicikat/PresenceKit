"""
系统状态接口：运行状态、错误日志、热重载
其余设置接口已拆分至：
  settings_proxy.py  — 代理
  settings_llm.py    — LLM 参数
  settings_misc.py   — 工具开关 / 上下文 / 破限 / TTS
"""

from pathlib import Path

from fastapi import APIRouter, Depends

from admin.auth import verify_token
from core.config_loader import get_config
from core.migration import get_fallback_stats
from core.sandbox import get_paths

router = APIRouter()


@router.get("/status", summary="获取机器人运行状态")
async def get_status(auth=Depends(verify_token)):
    from core import message_queue

    cfg = get_config()
    # v1: 从 memory_char_root 枚举用户子目录；legacy: 扫 history/ *.json
    char_root = get_paths().memory_char_root()
    if char_root.exists():
        user_count = sum(1 for d in char_root.iterdir() if d.is_dir())
    else:
        history_dir = get_paths().history()
        user_count = len(list(history_dir.glob("*.json"))) if history_dir.exists() else 0

    return {
        "status": "running",
        "active_sessions":      message_queue.active_sessions(),
        "active_session_count": len(message_queue.active_sessions()),
        "known_user_count":     user_count,
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
async def get_logs(lines: int = 200, auth=Depends(verify_token)):
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
async def clear_logs(auth=Depends(verify_token)):
    try:
        log_file = get_paths().error_log()
        log_file.parent.mkdir(parents=True, exist_ok=True)
        log_file.write_text("", encoding="utf-8")
        return {"message": "错误日志已清空"}
    except Exception as e:
        return {"error": str(e)}


@router.post("/reload", summary="热重载所有配置")
async def reload_config(auth=Depends(verify_token)):
    from core import config_loader, user_relation, qq_adapter
    config_loader.reload_config()
    user_relation.reload()
    qq_adapter.reload_blacklist()
    return {"message": "config.yaml / relations.yaml / blacklist.yaml 已全部热重载"}


@router.get("/pet", summary="获取宠物状态")
async def get_pet_status(auth=Depends(verify_token)):
    from core.pet import get_pet, pet_greeting
    pet = get_pet()
    if pet is None:
        return {"pet": None}
    return {"pet": {**pet, "greeting": pet_greeting(pet)}}


@router.post("/pet", summary="创建或更新宠物")
async def upsert_pet(body: dict, auth=Depends(verify_token)):
    name    = (body.get("name") or "").strip()
    species = (body.get("species") or "猫").strip()
    if not name:
        from fastapi import HTTPException
        raise HTTPException(status_code=422, detail="name 不能为空")
    from core.pet import create_pet
    pet = create_pet(name, species)
    return {"message": f"宠物 {name}（{species}）已创建/更新", "pet": pet}


@router.put("/pet/interact", summary="与宠物互动（摸摸头/喂食）")
async def pet_interact(body: dict, auth=Depends(verify_token)):
    action = body.get("action", "")
    from core.pet import get_pet, update_pet, pet_greeting
    pet = get_pet()
    if pet is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="还没有宠物")
    if action == "pet":       # 摸摸头
        pet = update_pet("mood",   min(100, int(pet.get("mood", 80)) + 10))
        msg = f"（{pet['name']}被摸了摸，心情好了一点）"
    elif action == "feed":    # 喂食
        pet = update_pet("hunger", max(0, int(pet.get("hunger", 20)) - 30))
        msg = f"（{pet['name']}吃得很满足）"
    else:
        from fastapi import HTTPException
        raise HTTPException(status_code=422, detail="action 只接受 'pet' 或 'feed'")
    return {"message": msg, "pet": {**pet, "greeting": pet_greeting(pet)}}


@router.post("/group-distill", summary="对指定群的聊天记录进行 LLM 蒸馏")
async def group_distill(body: dict, auth=Depends(verify_token)):
    """读取群消息记录，调用 LLM 生成摘要"""
    group_id = (body.get("group_id") or "").strip()
    if not group_id:
        from fastapi import HTTPException
        raise HTTPException(status_code=422, detail="group_id 不能为空")
    from core.tools.group_distill import distill
    result = await distill(group_id)
    return {"group_id": group_id, "summary": result}


@router.get("/system/data-path", summary="获取数据根目录路径")
async def get_data_path(auth=Depends(verify_token)):
    cfg = get_config()
    prefix = cfg.get("data_prefix", "data")
    return {"data_prefix": prefix}


@router.get("/system/meta-mode", summary="获取当前安全/危险模式")
async def get_meta_mode(auth=Depends(verify_token)):
    import json
    import time
    p = get_paths().meta_mode()
    if not p.exists():
        return {"mode": "safe", "expires_at": None}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        mode = data.get("mode", "safe")
        expires_at = data.get("expires_at")
        if mode == "danger" and expires_at is not None and time.time() > expires_at:
            mode = "safe"
            expires_at = None
        return {"mode": mode, "expires_at": expires_at}
    except Exception:
        return {"mode": "safe", "expires_at": None}


@router.patch("/system/meta-mode", summary="切换安全/危险模式")
async def patch_meta_mode(body: dict, auth=Depends(verify_token)):
    import json
    import time
    from core.safe_write import safe_write_json
    from fastapi import HTTPException

    mode = body.get("mode", "safe")
    if mode not in ("safe", "danger"):
        raise HTTPException(status_code=422, detail="mode 只接受 'safe' 或 'danger'")

    from core.tool_dispatcher import _DANGER_MODE_TTL_SECONDS
    expires_at: float | None = None
    if mode == "danger":
        ttl = int(body.get("ttl_seconds") or _DANGER_MODE_TTL_SECONDS)
        expires_at = time.time() + ttl

    p = get_paths().meta_mode()
    p.parent.mkdir(parents=True, exist_ok=True)
    safe_write_json(p, {"mode": mode, "expires_at": expires_at})
    return {"mode": mode, "expires_at": expires_at}
