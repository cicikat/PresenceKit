"""
Tests for get_mood_text() — verifies thinking/sleepy have dedicated copy
and do not fall back to neutral wording.
"""

import pytest
from core.mood_text import MOOD_TEXT, get_mood_text

_NEUTRAL_TEXTS = set(MOOD_TEXT["neutral"])


@pytest.mark.parametrize("emotion", ["thinking", "sleepy"])
def test_emotion_has_own_entry(emotion):
    assert emotion in MOOD_TEXT, f"{emotion} missing from MOOD_TEXT"


@pytest.mark.parametrize("emotion,intensity", [
    ("thinking", 0.2),
    ("thinking", 0.5),
    ("thinking", 0.8),
    ("sleepy",   0.2),
    ("sleepy",   0.5),
    ("sleepy",   0.8),
])
def test_not_neutral_fallback(emotion, intensity):
    state = {"current": emotion, "intensity": intensity}
    text = get_mood_text(state)
    for neutral_phrase in _NEUTRAL_TEXTS:
        assert neutral_phrase not in text, (
            f"get_mood_text({emotion!r}, intensity={intensity}) returned neutral copy: {text!r}"
        )
