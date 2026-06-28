"""
世界书管理路由
提供 lorebook.yaml 的增删改查接口（路径由 DataPaths.lorebook() 决定）
"""

from typing import List, Optional
import uuid

import yaml
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel

from admin.auth import verify_token
from core.sandbox import get_paths

router = APIRouter()


# ── 工具函数 ──────────────────────────────────────────────────────────────────

def _new_id() -> str:
    return str(uuid.uuid4())[:8]


def _ensure_ids(data: dict) -> bool:
    """为缺少 id 的条目补发 id，返回是否有补发（需回写）。"""
    changed = False
    for entry in data.get("entries", []):
        if not entry.get("id"):
            entry["id"] = _new_id()
            changed = True
    return changed


def _read_lorebook() -> dict:
    """读取 lorebook.yaml，文件不存在时返回空结构；加载后自动补发缺失 id 并回写。"""
    p = get_paths().lorebook()
    if not p.exists():
        return {"entries": []}
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        if "entries" not in data:
            data["entries"] = []
        if _ensure_ids(data):
            _write_lorebook(data)
        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取 lorebook.yaml 失败: {e}")


def _write_lorebook(data: dict):
    """写回 lorebook.yaml"""
    p = get_paths().lorebook()
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(p, "w", encoding="utf-8") as f:
            yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"写入 lorebook.yaml 失败: {e}")


def _reload_lore_engine():
    """热重载世界书引擎（通过 pipeline_registry 获取当前实例）"""
    try:
        from core.pipeline_registry import get as _get_pipeline
        pipeline = _get_pipeline()
        if pipeline is not None and hasattr(pipeline, "lore_engine"):
            pipeline.lore_engine.load()
    except Exception:
        pass  # admin 单独运行时 pipeline 可能未初始化，忽略


# ── 数据模型 ──────────────────────────────────────────────────────────────────

class LoreEntry(BaseModel):
    keyword: List[str]           # 关键词列表
    content: str                 # 注入到 prompt 的文本
    enabled: bool = True
    regex: bool = False          # True 时 keyword 作为正则表达式匹配
    insertion_order: int = 100   # 数字越小越靠前注入
    id: Optional[str] = None     # 稳定 id，缺失时后端自动生成


# ── 路由 ─────────────────────────────────────────────────────────────────────

@router.get("/lorebook", summary="获取所有世界书条目")
async def get_lorebook(auth=Depends(verify_token)):
    """读取 characters/reality/lorebook.yaml 并返回全部条目"""
    data = _read_lorebook()
    return {"entries": data.get("entries", [])}


@router.post("/lorebook", summary="新增世界书条目")
async def add_lore_entry(entry: LoreEntry, auth=Depends(verify_token)):
    """在 lorebook.yaml 末尾追加一条新条目，并热重载世界书引擎"""
    data = _read_lorebook()
    new_entry = {
        "id":              entry.id or _new_id(),
        "keyword":         entry.keyword,
        "content":         entry.content,
        "enabled":         entry.enabled,
        "regex":           entry.regex,
        "insertion_order": entry.insertion_order,
    }
    data["entries"].append(new_entry)
    _write_lorebook(data)
    _reload_lore_engine()
    return {"message": "条目已添加", "id": new_entry["id"]}


@router.put("/lorebook/{eid}", summary="修改世界书条目")
async def update_lore_entry(eid: str, entry: LoreEntry, auth=Depends(verify_token)):
    """按 id 修改 lorebook.yaml 中的条目，并热重载世界书引擎"""
    data = _read_lorebook()
    entries = data.get("entries", [])

    for i, e in enumerate(entries):
        if e.get("id") == eid:
            entries[i] = {
                "id":              eid,
                "keyword":         entry.keyword,
                "content":         entry.content,
                "enabled":         entry.enabled,
                "regex":           entry.regex,
                "insertion_order": entry.insertion_order,
            }
            data["entries"] = entries
            _write_lorebook(data)
            _reload_lore_engine()
            return {"message": f"条目 {eid} 已更新"}

    raise HTTPException(status_code=404, detail=f"条目 {eid} 不存在")


@router.post("/lorebook/import/txt", summary="从 txt 文件批量导入世界书条目")
async def import_lorebook_txt(file: UploadFile = File(...), auth=Depends(verify_token)):
    """
    读取上传的 .txt 文件，按空行分段：
      - 每段首行作为 keyword（逗号分隔多关键词）
      - 剩余行作为 content
    逐条追加到 lorebook.yaml 并热重载。
    """
    if not (file.filename or "").lower().endswith(".txt"):
        raise HTTPException(status_code=422, detail="只接受 .txt 文件")

    raw = await file.read()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("gbk", errors="replace")

    # 按空行分段
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    if not paragraphs:
        raise HTTPException(status_code=422, detail="文件内容为空或没有空行分段")

    data = _read_lorebook()
    added = 0
    for para in paragraphs:
        lines = [l.rstrip() for l in para.splitlines() if l.strip()]
        if not lines:
            continue
        keyword_line = lines[0]
        keywords = [k.strip() for k in keyword_line.replace("，", ",").split(",") if k.strip()]
        content_lines = lines[1:]
        if not keywords or not content_lines:
            continue
        data["entries"].append({
            "id":              _new_id(),
            "keyword":         keywords,
            "content":         "\n".join(content_lines),
            "enabled":         True,
            "regex":           False,
            "insertion_order": 100,
        })
        added += 1

    if added == 0:
        raise HTTPException(status_code=422, detail="未解析到有效条目，请确认格式：首行关键词，空行分段")

    _write_lorebook(data)
    _reload_lore_engine()
    return {"message": f"已导入 {added} 条世界书条目", "added": added}


@router.delete("/lorebook/{eid}", summary="删除世界书条目")
async def delete_lore_entry(eid: str, auth=Depends(verify_token)):
    """按 id 删除 lorebook.yaml 中的条目，并热重载世界书引擎"""
    data = _read_lorebook()
    entries = data.get("entries", [])
    new_entries = [e for e in entries if e.get("id") != eid]
    if len(new_entries) == len(entries):
        raise HTTPException(status_code=404, detail=f"条目 {eid} 不存在")
    data["entries"] = new_entries
    _write_lorebook(data)
    _reload_lore_engine()
    return {"message": f"条目 {eid} 已删除"}


@router.get("/lorebook/export/json", summary="导出世界书为JSON")
async def export_lorebook_json(auth=Depends(verify_token)):
    """导出完整lorebook.yaml为JSON格式"""
    data = _read_lorebook()
    from fastapi.responses import JSONResponse
    import json
    content = json.dumps({"entries": data.get("entries", [])}, ensure_ascii=False, indent=2)
    from fastapi.responses import Response
    return Response(
        content=content,
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=lorebook.json"}
    )


@router.post("/lorebook/import/json", summary="从JSON文件导入世界书条目")
async def import_lorebook_json(file: UploadFile = File(...), auth=Depends(verify_token)):
    """读取上传的.json文件，合并到现有lorebook.yaml"""
    if not (file.filename or "").lower().endswith(".json"):
        raise HTTPException(status_code=422, detail="只接受 .json 文件")
    raw = await file.read()
    try:
        import json
        incoming = json.loads(raw.decode("utf-8"))
    except Exception:
        raise HTTPException(status_code=422, detail="JSON解析失败")
    
    new_entries = incoming.get("entries", [])
    if not new_entries:
        raise HTTPException(status_code=422, detail="未找到entries字段")
    for e in new_entries:
        if not e.get("id"):
            e["id"] = _new_id()
    data = _read_lorebook()
    data["entries"].extend(new_entries)
    _write_lorebook(data)
    _reload_lore_engine()
    return {"message": f"已导入 {len(new_entries)} 条条目", "added": len(new_entries)}