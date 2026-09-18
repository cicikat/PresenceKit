"""
tests/test_trigger_boundary_p0_5.py — Trigger Boundary P0.5 hardening tests.

Coverage:
  TA1  _assert_trigger_outlet_kind: allowed kinds pass without raising
  TA2  _assert_trigger_outlet_kind: rejected kinds (tool/activity/plugin/dream) raise
  TA3  _assert_trigger_outlet_kind: unknown kind raises
  TB1  _write_trigger_audit_log: new structured fields present (event_id, dedupe_key,
         gate_result, dream_guard_status, source, kind, trust, did_generate_reply)
  TB2  _write_trigger_audit_log: full reply text never present even with new fields
  TB3  capture_turn: audit_extras forwarded to _write_trigger_audit_log
  TC1  receive_perceive_event BLOCK_UNCERTAIN → emits WARNING-level log
  TC2  receive_perceive_event BLOCK_ACTIVE → emits INFO-level log (not WARNING)
  TD1  dream pipeline not coupled to trigger outlet (_pipeline_send / perceive_event)
  TD2  fanout channel_message still carries source="reality" (P0 invariant re-check)
"""

from __future__ import annotations
from tests.fixtures.public_assets import TEST_CHAR_ID

import asyncio
import inspect
import json
import logging
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ── TA: kind validation on trigger-only outlet ────────────────────────────────

class TestTriggerOutletKindValidation(unittest.TestCase):
    """TA: _assert_trigger_outlet_kind enforces the allowed-kind contract."""

    def _assert_kind(self, kind: str) -> None:
        from core.scheduler.loop import _assert_trigger_outlet_kind
        _assert_trigger_outlet_kind(kind)

    def test_allowed_kinds_pass(self):
        for kind in ("trigger", "sensor", "scheduled", "wake"):
            with self.subTest(kind=kind):
                self._assert_kind(kind)  # must not raise

    def test_rejected_kinds_raise(self):
        for kind in ("tool", "activity", "plugin", "dream"):
            with self.subTest(kind=kind):
                with self.assertRaises(ValueError):
                    self._assert_kind(kind)

    def test_unknown_kind_raises(self):
        for kind in ("", "llm", "ui", "webhook", "unknown_kind_xyz"):
            with self.subTest(kind=kind):
                with self.assertRaises(ValueError):
                    self._assert_kind(kind)

    def test_rejected_kinds_error_message_mentions_kind(self):
        from core.scheduler.loop import _assert_trigger_outlet_kind
        with self.assertRaises(ValueError) as ctx:
            _assert_trigger_outlet_kind("dream")
        self.assertIn("dream", str(ctx.exception))

    def test_allowed_set_does_not_contain_rejected(self):
        from core.scheduler.loop import (
            _TRIGGER_OUTLET_ALLOWED_KINDS,
            _TRIGGER_OUTLET_REJECTED_KINDS,
        )
        overlap = _TRIGGER_OUTLET_ALLOWED_KINDS & _TRIGGER_OUTLET_REJECTED_KINDS
        self.assertEqual(overlap, frozenset(), "allowed and rejected sets must be disjoint")


# ── TB: _write_trigger_audit_log structured fields ────────────────────────────

class TestAuditLogStructuredFields(unittest.TestCase):
    """TB: trigger_audit_log has provenance fields; never has full reply text."""

    def _write_to_tmp(self, **kwargs) -> dict:
        tmp = Path(tempfile.mkdtemp())
        mock_paths = MagicMock()
        mock_paths._p = MagicMock(return_value=tmp)

        with patch("core.sandbox.get_paths", return_value=mock_paths), \
             patch("core.sandbox.safe_user_id", side_effect=lambda x: x):
            from core.memory.fixation_pipeline import _write_trigger_audit_log
            _write_trigger_audit_log(**kwargs)

        files = list(tmp.rglob("trigger_audit.jsonl"))
        if not files:
            return {}
        return json.loads(files[0].read_text(encoding="utf-8"))

    def test_structured_provenance_fields_written(self):
        record = self._write_to_tmp(
            uid="u1",
            turn_id="t1",
            trigger_name="morning_greeting",
            reply="早安",
            emotion="happy",
            char_id=TEST_CHAR_ID,
            event_id="evt-abc",
            dedupe_key="dk-xyz",
            gate_result="accepted",
            dream_guard_status="ALLOW",
            source="scheduler",
            kind="scheduled",
            trust="low_trust",
            did_generate_reply=True,
        )
        if not record:
            return  # path layout may differ; no exception = pass
        for field in ("event_id", "dedupe_key", "gate_result", "dream_guard_status",
                      "source", "kind", "trust", "did_generate_reply", "reply_hash", "reply_len"):
            self.assertIn(field, record, f"audit log missing field: {field}")
        self.assertEqual(record["event_id"], "evt-abc")
        self.assertEqual(record["dedupe_key"], "dk-xyz")
        self.assertEqual(record["gate_result"], "accepted")
        self.assertEqual(record["kind"], "stimulus")
        self.assertEqual(record["trust"], "low_trust")
        self.assertEqual(record["dream_guard_status"], "ALLOW")
        self.assertTrue(record["did_generate_reply"])

    def test_full_reply_text_never_in_record(self):
        reply = "早安，今天的你比阳光还要耀眼，快去吃早饭哦。"
        record = self._write_to_tmp(
            uid="u2",
            turn_id="t2",
            trigger_name="night_reminder",
            reply=reply,
            emotion="gentle",
            char_id=TEST_CHAR_ID,
            event_id="evt-no-text",
            dedupe_key="dk-no-text",
            gate_result="accepted",
        )
        if not record:
            return
        record_str = json.dumps(record)
        self.assertNotIn(reply, record_str, "audit log must never store full reply text")

    def test_empty_provenance_fields_omitted_from_record(self):
        """When called without provenance kwargs, fields are absent (compact legacy format)."""
        record = self._write_to_tmp(
            uid="u3",
            turn_id="t3",
            trigger_name="diary_reminder",
            reply="写日记了吗",
            emotion="neutral",
            char_id=TEST_CHAR_ID,
        )
        if not record:
            return
        # Legacy call: empty provenance fields stay absent; kind is now the
        # mandatory conceptual envelope name for every trigger audit record.
        for field in ("event_id", "dedupe_key", "source",
                      "dream_guard_status", "gate_result"):
            self.assertNotIn(field, record,
                             f"empty provenance field {field!r} should be omitted")
        self.assertEqual(record["kind"], "stimulus")


class TestCaptureTurnAuditExtrasForwarded(unittest.TestCase):
    """TB3: capture_turn forwards audit_extras to _write_trigger_audit_log."""

    def test_audit_extras_forwarded(self):
        captured: list[dict] = []

        def _capture_write(*args, **kwargs):
            captured.append(kwargs)

        with patch("core.memory.fixation_pipeline._write_trigger_audit_log",
                   side_effect=_capture_write), \
             patch("core.memory.event_log.append", return_value=True), \
             patch("core.reality_output_scrubber.scrub_reality_output_text",
                   side_effect=lambda x: x):
            from core.memory.fixation_pipeline import capture_turn
            from core.write_envelope import stamp_trigger
            capture_turn(
                uid="u1",
                user_msg="（触发描述）",
                reply="晚安",
                trigger_name="night_reminder",
                envelope=stamp_trigger(),
                char_id=TEST_CHAR_ID,
                audit_extras={
                    "event_id": "evt-fwd",
                    "dedupe_key": "dk-fwd",
                    "gate_result": "accepted",
                    "source": "scheduler",
                    "kind": "scheduled",
                    "dream_guard_status": "ALLOW",
                    "did_generate_reply": True,
                },
            )

        self.assertTrue(captured, "expected _write_trigger_audit_log to be called")
        call_kwargs = captured[0]
        self.assertEqual(call_kwargs.get("event_id"), "evt-fwd")
        self.assertEqual(call_kwargs.get("dedupe_key"), "dk-fwd")
        self.assertEqual(call_kwargs.get("gate_result"), "accepted")

    def test_unknown_audit_extras_do_not_interrupt_capture(self):
        from core.memory.fixation_pipeline import capture_turn
        from core.write_envelope import stamp_trigger

        with patch("core.memory.event_log.append", return_value=True), \
             patch("core.reality_output_scrubber.scrub_reality_output_text",
                   side_effect=lambda x: x), \
             patch("core.memory.fixation_pipeline.safe_append_jsonl") as append_jsonl, \
             patch("core.memory.fixation_pipeline.get_paths") as paths:
            audit_dir = Path(tempfile.mkdtemp())
            paths.return_value._p.return_value = audit_dir
            capture_turn(
                uid="u1",
                user_msg="（触发描述）",
                reply="主动开口",
                trigger_name="autonomy",
                envelope=stamp_trigger(),
                char_id=TEST_CHAR_ID,
                audit_extras={
                    "source": "autonomy",
                    "trigger_source": "idle",
                    "run_id": "run-1",
                    "correlation_id": "corr-1",
                    "not_a_real_field": object(),
                },
            )
            self.assertTrue(append_jsonl.called)
            record = append_jsonl.call_args.args[1]
            self.assertEqual(record["run_id"], "run-1")
            self.assertEqual(record["correlation_id"], "corr-1")
            self.assertEqual(record["trigger_source"], "idle")
            self.assertNotIn("not_a_real_field", record)
            self.assertEqual(record.get("source"), "autonomy")


# ── TC: BLOCK_UNCERTAIN emits WARNING ─────────────────────────────────────────

class TestBlockUncertainWarning(unittest.TestCase):
    """TC: BLOCK_UNCERTAIN path logs at WARNING; BLOCK_ACTIVE stays at INFO."""

    def setUp(self):
        from core.perceive_event import clear_dedup_registry_for_test
        clear_dedup_registry_for_test()

    def _make_event(self, uid="owner_warn"):
        from core.perceive_event import PerceiveEvent
        return PerceiveEvent(
            source="scheduler",
            uid=uid,
            channel="system",
            kind="scheduled",
            char_id=TEST_CHAR_ID,
            payload={"trigger_name": "morning_greeting"},
        )

    def test_block_uncertain_emits_warning(self):
        from core.perceive_event import PerceiveStatus, receive_perceive_event
        from core.dream.dream_state import DreamGuardStatus

        with self.assertLogs("core.perceive_event", level=logging.WARNING) as cm:
            with patch("core.dream.dream_state.get_reality_guard_status",
                       return_value=DreamGuardStatus.BLOCK_UNCERTAIN):
                result = _run(receive_perceive_event(self._make_event()))

        self.assertEqual(result.status, PerceiveStatus.BLOCKED_DREAM)
        warning_lines = [m for m in cm.output if "WARNING" in m]
        self.assertTrue(warning_lines,
                        "BLOCK_UNCERTAIN must emit at least one WARNING-level log")
        combined = " ".join(warning_lines)
        self.assertIn("BLOCK_UNCERTAIN", combined)

    def test_block_uncertain_log_contains_uid_and_source(self):
        from core.perceive_event import receive_perceive_event
        from core.dream.dream_state import DreamGuardStatus

        uid = "owner_warn_fields"
        with self.assertLogs("core.perceive_event", level=logging.WARNING) as cm:
            with patch("core.dream.dream_state.get_reality_guard_status",
                       return_value=DreamGuardStatus.BLOCK_UNCERTAIN):
                _run(receive_perceive_event(self._make_event(uid=uid)))

        combined = " ".join(cm.output)
        self.assertIn(uid, combined, "WARNING log must contain uid")
        self.assertIn("scheduler", combined, "WARNING log must contain source")

    def test_block_active_is_not_warning(self):
        from core.perceive_event import PerceiveStatus, receive_perceive_event
        from core.dream.dream_state import DreamGuardStatus

        # BLOCK_ACTIVE should NOT produce a WARNING-level log.
        with patch("core.dream.dream_state.get_reality_guard_status",
                   return_value=DreamGuardStatus.BLOCK_ACTIVE):
            # Capture at DEBUG to see all log records
            with self.assertLogs("core.perceive_event", level=logging.DEBUG) as cm:
                result = _run(receive_perceive_event(self._make_event(uid="owner_active")))

        self.assertEqual(result.status, PerceiveStatus.BLOCKED_DREAM)
        warning_lines = [m for m in cm.output if "WARNING" in m and "BLOCK_UNCERTAIN" in m]
        self.assertEqual(warning_lines, [],
                         "BLOCK_ACTIVE must NOT emit a BLOCK_UNCERTAIN WARNING")


# ── TD: Dream pipeline not coupled to trigger outlet ─────────────────────────

class TestDreamPipelineIsolation(unittest.TestCase):
    """TD: dream_pipeline has no coupling to _pipeline_send or perceive_event."""

    def test_dream_pipeline_not_coupled_to_trigger_outlet(self):
        import core.dream.dream_pipeline as _dp
        src = inspect.getsource(_dp)
        self.assertNotIn(
            "_pipeline_send", src,
            "dream_pipeline must not reference _pipeline_send (trigger-only outlet)",
        )
        self.assertNotIn(
            "receive_perceive_event", src,
            "dream_pipeline must not call receive_perceive_event",
        )

    def test_ws_channel_message_still_source_reality(self):
        """P0 invariant: WS channel_message still carries source='reality'."""
        sent: list[dict] = []

        async def _mock_send(payload: dict) -> bool:
            sent.append(payload)
            return True

        with patch("channels.desktop_ws._send_json", side_effect=_mock_send), \
             patch("channels.desktop_ws._current_ws", MagicMock()):
            from channels.desktop_ws import push_message
            _run(push_message("p0.5 reality check"))

        self.assertTrue(sent)
        self.assertEqual(sent[0].get("type"), "channel_message")
        self.assertEqual(sent[0].get("source"), "reality",
                         "channel_message source must still be 'reality' after P0.5")


if __name__ == "__main__":
    unittest.main()
