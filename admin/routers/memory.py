"""
记忆管理路由
"""

import json as _json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from admin.auth import require_scopes

router = APIRouter()


def _resolve_char_id(char_id: str | None) -> str:
    """Resolve and validate a char_id for memory operations.

    If char_id is None, reads active_character from active_prompt_assets.json.
    Raises HTTP 503 if active_character is missing or empty.
    Raises HTTP 422 if the resolved or supplied char_id is not a known character.
    Never falls back to a hardcoded character.
    """
    from core.sandbox import get_paths
    from core.asset_registry import get_registry

    if char_id is None:
        try:
            data = _json.loads(get_paths().active_prompt_assets().read_text(encoding="utf-8"))
            char_id = (data.get("active_character") or "").strip()
        except Exception as e:
            raise HTTPException(status_code=503, detail=f"读取 active_prompt_assets.json 失败: {e}")
        if not char_id:
            raise HTTPException(
                status_code=503,
                detail="active_prompt_assets.json 中 active_character 为空，请先设置活跃角色",
            )

    try:
        get_registry().resolve(char_id, "character")
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    return char_id


@router.get("/storyline/{user_id}", summary="读取 storyline 叙事弧概要（Brief 80）")
async def get_storyline_state(
    user_id: str,
    char_id: str | None = None,
    auth=Depends(require_scopes("memory.read")),
):
    from core.memory import storyline as _sl
    resolved = _resolve_char_id(char_id)
    data = _sl.load(user_id, char_id=resolved)
    from core.memory.path_resolver import resolve_path
    from core.memory.scope import MemoryScope
    inbox_path = resolve_path(MemoryScope.reality_scope(user_id, resolved), "storyline_inbox")
    try:
        inbox_count = len(_json.loads(inbox_path.read_text(encoding="utf-8"))) if inbox_path.exists() else 0
    except Exception:
        inbox_count = 0
    return {
        "user_id": user_id,
        "char_id": resolved,
        "meta": data["meta"],
        "inbox_count": inbox_count,
        "arcs": [
            {
                "arc_id": a["arc_id"],
                "title": a["title"],
                "status": a["status"],
                "tags": a["tags"],
                "node_count": len(a["nodes"]),
                "updated_at": a["updated_at"],
            }
            for a in data["arcs"]
        ],
    }


@router.get("/digest/{user_id}", summary="读取淘汰情景记忆的时期摘要")
async def get_memory_digest(
    user_id: str,
    char_id: str | None = None,
    auth=Depends(require_scopes("memory.read")),
):
    from core.memory.path_resolver import resolve_path
    from core.memory.scope import MemoryScope
    resolved = _resolve_char_id(char_id)
    path = resolve_path(MemoryScope.reality_scope(user_id, resolved), "memory_digest")
    if not path.exists():
        return {"user_id": user_id, "char_id": resolved, "content": ""}
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        content = ""
    return {"user_id": user_id, "char_id": resolved, "content": content}


# ── 短期记忆 ──────────────────────────────────────────────────────────────────

@router.get("/{user_id}/short-term", summary="获取短期记忆")
async def get_short_term(
    user_id: str,
    char_id: str | None = None,
    auth=Depends(require_scopes("memory.read")),
):
    """返回用户最近的对话历史（滚动窗口内的全部消息）。

    char_id 为空时使用当前 active_character；非法 char_id 返回错误，不默认 yexuan。
    """
    from core.memory import short_term
    resolved = _resolve_char_id(char_id)
    history = short_term.load(user_id, char_id=resolved)
    return {"user_id": user_id, "char_id": resolved, "history": history, "count": len(history)}


@router.delete("/{user_id}/short-term", summary="清除短期记忆")
async def clear_short_term(
    user_id: str,
    char_id: str | None = None,
    auth=Depends(require_scopes("admin")),
):
    """清空用户短期对话历史（写入空列表）。

    char_id 为空时使用当前 active_character；非法 char_id 返回错误，不跨角色清理。
    """
    from core.memory import short_term
    resolved = _resolve_char_id(char_id)
    short_term.clear(user_id, char_id=resolved)
    return {"message": f"用户 {user_id} 角色 {resolved} 短期记忆已清除", "char_id": resolved}


# ── 细粒度只读浏览端点（W4：补齐 list 读接口，供管理面板记忆管理页浏览后按需删除）───

@router.get("/{user_id}/episodic", summary="列出情景记忆条目")
async def list_episodic(
    user_id: str,
    char_id: str | None = None,
    auth=Depends(require_scopes("memory.read")),
):
    from core.memory import episodic_memory
    resolved = _resolve_char_id(char_id)
    entries = episodic_memory.list_episodes(user_id, char_id=resolved)
    return {"user_id": user_id, "char_id": resolved, "entries": entries, "count": len(entries)}


@router.get("/{user_id}/mid-term", summary="列出中期记忆事件")
async def list_mid_term(
    user_id: str,
    char_id: str | None = None,
    auth=Depends(require_scopes("memory.read")),
):
    from core.memory import mid_term
    resolved = _resolve_char_id(char_id)
    events = mid_term.load(user_id, char_id=resolved)
    return {"user_id": user_id, "char_id": resolved, "events": events, "count": len(events)}


@router.get("/{user_id}/user-facts", summary="获取全局用户事实（跨角色，global scope）")
async def list_user_facts(
    user_id: str,
    auth=Depends(require_scopes("memory.read")),
):
    from core.memory import user_facts
    facts = user_facts.load_user_facts(user_id)
    return {"user_id": user_id, "facts": facts}


@router.get("/{user_id}/event-log", summary="列出有事件日志的日期")
async def list_event_log_days(
    user_id: str,
    char_id: str | None = None,
    auth=Depends(require_scopes("memory.read")),
):
    from core.memory import event_log
    resolved = _resolve_char_id(char_id)
    days = event_log.list_days(user_id, char_id=resolved)
    return {"user_id": user_id, "char_id": resolved, "days": days, "count": len(days)}


# ── 细粒度遗忘端点 ────────────────────────────────────────────────────────────

class _FactBody(BaseModel):
    text: str
    tag: Optional[str] = "misc"


class _DimBody(BaseModel):
    text: str
    confidence: Optional[float] = 1.0
    evidence_count: Optional[int] = 1


# episodic

@router.delete("/{user_id}/episodic/{ep_id}", summary="删除一条情景记忆（连带删向量）")
async def delete_episodic_entry(
    user_id: str,
    ep_id: str,
    char_id: Optional[str] = None,
    auth=Depends(require_scopes("admin")),
):
    from core.memory import episodic_memory
    resolved = _resolve_char_id(char_id)
    ok = episodic_memory.delete_episode(user_id, ep_id, char_id=resolved)
    if not ok:
        raise HTTPException(status_code=404, detail=f"episodic 条目 {ep_id!r} 不存在")
    return {"message": f"情景记忆 {ep_id!r} 已删除", "char_id": resolved}


# profile.important_facts

@router.delete("/{user_id}/profile/important-facts/{index}", summary="删除一条 important_fact")
async def delete_profile_fact(
    user_id: str,
    index: int,
    char_id: Optional[str] = None,
    auth=Depends(require_scopes("admin")),
):
    from core.memory import user_profile
    resolved = _resolve_char_id(char_id)
    ok = user_profile.delete_important_fact(user_id, index, char_id=resolved)
    if not ok:
        raise HTTPException(status_code=404, detail=f"important_facts 下标 {index} 不存在")
    return {"message": f"important_fact[{index}] 已删除", "char_id": resolved}


@router.put("/{user_id}/profile/important-facts/{index}", summary="覆盖一条 important_fact")
async def overwrite_profile_fact(
    user_id: str,
    index: int,
    body: _FactBody,
    char_id: Optional[str] = None,
    auth=Depends(require_scopes("admin")),
):
    from core.memory import user_profile
    resolved = _resolve_char_id(char_id)
    ok = user_profile.overwrite_important_fact(user_id, index, body.text, char_id=resolved, tag=body.tag or "misc")
    if not ok:
        raise HTTPException(status_code=404, detail=f"important_facts 下标 {index} 不存在")
    return {"message": f"important_fact[{index}] 已覆盖", "char_id": resolved}


# user_identity

@router.delete("/{user_id}/identity/{key}", summary="删除一个 user_identity 维度")
async def delete_identity_dim(
    user_id: str,
    key: str,
    char_id: Optional[str] = None,
    auth=Depends(require_scopes("admin")),
):
    from core.memory import user_identity
    resolved = _resolve_char_id(char_id)
    if key not in {k for k, _ in user_identity.IDENTITY_DIMENSIONS}:
        raise HTTPException(status_code=422, detail=f"未知维度 key={key!r}")
    ok = await user_identity.delete_dimension(user_id, key, char_id=resolved)
    if not ok:
        raise HTTPException(status_code=404, detail=f"维度 {key!r} 不存在")
    return {"message": f"identity 维度 {key!r} 已删除", "char_id": resolved}


@router.put("/{user_id}/identity/{key}", summary="覆盖一个 user_identity 维度")
async def overwrite_identity_dim(
    user_id: str,
    key: str,
    body: _DimBody,
    char_id: Optional[str] = None,
    auth=Depends(require_scopes("admin")),
):
    from core.memory import user_identity
    resolved = _resolve_char_id(char_id)
    if key not in {k for k, _ in user_identity.IDENTITY_DIMENSIONS}:
        raise HTTPException(status_code=422, detail=f"未知维度 key={key!r}")
    ok = await user_identity.overwrite_dimension(
        user_id, key, body.text,
        char_id=resolved,
        confidence=body.confidence or 1.0,
        evidence_count=body.evidence_count or 1,
    )
    if not ok:
        raise HTTPException(status_code=500, detail="写入失败")
    return {"message": f"identity 维度 {key!r} 已覆盖", "char_id": resolved}


# mid_term

@router.delete("/{user_id}/mid-term/{mid_id}", summary="删除一条中期记忆事件")
async def delete_midterm_event(
    user_id: str,
    mid_id: str,
    char_id: Optional[str] = None,
    auth=Depends(require_scopes("admin")),
):
    from core.memory import mid_term
    resolved = _resolve_char_id(char_id)
    ok = mid_term.delete_event(user_id, mid_id, char_id=resolved)
    if not ok:
        raise HTTPException(status_code=404, detail=f"mid_term 条目 mid_id={mid_id!r} 不存在")
    return {"message": f"mid_term 条目 {mid_id!r} 已删除", "char_id": resolved}


# user_facts (global-scope, no char_id)

@router.delete("/{user_id}/user-facts/{key}", summary="删除一条全局用户事实")
async def delete_user_fact_entry(
    user_id: str,
    key: str,
    auth=Depends(require_scopes("admin")),
):
    from core.memory import user_facts
    ok = user_facts.delete_user_fact(user_id, key)
    if not ok:
        raise HTTPException(status_code=404, detail=f"user_facts key={key!r} 不存在或不允许删除")
    return {"message": f"user_facts[{key!r}] 已删除"}


# event_log

@router.delete("/{user_id}/event-log/{date_str}", summary="删除一天的事件日志文件")
async def delete_event_log_day(
    user_id: str,
    date_str: str,
    char_id: Optional[str] = None,
    auth=Depends(require_scopes("admin")),
):
    """date_str 格式 YYYY-MM-DD。仅删除新布局文件，不影响旧路径或 full_log.md。"""
    import re
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_str):
        raise HTTPException(status_code=422, detail="date_str 格式须为 YYYY-MM-DD")
    from core.memory import event_log
    resolved = _resolve_char_id(char_id)
    ok = event_log.delete_day(user_id, date_str, char_id=resolved)
    if not ok:
        raise HTTPException(status_code=404, detail=f"事件日志 {date_str} 不存在")
    return {"message": f"事件日志 {date_str} 已删除", "char_id": resolved}


# TODO(Step 8): GET /fixation/status?uid=...
#   返回该 uid 的 fixation_state + 最近 20 条 fixation.jsonl 日志。
#   实现要点：
#     from core.memory.fixation_pipeline import _load_fixation_state, _should_consolidate
#     from core.sandbox import get_paths
#     log_path = get_paths().fixation_log()
#     lines = log_path.read_text(encoding="utf-8").splitlines()[-20:] if log_path.exists() else []
#     records = [json.loads(l) for l in lines if f'"uid": "{uid}"' in l]
#     return {"fixation_state": _load_fixation_state(uid), "recent_logs": records}
