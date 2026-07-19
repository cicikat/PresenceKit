"""
Dream Stage (群聊梦境) 端点 — Brief 100 §3。

契约冻结：本文件的路径/方法/请求响应 shape 是 desktop Brief 38 的依赖面，
改动前先读 cc-tasks/100-群聊梦境DreamStage后端v1.md §3。

POST  /group/{id}/dream/enter    — 入梦：冻结世界/lore/逐角色快照/relations；冲突 409
POST  /group/{id}/dream/send     — {content} → {round_id, status:"accepted"}，异步整轮，WS 推送
POST  /group/{id}/dream/exit     — 无条件硬退（Invariant D）
GET   /group/{id}/dream/state    — 对齐单人 /dream/state shape；char_tension 为映射，新增 roster
GET   /group/{id}/dream/transcript — 轮询式读取共享 transcript（?after=cursor），供无 WS 客户端
                                     （mobile）使用；只读 dream_store，不接桌面 WS/mobile_queue 任何推送路径
GET   /group/{id}/dream/settings — 见 core.stage.dream_settings schema
PATCH /group/{id}/dream/settings — 同上，枚举校验对齐单人 settings

不变量（Brief 100 §0，全部靠"没接线"落实，不是过滤）：
- v1 仅 sandbox；scenario / mirror / D4.5 硬禁用（core.dream.dream_prompt 的
  dream_domain="group" 守卫）
- 零回流：绝不 import core.stage.projection / core.memory.fixation_pipeline /
  core.dream.dream_exit_afterglow / core.dream.distill_impression
- hard_exit 绝对：/dream/exit 无条件成功，不检查当前状态
- Phase R/T 强制关闭：core.stage.dream_runtime._load_dream_stage() 里
  max_reactions=0 / topic_seed_prob=0 写死
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from itertools import combinations

from fastapi import APIRouter, Depends, HTTPException

from admin.auth import require_scopes
from core.dream.body_state import BodyState
from core.dream.dream_state import DreamGuardStatus, DreamStatus, derive_dream_state_projection, get_reality_guard_status
from core.stage.dream_settings import load as load_dream_group_settings, save as save_dream_group_settings
from core.stage.dream_state import (
    clear_local_state as clear_dream_group_local_state,
    default_state as default_dream_group_state,
    is_active as is_group_dream_active,
    read_state as read_dream_group_state,
    write_state as write_dream_group_state,
)
from core.stage.dream_store import archive_dream_transcript, clear_dream_transcript, load_dream_transcript
from core.stage.models import Stage
from core.stage.store import load_stage

router = APIRouter()
logger = logging.getLogger(__name__)

_VALID_BOUNDARY_LEVEL = frozenset({"vague", "body_perceptible", "numbers_visible", "threshold_break"})
_SAFE_PRESET_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
_PATCH_ALLOWED = frozenset({"world_layer", "enable_dream_lorebook", "boundary_level", "jailbreak_presets", "per_char"})


def _require_reality_stage(group_id: str) -> Stage:
    stage = load_stage(group_id)
    if stage is None:
        raise HTTPException(status_code=404, detail=f"群 {group_id!r} 不存在")
    if stage.domain != "reality":
        # Groups are always created via POST /group/create with domain="reality"
        # (that endpoint rejects domain="dream" outright) — a Dream Stage is a
        # session layered on top of an existing reality group, not its own
        # group type. Seeing anything else here means the underlying group
        # meta was corrupted or hand-edited.
        raise HTTPException(status_code=409, detail=f"群 {group_id!r} 不是标准 reality 群，无法开始群聊梦境")
    return stage


def _valid_world_layer_values() -> frozenset[str]:
    from admin.routers.dream import _valid_world_layer_values as _solo_valid_world_layer_values
    return _solo_valid_world_layer_values()


# ── enter ────────────────────────────────────────────────────────────────────

async def _build_per_char_snapshots(stage: Stage, entry_reason: str) -> dict:
    from core.character_name_provider import get_char_name
    from core.dream.dream_context import build_snapshot

    snapshots: dict = {}
    for char_id in stage.roster:
        try:
            char_name = get_char_name(char_id)
        except Exception:
            char_name = char_id
        snapshot = await build_snapshot(
            stage.owner_uid, entry_reason=entry_reason, char_id=char_id, char_name=char_name,
        )
        snapshots[char_id] = _force_card_only(snapshot)
    return snapshots


def _force_card_only(snapshot: dict) -> dict:
    """Brief 100 §0: group dream memory_access is fixed to card_only, not
    configurable. `build_snapshot()` decides its tier from the owner's *solo*
    dream settings (unrelated to this group), so its result may carry
    relationship_summary/full_snapshot-tier fields — strip back down to
    exactly the card_only shape rather than trusting whatever tier it picked.
    """
    allowed_passthrough = {"created_at", "user_id", "boundary", "entry_reason", "relationship_state"}
    stripped = {
        k: v for k, v in snapshot.items()
        if k in allowed_passthrough or k.endswith("_awareness")
    }
    stripped["memory_access"] = "card_only"
    for key in (
        "recent_reality_context", "recent_reality_gist",
        "episodic_summary", "mid_term_context", "profile_impression",
    ):
        stripped[key] = ""
    # D4.5 is hard-disabled at the prompt layer for dream_domain="group" —
    # also drop the raw snapshot field so it can never leak through any
    # future reader that forgets that guard.
    stripped.pop("user_hidden_state_snapshot", None)
    return stripped


def _build_frozen_relations(roster: tuple[str, ...]) -> dict:
    from core.stage.char_relations import load_relation

    frozen: dict = {}
    for char_a, char_b in combinations(sorted(roster), 2):
        relation = load_relation(char_a, char_b)
        if relation:
            frozen[f"{char_a}__{char_b}"] = relation
    return frozen


@router.post("/{group_id}/dream/enter", summary="群聊入梦")
async def group_dream_enter(group_id: str, body: dict | None = None, _auth=Depends(require_scopes("chat"))):
    body = body or {}
    stage = _require_reality_stage(group_id)
    entry_reason = str(body.get("entry_reason") or "").strip()

    if is_group_dream_active(group_id):
        raise HTTPException(status_code=409, detail=f"群 {group_id!r} 已有正在进行的群聊梦境")

    from core.dream.dream_state import read_state as read_solo_state
    solo_state = read_solo_state(stage.owner_uid)
    if solo_state.get("status") in (DreamStatus.DREAM_ACTIVE.value, DreamStatus.DREAM_CLOSING.value):
        raise HTTPException(status_code=409, detail="owner 正在进行单人梦境，无法同时开始群聊梦境")

    from core.conversation_gate import conversation_lock
    if conversation_lock(stage.owner_uid).locked():
        raise HTTPException(status_code=409, detail="该 owner 当前有对话正在处理，请稍后重试")

    settings = load_dream_group_settings(group_id)
    world_id = settings.get("world_layer", "reality_derived")

    per_char_snapshots = await _build_per_char_snapshots(stage, entry_reason)
    frozen_relations = _build_frozen_relations(stage.roster)

    dream_id = f"dream_group_{group_id}_{int(time.time())}"
    state = default_dream_group_state(group_id, owner_uid=stage.owner_uid)
    state["status"] = DreamStatus.DREAM_ACTIVE.value
    state["dream_id"] = dream_id
    state["dream_started_at"] = time.time()
    state["frozen_world"] = world_id
    state["per_char_snapshots"] = per_char_snapshots
    state["frozen_relations"] = frozen_relations
    state["char_tension"] = {char_id: 0.0 for char_id in stage.roster}
    state["body_state"] = {}
    state["scene_state"] = None
    state["symbolic_anchors"] = []
    state["flow_entries"] = []
    write_dream_group_state(group_id, state)
    clear_dream_transcript(group_id)

    logger.info("[group_dream] entered group=%s dream_id=%s roster=%s", group_id, dream_id, stage.roster)
    return {"ok": True, "dream_id": dream_id, "roster": list(stage.roster)}


# ── send ─────────────────────────────────────────────────────────────────────

@router.post("/{group_id}/dream/send", summary="群聊梦境发言（异步整轮，WS 推送）")
async def group_dream_send(group_id: str, body: dict, _auth=Depends(require_scopes("chat"))):
    _require_reality_stage(group_id)
    if not is_group_dream_active(group_id):
        raise HTTPException(status_code=409, detail=f"群 {group_id!r} 当前没有进行中的梦境")

    content = str(body.get("content") or "").strip()
    if not content:
        raise HTTPException(status_code=422, detail="content 不能为空")

    round_id = uuid.uuid4().hex

    async def _run():
        try:
            from core.stage.dream_runtime import run_dream_stage_turn
            await run_dream_stage_turn(group_id, content, fanout=True, round_id=round_id)
        except Exception:
            logger.exception("[group_dream_send] dream stage turn failed group=%s round=%s", group_id, round_id)
            try:
                from channels import desktop_ws as _dws
                if _dws.is_connected():
                    await _dws.push_group_round_end(round_id, group_id, domain="dream")
            except Exception:
                pass

    asyncio.create_task(_run())
    return {"round_id": round_id, "status": "accepted"}


# ── exit ─────────────────────────────────────────────────────────────────────

@router.post("/{group_id}/dream/exit", summary="群聊梦境硬退出（Invariant D，无条件成功）")
async def group_dream_exit(group_id: str, _auth=Depends(require_scopes("chat"))):
    _require_reality_stage(group_id)

    state = read_dream_group_state(group_id)
    dream_id = str(state.get("dream_id") or "")
    if dream_id:
        archive_dream_transcript(group_id, dream_id)
    clear_dream_transcript(group_id)

    state = clear_dream_group_local_state(state)
    state["status"] = DreamStatus.REALITY_CHAT.value
    write_dream_group_state(group_id, state)

    logger.info("[group_dream] exited group=%s dream_id=%s", group_id, dream_id)
    return {"ok": True, "exited": True}


# ── state ────────────────────────────────────────────────────────────────────

@router.get("/{group_id}/dream/state", summary="读取群聊梦境状态（只读）")
async def group_dream_state_get(group_id: str, _auth=Depends(require_scopes("chat"))):
    stage = _require_reality_stage(group_id)
    state = read_dream_group_state(group_id)
    body = BodyState.from_dict(state.get("body_state") or {})

    base = {
        "status": state.get("status", DreamStatus.REALITY_CHAT.value),
        "dream_id": state.get("dream_id"),
        "roster": list(stage.roster),
        "frozen_world": state.get("frozen_world"),
        "body": {
            "heat": round(body.heat, 2),
            "sensitivity": round(body.sensitivity, 2),
            "tension": round(body.tension, 2),
        },
        "char_tension": dict(state.get("char_tension") or {}),
        "scene_state": state.get("scene_state"),
        "symbolic_anchors": list(state.get("symbolic_anchors") or []),
        "flow_entries": list(state.get("flow_entries") or []),
    }
    base.update(derive_dream_state_projection(state))
    base["blocks_chat"] = get_reality_guard_status(stage.owner_uid) != DreamGuardStatus.ALLOW
    return base


# ── transcript（Brief 100 之后新增：手机端无 WS，靠轮询拿逐条发言）───────────────

@router.get("/{group_id}/dream/transcript", summary="轮询式读取群聊梦境发言（供无 WS 客户端如手机端使用）")
async def group_dream_transcript_get(group_id: str, after: int = 0, _auth=Depends(require_scopes("chat"))):
    """`after` 是上次响应里的 `cursor`（已消费的条数），返回其后的新增发言。

    独立于桌面端 WS 推送线：只读 dream_store 的共享 transcript 文件，不复用
    desktop_ws/device_ws/ui_push 任何一条推送路径，天然不会跟桌面 WS 或现实群聊
    mobile_queue 打架（Brief 100 §0 零回流同源考量——这里只是多一个读法，不是新
    的写路径）。
    """
    stage = _require_reality_stage(group_id)
    entries = load_dream_transcript(group_id)
    after = max(0, after)
    state = read_dream_group_state(group_id)
    return {
        "status": state.get("status", DreamStatus.REALITY_CHAT.value),
        "dream_id": state.get("dream_id"),
        "cursor": len(entries),
        "entries": [
            {
                "index": after + i,
                "speaker_id": entry.speaker_id,
                "is_owner": entry.speaker_id == stage.owner_uid,
                "content": entry.content,
                "timestamp": entry.timestamp,
                "round_id": entry.turn_id,
            }
            for i, entry in enumerate(entries[after:])
        ],
    }


# ── settings ─────────────────────────────────────────────────────────────────

@router.get("/{group_id}/dream/settings", summary="读群聊梦境设置")
async def group_dream_settings_get(group_id: str, _auth=Depends(require_scopes("chat"))):
    _require_reality_stage(group_id)
    return load_dream_group_settings(group_id)


@router.patch("/{group_id}/dream/settings", summary="改群聊梦境设置（部分更新）")
async def group_dream_settings_patch(group_id: str, body: dict, _auth=Depends(require_scopes("chat"))):
    stage = _require_reality_stage(group_id)
    updates = {k: v for k, v in body.items() if k in _PATCH_ALLOWED}
    if not updates:
        raise HTTPException(status_code=422, detail=f"可更新字段：{sorted(_PATCH_ALLOWED)}")

    errors: list[str] = []

    if "boundary_level" in updates and updates["boundary_level"] not in _VALID_BOUNDARY_LEVEL:
        errors.append(f"boundary_level={updates['boundary_level']!r} 非法，有效值：{sorted(_VALID_BOUNDARY_LEVEL)}")

    if "world_layer" in updates:
        valid_worlds = _valid_world_layer_values()
        if updates["world_layer"] not in valid_worlds:
            errors.append(f"world_layer={updates['world_layer']!r} 非法，有效值：{sorted(valid_worlds)}")

    if "enable_dream_lorebook" in updates and not isinstance(updates["enable_dream_lorebook"], bool):
        errors.append(f"enable_dream_lorebook 必须为 bool，收到：{updates['enable_dream_lorebook']!r}")

    def _validate_presets(label: str, val) -> None:
        # 空列表合法：D0 回退链（Brief 100 §1）允许 per_char / 群级同时缺失，
        # 由 resolve_jailbreak_presets() 逐级回退到 default.md，不是错误状态。
        if not isinstance(val, list) or len(val) > 10:
            errors.append(f"{label} 必须为列表（最多 10 项，留空 = 回退默认）")
            return
        for item in val:
            if not isinstance(item, str) or not _SAFE_PRESET_RE.match(item):
                errors.append(f"{label} 条目 {item!r} 非法，只允许字母/数字/下划线/短横线（1-64字符）")

    if "jailbreak_presets" in updates:
        _validate_presets("jailbreak_presets", updates["jailbreak_presets"])

    if "per_char" in updates:
        val = updates["per_char"]
        if not isinstance(val, dict):
            errors.append(f"per_char 必须为对象，收到：{val!r}")
        else:
            for char_id, entry in val.items():
                if char_id not in stage.roster:
                    errors.append(f"per_char 中的 {char_id!r} 不在本群 roster 内")
                    continue
                if not isinstance(entry, dict) or set(entry) - {"jailbreak_presets"}:
                    errors.append(f"per_char[{char_id!r}] 只允许 jailbreak_presets 字段")
                    continue
                if "jailbreak_presets" in entry:
                    _validate_presets(f"per_char[{char_id!r}].jailbreak_presets", entry["jailbreak_presets"])

    if errors:
        raise HTTPException(status_code=422, detail="; ".join(errors))

    current = load_dream_group_settings(group_id)
    current.update(updates)
    saved = save_dream_group_settings(group_id, current)
    return {"ok": True, "settings": saved}
