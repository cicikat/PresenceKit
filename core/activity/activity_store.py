"""
ReadingSession 持久化层。

存储路径（v1 布局）:
  data/runtime/activity/reading/{char_id}/{uid}/{session_id}/
    metadata.json        — ReadingSession 元数据（不含页面正文）
    pages/{n}.txt        — 第 n 页文本（1-indexed，页码即文件名前缀）

隔离保证：
- char_id + uid 双重隔离，两角色不共用路径。
- session_id 经 safe_user_id() 验证（全路径已经过 DataPaths._p() 沙盒检查）。

禁止写入：short_term / event_log / user_hidden_state / afterglow — 见 docs/reading-activity.md。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from core.data_paths import safe_user_id
from core.safe_write import safe_write_json, safe_write_text
from core.sandbox import get_paths

from core.activity.reading_session import ReadingSession

logger = logging.getLogger(__name__)


# ── 路径助手 ──────────────────────────────────────────────────────────────────

def _session_dir(char_id: str, uid: str, session_id: str) -> Path:
    return get_paths().reading_session_dir(
        char_id=char_id, uid=uid, session_id=session_id
    )


def _pages_dir(char_id: str, uid: str, session_id: str) -> Path:
    return _session_dir(char_id, uid, session_id) / "pages"


# ── 写操作 ─────────────────────────────────────────────────────────────────────

def save_session(session: ReadingSession) -> None:
    """原子写入 session metadata（不含页面正文）。"""
    p = _session_dir(session.char_id, session.uid, session.session_id) / "metadata.json"
    ok = safe_write_json(p, session.to_dict())
    if not ok:
        logger.error("[activity_store] 保存 metadata 失败: %s", session.session_id)


def save_pages(char_id: str, uid: str, session_id: str, pages: list[str]) -> None:
    """逐页保存文本，pages[i] 是第 i+1 页（1-indexed）。"""
    d = _pages_dir(char_id, uid, session_id)
    d.mkdir(parents=True, exist_ok=True)
    for i, text in enumerate(pages):
        page_no = i + 1
        safe_write_text(d / f"{page_no}.txt", text)


# ── 读操作 ─────────────────────────────────────────────────────────────────────

def load_session(char_id: str, uid: str, session_id: str) -> ReadingSession | None:
    """按 char_id + uid + session_id 精确加载 session（已知 uid 时使用）。"""
    p = _session_dir(char_id, uid, session_id) / "metadata.json"
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return ReadingSession.from_dict(data)
    except Exception as e:
        logger.error("[activity_store] 加载 session 失败 %s: %s", session_id, e)
        return None


def load_session_by_id(char_id: str, session_id: str) -> ReadingSession | None:
    """按 session_id 扫描该 char_id 下所有 uid 子目录定位 session（不知道 uid 时使用）。"""
    char_root = get_paths().reading_char_root(char_id=char_id)
    if not char_root.exists():
        return None
    safe_sid = safe_user_id(session_id)
    for uid_dir in char_root.iterdir():
        if not uid_dir.is_dir():
            continue
        meta = uid_dir / safe_sid / "metadata.json"
        if not meta.exists():
            continue
        try:
            data = json.loads(meta.read_text(encoding="utf-8"))
            return ReadingSession.from_dict(data)
        except Exception:
            continue
    return None


def load_page(char_id: str, uid: str, session_id: str, page: int) -> str | None:
    """读取第 page 页（1-indexed）的文本，不存在返回 None。"""
    p = _pages_dir(char_id, uid, session_id) / f"{page}.txt"
    if not p.exists():
        return None
    try:
        return p.read_text(encoding="utf-8")
    except Exception as e:
        logger.error("[activity_store] 读取页面失败 page=%d: %s", page, e)
        return None


def find_active_session(char_id: str, uid: str) -> ReadingSession | None:
    """返回该 char_id + uid 下最新的 active session，无则返回 None。"""
    root = get_paths().reading_sessions_root(char_id=char_id, uid=uid)
    if not root.exists():
        return None
    candidates: list[ReadingSession] = []
    for session_dir in root.iterdir():
        if not session_dir.is_dir():
            continue
        meta = session_dir / "metadata.json"
        if not meta.exists():
            continue
        try:
            data = json.loads(meta.read_text(encoding="utf-8"))
            s = ReadingSession.from_dict(data)
            if s.status == "active":
                candidates.append(s)
        except Exception:
            continue
    if not candidates:
        return None
    candidates.sort(key=lambda s: s.updated_at, reverse=True)
    return candidates[0]
