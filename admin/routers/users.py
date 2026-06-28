"""
用户管理路由
"""

import json as _json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from admin.auth import verify_token

router = APIRouter()


# ── 工具函数 ─────────────────────────────────────────────────────────────────

def _get_known_users() -> list[str]:
    """扫描 history/ / profiles/（legacy）和 memory/{char_id}/（v1）目录，收集所有已知用户 ID。"""
    from core.sandbox import get_paths
    user_ids: set[str] = set()

    # legacy 扫描（含过渡期仍存在旧文件的情况）
    history_dir = get_paths().history()
    if history_dir.exists():
        user_ids.update(f.stem for f in history_dir.glob("*.json"))

    profiles_dir = get_paths().profiles()
    if profiles_dir.exists():
        user_ids.update(f.stem for f in profiles_dir.glob("*.json"))

    # v1 扫描：memory/{char_id}/ 下每个子目录名即为 uid
    char_root = get_paths().memory_char_root()
    if char_root.exists():
        user_ids.update(d.name for d in char_root.iterdir() if d.is_dir())

    return sorted(user_ids)


def _resolve_char_id(char_id: str | None) -> str:
    """Resolve and validate a char_id for profile operations.

    If char_id is None, reads active_character from active_prompt_assets.json.
    Raises HTTP 503 if active_character is missing or empty.
    Raises HTTP 422 if the resolved or supplied char_id is not a known character.
    Never falls back to a hardcoded character.
    """
    from core.sandbox import get_paths
    from core.asset_registry import get_registry

    if char_id is None:
        try:
            data = _json.loads(get_paths().active_prompt_assets().read_text(encoding="utf-8"))
            char_id = (data.get("active_character") or "").strip()
        except Exception as e:
            raise HTTPException(status_code=503, detail=f"读取 active_prompt_assets.json 失败: {e}")
        if not char_id:
            raise HTTPException(
                status_code=503,
                detail="active_prompt_assets.json 中 active_character 为空，请先设置活跃角色",
            )

    try:
        get_registry().resolve(char_id, "character")
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    return char_id


# ── 接口 ─────────────────────────────────────────────────────────────────────

@router.get("/", summary="获取所有用户列表")
async def get_users(auth=Depends(verify_token)):
    """返回所有有对话记录或画像的用户 ID 列表"""
    user_ids = _get_known_users()
    return {"users": user_ids, "total": len(user_ids)}


@router.get("/{user_id}/profile", summary="获取用户画像")
async def get_user_profile(
    user_id: str,
    char_id: str | None = None,
    auth=Depends(verify_token),
):
    """返回指定用户的画像 JSON。

    char_id 为空时使用当前 active_character；非法 char_id 返回错误，不默认 yexuan。
    """
    from core.memory import user_profile
    resolved = _resolve_char_id(char_id)
    profile = user_profile.load(user_id, char_id=resolved)
    return {"user_id": user_id, "char_id": resolved, "profile": profile}


@router.put("/{user_id}/profile", summary="更新用户画像")
async def update_user_profile(
    user_id: str,
    body: dict[str, Any],
    char_id: str | None = None,
    auth=Depends(verify_token),
):
    """直接覆盖更新用户画像字段（admin 直接编辑，不走 LLM 提取）。

    char_id 为空时使用当前 active_character；非法 char_id 返回错误，不默认 yexuan。
    """
    from core.memory import user_profile
    resolved = _resolve_char_id(char_id)
    profile = user_profile.load(user_id, char_id=resolved)
    # 允许直接覆盖所有字段（包括未来新增字段）
    for k, v in body.items():
        profile[k] = v
    user_profile.save(user_id, profile, char_id=resolved)
    return {"message": f"用户 {user_id} 画像已更新", "char_id": resolved, "profile": profile}


@router.get("/{user_id}/pronoun", summary="获取用户称谓")
async def get_user_pronoun(user_id: str, auth=Depends(verify_token)):
    """返回该用户的第三人称称谓（她/他/TA/它）。"""
    from core.memory.user_facts import get_user_pronoun as _get_pronoun
    return {"user_id": user_id, "pronoun": _get_pronoun(user_id)}


class _PronounBody(BaseModel):
    pronoun: str


@router.patch("/{user_id}/pronoun", summary="设置用户称谓")
async def set_user_pronoun(
    user_id: str,
    body: _PronounBody,
    auth=Depends(verify_token),
):
    """更新该用户的第三人称称谓（允许值：她/他/TA/它）。"""
    from core.memory.user_facts import update_user_facts, _VALID_PRONOUNS
    if body.pronoun not in _VALID_PRONOUNS:
        raise HTTPException(status_code=422, detail=f"非法称谓值 {body.pronoun!r}，允许：{sorted(_VALID_PRONOUNS)}")
    updated, rejected = update_user_facts(user_id, {"pronoun": body.pronoun})
    if rejected:
        raise HTTPException(status_code=422, detail=f"字段被拒绝: {rejected}")
    return {"user_id": user_id, "pronoun": updated.get("pronoun", body.pronoun)}


@router.delete("/{user_id}/memory", summary="清除用户所有记忆")
async def delete_user_memory(
    user_id: str,
    char_id: str | None = None,
    auth=Depends(verify_token),
):
    """清除用户的短期历史、画像和长期 RAG 记忆（冻结）。

    char_id 为空时使用当前 active_character；非法 char_id 返回错误，不跨角色清理。
    """
    from core.memory import short_term, user_profile

    resolved = _resolve_char_id(char_id)
    short_term.clear(user_id, char_id=resolved)
    user_profile.clear(user_id, char_id=resolved)

    return {"message": f"用户 {user_id} 角色 {resolved} 的所有记忆已清除", "char_id": resolved}
