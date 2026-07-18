"""tests/test_birthday_placeholder.py — Brief 95 §1：owner_birthday 占位符残留视同未填"""
from datetime import datetime

from core.scheduler.triggers import birthday


def test_birthday_returns_none_when_unfilled(monkeypatch):
    monkeypatch.setattr(birthday, "_cfg", lambda: {})
    assert birthday._birthday() is None


def test_birthday_placeholder_literal_treated_as_unfilled(monkeypatch):
    # config.example.yaml 里未改的占位符原样是字面量 "MM-DD"
    monkeypatch.setattr(birthday, "_cfg", lambda: {"owner_birthday": "MM-DD"})
    assert birthday._birthday() is None


def test_birthday_impossible_date_treated_as_unfilled(monkeypatch):
    monkeypatch.setattr(birthday, "_cfg", lambda: {"owner_birthday": "02-30"})
    assert birthday._birthday() is None


def test_birthday_valid_value_parses(monkeypatch):
    monkeypatch.setattr(birthday, "_cfg", lambda: {"owner_birthday": "04-24"})
    assert birthday._birthday() == (4, 24)


def test_unfilled_birthday_never_triggers_today_or_eve(monkeypatch):
    monkeypatch.setattr(birthday, "_cfg", lambda: {"owner_birthday": "MM-DD"})
    assert birthday._is_birthday_today() is False
    assert birthday._is_birthday_eve() is False


def test_unfilled_birthday_propose_returns_none(monkeypatch):
    monkeypatch.setattr(birthday, "_cfg", lambda: {})
    assert birthday.propose({"now_dt": datetime(2026, 1, 1, 0, 2)}) is None
