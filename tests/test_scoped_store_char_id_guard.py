"""
tests/test_scoped_store_char_id_guard.py — T-14A

Verifies that every migrated scoped store rejects invalid char_id values
(None, "", non-str) with a ValueError before any path construction or
MemoryScope creation, and that the default char_id=TEST_CHAR_ID still works.

Covers:
1.  require_character_id helper — None / "" / int / valid str
2.  user_profile — None / "" / int fail-loud; default works
3.  user_identity — None / "" / int fail-loud; default works
4.  mid_term — None / "" / int fail-loud; default works
5.  episodic_memory — None / "" / int fail-loud; default works
6.  short_term — None / "" / int fail-loud; default works
7.  event_log get_recent_days — None / "" fail-loud
    event_log append — None / "" returns False (caught by except), no yexuan fallback
8.  fixation_pipeline — None / "" / int fail-loud; default works
9.  user_hidden_state_store — None / "" / int fail-loud; default works
10. No new fallback to yexuan introduced (confirmed via ValueError not bypassed)
"""
from __future__ import annotations
from tests.fixtures.public_assets import TEST_CHAR_ID

import asyncio
import pytest

_UID = "guard_test_u1"


# ═══════════════════════════════════════════════════════════════════════════════
# 1. require_character_id helper
# ═══════════════════════════════════════════════════════════════════════════════

class TestRequireCharacterId:
    def test_valid_string_returned_unchanged(self):
        from core.memory.scope import require_character_id
        assert require_character_id(TEST_CHAR_ID) == TEST_CHAR_ID
        assert require_character_id("character_b") == "character_b"

    def test_none_raises(self):
        from core.memory.scope import require_character_id
        with pytest.raises(ValueError, match="character_id"):
            require_character_id(None)

    def test_empty_string_raises(self):
        from core.memory.scope import require_character_id
        with pytest.raises(ValueError, match="character_id"):
            require_character_id("")

    def test_int_raises(self):
        from core.memory.scope import require_character_id
        with pytest.raises(ValueError, match="character_id"):
            require_character_id(123)

    def test_list_raises(self):
        from core.memory.scope import require_character_id
        with pytest.raises(ValueError, match="character_id"):
            require_character_id([TEST_CHAR_ID])


# ═══════════════════════════════════════════════════════════════════════════════
# 2. user_profile
# ═══════════════════════════════════════════════════════════════════════════════

class TestUserProfileGuard:
    def test_load_none_raises(self, sandbox):
        import core.memory.user_profile as up
        with pytest.raises(ValueError, match="character_id"):
            up.load(_UID, char_id=None)

    def test_load_empty_raises(self, sandbox):
        import core.memory.user_profile as up
        with pytest.raises(ValueError, match="character_id"):
            up.load(_UID, char_id="")

    def test_load_int_raises(self, sandbox):
        import core.memory.user_profile as up
        with pytest.raises(ValueError, match="character_id"):
            up.load(_UID, char_id=123)

    def test_save_none_raises(self, sandbox):
        import core.memory.user_profile as up
        with pytest.raises(ValueError, match="character_id"):
            up.save(_UID, {}, char_id=None)

    def test_save_empty_raises(self, sandbox):
        import core.memory.user_profile as up
        with pytest.raises(ValueError, match="character_id"):
            up.save(_UID, {}, char_id="")

    def test_default_char_id_works(self, sandbox):
        import core.memory.user_profile as up
        profile = up.load(_UID)
        assert isinstance(profile, dict)


# ═══════════════════════════════════════════════════════════════════════════════
# 3. user_identity
# ═══════════════════════════════════════════════════════════════════════════════

class TestUserIdentityGuard:
    def test_load_none_raises(self, sandbox):
        import core.memory.user_identity as ui
        with pytest.raises(ValueError, match="character_id"):
            asyncio.run(ui.load(_UID, char_id=None))

    def test_load_empty_raises(self, sandbox):
        import core.memory.user_identity as ui
        with pytest.raises(ValueError, match="character_id"):
            asyncio.run(ui.load(_UID, char_id=""))

    def test_load_int_raises(self, sandbox):
        import core.memory.user_identity as ui
        with pytest.raises(ValueError, match="character_id"):
            asyncio.run(ui.load(_UID, char_id=42))

    def test_save_none_raises(self, sandbox):
        import core.memory.user_identity as ui
        with pytest.raises(ValueError, match="character_id"):
            asyncio.run(ui.save(_UID, {}, char_id=None))

    def test_save_empty_raises(self, sandbox):
        import core.memory.user_identity as ui
        with pytest.raises(ValueError, match="character_id"):
            asyncio.run(ui.save(_UID, {}, char_id=""))

    def test_default_char_id_works(self, sandbox):
        import core.memory.user_identity as ui
        result = asyncio.run(ui.load(_UID))
        assert isinstance(result, dict)


# ═══════════════════════════════════════════════════════════════════════════════
# 4. mid_term
# ═══════════════════════════════════════════════════════════════════════════════

class TestMidTermGuard:
    def test_load_none_raises(self, sandbox):
        import core.memory.mid_term as mt
        with pytest.raises(ValueError, match="character_id"):
            mt.load(_UID, char_id=None)

    def test_load_empty_raises(self, sandbox):
        import core.memory.mid_term as mt
        with pytest.raises(ValueError, match="character_id"):
            mt.load(_UID, char_id="")

    def test_load_int_raises(self, sandbox):
        import core.memory.mid_term as mt
        with pytest.raises(ValueError, match="character_id"):
            mt.load(_UID, char_id=7)

    def test_append_none_raises(self, sandbox):
        import core.memory.mid_term as mt
        with pytest.raises(ValueError, match="character_id"):
            mt.append(_UID, "some summary", char_id=None)

    def test_append_empty_raises(self, sandbox):
        import core.memory.mid_term as mt
        with pytest.raises(ValueError, match="character_id"):
            mt.append(_UID, "some summary", char_id="")

    def test_default_char_id_works(self, sandbox):
        import core.memory.mid_term as mt
        result = mt.load(_UID)
        assert result == []


# ═══════════════════════════════════════════════════════════════════════════════
# 5. episodic_memory
# ═══════════════════════════════════════════════════════════════════════════════

class TestEpisodicMemoryGuard:
    def test_load_unconsolidated_none_raises(self, sandbox):
        import core.memory.episodic_memory as em
        with pytest.raises(ValueError, match="character_id"):
            em.load_unconsolidated(_UID, char_id=None)

    def test_load_unconsolidated_empty_raises(self, sandbox):
        import core.memory.episodic_memory as em
        with pytest.raises(ValueError, match="character_id"):
            em.load_unconsolidated(_UID, char_id="")

    def test_load_unconsolidated_int_raises(self, sandbox):
        import core.memory.episodic_memory as em
        with pytest.raises(ValueError, match="character_id"):
            em.load_unconsolidated(_UID, char_id=5)

    def test_mem_read_file_none_raises(self, sandbox):
        from core.memory.episodic_memory import _mem_read_file
        with pytest.raises(ValueError, match="character_id"):
            _mem_read_file(_UID, char_id=None)

    def test_index_read_file_empty_raises(self, sandbox):
        from core.memory.episodic_memory import _index_read_file
        with pytest.raises(ValueError, match="character_id"):
            _index_read_file(_UID, char_id="")

    def test_default_char_id_works(self, sandbox):
        import core.memory.episodic_memory as em
        result = em.load_unconsolidated(_UID)
        assert result == []


# ═══════════════════════════════════════════════════════════════════════════════
# 6. short_term
# ═══════════════════════════════════════════════════════════════════════════════

class TestShortTermGuard:
    def test_load_none_raises(self, sandbox):
        import core.memory.short_term as st
        with pytest.raises(ValueError, match="character_id"):
            st.load(_UID, char_id=None)

    def test_load_empty_raises(self, sandbox):
        import core.memory.short_term as st
        with pytest.raises(ValueError, match="character_id"):
            st.load(_UID, char_id="")

    def test_load_int_raises(self, sandbox):
        import core.memory.short_term as st
        with pytest.raises(ValueError, match="character_id"):
            st.load(_UID, char_id=0)

    def test_clear_none_raises(self, sandbox):
        import core.memory.short_term as st
        with pytest.raises(ValueError, match="character_id"):
            asyncio.run(st.clear(_UID, char_id=None))

    def test_clear_empty_raises(self, sandbox):
        import core.memory.short_term as st
        with pytest.raises(ValueError, match="character_id"):
            asyncio.run(st.clear(_UID, char_id=""))

    def test_default_char_id_works(self, sandbox):
        import core.memory.short_term as st
        result = st.load(_UID)
        assert result == []


# ═══════════════════════════════════════════════════════════════════════════════
# 7. event_log
# ═══════════════════════════════════════════════════════════════════════════════

class TestEventLogGuard:
    def test_get_recent_days_none_raises(self, sandbox):
        import core.memory.event_log as el
        with pytest.raises(ValueError, match="character_id"):
            el.get_recent_days(_UID, char_id=None)

    def test_get_recent_days_empty_raises(self, sandbox):
        import core.memory.event_log as el
        with pytest.raises(ValueError, match="character_id"):
            el.get_recent_days(_UID, char_id="")

    def test_get_recent_days_int_raises(self, sandbox):
        import core.memory.event_log as el
        with pytest.raises(ValueError, match="character_id"):
            el.get_recent_days(_UID, char_id=1)

    def test_append_none_returns_false_not_write_yexuan(self, sandbox):
        """append() catches all exceptions and returns False; must not write to yexuan path."""
        import core.memory.event_log as el
        from core.memory.scope import MemoryScope
        from core.memory.path_resolver import resolve_path

        result = el.append(_UID, "user", "hello", char_id=None)
        assert result is False

        # Confirm yexuan bucket was not written
        yexuan_dir = resolve_path(MemoryScope.reality_scope(_UID, TEST_CHAR_ID), "event_log")
        assert not yexuan_dir.exists(), f'append must not fall back to writing {TEST_CHAR_ID} bucket'

    def test_append_empty_returns_false_not_write_yexuan(self, sandbox):
        import core.memory.event_log as el
        from core.memory.scope import MemoryScope
        from core.memory.path_resolver import resolve_path

        result = el.append(_UID, "user", "hello", char_id="")
        assert result is False

        yexuan_dir = resolve_path(MemoryScope.reality_scope(_UID, TEST_CHAR_ID), "event_log")
        assert not yexuan_dir.exists()

    def test_default_char_id_works(self, sandbox):
        import core.memory.event_log as el
        result = el.get_recent_days(_UID)
        assert isinstance(result, str)


# ═══════════════════════════════════════════════════════════════════════════════
# 8. fixation_pipeline
# ═══════════════════════════════════════════════════════════════════════════════

class TestFixationPipelineGuard:
    def test_load_fixation_state_none_raises(self, sandbox):
        from core.memory.fixation_pipeline import _load_fixation_state
        with pytest.raises(ValueError, match="character_id"):
            _load_fixation_state(_UID, char_id=None)

    def test_load_fixation_state_empty_raises(self, sandbox):
        from core.memory.fixation_pipeline import _load_fixation_state
        with pytest.raises(ValueError, match="character_id"):
            _load_fixation_state(_UID, char_id="")

    def test_load_fixation_state_int_raises(self, sandbox):
        from core.memory.fixation_pipeline import _load_fixation_state
        with pytest.raises(ValueError, match="character_id"):
            _load_fixation_state(_UID, char_id=9)

    def test_state_read_file_none_raises(self, sandbox):
        from core.memory.fixation_pipeline import _state_read_file
        with pytest.raises(ValueError, match="character_id"):
            _state_read_file(_UID, char_id=None)

    def test_state_write_file_empty_raises(self, sandbox):
        from core.memory.fixation_pipeline import _state_write_file
        with pytest.raises(ValueError, match="character_id"):
            _state_write_file(_UID, char_id="")

    def test_default_char_id_works(self, sandbox):
        from core.memory.fixation_pipeline import _load_fixation_state
        result = _load_fixation_state(_UID)
        assert isinstance(result, dict)
        assert "last_consolidated_at" in result


# ═══════════════════════════════════════════════════════════════════════════════
# 9. user_hidden_state_store
# ═══════════════════════════════════════════════════════════════════════════════

class TestHiddenStateStoreGuard:
    def test_load_hidden_state_none_raises(self, sandbox):
        from core.memory.user_hidden_state_store import load_hidden_state
        with pytest.raises(ValueError, match="character_id"):
            load_hidden_state(_UID, char_id=None)

    def test_load_hidden_state_empty_raises(self, sandbox):
        from core.memory.user_hidden_state_store import load_hidden_state
        with pytest.raises(ValueError, match="character_id"):
            load_hidden_state(_UID, char_id="")

    def test_load_hidden_state_int_raises(self, sandbox):
        from core.memory.user_hidden_state_store import load_hidden_state
        with pytest.raises(ValueError, match="character_id"):
            load_hidden_state(_UID, char_id=0)

    def test_save_hidden_state_none_raises(self, sandbox):
        from core.memory.user_hidden_state import default_hidden_state
        from core.memory.user_hidden_state_store import save_hidden_state
        with pytest.raises(ValueError, match="character_id"):
            save_hidden_state(_UID, default_hidden_state(), char_id=None)

    def test_save_hidden_state_empty_raises(self, sandbox):
        from core.memory.user_hidden_state import default_hidden_state
        from core.memory.user_hidden_state_store import save_hidden_state
        with pytest.raises(ValueError, match="character_id"):
            save_hidden_state(_UID, default_hidden_state(), char_id="")

    def test_load_afterglow_raw_none_raises(self, sandbox):
        from core.memory.user_hidden_state_store import _load_afterglow_raw
        with pytest.raises(ValueError, match="character_id"):
            _load_afterglow_raw(_UID, char_id=None)

    def test_load_afterglow_raw_empty_raises(self, sandbox):
        from core.memory.user_hidden_state_store import _load_afterglow_raw
        with pytest.raises(ValueError, match="character_id"):
            _load_afterglow_raw(_UID, char_id="")

    def test_default_char_id_works(self, sandbox):
        from core.memory.user_hidden_state import default_hidden_state
        from core.memory.user_hidden_state_store import load_hidden_state, save_hidden_state
        state = default_hidden_state()
        ok = save_hidden_state(_UID, state)
        assert ok is True
        loaded = load_hidden_state(_UID)
        assert loaded is not None


# ═══════════════════════════════════════════════════════════════════════════════
# 10. No new yexuan fallback — cross-store smoke check
# ═══════════════════════════════════════════════════════════════════════════════

class TestNoYexuanFallback:
    """Passing an obviously wrong char_id must never silently write to a yexuan path."""

    def test_user_profile_no_yexuan_fallback(self, sandbox):
        import core.memory.user_profile as up
        from core.memory.scope import MemoryScope
        from core.memory.path_resolver import resolve_path

        with pytest.raises(ValueError):
            up.save(_UID, {"name": "injected"}, char_id=None)

        yexuan_path = resolve_path(MemoryScope.reality_scope(_UID, TEST_CHAR_ID), "profile")
        assert not yexuan_path.exists()

    def test_mid_term_no_yexuan_fallback(self, sandbox):
        import core.memory.mid_term as mt
        from core.memory.scope import MemoryScope
        from core.memory.path_resolver import resolve_path

        with pytest.raises(ValueError):
            mt.append(_UID, "injected summary", char_id=None)

        yexuan_path = resolve_path(MemoryScope.reality_scope(_UID, TEST_CHAR_ID), "mid_term")
        assert not yexuan_path.exists()

    def test_short_term_no_yexuan_fallback(self, sandbox):
        import core.memory.short_term as st
        from core.memory.scope import MemoryScope
        from core.memory.path_resolver import resolve_path

        with pytest.raises(ValueError):
            st.load(_UID, char_id=None)

        yexuan_path = resolve_path(MemoryScope.reality_scope(_UID, TEST_CHAR_ID), "history")
        assert not yexuan_path.exists()
