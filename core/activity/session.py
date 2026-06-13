"""
ActivitySession — reading / gomoku / chess 共用的活动会话模型。

设计约束（见 docs/activity-session.md）：
- Reality-side session，不接 Dream / Scenario / trigger / stimulus。
- 必须由用户显式 API 调用启动，不自动触发。
- state 字段存放 activity-specific 数据，不写入 short_term / event_log / user_hidden_state。
- 规则、胜负、合法性由代码判断，不由 LLM 判断。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

SessionStatus = Literal["active", "closed"]


@dataclass
class ActivitySession:
    session_id: str
    uid: str
    char_id: str
    activity_type: str   # reading | gomoku | chess | dream_seed
    status: SessionStatus
    state: dict
    created_at: str      # ISO 8601 UTC
    updated_at: str      # ISO 8601 UTC

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "uid": self.uid,
            "char_id": self.char_id,
            "activity_type": self.activity_type,
            "status": self.status,
            "state": self.state,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ActivitySession":
        return cls(
            session_id=data["session_id"],
            uid=data["uid"],
            char_id=data["char_id"],
            activity_type=data["activity_type"],
            status=data["status"],
            state=data.get("state") or {},
            created_at=data["created_at"],
            updated_at=data["updated_at"],
        )


def new_session_id() -> str:
    """全局唯一 session id（32 位十六进制，仅含 [0-9a-f]）。"""
    return uuid.uuid4().hex


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
