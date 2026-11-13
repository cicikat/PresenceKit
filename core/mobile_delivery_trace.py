"""Bounded, process-local delivery timings. Only opaque message hashes are retained."""

from collections import OrderedDict
from hashlib import sha256
from math import ceil
from threading import Lock
import time

_lock = Lock()
_traces = OrderedDict()


def mark(message_id: str, stage: str, *, at_ms: int | None = None, **details) -> None:
    if not message_id:
        return
    key = sha256(message_id.encode("utf-8")).hexdigest()[:16]
    entry = {"stage": stage, "at_ms": at_ms if at_ms is not None else time.time_ns() // 1_000_000, **details}
    with _lock:
        stages = _traces.setdefault(key, [])
        stages.append(entry)
        del stages[:-24]
        _traces.move_to_end(key)
        if len(_traces) > 256:
            _traces.popitem(last=False)


def snapshot() -> dict:
    with _lock:
        traces = [{"id_hash": key, "events": list(events)} for key, events in reversed(_traces.items())]
    segments = {}
    for segment, start, end in (
        ("backend", "turn_ready", "queued"),
        ("relay", "publish_started", "publish_finished"),
    ):
        samples = []
        for trace in traces:
            events = trace["events"]
            first = next((event for event in events if event["stage"] == start), None)
            last = next((event for event in reversed(events) if event["stage"] == end), None)
            if first and last and last.get("outcome", "ok") == "ok" and last["at_ms"] >= first["at_ms"]:
                samples.append(last["at_ms"] - first["at_ms"])
        if samples:
            samples.sort()
            segments[segment] = {
                "count": len(samples),
                "p50_ms": samples[ceil(len(samples) * 0.50) - 1],
                "p95_ms": samples[ceil(len(samples) * 0.95) - 1],
            }
    return {"traces": traces, "segments": segments}
