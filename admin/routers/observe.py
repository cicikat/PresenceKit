"""
只读观测接口（运行时内部态 + Prompt 层检视器 + 探针 + 梦境 Prompt）。

GET /observe/runtime              — 运行时内部态快照（队列/锁/通道/感知暂存/DLQ/情绪）
GET /observe/prompt-layers/{uid}  — 最近 N 轮 build_prompt 层级明细（含召回溯源 + LLM 输出）
GET /observe/prompt-layers        — 有 prompt 快照的 uid 列表
GET /observe/probe/{uid}          — 最近 N 轮探针决策快照（fast-path / LLM probe / 工具执行）
GET /observe/probe                — 有探针快照的 uid 列表
GET /observe/dream-prompt/{uid}   — 最近 N 轮梦境 Prompt 层级快照（D0-D10 + LLM 输出）
GET /observe/dream-prompt         — 有梦境快照的 uid 列表
GET /observe/trigger-catalog      — 触发器目录：全部 proposer + 最近真实捕获快照（seed prompt / search_query）
"""

import logging
from fastapi import APIRouter, Depends

from admin.auth import verify_token

logger = logging.getLogger(__name__)
router = APIRouter()


# ─────────────────────────────────────────────────────────────────────────────
# /observe/runtime
# ─────────────────────────────────────────────────────────────────────────────

def _slow_queue_state() -> dict:
    try:
        from core.post_process import slow_queue
        return {
            "queue_size": slow_queue.queue_size(),
            "worker_alive": slow_queue.worker_alive(),
            "current_task_type": slow_queue.current_task_type(),
        }
    except Exception as exc:
        logger.warning("[observe/runtime] slow_queue read failed: %s", exc)
        return {"error": "读取失败"}


def _dlq_state() -> dict:
    try:
        import time
        from core.sandbox import get_paths
        dlq_dir = get_paths().dead_letter_queue()
        if not dlq_dir.exists():
            return {"count": 0, "recent": []}
        files = sorted(dlq_dir.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True)
        recent = []
        for f in files[:10]:
            stem = f.stem  # e.g. "1718000000000_summarize_to_midterm"
            parts = stem.split("_", 1)
            ts_ms = int(parts[0]) if parts[0].isdigit() else 0
            task_type = parts[1] if len(parts) > 1 else "unknown"
            from datetime import datetime, timezone
            ts_str = (
                datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).isoformat()
                if ts_ms else ""
            )
            recent.append({"filename": f.name, "task_type": task_type, "failed_at": ts_str})
        return {"count": len(files), "recent": recent}
    except Exception as exc:
        logger.warning("[observe/runtime] DLQ read failed: %s", exc)
        return {"error": "读取失败"}


def _pending_perception_state() -> dict:
    try:
        from core.sandbox import get_paths
        from datetime import datetime, timezone
        pending_dir = get_paths().pending_perception_dir()
        files = list(pending_dir.glob("*"))
        if not files:
            return {"count": 0, "oldest": None}
        oldest_mtime = min(f.stat().st_mtime for f in files)
        oldest_str = datetime.fromtimestamp(oldest_mtime, tz=timezone.utc).isoformat()
        return {"count": len(files), "oldest": oldest_str}
    except Exception as exc:
        logger.warning("[observe/runtime] pending_perception read failed: %s", exc)
        return {"error": "读取失败"}


def _lock_state() -> dict:
    try:
        from core.memory.locks import locked_uids, locked_globals
        from core.conversation_gate import locked_conversation_uids
        return {
            "uid_locks_held": locked_uids(),
            "global_locks_held": locked_globals(),
            "conversation_locks_held": locked_conversation_uids(),
        }
    except Exception as exc:
        logger.warning("[observe/runtime] lock state read failed: %s", exc)
        return {"error": "读取失败"}


def _active_channels_state() -> dict:
    try:
        from channels.registry import get_active
        return {"active": [c.name for c in get_active()]}
    except Exception as exc:
        logger.warning("[observe/runtime] active channels read failed: %s", exc)
        return {"error": "读取失败"}


def _mood_state() -> dict:
    try:
        import json
        from core.sandbox import get_paths
        from core.observe.prompt_capture import list_uids

        def _active_char_id() -> str:
            p = get_paths().active_prompt_assets()
            data = json.loads(p.read_text(encoding="utf-8"))
            char_id = (data.get("active_character") or "").strip()
            return char_id if char_id else "yexuan"

        char_id = _active_char_id()
        mood_path = get_paths().mood_state(char_id=char_id)
        if not mood_path.exists():
            return {"mood": None}
        mood_raw = json.loads(mood_path.read_text(encoding="utf-8"))
        from core.mood_text import get_mood_text
        return {
            "mood_text": get_mood_text(mood_raw),
            "mood_raw": {k: v for k, v in mood_raw.items() if k != "history"},
        }
    except Exception as exc:
        logger.warning("[observe/runtime] mood read failed: %s", exc)
        return {"error": "读取失败"}


@router.get(
    "/observe/runtime",
    summary="运行时内部态快照（只读）",
    description=(
        "一次性返回后端所有运行时内部态：slow_queue、DLQ、pending_perception、"
        "锁状态、活跃通道、当前情绪。各子项失败时独立 fallback，不整页崩。\n\n"
        "**只读，不触发任何写操作。** 数据为瞬时快照，刷新后可能变化。"
    ),
    tags=["观测"],
)
async def get_runtime_state(auth=Depends(verify_token)):
    return {
        "slow_queue": _slow_queue_state(),
        "dead_letter_queue": _dlq_state(),
        "pending_perception": _pending_perception_state(),
        "locks": _lock_state(),
        "channels": _active_channels_state(),
        "mood": _mood_state(),
    }


# ─────────────────────────────────────────────────────────────────────────────
# /observe/prompt-layers
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/observe/prompt-layers",
    summary="列出有 Prompt 层快照的 uid",
    tags=["观测"],
)
async def list_prompt_layer_uids(auth=Depends(verify_token)):
    from core.observe.prompt_capture import list_uids
    return {"uids": list_uids()}


@router.get(
    "/observe/prompt-layers/{uid}",
    summary="查看指定 uid 的最近 Prompt 层快照",
    description=(
        "返回最近 N 轮 build_prompt() 的层级明细。每轮包含：\n"
        "- 各层名称、注入位置、字符数、估算 token、drop_priority、是否被裁剪\n"
        "- 顶层：token 估算 vs 三条阈值线、激活 tags、被裁层列表\n\n"
        "快照在内存中保存最近 5 轮，进程重启后清空。\n"
        "使用 `?n=` 参数指定查看第几轮（0=最新，1=次新…）。"
    ),
    tags=["观测"],
)
async def get_prompt_layers(uid: str, n: int = 0, auth=Depends(verify_token)):
    from core.observe.prompt_capture import get_snapshots
    snaps = get_snapshots(uid)
    if not snaps:
        return {"uid": uid, "snapshot": None, "total_snapshots": 0}
    total = len(snaps)
    # n=0 → newest (last in list)
    idx = max(0, min(n, total - 1))
    snap = snaps[-(idx + 1)]
    return {"uid": uid, "snapshot": snap, "total_snapshots": total, "n": idx}


# ─────────────────────────────────────────────────────────────────────────────
# /observe/probe  —  探针决策观测
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/observe/probe",
    summary="列出有探针快照的 uid",
    tags=["观测"],
)
async def list_probe_uids(auth=Depends(verify_token)):
    from core.observe.probe_capture import list_probe_uids as _list
    return {"uids": _list()}


@router.get(
    "/observe/probe/{uid}",
    summary="查看指定 uid 的最近探针决策快照",
    description=(
        "返回最近 N 轮探针（工具调用检测）的决策快照。每轮包含：\n"
        "- 是否走了 fast-path（跳过探针 LLM）+ 命中词 / fast_path_risk\n"
        "- 探针实际收到的 system prompt、最近几轮上下文、用户消息\n"
        "- 提供给探针的工具名列表\n"
        "- 探针原始返回字符串 + 解析出的 tool_calls\n"
        "- 每个工具的执行结果 + has_side_effect\n\n"
        "快照在内存中保存最近 5 轮，进程重启后清空。\n"
        "使用 `?n=` 参数指定查看第几轮（0=最新，1=次新…）。"
    ),
    tags=["观测"],
)
async def get_probe_snapshot(uid: str, n: int = 0, auth=Depends(verify_token)):
    from core.observe.probe_capture import get_probe_snapshots
    snaps = get_probe_snapshots(uid)
    if not snaps:
        return {"uid": uid, "snapshot": None, "total_snapshots": 0}
    total = len(snaps)
    idx = max(0, min(n, total - 1))
    snap = snaps[-(idx + 1)]
    return {"uid": uid, "snapshot": snap, "total_snapshots": total, "n": idx}


# ─────────────────────────────────────────────────────────────────────────────
# /observe/dream-prompt  —  梦境 Prompt 层级观测
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/observe/dream-prompt",
    summary="列出有梦境 Prompt 快照的 uid",
    tags=["观测"],
)
async def list_dream_prompt_uids(auth=Depends(verify_token)):
    from core.observe.dream_capture import list_dream_uids
    return {"uids": list_dream_uids()}


@router.get(
    "/observe/dream-prompt/{uid}",
    summary="查看指定 uid 的最近梦境 Prompt 层级快照",
    description=(
        "返回最近 N 轮梦境对话的 Prompt 层级快照。每轮包含：\n"
        "- D0-D10 各层 label / chars / tokens / flags / note / 是否注入\n"
        "- 顶层：world_id、lucid_mode、dream_mode、scene_tags、total_tokens\n"
        "- 当轮用户消息 + LLM 梦境回复\n\n"
        "与主 pipeline 检视器完全独立（缓冲分开，层命名不同）。\n"
        "快照在内存中保存最近 5 轮，进程重启后清空。\n"
        "使用 `?n=` 参数指定查看第几轮（0=最新，1=次新…）。"
    ),
    tags=["观测"],
)
async def get_dream_prompt_snapshot(uid: str, n: int = 0, auth=Depends(verify_token)):
    from core.observe.dream_capture import get_dream_snapshots
    snaps = get_dream_snapshots(uid)
    if not snaps:
        return {"uid": uid, "snapshot": None, "total_snapshots": 0}
    total = len(snaps)
    idx = max(0, min(n, total - 1))
    snap = snaps[-(idx + 1)]
    return {"uid": uid, "snapshot": snap, "total_snapshots": total, "n": idx}


# ─────────────────────────────────────────────────────────────────────────────
# /observe/trigger-catalog  —  触发器目录（静态注册表 + 最近真实捕获样本）
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/observe/trigger-catalog",
    summary="触发器目录：全部 proposer + 最近真实捕获快照",
    description=(
        "返回所有已注册 proposer 的目录，以及每种触发器最近一次真实捕获快照中的"
        " seed_prompt / search_query / LLM 输出。\n\n"
        "数据来源：\n"
        "- **proposers**：`proposer_registry.iter_proposers()` 静态注册表（name + trigger_names）\n"
        "- **samples**：`prompt_capture` 内存环形缓冲中标记为 `origin.proactive` 的最新快照，"
        "按 trigger_name 分桶。进程重启后清空，首次触发后才有样本。\n\n"
        "**只读**，不触发任何写操作。"
    ),
    tags=["观测"],
)
async def get_trigger_catalog(auth=Depends(verify_token)):
    try:
        from core.scheduler.proposer_registry import iter_proposers
    except Exception as exc:
        logger.warning("[observe/trigger-catalog] proposer_registry 加载失败: %s", exc)
        return {"proposers": [], "error": str(exc)}

    from core.observe.prompt_capture import get_latest_proactive_by_trigger
    latest = get_latest_proactive_by_trigger()

    catalog = []
    for entry in iter_proposers():
        trigger_names = sorted(entry.trigger_names)
        samples: dict = {}
        for tname in trigger_names:
            snap = latest.get(tname)
            if snap is None:
                samples[tname] = None
            else:
                origin = snap.get("origin") or {}
                samples[tname] = {
                    "captured_at": snap.get("captured_at"),
                    "uid": snap.get("uid"),
                    "seed_prompt": origin.get("seed_prompt", ""),
                    "search_query": origin.get("search_query", ""),
                    "llm_output": snap.get("llm_output"),
                    "token_estimate": snap.get("token_estimate"),
                }
        catalog.append({
            "name": entry.name,
            "trigger_names": trigger_names,
            "samples": samples,
        })

    return {"proposers": catalog}
