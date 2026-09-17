"""
Dream session endpoints.

POST  /dream/enter    — enter dream (build frozen snapshot, DREAM_ACTIVE)
POST  /dream/chat     — dream turn (goes to dream_pipeline, never reality pipeline)
POST  /dream/exit     — hard exit (force_exit_dream, unconditional)
GET   /dream/state    — read-only UI panel state (projected fields only)
GET   /dream/settings — read full per-character dream settings
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
import json
import re
import time
from pathlib import Path

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from admin.auth import require_scopes
from core.config_loader import get_config
from core.data_paths import DEFAULT_CHAR_ID
from core.sandbox import get_paths

router = APIRouter()
logger = logging.getLogger(__name__)

# ── Enum validators for PATCH /dream/settings ─────────────────────────────────
_VALID_MEMORY_ACCESS = frozenset({"card_only", "relationship_summary", "full_snapshot"})
_VALID_BOUNDARY_LEVEL = frozenset({"vague", "body_perceptible", "numbers_visible", "threshold_break"})
# 内建六个世界包，独立于磁盘是否真的存在这些文件夹（world_loader 对缺失世界
# fail-open 回退到 _default 内容，不崩），保证 CI/fresh 环境里这六个值恒合法。
_VALID_WORLD_LAYER_BUILTIN = frozenset({"reality_derived", "abo", "vampire", "cat", "flower_bud", "custom"})
_VALID_LUCID_MODE = frozenset({"lucid_shared", "non_lucid"})
_VALID_DREAM_MODE = frozenset({"sandbox", "scenario", "mirror", "rpg"})
_VALID_SCENARIO_INJECTION_MODE = frozenset({"strict_stage", "full_script"})

_ENUM_VALIDATORS: dict[str, frozenset] = {
    "memory_access": _VALID_MEMORY_ACCESS,
    "boundary_level": _VALID_BOUNDARY_LEVEL,
    "lucid_mode": _VALID_LUCID_MODE,
    "scenario_injection_mode": _VALID_SCENARIO_INJECTION_MODE,
}


def _valid_world_layer_values() -> frozenset[str]:
    """内建六个世界 ∪ 磁盘上实际发现的世界（含面板新建的自定义世界）∪ _default。

    _default 是保留兜底世界（Brief 96 §1）：删除当前世界时设置回退到它。

    直接扫描 get_paths().dream_worlds_dir()（与本文件世界管理端点用的是同一个
    accessor），不经 core.dream.world_loader.discover_worlds() —— 后者用的是
    模块级裸 Path("characters/dream_worlds")，不随 sandbox 测试夹具重定向，
    生产模式下两者指向同一目录、行为等价，但测试夹具下会读到不一致的目录。
    """
    from core.dream.world_loader import discover_worlds
    discovered = frozenset(discover_worlds())
    return _VALID_WORLD_LAYER_BUILTIN | discovered | {"_default"}

_PATCH_ALLOWED = frozenset({
    "memory_access", "boundary_level", "world_layer", "lucid_mode",
    "enable_dream_lorebook", "jailbreak_presets", "display",
    "scenario_injection_mode",
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
_SAFE_DREAM_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,160}$")


def _owner_uid() -> str:
    uid = str(get_config().get("scheduler", {}).get("owner_id", "owner"))
    if not uid:
        raise HTTPException(status_code=503, detail="owner_id 未配置")
    return uid


def _active_dream_char_id() -> str:
    from core.pipeline_registry import get as _get_pipeline

    pl = _get_pipeline()
    return str((getattr(pl, "_active_character_id", None) if pl else None) or DEFAULT_CHAR_ID)


def _validated_archive_char_id(char_id: str | None) -> str:
    value = str(char_id or _active_dream_char_id()).strip()
    if not _SAFE_DREAM_ID_RE.fullmatch(value):
        raise HTTPException(status_code=422, detail="char_id 不合法")
    return value


def _validated_dream_id(dream_id: str) -> str:
    value = str(dream_id or "").strip()
    if not _SAFE_DREAM_ID_RE.fullmatch(value):
        raise HTTPException(status_code=422, detail="dream_id 不合法")
    return value


def _requested_dream_id(body: dict | None) -> str | None:
    """Read the optional session identity used by WAKE/resume transitions."""
    if body in (None, {}):
        return None
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="dream request body invalid")
    raw = body.get("dream_id")
    if raw is None:
        return None
    return _validated_dream_id(str(raw))


def _set_wake_observation(
    state: dict,
    *,
    dream_id: str,
    result: str,
    confirmation: str,
    retention_offered: bool,
) -> dict:
    """Keep bounded, text-free WAKE choice metadata in the durable state."""
    state["last_wake_observation"] = {
        "dream_id": dream_id,
        "result": result,
        "confirmation": confirmation,
        "retention_offered": bool(retention_offered),
        "ts": time.time(),
    }
    return state


def _read_archive_file(path: Path) -> tuple[list[dict], bool]:
    turns: list[dict] = []
    parse_error = False
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except Exception:
                parse_error = True
                continue
            if isinstance(value, dict):
                turns.append(value)
            else:
                parse_error = True
    except Exception:
        return [], True
    return turns, parse_error


def _safe_summary(summary_path: Path) -> dict:
    if not summary_path.is_file():
        return {}
    try:
        value = json.loads(summary_path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _archive_metadata(dream_id: str, char_id: str, path: Path) -> dict:
    turns, parse_error = _read_archive_file(path)
    valid_turns = [
        item for item in turns
        if item.get("role") in {"user", "assistant"} and str(item.get("content") or "").strip()
    ]
    assistant_turns = sum(1 for item in valid_turns if item.get("role") == "assistant")
    user_turns = sum(1 for item in valid_turns if item.get("role") == "user")
    timestamps = []
    for item in valid_turns:
        try:
            value = float(item.get("ts"))
            if value > 0:
                timestamps.append(value)
        except (TypeError, ValueError):
            pass
    summary = _safe_summary(get_paths().dreams_summaries_dir(char_id=char_id) / f"dream_{dream_id}.summary.json")
    try:
        fallback_mtime = path.stat().st_mtime
    except OSError:
        fallback_mtime = None
    return {
        "dream_id": dream_id,
        "char_id": char_id,
        "started_at": min(timestamps) if timestamps else fallback_mtime,
        "ended_at": max(timestamps) if timestamps else fallback_mtime,
        "valid_turns": len(valid_turns),
        "valid_user_turns": user_turns,
        "valid_assistant_turns": assistant_turns,
        "dream_mode": str(summary.get("dream_mode") or "unknown"),
        "world_name": str(summary.get("world_id") or "unknown")[:80],
        "exit_mechanism": str(summary.get("exit_mechanism") or "unknown"),
        "exit_initiator": str(summary.get("exit_initiator") or "unknown"),
        "completion": str(summary.get("completion") or "unknown"),
        "exit_reason": str(summary.get("exit_reason") or "unknown"),
        "summary_present": bool(summary),
        "summary_created_at": summary.get("created_at"),
        "summary_title": str(summary.get("title") or "")[:120],
        "summary_preview": str(summary.get("summary") or "")[:240],
        "archive_parse_error": parse_error,
    }


def _archive_path(dream_id: str, char_id: str) -> Path:
    return get_paths().dreams_archive_dir(char_id=char_id) / f"dream_{dream_id}.jsonl"


def _project_archive_message(role: str, content: str, ts: float | None) -> dict:
    """Project one stored turn into the safe, read-only replay shape.

    Archive files deliberately remain write-once and only contain stripped
    ``role/content/ts`` fields.  Narrative segments are a display projection
    derived at read time with the same parser used by live Dream replies.
    """
    message = {"role": role, "content": content, "ts": ts}
    if role != "assistant":
        return message

    try:
        from core.narrative_parser import parse_narrative_segments

        parsed = parse_narrative_segments(content)
        segments = parsed.get("segments")
        segmented_content = parsed.get("content")
        if not isinstance(segments, list) or not isinstance(segmented_content, str):
            raise ValueError("invalid narrative projection")
        message["segments"] = segments
        message["segmented_content"] = segmented_content
    except Exception:
        # A legacy/malformed line must remain replayable.  The marker is a
        # fixed, non-sensitive UI signal; no parser exception is exposed.
        message["segmented_content"] = content
        message["segment_parse_fallback"] = True
    return message


@router.get("/dream/archive", summary="分页读取单人梦境 archive 元数据（只读）")
async def dream_archive_list(
    offset: int = Query(default=0, ge=0, le=10000),
    limit: int = Query(default=20, ge=1, le=100),
    char_id: str | None = Query(default=None),
    _auth=Depends(require_scopes("activity")),
):
    """List archived solo Dream sessions without reading current_dream/tmp."""
    selected_char = _validated_archive_char_id(char_id)
    archive_dir = get_paths().dreams_archive_dir(char_id=selected_char)
    files = []
    if archive_dir.is_dir():
        for path in archive_dir.glob("dream_*.jsonl"):
            stem = path.name[len("dream_"):-len(".jsonl")]
            if _SAFE_DREAM_ID_RE.fullmatch(stem):
                files.append((path, stem))
    items = [_archive_metadata(dream_id, selected_char, path) for path, dream_id in files]
    # RPG archives are player-only and physically isolated from solo Dream archives.
    try:
        from core.dream.rpg_archive import read_archive
        rpg_root = get_paths().dream_rpg_archive_owner_dir(_owner_uid(), char_id=selected_char)
        if rpg_root.is_dir():
            for metadata_path in rpg_root.glob("*/metadata.json"):
                rpg_id = metadata_path.parent.name
                if not _SAFE_DREAM_ID_RE.fullmatch(rpg_id):
                    continue
                rows, metadata, partial = read_archive(_owner_uid(), rpg_id, char_id=selected_char)
                items.append({"dream_id": rpg_id, "char_id": selected_char, "started_at": None, "ended_at": metadata.get("archived_at"), "valid_turns": len(rows), "valid_user_turns": sum(1 for row in rows if row.get("kind") == "user_action"), "valid_assistant_turns": sum(1 for row in rows if row.get("kind") in {"character_reply", "character_followup"}), "dream_mode": "rpg", "world_name": "rpg", "exit_mechanism": "rpg_hard_exit", "exit_initiator": "user", "completion": "archived", "exit_reason": "rpg_hard_exit", "summary_present": False, "summary_created_at": None, "summary_title": "", "summary_preview": "", "archive_parse_error": partial})
    except Exception:
        logger.debug("[dream_archive] RPG archive scan failed", exc_info=True)
    items.sort(key=lambda item: float(item.get("ended_at") or 0), reverse=True)
    page = items[offset:offset + limit]
    return {
        "char_id": selected_char,
        "items": page,
        "offset": offset,
        "limit": limit,
        "total": len(items),
        "has_more": offset + limit < len(items),
    }


@router.get("/dream/archive/{dream_id}", summary="读取单场梦境逐回合 archive（只读回放）")
async def dream_archive_detail(
    dream_id: str,
    char_id: str | None = Query(default=None),
    _auth=Depends(require_scopes("activity")),
):
    selected_id = _validated_dream_id(dream_id)
    selected_char = _validated_archive_char_id(char_id)
    path = _archive_path(selected_id, selected_char)
    if not path.is_file():
        try:
            from core.dream.rpg_archive import read_archive
            rows, metadata, partial = read_archive(_owner_uid(), selected_id, char_id=selected_char)
            if rows or metadata:
                return {"dream_id": selected_id, "char_id": selected_char, "metadata": {"dream_id": selected_id, "char_id": selected_char, "dream_mode": "rpg", "completion": "archived", "archive_parse_error": partial, **metadata}, "messages": rows, "partial_read": partial}
        except Exception:
            logger.debug("[dream_archive] RPG archive read failed", exc_info=True)
        raise HTTPException(status_code=404, detail="dream archive 不存在")
    turns, parse_error = _read_archive_file(path)
    if parse_error and not turns:
        raise HTTPException(status_code=422, detail="dream archive 无法读取")
    messages = []
    for item in turns:
        role = item.get("role")
        content = str(item.get("content") or "")
        if role not in {"user", "assistant"} or not content:
            continue
        ts = item.get("ts")
        try:
            ts = float(ts)
        except (TypeError, ValueError):
            ts = None
        messages.append(_project_archive_message(role, content, ts))
    return {
        "dream_id": selected_id,
        "char_id": selected_char,
        "metadata": _archive_metadata(selected_id, selected_char, path),
        "messages": messages,
        "partial_read": parse_error,
    }


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

    from core.dream.dream_state import DreamStatus, read_state_checked
    current_state, state_ok = read_state_checked(uid)
    if (
        state_ok
        and current_state.get("status") == DreamStatus.DREAM_ACTIVE.value
        and current_state.get("dream_mode") == "rpg"
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "RPG_ENDPOINT_REQUIRED",
                "message": "RPG Dream input must use the RPG round endpoint.",
                "retryable": False,
            },
        )

    from core.conversation_gate import conversation_lock
    from core.dream.dream_pipeline import dream_turn

    async with conversation_lock(uid):
        result = await dream_turn(uid, message)

    scenario_reconcile_request = result.pop("scenario_reconcile_request", None)

    if err := result.get("error"):
        raise HTTPException(status_code=409, detail=err)

    # Brief 84: pseudo-stream typewriter replay for the dream reply. Generation
    # already finished above (dream_turn is fully isolated, zero WS side effects
    # by construction); the animation itself doesn't need conversation_lock.
    # fail-open: pseudo_stream_push never raises, msg_id lets the client dedup
    # against this HTTP response the same way owner chat's stream path does.
    reply = result.get("reply") or ""
    _char_id = ""
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

    if scenario_reconcile_request:
        try:
            from core.dream.scenario_reconciler import schedule as _schedule_scenario_reconcile

            _schedule_scenario_reconcile(scenario_reconcile_request)
        except Exception:
            logger.debug("[dream_chat] scenario reconciler schedule failed", exc_info=True)

    # Brief 170: enqueue Reality continuation only after the final Dream reply
    # has been handed to the visible channel.  The worker itself is durable,
    # gate-aware, and never runs Dream content through the Reality prompt.
    if reply and result.get("continuation_eligible") and result.get("dream_id"):
        try:
            from core.dream.reality_continuation import enqueue as _enqueue_continuation

            _enqueue_continuation(
                uid,
                str(result["dream_id"]),
                char_id=_char_id,
            )
        except Exception:
            logger.warning("[dream_chat] Reality continuation enqueue failed", exc_info=True)

    return result


@router.post("/dream/exit", summary="强退梦境（硬出口，不可被拒）")
async def dream_exit(_auth=Depends(require_scopes("activity"))):
    """
    Hard exit — unconditional, immediate, penetrates all state.
    Cannot be disabled by config or role behavior (invariant D).
    """
    uid = _owner_uid()

    from core.dream.dream_pipeline import force_exit_dream
    return await force_exit_dream(uid)


@router.post("/dream/wake", summary="软挽留闸门（满足门控时角色挽留一次；否则直接硬退）")
async def dream_wake(body: dict = Body(default={}), _auth=Depends(require_scopes("activity"))):
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

    from core.dream.dream_state import DreamStatus, read_state_checked, write_state
    from core.dream.dream_pipeline import (
        _should_retain, _generate_retention_line, force_exit_dream,
    )

    state, state_ok = read_state_checked(uid)
    if not state_ok:
        raise HTTPException(status_code=503, detail="dream_state_unavailable")
    status = state.get("status")
    if state.get("dream_mode") == "rpg":
        close_result = await force_exit_dream(
            uid,
            exit_mechanism="user_hard_exit",
            exit_initiator="user",
            exit_reason="rpg_wake_hard_exit",
        )
        return {"retained": False, **close_result}
    dream_id = str(state.get("dream_id") or "").strip()
    requested_dream_id = _requested_dream_id(body)

    # A supplied identity is never allowed to act on another live session.
    if requested_dream_id and requested_dream_id != dream_id:
        # A closed session may be queried idempotently with its last id.  A
        # live session (including a later new dream) must never accept an old
        # confirmation token.
        closed_last_id = str(state.get("last_dream_id") or "").strip()
        if status not in {
            DreamStatus.REALITY_AFTERGLOW.value,
            DreamStatus.REALITY_CHAT.value,
            DreamStatus.DREAM_ENTRANCE_AVAILABLE.value,
        } or requested_dream_id != closed_last_id:
            raise HTTPException(status_code=409, detail="dream_session_mismatch")

    # A retention response is a live confirmation state, not a closed dream.
    # The second WAKE (or the leave choice) must carry the same session id and
    # use the single close chokepoint with an observable user-confirmed reason.
    if status == DreamStatus.DREAM_EXIT_REQUESTED.value:
        if not dream_id or requested_dream_id != dream_id:
            raise HTTPException(status_code=409, detail="dream_session_mismatch")
        _set_wake_observation(
            state,
            dream_id=dream_id,
            result="confirmed_wake",
            confirmation="confirmed_wake",
            retention_offered=True,
        )
        write_state(uid, state)
        close_result = await force_exit_dream(
            uid,
            exit_mechanism="user_hard_exit",
            exit_initiator="user",
            exit_reason="user_wake_confirmed_after_retention",
        )
        return {"retained": False, **close_result}

    # REALITY/entrance/locked states are already outside the retention gate.
    # DREAM_CLOSING is retried only through force_exit_dream so an archive
    # failure can recover, while completed states return their first metadata.
    if status != DreamStatus.DREAM_ACTIVE.value:
        close_result = await force_exit_dream(uid)
        return {"retained": False, **close_result}

    # Already offered retention this dream → hard exit (no repeated nagging)
    if state.get("retention_offered_dream_id") == dream_id:
        _set_wake_observation(
            state,
            dream_id=dream_id,
            result="closed",
            confirmation="no_retention",
            retention_offered=False,
        )
        write_state(uid, state)
        close_result = await force_exit_dream(
            uid,
            exit_mechanism="user_hard_exit",
            exit_initiator="user",
            exit_reason="user_wake_no_retention",
        )
        return {"retained": False, **close_result}

    # Gate check: immersion + emotional threshold
    if not _should_retain(state):
        _set_wake_observation(
            state,
            dream_id=dream_id,
            result="closed",
            confirmation="no_retention",
            retention_offered=False,
        )
        write_state(uid, state)
        close_result = await force_exit_dream(
            uid,
            exit_mechanism="user_hard_exit",
            exit_initiator="user",
            exit_reason="user_wake_no_retention",
        )
        return {"retained": False, **close_result}

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
        _set_wake_observation(
            state,
            dream_id=dream_id,
            result="closed",
            confirmation="no_retention",
            retention_offered=False,
        )
        write_state(uid, state)
        close_result = await force_exit_dream(
            uid,
            exit_mechanism="user_hard_exit",
            exit_initiator="user",
            exit_reason="user_wake_no_retention",
        )
        return {"retained": False, **close_result}

    state = append_status_shift(state, "retained")
    _set_wake_observation(
        state,
        dream_id=dream_id,
        result="retained",
        confirmation="pending",
        retention_offered=True,
    )
    write_state(uid, state)
    return {"retained": True, "retention_text": retention_text, "dream_id": dream_id}


@router.post("/dream/resume", summary="挽留后留下（status → DREAM_ACTIVE）")
async def dream_resume(body: dict = Body(default={}), _auth=Depends(require_scopes("activity"))):
    """
    Resume after a soft retention: set status back to DREAM_ACTIVE so
    dream_turn() can continue processing messages.

    Only acts when status == DREAM_EXIT_REQUESTED; any other status is a no-op
    (idempotent, safe to call spuriously).
    """
    uid = _owner_uid()

    from core.dream.dream_state import DreamStatus, read_state_checked, write_state

    state, state_ok = read_state_checked(uid)
    if not state_ok:
        raise HTTPException(status_code=503, detail="dream_state_unavailable")
    requested_dream_id = _requested_dream_id(body)
    current_dream_id = str(state.get("dream_id") or "").strip()
    if requested_dream_id and requested_dream_id != current_dream_id:
        raise HTTPException(status_code=409, detail="dream_session_mismatch")
    if state.get("status") == DreamStatus.DREAM_EXIT_REQUESTED.value:
        if not current_dream_id or requested_dream_id != current_dream_id:
            raise HTTPException(status_code=409, detail="dream_session_mismatch")
        from core.dream.dream_flow import append_status_shift

        state = append_status_shift(state, "resumed")
        state["status"] = DreamStatus.DREAM_ACTIVE.value
        observation = state.get("last_wake_observation")
        if isinstance(observation, dict) and observation.get("dream_id") == current_dream_id:
            state["last_wake_observation"] = {
                **observation,
                "result": "retained_and_resumed",
                "confirmation": "stayed",
                "resumed_at": time.time(),
            }
        write_state(uid, state)
        logger.info("[dream_resume] resumed uid=%s dream_id=%s", uid, current_dream_id)
        return {"ok": True, "resumed": True, "dream_id": current_dream_id}

    return {
        "ok": False,
        "resumed": False,
        "dream_id": str(state.get("last_dream_id") or "") or None,
        "error": "dream_not_waiting_for_confirmation",
    }


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
    settings = _load_settings(uid, char_id=_active_dream_char_id())

    dream_mode = state.get("dream_mode", "sandbox")
    scenario_info: dict | None = None
    scenario_injection_mode = "strict_stage"
    if dream_mode == "scenario" and state.get("scenario_core"):
        scenario_injection_mode = state.get("scenario_injection_mode", "strict_stage")
        if scenario_injection_mode not in _VALID_SCENARIO_INJECTION_MODE:
            scenario_injection_mode = "strict_stage"
        _sc = state["scenario_core"]
        from core.dream.scenario_projection import scenario_projection_metadata

        _scenario_projection = scenario_projection_metadata(
            _sc, injection_mode=scenario_injection_mode
        )
        scenario_info = {
            "script_id": _sc.get("script_id"),
            "current_stage_id": _sc.get("current_stage_id"),
            "stage_turns": int(_sc.get("stage_turns") or 0),
            "last_progress_signal": _sc.get("last_progress_signal"),
            "last_control_status": _sc.get("last_control_status"),
            "last_control_version": _sc.get("last_control_version"),
            "matched_exit_ids": list(_sc.get("last_matched_exit_ids") or []),
            "blocked_ids": list(_sc.get("last_blocked_ids") or []),
            "valid_exit_sign_count": int(_sc.get("last_valid_exit_sign_count") or 0),
            "unknown_exit_sign_count": int(_sc.get("last_unknown_exit_sign_count") or 0),
            "unknown_blocked_event_count": int(_sc.get("last_unknown_blocked_event_count") or 0),
            "advance_disposition": _sc.get("advance_disposition"),
            "advance_blocked_reason": _sc.get("advance_blocked_reason"),
            "advance_blocked_current_bucket": _sc.get("advance_blocked_current_bucket"),
            "advance_blocked_target_bucket": _sc.get("advance_blocked_target_bucket"),
            "stall_turns": int(_sc.get("stall_turns") or 0),
            "recovery_pending": bool(_sc.get("recovery_pending")),
            "blocked_event_count": len(_sc.get("last_blocked_events") or []),
            "scenario_injection_mode": scenario_injection_mode,
            "projection": _scenario_projection,
        }

    base = {
        "status": state.get("status", "REALITY_CHAT"),
        "dream_id": state.get("dream_id"),
        "dream_mode": dream_mode,
        "scenario": scenario_info,
        "scenario_injection_mode": scenario_injection_mode if dream_mode == "scenario" else None,
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
        "exit_observation": state.get("last_exit_observation"),
        "wake_observation": state.get("last_wake_observation"),
        "last_exit_mechanism": state.get("last_exit_mechanism"),
        "last_exit_initiator": state.get("last_exit_initiator"),
        "last_completion": state.get("last_completion"),
        "last_exit_reason": state.get("last_exit_reason"),
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


@router.get("/dream/operations", summary="读取梦境退出与明信片运维状态（只读）")
async def dream_operations_get(_auth=Depends(require_scopes("activity"))):
    """Return bounded lifecycle metadata for the management surface.

    This endpoint deliberately omits archive turns, postcard letter text,
    prompts, and filesystem paths.  Replay is a separate explicitly scoped
    archive endpoint added for the desktop reader.
    """
    uid = _owner_uid()
    from core.dream.dream_state import read_state
    from core.dream.exit_observability import DELIVERY_CONTINUATION, list_records
    from core.dream.scenario_progress_audit import list_records as list_scenario_progress
    from core.sandbox import get_paths as _get_paths

    char_id = _active_dream_char_id()
    state = read_state(uid)
    lifecycle = list_records(char_id=char_id, limit=50)
    continuation_lifecycle = list_records(
        char_id=char_id,
        limit=20,
        delivery_kind=DELIVERY_CONTINUATION,
    )
    scenario_dream_id = str(state.get("dream_id") or state.get("last_dream_id") or "")
    scenario_rows = list_scenario_progress(
        char_id=char_id,
        dream_id=scenario_dream_id or None,
        limit=50,
    )
    scenario_reconciler_rows = [
        row for row in scenario_rows if row.get("record_kind") == "reconciler"
    ]
    scenario_progress_rows = [
        row for row in scenario_rows if row.get("record_kind") != "reconciler"
    ]
    scenario_last = scenario_progress_rows[0] if scenario_progress_rows else None
    scenario_current = state.get("scenario_core") or {}
    scenario_progress = {
        "dream_id": scenario_dream_id or None,
        "current_stage_id": scenario_current.get("current_stage_id") or (
            scenario_last.get("current_stage_id") if scenario_last else None
        ),
        "final_stage_id": scenario_last.get("current_stage_id") if scenario_last else None,
        "last": scenario_last,
        "recent": scenario_progress_rows,
    }
    reconciler_statuses = [row.get("reconciler_status") for row in scenario_reconciler_rows]
    scenario_reconciliation = {
        "dream_id": scenario_dream_id or None,
        "injection_mode": state.get("scenario_injection_mode")
        or state.get("last_scenario_injection_mode")
        or "strict_stage",
        "latest": scenario_reconciler_rows[0] if scenario_reconciler_rows else None,
        "recent": scenario_reconciler_rows,
        "trigger_count": len(scenario_reconciler_rows),
        "applied_count": sum(1 for row in scenario_reconciler_rows if row.get("reconciler_applied")),
        "stale_count": sum(1 for status in reconciler_statuses if status == "stale"),
        "failed_count": sum(1 for status in reconciler_statuses if status == "failed"),
    }
    archive_page = await dream_archive_list(offset=0, limit=20, char_id=char_id)
    last_dream_id = str(state.get("last_dream_id") or "")
    latest_archive = next(
        (item for item in archive_page["items"] if item.get("dream_id") == last_dream_id),
        None,
    )
    close_metadata_consistent = None
    if latest_archive and last_dream_id:
        pairs = (
            ("last_dream_mode", "dream_mode"),
            ("last_exit_mechanism", "exit_mechanism"),
            ("last_exit_initiator", "exit_initiator"),
            ("last_completion", "completion"),
            ("last_exit_reason", "exit_reason"),
        )
        archive_values = [str(latest_archive.get(archive_key) or "") for _, archive_key in pairs]
        if all(value not in {"", "unknown"} for value in archive_values):
            close_metadata_consistent = all(
                str(state.get(state_key) or "") == str(latest_archive.get(archive_key) or "")
                for state_key, archive_key in pairs
            )

    schedule: list[dict] = []
    schedule_path = _get_paths().dreams_postcards_dir(char_id=char_id) / "schedule.json"
    try:
        raw = json.loads(schedule_path.read_text(encoding="utf-8")) if schedule_path.exists() else []
        if isinstance(raw, list):
            for item in raw[-50:]:
                if not isinstance(item, dict):
                    continue
                schedule.append({
                    key: item.get(key)
                    for key in (
                        "dream_id", "scheduled_date", "sent", "attempts", "last_error",
                        "generation_status", "delivery_status", "eligibility_reason",
                        "completion", "exit_mechanism", "exit_initiator",
                    )
                })
    except Exception:
        schedule = []

    afterglow: dict = {"present": False, "created_at": None}
    try:
        from core.memory.user_hidden_state_store import _load_afterglow_raw
        residue = _load_afterglow_raw(uid, char_id=char_id) or {}
        afterglow = {
            "present": bool(residue),
            "created_at": residue.get("created_at"),
        }
    except Exception:
        pass

    summary: dict = {"present": False, "created_at": None, "dream_id": None}
    if _SAFE_DREAM_ID_RE.fullmatch(last_dream_id):
        summary_path = _get_paths().dreams_summaries_dir(char_id=char_id) / f"dream_{last_dream_id}.summary.json"
        try:
            data = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.exists() else {}
            summary = {
                "present": bool(data),
                "created_at": data.get("created_at"),
                "dream_id": last_dream_id,
            }
        except Exception:
            pass

    return {
        "char_id": char_id,
        "current": {
            key: state.get(key)
            for key in (
                "status", "dream_id", "last_dream_id", "last_greeted_dream_id",
                "last_exit_type", "last_exit_mechanism", "last_exit_initiator",
                "last_completion", "last_exit_reason", "last_exit_assistant_turns",
                "last_archive_ok", "last_exited_at", "last_wake_observation",
                "exit_duplicate_count", "last_duplicate_exit_at", "last_close_attempt",
            )
        },
        "consistency": {
            "last_dream_id_present": bool(last_dream_id),
            "last_greeted_matches_last_dream": bool(
                state.get("last_greeted_dream_id") in (None, "", last_dream_id)
            ),
            "close_metadata_consistent": close_metadata_consistent,
        },
        "archives": archive_page["items"],
        "exit_lifecycle": lifecycle,
        "continuation": {
            "last": continuation_lifecycle[0] if continuation_lifecycle else None,
            "recent": continuation_lifecycle,
        },
        "scenario_progress": scenario_progress,
        "scenario_reconciliation": scenario_reconciliation,
        "postcards": schedule,
        "afterglow": afterglow,
        "summary": summary,
    }


@router.get("/dream/presets", summary="列出可用破限预设名（只读，供群聊梦境 per-char 选择器，Brief 100 §3）")
async def list_dream_presets(_auth=Depends(require_scopes("activity"))):
    """列出 characters/dream_presets/ 下经 asset registry 登记的预设（经此层解析
    真实文件名，兼容中文命名的预设）。只读，不返回正文内容。"""
    from core.asset_registry import get_registry

    entries = get_registry().list_all("dream_preset")
    presets = sorted(
        ({"id": e.id, "label": e.label} for e in entries if not e.hidden),
        key=lambda item: item["id"],
    )
    return {"presets": presets}


def _preset_asset_path(preset: str) -> Path:
    """Resolve a standalone dream-preset id to its authored asset path.

    Asset registry ids are the public contract (including mapped legacy file
    names).  New assets use the same safe ASCII id convention as
    ``jailbreak_presets`` in dream settings.
    """
    if not isinstance(preset, str) or not _SAFE_PRESET_RE.match(preset):
        raise HTTPException(status_code=422, detail=f"预设 id 不合法: {preset!r}")

    from core.asset_registry import get_registry
    from core.sandbox import get_paths

    try:
        return get_registry().resolve(preset, "dream_preset").path()
    except ValueError:
        from core.authored_asset_resolver import resolve_layered_file
        user_dir, legacy_dir = get_paths().dream_preset_read_dirs()
        item = resolve_layered_file(
            user_dir, legacy_dir, f"{preset}.md", logical_asset="dream_preset"
        )
        return item.path if item is not None else get_paths().dream_preset_write_path(preset)


def _preset_write_path(preset: str) -> Path:
    if not isinstance(preset, str) or not _SAFE_PRESET_RE.match(preset):
        raise HTTPException(status_code=422, detail=f"预设 id 不合法: {preset!r}")
    return get_paths().dream_preset_write_path(preset)


def _authored_source(read_path: Path, canonical: Path) -> str:
    return "user" if read_path == canonical else "legacy"


def _log_dream_write(
    *, kind: str, read_path: Path, canonical: Path, source: str | None = None
) -> None:
    logger.info(
        "[authored-writer] kind=%s effective_read_source=%s canonical_write_target=user",
        kind,
        source or _authored_source(read_path, canonical),
    )


def _reload_dream_preset_registry() -> None:
    """Make authoring changes visible to the next dream without a restart."""
    from core.asset_registry import reload_registry
    reload_registry()


@router.get("/dream/presets/{preset}", summary="读取独立梦境破限预设")
async def get_standalone_dream_preset(
    preset: str,
    _auth=Depends(require_scopes("activity")),
):
    path = _preset_asset_path(preset)
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"预设 {preset} 不存在")
    return {"id": preset, "content": path.read_text(encoding="utf-8")}


@router.post("/dream/presets", summary="新建独立梦境破限预设")
async def create_standalone_dream_preset(
    body: dict,
    _auth=Depends(require_scopes("activity")),
):
    preset = body.get("id")
    content = body.get("content", "")
    if not isinstance(content, str):
        raise HTTPException(status_code=422, detail="content 必须为字符串")
    read_path = _preset_asset_path(preset)
    path = _preset_write_path(preset)
    if read_path.exists():
        raise HTTPException(status_code=409, detail=f"预设 {preset} 已存在")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    _log_dream_write(kind="dream_preset", read_path=path, canonical=path, source="user")
    _reload_dream_preset_registry()
    return {"ok": True, "id": preset, "bytes": len(content.encode("utf-8"))}


@router.put("/dream/presets/{preset}", summary="编辑独立梦境破限预设")
async def put_standalone_dream_preset(
    preset: str,
    body: dict,
    _auth=Depends(require_scopes("activity")),
):
    content = body.get("content")
    if not isinstance(content, str):
        raise HTTPException(status_code=422, detail="content 必须为字符串")
    read_path = _preset_asset_path(preset)
    if not read_path.is_file():
        raise HTTPException(status_code=404, detail=f"预设 {preset} 不存在")
    path = _preset_write_path(preset)
    path.parent.mkdir(parents=True, exist_ok=True)
    _log_dream_write(kind="dream_preset", read_path=read_path, canonical=path)
    path.write_text(content, encoding="utf-8")
    _reload_dream_preset_registry()
    return {"ok": True, "id": preset, "bytes": len(content.encode("utf-8"))}


@router.delete("/dream/presets/{preset}", summary="删除独立梦境破限预设")
async def delete_standalone_dream_preset(
    preset: str,
    _auth=Depends(require_scopes("activity")),
):
    path = _preset_asset_path(preset)
    if not path.is_file():
        legacy = get_paths().legacy_dream_presets_dir() / f"{preset}.md"
        if legacy.is_file():
            raise HTTPException(status_code=409, detail="legacy fallback Dream 预设为只读，不能删除")
        raise HTTPException(status_code=404, detail=f"预设 {preset} 不存在")

    from core.dream.dream_settings import load as _load_settings
    if preset in (_load_settings(_owner_uid(), char_id=_active_dream_char_id()).get("jailbreak_presets") or []):
        raise HTTPException(status_code=409, detail="该预设正在下一场梦的选用列表中，请先取消选用")

    canonical = _preset_write_path(preset)
    if path != canonical:
        raise HTTPException(status_code=409, detail="legacy fallback Dream 预设为只读，不能删除")
    path.unlink()
    _reload_dream_preset_registry()
    legacy_reappeared = (get_paths().legacy_dream_presets_dir() / path.name).is_file()
    return {"ok": True, "deleted": preset, "legacy_fallback_reappeared": legacy_reappeared}


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
    return _load(uid, char_id=_active_dream_char_id())


@router.patch("/dream/settings", summary="部分更新梦境设置（校验枚举值；仅影响下一场梦）")
async def dream_settings_patch(body: dict, _auth=Depends(require_scopes("activity"))):
    """
    Partial update for dream settings. Validates enum values before writing.

    Allowed fields: memory_access / boundary_level / world_layer / lucid_mode /
                    enable_dream_lorebook / display / scenario_injection_mode

    ★ NEVER backfills into a running dream's frozen_world / lucid_mode.
      Those fields are frozen at dream entry (enter_dream reads from settings
      and copies into dream_state). PATCH only affects the next dream session.
      Changing world_layer while DREAM_ACTIVE does NOT change the current dream.
    """
    uid = _owner_uid()
    char_id = _active_dream_char_id()
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

    current = _load(uid, char_id=char_id)
    current.update(updates)
    _save(uid, current, char_id=char_id)
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
    """Resolve a Dream world package for reading (legacy fallback preserved)."""
    if not _SAFE_WORLD_RE.match(world) or world in (".", ".."):
        raise HTTPException(status_code=422, detail=f"世界名称不合法: {world!r}")
    from core.dream.world_loader import resolve_dream_world
    item = resolve_dream_world(world)
    return item.path if item is not None else get_paths().dream_worlds_dir() / world


def _world_write_dir(world: str) -> Path:
    _validate_world_name(world, allow_reserved=True)
    return get_paths().dream_world_write_dir(world)


def _materialize_world_for_write(world: str) -> Path:
    """Copy one effective world package into userdata before mutating it."""
    read_dir = _world_dir(world)
    write_dir = _world_write_dir(world)
    if write_dir.exists():
        return write_dir
    if not read_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"世界 {world} 不存在")
    import shutil as _shutil
    _shutil.copytree(read_dir, write_dir)
    _log_dream_write(kind="dream_world", read_path=read_dir, canonical=write_dir)
    return write_dir

def _preset_path(world: str):
    """Resolve the legacy-compatible world-named preset for reading."""
    if not _SAFE_WORLD_RE.match(world) or world in (".", ".."):
        raise HTTPException(status_code=422, detail=f"世界名称不合法: {world!r}")
    from core.authored_asset_resolver import resolve_layered_file
    user_dir, legacy_dir = get_paths().dream_preset_read_dirs()
    item = resolve_layered_file(user_dir, legacy_dir, f"{world}.md", logical_asset="dream_preset")
    return item.path if item is not None else get_paths().dream_preset_write_path(world)


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
    char_id = _active_dream_char_id()
    settings = _load_settings(uid, char_id=char_id)
    if settings.get("world_layer") == match_world:
        settings["world_layer"] = reset_to
        _save_settings(uid, settings, char_id=char_id)


def _ensure_default_world_template_seeded() -> Path:
    """Return the tracked default package as a read-only seed source.

    C1.1 deliberately does not build a ``characters/dream_worlds`` subtree.
    New worlds copy directly into the canonical userdata target.
    """
    from core.sandbox import get_paths
    template = get_paths().default_dream_world_template_dir()
    if not template.is_dir():
        raise HTTPException(status_code=500, detail="默认 Dream 世界模板缺失")
    return template


@router.get("/dream/worlds", summary="列出梦境世界目录")
async def list_dream_worlds(_auth=Depends(require_scopes("activity"))):
    from core.dream.world_loader import discover_worlds
    return {"worlds": discover_worlds()}


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

    dest = get_paths().dream_world_write_dir(world)
    if _world_dir(world).exists():
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

    _log_dream_write(kind="dream_world", read_path=template, canonical=dest, source="default")

    return {"ok": True, "world": world}


@router.put("/dream/worlds/{world}/rename", summary="重命名梦境世界文件夹")
async def rename_dream_world(world: str, body: dict, _auth=Depends(require_scopes("activity"))):
    """重命名世界文件夹，并同步世界选择引用：

    - dream_settings.world_layer 若正指向旧名，改写为新名。
    - 独立破限预设绝不随世界移动；早期同名文件也保留为独立资产。
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
    dst = _world_write_dir(new_name)
    if _world_dir(new_name).exists():
        raise HTTPException(status_code=409, detail=f"世界 {new_name} 已存在")

    if _dream_active_referencing_world(world):
        raise HTTPException(status_code=409, detail="该世界正被进行中的梦境使用，梦醒后再重命名")

    if src != _world_write_dir(world):
        raise HTTPException(status_code=409, detail="legacy fallback Dream 世界为只读，不能重命名")
    dst.parent.mkdir(parents=True, exist_ok=True)
    src.rename(dst)

    _reset_world_layer_setting_if(world, new_name)

    return {"ok": True, "world": new_name}


@router.delete("/dream/worlds/{world}", summary="删除梦境世界文件夹")
async def delete_dream_world(world: str, _auth=Depends(require_scopes("activity"))):
    """删除世界文件夹（二次确认由前端做）。

    - _default / reality_derived 拒删。
    - 正在被进行中的梦引用时拒绝。
    - 独立破限预设不会随世界删除（即使它恰好同名）。
    - 若为当前 dream_settings.world_layer，重置为 _default。
    """
    _validate_world_name(world)  # 已在此拒绝 _default / reality_derived

    target = _world_dir(world)
    if not target.is_dir():
        legacy = get_paths().legacy_dream_worlds_dir() / world
        if legacy.is_dir():
            raise HTTPException(status_code=409, detail="legacy fallback Dream 世界为只读，不能删除")
        raise HTTPException(status_code=404, detail=f"世界 {world} 不存在")

    canonical = _world_write_dir(world)
    if target != canonical:
        raise HTTPException(status_code=409, detail="legacy fallback Dream 世界为只读，不能删除")
    if _dream_active_referencing_world(world):
        raise HTTPException(status_code=409, detail="该世界正被进行中的梦境使用，梦醒后再删除")
    import shutil as _shutil
    _shutil.rmtree(target)

    _reset_world_layer_setting_if(world, "_default")

    legacy_reappeared = (get_paths().legacy_dream_worlds_dir() / world).is_dir()
    return {"ok": True, "deleted": world, "legacy_fallback_reappeared": legacy_reappeared}


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
    p = _materialize_world_for_write(world) / "lorebook.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        _yaml.dump(entries, allow_unicode=True, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )


@router.post("/dream/worlds/{world}/lorebook", summary="新增梦境世界书条目")
async def add_dream_lore_entry(world: str, body: dict, _auth=Depends(require_scopes("activity"))):
    import yaml as _yaml
    p = _materialize_world_for_write(world) / "lorebook.yaml"
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
    p = _materialize_world_for_write(world) / "lorebook.yaml"
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
    p = _materialize_world_for_write(world) / "lorebook.yaml"
    if not p.exists():
        raise HTTPException(status_code=404, detail="lorebook.yaml 不存在")
    entries = _yaml.safe_load(p.read_text(encoding="utf-8")) or []
    if not isinstance(entries, list) or index < 0 or index >= len(entries):
        raise HTTPException(status_code=404, detail=f"条目 {index} 不存在")

    entries.pop(index)
    _write_dream_lorebook(world, entries)
    return {"ok": True, "remaining": len(entries)}


@router.get("/dream/worlds/{world}/preset", summary="读取旧版同名梦境预设（兼容）")
async def get_dream_preset(world: str, _auth=Depends(require_scopes("activity"))):
    p = _preset_path(world)
    if not p.exists():
        return {"world": world, "content": ""}
    return {"world": world, "content": p.read_text(encoding="utf-8")}


@router.put("/dream/worlds/{world}/preset", summary="保存旧版同名梦境预设（兼容）")
async def put_dream_preset(world: str, body: dict, _auth=Depends(require_scopes("activity"))):
    content = body.get("content", "")
    read_path = _preset_path(world)
    p = get_paths().dream_preset_write_path(world)
    p.parent.mkdir(parents=True, exist_ok=True)
    _log_dream_write(kind="dream_preset", read_path=read_path, canonical=p)
    p.write_text(str(content), encoding="utf-8")
    _reload_dream_preset_registry()
    return {"ok": True, "world": world, "bytes": len(content.encode("utf-8"))}


# ── scenario 剧本 CRUD ────────────────────────────────────────────────────────
# 新写入走 userdata/characters/dream/scenarios/{id}.yaml；历史
# data/dream/scenarios 只读回退。schema 校验复用 scenario_loader._validate_script —— 与
# dream_turn 实际加载剧本时用的是同一份 schema，不是另起一套校验规则。
_SAFE_SCRIPT_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def _validate_scenario_id(script_id: str) -> str:
    if not _SAFE_SCRIPT_ID_RE.match(script_id):
        raise HTTPException(status_code=422, detail=f"剧本 id 不合法: {script_id!r}")
    return script_id


def _scenario_write_path(script_id: str) -> Path:
    from core.sandbox import get_paths
    return get_paths().dream_scenario_write_path(_validate_scenario_id(script_id))


def _scenario_read_path(script_id: str) -> tuple[Path, str] | None:
    from core.sandbox import get_paths
    safe_id = _validate_scenario_id(script_id)
    primary, fallback = get_paths().dream_scenario_read_dirs()
    primary_path = primary / f"{safe_id}.yaml"
    if primary_path.exists():
        return primary_path, "user"
    if fallback is not None:
        fallback_path = fallback / f"{safe_id}.yaml"
        if fallback_path.exists():
            return fallback_path, "legacy"
    return None


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


def _parse_and_validate_scenario_yaml(
    script_id: str | None,
    yaml_text: str,
    *,
    require_progressable: bool = False,
) -> dict:
    """解析 + 用剧本加载器的真实 schema 校验，失败返回具体字段错误而不是 500。"""
    import yaml as _yaml
    from core.dream.scenario_loader import _validate_script

    try:
        data = _yaml.safe_load(_normalize_field_outline_scenario_yaml(yaml_text))
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"YAML 解析失败: {e}")
    if not isinstance(data, dict):
        raise HTTPException(status_code=422, detail="剧本必须是 YAML 映射（mapping），不能是列表或标量")
    if script_id is not None and data.get("id") != script_id:
        raise HTTPException(
            status_code=422,
            detail=f"YAML 内 id={data.get('id')!r} 与剧本 id={script_id!r} 不一致",
        )
    try:
        _validate_script(data, require_progressable=require_progressable)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=f"剧本 schema 校验失败: {e}")
    return data


_OUTLINE_STAGE_FIELDS = frozenset({
    "id", "name", "dramatic_task", "entry_pressure", "exit_signs",
    "not_yet_allowed", "drift_pressure",
})
_OUTLINE_BLOCK_FIELDS = frozenset({"dramatic_task", "entry_pressure", "instruction"})
_OUTLINE_LIST_FIELDS = frozenset({"exit_signs", "not_yet_allowed", "allowed_hints"})


def _normalize_field_outline_scenario_yaml(yaml_text: str) -> str:
    """Convert the legacy left-aligned scenario outline to canonical YAML.

    This is deliberately not a permissive YAML parser.  It recognizes only the
    scenario fields rendered by the editor and otherwise returns the original
    text for normal YAML parsing and validation.
    """
    lines = yaml_text.splitlines()
    if not any(line.strip() == "private_truths:" for line in lines) or not any(
        line.strip() == "stages:" for line in lines
    ):
        return yaml_text

    document: dict = {"private_truths": [], "stages": []}
    section = "root"
    current_truth: dict | None = None
    current_stage: dict | None = None
    current_rule: dict | None = None
    collecting: tuple[dict, str, str] | None = None

    def fail() -> str:
        return yaml_text

    def append_collected(line: str) -> bool:
        nonlocal collecting
        if collecting is None:
            return False
        target, key, kind = collecting
        if kind == "list":
            if line.strip():
                target[key].append(line.strip().removeprefix("- ").strip())
        else:
            target[key] = f"{target[key]}\n{line}".strip()
        return True

    for raw_line in lines:
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            if collecting and collecting[2] == "block":
                append_collected("")
            continue
        match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*):(?:\s*(.*))?$", raw_line)
        if not match:
            if not append_collected(raw_line):
                return fail()
            continue

        key, value = match.groups()
        value = (value or "").strip()
        if value in {">", "|"}:
            value = ""
        collecting = None
        if key == "private_truths" and not value and section == "root":
            section = "truths"
            continue
        if key == "stages" and not value and section in {"root", "truths"}:
            section = "stages"
            continue
        if section == "root":
            if key not in {"id", "title"} or not value:
                return fail()
            document[key] = value
            continue
        if section == "truths":
            if key == "id" and value:
                current_truth = {"id": value, "disclosure": {}}
                document["private_truths"].append(current_truth)
                current_rule = None
                continue
            if current_truth is None:
                return fail()
            if key == "truth" and value:
                current_truth[key] = value
                continue
            if key == "disclosure" and not value:
                current_rule = None
                continue
            if key == "allowed_hints" and not value and current_rule is not None:
                current_rule[key] = []
                collecting = (current_rule, key, "list")
                continue
            if key == "policy" and value and current_rule is not None:
                current_rule[key] = value
                continue
            if not value:
                current_rule = {}
                current_truth["disclosure"][key] = current_rule
                continue
            return fail()
        if section == "stages":
            if key == "id" and value:
                current_stage = {"id": value}
                document["stages"].append(current_stage)
                current_rule = None
                continue
            if current_stage is None or key not in (_OUTLINE_STAGE_FIELDS - {"id"}) | {"after_turns", "instruction"}:
                return fail()
            if key in _OUTLINE_LIST_FIELDS and not value:
                current_stage[key] = []
                collecting = (current_stage, key, "list")
                continue
            if key == "drift_pressure" and not value:
                current_rule = {}
                current_stage[key] = current_rule
                continue
            if key in {"after_turns", "instruction"} and current_rule is not None:
                if key == "after_turns":
                    if not value.isdigit():
                        return fail()
                    current_rule[key] = int(value)
                else:
                    current_rule[key] = value
                    collecting = (current_rule, key, "block")
                continue
            if key in _OUTLINE_BLOCK_FIELDS:
                current_stage[key] = value
                collecting = (current_stage, key, "block")
                continue
            if key == "name" and value:
                current_stage[key] = value
                continue
            return fail()

    if not document.get("id") or not document.get("title"):
        return yaml_text
    import yaml as _yaml
    return _yaml.safe_dump(document, allow_unicode=True, default_flow_style=False, sort_keys=False)


def _canonical_scenario_yaml(document: dict) -> str:
    import yaml as _yaml

    return _yaml.safe_dump(
        document,
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
    )


def _scenario_document_and_yaml(script_id: str, body: dict) -> tuple[dict, str]:
    document = body.get("document")
    if document is not None:
        if not isinstance(document, dict):
            raise HTTPException(status_code=422, detail="document 必须是 JSON object")
        data = dict(document)
        if data.get("id") and data["id"] != script_id:
            raise HTTPException(status_code=422, detail="document.id 与剧本 id 不一致")
        data["id"] = script_id
        from core.dream.scenario_loader import _validate_script
        try:
            _validate_script(data, require_progressable=True)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"剧本 schema 校验失败: {exc}")
        return data, _canonical_scenario_yaml(data)

    yaml_text = body.get("yaml") or ""
    data = _parse_and_validate_scenario_yaml(script_id, yaml_text)
    return data, _canonical_scenario_yaml(data)


@router.get("/dream/scenarios", summary="列出剧本")
async def list_dream_scenarios(_auth=Depends(require_scopes("activity"))):
    from core.sandbox import get_paths
    import yaml as _yaml

    primary, fallback = get_paths().dream_scenario_read_dirs()
    merged: dict[str, dict] = {}
    for directory, source in ((primary, "user"), (fallback, "legacy")):
        if directory is None or not directory.exists():
            continue
        for p in sorted(directory.glob("*.yaml")):
            script_id = p.stem
            if script_id in merged:
                continue
            title = script_id
            data = {}
            try:
                data = _yaml.safe_load(p.read_text(encoding="utf-8")) or {}
                if isinstance(data, dict) and data.get("title"):
                    title = data["title"]
            except Exception:
                pass
            from core.dream.scenario_loader import unprogressable_stage_ids
            unprogressable = unprogressable_stage_ids(data) if isinstance(data, dict) else []
            merged[script_id] = {
                "id": script_id,
                "title": title,
                "source": source,
                "progressable": not bool(unprogressable),
                "unprogressable_stage_ids": unprogressable,
            }
    return {"scenarios": [merged[key] for key in sorted(merged)]}


@router.post("/dream/scenarios/validate", summary="校验并规范化剧本（不落盘）")
async def validate_dream_scenario(body: dict, _auth=Depends(require_scopes("activity"))):
    """Validate/serialize an authored draft without creating a real scenario file.

    YAML is parsed here so the browser never needs a second YAML implementation.
    The same endpoint also gives the editor a canonical export for unsaved drafts.
    """
    yaml_text = body.get("yaml")
    document = body.get("document")
    requested_id = body.get("id")

    if yaml_text is not None:
        if not isinstance(yaml_text, str) or not yaml_text.strip():
            raise HTTPException(status_code=422, detail="yaml 不能为空")
        script_id = str(requested_id).strip() if requested_id is not None else None
        if script_id == "":
            script_id = None
        parsed = _parse_and_validate_scenario_yaml(script_id, yaml_text, require_progressable=True)
        canonical = _canonical_scenario_yaml(parsed)
    elif document is not None:
        if not isinstance(document, dict):
            raise HTTPException(status_code=422, detail="document 必须是 JSON object")
        script_id = str(requested_id or document.get("id") or "").strip()
        if not script_id:
            raise HTTPException(status_code=422, detail="id 不能为空")
        parsed, canonical = _scenario_document_and_yaml(script_id, {"document": document})
    else:
        raise HTTPException(status_code=422, detail="需要提供 yaml 或 document")

    return {"ok": True, "id": parsed["id"], "document": parsed, "yaml": canonical}


@router.get("/dream/scenarios/{script_id}", summary="读取剧本 YAML 原文")
async def get_dream_scenario(script_id: str, _auth=Depends(require_scopes("activity"))):
    resolved = _scenario_read_path(script_id)
    if resolved is None:
        raise HTTPException(status_code=404, detail=f"剧本 {script_id} 不存在")
    p, source = resolved
    document = _parse_and_validate_scenario_yaml(script_id, p.read_text(encoding="utf-8"))
    from core.dream.scenario_loader import unprogressable_stage_ids
    unprogressable = unprogressable_stage_ids(document)
    yaml_text = _canonical_scenario_yaml(document)
    return {
        "id": script_id,
        "yaml": yaml_text,
        "document": document,
        "source": source,
        "progressable": not bool(unprogressable),
        "unprogressable_stage_ids": unprogressable,
    }


@router.post("/dream/scenarios", summary="新建剧本")
async def create_dream_scenario(body: dict, _auth=Depends(require_scopes("activity"))):
    script_id = (body.get("id") or "").strip()
    if not script_id:
        raise HTTPException(status_code=422, detail="id 不能为空")
    if _scenario_read_path(script_id) is not None:
        raise HTTPException(status_code=409, detail=f"剧本 {script_id} 已存在")
    _, yaml_text = _scenario_document_and_yaml(script_id, body)
    p = _scenario_write_path(script_id)

    from core.safe_write import safe_write_text
    p.parent.mkdir(parents=True, exist_ok=True)
    _log_dream_write(kind="dream_scenario", read_path=p, canonical=p, source="new")
    safe_write_text(p, yaml_text)
    return {"ok": True, "id": script_id}


@router.put("/dream/scenarios/{script_id}", summary="修改剧本")
async def update_dream_scenario(
    script_id: str, body: dict, _auth=Depends(require_scopes("activity"))
):
    resolved = _scenario_read_path(script_id)
    if resolved is None:
        raise HTTPException(status_code=404, detail=f"剧本 {script_id} 不存在")
    if _scenario_active(script_id):
        raise HTTPException(status_code=409, detail="剧本正在被进行中的梦引用，梦醒后再编辑")

    _, yaml_text = _scenario_document_and_yaml(script_id, body)
    p = _scenario_write_path(script_id)

    from core.safe_write import safe_write_text
    p.parent.mkdir(parents=True, exist_ok=True)
    _log_dream_write(
        kind="dream_scenario",
        read_path=resolved[0],
        canonical=p,
        source=resolved[1],
    )
    safe_write_text(p, yaml_text)
    return {"ok": True, "id": script_id, "source": "user"}


@router.delete("/dream/scenarios/{script_id}", summary="删除剧本")
async def delete_dream_scenario(script_id: str, _auth=Depends(require_scopes("activity"))):
    resolved = _scenario_read_path(script_id)
    if resolved is None:
        raise HTTPException(status_code=404, detail=f"剧本 {script_id} 不存在")
    if _scenario_active(script_id):
        raise HTTPException(status_code=409, detail="剧本正在被进行中的梦引用，梦醒后再删除")

    p, source = resolved
    if source != "user":
        raise HTTPException(status_code=409, detail="旧路径剧本为只读兼容来源；编辑后会写入 userdata 覆盖副本")
    p.unlink()
    return {"ok": True, "deleted": script_id}
