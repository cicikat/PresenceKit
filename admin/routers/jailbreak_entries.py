"""
破限预设条目化管理
存储路径由 DataPaths.jailbreak_entries() 决定
"""
from typing import Optional
import logging
import json
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import Response
from pydantic import BaseModel

from admin.auth import require_scopes
from core.sandbox import get_paths
from admin.routers.users import _resolve_char_id
from core.safe_write import safe_write_text

router = APIRouter()
logger = logging.getLogger(__name__)


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


def _read(char_id: str | None = None) -> dict:
    p = get_paths().jailbreak_entries(char_id=char_id)
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if _ensure_ids(data):
                _write(data, char_id)
            return data
        except Exception:
            pass
    return {"entries": []}


def _write(data: dict, char_id: str | None = None):
    paths = get_paths()
    read_path = paths.jailbreak_entries(char_id=char_id)
    p = paths.reality_jailbreak_write_path(char_id=char_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    if not safe_write_text(p, json.dumps(data, ensure_ascii=False, indent=2)):
        raise HTTPException(status_code=500, detail="prompt entries save failed")
    logger.info(
        "[authored-writer] kind=reality_jailbreak effective_read_source=%s canonical_write_target=user",
        "user" if read_path == p else "legacy",
    )


def _reload():
    from core import config_loader
    config_loader.reload_config()


class JbEntry(BaseModel):
    title:   str
    content: str
    enabled: bool = True
    layer:   int  = 0


@router.get("/jailbreak-entries", summary="获取所有破限条目")
async def get_entries(char_id: str | None = None, auth=Depends(require_scopes("persona"))):
    char_id = _resolve_char_id(char_id)
    return {**_read(char_id), "char_id": char_id}


@router.get("/jailbreak-entries/export/json", summary="导出破限条目JSON")
async def export_entries(char_id: str | None = None, auth=Depends(require_scopes("persona"))):
    char_id = _resolve_char_id(char_id)
    p = get_paths().jailbreak_entries(char_id=char_id)
    content = p.read_text(encoding="utf-8") if p.exists() else '{"entries":[]}'
    return Response(content=content, media_type="application/json",
                    headers={"Content-Disposition": "attachment; filename=jailbreak_entries.json"})


@router.post("/jailbreak-entries/import/json", summary="导入破限条目JSON")
async def import_entries_json(file: UploadFile = File(...), char_id: str | None = None, auth=Depends(require_scopes("persona"))):
    char_id = _resolve_char_id(char_id)
    raw = await file.read()
    try:
        incoming = json.loads(raw.decode("utf-8"))
    except Exception:
        raise HTTPException(status_code=422, detail="JSON解析失败")
    new_entries = incoming.get("entries", [])
    if not new_entries:
        raise HTTPException(status_code=422, detail="未找到entries字段")
    for e in new_entries:
        if not e.get("id"):
            e["id"] = _new_id()
    data = _read(char_id)
    data["entries"].extend(new_entries)
    _write(data, char_id)
    return {"message": f"已导入 {len(new_entries)} 条", "added": len(new_entries)}


@router.post("/jailbreak-entries/import/txt", summary="导入破限预设txt")
async def import_entries_txt(file: UploadFile = File(...), char_id: str | None = None, auth=Depends(require_scopes("persona"))):
    char_id = _resolve_char_id(char_id)
    if not (file.filename or "").endswith(".txt"):
        raise HTTPException(status_code=422, detail="只接受.txt文件")
    raw = await file.read()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("gbk", errors="replace")
    data = _read(char_id)
    data["entries"].append({
        "id":      _new_id(),
        "title":   Path(file.filename or "untitled").stem,
        "content": text.strip(),
        "enabled": True,
        "layer":   0,
    })
    _write(data, char_id)
    return {"message": "已导入"}


@router.post("/jailbreak-entries", summary="新增破限条目")
async def add_entry(entry: JbEntry, char_id: str | None = None, auth=Depends(require_scopes("persona"))):
    char_id = _resolve_char_id(char_id)
    data = _read(char_id)
    data["entries"].append({
        "id":      _new_id(),
        "title":   entry.title,
        "content": entry.content,
        "enabled": entry.enabled,
        "layer":   entry.layer,
    })
    _write(data, char_id)
    return {"message": "已添加"}


@router.put("/jailbreak-entries/{eid}", summary="修改破限条目")
async def update_entry(eid: str, entry: JbEntry, char_id: str | None = None, auth=Depends(require_scopes("persona"))):
    char_id = _resolve_char_id(char_id)
    data = _read(char_id)
    for e in data["entries"]:
        if e["id"] == eid:
            e.update({"title": entry.title, "content": entry.content,
                      "enabled": entry.enabled, "layer": entry.layer})
            _write(data, char_id)
            return {"message": "已更新"}
    raise HTTPException(status_code=404, detail="条目不存在")


@router.delete("/jailbreak-entries/{eid}", summary="删除破限条目")
async def delete_entry(eid: str, char_id: str | None = None, auth=Depends(require_scopes("persona"))):
    char_id = _resolve_char_id(char_id)
    data = _read(char_id)
    data["entries"] = [e for e in data["entries"] if e["id"] != eid]
    _write(data, char_id)
    return {"message": "已删除"}






