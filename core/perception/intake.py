"""Phase 0 perception intake.

Intake validates and policy-normalizes events, then writes only a shadow log.
It does not call the pipeline, scheduler, memory, tools, LLMs, or devices.
"""

from __future__ import annotations

import json
from dataclasses import replace

from core.sandbox import get_paths

from .events import PerceptionEvent, event_to_dict, validate_event
from .policy import decide


def submit(event: PerceptionEvent) -> PerceptionEvent:
    validated = validate_event(event)
    normalized = replace(validated, affects=decide(validated))
    path = get_paths()._p("logs", "perception_shadow.jsonl")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event_to_dict(normalized), ensure_ascii=False) + "\n")
    return normalized
