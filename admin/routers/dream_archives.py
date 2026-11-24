"""Dream archives API responsibilities; legacy names re-exported by dream.py."""
from admin.character_dependencies import require_character_reader

from admin.routers import dream_common
from core import sandbox

import logging
import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from admin.auth import require_scopes

router = APIRouter()
logger = logging.getLogger("admin.routers.dream")

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
    summary = _safe_summary(sandbox.get_paths().dreams_summaries_dir(char_id=char_id) / f"dream_{dream_id}.summary.json")
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
    return sandbox.get_paths().dreams_archive_dir(char_id=char_id) / f"dream_{dream_id}.jsonl"


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

    *, read_character=Depends(require_character_reader),
):
    """List archived solo Dream sessions without reading current_dream/tmp."""
    selected_char = dream_common._validated_archive_char_id(char_id, read_character=read_character)
    archive_dir = sandbox.get_paths().dreams_archive_dir(char_id=selected_char)
    files = []
    if archive_dir.is_dir():
        for path in archive_dir.glob("dream_*.jsonl"):
            stem = path.name[len("dream_"):-len(".jsonl")]
            if dream_common._SAFE_DREAM_ID_RE.fullmatch(stem):
                files.append((path, stem))
    items = [_archive_metadata(dream_id, selected_char, path) for path, dream_id in files]
    # RPG archives are player-only and physically isolated from solo Dream archives.
    try:
        from core.dream.rpg_archive import read_archive
        rpg_root = sandbox.get_paths().dream_rpg_archive_owner_dir(dream_common._owner_uid(), char_id=selected_char)
        if rpg_root.is_dir():
            for metadata_path in rpg_root.glob("*/metadata.json"):
                rpg_id = metadata_path.parent.name
                if not dream_common._SAFE_DREAM_ID_RE.fullmatch(rpg_id):
                    continue
                rows, metadata, partial = read_archive(dream_common._owner_uid(), rpg_id, char_id=selected_char)
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

    *, read_character=Depends(require_character_reader),
):
    selected_id = dream_common._validated_dream_id(dream_id)
    selected_char = dream_common._validated_archive_char_id(char_id, read_character=read_character)
    path = _archive_path(selected_id, selected_char)
    if not path.is_file():
        try:
            from core.dream.rpg_archive import read_archive
            rows, metadata, partial = read_archive(dream_common._owner_uid(), selected_id, char_id=selected_char)
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
