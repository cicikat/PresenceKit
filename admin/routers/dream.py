"""
Dream session endpoints.

POST  /dream/enter    — enter dream (build frozen snapshot, DREAM_ACTIVE)
POST  /dream/chat     — dream turn (goes to dream_pipeline, never reality pipeline)
POST  /dream/exit     — hard exit (force_exit_dream, unconditional)
GET   /dream/state    — read-only UI panel state (projected fields only)
GET   /dream/settings — read full per-uid dream settings
PATCH /dream/settings — partial update (enum-validated; only affects next dream)

Invariants:
- /dream/chat never calls notify_owner_turn, never triggers scheduler/gating.
- conversation_lock(uid) wraps the full dream_turn for serialization safety.
- Hard reject: DREAM_ACTIVE / DREAM_CLOSING prevents reality endpoints from
  processing turns (safety net implemented in chat.py and mobile.py).
- GET /dream/state is pure read-only: never writes files, never triggers any
  reality pipeline or mood_state. Returns safe defaults (status=REALITY_CHAT)
  when no dream is active.
- PATCH /dream/settings NEVER writes into dream_state. frozen_world and
  lucid_mode are frozen at dream entry from settings; PATCH only affects the
  next dream session entered via POST /dream/enter.
"""

import logging
import re
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from admin.auth import require_scopes
from core.config_loader import get_config
from core.data_paths import DEFAULT_CHAR_ID

router = APIRouter()
logger = logging.getLogger(__name__)

# ── Enum validators for PATCH /dream/settings ─────────────────────────────────
_VALID_MEMORY_ACCESS = frozenset({"card_only", "relationship_summary", "full_snapshot"})
_VALID_BOUNDARY_LEVEL = frozenset({"vague", "body_perceptible", "numbers_visible", "threshold_break"})
# 内建六个世界包，独立于磁盘是否真的存在这些文件夹（world_loader 对缺失世界
# fail-open 回退到 _default 内容，不崩），保证 CI/fresh 环境里这六个值恒合法。
_VALID_WORLD_LAYER_BUILTIN = frozenset({"reality_derived", "abo", "vampire", "cat", "flower_bud", "custom"})
_VALID_LUCID_MODE = frozenset({"lucid_shared", "non_lucid"})
_VALID_DREAM_MODE = frozenset({"sandbox", "scenario", "mirror"})

_ENUM_VALIDATORS: dict[str, frozenset] = {
    "memory_access": _VALID_MEMORY_ACCESS,
    "boundary_level": _VALID_BOUNDARY_LEVEL,
    "lucid_mode": _VALID_LUCID_MODE,
}


def _valid_world_layer_values() -> frozenset[str]:
    """内建六个世界 ∪ 磁盘上实际发现的世界（含面板新建的自定义世界）∪ _default。

    _default 是保留兜底世界（Brief 96 §1）：删除当前世界时设置回退到它。

    直接扫描 get_paths().dream_worlds_dir()（与本文件世界管理端点用的是同一个
    accessor），不经 core.dream.world_loader.discover_worlds() —— 后者用的是
    模块级裸 Path("characters/dream_worlds")，不随 sandbox 测试夹具重定向，
    生产模式下两者指向同一目录、行为等价，但测试夹具下会读到不一致的目录。
    """
    from core.sandbox import get_paths
    worlds_dir = get_paths().dream_worlds_dir()
    discovered: frozenset[str] = frozenset()
    try:
        if worlds_dir.exists():
            discovered = frozenset(
                d.name for d in worlds_dir.iterdir()
                if d.is_dir() and not d.name.startswith("_")
            )
    except Exception:
        discovered = frozenset()
    return _VALID_WORLD_LAYER_BUILTIN | discovered | {"_default"}

_PATCH_ALLOWED = frozenset({
    "memory_access", "boundary_level", "world_layer", "lucid_mode",
    "enable_dream_lorebook", "jailbreak_presets", "display",
})

@router.get("/dream/invariants", summary="跨世界身份稳定性（只读）")
async def dream_invariants_get(_auth=Depends(require_scopes("activity"))):
    from core.pipeline_registry import get as _get_pipeline
    from core.dream.invariants import load
    pl = _get_pipeline()
    char_id = (pl._active_character_id if pl else None) or DEFAULT_CHAR_ID
    entries = load(_owner_uid(), char_id=char_id)
    entries.sort(key=lambda item: (bool(item.get("contradicted_by")), int(item.get("count") or 0)), reverse=True)
    return {"entries": entries}

_SAFE_PRESET_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def _owner_uid() -> str:
    uid = str(get_config().get("scheduler", {}).get("owner_id", "owner"))
    if not uid:
        raise HTTPException(status_code=503, detail="owner_id 未配置")
    return uid


@router.post("/dream/enter", summary="进入梦境")
async def dream_enter(body: dict = {}, _auth=Depends(require_scopes("activity"))):
    uid = _owner_uid()
    entry_reason = (body.get("entry_reason") or "").strip()
    dream_mode = (body.get("dream_mode") or "sandbox").strip()
    script_id = (body.get("script_id") or "").strip() or None

    if dream_mode not in _VALID_DREAM_MODE:
        raise HTTPException(
            status_code=422,
            detail=f"dream_mode={dream_mode!r} 非法，有效值：{sorted(_VALID_DREAM_MODE)}",
        )

    from core.pipeline_registry import get as _get_pipeline
    from core.dream.dream_pipeline import enter_dream

    pl = _get_pipeline()
    if pl is None:
        raise HTTPException(status_code=503, detail="pipeline not initialized")
    char_id = pl._active_character_id
    if not char_id:
        raise HTTPException(status_code=503, detail="active character not set")

    result = await enter_dream(
        uid, entry_reason=entry_reason, char_id=char_id,
        dream_mode=dream_mode, script_id=script_id,
    )
    if not result.get("ok"):
        raise HTTPException(status_code=409, detail=result.get("error", "cannot enter dream"))
    return result


@router.post("/dream/chat", summary="梦境对话（独立 pipeline）")
async def dream_chat(body: dict, _auth=Depends(require_scopes("activity"))):
    """
    Dream turn endpoint — routes to dream_pipeline, never to reality pipeline.

    conversation_lock(uid) serializes the full turn.
    Does NOT call notify_owner_turn, scheduler, or gating.
    """
    message = (body.get("message") or "").strip()
    if not message:
        raise HTTPException(status_code=422, detail="message 不能为空")

    uid = _owner_uid()

    from core.conversation_gate import conversation_lock
    from core.dream.dream_pipeline import dream_turn

    async with conversation_lock(uid):
        result = await dream_turn(uid, message)

    if err := result.get("error"):
        raise HTTPException(status_code=409, detail=err)

    # Brief 84: pseudo-stream typewriter replay for the dream reply. Generation
    # already finished above (dream_turn is fully isolated, zero WS side effects
    # by construction); the animation itself doesn't need conversation_lock.
    # fail-open: pseudo_stream_push never raises, msg_id lets the client dedup
    # against this HTTP response the same way owner chat's stream path does.
    reply = result.get("reply") or ""
    if reply:
        import uuid as _uuid

        from channels import ui_push as _ui_push
        from core.pipeline_registry import get as _get_pipeline

        _msg_id = _uuid.uuid4().hex
        _pl = _get_pipeline()
        _char_id = getattr(_pl, "_active_character_id", None) or ""
        try:
            await _ui_push.pseudo_stream_push(
                reply, msg_id=_msg_id, char_id=_char_id, profile="dream",
            )
        except Exception:
            logger.debug("[dream_chat] pseudo_stream_push failed", exc_info=True)
        result["msg_id"] = _msg_id

    return result


@router.post("/dream/exit", summary="强退梦境（硬出口，不可被拒）")
async def dream_exit(_auth=Depends(require_scopes("activity"))):
    """
    Hard exit — unconditional, immediate, penetrates all state.
    Cannot be disabled by config or role behavior (invariant D).
    """
    uid = _owner_uid()

    from core.dream.dream_pipeline import force_exit_dream
    await force_exit_dream(uid)

    return {"ok": True, "exited": True}


@router.post("/dream/wake", summary="软挽留闸门（满足门控时角色挽留一次；否则直接硬退）")
async def dream_wake(_auth=Depends(require_scopes("activity"))):
    """
    Soft retention gate called when user taps the WAKE button.

    - If status != DREAM_ACTIVE, OR retention already offered this dream, OR gate
      threshold not met → falls through to force_exit_dream immediately.
    - If gate passes → sets status=DREAM_EXIT_REQUESTED, generates one retention
      sentence, returns {"retained": True, "retention_text": "...", "dream_id": "..."}.
    - LLM failure in _generate_retention_line → falls back to immediate hard exit
      (fail-open: user is never blocked from leaving).

    Invariant D preserved: /dream/exit is untouched and always succeeds.
    """
    uid = _owner_uid()

    from core.dream.dream_state import DreamStatus, read_state, write_state
    from core.dream.dream_pipeline import (
        _should_retain, _generate_retention_line, force_exit_dream,
    )

    state = read_state(uid)
    status = state.get("status")
    dream_id = str(state.get("dream_id") or "").strip()

    # Not in an active dream → hard exit fallback (idempotent, safe)
    if status != DreamStatus.DREAM_ACTIVE.value:
        await force_exit_dream(uid)
        return {"retained": False, "exited": True}

    # Already offered retention this dream → hard exit (no repeated nagging)
    if state.get("retention_offered_dream_id") == dream_id:
        await force_exit_dream(uid)
        return {"retained": False, "exited": True}

    # Gate check: immersion + emotional threshold
    if not _should_retain(state):
        await force_exit_dream(uid)
        return {"retained": False, "exited": True}

    # Transition to EXIT_REQUESTED and mark retention offered
    from core.dream.dream_flow import append_status_shift
    state = append_status_shift(state, "exit_requested")
    state["status"] = DreamStatus.DREAM_EXIT_REQUESTED.value
    state["retention_offered_dream_id"] = dream_id
    write_state(uid, state)

    # Generate retention line — fail-open: LLM failure → hard exit
    retention_text = await _generate_retention_line(uid, state)
    if not retention_text:
        logger.warning("[dream_wake] retention LLM failed uid=%s, falling back to hard exit", uid)
        await force_exit_dream(uid)
        return {"retained": False, "exited": True}

    state = append_status_shift(state, "retained")
    write_state(uid, state)
    return {"retained": True, "retention_text": retention_text, "dream_id": dream_id}


@router.post("/dream/resume", summary="挽留后留下（status → DREAM_ACTIVE）")
async def dream_resume(_auth=Depends(require_scopes("activity"))):
    """
    Resume after a soft retention: set status back to DREAM_ACTIVE so
    dream_turn() can continue processing messages.

    Only acts when status == DREAM_EXIT_REQUESTED; any other status is a no-op
    (idempotent, safe to call spuriously).
    """
    uid = _owner_uid()

    from core.dream.dream_state import DreamStatus, read_state, write_state

    state = read_state(uid)
    if state.get("status") == DreamStatus.DREAM_EXIT_REQUESTED.value:
        state["status"] = DreamStatus.DREAM_ACTIVE.value
        write_state(uid, state)
        logger.info("[dream_resume] resumed uid=%s dream_id=%s", uid, state.get("dream_id"))

    return {"ok": True}


_BOUNDARY_FACTOR: dict[str, int] = {
    "vague": 10,
    "body_perceptible": 20,
    "numbers_visible": 35,
    "threshold_break": 35,
}


def _compute_hud_v0(state: dict, settings: dict, body) -> dict:
    """Compute Dream HUD v0 derived fields. Pure, no side effects, no I/O."""
    heat = body.heat
    sensitivity = body.sensitivity
    raw_tension = float(state.get("emotional_tension", 0.0))
    emotion_tension = round(raw_tension * 100)

    physiological_arousal = round(min(100.0, max(0.0, heat)))

    world = state.get("frozen_world") or settings.get("world_layer", "reality_derived")
    base_intimacy = (heat + sensitivity + emotion_tension) / 3.0
    if world == "abo":
        base_intimacy *= 1.2
    elif world == "cat":
        base_intimacy *= 0.8
    intimacy_tendency = round(min(100.0, max(0.0, base_intimacy)))

    boundary_factor = _BOUNDARY_FACTOR.get(settings.get("boundary_level", "body_perceptible"), 20)
    boundary_intrusion = round(min(100.0, max(0.0, heat * 0.4 + emotion_tension * 0.4 + boundary_factor)))

    anchor_score = min(len(list(state.get("symbolic_anchors") or [])) * 10, 40)
    obsession = round(min(100.0, max(0.0, emotion_tension * 0.7 + anchor_score * 0.3)))

    turn_factor = 10  # no turn_count tracked in v0
    dream_depth = round(min(100.0, max(0.0, (heat + sensitivity + turn_factor) / 3.0)))

    scene_bonus = 20 if state.get("scene_state") else 0
    dream_stability = round(min(100.0, max(0.0, 100 - emotion_tension * 0.4 - boundary_intrusion * 0.2 + scene_bonus)))

    if emotion_tension < 25:
        emotion_label = "平静"
    elif emotion_tension < 45:
        emotion_label = "专注"
    elif emotion_tension < 65:
        emotion_label = "克制"
    elif emotion_tension < 80:
        emotion_label = "紧绷"
    else:
        emotion_label = "临界"

    scene_state = state.get("scene_state")
    if scene_state:
        scene_label = scene_state
    elif dream_stability > 70:
        scene_label = "稳定"
    elif dream_depth > 70:
        scene_label = "下沉"
    elif boundary_intrusion > 60:
        scene_label = "边界波动"
    else:
        scene_label = "梦境中"

    return {
        "emotion_label": emotion_label,
        "scene_label": scene_label,
        "emotion_tension": emotion_tension,
        "boundary_intrusion": boundary_intrusion,
        "intimacy_tendency": intimacy_tendency,
        "obsession": obsession,
        "dream_stability": dream_stability,
        "dream_depth": dream_depth,
        "physiological_arousal": physiological_arousal,
    }


@router.get("/dream/state", summary="读取梦境状态（只读 UI 面板字段）")
async def dream_state_get(_auth=Depends(require_scopes("activity"))):
    """
    Read-only UI panel. Returns safe defaults when no dream is active.

    HUD v1: EMA-smoothed fields, anchor_charge injection, world multipliers.
    Persists smooth values to dream_hud_state.json (dream-local, cleared at close).
    Does not read mood_state, user_identity, or any reality store.

    body.{heat,sensitivity,tension} — user always sees own numbers (orthogonal to
      boundary_level, which controls the character's perception only).
    char_tension (yexuan_tension deprecated alias) — the character's dream-local
      emotional tension (0.0–1.0).
    HUD fields: emotion_label, scene_label, emotion_tension, boundary_intrusion,
      intimacy_tendency, obsession, dream_stability, dream_depth,
      physiological_arousal — all int 0–100.
    """
    uid = _owner_uid()
    from core.dream.dream_state import (
        read_state, DreamStatus, DreamGuardStatus,
        derive_dream_state_projection, get_reality_guard_status,
    )
    from core.dream.body_state import BodyState
    from core.dream.dream_settings import load as _load_settings
    from core.dream.dream_hud import derive_hud_v1, load_hud_state, save_hud_state

    state = read_state(uid)
    body = BodyState.from_dict(state.get("body_state") or {})
    settings = _load_settings(uid)

    dream_mode = state.get("dream_mode", "sandbox")
    scenario_info: dict | None = None
    if dream_mode == "scenario" and state.get("scenario_core"):
        _sc = state["scenario_core"]
        scenario_info = {
            "script_id": _sc.get("script_id"),
            "current_stage_id": _sc.get("current_stage_id"),
        }

    base = {
        "status": state.get("status", "REALITY_CHAT"),
        "dream_id": state.get("dream_id"),
        "dream_mode": dream_mode,
        "scenario": scenario_info,
        "frozen_world": state.get("frozen_world"),
        "lucid_mode": state.get("lucid_mode"),
        "body": {
            "heat": round(body.heat, 2),
            "sensitivity": round(body.sensitivity, 2),
            "tension": round(body.tension, 2),
        },
        "char_tension": float(state.get("emotional_tension", 0.0)),
        "yexuan_tension": float(state.get("emotional_tension", 0.0)),  # deprecated alias, see Brief 25 §3 P2
        "scene_state": state.get("scene_state"),
        "symbolic_anchors": list(state.get("symbolic_anchors") or []),
        "flow_entries": list(state.get("flow_entries") or []),
    }

    # Structured status projection for the desktop client (Brief 94 §2): replaces
    # the client's own blanket "正在做梦无法聊天" guess with real bucket + timing +
    # the actual chat-blocking verdict (mirrors chat.py's guard exactly).
    base.update(derive_dream_state_projection(state))
    base["blocks_chat"] = get_reality_guard_status(uid) != DreamGuardStatus.ALLOW

    # HUD v1: EMA smooth + anchor_charge + world corrections
    # When dream is not active we still compute (using zeroed body), but do not persist.
    dream_active = state.get("status") in (
        DreamStatus.DREAM_ACTIVE.value,
        DreamStatus.DREAM_CLOSING.value,
        DreamStatus.DREAM_EXIT_REQUESTED.value,
    )
    prev_smooth = load_hud_state(uid) if dream_active else {}
    smooth, hud = derive_hud_v1(state, settings, body, prev_smooth)
    if dream_active:
        save_hud_state(uid, smooth)

    base.update(hud)
    return base


@router.get("/dream/stats", summary="梦境次数统计（只读，有效梦 > N 轮）")
async def dream_stats_get(_auth=Depends(require_scopes("activity"))):
    from core.pipeline_registry import get as _get_pipeline
    from core.dream.dream_log import count_valid_dreams
    pl = _get_pipeline()
    char_id = (pl._active_character_id if pl else None) or DEFAULT_CHAR_ID
    return count_valid_dreams(char_id=char_id)


@router.get("/dream/settings", summary="读取梦境设置（全字段）")
async def dream_settings_get(_auth=Depends(require_scopes("activity"))):
    """Read-only: returns all dream settings fields with defaults applied."""
    uid = _owner_uid()
    from core.dream.dream_settings import load as _load
    return _load(uid)


@router.patch("/dream/settings", summary="部分更新梦境设置（校验枚举值；仅影响下一场梦）")
async def dream_settings_patch(body: dict, _auth=Depends(require_scopes("activity"))):
    """
    Partial update for dream settings. Validates enum values before writing.

    Allowed fields: memory_access / boundary_level / world_layer / lucid_mode /
                    enable_dream_lorebook / display

    ★ NEVER backfills into a running dream's frozen_world / lucid_mode.
      Those fields are frozen at dream entry (enter_dream reads from settings
      and copies into dream_state). PATCH only affects the next dream session.
      Changing world_layer while DREAM_ACTIVE does NOT change the current dream.
    """
    uid = _owner_uid()
    from core.dream.dream_settings import load as _load, save as _save

    updates = {k: v for k, v in body.items() if k in _PATCH_ALLOWED}
    if not updates:
        raise HTTPException(status_code=422, detail=f"可更新字段：{sorted(_PATCH_ALLOWED)}")

    errors: list[str] = []
    for key, valid_set in _ENUM_VALIDATORS.items():
        if key in updates:
            val = updates[key]
            if val not in valid_set:
                errors.append(f"{key}={val!r} 非法，有效值：{sorted(valid_set)}")
    if "world_layer" in updates:
        valid_worlds = _valid_world_layer_values()
        if updates["world_layer"] not in valid_worlds:
            errors.append(f"world_layer={updates['world_layer']!r} 非法，有效值：{sorted(valid_worlds)}")
    if "enable_dream_lorebook" in updates and not isinstance(updates["enable_dream_lorebook"], bool):
        errors.append(
            f"enable_dream_lorebook 必须为 bool，收到：{updates['enable_dream_lorebook']!r}"
        )
    if "jailbreak_presets" in updates:
        val = updates["jailbreak_presets"]
        if not isinstance(val, list) or len(val) == 0 or len(val) > 10:
            errors.append("jailbreak_presets 必须为非空列表（最多 10 项）")
        else:
            for item in val:
                if not isinstance(item, str) or not _SAFE_PRESET_RE.match(item):
                    errors.append(
                        f"jailbreak_presets 条目 {item!r} 非法，只允许字母/数字/下划线/短横线（1-64字符）"
                    )
    if "display" in updates:
        val = updates["display"]
        if not isinstance(val, dict):
            errors.append(f"display 必须为对象，收到：{val!r}")
        elif set(val) != {"physiological_arousal"}:
            errors.append("display 只允许 physiological_arousal 字段")
        elif not isinstance(val["physiological_arousal"], bool):
            errors.append(
                "display.physiological_arousal 必须为 bool，"
                f"收到：{val['physiological_arousal']!r}"
            )
    if errors:
        raise HTTPException(status_code=422, detail="; ".join(errors))

    current = _load(uid)
    current.update(updates)
    _save(uid, current)
    return {"ok": True, "settings": current}


# ── 梦境世界书/预设 Authoring ─────────────────────────────────────────────────

import re as _re
_SAFE_WORLD_RE = _re.compile(r'^[^/\\<>:"|?*\x00-\x1f]{1,64}$')
# world_loader.py 的兜底链依赖这两个目录名；允许被删/改名会让所有世界静默回退到
# 空内容（world_loader 对缺失内容 fail-open，不报错，退化不会被立刻发现）。
_RESERVED_WORLD_NAMES = frozenset({"_default", "reality_derived"})


def _validate_world_name(world: str, *, allow_reserved: bool = False) -> None:
    """校验世界名称：合法文件名字符集 + 不是 . / ..（防越出 dream_worlds_dir 一级）。

    allow_reserved=False 时额外拒绝 _default / reality_derived 及任意下划线开头
    的隐藏名（与 world_loader.discover_worlds() 的隐藏目录约定一致）。
    """
    if not _SAFE_WORLD_RE.match(world) or world in (".", ".."):
        raise HTTPException(status_code=422, detail=f"世界名称不合法: {world!r}")
    if not allow_reserved and (world in _RESERVED_WORLD_NAMES or world.startswith("_")):
        raise HTTPException(status_code=422, detail=f"{world!r} 是保留名，不能使用")


def _world_dir(world: str):
    """返回 characters/dream_worlds/{world}/ 路径，经 sandbox 验证。"""
    from core.sandbox import get_paths
    if not _SAFE_WORLD_RE.match(world) or world in (".", ".."):
        raise HTTPException(status_code=422, detail=f"世界名称不合法: {world!r}")
    p = get_paths().dream_worlds_dir() / world
    return p


def _preset_path(world: str):
    """返回 characters/dream_presets/{world}.md 路径。"""
    from core.sandbox import get_paths
    if not _SAFE_WORLD_RE.match(world) or world in (".", ".."):
        raise HTTPException(status_code=422, detail=f"世界名称不合法: {world!r}")
    return get_paths().dream_presets_dir() / f"{world}.md"


def _dream_active_referencing_world(world: str) -> bool:
    """当前是否有「进行中」的梦冻结着这个世界（DREAM_ACTIVE / CLOSING / EXIT_REQUESTED）。"""
    from core.dream.dream_state import read_state, DreamStatus
    uid = _owner_uid()
    state = read_state(uid)
    active_statuses = {
        DreamStatus.DREAM_ACTIVE.value,
        DreamStatus.DREAM_CLOSING.value,
        DreamStatus.DREAM_EXIT_REQUESTED.value,
    }
    return state.get("status") in active_statuses and state.get("frozen_world") == world


def _reset_world_layer_setting_if(match_world: str, reset_to: str) -> None:
    """若当前 dream_settings.world_layer == match_world，改写为 reset_to。"""
    from core.dream.dream_settings import load as _load_settings, save as _save_settings
    uid = _owner_uid()
    settings = _load_settings(uid)
    if settings.get("world_layer") == match_world:
        settings["world_layer"] = reset_to
        _save_settings(uid, settings)


def _ensure_default_world_template_seeded() -> Path:
    """确保 characters/dream_worlds/_default/ 存在，缺失文件从 tracked 模板补齐。

    characters/dream_worlds/ 整体在 .gitignore 内，fresh clone/release 包没有
    任何世界文件——包括 _default/ 本身。新建世界的骨架必须"有东西可复制"，
    所以这里先从随仓库发布的 defaults/dream_worlds/_default/ 播种，
    再返回补齐后的 characters/dream_worlds/_default/ 供调用方复制。
    """
    from core.sandbox import get_paths
    import shutil as _shutil

    template = get_paths().default_dream_world_template_dir()
    dest = get_paths().dream_worlds_dir() / "_default"
    dest.mkdir(parents=True, exist_ok=True)
    for name in ("ruleset.md", "mes_example.md", "vocab.json", "lorebook.yaml"):
        dest_file = dest / name
        if dest_file.exists():
            continue
        src_file = template / name
        if src_file.exists():
            _shutil.copy2(src_file, dest_file)
    return dest


@router.get("/dream/worlds", summary="列出梦境世界目录")
async def list_dream_worlds(_auth=Depends(require_scopes("activity"))):
    from core.sandbox import get_paths
    worlds_dir = get_paths().dream_worlds_dir()
    if not worlds_dir.exists():
        return {"worlds": []}
    worlds = sorted(
        d.name for d in worlds_dir.iterdir()
        if d.is_dir() and not d.name.startswith("_")
    )
    return {"worlds": worlds}


@router.post("/dream/worlds", summary="新建梦境世界（建文件夹 + 最小骨架）")
async def create_dream_world(body: dict, _auth=Depends(require_scopes("activity"))):
    """新建 characters/dream_worlds/{world}/，骨架文件从 _default 复制而来。

    _default 本身若因 fresh 安装缺失，先从 defaults/dream_worlds/_default/
    （tracked）播种一份，保证骨架"有东西可复制"（Brief 96 §1）。
    """
    world = (body.get("world") or "").strip()
    if not world:
        raise HTTPException(status_code=422, detail="world 不能为空")
    _validate_world_name(world)

    from core.sandbox import get_paths
    import shutil as _shutil

    dest = get_paths().dream_worlds_dir() / world
    if dest.exists():
        raise HTTPException(status_code=409, detail=f"世界 {world} 已存在")

    template = _ensure_default_world_template_seeded()
    dest.mkdir(parents=True, exist_ok=True)
    for name in ("ruleset.md", "mes_example.md", "vocab.json", "lorebook.yaml"):
        src = template / name
        if src.exists():
            _shutil.copy2(src, dest / name)

    label = (body.get("label") or "").strip()
    if label:
        import json as _json
        (dest / "meta.json").write_text(
            _json.dumps({"label": label}, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    return {"ok": True, "world": world}


@router.put("/dream/worlds/{world}/rename", summary="重命名梦境世界文件夹")
async def rename_dream_world(world: str, body: dict, _auth=Depends(require_scopes("activity"))):
    """重命名世界文件夹，并同步随世界名走的引用：

    - characters/dream_presets/{world}.md（若存在，按约定与世界同名）一并改名；
    - dream_settings.world_layer 若正指向旧名，改写为新名。
    - anchor_weights.json 是全局字符→权重表，不含世界名字符串，核实后确认无需同步。
    """
    _validate_world_name(world)
    new_name = (body.get("new_name") or "").strip()
    if not new_name:
        raise HTTPException(status_code=422, detail="new_name 不能为空")
    _validate_world_name(new_name)

    src = _world_dir(world)
    if not src.is_dir():
        raise HTTPException(status_code=404, detail=f"世界 {world} 不存在")
    dst = _world_dir(new_name)
    if dst.exists():
        raise HTTPException(status_code=409, detail=f"世界 {new_name} 已存在")

    if _dream_active_referencing_world(world):
        raise HTTPException(status_code=409, detail="该世界正被进行中的梦境使用，梦醒后再重命名")

    src.rename(dst)

    old_preset = _preset_path(world)
    if old_preset.exists():
        new_preset = _preset_path(new_name)
        new_preset.parent.mkdir(parents=True, exist_ok=True)
        old_preset.rename(new_preset)

    _reset_world_layer_setting_if(world, new_name)

    return {"ok": True, "world": new_name}


@router.delete("/dream/worlds/{world}", summary="删除梦境世界文件夹")
async def delete_dream_world(world: str, _auth=Depends(require_scopes("activity"))):
    """删除世界文件夹（二次确认由前端做）。

    - _default / reality_derived 拒删。
    - 正在被进行中的梦引用时拒绝。
    - 同名破限预设文件一并删除（避免遗留孤儿文件）。
    - 若为当前 dream_settings.world_layer，重置为 _default。
    """
    _validate_world_name(world)  # 已在此拒绝 _default / reality_derived

    target = _world_dir(world)
    if not target.is_dir():
        raise HTTPException(status_code=404, detail=f"世界 {world} 不存在")

    if _dream_active_referencing_world(world):
        raise HTTPException(status_code=409, detail="该世界正被进行中的梦境使用，梦醒后再删除")

    import shutil as _shutil
    _shutil.rmtree(target)

    preset = _preset_path(world)
    if preset.exists():
        preset.unlink()

    _reset_world_layer_setting_if(world, "_default")

    return {"ok": True, "deleted": world}


@router.get("/dream/worlds/{world}/lorebook", summary="读取梦境世界书条目列表")
async def get_dream_lorebook(world: str, _auth=Depends(require_scopes("activity"))):
    import yaml as _yaml
    p = _world_dir(world) / "lorebook.yaml"
    if not p.exists():
        return {"entries": []}
    try:
        data = _yaml.safe_load(p.read_text(encoding="utf-8")) or []
        if not isinstance(data, list):
            raise HTTPException(status_code=500, detail="lorebook.yaml 格式错误：应为裸 list")
        return {"entries": data}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取失败: {e}")


def _write_dream_lorebook(world: str, entries: list):
    import yaml as _yaml
    p = _world_dir(world) / "lorebook.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        _yaml.dump(entries, allow_unicode=True, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )


@router.post("/dream/worlds/{world}/lorebook", summary="新增梦境世界书条目")
async def add_dream_lore_entry(world: str, body: dict, _auth=Depends(require_scopes("activity"))):
    import yaml as _yaml
    p = _world_dir(world) / "lorebook.yaml"
    entries = []
    if p.exists():
        raw = _yaml.safe_load(p.read_text(encoding="utf-8")) or []
        entries = raw if isinstance(raw, list) else []

    keywords = body.get("keywords")
    content = body.get("content", "")
    if not keywords or not isinstance(keywords, list):
        raise HTTPException(status_code=422, detail="keywords 必须为非空列表")
    if not content.strip():
        raise HTTPException(status_code=422, detail="content 不能为空")

    entry = {
        "keywords": [str(k) for k in keywords],
        "content": str(content),
        "insertion_order": int(body.get("insertion_order", len(entries))),
        "regex": bool(body.get("regex", False)),
    }
    entries.append(entry)
    _write_dream_lorebook(world, entries)
    return {"ok": True, "index": len(entries) - 1, "entry": entry}


@router.put("/dream/worlds/{world}/lorebook/{index}", summary="修改梦境世界书条目")
async def update_dream_lore_entry(world: str, index: int, body: dict, _auth=Depends(require_scopes("activity"))):
    import yaml as _yaml
    p = _world_dir(world) / "lorebook.yaml"
    if not p.exists():
        raise HTTPException(status_code=404, detail="lorebook.yaml 不存在")
    entries = _yaml.safe_load(p.read_text(encoding="utf-8")) or []
    if not isinstance(entries, list) or index < 0 or index >= len(entries):
        raise HTTPException(status_code=404, detail=f"条目 {index} 不存在")

    entry = dict(entries[index])
    if "keywords" in body:
        kw = body["keywords"]
        if not isinstance(kw, list) or not kw:
            raise HTTPException(status_code=422, detail="keywords 必须为非空列表")
        entry["keywords"] = [str(k) for k in kw]
    if "content" in body:
        entry["content"] = str(body["content"])
    if "insertion_order" in body:
        entry["insertion_order"] = int(body["insertion_order"])
    if "regex" in body:
        entry["regex"] = bool(body["regex"])

    entries[index] = entry
    _write_dream_lorebook(world, entries)
    return {"ok": True, "index": index, "entry": entry}


@router.delete("/dream/worlds/{world}/lorebook/{index}", summary="删除梦境世界书条目")
async def delete_dream_lore_entry(world: str, index: int, _auth=Depends(require_scopes("activity"))):
    import yaml as _yaml
    p = _world_dir(world) / "lorebook.yaml"
    if not p.exists():
        raise HTTPException(status_code=404, detail="lorebook.yaml 不存在")
    entries = _yaml.safe_load(p.read_text(encoding="utf-8")) or []
    if not isinstance(entries, list) or index < 0 or index >= len(entries):
        raise HTTPException(status_code=404, detail=f"条目 {index} 不存在")

    entries.pop(index)
    _write_dream_lorebook(world, entries)
    return {"ok": True, "remaining": len(entries)}


@router.get("/dream/worlds/{world}/preset", summary="读取梦境世界预设文本")
async def get_dream_preset(world: str, _auth=Depends(require_scopes("activity"))):
    p = _preset_path(world)
    if not p.exists():
        return {"world": world, "content": ""}
    return {"world": world, "content": p.read_text(encoding="utf-8")}


@router.put("/dream/worlds/{world}/preset", summary="保存梦境世界预设文本")
async def put_dream_preset(world: str, body: dict, _auth=Depends(require_scopes("activity"))):
    content = body.get("content", "")
    p = _preset_path(world)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(str(content), encoding="utf-8")
    return {"ok": True, "world": world, "bytes": len(content.encode("utf-8"))}


# ── scenario 剧本 CRUD ────────────────────────────────────────────────────────
# 存储沿用 data/dream/scenarios/{id}.yaml；路径走 get_paths().dream_scenarios_dir()
# （不硬拼），schema 校验复用 core.dream.scenario_loader._validate_script —— 与
# dream_turn 实际加载剧本时用的是同一份 schema，不是另起一套校验规则。
_SAFE_SCRIPT_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def _scenario_path(script_id: str) -> Path:
    from core.sandbox import get_paths
    if not _SAFE_SCRIPT_ID_RE.match(script_id):
        raise HTTPException(status_code=422, detail=f"剧本 id 不合法: {script_id!r}")
    return get_paths().dream_scenarios_dir() / f"{script_id}.yaml"


def _scenario_active(script_id: str) -> bool:
    """当前是否有「进行中」的梦冻结着这个剧本（DREAM_ACTIVE / CLOSING / EXIT_REQUESTED）。"""
    from core.dream.dream_state import read_state, DreamStatus
    uid = _owner_uid()
    state = read_state(uid)
    active_statuses = {
        DreamStatus.DREAM_ACTIVE.value,
        DreamStatus.DREAM_CLOSING.value,
        DreamStatus.DREAM_EXIT_REQUESTED.value,
    }
    if state.get("status") not in active_statuses:
        return False
    scenario_core = state.get("scenario_core") or {}
    return scenario_core.get("script_id") == script_id


def _parse_and_validate_scenario_yaml(script_id: str, yaml_text: str) -> dict:
    """解析 + 用剧本加载器的真实 schema 校验，失败返回具体字段错误而不是 500。"""
    import yaml as _yaml
    from core.dream.scenario_loader import _validate_script

    try:
        data = _yaml.safe_load(yaml_text)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"YAML 解析失败: {e}")
    if not isinstance(data, dict):
        raise HTTPException(status_code=422, detail="剧本必须是 YAML 映射（mapping），不能是列表或标量")
    if data.get("id") and data["id"] != script_id:
        raise HTTPException(
            status_code=422,
            detail=f"YAML 内 id={data['id']!r} 与剧本 id={script_id!r} 不一致",
        )
    data.setdefault("id", script_id)
    try:
        _validate_script(data)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=f"剧本 schema 校验失败: {e}")
    return data


@router.get("/dream/scenarios", summary="列出剧本")
async def list_dream_scenarios(_auth=Depends(require_scopes("activity"))):
    from core.sandbox import get_paths
    import yaml as _yaml

    d = get_paths().dream_scenarios_dir()
    if not d.exists():
        return {"scenarios": []}

    items = []
    for p in sorted(d.glob("*.yaml")):
        script_id = p.stem
        title = script_id
        try:
            data = _yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            if isinstance(data, dict) and data.get("title"):
                title = data["title"]
        except Exception:
            pass
        items.append({"id": script_id, "title": title})
    return {"scenarios": items}


@router.get("/dream/scenarios/{script_id}", summary="读取剧本 YAML 原文")
async def get_dream_scenario(script_id: str, _auth=Depends(require_scopes("activity"))):
    p = _scenario_path(script_id)
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"剧本 {script_id} 不存在")
    return {"id": script_id, "yaml": p.read_text(encoding="utf-8")}


@router.post("/dream/scenarios", summary="新建剧本")
async def create_dream_scenario(body: dict, _auth=Depends(require_scopes("activity"))):
    script_id = (body.get("id") or "").strip()
    yaml_text = body.get("yaml") or ""
    if not script_id:
        raise HTTPException(status_code=422, detail="id 不能为空")
    p = _scenario_path(script_id)
    if p.exists():
        raise HTTPException(status_code=409, detail=f"剧本 {script_id} 已存在")

    _parse_and_validate_scenario_yaml(script_id, yaml_text)

    from core.safe_write import safe_write_text
    p.parent.mkdir(parents=True, exist_ok=True)
    safe_write_text(p, yaml_text)
    return {"ok": True, "id": script_id}


@router.put("/dream/scenarios/{script_id}", summary="修改剧本")
async def update_dream_scenario(script_id: str, body: dict, _auth=Depends(require_scopes("activity"))):
    p = _scenario_path(script_id)
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"剧本 {script_id} 不存在")
    if _scenario_active(script_id):
        raise HTTPException(status_code=409, detail="剧本正在被进行中的梦引用，梦醒后再编辑")

    yaml_text = body.get("yaml") or ""
    _parse_and_validate_scenario_yaml(script_id, yaml_text)

    from core.safe_write import safe_write_text
    safe_write_text(p, yaml_text)
    return {"ok": True, "id": script_id}


@router.delete("/dream/scenarios/{script_id}", summary="删除剧本")
async def delete_dream_scenario(script_id: str, _auth=Depends(require_scopes("activity"))):
    p = _scenario_path(script_id)
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"剧本 {script_id} 不存在")
    if _scenario_active(script_id):
        raise HTTPException(status_code=409, detail="剧本正在被进行中的梦引用，梦醒后再删除")

    p.unlink()
    return {"ok": True, "deleted": script_id}
