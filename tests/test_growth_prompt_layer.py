from unittest.mock import MagicMock, patch


def _character():
    char = MagicMock()
    char.name = "Companion"
    char.system_prompt = ""
    char.description = ""
    char.personality = ""
    char.scenario = ""
    char.mes_example = ""
    char.jailbreak_entries = []
    return char


def _build(tags: set[str]) -> list[dict]:
    from core import prompt_builder

    with (
        patch("core.prompt_builder._load_jailbreak", return_value=""),
        patch("core.prompt_builder._load_style_hint", return_value=""),
        patch("core.presence.get_last_seen_text", return_value=""),
        patch("core.author_note_rotator.get_current_note", return_value=""),
        patch("core.config_loader.get_config", return_value={"chat": {"style": "roleplay"}}),
        patch("core.mood_text.get_mood_text", return_value=""),
        patch("core.growth.interest_state.active_interests", return_value=[{
            "id": "int-writing", "name": "写作", "domain": "writing", "level": 3,
        }]),
        patch("core.growth.notes.load", return_value=[{"text": "收束句子比堆叠意象更有力"}]),
    ):
        messages, _ = prompt_builder.build(
            character=_character(), user_id="growth-layer", user_message="你好",
            history=[], relation={"role": "朋友"}, profile={}, group_context=[], tags=tags,
        )
    return messages


def test_writing_topic_injects_level_and_latest_note():
    messages = _build({"topic.writing"})
    layer = next(message for message in messages if message.get("_layer") == "3.8_growth_self")
    assert "level 3" in layer["content"]
    assert "收束句子比堆叠意象更有力" in layer["content"]
    assert layer["_provenance"]["mode"] == "tagged"


def test_direct_growth_question_gets_dedicated_tag():
    from core.tag_rules import get_tags

    assert "query.growth_self" in get_tags("你最近在学什么")


def test_unrelated_chat_does_not_inject_growth_layer():
    assert all(message.get("_layer") != "3.8_growth_self" for message in _build(set()))


def test_no_active_interest_fails_open_without_layer():
    from core import prompt_builder

    with patch("core.growth.interest_state.active_interests", return_value=[]):
        assert prompt_builder._format_growth_self_hint("character-a") == ""
