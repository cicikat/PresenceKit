"""Brief 85 · Stage group interaction upgrade: reactions, topic seeds, relation arbitration."""
from __future__ import annotations

import pytest


def _settings(**overrides):
    from core.stage.models import StageSettings

    values = {
        "min_responders": 1,
        "max_responders": 1,
        "max_ai_chain_depth": 0,
        "transcript_limit": 200,
    }
    values.update(overrides)
    return StageSettings(**values)


# ── §3 short reactions ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_phase_r_emits_bounded_short_reactions(sandbox, monkeypatch):
    from core.stage import runner as runner_mod
    from core.stage.arbiter import CandidateScore
    from core.stage.store import create_stage

    create_stage(
        "reaction-group",
        "owner",
        ["yexuan", "yexuanJ-5412", "hongcha"],
        settings=_settings(react_threshold=0.2, speak_threshold=0.5, max_reactions=1),
    )

    def fake_score(stg, transcript, *, candidates=None, derived_keywords=None):
        pool = list(candidates) if candidates is not None else list(stg.roster)
        scores = {"yexuan": 0.9, "yexuanJ-5412": 0.3, "hongcha": 0.3}
        ranked = [CandidateScore(char_id=c, total=scores.get(c, 0.0), parts={}) for c in pool]
        ranked.sort(key=lambda item: -item.total)
        return ranked

    monkeypatch.setattr(runner_mod, "score_candidates", fake_score)

    async def generate_reply(stg, speaker_id, transcript, turn_id, triggered_by):
        return f"{speaker_id}回复"

    reaction_calls: list[str] = []

    async def generate_reaction(stg, speaker_id, transcript, turn_id, triggered_by):
        reaction_calls.append(speaker_id)
        return f"{speaker_id}哈哈"

    result = await runner_mod.run_owner_turn(
        "reaction-group",
        "大家好",
        generate_reply=generate_reply,
        generate_reaction=generate_reaction,
        turn_id="t-react",
    )

    reaction_entries = [e for e in result.replies if e.speaker_id != "yexuan"]
    assert len(reaction_entries) == 1
    assert reaction_entries[0].speaker_id == "yexuanJ-5412"
    assert reaction_entries[0].triggered_by == "yexuan"
    assert reaction_calls == ["yexuanJ-5412"]


@pytest.mark.asyncio
async def test_phase_r_skipped_without_generate_reaction_callback(sandbox, monkeypatch):
    """max_reactions defaults to 2 — old callers that don't wire generate_reaction see no change."""
    from core.stage import runner as runner_mod
    from core.stage.arbiter import CandidateScore
    from core.stage.store import create_stage

    create_stage(
        "reaction-group-legacy",
        "owner",
        ["yexuan", "yexuanJ-5412"],
        settings=_settings(react_threshold=0.0, speak_threshold=1.0),
    )

    def fake_score(stg, transcript, *, candidates=None, derived_keywords=None):
        pool = list(candidates) if candidates is not None else list(stg.roster)
        return [CandidateScore(char_id=c, total=0.9, parts={}) for c in pool]

    monkeypatch.setattr(runner_mod, "score_candidates", fake_score)

    async def generate_reply(stg, speaker_id, transcript, turn_id, triggered_by):
        return f"{speaker_id}回复"

    result = await runner_mod.run_owner_turn(
        "reaction-group-legacy", "大家好", generate_reply=generate_reply, turn_id="t-legacy",
    )

    assert {e.speaker_id for e in result.replies} == {"yexuan"}


@pytest.mark.asyncio
async def test_generate_reaction_truncates_and_caps_tokens(sandbox, monkeypatch):
    from core.stage.models import Stage, TranscriptEntry
    from core.stage.views import StageCharacterView, REACTION_MAX_CHARS

    captured = {}

    async def fake_chat(messages, **kwargs):
        captured["messages"] = messages
        captured["kwargs"] = kwargs
        return "这是一句超过十五个字上限的短反应文本用来测试截断行为"

    monkeypatch.setattr("core.llm_client.chat", fake_chat)

    view = object.__new__(StageCharacterView)
    view.char_id = "yexuanJ-5412"
    from types import SimpleNamespace

    view._character = SimpleNamespace(name="乙", personality="直率", description="")
    stage = Stage("g", "owner", ("yexuan", "yexuanJ-5412"), settings=_settings())
    transcript = [
        TranscriptEntry("owner", "在吗", 1, "t", "user"),
        TranscriptEntry("yexuan", "我在忙", 2, "t", "user"),
    ]

    reaction = await view.generate_reaction(stage, transcript, "t", "yexuan")

    assert len(reaction) <= REACTION_MAX_CHARS
    assert captured["kwargs"]["max_tokens_override"] == 40
    assert captured["kwargs"]["char_id"] == "yexuanJ-5412"
