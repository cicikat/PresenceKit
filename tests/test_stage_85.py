"""Brief 85 · Stage group interaction upgrade: reactions, topic seeds, relation arbitration."""
from __future__ import annotations
from tests.fixtures.public_assets import TEST_CHAR_ID, TEST_PEER_CHAR_ID, TEST_THIRD_CHAR_ID

import pytest


# ── §5 relation arbitration + recent_moments ────────────────────────────────


def test_arbiter_peer_reply_modulated_by_valence(sandbox):
    from core.stage.arbiter import PEER_REPLY_BASE, score_candidates
    from core.stage.char_relations import _empty_relation, _save_relation
    from core.stage.models import Stage, StageSettings, TranscriptEntry

    stage = Stage(
        "g", "owner", (TEST_CHAR_ID, TEST_PEER_CHAR_ID),
        settings=StageSettings(talkativeness={TEST_PEER_CHAR_ID: 1.0}),
    )
    transcript = [
        TranscriptEntry("owner", "在吗", 1, "t", "user"),
        TranscriptEntry(TEST_CHAR_ID, "我在", 2, "t", "user"),
    ]

    baseline = score_candidates(stage, transcript, candidates=[TEST_PEER_CHAR_ID])[0]
    assert baseline.parts["peer_reply"] == pytest.approx(PEER_REPLY_BASE)

    relation = _empty_relation(TEST_CHAR_ID, TEST_PEER_CHAR_ID)
    relation["b_of_a"]["valence"] = 1.0
    assert _save_relation(relation)
    fond = score_candidates(stage, transcript, candidates=[TEST_PEER_CHAR_ID])[0]
    assert fond.parts["peer_reply"] == pytest.approx(PEER_REPLY_BASE * 1.2)

    relation["b_of_a"]["valence"] = -1.0
    assert _save_relation(relation)
    cold = score_candidates(stage, transcript, candidates=[TEST_PEER_CHAR_ID])[0]
    assert cold.parts["peer_reply"] == pytest.approx(PEER_REPLY_BASE * 0.8)


@pytest.mark.asyncio
async def test_relation_handler_rolls_recent_moments_capped_at_five(sandbox, monkeypatch):
    from core.stage.char_relations import handler_update_char_relations, recent_moments

    call_n = {"i": 0}

    async def fake_chat(*args, **kwargs):
        call_n["i"] += 1
        return (
            '{"a_of_b":{"summary":"甲觉得乙靠谱","valence":0.1},'
            '"b_of_a":{"summary":"乙觉得甲随和","valence":0.1},'
            f'"moment":"往事{call_n["i"]}"}}'
        )

    monkeypatch.setattr("core.llm_client.chat", fake_chat)
    for i in range(7):
        await handler_update_char_relations({
            "uid": "owner", "char_a": TEST_CHAR_ID, "char_b": TEST_PEER_CHAR_ID,
            "excerpt": "甲→乙：回应", "timestamp": 100000.0 + i * 3600 * 7,
            "write_envelope": {"source": "user_chat", "can_write_memory": True},
        })

    assert call_n["i"] == 7
    assert recent_moments(TEST_CHAR_ID, TEST_PEER_CHAR_ID) == [f"往事{i}" for i in range(3, 8)]


def test_relation_prompt_constrains_tone_against_unwarranted_intimacy(sandbox):
    """2026-07-25（茶茶反馈）：群聊里角色会没来由地说出对另一角色比较暧昧的话
    （如"今晚我跟{另一角色}聊天的时候……"）——根因是 _relation_prompt() 生成的
    摘要会被 core/stage/context.py::render_presence() 原样当作"既有印象"注入
    每一轮群聊 prompt，角色会当真，而旧 prompt 对措辞语气没有任何约束。"""
    from core.stage.char_relations import _relation_prompt

    prompt = _relation_prompt(TEST_CHAR_ID, TEST_PEER_CHAR_ID, "甲→乙：一起写了首歌", {})
    assert "不要主动往暧昧" in prompt or "不要臆测" in prompt, (
        "_relation_prompt 必须显式约束措辞，禁止在互动内容之外主动添加暧昧色彩"
    )


def test_recent_moments_backward_compatible_with_old_relation_files(sandbox):
    from core.safe_write import safe_write_json
    from core.sandbox import get_paths
    from core.stage.char_relations import _pair, recent_moments

    char_a, char_b = _pair(TEST_CHAR_ID, TEST_PEER_CHAR_ID)
    old_style = {
        "char_a": char_a, "char_b": char_b,
        "a_of_b": {"summary": "旧摘要", "valence": 0.0, "updated_at": ""},
        "b_of_a": {"summary": "", "valence": 0.0, "updated_at": ""},
        "interaction_count": 1, "last_interaction_ts": 0.0,
    }
    assert safe_write_json(get_paths().char_relation(char_a=char_a, char_b=char_b), old_style)

    assert recent_moments(TEST_CHAR_ID, TEST_PEER_CHAR_ID) == []


# ── §4 topic seed ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_phase_t_topic_seed_triggers_when_round_falls_flat(sandbox, monkeypatch):
    from core.stage import runner as runner_mod
    from core.stage.models import StageSettings
    from core.stage.store import create_stage

    create_stage(
        "seed-group",
        "owner",
        [TEST_CHAR_ID, TEST_PEER_CHAR_ID, TEST_THIRD_CHAR_ID],
        settings=StageSettings(
            min_responders=1, max_responders=1, max_ai_chain_depth=0, max_reactions=0,
            topic_seed_prob=1.0,
            talkativeness={TEST_CHAR_ID: 1.0, TEST_PEER_CHAR_ID: 0.9, TEST_THIRD_CHAR_ID: 0.2},
        ),
    )
    monkeypatch.setattr(runner_mod.random, "random", lambda: 0.0)

    async def generate_reply(stg, speaker_id, transcript, turn_id, triggered_by):
        return f"{speaker_id}说话"

    result = await runner_mod.run_owner_turn(
        "seed-group", "随便聊聊", generate_reply=generate_reply, turn_id="t-seed",
    )

    # Phase A: only yexuan speaks (max_responders=1); B/R are off → 1 < 2 falls flat.
    # The seed goes to the highest-talkativeness char who hasn't spoken: yexuanJ-5412.
    seed_entries = [e for e in result.replies if e.triggered_by == "topic_seed"]
    assert len(seed_entries) == 1
    assert seed_entries[0].speaker_id == TEST_PEER_CHAR_ID


@pytest.mark.asyncio
async def test_phase_t_skipped_by_probability_gate(sandbox, monkeypatch):
    from core.stage import runner as runner_mod
    from core.stage.models import StageSettings
    from core.stage.store import create_stage

    create_stage(
        "seed-group-noprob",
        "owner",
        [TEST_CHAR_ID, TEST_PEER_CHAR_ID],
        settings=StageSettings(
            min_responders=1, max_responders=1, max_ai_chain_depth=0, max_reactions=0,
            topic_seed_prob=0.25,
        ),
    )
    monkeypatch.setattr(runner_mod.random, "random", lambda: 0.99)

    async def generate_reply(stg, speaker_id, transcript, turn_id, triggered_by):
        return f"{speaker_id}说话"

    result = await runner_mod.run_owner_turn(
        "seed-group-noprob", "随便聊聊", generate_reply=generate_reply, turn_id="t-noprob",
    )

    assert not any(e.triggered_by == "topic_seed" for e in result.replies)


@pytest.mark.asyncio
async def test_phase_t_skipped_when_round_did_not_fall_flat(sandbox, monkeypatch):
    from core.stage import runner as runner_mod
    from core.stage.arbiter import CandidateScore
    from core.stage.models import StageSettings
    from core.stage.store import create_stage

    create_stage(
        "seed-group-lively",
        "owner",
        [TEST_CHAR_ID, TEST_PEER_CHAR_ID],
        settings=StageSettings(
            min_responders=1, max_responders=1, max_ai_chain_depth=1, max_reactions=0,
            topic_seed_prob=1.0,
        ),
    )
    monkeypatch.setattr(runner_mod.random, "random", lambda: 0.0)

    def fake_score(stg, transcript, *, candidates=None, derived_keywords=None):
        pool = list(candidates) if candidates is not None else list(stg.roster)
        return [CandidateScore(char_id=c, total=0.9, parts={}) for c in pool]

    monkeypatch.setattr(runner_mod, "score_candidates", fake_score)

    _TEXT = {TEST_CHAR_ID: "今天天气不错", TEST_PEER_CHAR_ID: "你怎么看？"}

    async def generate_reply(stg, speaker_id, transcript, turn_id, triggered_by):
        return _TEXT[speaker_id]

    result = await runner_mod.run_owner_turn(
        "seed-group-lively", "随便聊聊", generate_reply=generate_reply, turn_id="t-lively",
    )

    # 2 substantive replies (Phase A + Phase B), last one ends in a question → not flat.
    assert len([e for e in result.replies if e.triggered_by != "topic_seed"]) == 2
    assert not any(e.triggered_by == "topic_seed" for e in result.replies)


@pytest.mark.asyncio
async def test_round_llm_call_budget_hard_cap(sandbox, monkeypatch):
    """max_responders + max_ai_chain_depth + max_reactions + 1(seed) is a hard ceiling."""
    from core.stage import runner as runner_mod
    from core.stage.arbiter import CandidateScore
    from core.stage.models import StageSettings
    from core.stage.store import create_stage

    settings = StageSettings(
        min_responders=1, max_responders=1, max_ai_chain_depth=1, max_reactions=0,
        topic_seed_prob=1.0,
    )
    create_stage("budget-group", "owner", [TEST_CHAR_ID, TEST_PEER_CHAR_ID, TEST_THIRD_CHAR_ID], settings=settings)
    monkeypatch.setattr(runner_mod.random, "random", lambda: 0.0)

    def fake_score(stg, transcript, *, candidates=None, derived_keywords=None):
        pool = list(candidates) if candidates is not None else list(stg.roster)
        return [CandidateScore(char_id=c, total=0.99, parts={}) for c in pool]

    monkeypatch.setattr(runner_mod, "score_candidates", fake_score)

    _TEXT = {
        TEST_CHAR_ID: "今天天气还不错吧",
        TEST_PEER_CHAR_ID: "我倒是觉得有点闷热",
        TEST_THIRD_CHAR_ID: "对了我们聊聊别的",
    }
    call_log: list[tuple[str, str]] = []

    async def generate_reply(stg, speaker_id, transcript, turn_id, triggered_by):
        call_log.append((speaker_id, triggered_by))
        return _TEXT[speaker_id]

    result = await runner_mod.run_owner_turn(
        "budget-group", "随便聊聊", generate_reply=generate_reply, turn_id="t-budget",
    )

    budget = settings.max_responders + settings.max_ai_chain_depth + settings.max_reactions + 1
    assert len(call_log) <= budget
    assert len(call_log) == 3  # tight in this scenario: 1 (A) + 1 (B) + 0 (R) + 1 (T)
    assert any(triggered_by == "topic_seed" for _speaker, triggered_by in call_log)
    assert len(result.replies) == len(call_log)


@pytest.mark.asyncio
async def test_topic_seed_block_assembles_activity_topic_and_moment(sandbox, monkeypatch):
    from core.scheduler.last_mentioned import mark_topic_followed
    from core.stage.char_relations import _empty_relation, _save_relation
    from core.stage.models import Stage, TranscriptEntry
    from core.stage.views import StageCharacterView

    mark_topic_followed("没聊完的暑假计划")
    relation = _empty_relation(TEST_CHAR_ID, TEST_PEER_CHAR_ID)
    relation["recent_moments"] = ["上次一起弹琴"]
    assert _save_relation(relation)

    captured = {}

    class FakePipeline:
        async def fetch_context(self, uid, content, *, frozen_scope):
            raise AssertionError("topic seed must use the lightweight path")

        def build_prompt(self, uid, content, context, **kwargs):
            captured["content"] = content
            return ([{"role": "user", "content": content}], {"token_estimate": 1})

        async def run_llm(self, messages, *, char_id=None):
            return "新话题来了"

    view = object.__new__(StageCharacterView)
    view.char_id = TEST_CHAR_ID
    view.pipeline = FakePipeline()
    stage = Stage("g", "owner", (TEST_CHAR_ID, TEST_PEER_CHAR_ID))
    transcript = [TranscriptEntry("owner", "在吗", 1, "t", "user")]

    reply = await view.generate(stage, transcript, "t", "topic_seed")

    assert reply == "新话题来了"
    assert "没聊完的暑假计划" in captured["content"]
    assert "上次一起弹琴" in captured["content"]


def test_directed_block_and_presence_surface_recent_moment(sandbox):
    from core.stage.char_relations import _empty_relation, _save_relation
    from core.stage.context import render_presence
    from core.stage.models import Stage

    relation = _empty_relation(TEST_CHAR_ID, TEST_PEER_CHAR_ID)
    relation["recent_moments"] = ["上次乙帮甲调琴"]
    assert _save_relation(relation)

    stage = Stage("g", "owner", (TEST_CHAR_ID, TEST_PEER_CHAR_ID))
    presence = render_presence(stage, viewer_id=TEST_CHAR_ID)
    assert "上次乙帮甲调琴" in presence


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
        [TEST_CHAR_ID, TEST_PEER_CHAR_ID, TEST_THIRD_CHAR_ID],
        settings=_settings(react_threshold=0.2, speak_threshold=0.5, max_reactions=1, topic_seed_prob=0.0),
    )

    def fake_score(stg, transcript, *, candidates=None, derived_keywords=None):
        pool = list(candidates) if candidates is not None else list(stg.roster)
        scores = {TEST_CHAR_ID: 0.9, TEST_PEER_CHAR_ID: 0.3, TEST_THIRD_CHAR_ID: 0.3}
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

    reaction_entries = [e for e in result.replies if e.speaker_id != TEST_CHAR_ID]
    assert len(reaction_entries) == 1
    assert reaction_entries[0].speaker_id == TEST_PEER_CHAR_ID
    assert reaction_entries[0].triggered_by == TEST_CHAR_ID
    assert reaction_calls == [TEST_PEER_CHAR_ID]


@pytest.mark.asyncio
async def test_phase_r_skipped_without_generate_reaction_callback(sandbox, monkeypatch):
    """max_reactions defaults to 2 — old callers that don't wire generate_reaction see no change."""
    from core.stage import runner as runner_mod
    from core.stage.arbiter import CandidateScore
    from core.stage.store import create_stage

    create_stage(
        "reaction-group-legacy",
        "owner",
        [TEST_CHAR_ID, TEST_PEER_CHAR_ID],
        settings=_settings(react_threshold=0.0, speak_threshold=1.0, topic_seed_prob=0.0),
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

    assert {e.speaker_id for e in result.replies} == {TEST_CHAR_ID}


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
    view.char_id = TEST_PEER_CHAR_ID
    from types import SimpleNamespace

    view._character = SimpleNamespace(name="乙", personality="直率", description="")
    stage = Stage("g", "owner", (TEST_CHAR_ID, TEST_PEER_CHAR_ID), settings=_settings())
    transcript = [
        TranscriptEntry("owner", "在吗", 1, "t", "user"),
        TranscriptEntry(TEST_CHAR_ID, "我在忙", 2, "t", "user"),
    ]

    reaction = await view.generate_reaction(stage, transcript, "t", TEST_CHAR_ID)

    assert len(reaction) <= REACTION_MAX_CHARS
    assert captured["kwargs"]["max_tokens_override"] == 40
    assert captured["kwargs"]["char_id"] == TEST_PEER_CHAR_ID
