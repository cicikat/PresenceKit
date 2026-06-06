"""
tests/test_user_facts_smoke.py
===============================
Smoke test: verify user_facts global layer is injected correctly across
all characters, and that scoped profile/identity remain isolated.

Run with:
    pytest tests/test_user_facts_smoke.py -v
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import sys
import unittest.mock as mock

import pytest

ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))


# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

OWNER_UID = "1043484516"
CHARACTERS = ["yexuan", "hongcha", "yexuanJ-5412"]

TEST_FACTS = {
    "preferred_language": "Chinese",
    "timezone": "Asia/Shanghai",
    "project_paths": ["/d/ai/qq-st-bot", "/d/ai/emerald"],
    "known_projects": ["qq-st-bot", "emerald-bot"],
    "tool_usage_preferences": "VSCode, Python, pytest",
}


# ─────────────────────────────────────────────────────────────────────────────
# Test helpers
# ─────────────────────────────────────────────────────────────────────────────

def _get_user_facts_path() -> pathlib.Path:
    from core.memory.scope import MemoryScope
    from core.memory.path_resolver import resolve_path
    scope = MemoryScope.global_scope(OWNER_UID)
    return resolve_path(scope, "user_facts")


def _write_facts(facts: dict) -> pathlib.Path:
    p = _get_user_facts_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(facts, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def _delete_facts():
    p = _get_user_facts_path()
    if p.exists():
        p.unlink()


def _extract_layers(messages: list[dict]) -> dict[str, str]:
    """Return {layer_name: content} for all messages that have a _layer field."""
    return {
        m["_layer"]: m.get("content", "")
        for m in messages
        if "_layer" in m
    }


def _apply_build_stubs(monkeypatch):
    """Stub all filesystem-touching helpers so build() can run in tests."""
    import core.prompt_builder as _pb
    import core.presence as _pres
    import core.author_note_rotator as _anr
    import core.config_loader as _cl

    monkeypatch.setattr(_pb, "_load_jailbreak", lambda layer=None: "")
    monkeypatch.setattr(_pb, "_load_style_hint", lambda: "")
    monkeypatch.setattr(_pb, "_load_activity_snapshot", lambda: "")
    monkeypatch.setattr(_pb, "_format_afterglow_soft_hint", lambda uid, char_id="yexuan": "")
    monkeypatch.setattr(_pres, "get_last_seen_text", lambda uid: "")
    monkeypatch.setattr(_anr, "get_current_note", lambda paths=None: "")
    monkeypatch.setattr(_cl, "get_config", lambda: {"chat": {}})


def _make_character(name: str = "叶瑄"):
    from core.character_loader import Character
    return Character(name=name)


# ─────────────────────────────────────────────────────────────────────────────
# 1. format_for_prompt basic
# ─────────────────────────────────────────────────────────────────────────────

class TestFormatForPrompt:
    def test_empty_when_no_file(self):
        _delete_facts()
        from core.memory.user_facts import format_for_prompt
        result = format_for_prompt(OWNER_UID)
        assert result == ""

    def test_renders_all_fields(self):
        _write_facts(TEST_FACTS)
        from core.memory.user_facts import format_for_prompt
        result = format_for_prompt(OWNER_UID)
        assert "preferred_language" in result
        assert "Chinese" in result
        assert "timezone" in result
        assert "Asia/Shanghai" in result
        assert "qq-st-bot" in result
        assert "VSCode" in result

    def test_list_fields_joined_with_comma(self):
        _write_facts({"known_projects": ["proj-a", "proj-b", "proj-c"]})
        from core.memory.user_facts import format_for_prompt
        result = format_for_prompt(OWNER_UID)
        assert "proj-a, proj-b, proj-c" in result

    def test_empty_dict_returns_empty_string(self):
        _write_facts({})
        from core.memory.user_facts import format_for_prompt
        result = format_for_prompt(OWNER_UID)
        assert result == ""


# ─────────────────────────────────────────────────────────────────────────────
# 2. save / update field guard
# ─────────────────────────────────────────────────────────────────────────────

class TestFieldGuard:
    def test_denied_fields_rejected_by_update(self):
        _write_facts({})
        from core.memory.user_facts import update_user_facts
        _, rejected = update_user_facts(OWNER_UID, {
            "nickname": "honey",
            "mood": "happy",
            "name": "Alice",
        })
        assert "nickname" in rejected
        assert "mood" in rejected
        assert "name" in rejected

    def test_unknown_field_rejected(self):
        _write_facts({})
        from core.memory.user_facts import update_user_facts
        _, rejected = update_user_facts(OWNER_UID, {"custom_field": "x"})
        assert "custom_field" in rejected

    def test_save_strips_denied_fields(self):
        from core.memory.user_facts import save_user_facts, load_user_facts
        save_user_facts(OWNER_UID, {
            "preferred_language": "Chinese",
            "nickname": "honey",       # denied
            "mood": "happy",           # denied
        })
        loaded = load_user_facts(OWNER_UID)
        assert "preferred_language" in loaded
        assert "nickname" not in loaded
        assert "mood" not in loaded

    def test_profile_identity_fields_denied(self):
        """Fields belonging in scoped profile/identity must be rejected."""
        from core.memory.user_facts import update_user_facts
        profile_like = {
            "name": "Alice",
            "location": "Shanghai",
            "pets": ["cat"],
            "interests": ["coding"],
            "occupation": "engineer",
            "important_facts": "something",
        }
        _, rejected = update_user_facts(OWNER_UID, profile_like)
        for k in profile_like:
            assert k in rejected, f"{k} should have been rejected"


# ─────────────────────────────────────────────────────────────────────────────
# 3. Prompt layer injection: 5.1_user_facts in build() output
# ─────────────────────────────────────────────────────────────────────────────

CHAR_NAMES = {
    "yexuan": "叶瑄",
    "hongcha": "红茶",
    "yexuanJ-5412": "叶瑄J",
}


class TestPromptLayerInjection:
    """
    Calls prompt_builder.build() with a minimal context.
    Checks that 5.1_user_facts appears when user_facts_text is set,
    and is absent when user_facts_text is empty.
    """

    @pytest.fixture(autouse=True)
    def setup_facts(self):
        _write_facts(TEST_FACTS)
        yield
        _delete_facts()

    def _call_build(self, char_id: str, user_facts_text: str, monkeypatch) -> list[dict]:
        _apply_build_stubs(monkeypatch)
        import core.prompt_builder as _pb
        char = _make_character(CHAR_NAMES.get(char_id, char_id))
        messages, _ = _pb.build(
            character=char,
            user_id=OWNER_UID,
            user_message="你好",
            history=[],
            relation={},
            profile={},
            group_context=[],
            user_identity_text=f"[identity for {char_id}]",
            user_facts_text=user_facts_text,
            char_id=char_id,
        )
        return messages

    @pytest.mark.parametrize("char_id", CHARACTERS)
    def test_user_facts_layer_present_when_nonempty(self, char_id, monkeypatch):
        from core.memory.user_facts import format_for_prompt
        uf_text = format_for_prompt(OWNER_UID)
        assert uf_text, "setup: user_facts should be non-empty"

        messages = self._call_build(char_id, uf_text, monkeypatch)
        layers = _extract_layers(messages)

        assert "5.1_user_facts" in layers, (
            f"[{char_id}] 5.1_user_facts layer missing from prompt. "
            f"Present layers: {list(layers.keys())}"
        )
        content = layers["5.1_user_facts"]
        assert "跨角色通用" in content, "header must say cross-character"
        assert "非角色记忆" in content, "header must say non-character memory"
        assert "preferred_language" in content or "Chinese" in content

    @pytest.mark.parametrize("char_id", CHARACTERS)
    def test_user_facts_layer_absent_when_empty(self, char_id, monkeypatch):
        messages = self._call_build(char_id, "", monkeypatch)
        layers = _extract_layers(messages)
        assert "5.1_user_facts" not in layers, (
            f"[{char_id}] 5.1_user_facts layer should be absent when user_facts_text=''"
        )

    @pytest.mark.parametrize("char_id", CHARACTERS)
    def test_same_facts_content_across_all_chars(self, char_id, monkeypatch):
        from core.memory.user_facts import format_for_prompt
        uf_text = format_for_prompt(OWNER_UID)
        messages = self._call_build(char_id, uf_text, monkeypatch)
        layers = _extract_layers(messages)
        content = layers.get("5.1_user_facts", "")
        assert "Asia/Shanghai" in content, f"[{char_id}] timezone missing from user_facts layer"
        assert "qq-st-bot" in content, f"[{char_id}] known_projects missing from user_facts layer"


# ─────────────────────────────────────────────────────────────────────────────
# 4. Scoped isolation: identity text is distinct per char; user_facts is same
# ─────────────────────────────────────────────────────────────────────────────

class TestScopedIsolation:
    @pytest.fixture(autouse=True)
    def setup_facts(self):
        _write_facts(TEST_FACTS)
        yield
        _delete_facts()

    def _call_build_with_identity(self, char_id: str, identity: str, monkeypatch) -> list[dict]:
        _apply_build_stubs(monkeypatch)
        import core.prompt_builder as _pb
        char = _make_character(CHAR_NAMES.get(char_id, char_id))
        messages, _ = _pb.build(
            character=char,
            user_id=OWNER_UID,
            user_message="test",
            history=[],
            relation={},
            profile={},
            group_context=[],
            user_identity_text=identity,
            user_facts_text="preferred_language: Chinese\ntimezone: Asia/Shanghai",
            char_id=char_id,
        )
        return messages

    def test_identity_text_appears_per_char(self, monkeypatch):
        for char_id in CHARACTERS:
            identity = f"[identity-for-{char_id}]"
            messages = self._call_build_with_identity(char_id, identity, monkeypatch)
            all_content = " ".join(m.get("content", "") for m in messages)
            assert identity in all_content, (
                f"[{char_id}] own identity text not found in prompt"
            )

    def test_user_facts_same_content_all_chars(self, monkeypatch):
        """All chars receive identical user_facts content."""
        facts_contents = {}
        for char_id in CHARACTERS:
            messages = self._call_build_with_identity(
                char_id, f"[identity-for-{char_id}]", monkeypatch
            )
            layers = _extract_layers(messages)
            facts_contents[char_id] = layers.get("5.1_user_facts", "")

        contents = list(facts_contents.values())
        assert all(c == contents[0] for c in contents), (
            f"user_facts content differs across chars: {facts_contents}"
        )

    def test_profile_fields_not_in_user_facts_layer(self, monkeypatch):
        """user_facts layer must not bleed in profile/identity denied fields."""
        from core.memory.user_facts import save_user_facts
        # save_user_facts strips denied fields before writing
        save_user_facts(OWNER_UID, {
            "preferred_language": "Chinese",
            "nickname": "honey",    # denied — stripped at save
            "intimacy": "high",     # denied — stripped at save
        })
        _apply_build_stubs(monkeypatch)
        import core.prompt_builder as _pb
        char = _make_character()
        messages, _ = _pb.build(
            character=char,
            user_id=OWNER_UID,
            user_message="test",
            history=[],
            relation={},
            profile={},
            group_context=[],
            user_facts_text="preferred_language: Chinese",
            char_id="yexuan",
        )
        layers = _extract_layers(messages)
        facts_layer = layers.get("5.1_user_facts", "")
        assert "nickname" not in facts_layer
        assert "intimacy" not in facts_layer
        assert "preferred_language" in facts_layer


# ─────────────────────────────────────────────────────────────────────────────
# 5. No-facts scenario: build() does not crash and layer is absent
# ─────────────────────────────────────────────────────────────────────────────

class TestNoFactsScenario:
    def test_no_crash_when_no_user_facts_file(self, monkeypatch):
        _delete_facts()
        from core.memory.user_facts import format_for_prompt
        uf_text = format_for_prompt(OWNER_UID)
        assert uf_text == ""

        _apply_build_stubs(monkeypatch)
        import core.prompt_builder as _pb
        char = _make_character()
        messages, _ = _pb.build(
            character=char,
            user_id=OWNER_UID,
            user_message="test",
            history=[],
            relation={},
            profile={},
            group_context=[],
            user_facts_text=uf_text,
            char_id="yexuan",
        )
        layers = _extract_layers(messages)
        assert "5.1_user_facts" not in layers

    def test_no_yexuan_fallback_in_user_facts(self):
        """user_facts load must not fall back to yexuan scope."""
        _delete_facts()
        from core.memory.user_facts import load_user_facts
        result = load_user_facts("nonexistent_uid_9999")
        assert result == {}, "should return empty dict for unknown uid, no fallback"


# ─────────────────────────────────────────────────────────────────────────────
# 6. Pipeline fetch_context wires user_facts_text (integration)
#
# Strategy: mock _current_reality_scope + all async IO, then call fetch_context.
# This verifies user_facts.format_for_prompt(uid) is called and placed in context.
# ─────────────────────────────────────────────────────────────────────────────

class TestPipelineFetchContext:
    @pytest.fixture(autouse=True)
    def setup_facts(self):
        _write_facts(TEST_FACTS)
        yield
        _delete_facts()

    def _make_pipeline(self, char_id: str):
        from core.pipeline import Pipeline
        from core.character_loader import Character
        from core.memory.scope import MemoryScope

        pipeline = Pipeline.__new__(Pipeline)
        pipeline.character = Character(name=CHAR_NAMES.get(char_id, char_id))
        pipeline._active_character_id = char_id
        pipeline.author_note_extra = ""
        pipeline._last_channel = None
        pipeline.lore_engine = mock.MagicMock()
        pipeline.lore_engine.match.return_value = []

        # Stub _current_reality_scope to bypass active_prompt_assets.json
        scope = MemoryScope.reality_scope(OWNER_UID, char_id)
        pipeline._current_reality_scope = mock.MagicMock(return_value=scope)

        return pipeline

    def _run_fetch_context(self, pipeline, uid: str, char_id: str) -> dict:
        async def _go():
            with (
                mock.patch("core.memory.short_term.load_for_prompt", return_value=[]),
                mock.patch("core.memory.user_profile.load", return_value={}),
                mock.patch("core.memory.mid_term.format_for_prompt", return_value=""),
                mock.patch("core.memory.group_context.get_recent", return_value=[]),
                mock.patch("core.user_relation.get_relation", return_value={}),
                mock.patch("core.memory.episodic_memory.retrieve", return_value=[]),
                mock.patch("core.memory.episodic_memory.format_for_prompt", return_value=""),
                mock.patch("core.memory.episodic_memory.retrieve_fallback", return_value=[]),
                mock.patch("core.memory.mood_state.get_current", return_value="calm"),
                mock.patch("core.memory.event_log.search", new=mock.AsyncMock(return_value="")),
                mock.patch("core.memory.user_identity.format_for_prompt",
                           new=mock.AsyncMock(return_value=f"[identity-{char_id}]")),
                mock.patch("core.tools.reminder.get_reminders", return_value=[]),
                mock.patch("core.memory.diary_context.load", return_value=""),
                mock.patch("core.dream.impression_loader.load_impression_text", return_value=""),
            ):
                return await pipeline.fetch_context(
                    user_id=uid,
                    content="hello",
                    group_id=None,
                )
        return asyncio.run(_go())

    def test_fetch_context_includes_user_facts_text(self):
        pipeline = self._make_pipeline("yexuan")
        ctx = self._run_fetch_context(pipeline, OWNER_UID, "yexuan")

        assert "user_facts_text" in ctx, "fetch_context must return user_facts_text key"
        uf_text = ctx["user_facts_text"]
        assert "preferred_language" in uf_text, (
            f"user_facts not loaded in fetch_context, got: {uf_text!r}"
        )
        assert "Asia/Shanghai" in uf_text

    def test_fetch_context_user_facts_uid_only_no_char_bleed(self):
        """user_facts_text is the same regardless of char_id."""
        results = {}
        for char_id in CHARACTERS:
            pipeline = self._make_pipeline(char_id)
            ctx = self._run_fetch_context(pipeline, OWNER_UID, char_id)
            results[char_id] = ctx.get("user_facts_text", "")

        contents = list(results.values())
        assert all(c == contents[0] for c in contents), (
            f"user_facts_text differs across chars in fetch_context: {results}"
        )
        assert contents[0] != "", "user_facts_text should be non-empty"

    def test_fetch_context_user_identity_is_char_scoped(self):
        """user_identity_text differs per char (scoped); user_facts_text does not."""
        identity_results = {}
        facts_results = {}
        for char_id in CHARACTERS:
            pipeline = self._make_pipeline(char_id)

            async def _go(cid=char_id):
                with (
                    mock.patch("core.memory.short_term.load_for_prompt", return_value=[]),
                    mock.patch("core.memory.user_profile.load", return_value={}),
                    mock.patch("core.memory.mid_term.format_for_prompt", return_value=""),
                    mock.patch("core.memory.group_context.get_recent", return_value=[]),
                    mock.patch("core.user_relation.get_relation", return_value={}),
                    mock.patch("core.memory.episodic_memory.retrieve", return_value=[]),
                    mock.patch("core.memory.episodic_memory.format_for_prompt", return_value=""),
                    mock.patch("core.memory.episodic_memory.retrieve_fallback", return_value=[]),
                    mock.patch("core.memory.mood_state.get_current", return_value="calm"),
                    mock.patch("core.memory.event_log.search", new=mock.AsyncMock(return_value="")),
                    mock.patch("core.memory.user_identity.format_for_prompt",
                               new=mock.AsyncMock(return_value=f"[scoped-identity-{cid}]")),
                    mock.patch("core.tools.reminder.get_reminders", return_value=[]),
                    mock.patch("core.memory.diary_context.load", return_value=""),
                    mock.patch("core.dream.impression_loader.load_impression_text", return_value=""),
                ):
                    return await pipeline.fetch_context(
                        user_id=OWNER_UID,
                        content="hello",
                        group_id=None,
                    )

            ctx = asyncio.run(_go())
            identity_results[char_id] = ctx.get("user_identity_text", "")
            facts_results[char_id] = ctx.get("user_facts_text", "")

        # user_identity_text should differ per char (our mock returns char-specific text)
        identity_values = list(identity_results.values())
        assert len(set(identity_values)) == len(CHARACTERS), (
            f"Expected distinct identity_text per char, got: {identity_results}"
        )

        # user_facts_text should be identical across all chars
        facts_values = list(facts_results.values())
        assert all(v == facts_values[0] for v in facts_values), (
            f"user_facts_text should be same across chars, got: {facts_results}"
        )
