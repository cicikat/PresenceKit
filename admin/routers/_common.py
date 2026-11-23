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

from collections.abc import Callable

from fastapi import HTTPException

from core import character_context




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


def active_char_id() -> str:
    """Map strict domain character-selection errors at the HTTP boundary."""
    try:
        return character_context.active_character_id()
    except character_context.ActiveCharacterError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None
