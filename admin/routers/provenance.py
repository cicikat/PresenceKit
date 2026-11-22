"""
改动溯源接口（G3）

GET /provenance/{uid}
  ?artifact= — 过滤 artifact 类型（identity / episodic / mid_term / …）
  ?field=    — 过滤字段名（identity 维度，如 trust_pattern）
  ?scope=yexuan_self — 视图 B：只返回他自身漂移条目（trait_state / author_note_state）
  ?limit=    — 最多返回条数（默认 100，上限 500）
  ?char_id=  — 角色桶（默认从活跃角色读取）

视图 A：按 artifact/field 查"这条概括什么时候、因为什么变的"
视图 B：scope=yexuan_self，筛他自身（trait/author_note）漂移轨迹
"""

import logging
from fastapi import APIRouter, Depends, HTTPException

from admin.auth import require_scopes
from admin import observation_scope

logger = logging.getLogger(__name__)
router = APIRouter()




@router.get(
    "/provenance/{uid}",
    summary="改动溯源查询（G3）",
    description=(
        "查询指定用户的记忆改动溯源日志。\n\n"
        "**视图 A**（默认）：按 artifact / field 过滤，查询某个概括字段\"何时、因何改动\"。\n\n"
        "**视图 B**（`scope=yexuan_self`）：筛出他自身漂移条目"
        "（`trait_state` / `artifact=author_note_state`），即\"他被用户改变\"的轨迹。\n\n"
        "日志从接入当日起前向积累，不可回溯历史。返回结果为最新优先。"
    ),
    tags=["观测"],
)
async def get_provenance(
    uid: str,
    artifact: str = "",
    field: str = "",
    scope: str = "",
    limit: int = 100,
    char_id: str = "",
    auth=Depends(require_scopes("memory.read")),
):
    if limit < 1 or limit > 500:
        raise HTTPException(status_code=422, detail="limit 须在 1-500 之间")

    resolved_char_id = observation_scope.resolve_observation_character_id(char_id)
    scope_yexuan_self = scope == "yexuan_self"

    from core.memory.provenance_log import query
    records = query(
        uid,
        resolved_char_id,
        artifact=artifact,
        field=field,
        scope_yexuan_self=scope_yexuan_self,
        limit=limit,
    )

    return {
        "uid": uid,
        "char_id": resolved_char_id,
        "filters": {
            "artifact": artifact or None,
            "field": field or None,
            "scope": scope or None,
        },
        "count": len(records),
        "records": records,
    }
