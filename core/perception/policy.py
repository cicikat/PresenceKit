"""Phase 0 perception policy skeleton."""

from .events import PerceptionAffects, PerceptionEvent, validate_event


def decide(event: PerceptionEvent) -> PerceptionAffects:
    validate_event(event)
    return PerceptionAffects()
