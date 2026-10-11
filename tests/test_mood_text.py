import time
from core.mood_text import get_mood_text


def test_no_signal_or_legacy_provides_no_fact():
    assert get_mood_text({"current": "sleepy"}) == ""
    assert get_mood_text({"current": "neutral", "has_signal": False}) == ""


def test_explicit_source_and_expiry(monkeypatch):
    monkeypatch.setattr(time, "time", lambda: 1000)
    state = {"current": "happy", "has_signal": True, "source_turn_id": "turn-a", "expires_at": 1100}
    assert "turn-a" in get_mood_text(state)
    monkeypatch.setattr(time, "time", lambda: 1100)
    assert get_mood_text(state) == ""
