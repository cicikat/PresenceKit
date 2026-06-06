"""Smoke test: memory-isolation-p1-checkpoint.md exists and contains required sections."""
from pathlib import Path

DOC = Path(__file__).parent.parent / "docs" / "memory-isolation-p1-checkpoint.md"

REQUIRED_STRINGS = [
    "P1 freeze checkpoint",
    "P1-FINAL",
    "known violations",
    "known violations cleared",
    "character_growth legacy/dead",
    "slow_queue scope payload",
    "pipeline MemoryScope",
    "P2 migration",
]


def test_checkpoint_doc_exists():
    assert DOC.exists(), f"checkpoint doc missing: {DOC}"


def test_checkpoint_doc_contains_required_sections():
    text = DOC.read_text(encoding="utf-8")
    missing = [s for s in REQUIRED_STRINGS if s.lower() not in text.lower()]
    assert not missing, f"checkpoint doc missing required strings: {missing}"
