"""
ReadingSession — 一起看书 Activity 的会话模型。

设计约束（见 docs/reading-activity.md）：
- Reality-side Activity，不接 Dream / Scenario / trigger / stimulus。
- 必须由用户显式操作（API 调用）启动，不自动触发。
- 页面内容不写入 short_term / event_log / user_hidden_state。
"""
from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal


SessionStatus = Literal["active", "closed"]


@dataclass
class ReadingSession:
    session_id: str
    uid: str
    char_id: str
    file_id: str
    filename: str
    total_pages: int
    current_page: int        # 1-indexed，对外展示
    created_at: str          # ISO 8601 UTC
    updated_at: str          # ISO 8601 UTC
    status: SessionStatus
    mode: Literal["reading"] = "reading"

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "uid": self.uid,
            "char_id": self.char_id,
            "file_id": self.file_id,
            "filename": self.filename,
            "total_pages": self.total_pages,
            "current_page": self.current_page,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "status": self.status,
            "mode": self.mode,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ReadingSession":
        return cls(
            session_id=data["session_id"],
            uid=data["uid"],
            char_id=data["char_id"],
            file_id=data["file_id"],
            filename=data["filename"],
            total_pages=int(data["total_pages"]),
            current_page=int(data["current_page"]),
            created_at=data["created_at"],
            updated_at=data["updated_at"],
            status=data["status"],
            mode=data.get("mode", "reading"),
        )


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_session_id() -> str:
    """生成全局唯一 session id（32 位十六进制，仅含 [0-9a-f]）。"""
    return uuid.uuid4().hex


def make_file_id(filename: str) -> str:
    """从文件名派生 file_id（同名文件重用相同 id）。"""
    return "f_" + hashlib.sha1(filename.encode()).hexdigest()[:12]
