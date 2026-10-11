from datetime import date, timedelta
from unittest.mock import patch


def _build_layers(segment_date: date, tags: set[str]) -> list[str]:
    from unittest.mock import MagicMock
    from core import prompt_builder

    char = MagicMock()
    char.name = "Companion"
    char.system_prompt = ""
    char.description = ""
    char.personality = ""
    char.scenario = ""
    char.mes_example = ""
    char.jailbreak_entries = []
    profile = {
        "sleep_segments": [{
            "time": segment_date.isoformat(),
            "duration_minutes": 420,
            "sleep_start": "00:10",
            "sleep_end_time": "07:10",
        }]
    }
    with (
        patch("core.prompt_builder._load_jailbreak", return_value=""),
        patch("core.prompt_builder._load_style_hint", return_value=""),
        patch("core.presence.get_last_seen_text", return_value=""),
        patch("core.author_note_rotator.get_current_note", return_value=""),
        patch("core.config_loader.get_config", return_value={
            "chat": {"style": "roleplay"}, "watch": {"fresh_days": 3},
        }),
        patch("core.memory.health_state.load", return_value=profile),
        patch("core.mood_text.get_mood_text", return_value=""),
    ):
        messages, _ = prompt_builder.build(
            character=char,
            user_id="watch-freshness",
            user_message="我最近睡得怎么样",
            history=[],
            relation={"role": "朋友"},
            profile={},
            group_context=[],
            tags=tags,
        )
    return [message.get("_layer", "") for message in messages]


def test_watch_segment_four_days_old_is_stale():
    from core.prompt_builder import _watch_segment_is_fresh

    today = date(2026, 7, 16)
    with patch("core.config_loader.get_config", return_value={"watch": {"fresh_days": 3}}):
        assert _watch_segment_is_fresh(
            (today - timedelta(days=4)).isoformat(), today=today
        ) is False


def test_watch_segment_one_day_old_is_fresh():
    from core.prompt_builder import _watch_segment_is_fresh

    today = date(2026, 7, 16)
    with patch("core.config_loader.get_config", return_value={"watch": {"fresh_days": 3}}):
        assert _watch_segment_is_fresh(
            (today - timedelta(days=1)).isoformat(), today=today
        ) is True


def test_watch_triggers_exclude_broad_emotion_tags():
    from core.prompt_builder import _WATCH_TRIGGERS

    assert "emotion.down" not in _WATCH_TRIGGERS
    assert "emotion.indirect" not in _WATCH_TRIGGERS
    assert _WATCH_TRIGGERS == {
        "topic.energy", "topic.health", "topic.activity", "query.body_state",
    }


def test_health_topic_skips_four_day_old_watch_layer():
    assert "3.6_watch" not in _build_layers(
        date.today() - timedelta(days=4), {"topic.health"}
    )


def test_health_topic_injects_one_day_old_watch_layer():
    assert "3.6_watch" in _build_layers(
        date.today() - timedelta(days=1), {"topic.health"}
    )


def test_emotion_down_alone_does_not_inject_watch_layer():
    assert "3.6_watch" not in _build_layers(
        date.today() - timedelta(days=1), {"emotion.down"}
    )
