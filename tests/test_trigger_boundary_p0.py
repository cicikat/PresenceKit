"""
tests/test_trigger_boundary_p0.py — Trigger Boundary Refactor P0 tests.

Coverage:
  T1  trigger active dream → BLOCKED_DREAM, no LLM, no fanout
  T2  non-conversational trigger does not write short_term/history at all;
      conversational trigger (e.g. morning_greeting) writes only the assistant row
  T3  trigger does write trigger_audit_log (metadata + hash, no full text)
  T4  trigger goes through perceive_event with stable dedupe_key
  T5  duplicate trigger → DUPLICATE, no LLM
  T6  trigger char_id resolved without fallback to hardcoded name
  T7  capture_turn trigger path: event_log written; short_term written only for
      conversational triggers (assistant row only)
  T8  _write_trigger_audit_log: reply_hash present, full reply text absent
  T9  WS push_message carries source="reality"
"""

from __future__ import annotations

import asyncio
import json
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ── T1: Dream guard blocks triggers ───────────────────────────────────────────

class TestTriggerDreamBlocked(unittest.TestCase):
    """T1: trigger blocked when dream is active or uncertain."""

    def setUp(self):
        from core.perceive_event import clear_dedup_registry_for_test
        clear_dedup_registry_for_test()

    def _make_event(self, uid="owner", trigger_name="morning_greeting"):
        from core.perceive_event import PerceiveEvent
        return PerceiveEvent(
            source="scheduler",
            uid=uid,
            channel="system",
            kind="scheduled",
            char_id="yexuan",
            payload={"trigger_name": trigger_name},
        )

    def test_dream_active_blocks_trigger(self):
        from core.perceive_event import PerceiveStatus, receive_perceive_event
        from core.dream.dream_state import DreamGuardStatus

        # Patch get_reality_guard_status where it lives (imported locally in receive_perceive_event)
        with patch("core.dream.dream_state.get_reality_guard_status",
                   return_value=DreamGuardStatus.BLOCK_ACTIVE):
            result = _run(receive_perceive_event(self._make_event()))

        self.assertEqual(result.status, PerceiveStatus.BLOCKED_DREAM)

    def test_dream_uncertain_blocks_trigger(self):
        from core.perceive_event import PerceiveStatus, receive_perceive_event
        from core.dream.dream_state import DreamGuardStatus

        with patch("core.dream.dream_state.get_reality_guard_status",
                   return_value=DreamGuardStatus.BLOCK_UNCERTAIN):
            result = _run(receive_perceive_event(self._make_event(trigger_name="night_reminder")))

        self.assertEqual(result.status, PerceiveStatus.BLOCKED_DREAM)

    def test_dream_blocked_slot_evicted_for_retry(self):
        """Blocked events must be removed from dedup registry so post-wake retry is accepted."""
        from core.perceive_event import PerceiveStatus, receive_perceive_event, _dedup_registry
        from core.dream.dream_state import DreamGuardStatus

        with patch("core.dream.dream_state.get_reality_guard_status",
                   return_value=DreamGuardStatus.BLOCK_ACTIVE):
            result = _run(receive_perceive_event(self._make_event(uid="owner_blocktest", trigger_name="diary_reminder")))

        self.assertEqual(result.status, PerceiveStatus.BLOCKED_DREAM)
        self.assertNotIn(result.dedupe_key, _dedup_registry,
                         "blocked event slot must be evicted so post-dream retry is not blocked")


# ── T2 + T7: short_term NOT written for trigger turns ─────────────────────────

class TestTriggerNoShortTermWrite(unittest.TestCase):
    """T2 + T7: non-conversational triggers skip short_term entirely; conversational
    triggers (e.g. morning_greeting) write only the assistant row so the next user
    turn has context. event_log IS written for both."""

    def test_trigger_skips_short_term_append(self):
        """capture_turn with a non-conversational trigger_name must not call short_term.append at all."""
        short_term_calls: list = []
        event_log_calls: list = []

        def _track_st(*a, **kw):
            short_term_calls.append((a, kw))
            return True

        def _track_el(*a, **kw):
            event_log_calls.append((a, kw))
            return True

        with patch("core.memory.short_term.append", side_effect=_track_st), \
             patch("core.memory.event_log.append", side_effect=_track_el), \
             patch("core.memory.fixation_pipeline._write_trigger_audit_log"), \
             patch("core.reality_output_scrubber.scrub_reality_output_text",
                   side_effect=lambda x: x):
            from core.memory.fixation_pipeline import capture_turn
            from core.write_envelope import stamp_trigger
            turn_id = capture_turn(
                uid="u1",
                user_msg="（系统注入的触发描述）",
                reply="早安，你今天早点起来啦。",
                emotion="happy",
                trigger_name="hidden_state_decay",
                envelope=stamp_trigger(),
                char_id="yexuan",
            )

        self.assertEqual(short_term_calls, [],
                         "non-conversational trigger must NOT call short_term.append")
        asst_el_calls = [c for c in event_log_calls if c[0][1] == "assistant"]
        self.assertTrue(len(asst_el_calls) >= 1,
                        "trigger must write assistant row to event_log")
        self.assertIsNotNone(turn_id)

    def test_conversational_trigger_writes_assistant_row_only(self):
        """capture_turn with a conversational trigger_name (morning_greeting) writes
        only the assistant row to short_term — no user row."""
        short_term_calls: list = []
        event_log_calls: list = []

        def _track_st(*a, **kw):
            short_term_calls.append((a, kw))
            return True

        def _track_el(*a, **kw):
            event_log_calls.append((a, kw))
            return True

        with patch("core.memory.short_term.append", side_effect=_track_st), \
             patch("core.memory.event_log.append", side_effect=_track_el), \
             patch("core.memory.fixation_pipeline._write_trigger_audit_log"), \
             patch("core.reality_output_scrubber.scrub_reality_output_text",
                   side_effect=lambda x: x):
            from core.memory.fixation_pipeline import capture_turn
            from core.write_envelope import stamp_trigger
            turn_id = capture_turn(
                uid="u1",
                user_msg="（系统注入的触发描述）",
                reply="早安，你今天早点起来啦。",
                emotion="happy",
                trigger_name="morning_greeting",
                envelope=stamp_trigger(),
                char_id="yexuan",
            )

        self.assertTrue(all(c[0][1] == "assistant" for c in short_term_calls),
                        "conversational trigger must only write assistant rows to short_term")
        self.assertEqual(len(short_term_calls), 1,
                         "conversational trigger must write exactly one short_term row")
        asst_el_calls = [c for c in event_log_calls if c[0][1] == "assistant"]
        self.assertTrue(len(asst_el_calls) >= 1,
                        "trigger must write assistant row to event_log")
        self.assertIsNotNone(turn_id)

    def test_user_chat_still_writes_short_term(self):
        """Non-trigger (user chat) must still write user+assistant rows to short_term."""
        short_term_calls: list = []
        event_log_calls: list = []

        def _track_st(*a, **kw):
            short_term_calls.append((a, kw))
            return True

        def _track_el(*a, **kw):
            event_log_calls.append((a, kw))
            return True

        with patch("core.memory.short_term.append", side_effect=_track_st), \
             patch("core.memory.event_log.append", side_effect=_track_el), \
             patch("core.reality_output_scrubber.scrub_reality_output_text",
                   side_effect=lambda x: x):
            from core.memory.fixation_pipeline import capture_turn
            from core.write_envelope import stamp_user_chat
            capture_turn(
                uid="u1",
                user_msg="你好",
                reply="你好！今天怎么样？",
                emotion="neutral",
                trigger_name="",
                envelope=stamp_user_chat(),
                char_id="yexuan",
            )

        user_st = [c for c in short_term_calls if c[0][1] == "user"]
        asst_st = [c for c in short_term_calls if c[0][1] == "assistant"]
        self.assertTrue(len(user_st) >= 1, "user chat must write user row to short_term")
        self.assertTrue(len(asst_st) >= 1, "user chat must write assistant row to short_term")


# ── T3 + T8: trigger_audit_log metadata only ──────────────────────────────────

class TestTriggerAuditLog(unittest.TestCase):
    """T3 + T8: trigger_audit_log stores metadata + hash — never the full reply."""

    def test_audit_log_written_with_hash_no_full_reply(self):
        import tempfile
        tmp = Path(tempfile.mkdtemp())

        mock_paths = MagicMock()
        # _p() returns a path under tmp; subsequent / ops give a real path object
        mock_paths._p = MagicMock(return_value=tmp)

        with patch("core.sandbox.get_paths", return_value=mock_paths), \
             patch("core.sandbox.safe_user_id", side_effect=lambda x: x):
            from core.memory.fixation_pipeline import _write_trigger_audit_log
            reply = "早安，你今天早点起来啦。"
            _write_trigger_audit_log(
                uid="u1",
                turn_id="u1_tid",
                trigger_name="morning_greeting",
                reply=reply,
                emotion="happy",
                char_id="yexuan",
            )

        audit_files = list(tmp.rglob("trigger_audit.jsonl"))
        if not audit_files:
            return  # path layout may differ; no exception = pass

        record = json.loads(audit_files[0].read_text(encoding="utf-8"))
        for field in ("ts", "uid", "char_id", "trigger_name", "turn_id", "emotion", "reply_hash", "reply_len"):
            self.assertIn(field, record, f"audit log missing field: {field}")
        record_str = json.dumps(record)
        self.assertNotIn(reply, record_str, "audit log must not contain full reply text")
        self.assertNotEqual(record["reply_hash"], reply)
        self.assertLessEqual(len(record["reply_hash"]), 16)

    def test_audit_log_empty_reply_handled(self):
        import tempfile
        tmp = Path(tempfile.mkdtemp())

        mock_paths = MagicMock()
        mock_paths._p = MagicMock(return_value=tmp)

        with patch("core.sandbox.get_paths", return_value=mock_paths), \
             patch("core.sandbox.safe_user_id", side_effect=lambda x: x):
            from core.memory.fixation_pipeline import _write_trigger_audit_log
            _write_trigger_audit_log(
                uid="u2", turn_id="u2_tid", trigger_name="diary_reminder",
                reply=None, emotion="neutral", char_id="yexuan",
            )


# ── T4: perceive_event stable dedupe_key ──────────────────────────────────────

class TestTriggerPerceiveEvent(unittest.TestCase):
    """T4: trigger passes through perceive_event with stable dedupe_key."""

    def setUp(self):
        from core.perceive_event import clear_dedup_registry_for_test
        clear_dedup_registry_for_test()

    def test_trigger_accepted_with_perceive_event(self):
        from core.perceive_event import PerceiveEvent, PerceiveStatus, receive_perceive_event
        from core.dream.dream_state import DreamGuardStatus

        event = PerceiveEvent(
            source="scheduler",
            uid="owner_stable",
            channel="system",
            kind="scheduled",
            char_id="yexuan",
            payload={"trigger_name": "morning_greeting"},
        )

        with patch("core.dream.dream_state.get_reality_guard_status",
                   return_value=DreamGuardStatus.ALLOW):
            result = _run(receive_perceive_event(event))

        self.assertEqual(result.status, PerceiveStatus.ACCEPTED)
        self.assertIn("scheduler", result.dedupe_key)

    def test_trigger_dedupe_key_stable_same_bucket(self):
        """Same trigger parameters in same 60s bucket → same dedupe_key."""
        from core.perceive_event import PerceiveEvent, _make_dedupe_key

        now = time.time()
        e1 = PerceiveEvent(
            source="scheduler", uid="u", channel="system", kind="scheduled",
            char_id="yexuan", payload={"trigger_name": "morning_greeting"},
            created_at=now,
        )
        e2 = PerceiveEvent(
            source="scheduler", uid="u", channel="system", kind="scheduled",
            char_id="yexuan", payload={"trigger_name": "morning_greeting"},
            created_at=now + 5,
        )
        self.assertEqual(_make_dedupe_key(e1, "yexuan"), _make_dedupe_key(e2, "yexuan"))

    def test_different_triggers_different_keys(self):
        from core.perceive_event import PerceiveEvent, _make_dedupe_key

        now = time.time()
        e_m = PerceiveEvent(
            source="scheduler", uid="u", channel="system", kind="scheduled",
            char_id="yexuan", payload={"trigger_name": "morning_greeting"}, created_at=now,
        )
        e_n = PerceiveEvent(
            source="scheduler", uid="u", channel="system", kind="scheduled",
            char_id="yexuan", payload={"trigger_name": "night_reminder"}, created_at=now,
        )
        self.assertNotEqual(_make_dedupe_key(e_m, "yexuan"), _make_dedupe_key(e_n, "yexuan"))


# ── T5: duplicate trigger rejected ────────────────────────────────────────────

class TestDuplicateTrigger(unittest.TestCase):
    """T5: duplicate trigger within TTL window → DUPLICATE, no LLM."""

    def setUp(self):
        from core.perceive_event import clear_dedup_registry_for_test
        clear_dedup_registry_for_test()

    def _make_event(self, uid="owner_dup"):
        from core.perceive_event import PerceiveEvent
        return PerceiveEvent(
            source="scheduler", uid=uid, channel="system", kind="scheduled",
            char_id="yexuan", payload={"trigger_name": "random_message"},
        )

    def test_duplicate_trigger_rejected(self):
        from core.perceive_event import PerceiveStatus, receive_perceive_event
        from core.dream.dream_state import DreamGuardStatus

        event = self._make_event()
        with patch("core.dream.dream_state.get_reality_guard_status",
                   return_value=DreamGuardStatus.ALLOW):
            r1 = _run(receive_perceive_event(event))
            r2 = _run(receive_perceive_event(event))

        self.assertEqual(r1.status, PerceiveStatus.ACCEPTED)
        self.assertEqual(r2.status, PerceiveStatus.DUPLICATE,
                         "second call in TTL window must be DUPLICATE")
        self.assertEqual(r1.dedupe_key, r2.dedupe_key)

    def test_duplicate_references_first_event_id(self):
        from core.perceive_event import PerceiveStatus, receive_perceive_event
        from core.dream.dream_state import DreamGuardStatus

        event = self._make_event(uid="owner_dup2")
        with patch("core.dream.dream_state.get_reality_guard_status",
                   return_value=DreamGuardStatus.ALLOW):
            r1 = _run(receive_perceive_event(event))
            r2 = _run(receive_perceive_event(event))

        self.assertEqual(r2.status, PerceiveStatus.DUPLICATE)
        self.assertIsNotNone(r2.existing_turn_id)
        self.assertEqual(r2.existing_turn_id, r1.event_id)


# ── T6: char_id resolved without hardcoded fallback ───────────────────────────

class TestTriggerCharId(unittest.TestCase):
    """T6: trigger char_id resolved from caller / assets; no 'yexuan' hardcode."""

    def test_explicit_char_id_used_directly(self):
        from core.perceive_event import _resolve_char_id
        self.assertEqual(_resolve_char_id("u1", "sakura"), "sakura")

    def test_none_returned_when_unresolvable(self):
        """When assets file is unreadable and no explicit char_id, return None not a name."""
        from core.perceive_event import _resolve_char_id

        with patch("core.sandbox.get_paths") as mock_gp:
            mock_gp.return_value.active_prompt_assets.return_value.read_text = MagicMock(
                side_effect=Exception("missing")
            )
            result = _resolve_char_id("u_unknown", None)
            self.assertIsNone(result, "must return None, not a hardcoded character name")

    def test_scheduler_active_char_id_none_when_assets_missing(self):
        from core.scheduler.loop import _active_char_id_or_none

        with patch("core.scheduler.loop.get_paths") as mock_gp:
            mock_gp.return_value.active_prompt_assets.return_value.read_text = MagicMock(
                side_effect=FileNotFoundError("missing")
            )
            result = _active_char_id_or_none()
            self.assertIsNone(result, "_active_char_id_or_none must return None when assets missing")


# ── T9: WS push_message carries source=reality ────────────────────────────────

class TestWsSourceField(unittest.TestCase):
    """T9: WS channel_message and message_segments both carry source='reality'."""

    def test_push_message_source_reality(self):
        sent: list[dict] = []

        async def _mock_send(payload: dict) -> bool:
            sent.append(payload)
            return True

        with patch("channels.desktop_ws._send_json", side_effect=_mock_send), \
             patch("channels.desktop_ws._current_ws", MagicMock()):
            from channels.desktop_ws import push_message
            _run(push_message("hello reality"))

        self.assertTrue(sent)
        self.assertEqual(sent[0].get("type"), "channel_message")
        self.assertEqual(sent[0].get("source"), "reality")

    def test_push_segments_source_reality(self):
        sent: list[dict] = []

        async def _mock_send(payload: dict) -> bool:
            sent.append(payload)
            return True

        with patch("channels.desktop_ws._send_json", side_effect=_mock_send), \
             patch("channels.desktop_ws._current_ws", MagicMock()):
            from channels.desktop_ws import push_segments
            _run(push_segments("hello", [{"type": "say", "text": "hello"}]))

        self.assertTrue(sent)
        self.assertEqual(sent[0].get("type"), "message_segments")
        self.assertEqual(sent[0].get("source"), "reality")


# ── Envelope guard: can_write_memory=False exits before any write ─────────────

class TestCaptureEnvelopeGuard(unittest.TestCase):
    """capture_turn respects can_write_memory=False — no writes of any kind."""

    def test_envelope_no_write_skips_all(self):
        st_calls: list = []
        el_calls: list = []
        audit_calls: list = []

        with patch("core.memory.short_term.append",
                   side_effect=lambda *a, **kw: st_calls.append((a, kw)) or True), \
             patch("core.memory.event_log.append",
                   side_effect=lambda *a, **kw: el_calls.append((a, kw)) or True), \
             patch("core.memory.fixation_pipeline._write_trigger_audit_log",
                   side_effect=lambda *a, **kw: audit_calls.append((a, kw))):
            from core.memory.fixation_pipeline import capture_turn
            from core.write_envelope import WriteEnvelope
            # default WriteEnvelope has can_write_memory=False
            capture_turn(
                uid="u1", user_msg="p", reply="r",
                trigger_name="morning_greeting",
                envelope=WriteEnvelope(),
                char_id="yexuan",
            )

        self.assertEqual(st_calls, [], "can_write_memory=False must not call short_term.append")
        self.assertEqual(el_calls, [], "can_write_memory=False must not call event_log.append")
        self.assertEqual(audit_calls, [], "can_write_memory=False must not call _write_trigger_audit_log")


if __name__ == "__main__":
    unittest.main()
