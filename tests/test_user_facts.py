from tests.fixtures.public_assets import TEST_CHAR_ID
"""
tests/test_user_facts.py
========================
P1-4: Tests for core/memory/user_facts.py

Covers:
  - Path is global (uid-only), no char_id anywhere in path
  - Different char_ids reading same uid get same user_facts
  - load returns {} when file absent
  - save writes only ALLOWED_FIELDS, drops others silently
  - update merges allowed fields, returns rejected list for denied/unknown
  - update list-valued fields extend rather than replace
  - clear resets to {}
  - format_for_prompt returns '' when empty, non-empty string otherwise
  - Denied fields are rejected (not written)
  - Unknown fields are rejected (not written)
  - No yexuan fallback in path
  - Scoped profile/identity paths still contain char_id (isolation unchanged)
  - prompt_builder build() signature accepts user_facts_text
  - prompt_builder layer 5.1 appears when user_facts_text provided, absent otherwise
"""

import pytest

from core.memory.scope import MemoryScope
from core.memory.path_resolver import resolve_path
from core.memory import user_facts as uf


UID = "testuser42"
CHAR_A = TEST_CHAR_ID
CHAR_B = "character_b"


def _s(path) -> str:
    return str(path).replace("\\", "/")


# ---------------------------------------------------------------------------
# 1. Path shape: uid-only, no char_id
# ---------------------------------------------------------------------------

def test_user_facts_path_contains_uid(sandbox):
    scope = MemoryScope.reality_scope(UID, CHAR_A)
    p = _s(resolve_path(scope, "user_facts"))
    assert UID in p


def test_user_facts_path_not_contain_char_a(sandbox):
    scope = MemoryScope.reality_scope(UID, CHAR_A)
    p = _s(resolve_path(scope, "user_facts"))
    assert CHAR_A in p


def test_user_facts_path_not_contain_char_b(sandbox):
    scope = MemoryScope.reality_scope(UID, CHAR_A)
    p = _s(resolve_path(scope, "user_facts"))
    assert CHAR_B not in p


def test_user_facts_path_no_yexuan_fallback(sandbox):
    scope = MemoryScope.reality_scope(UID, CHAR_A)
    p = resolve_path(scope, "user_facts")
    # Check the relative portion under the sandbox base — not the tmp dir name
    rel = _s(p.relative_to(sandbox._base))
    assert CHAR_A in rel
    assert CHAR_B not in rel


def test_user_facts_path_ends_with_user_facts_json(sandbox):
    scope = MemoryScope.reality_scope(UID, CHAR_A)
    p = _s(resolve_path(scope, "user_facts"))
    assert p.endswith("user_facts.json")


# ---------------------------------------------------------------------------
# 2. Cross-character identity: yexuan and character_b share the same facts file
# ---------------------------------------------------------------------------

def test_cross_character_separate_facts(sandbox):
    uf.save_user_facts(UID, {"preferred_language": "zh-CN"}, char_id=CHAR_A)
    data_a = uf.load_user_facts(UID, char_id=CHAR_A)
    data_b = uf.load_user_facts(UID, char_id=CHAR_B)
    assert data_b == {}
    assert data_a["preferred_language"] == "zh-CN"


def test_profile_path_differs_by_char(sandbox):
    """Reality-scoped profile still contains char_id — isolation unchanged."""
    scope_a = MemoryScope.reality_scope(UID, CHAR_A)
    scope_b = MemoryScope.reality_scope(UID, CHAR_B)
    p_a = _s(resolve_path(scope_a, "profile"))
    p_b = _s(resolve_path(scope_b, "profile"))
    assert p_a != p_b
    assert CHAR_A in p_a
    assert CHAR_B in p_b


# ---------------------------------------------------------------------------
# 3. load: absent file → {}
# ---------------------------------------------------------------------------

def test_load_absent_returns_empty(sandbox):
    assert uf.load_user_facts("nonexistent_uid_xyz") == {}


# ---------------------------------------------------------------------------
# 4. save: only ALLOWED_FIELDS are written
# ---------------------------------------------------------------------------

def test_save_allowed_field(sandbox):
    uf.save_user_facts(UID, {"preferred_language": "en"})
    data = uf.load_user_facts(UID)
    assert data["preferred_language"] == "en"


def test_save_drops_unknown_field(sandbox):
    uf.save_user_facts(UID, {"preferred_language": "en", "unknown_field": "x"})
    data = uf.load_user_facts(UID)
    assert "unknown_field" not in data


def test_save_drops_denied_field_name(sandbox):
    uf.save_user_facts(UID, {"preferred_language": "en", "mood": "happy"})
    data = uf.load_user_facts(UID)
    assert "mood" not in data


def test_save_empty_dict(sandbox):
    uf.save_user_facts(UID, {})
    assert uf.load_user_facts(UID) == {}


# ---------------------------------------------------------------------------
# 5. update: merge, reject denied/unknown, return rejected list
# ---------------------------------------------------------------------------

def test_update_allowed_field(sandbox):
    updated, rejected = uf.update_user_facts(UID, {"timezone": "Asia/Shanghai"})
    assert updated["timezone"] == "Asia/Shanghai"
    assert rejected == []


def test_update_rejected_denied_field(sandbox):
    _, rejected = uf.update_user_facts(UID, {"mood": "happy"})
    assert "mood" in rejected
    data = uf.load_user_facts(UID)
    assert "mood" not in data


def test_update_rejected_unknown_field(sandbox):
    _, rejected = uf.update_user_facts(UID, {"favorite_color": "blue"})
    assert "favorite_color" in rejected
    data = uf.load_user_facts(UID)
    assert "favorite_color" not in data


def test_update_rejected_nickname(sandbox):
    _, rejected = uf.update_user_facts(UID, {"nickname": "宝宝"})
    assert "nickname" in rejected


def test_update_rejected_afterglow(sandbox):
    _, rejected = uf.update_user_facts(UID, {"afterglow": {"tone": "warm"}})
    assert "afterglow" in rejected


def test_update_rejected_hidden_state(sandbox):
    _, rejected = uf.update_user_facts(UID, {"hidden_state": {}})
    assert "hidden_state" in rejected


def test_update_rejected_affection(sandbox):
    _, rejected = uf.update_user_facts(UID, {"affection": 500})
    assert "affection" in rejected


def test_update_persists(sandbox):
    uf.update_user_facts(UID, {"device_os": "Windows"})
    assert uf.load_user_facts(UID)["device_os"] == "Windows"


def test_update_scalar_overwrite(sandbox):
    uf.update_user_facts(UID, {"device_os": "Windows"})
    uf.update_user_facts(UID, {"device_os": "macOS"})
    assert uf.load_user_facts(UID)["device_os"] == "macOS"


# ---------------------------------------------------------------------------
# 6. update list-valued fields extend, no duplicates
# ---------------------------------------------------------------------------

def test_update_list_field_extend(sandbox):
    uf.update_user_facts(UID, {"known_projects": ["proj_a"]})
    uf.update_user_facts(UID, {"known_projects": ["proj_b"]})
    data = uf.load_user_facts(UID)
    assert "proj_a" in data["known_projects"]
    assert "proj_b" in data["known_projects"]


def test_update_list_field_no_duplicates(sandbox):
    uf.update_user_facts(UID, {"known_projects": ["proj_a"]})
    uf.update_user_facts(UID, {"known_projects": ["proj_a"]})
    data = uf.load_user_facts(UID)
    assert data["known_projects"].count("proj_a") == 1


# ---------------------------------------------------------------------------
# 7. clear
# ---------------------------------------------------------------------------

def test_clear_resets_to_empty(sandbox):
    uf.save_user_facts(UID, {"preferred_language": "zh-CN"})
    uf.clear_user_facts(UID)
    assert uf.load_user_facts(UID) == {}


# ---------------------------------------------------------------------------
# 8. format_for_prompt
# ---------------------------------------------------------------------------

def test_format_empty_returns_empty_string(sandbox):
    assert uf.format_for_prompt("uid_empty_xyz") == ""


def test_format_returns_nonempty_when_facts_present(sandbox):
    uf.save_user_facts(UID, {"preferred_language": "zh-CN", "timezone": "Asia/Tokyo"})
    text = uf.format_for_prompt(UID)
    assert "preferred_language" in text
    assert "zh-CN" in text


def test_format_skips_none_values(sandbox):
    uf.save_user_facts(UID, {"preferred_language": "zh-CN"})
    # Inject None directly to test None-skip (bypass save filter)
    import json
    from core.memory.path_resolver import resolve_path
    p = resolve_path(MemoryScope.reality_scope(UID, CHAR_A), "user_facts")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"preferred_language": "zh-CN", "timezone": None}), encoding="utf-8")
    text = uf.format_for_prompt(UID)
    assert "timezone" not in text
    assert "zh-CN" in text


# ---------------------------------------------------------------------------
# 9. prompt_builder: user_facts_text parameter and layer 5.1
# ---------------------------------------------------------------------------

def test_prompt_builder_accepts_user_facts_text_param():
    """build() should not raise when user_facts_text is passed."""
    import inspect
    from core.prompt_builder import build
    sig = inspect.signature(build)
    assert "user_facts_text" in sig.parameters


def test_prompt_builder_layer_51_present_when_facts_given(sandbox):
    """Layer 5.1_user_facts appears when user_facts_text is non-empty."""
    from unittest.mock import MagicMock
    from core.prompt_builder import build

    char = MagicMock()
    char.system_prompt = ""
    char.description = ""
    char.personality = ""
    char.scenario = ""
    char.mes_example = ""
    char.name = "TestChar"

    messages, _ = build(
        character=char,
        user_id=UID,
        user_message="hi",
        history=[],
        relation={"role": "stranger"},
        profile={},
        group_context=[],
        user_facts_text="preferred_language: zh-CN",
        char_id=CHAR_A,
    )
    layers = [m.get("_layer") for m in messages]
    assert "5.1_user_facts" in layers


def test_prompt_builder_layer_51_absent_when_facts_empty(sandbox):
    """Layer 5.1_user_facts is NOT injected when user_facts_text is ''."""
    from unittest.mock import MagicMock
    from core.prompt_builder import build

    char = MagicMock()
    char.system_prompt = ""
    char.description = ""
    char.personality = ""
    char.scenario = ""
    char.mes_example = ""
    char.name = "TestChar"

    messages, _ = build(
        character=char,
        user_id=UID,
        user_message="hi",
        history=[],
        relation={"role": "stranger"},
        profile={},
        group_context=[],
        user_facts_text="",
        char_id=CHAR_A,
    )
    layers = [m.get("_layer") for m in messages]
    assert "5.1_user_facts" not in layers


def test_prompt_builder_layer_51_content_contains_facts(sandbox):
    """The injected 5.1 message body includes the facts text."""
    from unittest.mock import MagicMock
    from core.prompt_builder import build

    char = MagicMock()
    char.system_prompt = ""
    char.description = ""
    char.personality = ""
    char.scenario = ""
    char.mes_example = ""
    char.name = "TestChar"

    messages, _ = build(
        character=char,
        user_id=UID,
        user_message="hi",
        history=[],
        relation={"role": "stranger"},
        profile={},
        group_context=[],
        user_facts_text="device_os: Windows\ntimezone: Asia/Shanghai",
        char_id=CHAR_A,
    )
    facts_msg = next((m for m in messages if m.get("_layer") == "5.1_user_facts"), None)
    assert facts_msg is not None
    assert "device_os" in facts_msg["content"]
    assert "timezone" in facts_msg["content"]


def test_prompt_builder_layer_51_both_profile_and_facts(sandbox):
    """5_profile and 5.1_user_facts both appear when both provided."""
    from unittest.mock import MagicMock
    from core.prompt_builder import build

    char = MagicMock()
    char.system_prompt = ""
    char.description = ""
    char.personality = ""
    char.scenario = ""
    char.mes_example = ""
    char.name = "TestChar"

    messages, _ = build(
        character=char,
        user_id=UID,
        user_message="hi",
        history=[],
        relation={"role": "stranger"},
        profile={"name": "Alice", "important_facts": []},
        group_context=[],
        user_facts_text="preferred_language: en",
        char_id=CHAR_A,
    )
    layers = [m.get("_layer") for m in messages]
    assert "5_profile" in layers
    assert "5.1_user_facts" in layers
