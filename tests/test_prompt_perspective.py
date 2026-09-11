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
    expected = user_name or "用户"
    assert f"{expected}是你的friend" in layer(messages, "3_relation")
    assert f"{expected}在阅读" in layer(messages, "3.8_activity")
    assert '你称呼对方为"Reader"' in layer(messages, "3_relation")
    assert "她" not in layer(messages, "3_relation")


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
