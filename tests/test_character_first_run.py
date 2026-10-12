import json

import pytest

from core.character_loader import Character
from core.character_data import load_traits
from core.memory import user_facts
from core.memory.trait_tracker import update_trait_state
from core.author_note_rotator import get_current_note
from core import prompt_builder


@pytest.mark.parametrize("value", [[], None, {"windows": "broken"}, {"windows": [None, {"counts": {"calm": "broken"}}]}])
def test_malformed_trait_window_uses_current_evidence(sandbox, value):
    path = sandbox.trait_state(char_id="fresh")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8", newline="\n")
    update_trait_state({"calm": 1}, path)
    result = json.loads(path.read_text(encoding="utf-8"))
    assert result["windows"][0]["counts"] == {"calm": 1}
    assert result["underrepresented"] == ["calm"]


def test_missing_or_invalid_trait_and_author_materials_are_optional(sandbox):
    assert load_traits("fresh") == []
    assert get_current_note(sandbox, char_id="fresh") == ""
    path = sandbox.user_authored_character_dir(char_id="fresh") / "traits.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'traits: [invalid YAML')
    with pytest.raises(ValueError, match="invalid trait YAML"):
        load_traits("fresh")


@pytest.mark.parametrize("value", [{}, [], 1, None])
def test_invalid_pronoun_is_neutral(sandbox, value):
    user_facts.save_user_facts("new-owner", {"pronoun": value}, char_id="fresh")
    assert user_facts.get_user_pronoun("new-owner", char_id="fresh") == "她"


def test_fresh_character_prompt_survives_optional_switches_without_materials(sandbox, monkeypatch):
    # No model, watch, sensor, authored notes, trait, memory or optional API
    # connection is seeded. These flags must not create required schema keys.
    monkeypatch.setattr("core.config_loader.get_config", lambda: {
        "chat": {}, "scheduler": {}, "watch": {}, "hds_local": {"enabled": True, "character_read_enabled": True},
        "audio_music": {"speech_analysis": {"enabled": True}}, "visual_perception": {"enabled": True},
        "life_records": {"enabled": True, "character_readable": True},
    })
    messages, _ = prompt_builder.build(
        character=Character(name="Companion"), user_id="new-owner", char_id="fresh",
        user_message="Hello", history=[], relation={}, profile={}, group_context=[],
        tags={"topic.health", "topic.body", "topic.activity"},
    )
    assert any(message.get("_layer") == "12_user_message" for message in messages)
    assert not {"3.5_period", "3.6_watch", "3.7_sensor", "3.75_heart_rate"} & {message.get("_layer") for message in messages}
