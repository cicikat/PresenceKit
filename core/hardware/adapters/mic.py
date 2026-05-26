"""Dry input adapter for microphone-derived acoustic features.

Phase 2 does not capture audio. Callers feed frames into this adapter, and raw
audio bytes are reduced inside this module to scalar features only.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from typing import Any, AsyncIterator

from core.hardware.base import InputDevice


MicFeature = dict[str, bool | int]


class MicDevice(InputDevice):
    def __init__(
        self,
        device_id: str = "mic.default",
        *,
        bucket_thresholds: tuple[float, float, float] = (4.0, 16.0, 48.0),
        vad_bucket: int = 1,
    ) -> None:
        self._device_id = device_id
        self._bucket_thresholds = bucket_thresholds
        self._vad_bucket = vad_bucket

    @property
    def device_id(self) -> str:
        return self._device_id

    @property
    def modality(self) -> str:
        return "audio"

    @property
    def dangerous(self) -> bool:
        return False

    async def signals(self) -> AsyncIterator[MicFeature]:
        for feature in ():
            yield feature

    def analyze_frame(self, frame: object) -> MicFeature:
        rms = _rms(frame)
        bucket = self._bucket_for_rms(rms)
        return {"vad": bucket >= self._vad_bucket, "rms_bucket": bucket}

    def analyze_frames(self, frames: Iterable[object]) -> list[MicFeature]:
        return [self.analyze_frame(frame) for frame in frames]

    def _bucket_for_rms(self, rms: float) -> int:
        low, medium, high = self._bucket_thresholds
        if rms < low:
            return 0
        if rms < medium:
            return 1
        if rms < high:
            return 2
        return 3


def _rms(frame: object) -> float:
    samples = _iter_sample_values(frame)
    total = 0.0
    count = 0
    for sample in samples:
        total += sample * sample
        count += 1
    if count == 0:
        return 0.0
    return math.sqrt(total / count)


def _iter_sample_values(frame: object) -> Iterable[float]:
    if isinstance(frame, Mapping):
        for key in ("audio", "audio_bytes", "pcm", "frame", "data", "raw_audio"):
            if key in frame:
                return _iter_sample_values(frame[key])
        for key in ("samples", "values"):
            if key in frame:
                return _iter_numeric_values(frame[key])
        return ()
    if isinstance(frame, (bytes, bytearray, memoryview)):
        view = memoryview(frame)
        return (float(value) - 128.0 for value in view.cast("B"))
    return _iter_numeric_values(frame)


def _iter_numeric_values(value: Any) -> Iterable[float]:
    if isinstance(value, (int, float)):
        return (float(value),)
    if isinstance(value, Iterable) and not isinstance(value, (str, bytes, bytearray, memoryview, Mapping)):
        return (float(item) for item in value if isinstance(item, (int, float)))
    return ()
