"""
关系管理路由
注意：路由顺序很重要：/blacklist 等具体路由必须在 /{user_id} 之前注册，
      否则 FastAPI 会把 "blacklist" 当作 user_id 参数匹配。
路径由 DataPaths.relations() / DataPaths.blacklist() 决定。
"""

from typing import Optional

import yaml
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from admin.auth import require_scopes
from core.sandbox import get_paths

router = APIRouter()


# ── 辅助函数 ─────────────────────────────────────────────────────────────────

def _read_relations() -> dict:
    """读取 relations.yaml，返回 relations 字段内容"""
    rf = get_paths().relations()
    if not rf.exists():
        return {}
    with open(rf, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data.get("relations", {})


def _write_relations(relations: dict):
    """将 relations 字典写回 relations.yaml"""
    rf = get_paths().relations()
    rf.parent.mkdir(parents=True, exist_ok=True)
    with open(rf, "w", encoding="utf-8") as f:
        yaml.dump(
            {"relations": relations},
            f,
            allow_unicode=True,
            default_flow_style=False,
            sort_keys=False,
        )


def _read_blacklist() -> list[str]:
    """读取 blacklist.yaml，返回字符串列表"""
    bf = get_paths().blacklist()
    if not bf.exists():
        return []
    with open(bf, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return [str(uid) for uid in data.get("blacklist", [])]


def _write_blacklist(blacklist: list[str]):
    """将黑名单列表写回 blacklist.yaml"""
    bf = get_paths().blacklist()
    bf.parent.mkdir(parents=True, exist_ok=True)
    with open(bf, "w", encoding="utf-8") as f:
        yaml.dump(
            {"blacklist": blacklist},
            f,
            allow_unicode=True,
            default_flow_style=False,
        )


def _hot_reload():
    """热重载 user_relation 模块和黑名单缓存"""
    from core import user_relation, qq_adapter
    user_relation.reload()
    qq_adapter.reload_blacklist()


# ── 数据模型 ─────────────────────────────────────────────────────────────────

class RelationUpdate(BaseModel):
    role: Optional[str] = None
    nickname: Optional[str] = None
    priority: Optional[int] = None
    extra_prompt: Optional[str] = None
    permissions: Optional[dict] = None


class BlacklistRequest(BaseModel):
    user_id: str


# ── 黑名单接口（必须在 /{user_id} 之前注册）────────────────────────────────

@router.get("/blacklist", summary="获取黑名单列表")
async def get_blacklist(auth=Depends(require_scopes("memory.read"))):
    """返回当前所有被屏蔽的用户 ID"""
    blacklist = _read_blacklist()
    return {"blacklist": blacklist, "total": len(blacklist)}


@router.post("/blacklist", summary="添加用户到黑名单")
async def add_to_blacklist(body: BlacklistRequest, auth=Depends(require_scopes("admin"))):
    """将指定用户加入 blacklist.yaml 并立即热重载"""
    blacklist = _read_blacklist()
    uid = str(body.user_id)
    if uid in blacklist:
        return {"message": f"用户 {uid} 已在黑名单中", "blacklist": blacklist}
    blacklist.append(uid)
    _write_blacklist(blacklist)
    _hot_reload()
    return {"message": f"用户 {uid} 已加入黑名单", "blacklist": blacklist}


@router.delete("/blacklist/{user_id}", summary="从黑名单移除")
async def remove_from_blacklist(user_id: str, auth=Depends(require_scopes("admin"))):
    """从 blacklist.yaml 删除指定用户并立即热重载"""
    blacklist = _read_blacklist()
    uid = str(user_id)
    if uid not in blacklist:
        raise HTTPException(status_code=404, detail=f"用户 {uid} 不在黑名单中")
    blacklist.remove(uid)
    _write_blacklist(blacklist)
    _hot_reload()
    return {"message": f"用户 {uid} 已从黑名单移除", "blacklist": blacklist}


# ── 角色私下往来只读观测（Brief 86，必须在 /{user_id} 之前注册）────────────────

@router.get("/private-log", summary="读取角色私下往来 transcript 尾部")
async def get_private_exchange_log(
    char_a: str,
    char_b: str,
    limit: int = 50,
    auth=Depends(require_scopes("memory.read")),
):
    """管理面板专用只读端点——群聊仲裁页按角色 pair 展示 transcript 尾部。

    transcript 全文只落这一处磁盘，按决策 3（自产内容不固化）永不进入五大记忆库/
    event_log/向量库，唯一回流是 char_relations 摘要投影 + presence 提示。
    """
    from core.stage.private_exchange import load_transcript

    entries = load_transcript(char_a, char_b, limit=max(1, min(limit, 200)))
    return {"char_a": char_a, "char_b": char_b, "entries": entries, "count": len(entries)}


# ── 关系配置接口 ──────────────────────────────────────────────────────────────

@router.get("/{user_id}", summary="获取单用户关系配置")
async def get_relation(user_id: str, auth=Depends(require_scopes("memory.read"))):
    """
    获取指定用户的关系配置。
    raw: relations.yaml 中的原始条目；
    merged: 经 default / 内置默认合并后的实际生效配置。
    """
    from core import user_relation
    raw = _read_relations().get(str(user_id))
    merged = user_relation.get_relation(user_id)
    return {"user_id": user_id, "raw": raw, "merged": merged}


@router.put("/{user_id}", summary="新增或更新用户关系配置")
async def update_relation(user_id: str, body: RelationUpdate, auth=Depends(require_scopes("admin"))):
    """
    合并更新 relations.yaml 中的指定用户条目，然后热重载。
    只传入需要修改的字段即可，未传入字段保持原值。
    permissions 字段也是合并更新，不整体覆盖。
    """
    relations = _read_relations()
    uid = str(user_id)
    existing = dict(relations.get(uid, {}))

    update_data = body.model_dump(exclude_none=True)

    if "permissions" in update_data and "permissions" in existing:
        merged_perms = dict(existing["permissions"])
        merged_perms.update(update_data.pop("permissions"))
        existing["permissions"] = merged_perms
    elif "permissions" in update_data:
        existing["permissions"] = update_data.pop("permissions")

    existing.update(update_data)
    relations[uid] = existing
    _write_relations(relations)
    _hot_reload()
    return {"message": f"用户 {uid} 关系配置已更新", "relation": relations[uid]}


@router.delete("/{user_id}", summary="删除用户关系配置")
async def delete_relation(user_id: str, auth=Depends(require_scopes("admin"))):
    """从 relations.yaml 删除指定用户条目并热重载（不影响 default）"""
    relations = _read_relations()
    uid = str(user_id)
    if uid not in relations:
        raise HTTPException(status_code=404, detail=f"用户 {uid} 没有关系配置")
    del relations[uid]
    _write_relations(relations)
    _hot_reload()
    return {"message": f"用户 {uid} 关系配置已删除"}
