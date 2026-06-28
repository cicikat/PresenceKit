"""
日记上下文独立存储
日记内容不写入 event_log，单独存储，只注入 prompt 不参与检索。
"""
import json
import re
import time
from pathlib import Path
from core.error_handler import log_error
from core.sandbox import get_paths, safe_user_id

_DATE_HDR_RE = re.compile(r"^#\s*(\d{4}-\d{2}-\d{2})", re.MULTILINE)


def _diary_context_read_path(user_id: str, *, char_id: str = "yexuan") -> Path:
    uid = safe_user_id(user_id)
    return get_paths().user_memory_root(uid, char_id=char_id) / "diary_context.txt"


def _diary_context_write_path(user_id: str, *, char_id: str = "yexuan") -> Path:
    """写路径：始终写新布局。"""
    uid = safe_user_id(user_id)
    p = get_paths().user_memory_root(uid, char_id=char_id) / "diary_context.txt"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _meta_path(user_id: str, *, char_id: str = "yexuan") -> Path:
    uid = safe_user_id(user_id)
    p = get_paths().user_memory_root(uid, char_id=char_id) / "diary_context.meta.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _parse_latest_date(text: str) -> str | None:
    """从 '# YYYY-MM-DD' 头里取最新日期；无则 None。"""
    dates = _DATE_HDR_RE.findall(text or "")
    return max(dates) if dates else None


def save(user_id: str, text: str, *, char_id: str = "yexuan"):
    """text 为空也写（清空快照），同时写元数据。"""
    try:
        _diary_context_write_path(user_id, char_id=char_id).write_text(text or "", encoding="utf-8")
        meta = {"captured_at": time.time(), "latest_entry_date": _parse_latest_date(text)}
        _meta_path(user_id, char_id=char_id).write_text(
            json.dumps(meta, ensure_ascii=False), encoding="utf-8"
        )
    except Exception as e:
        log_error("diary_context.save", e)


def load_meta(user_id: str, *, char_id: str = "yexuan") -> dict:
    """返回 {captured_at, latest_entry_date}；缺失返回 {}。"""
    try:
        p = _meta_path(user_id, char_id=char_id)
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        log_error("diary_context.load_meta", e)
    return {}


def load(user_id: str, *, char_id: str = "yexuan") -> str:
    try:
        p = _diary_context_read_path(user_id, char_id=char_id)
        if p.exists():
            return p.read_text(encoding="utf-8").strip()
    except Exception as e:
        log_error("diary_context.load", e)
    return ""