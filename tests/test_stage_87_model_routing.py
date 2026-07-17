"""
tests/test_stage_87_model_routing.py — Brief 87 §2: Stage per-角色路由验收

本单核心验收点：StageCharacterView 的生成路径最终传给 get_model_client 的
char_id 是 speaker 自己的（不是活跃角色，也不会在传递链路上丢失），因此两个
声明了不同 model_routing 的角色在同一个 Stage 群聊里各自解析到自己的 preset。

覆盖：
1. generate()（Phase A 全量 + Phase B 轻量续话，Brief 85 §1）——两个 speaker
   各自路由到自己声明的 preset，互不串扰；轻量续话不因"轻量"而降级到默认。
2. generate_reaction()（Brief 85 §3 短反应）—— 同一 speaker 与 generate() 落在
   同一个 preset（因为路由是按角色卡的 profile 整体绑定，不是按 category 单独配置，
   同一 profile 天然覆盖 chat 与 stage_reaction）。
"""
from __future__ import annotations

import types

import pytest

from core.pipeline import Pipeline
from core.stage.models import Stage, TranscriptEntry
from core.stage.views import StageCharacterView

_MP_CONFIG = {
    "active_routing": "default",
    "defaults": {},
    "presets": {"ds": {}, "claude": {}},
    "routing_profiles": {
        "default": {"chat": "ds", "stage_reaction": "ds"},
        "claude-main": {"chat": "claude", "stage_reaction": "claude"},
    },
}

_CARDS = {
    "char-claude": {"model_routing": "claude-main"},
    "char-ds": {},  # 未声明 → 回落全局 active_routing
}


@pytest.fixture(autouse=True)
def _clear_pipeline_registry():
    from core import pipeline_registry
    pipeline_registry.register(None)
    yield
    pipeline_registry.register(None)


@pytest.fixture(autouse=True)
def _patch_config_and_cards(monkeypatch):
    monkeypatch.setattr("core.model_registry._get_preset_config", lambda: _MP_CONFIG)

    def _fake_load(char_id):
        return types.SimpleNamespace(
            name=char_id, personality="", description="", world_book=[],
            presence_ext=_CARDS.get(char_id, {}),
        )

    monkeypatch.setattr("core.character_loader.load", _fake_load)


def _make_view(char_id: str) -> StageCharacterView:
    """构造一个跳过真实 __init__（避免真读磁盘/LoreEngine）的 view，
    但保留真实 Pipeline 实例，好让 run_llm 真正走到 llm_client.chat 这一跳。
    """
    view = object.__new__(StageCharacterView)
    character = types.SimpleNamespace(
        name=char_id, personality="沉稳", description="", world_book=[],
    )
    view.char_id = char_id
    view._character = character
    view._lore = types.SimpleNamespace(entries=[])
    pipeline = Pipeline(character, view._lore, active_character_id=char_id)
    pipeline.build_prompt = lambda uid, instruction, context, **kw: (
        [{"role": "user", "content": instruction}], {"token_estimate": 1}
    )
    view.pipeline = pipeline
    return view


def _fake_model_client(name: str, reply: str = "ok"):
    from core.model_registry import ModelClient

    async def fake_create(**kwargs):
        msg = types.SimpleNamespace(content=reply)
        choice = types.SimpleNamespace(message=msg)
        return types.SimpleNamespace(choices=[choice])

    completions = types.SimpleNamespace(create=fake_create)
    chat_obj = types.SimpleNamespace(completions=completions)
    fake_client = types.SimpleNamespace(chat=chat_obj)
    return ModelClient(
        name=name, provider_kind="deepseek", model=f"{name}-model",
        tool_call_mode="function_calling", prompt_style="narrative",
        params={}, client=fake_client,
    )


def _install_capturing_get_model_client(monkeypatch, seen: list):
    from core.model_registry import _resolve_preset_name

    def _fake_get_model_client(call_category, *, char_id=None):
        preset_name = _resolve_preset_name(call_category, char_id=char_id)
        seen.append((call_category, char_id, preset_name))
        return _fake_model_client(preset_name)

    monkeypatch.setattr("core.llm_client.get_model_client", _fake_get_model_client)


@pytest.mark.asyncio
async def test_generate_phase_a_and_b_route_by_speaker_own_card(monkeypatch):
    seen: list = []
    _install_capturing_get_model_client(monkeypatch, seen)

    stage = Stage("g1", "owner1", ("char-claude", "char-ds"))
    transcript = [TranscriptEntry("owner", "在吗", 1, "t0", "user")]

    view_claude = _make_view("char-claude")
    view_ds = _make_view("char-ds")

    # Phase A：triggered_by="owner" → 全量 fetch_context 路径（stub 到 Pipeline 实例上，
    # 避免真读记忆/磁盘；本单只关心 run_llm 这一跳的 char_id 是否正确穿线）。
    async def _fake_fetch_context(uid, content, *, frozen_scope=None):
        return _empty_ctx()

    view_claude.pipeline.fetch_context = _fake_fetch_context
    view_ds.pipeline.fetch_context = _fake_fetch_context

    reply_a1 = await view_claude.generate(stage, transcript, "t1", triggered_by="owner")
    reply_a2 = await view_ds.generate(stage, transcript, "t2", triggered_by="owner")

    # Phase B：triggered_by 是另一个角色 id（不在 ("user","owner")）→ 轻量续话路径
    reply_b1 = await view_claude.generate(stage, transcript, "t3", triggered_by="char-ds")
    reply_b2 = await view_ds.generate(stage, transcript, "t4", triggered_by="char-claude")

    assert reply_a1 == reply_a2 == reply_b1 == reply_b2 == "ok"

    # 每个 speaker 无论 Phase A 还是 Phase B，都解析到自己卡声明的 preset：
    # char-claude → claude；char-ds（未声明）→ 全局 default → ds。互不串扰。
    for call_category, char_id, preset_name in seen:
        assert call_category == "chat"
        if char_id == "char-claude":
            assert preset_name == "claude"
        elif char_id == "char-ds":
            assert preset_name == "ds"
        else:
            pytest.fail(f"unexpected char_id in get_model_client call: {char_id!r}")

    # Phase A 和 Phase B 都必须出现，且同一 speaker 两次结果一致（轻量不降级）
    claude_presets = {p for cat, cid, p in seen if cid == "char-claude"}
    ds_presets = {p for cat, cid, p in seen if cid == "char-ds"}
    assert claude_presets == {"claude"}
    assert ds_presets == {"ds"}
    assert len(seen) == 4


def _empty_ctx() -> dict:
    return {
        "history": [], "relation": {}, "profile": {}, "group_context": "",
        "user_identity_text": "", "user_facts_text": "", "event_search_result": "",
        "lore_entries": [], "episodic_result": "", "episodic_fallback_result": "",
        "mid_term": "", "diary_context": "", "reminders": [],
    }


@pytest.mark.asyncio
async def test_generate_reaction_uses_same_preset_as_generate_no_downgrade(monkeypatch):
    """Brief 85 §3 短反应：同一 speaker 走同一路由，不因"轻量"降级到默认 preset。"""
    seen: list = []
    _install_capturing_get_model_client(monkeypatch, seen)

    stage = Stage("g1", "owner1", ("char-claude", "char-ds"))
    transcript = [
        TranscriptEntry("owner", "在吗", 1, "t0", "user"),
        TranscriptEntry("char-ds", "在的", 2, "t0", "owner"),
    ]

    view_claude = _make_view("char-claude")
    reaction = await view_claude.generate_reaction(stage, transcript, "t1", triggered_by="char-ds")
    assert reaction  # 非空短反应文本

    async def _fake_fetch_context(uid, content, *, frozen_scope=None):
        return _empty_ctx()

    view_claude.pipeline.build_prompt = lambda uid, instruction, context, **kw: (
        [{"role": "user", "content": instruction}], {"token_estimate": 1}
    )
    view_claude.pipeline.fetch_context = _fake_fetch_context
    _ = await view_claude.generate(stage, transcript, "t2", triggered_by="char-ds")

    reaction_calls = [c for c in seen if c[0] == "stage_reaction"]
    chat_calls = [c for c in seen if c[0] == "chat"]
    assert reaction_calls and chat_calls
    assert {p for _, _, p in reaction_calls} == {"claude"}
    assert {p for _, _, p in chat_calls} == {"claude"}, "短反应与全量对话必须落在同一个 preset 上"
