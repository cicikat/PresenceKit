"""
ActivitySession 通用持久化层。

存储路径:
  data/runtime/activity/{char_id}/{uid}/{activity_type}/{session_id}/session.json

隔离保证：
- char_id + uid 双重隔离，不同角色/用户路径不相交。
- session_id 经 safe_user_id() 验证（全路径已经过 DataPaths._p() 沙盒检查）。
- activity_type 必须在 ALLOWED_ACTIVITY_TYPES 中（ValueError 拒绝未知类型）。

禁止写入：short_term / event_log / user_hidden_state / afterglow — 见 docs/activity-session.md。

同类型单 active session 策略：create_session 发现已有 active session 时先将其 close，
再创建新 session，保证同一 (uid, char_id, activity_type) 最多一个 active session。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from core.safe_write import safe_write_json
from core.sandbox import get_paths

from core.activity.session import ActivitySession, new_session_id, now_iso
from core.activity.types import ALLOWED_ACTIVITY_TYPES

logger = logging.getLogger(__name__)


# ── 验证 ──────────────────────────────────────────────────────────────────────

def _validate_activity_type(activity_type: str) -> None:
    if activity_type not in ALLOWED_ACTIVITY_TYPES:
        raise ValueError(
            f"unknown activity_type: {activity_type!r}  "
            f"(allowed: {sorted(ALLOWED_ACTIVITY_TYPES)})"
        )


# ── 路径助手 ──────────────────────────────────────────────────────────────────

def _session_path(char_id: str, uid: str, activity_type: str, session_id: str) -> Path:
    return get_paths().activity_session_dir(
        char_id=char_id, uid=uid, activity_type=activity_type, session_id=session_id
    ) / "session.json"


# ── 基础读写 ──────────────────────────────────────────────────────────────────

def save_session(session: ActivitySession) -> None:
    """原子写入 session.json。"""
    p = _session_path(session.char_id, session.uid, session.activity_type, session.session_id)
    ok = safe_write_json(p, session.to_dict())
    if not ok:
        logger.error("[activity_store] save failed: %s", session.session_id)


def load_session(
    char_id: str, uid: str, activity_type: str, session_id: str
) -> ActivitySession | None:
    """按完整四元组精确加载 session，不存在返回 None。"""
    p = _session_path(char_id, uid, activity_type, session_id)
    if not p.exists():
        return None
    try:
        return ActivitySession.from_dict(json.loads(p.read_text(encoding="utf-8")))
    except Exception as e:
        logger.error("[activity_store] load failed %s: %s", session_id, e)
        return None


# ── 查询 ──────────────────────────────────────────────────────────────────────

def find_active_session(
    char_id: str, uid: str, activity_type: str
) -> ActivitySession | None:
    """返回该 (char_id, uid, activity_type) 下最新的 active session，无则返回 None。"""
    _validate_activity_type(activity_type)
    root = get_paths().activity_sessions_root(char_id=char_id, uid=uid, activity_type=activity_type)
    if not root.exists():
        return None
    candidates: list[ActivitySession] = []
    for session_dir in root.iterdir():
        if not session_dir.is_dir():
            continue
        p = session_dir / "session.json"
        if not p.exists():
            continue
        try:
            s = ActivitySession.from_dict(json.loads(p.read_text(encoding="utf-8")))
            if s.status == "active":
                candidates.append(s)
        except Exception:
            continue
    if not candidates:
        return None
    candidates.sort(key=lambda s: s.updated_at, reverse=True)
    return candidates[0]


# ── 写操作 ────────────────────────────────────────────────────────────────────

def create_session(
    uid: str,
    char_id: str,
    activity_type: str,
    initial_state: dict | None = None,
) -> ActivitySession:
    """创建新 session。若已存在 active session，先将其 close 再创建。"""
    _validate_activity_type(activity_type)
    existing = find_active_session(char_id, uid, activity_type)
    if existing is not None:
        existing.status = "closed"
        existing.updated_at = now_iso()
        save_session(existing)
        logger.info(
            "[activity_store] auto-closed old session %s (new %s/%s starting)",
            existing.session_id, uid, activity_type,
        )

    now = now_iso()
    session = ActivitySession(
        session_id=new_session_id(),
        uid=uid,
        char_id=char_id,
        activity_type=activity_type,
        status="active",
        state=initial_state or {},
        created_at=now,
        updated_at=now,
    )
    save_session(session)
    logger.info(
        "[activity_store] create: uid=%s char=%s type=%s session=%s",
        uid, char_id, activity_type, session.session_id,
    )
    return session


def update_state(
    char_id: str,
    uid: str,
    activity_type: str,
    session_id: str,
    state: dict,
) -> ActivitySession | None:
    """替换 session.state 并持久化，返回更新后的 session（不存在返回 None）。"""
    session = load_session(char_id, uid, activity_type, session_id)
    if session is None:
        return None
    session.state = state
    session.updated_at = now_iso()
    save_session(session)
    return session


def close_session(
    char_id: str,
    uid: str,
    activity_type: str,
    session_id: str,
) -> ActivitySession | None:
    """关闭 session（幂等：已关闭则直接返回）。"""
    session = load_session(char_id, uid, activity_type, session_id)
    if session is None:
        return None
    if session.status == "closed":
        return session
    session.status = "closed"
    session.updated_at = now_iso()
    save_session(session)
    logger.info("[activity_store] close: session=%s", session_id)
    return session


def save_summary(
    char_id: str,
    uid: str,
    activity_type: str,
    session_id: str,
    summary: dict,
) -> bool:
    """将摘要写入 session 目录的 summary.json（原子写入）。"""
    p = _session_path(char_id, uid, activity_type, session_id).parent / "summary.json"
    ok = safe_write_json(p, summary)
    if not ok:
        logger.error("[activity_store] save_summary failed: %s", session_id)
    return ok


def load_summary(
    char_id: str,
    uid: str,
    activity_type: str,
    session_id: str,
) -> dict | None:
    """加载 session 目录的 summary.json，不存在返回 None。"""
    p = _session_path(char_id, uid, activity_type, session_id).parent / "summary.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        logger.error("[activity_store] load_summary failed %s: %s", session_id, e)
        return None
