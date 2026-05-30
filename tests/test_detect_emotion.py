"""
Tests for detect_emotion() validation logic.

Covers:
- thinking / sleepy pass through (not fallen back to neutral)
- unrecognised emotion still falls back to neutral
"""

import types

import pytest


def _make_fake_client(emotion: str):
    """Return a minimal fake AsyncOpenAI client whose create() yields `emotion`."""

    async def fake_create(**kwargs):
        msg = types.SimpleNamespace(content=emotion)
        choice = types.SimpleNamespace(message=msg)
        return types.SimpleNamespace(choices=[choice])

    completions = types.SimpleNamespace(create=fake_create)
    chat = types.SimpleNamespace(completions=completions)
    return types.SimpleNamespace(chat=chat)


@pytest.mark.asyncio
@pytest.mark.parametrize("emotion", ["thinking", "sleepy"])
async def test_new_emotions_not_fallen_back(emotion, monkeypatch):
    from core import llm_client

    monkeypatch.setattr(llm_client, "_get_client", lambda: _make_fake_client(emotion))
    assert await llm_client.detect_emotion("some text") == emotion


@pytest.mark.asyncio
async def test_invalid_emotion_falls_back_to_neutral(monkeypatch):
    from core import llm_client

    monkeypatch.setattr(llm_client, "_get_client", lambda: _make_fake_client("confused"))
    assert await llm_client.detect_emotion("some text") == "neutral"
