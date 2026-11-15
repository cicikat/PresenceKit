"""Dream common API responsibilities; legacy names re-exported by dream.py."""

import logging
import json
import re
import re as _re
import time
from pathlib import Path

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from admin.auth import require_scopes
from admin.routers._common import requested_char_id, resolve_requested_char_id
from core.config_loader import get_config
from core.sandbox import get_paths

router = APIRouter()
logger = logging.getLogger("admin.routers.dream")

_SAFE_PRESET_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


_SAFE_DREAM_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,160}$")


def _owner_uid() -> str:
    from admin.routers import dream as _runtime
    uid = str(_runtime.get_config().get("scheduler", {}).get("owner_id", "owner"))
    if not uid:
        raise _runtime.HTTPException(status_code=503, detail="owner_id 未配置")
    return uid


def _active_dream_char_id() -> str:
    from admin.routers import dream as _runtime
    from core.pipeline_registry import get as _get_pipeline

    pl = _get_pipeline()
    cid = str((getattr(pl, "_active_character_id", None) if pl else None) or "").strip()
    if cid:
        return _runtime.resolve_requested_char_id(cid)
    from admin.routers._common import active_char_id
    return active_char_id()


def _validated_live_char_id(char_id: object | None = None) -> str:
    from admin.routers import dream as _runtime
    value = _runtime.resolve_requested_char_id(char_id, fallback=_runtime._active_dream_char_id)
    if not _runtime._SAFE_DREAM_ID_RE.fullmatch(value):
        raise _runtime.HTTPException(status_code=422, detail="char_id 不合法")
    return value


def _validated_archive_char_id(char_id: object | None = None) -> str:
    """Archive buckets keep historical ids; live pages fail-loud instead."""
    from admin.routers import dream as _runtime
    requested = _runtime.requested_char_id(char_id)
    if requested:
        if not _runtime._SAFE_DREAM_ID_RE.fullmatch(requested):
            raise _runtime.HTTPException(status_code=422, detail="char_id 不合法")
        return requested
    value = _runtime._active_dream_char_id()
    if not _runtime._SAFE_DREAM_ID_RE.fullmatch(value):
        raise _runtime.HTTPException(status_code=422, detail="char_id 不合法")
    return value


def _validated_dream_id(dream_id: str) -> str:
    from admin.routers import dream as _runtime
    value = str(dream_id or "").strip()
    if not _runtime._SAFE_DREAM_ID_RE.fullmatch(value):
        raise _runtime.HTTPException(status_code=422, detail="dream_id 不合法")
    return value


def _authored_source(read_path: Path, canonical: Path) -> str:
    from admin.routers import dream as _runtime
    return "user" if read_path == canonical else "legacy"


def _log_dream_write(
    *, kind: str, read_path: Path, canonical: Path, source: str | None = None
) -> None:
    from admin.routers import dream as _runtime
    _runtime.logger.info(
        "[authored-writer] kind=%s effective_read_source=%s canonical_write_target=user",
        kind,
        source or _runtime._authored_source(read_path, canonical),
    )
