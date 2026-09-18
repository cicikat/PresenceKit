"""日记只读接口
GET /diary/list  — 返回日记列表（不含正文）
GET /diary/{date} — 返回单篇日记（含正文）

char_id 查询参数：指定角色；缺省 = active char。
"""

import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from admin.auth import require_scopes
from admin.routers._common import resolve_requested_char_id
from core.sandbox import get_paths

router = APIRouter()

_DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
_FILE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}\.md$')
_STOP_CHARS = '。！？'


def _active_char_id() -> str:
    import json as _json
    try:
        data = _json.loads(get_paths().active_prompt_assets().read_text(encoding="utf-8"))
        cid = (data.get("active_character") or "").strip()
        if cid:
            return cid
    except Exception:
        pass
    raise HTTPException(status_code=503, detail="active_character missing")


def _derive_title(content: str) -> str:
    if not content.strip():
        return '(空)'
    lines = content.split('\n')
    if lines and lines[0].startswith('# '):
        lines = lines[1:]
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith('## '):
            continue
        sentence = ''
        for ch in stripped:
            sentence += ch
            if ch in _STOP_CHARS:
                break
        if sentence:
            return sentence[:20] + '…' if len(sentence) > 20 else sentence
    return '(空)'


def _strip_body(content: str) -> str:
    lines = content.split('\n')
    if lines and lines[0].startswith('# '):
        lines = lines[1:]
    return '\n'.join(lines).lstrip('\n')


def _split_layers(content: str) -> tuple[str, str]:
    """Return (facts, feeling), keeping the event layer off the UI API."""
    body = _strip_body(content)
    marker = re.search(r"(?m)^##\s*今日感受\s*$", body)
    if marker:
        return body[:marker.start()].strip(), body[marker.end():].strip()
    if re.search(r"(?m)^##\s*今日事件\s*$", body):
        return body.strip(), ""
    # Legacy entries had no section headings; they are still readable and
    # are treated as a single feeling block for the presentation layer.
    return "", body.strip()


def _feeling_title(feeling: str) -> str:
    return _derive_title(feeling)


@router.get("/list", summary="获取日记列表")
async def list_diary(
    char_id: Optional[str] = Query(default=None, description="角色 id；缺省 = active char"),
    auth=Depends(require_scopes("memory.read")),
):
    cid = resolve_requested_char_id(char_id, fallback=_active_char_id)
    entries = []
    diary_dir = get_paths().yexuan_inner_diary(char_id=cid)
    if diary_dir.exists():
        for f in diary_dir.iterdir():
            if not _FILE_RE.match(f.name):
                continue
            content = f.read_text(encoding='utf-8')
            _, feeling = _split_layers(content)
            entries.append({
                "date": f.stem,
                "title": _feeling_title(feeling),
                "emotion": None,
                "feeling": feeling,
            })
    entries.sort(key=lambda e: e["date"], reverse=True)
    return {"entries": entries, "count": len(entries)}


@router.get("/{date}", summary="获取单篇日记")
async def get_diary(
    date: str,
    char_id: Optional[str] = Query(default=None, description="角色 id；缺省 = active char"),
    auth=Depends(require_scopes("memory.read")),
):
    if not _DATE_RE.match(date):
        raise HTTPException(status_code=422, detail="date format must be YYYY-MM-DD")
    cid = resolve_requested_char_id(char_id, fallback=_active_char_id)
    path = get_paths().yexuan_inner_diary(char_id=cid) / f"{date}.md"
    if not path.exists():
        raise HTTPException(status_code=404, detail="diary not found")
    content = path.read_text(encoding='utf-8')
    _, feeling = _split_layers(content)
    return {
        "date": date,
        "title": _feeling_title(feeling),
        "emotion": None,
        "feeling": feeling,
        "body": feeling,
    }
