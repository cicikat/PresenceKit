"""
admin/routers 共用 helper。

active_char_id()：从 active_prompt_assets.json 读取当前激活角色 id，校验其在
asset_registry 中确实存在。之前 mood.py / reading.py 各自维护一份等价实现，
CC 任务 24 · 3 抽成此处公共版本，供 mood.py / reading.py / activity.py 复用。

resolve_requested_char_id()：请求显式 char_id 时校验可见角色；缺省仍走 active。
隐藏或未知角色 fail-loud 为 character_unavailable，不回退 live active。
直接调用路由处理函数时，FastAPI Query() 默认对象视为省略，不当成角色 id。
"""
from __future__ import annotations

import json
from collections.abc import Callable

from fastapi import HTTPException

from core.sandbox import get_paths as _get_paths


def active_char_id() -> str:
    try:
        raw = json.loads(_get_paths().active_prompt_assets().read_text(encoding="utf-8"))
        cid = (raw.get("active_character") or "").strip()
    except Exception:
        raise HTTPException(status_code=503, detail="active character unavailable")

    if not cid:
        raise HTTPException(status_code=503, detail="active_character missing")

    from core.asset_registry import get_registry
    try:
        get_registry().resolve(cid, "character")
    except ValueError:
        raise HTTPException(status_code=422, detail=f"unknown character id: {cid!r}")

    return cid


def requested_char_id(char_id: object | None = None) -> str | None:
    """Normalize an optional request character id.

    FastAPI injects Query() objects when tests call handlers without kwargs.
    Those are omit, not an explicit character id.
    """
    if not isinstance(char_id, str):
        return None
    cid = char_id.strip()
    return cid or None


def resolve_requested_char_id(
    char_id: object | None = None,
    *,
    fallback: Callable[[], str] | None = None,
) -> str:
    """Use an explicit request character, or fall back to live active.

    Explicit ids must resolve to a non-hidden character. Missing/unknown/hidden
    request ids are character_unavailable; they never substitute live active.
    """
    cid = requested_char_id(char_id)
    if not cid:
        return fallback() if fallback is not None else active_char_id()

    from core.asset_registry import get_registry
    try:
        entry = get_registry().resolve(cid, "character")
    except ValueError:
        raise HTTPException(status_code=422, detail="character_unavailable") from None
    if getattr(entry, "hidden", False):
        raise HTTPException(status_code=422, detail="character_unavailable")
    return cid
