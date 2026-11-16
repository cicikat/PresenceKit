"""Dream common API responsibilities; legacy names re-exported by dream.py."""

from core import config_loader

import logging
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException
from admin.routers._common import requested_char_id, resolve_requested_char_id

router = APIRouter()
logger = logging.getLogger("admin.routers.dream")

_SAFE_PRESET_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


from core.dream.session_control import _SAFE_DREAM_ID_RE


def _owner_uid() -> str:
    uid = str(config_loader.get_config().get("scheduler", {}).get("owner_id", "owner"))
    if not uid:
        raise HTTPException(status_code=503, detail="owner_id 未配置")
    return uid


def _active_dream_char_id() -> str:
    from core.pipeline_registry import get as _get_pipeline

    pl = _get_pipeline()
    cid = str((getattr(pl, "_active_character_id", None) if pl else None) or "").strip()
    if cid:
        return resolve_requested_char_id(cid)
    from admin.routers._common import active_char_id
    return active_char_id()


def _validated_live_char_id(char_id: object | None = None) -> str:
    value = resolve_requested_char_id(char_id, fallback=_active_dream_char_id)
    if not _SAFE_DREAM_ID_RE.fullmatch(value):
        raise HTTPException(status_code=422, detail="char_id 不合法")
    return value


def _validated_archive_char_id(char_id: object | None = None) -> str:
    """Archive buckets keep historical ids; live pages fail-loud instead."""
    requested = requested_char_id(char_id)
    if requested:
        if not _SAFE_DREAM_ID_RE.fullmatch(requested):
            raise HTTPException(status_code=422, detail="char_id 不合法")
        return requested
    value = _active_dream_char_id()
    if not _SAFE_DREAM_ID_RE.fullmatch(value):
        raise HTTPException(status_code=422, detail="char_id 不合法")
    return value


def _validated_dream_id(dream_id: str) -> str:
    from core.dream.session_control import DreamSessionError, validated_dream_id
    try:
        return validated_dream_id(dream_id)
    except DreamSessionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


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
