"""Normalized perception event contracts for Phase 0."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from time import time
from typing import Any, Mapping


SCHEMA_VERSION = 1
MAX_PAYLOAD_BYTES = 8 * 1024
MAX_PAYLOAD_DEPTH = 4
MAX_COLLECTION_ITEMS = 64
MAX_STRING_LENGTH = 1024


class Visibility(str, Enum):
    RUNTIME = "runtime"
    STATE = "state"
    MEMORY = "memory"


class Sensitivity(str, Enum):
    LOW = "low"
    HIGH = "high"


@dataclass(frozen=True)
class PerceptionAffects:
    can_affect_state: bool = False
    can_affect_mood: bool = False
    can_write_memory: bool = False


@dataclass(frozen=True)
class PerceptionEvent:
    schema_version: int
    source: str
    modality: str
    type: str
    subject: str | None
    timestamp: float
    confidence: float
    visibility: Visibility
    sensitivity: Sensitivity
    expires_at: float | None
    dedupe_key: str | None
    payload: dict[str, Any] = field(default_factory=dict)
    affects: PerceptionAffects = field(default_factory=PerceptionAffects)


def create_event(
    *,
    source: str,
    modality: str,
    type: str,
    subject: str | None = None,
    timestamp: float | None = None,
    confidence: float = 1.0,
    visibility: Visibility | str = Visibility.RUNTIME,
    sensitivity: Sensitivity | str = Sensitivity.LOW,
    expires_at: float | None = None,
    dedupe_key: str | None = None,
    payload: dict[str, Any] | None = None,
    affects: PerceptionAffects | Mapping[str, Any] | None = None,
) -> PerceptionEvent:
    event = PerceptionEvent(
        schema_version=SCHEMA_VERSION,
        source=source,
        modality=modality,
        type=type,
        subject=subject,
        timestamp=time() if timestamp is None else timestamp,
        confidence=confidence,
        visibility=Visibility(visibility),
        sensitivity=Sensitivity(sensitivity),
        expires_at=expires_at,
        dedupe_key=dedupe_key,
        payload={} if payload is None else payload,
        affects=_coerce_affects(affects),
    )
    return validate_event(event)


def validate_event(event: PerceptionEvent | Mapping[str, Any]) -> PerceptionEvent:
    raw = _event_to_mapping(event)
    required = {
        "schema_version",
        "source",
        "modality",
        "type",
        "subject",
        "timestamp",
        "confidence",
        "visibility",
        "sensitivity",
        "expires_at",
        "dedupe_key",
        "payload",
        "affects",
    }
    missing = sorted(required - set(raw))
    if missing:
        raise ValueError(f"missing perception event fields: {', '.join(missing)}")
    if raw["schema_version"] != SCHEMA_VERSION:
        raise ValueError("unsupported perception event schema_version")
    for field_name in ("source", "modality", "type"):
        if not isinstance(raw[field_name], str) or not raw[field_name]:
            raise ValueError(f"{field_name} must be a non-empty string")
    if raw["subject"] is not None and not isinstance(raw["subject"], str):
        raise ValueError("subject must be a string or None")
    if not isinstance(raw["timestamp"], (int, float)):
        raise ValueError("timestamp must be numeric")
    if raw["expires_at"] is not None and not isinstance(raw["expires_at"], (int, float)):
        raise ValueError("expires_at must be numeric or None")
    if raw["dedupe_key"] is not None and not isinstance(raw["dedupe_key"], str):
        raise ValueError("dedupe_key must be a string or None")
    confidence = raw["confidence"]
    if not isinstance(confidence, (int, float)) or not 0.0 <= float(confidence) <= 1.0:
        raise ValueError("confidence must be between 0 and 1")

    visibility = Visibility(raw["visibility"])
    sensitivity = Sensitivity(raw["sensitivity"])
    payload = raw["payload"]
    if not isinstance(payload, dict):
        raise ValueError("payload must be a dict")
    _validate_payload(payload)

    return PerceptionEvent(
        schema_version=SCHEMA_VERSION,
        source=raw["source"],
        modality=raw["modality"],
        type=raw["type"],
        subject=raw["subject"],
        timestamp=float(raw["timestamp"]),
        confidence=float(confidence),
        visibility=visibility,
        sensitivity=sensitivity,
        expires_at=None if raw["expires_at"] is None else float(raw["expires_at"]),
        dedupe_key=raw["dedupe_key"],
        payload=dict(payload),
        affects=_coerce_affects(raw["affects"]),
    )


def event_to_dict(event: PerceptionEvent) -> dict[str, Any]:
    row = asdict(event)
    row["visibility"] = event.visibility.value
    row["sensitivity"] = event.sensitivity.value
    return row


def _event_to_mapping(event: PerceptionEvent | Mapping[str, Any]) -> Mapping[str, Any]:
    if isinstance(event, PerceptionEvent):
        return {
            **event_to_dict(event),
            "affects": asdict(event.affects),
        }
    if isinstance(event, Mapping):
        return event
    raise TypeError("event must be a PerceptionEvent or mapping")


def _coerce_affects(value: PerceptionAffects | Mapping[str, Any] | None) -> PerceptionAffects:
    if value is None:
        return PerceptionAffects()
    if isinstance(value, PerceptionAffects):
        return value
    required = {"can_affect_state", "can_affect_mood", "can_write_memory"}
    missing = sorted(required - set(value))
    if missing:
        raise ValueError(f"missing affects fields: {', '.join(missing)}")
    return PerceptionAffects(
        can_affect_state=bool(value["can_affect_state"]),
        can_affect_mood=bool(value["can_affect_mood"]),
        can_write_memory=bool(value["can_write_memory"]),
    )


def _validate_payload(payload: dict[str, Any]) -> None:
    import json

    _validate_payload_value(payload, depth=0)
    try:
        encoded = json.dumps(payload, ensure_ascii=False)
    except TypeError as e:
        raise ValueError("payload must be JSON serializable") from e
    if len(encoded.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        raise ValueError("payload is too large")


def _validate_payload_value(value: Any, *, depth: int) -> None:
    if isinstance(value, (bytes, bytearray, memoryview)):
        raise ValueError("payload must not contain raw media bytes")
    if depth > MAX_PAYLOAD_DEPTH:
        raise ValueError("payload nesting is too deep")
    if value is None or isinstance(value, (bool, int, float)):
        return
    if isinstance(value, str):
        if len(value) > MAX_STRING_LENGTH:
            raise ValueError("payload string value is too large")
        return
    if isinstance(value, Mapping):
        if len(value) > MAX_COLLECTION_ITEMS:
            raise ValueError("payload dict has too many items")
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("payload keys must be strings")
            _validate_payload_value(item, depth=depth + 1)
        return
    raise ValueError(f"payload contains unsupported value type: {type(value).__name__}")
