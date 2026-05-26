"""Microphone-derived perception source.

This source turns MicDevice scalar features into high-sensitivity runtime
perception events and sends them to the Phase 0 shadow intake only.
"""

from __future__ import annotations

from time import time
from typing import Iterable

from core.hardware.adapters.mic import MicFeature
from core.perception import intake
from core.perception.events import PerceptionEvent, create_event


SOURCE = "mic"
TTL_SECONDS = 30.0


def event_from_feature(feature: MicFeature, *, now_ts: float | None = None) -> PerceptionEvent:
    timestamp = time() if now_ts is None else now_ts
    vad = bool(feature["vad"])
    rms_bucket = int(feature["rms_bucket"])
    return create_event(
        source=SOURCE,
        modality="acoustic",
        type="vad",
        subject=None,
        timestamp=timestamp,
        confidence=_confidence(vad, rms_bucket),
        visibility="runtime",
        sensitivity="high",
        expires_at=timestamp + TTL_SECONDS,
        dedupe_key=f"{SOURCE}:vad:{int(vad)}",
        payload={"vad": vad, "rms_bucket": rms_bucket},
    )


def submit_features(features: Iterable[MicFeature], *, now_ts: float | None = None) -> list[PerceptionEvent]:
    submitted = []
    for feature in features:
        submitted.append(intake.submit(event_from_feature(feature, now_ts=now_ts)))
    return submitted


def _confidence(vad: bool, rms_bucket: int) -> float:
    if not vad:
        return 0.45
    return min(0.75, 0.50 + (max(0, min(rms_bucket, 3)) * 0.08))
