"""
Group Chat 路由

GET  /group/list                  — 列出所有 Stage 群
POST /group/create                — 建群（roster + domain + settings）
GET  /group/{group_id}            — 取群详情（roster + settings + 近期 transcript）
POST /group/{group_id}/send       — 触发 arbiter 一轮（异步，立即返回 round_id）
GET  /group/{group_id}/history    — 分页 transcript（?before=<timestamp>）
GET  /group/{group_id}/settings   — 读群设置
PATCH /group/{group_id}/settings  — 改群设置
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import replace
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from admin.auth import require_scopes
from core.sandbox import get_paths
from core.stage.models import Stage, StageSettings, now_iso
from core.stage.store import (
    append_transcript,
    create_stage,
    delete_stage,
    load_stage,
    load_transcript,
    save_stage,
)

router = APIRouter()
logger = logging.getLogger(__name__)

_RECENT_TRANSCRIPT_LIMIT = 50
_HISTORY_PAGE_SIZE = 50
_ARBITER_TRACE_DEFAULT_LIMIT = 100
_ARBITER_TRACE_MAX_LIMIT = 500


# ── helpers ──────────────────────────────────────────────────────────────────

def _roster_members(roster: tuple[str, ...]) -> list[dict]:
    from core.asset_registry import get_registry
    reg = get_registry()
    members = []
    for char_id in roster:
        try:
            entry = reg.resolve(char_id, "character")
            members.append({
                "char_id": char_id,
                "label": entry.label,
                "avatar_url": entry.avatar_url,
            })
        except ValueError:
            members.append({"char_id": char_id, "label": char_id, "avatar_url": None})
    return members


def _stage_title(stage: Stage) -> str:
    from core.asset_registry import get_registry
    reg = get_registry()
    labels = []
    for char_id in stage.roster:
        try:
            entry = reg.resolve(char_id, "character")
            labels.append(entry.label)
        except ValueError:
            labels.append(char_id)
    return "、".join(labels) + " 的群聊" if labels else stage.group_id


def _summary(stage: Stage) -> dict:
    return {
        "group_id": stage.group_id,
        "domain": stage.domain,
        "status": stage.status,
        "roster": _roster_members(stage.roster),
        "title": _stage_title(stage),
    }


def _settings_dict(settings: StageSettings) -> dict:
    return {
        "min_responders": settings.min_responders,
        "max_responders": settings.max_responders,
        "max_ai_chain_depth": settings.max_ai_chain_depth,
        "respond_threshold": settings.respond_threshold,
        "spontaneous_threshold": settings.spontaneous_threshold,
        "addressed_exclusive": settings.addressed_exclusive,
        "allow_silent_rounds": settings.allow_silent_rounds,
        "transcript_limit": settings.transcript_limit,
        "group_memory_strength": settings.group_memory_strength,
        "debug_token_log": settings.debug_token_log,
        "talkativeness": dict(settings.talkativeness),
    }


def _entry_to_message(entry) -> dict:
    return {
        "msg_id": entry.turn_id,
        "speaker_id": entry.speaker_id,
        "content": entry.content,
        "timestamp": entry.timestamp,
        "triggered_by": entry.triggered_by,
    }


def _get_owner_uid() -> str:
    from core.config_loader import get_config
    return str(get_config().get("scheduler", {}).get("owner_id", "owner"))


def _require_stage(group_id: str) -> Stage:
    stage = load_stage(group_id)
    if stage is None:
        raise HTTPException(status_code=404, detail=f"群 {group_id!r} 不存在")
    return stage


# ── list ─────────────────────────────────────────────────────────────────────

@router.get("/list", summary="列出所有群")
async def list_groups(_auth=Depends(require_scopes("chat"))):
    groups_dir = get_paths().stage_group_dir(group_id="_dummy").parent
    if not groups_dir.exists():
        return []
    results = []
    for meta_path in sorted(groups_dir.glob("*/meta.json")):
        stage = load_stage(meta_path.parent.name)
        if stage is not None:
            results.append(_summary(stage))
    return results


# ── create ───────────────────────────────────────────────────────────────────

@router.post("/create", summary="建群")
async def create_group(body: dict, _auth=Depends(require_scopes("chat"))):
    roster: list[str] = body.get("roster") or []
    if not roster:
        raise HTTPException(status_code=422, detail="roster 不能为空")
    domain: str = body.get("domain", "reality")
    if domain not in ("reality", "dream"):
        raise HTTPException(status_code=422, detail="domain 必须是 reality 或 dream")
    if domain == "dream":
        raise HTTPException(
            status_code=422,
            detail="dream 群聊 v1 未开放，请使用 reality",
        )
    settings_raw: dict = body.get("settings") or {}
    group_id: str = body.get("group_id") or uuid.uuid4().hex[:12]
    owner_uid = _get_owner_uid()

    try:
        settings = StageSettings.from_dict(settings_raw) if settings_raw else None
        stage = create_stage(
            group_id,
            owner_uid,
            roster,
            domain=domain,
            settings=settings,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    transcript = load_transcript(stage.group_id)
    return {
        **_summary(stage),
        "settings": _settings_dict(stage.settings),
        "recent": [_entry_to_message(e) for e in transcript[-_RECENT_TRANSCRIPT_LIMIT:]],
    }


# ── get detail ───────────────────────────────────────────────────────────────

@router.get("/{group_id}", summary="取群详情")
async def get_group(group_id: str, _auth=Depends(require_scopes("chat"))):
    stage = _require_stage(group_id)
    transcript = load_transcript(stage.group_id)
    return {
        **_summary(stage),
        "settings": _settings_dict(stage.settings),
        "recent": [_entry_to_message(e) for e in transcript[-_RECENT_TRANSCRIPT_LIMIT:]],
    }


@router.get("/{group_id}/relations", summary="读取群聊角色双向印象")
async def get_group_relations(group_id: str, _auth=Depends(require_scopes("memory.read"))):
    """Return the global pair records relevant to this Stage roster."""
    from itertools import combinations
    from core.stage.char_relations import load_relation

    stage = _require_stage(group_id)
    relations = [
        relation for pair in combinations(stage.roster, 2)
        if (relation := load_relation(*pair)) is not None
    ]
    return {"group_id": group_id, "relations": relations, "count": len(relations)}


@router.delete("/{group_id}/relations/{char_a}/{char_b}", summary="删除角色双向印象")
async def delete_group_relation(
    group_id: str,
    char_a: str,
    char_b: str,
    _auth=Depends(require_scopes("admin")),
):
    from core.stage.char_relations import delete_relation

    stage = _require_stage(group_id)
    if char_a not in stage.roster or char_b not in stage.roster or char_a == char_b:
        raise HTTPException(status_code=422, detail="角色必须是该群的两个不同成员")
    if not delete_relation(char_a, char_b, uid=stage.owner_uid):
        raise HTTPException(status_code=404, detail="角色关系不存在")
    return {"ok": True, "deleted": sorted((char_a, char_b))}


# ── send ─────────────────────────────────────────────────────────────────────

@router.post("/{group_id}/send", summary="触发 arbiter 一轮（异步）")
async def group_send(group_id: str, body: dict, _auth=Depends(require_scopes("chat"))):
    stage = _require_stage(group_id)
    if stage.status != "active":
        raise HTTPException(status_code=409, detail=f"群 {group_id!r} 已关闭")
    if stage.domain == "dream":
        raise HTTPException(status_code=422, detail="dream 群聊 v1 未开放")

    message: str = (body.get("message") or "").strip()
    if not message:
        raise HTTPException(status_code=422, detail="message 不能为空")

    round_id = uuid.uuid4().hex

    async def _run():
        try:
            from core.stage.runtime import run_reality_stage_turn
            await run_reality_stage_turn(
                group_id,
                message,
                fanout=True,
                round_id=round_id,
            )
        except Exception:
            logger.exception("[group_send] stage turn failed group=%s round=%s", group_id, round_id)
            # Push round_end even on error so the client can unlock its input box.
            try:
                from channels import desktop_ws as _dws
                if _dws.is_connected():
                    await _dws.push_group_round_end(round_id, group_id)
            except Exception:
                pass

    asyncio.create_task(_run())
    return {"round_id": round_id, "status": "accepted"}


# ── history ──────────────────────────────────────────────────────────────────

@router.get("/{group_id}/history", summary="读历史 transcript（分页）")
async def group_history(
    group_id: str,
    before: float | None = None,
    _auth=Depends(require_scopes("chat")),
):
    _require_stage(group_id)
    transcript = load_transcript(group_id)
    if before is not None:
        transcript = [e for e in transcript if e.timestamp < before]
    page = transcript[-_HISTORY_PAGE_SIZE:]
    return [_entry_to_message(e) for e in page]


@router.get("/{group_id}/arbiter-trace", summary="读仲裁决策 trace")
async def group_arbiter_trace(
    group_id: str,
    limit: int = _ARBITER_TRACE_DEFAULT_LIMIT,
    _auth=Depends(require_scopes("chat")),
):
    _require_stage(group_id)
    if limit < 1 or limit > _ARBITER_TRACE_MAX_LIMIT:
        raise HTTPException(status_code=422, detail=f"limit 必须在 1..{_ARBITER_TRACE_MAX_LIMIT}")
    path = get_paths().stage_arbiter_trace(group_id=group_id)
    if not path.exists():
        return []
    try:
        records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    except (OSError, json.JSONDecodeError):
        logger.debug("[group] arbiter trace read failed group=%s", group_id, exc_info=True)
        return []
    return list(reversed(records[-limit:]))


# ── settings get ─────────────────────────────────────────────────────────────

@router.get("/{group_id}/settings", summary="读群设置")
async def get_group_settings(group_id: str, _auth=Depends(require_scopes("chat"))):
    stage = _require_stage(group_id)
    return _settings_dict(stage.settings)


# ── settings patch ───────────────────────────────────────────────────────────

@router.delete("/{group_id}", summary="删除群（连同 transcript）")
async def delete_group(group_id: str, _auth=Depends(require_scopes("chat"))):
    ok = delete_stage(group_id)
    if not ok:
        raise HTTPException(status_code=404, detail="群不存在")
    return {"ok": True, "deleted": group_id}


@router.patch("/{group_id}/roster", summary="改群成员（加/减角色）")
async def patch_group_roster(group_id: str, body: dict, _auth=Depends(require_scopes("chat"))):
    stage = _require_stage(group_id)
    new_roster = [str(r).strip() for r in (body.get("roster") or []) if str(r).strip()]
    if not new_roster:
        raise HTTPException(status_code=422, detail="roster 不能为空")
    if len(set(new_roster)) != len(new_roster):
        raise HTTPException(status_code=422, detail="roster 不能含重复成员")
    from core.asset_registry import get_registry
    reg = get_registry()
    for char_id in new_roster:
        try:
            reg.resolve(char_id, "character")
        except ValueError:
            raise HTTPException(status_code=422, detail=f"角色 {char_id!r} 不存在")
    settings = stage.settings
    if settings.max_responders > len(new_roster):
        settings = replace(settings, max_responders=len(new_roster))
    updated = replace(stage, roster=tuple(new_roster), settings=settings, updated_at=now_iso())
    if not save_stage(updated):
        raise HTTPException(status_code=500, detail="保存失败")
    return {**_summary(updated), "settings": _settings_dict(updated.settings)}


@router.patch("/{group_id}/settings", summary="改群设置（部分更新）")
async def patch_group_settings(group_id: str, body: dict, _auth=Depends(require_scopes("chat"))):
    stage = _require_stage(group_id)
    current = stage.settings.to_dict()
    # Flatten memory_strength.group into group_memory_strength for from_dict.
    current_flat = {
        "min_responders": current["min_responders"],
        "max_responders": current["max_responders"],
        "max_ai_chain_depth": current["max_ai_chain_depth"],
        "respond_threshold": current["respond_threshold"],
        "spontaneous_threshold": current["spontaneous_threshold"],
        "addressed_exclusive": current["addressed_exclusive"],
        "allow_silent_rounds": current["allow_silent_rounds"],
        "transcript_limit": current["transcript_limit"],
        "memory_strength": {"group": current["memory_strength"]["group"]},
        "debug_token_log": current["debug_token_log"],
        "talkativeness": current["talkativeness"],
    }
    merged = {**current_flat, **{k: v for k, v in body.items() if k in current_flat}}
    try:
        new_settings = StageSettings.from_dict(merged)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    updated = replace(stage, settings=new_settings, updated_at=now_iso())
    if not save_stage(updated):
        raise HTTPException(status_code=500, detail="群设置保存失败")
    return _settings_dict(new_settings)
