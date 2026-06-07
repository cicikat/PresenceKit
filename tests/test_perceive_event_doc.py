"""Smoke test: docs/scheduler.md contains required perceive_event terms."""
from pathlib import Path

DOC = Path(__file__).parent.parent / "docs" / "scheduler.md"

REQUIRED_STRINGS = [
    "perceive_event",
    "dedupe_key",
    "event_id is tracing only",
    "last_seen must not be part of dedupe payload",
    "desktop_wake",
    "scheduler",
    "conversation_lock",
]


def test_perceive_event_doc_exists():
    assert DOC.exists(), f"scheduler doc missing: {DOC}"


def test_perceive_event_doc_contains_required_terms():
    text = DOC.read_text(encoding="utf-8")
    missing = [s for s in REQUIRED_STRINGS if s.lower() not in text.lower()]
    assert not missing, f"scheduler doc missing required terms: {missing}"
