"""
admin/routers 共用 helper。

active_char_id()：从 active_prompt_assets.json 读取当前激活角色 id，校验其在
asset_registry 中确实存在。之前 mood.py / reading.py 各自维护一份等价实现，
CC 任务 24 · 3 抽成此处公共版本，供 mood.py / reading.py / activity.py 复用。
"""
import json

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
