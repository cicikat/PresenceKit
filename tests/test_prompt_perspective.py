"""Behavioral contracts for framework perspective and verbatim source material."""
from datetime import date

import pytest

from core.character_loader import Character
from core import prompt_builder
from tests.fixtures.public_assets import TEST_CHAR_ID


@pytest.fixture
def build_prompt(monkeypatch):
    monkeypatch.setattr(prompt_builder, "_load_jailbreak", lambda layer=None: "")
    monkeypatch.setattr(prompt_builder, "_load_style_hint", lambda **kwargs: "")
    monkeypatch.setattr(prompt_builder, "_load_activity_snapshot", lambda **kwargs: "阅读")
    monkeypatch.setattr(prompt_builder, "_format_realtime_awareness", lambda *args, **kwargs: "")
    monkeypatch.setattr("core.activity_manager.get_prompt_fragment", lambda **kwargs: "")
    monkeypatch.setattr("core.author_note_rotator.get_current_note", lambda **kwargs: "")
    monkeypatch.setattr("core.user_relation.has_configured_relation", lambda uid: True)
    monkeypatch.setattr("core.config_loader.get_config", lambda: {"anti_collapse": {"enabled": False}})
    monkeypatch.setattr("core.config_loader.get_user_display_name", lambda: "Test Reader")

    def build(**kwargs):
        params = dict(
            character=Character(name="Test Companion"),
            user_id="test_owner", char_id=TEST_CHAR_ID,
            user_message="你好", history=[], relation={}, profile={}, group_context=[],
        )
        params.update(kwargs)
        return prompt_builder.build(**params)
    return build


def layer(messages, name):
    return next(m["content"] for m in messages if m.get("_layer") == name)


@pytest.mark.parametrize("user_name", ["Test Reader", "", "用户 user {char_name}"])
def test_framework_subjects_are_bound_without_gender_guessing(build_prompt, monkeypatch, user_name):
    monkeypatch.setattr("core.config_loader.get_user_display_name", lambda: user_name)
    messages, _ = build_prompt(relation={"role": "friend", "nickname": "Reader"}, tags={"topic.activity"})
    assert "你是Test Companion" in layer(messages, "1_system_prompt")
    expected = user_name or "她"
    assert f"{expected}是你的friend" in layer(messages, "3_relation")
    assert f"{expected}在阅读" in layer(messages, "3.8_activity")
    assert '你称呼对方为"Reader"' in layer(messages, "3_relation")
    assert "第三人称提及这位对话者时用‘她’" in layer(messages, "1_system_prompt")


def test_authored_and_quoted_system_sources_keep_original_words(build_prompt):
    raw = "用户说：我不是她。The user wrote {user_name}. <user>原文</user>"
    char = Character(name="Test Companion", system_prompt=raw, description=raw, personality=raw, scenario=raw)
    messages, _ = build_prompt(
        character=char, lore_entries=[raw], user_facts_text=raw,
        event_search_result=raw, episodic_result=raw, diary_context=raw,
        user_identity_text=raw, mid_term_context=raw, stage_transcript=raw,
        web_recall_result=raw, author_note_extra=raw, tags={"emotion.down"},
    )
    for name in ("1_system_prompt", "2_char_desc", "5.5_lore", "5.1_user_facts",
                 "6b_event_search", "6c_episodic", "6d_diary_context", "6a_user_identity",
                 "mid_term", "4.2_stage_transcript", "web_recall", "11_author_note"):
        assert raw in layer(messages, name), name
    assert char.system_prompt == raw
    assert "非角色记忆" in layer(messages, "5.1_user_facts")
    assert "外部事实" in layer(messages, "web_recall")
    assert all("_layer" in m and "_provenance" in m for m in messages)


def test_history_examples_and_user_text_keep_speakers(build_prompt):
    raw = "用户 user：我记得她。"
    history = [{"role": "user", "content": raw}, {"role": "assistant", "content": raw}]
    messages, _ = build_prompt(
        character=Character(name="Test Companion", mes_example="{{user}}: " + raw + "\n{{char}}: " + raw),
        user_message=raw, history=history,
    )
    actual = [(m["role"], m["content"]) for m in messages if m.get("_layer") == "9_history" and m["role"] != "system"]
    assert actual == [("user", raw), ("assistant", raw)]
    examples = [m["content"] for m in messages if m.get("_layer") == "7_mes_example_item" and m["role"] != "system"]
    assert examples == [raw, raw]
    assert layer(messages, "12_user_message") == raw
    assert history[0]["content"] == raw


def test_failed_tool_evidence_keeps_source_and_status(build_prompt):
    raw = "读取失败：user 权限不足，用户未授权。"
    messages, _ = build_prompt(tool_result=raw, tool_result_status="tool_failed")
    assert raw in layer(messages, "10_tool_result")
    assert "失败" in layer(messages, "10_tool_result")
    assert "【工具结果已提供】" not in layer(messages, "11_author_note")


def test_sensor_subject_and_provenance(build_prompt, monkeypatch):
    monkeypatch.setattr("core.memory.health_state.load", lambda uid: {
        "phone_sensor_today": {"date": date.today().isoformat(), "steps": 300},
        "sleep_segments": [{"time": date.today().isoformat(), "duration_minutes": 420}],
    })
    messages, _ = build_prompt(tags={"topic.health"})
    assert "Test Reader今天：300步" in layer(messages, "3.7_sensor")
    watch = next(m for m in messages if m.get("_layer") == "3.6_watch")
    assert "Test Reader最近一次睡眠" in watch["content"]
    assert watch["_provenance"]["matched_tags"] == ["topic.health"]


def test_mood_prompt_does_not_resolve_global_character(monkeypatch):
    from core.mood_text import get_mood_text
    monkeypatch.setattr("core.mood_text._char_name", lambda: pytest.fail("global character lookup"))
    assert get_mood_text({"current": "happy", "intensity": .5}, subject="你") == "你此刻：心情不错。"


@pytest.mark.parametrize("system_prompt", ["", "自定义角色规则", "记录：{perception_block}", "## 当前感知（实时，非记忆）\n{perception_block}"])
def test_state_injection_does_not_depend_on_authored_heading(build_prompt, sandbox, system_prompt):
    import json
    from core.observe import prompt_capture
    path = sandbox.mood_state(char_id=TEST_CHAR_ID)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"current": "happy", "intensity": .5}), encoding="utf-8")
    character = Character(name="Scoped Companion", system_prompt=system_prompt)
    messages, meta = build_prompt(character=character, perception_block="跨通道接续 user {user_name}")
    text = layer(messages, "1_system_prompt")
    assert text.count("你此刻：心情不错。") == 1
    assert text.count("跨通道接续 user {user_name}") == 1
    assert character.system_prompt == system_prompt
    prompt_capture.capture("test_owner", messages, meta)
    snapshot = prompt_capture.get_snapshots("test_owner")[0]
    assert any("你此刻：心情不错。" in item.get("content", "") for item in snapshot["layers"])


@pytest.mark.parametrize("raw", [None, "{broken", "[]", "{}"])
def test_missing_or_invalid_mood_is_not_invented(build_prompt, sandbox, raw):
    path = sandbox.mood_state(char_id=TEST_CHAR_ID)
    if raw is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(raw, encoding="utf-8")
    messages, _ = build_prompt()
    assert "你此刻：" not in layer(messages, "1_system_prompt")


def test_perception_ablation_still_hides_fallback(build_prompt, monkeypatch):
    monkeypatch.setattr("core.prompt_ablation.get_state", lambda: {
        "disabled_layers": set(), "perception_block_disabled": True,
    })
    messages, _ = build_prompt(perception_block="private context")
    assert "private context" not in layer(messages, "1_system_prompt")


def test_thinking_mood_uses_scoped_state_without_global_name(sandbox, monkeypatch):
    from core.thinking import _mood_hint
    path = sandbox.mood_state(char_id="other_character")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"current":"happy","intensity":0.5}', encoding="utf-8")
    monkeypatch.setattr("core.mood_text._char_name", lambda: pytest.fail("global character lookup"))
    assert _mood_hint("other_character") == "你此刻：心情不错。"


@pytest.mark.asyncio
@pytest.mark.parametrize("nudge", [True, False])
@pytest.mark.parametrize("result", ["查到时间：10:00", "执行失败：权限不足"])
async def test_builder_rules_remain_valid_after_loop_tool_return(build_prompt, monkeypatch, nudge, result):
    from core.llm_client import ChatTurn
    from tests.test_tool_loop import (
        _make_pipeline, _patch_tool_loop_config, _patch_tools_schema,
        _script_chat_turn, _script_execute, _patch_final_chat,
    )
    messages, _ = build_prompt()
    _patch_tool_loop_config(monkeypatch, nudge_hint=nudge)
    _patch_tools_schema(monkeypatch, ["get_time"])
    call = {"id": "call_test", "type": "function", "function": {"name": "get_time", "arguments": "{}"}}
    calls = _script_chat_turn(monkeypatch, [
        ChatTurn(content="", tool_calls=[{"id": "call_test", "name": "get_time", "arguments": {}}], assistant_message={"role": "assistant", "content": "", "tool_calls": [call]}),
        ChatTurn(content="收到结果。", tool_calls=[], assistant_message={"role": "assistant", "content": "收到结果。"}),
    ])
    _script_execute(monkeypatch, [(result, None)])
    _patch_final_chat(monkeypatch, "收到结果。")
    await _make_pipeline().run_agentic_loop(messages, uid="test_owner", char_id=TEST_CHAR_ID, session_state=object())
    assert len(calls) == 2
    note = layer(calls[-1]["messages"], "11_author_note")
    assert "后续收到的新结果同样适用" in note
    assert "失败、待确认、已受理或结果不明均不代表完成" in note
    assert "本轮没有任何工具执行结果" not in note
    assert "禁止声称调用了任何工具" not in note
    assert any(result in m.get("content", "") for m in calls[-1]["messages"] if m["role"] == "tool")


@pytest.mark.parametrize("pronoun", ["她", "他", "祂", "TA", "它"])
def test_selected_pronoun_reaches_framework_without_rewriting_sources(build_prompt, monkeypatch, pronoun):
    monkeypatch.setattr("core.memory.user_facts.get_user_pronoun", lambda uid: pronoun)
    raw = "用户 user 说她：{user_pronoun}"
    messages, _ = build_prompt(user_identity_text=raw, diary_context=raw, tags={"emotion.down"})
    assert f"用‘{pronoun}’" in layer(messages, "1_system_prompt")
    assert f"关于{pronoun}的长期观察" in layer(messages, "6a_user_identity")
    assert raw in layer(messages, "6a_user_identity")
    assert raw in layer(messages, "6d_diary_context")
    assert f"与{pronoun}真实发生的对话" in layer(messages, "9_history")
