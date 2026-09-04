"""Typed contracts for the Reality Task Manager."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


TASK_SCHEMA_VERSION = "agent-runtime-task.v1"
STORE_SCHEMA_VERSION = "agent-runtime-task-store.v1"


class _StringEnum(str, Enum):
    def __str__(self) -> str:
        return self.value


class TaskStatus(_StringEnum):
    CREATED = "created"
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_CONFIRM = "waiting_confirm"
    PAUSED = "paused"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"
    EXPIRED = "expired"
    OUTCOME_UNKNOWN = "outcome_unknown"


TERMINAL_STATUSES = frozenset({
    TaskStatus.SUCCEEDED.value,
    TaskStatus.FAILED.value,
    TaskStatus.CANCELED.value,
    TaskStatus.EXPIRED.value,
    TaskStatus.OUTCOME_UNKNOWN.value,
})


class RetryPolicy(_StringEnum):
    NEVER = "never"
    SAFE = "safe"


@dataclass(frozen=True)
class TaskPrincipal:
    uid: str
    char_id: str
    realm: str = "reality"

    @classmethod
    def reality(cls, uid: str | int, char_id: str) -> "TaskPrincipal":
        return cls(uid=str(uid), char_id=str(char_id), realm="reality")


@dataclass(frozen=True)
class CausationRef:
    kind: str
    reference: str


@dataclass
class TaskRecord:
    task_id: str
    uid: str
    char_id: str
    capability: str
    source: str
    status: str
    created_at: float
    updated_at: float
    ttl_seconds: int
    expires_at: float
    idempotency_digest: str
    request_digest: str
    request_fingerprint: str = ""
    request_summary: dict[str, Any] = field(default_factory=dict)
    confirmation_required: bool = False
    confirmation_granted_at: float = 0.0
    realm: str = "reality"
    schema_version: str = TASK_SCHEMA_VERSION
    causation_ref: dict[str, Any] | None = None
    retry_policy: str = RetryPolicy.NEVER.value
    max_attempts: int = 1
    attempt_count: int = 0
    queued_at: float = 0.0
    started_at: float = 0.0
    finished_at: float = 0.0
    next_attempt_at: float = 0.0
    current_attempt_id: str = ""
    lease_owner_instance: str = ""
    lease_token: str = ""
    lease_until: float = 0.0
    cancel_requested_at: float = 0.0
    cancel_reason_code: str = ""
    pause_requested_at: float = 0.0
    error_code: str = ""
    recovery_reason: str = ""
    result_metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "TaskRecord":
        if not isinstance(raw, dict):
            raise TypeError("task record must be an object")
        values = {key: raw[key] for key in cls.__dataclass_fields__ if key in raw}
        return cls(**values)

    @property
    def terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES


@dataclass(frozen=True)
class TaskLease:
    task_id: str
    attempt_id: str
    lease_token: str
    lease_until: float
    receipt: dict[str, Any]
