"""
tests/test_user_hidden_state_store.py
====================================
Phase 1.5 — UserHiddenState persistence tests

Coverage:
  1. round_trip              — save → load preserves all Phase 1 writable fields
  2. missing_file            — load returns default when file is absent
  3. corrupt_json            — load returns default on invalid JSON; logs warning
  4. schema_version_missing  — from_dict deserializes leniently; logs warning
  5. schema_version_mismatch — future version raises; load returns read-only; corrupt backup
"""
from __future__ import annotations

import json
import logging

import pytest

from core.memory.user_hidden_state import (
    HiddenStateVersionError,
    SCALAR_CENTER,
    UpdateSource,
    default_hidden_state,
    discharge_touch_deficit,
    from_dict,
    nudge_current_sensitivity,
    to_dict,
)
from core.memory.user_hidden_state_store import (
    HIDDEN_STATE_FILENAME,
    load_hidden_state,
    save_hidden_state,
)

NOW = "2026-06-02T00:00:00Z"
TEST_UID = "user_999"


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Round-trip
# ═══════════════════════════════════════════════════════════════════════════════

class TestRoundTrip:
    def test_default_state_round_trips(self, sandbox):
        state = default_hidden_state()
        ok = save_hidden_state(TEST_UID, state)
        assert ok

        loaded = load_hidden_state(TEST_UID)
        assert loaded.schema_version == state.schema_version
        assert loaded.sensitivity.current.value == state.sensitivity.current.value
        assert loaded.sensitivity.baseline.value == state.sensitivity.baseline.value
        assert loaded.touch_need.deficit.value == state.touch_need.deficit.value
        assert loaded.touch_need.baseline.value == state.touch_need.baseline.value
        assert loaded.embodied_ease.value == state.embodied_ease.value
        assert loaded.last_decay_tick == state.last_decay_tick

    def test_modified_phase1_fields_round_trip(self, sandbox):
        """Phase-1 writable fields survive a save→load cycle."""
        state = default_hidden_state()
        state.touch_need.deficit.value = 30.0
        state = nudge_current_sensitivity(state, 6.0, UpdateSource.REALITY_BEHAVIOR, NOW)
        state = discharge_touch_deficit(state, 5.0, UpdateSource.REALITY_BEHAVIOR, NOW)
        state.last_decay_tick = NOW

        save_hidden_state(TEST_UID, state)
        loaded = load_hidden_state(TEST_UID)

        assert loaded.sensitivity.current.value == pytest.approx(SCALAR_CENTER + 6.0)
        assert loaded.touch_need.deficit.value == pytest.approx(25.0)   # 30 - 5
        assert loaded.sensitivity.current.last_update_source == UpdateSource.REALITY_BEHAVIOR
        assert loaded.touch_need.deficit.last_update_source == UpdateSource.REALITY_BEHAVIOR
        assert loaded.last_decay_tick == NOW

    def test_pure_in_memory_round_trip(self):
        """to_dict → from_dict without filesystem."""
        state = default_hidden_state()
        state.sensitivity.current.value = 73.5
        state.touch_need.deficit.value = 22.0
        state.last_decay_tick = NOW

        restored = from_dict(to_dict(state))

        assert restored.sensitivity.current.value == pytest.approx(73.5)
        assert restored.touch_need.deficit.value == pytest.approx(22.0)
        assert restored.last_decay_tick == NOW
        assert restored.schema_version == state.schema_version

    def test_long_term_fields_preserved_through_round_trip(self, sandbox):
        """Baseline / embodied_ease survive serialization unchanged."""
        state = default_hidden_state()
        state.sensitivity.baseline.value = 55.0
        state.touch_need.baseline.value = 45.0
        state.embodied_ease.value = 60.0

        save_hidden_state(TEST_UID, state)
        loaded = load_hidden_state(TEST_UID)

        assert loaded.sensitivity.baseline.value == pytest.approx(55.0)
        assert loaded.touch_need.baseline.value == pytest.approx(45.0)
        assert loaded.embodied_ease.value == pytest.approx(60.0)

    def test_save_returns_true_on_success(self, sandbox):
        ok = save_hidden_state(TEST_UID, default_hidden_state())
        assert ok is True


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Missing file
# ═══════════════════════════════════════════════════════════════════════════════

class TestMissingFile:
    def test_returns_default_when_file_absent(self, sandbox):
        result = load_hidden_state(TEST_UID)
        defaults = default_hidden_state()
        assert result.sensitivity.current.value == defaults.sensitivity.current.value
        assert result.touch_need.deficit.value == defaults.touch_need.deficit.value
        assert result.schema_version == defaults.schema_version

    def test_does_not_raise_for_absent_file(self, sandbox):
        load_hidden_state("nonexistent_uid_xyz")  # must not raise

    def test_absent_file_returns_scalar_center_sensitivity(self, sandbox):
        result = load_hidden_state(TEST_UID)
        assert result.sensitivity.current.value == SCALAR_CENTER

    def test_absent_file_returns_zero_deficit(self, sandbox):
        result = load_hidden_state(TEST_UID)
        assert result.touch_need.deficit.value == 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Corrupt JSON
# ═══════════════════════════════════════════════════════════════════════════════

class TestCorruptJson:
    def _write_raw(self, sandbox, uid: str, content: str):
        path = sandbox.user_memory_root(uid) / HIDDEN_STATE_FILENAME
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def test_returns_default_on_invalid_json(self, sandbox):
        self._write_raw(sandbox, TEST_UID, "this is { not valid json")
        result = load_hidden_state(TEST_UID)
        assert result.sensitivity.current.value == SCALAR_CENTER

    def test_logs_warning_on_corrupt_json(self, sandbox, caplog):
        self._write_raw(sandbox, TEST_UID, "{broken json}")
        with caplog.at_level(logging.WARNING, logger="core.memory.user_hidden_state_store"):
            load_hidden_state(TEST_UID)
        assert any("corrupt" in r.message.lower() for r in caplog.records)

    def test_returns_default_on_empty_file(self, sandbox):
        self._write_raw(sandbox, TEST_UID, "")
        result = load_hidden_state(TEST_UID)
        assert result.schema_version == 1

    def test_returns_default_on_json_null(self, sandbox):
        self._write_raw(sandbox, TEST_UID, "null")
        result = load_hidden_state(TEST_UID)
        assert result.sensitivity.current.value == SCALAR_CENTER

    def test_does_not_raise_on_corrupt_json(self, sandbox):
        self._write_raw(sandbox, TEST_UID, "<<<not json>>>")
        load_hidden_state(TEST_UID)  # must not raise


# ═══════════════════════════════════════════════════════════════════════════════
# 4. schema_version missing
# ═══════════════════════════════════════════════════════════════════════════════

class TestSchemaVersionMissing:
    _DATA_NO_VERSION = {
        "sensitivity": {
            "baseline": {"value": 55.0, "last_updated": None, "last_update_source": "init"},
            "current":  {"value": 60.0, "last_updated": NOW,  "last_update_source": "reality_behavior"},
        },
        "touch_need": {
            "baseline": {"value": 50.0, "last_updated": None, "last_update_source": "init"},
            "deficit":  {"value": 12.0, "last_updated": NOW,  "last_update_source": "reality_behavior"},
        },
        "embodied_ease": {"value": 50.0, "last_updated": None, "last_update_source": "init"},
        "body_memory": {"entries": [], "max_entries": 32},
        "last_decay_tick": None,
        # schema_version intentionally absent
    }

    def test_from_dict_without_schema_version_deserializes_leniently(self):
        result = from_dict(self._DATA_NO_VERSION)
        assert result.sensitivity.current.value == pytest.approx(60.0)
        assert result.touch_need.deficit.value == pytest.approx(12.0)

    def test_from_dict_missing_version_logs_warning(self, caplog):
        with caplog.at_level(logging.WARNING, logger="core.memory.user_hidden_state"):
            from_dict({"sensitivity": {}, "touch_need": {}})
        assert any("schema_version" in r.message for r in caplog.records)

    def test_from_dict_missing_version_returns_valid_state(self):
        result = from_dict(self._DATA_NO_VERSION)
        assert isinstance(result, type(default_hidden_state()))
        assert result.schema_version == default_hidden_state().schema_version

    def test_load_hidden_state_file_missing_schema_version(self, sandbox):
        """File on disk without schema_version key → load returns usable state."""
        path = sandbox.user_memory_root(TEST_UID) / HIDDEN_STATE_FILENAME
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self._DATA_NO_VERSION), encoding="utf-8")

        result = load_hidden_state(TEST_UID)
        assert result.sensitivity.current.value == pytest.approx(60.0)
        assert result.touch_need.deficit.value == pytest.approx(12.0)


# ═══════════════════════════════════════════════════════════════════════════════
# 5. schema_version mismatch
# ═══════════════════════════════════════════════════════════════════════════════

class TestSchemaVersionMismatch:
    """S5a：未来版本抛 HiddenStateVersionError，load 返回只读状态且不覆盖文件。"""

    def test_from_dict_future_version_raises(self):
        with pytest.raises(HiddenStateVersionError):
            from_dict({"schema_version": 99})

    def test_from_dict_mismatch_logs_warning(self, caplog):
        with caplog.at_level(logging.WARNING, logger="core.memory.user_hidden_state"):
            with pytest.raises(HiddenStateVersionError):
                from_dict({"schema_version": 99})
        assert any("mismatch" in r.message.lower() for r in caplog.records)

    def test_load_future_version_is_read_only_and_not_overwritten(self, sandbox):
        path = sandbox.user_memory_root(TEST_UID) / HIDDEN_STATE_FILENAME
        path.parent.mkdir(parents=True, exist_ok=True)
        original = json.dumps({"schema_version": 99, "sensitivity": {"current": {"value": 99.0}}})
        path.write_text(original, encoding="utf-8")
        result = load_hidden_state(TEST_UID)
        assert result.read_only is True
        assert result.sensitivity.current.value == default_hidden_state().sensitivity.current.value
        assert save_hidden_state(TEST_UID, result) is False
        assert path.read_text(encoding="utf-8") == original

    def test_corrupt_json_is_backed_up(self, sandbox):
        path = sandbox.user_memory_root(TEST_UID) / HIDDEN_STATE_FILENAME
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json", encoding="utf-8")
        result = load_hidden_state(TEST_UID)
        assert result.read_only is False
        backups = list(path.parent.glob(HIDDEN_STATE_FILENAME + ".corrupt-*"))
        assert len(backups) == 1
        assert backups[0].read_text(encoding="utf-8") == "{not json"


class TestConcurrentWrites:
    def test_concurrent_integrations_both_preserved(self, sandbox):
        import threading
        from core.memory.user_hidden_state_integrator import integrate_event_and_save
        from core.memory.user_hidden_state_integrator import RealityEventType
        from core.write_envelope import stamp_user_chat

        env = stamp_user_chat()
        n = 8
        barrier = threading.Barrier(n)

        def work():
            barrier.wait()
            integrate_event_and_save(TEST_UID, next(iter(RealityEventType)), env, NOW)

        # 基线：单次事件造成的位移
        single = default_hidden_state()
        from core.memory.user_hidden_state_integrator import integrate_event
        single, res = integrate_event(next(iter(RealityEventType)), single, env, NOW)
        threads = [threading.Thread(target=work) for _ in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        after = load_hidden_state(TEST_UID)
        base = default_hidden_state()
        one = abs(single.sensitivity.current.value - base.sensitivity.current.value) + abs(
            single.touch_need.deficit.value - base.touch_need.deficit.value)
        got = abs(after.sensitivity.current.value - base.sensitivity.current.value) + abs(
            after.touch_need.deficit.value - base.touch_need.deficit.value)
        if res.accepted and one > 0:
            assert got > one  # 丢写时 n 次只会剩 1 次的位移


class TestNudgeCap:
    def test_nudge_current_sensitivity_capped(self):
        from core.memory.user_hidden_state import MAX_NUDGE_PER_EVENT
        st = default_hidden_state()
        before = st.sensitivity.current.value
        nudge_current_sensitivity(st, 1000.0, UpdateSource.REALITY_BEHAVIOR, NOW)
        assert st.sensitivity.current.value - before == pytest.approx(MAX_NUDGE_PER_EVENT)
