from tests.fixtures.public_assets import TEST_CHAR_ID
"""
tests/test_dedupe_key_stability.py — P1.1 dedupe_key stability audit

Verifies:
1. desktop_wake dedupe_key is stable: same wake stimulus → DUPLICATE even if result.event_id differs
2. scheduler dedupe_key is stable: same trigger identity within TTL → DUPLICATE
3. Different payload / different trigger → NOT duplicate (no false kill)
4. Different uid → NOT duplicate
5. Different char_id → NOT duplicate
6. result.event_id appears in dedup registry (tracing) but does NOT determine dedupe outcome
7. desktop_wake body with last_seen does NOT leak into dedup (fixed: payload={} in handler)
8. desktop_wake end-to-end: two rapid calls with differing last_seen → second is duplicate_wake
"""

import asyncio
import time
import pytest


# ── helpers ──────────────────────────────────────────────────────────────────

def _allow_dream_guard(monkeypatch):
    from core.dream import dream_state as _ds

    class _Allow:
        ALLOW = "allow"

    monkeypatch.setattr(_ds, "get_reality_guard_status", lambda uid: _Allow.ALLOW)
    monkeypatch.setattr(_ds, "DreamGuardStatus", _Allow)


# ── Test 1: desktop_wake — same payload {} → second DUPLICATE even though result.event_id differs ─

async def test_desktop_wake_stable_key_same_payload(monkeypatch):
    """
    Two desktop_wake PerceiveEvents with no caller-supplied event_id and same payload {}.
    receive_perceive_event generates a fresh UUID for result.event_id each call,
    but the dedupe_key (auto-key) is stable → second call is DUPLICATE.
    """
    _allow_dream_guard(monkeypatch)
    from core.perceive_event import PerceiveEvent, receive_perceive_event, PerceiveStatus

    ts = time.time()
    e1 = PerceiveEvent(
        source="desktop_wake", uid="u-wake1", channel="desktop", kind="wake",
        char_id="char-a", payload={}, created_at=ts,
    )
    e2 = PerceiveEvent(
        source="desktop_wake", uid="u-wake1", channel="desktop", kind="wake",
        char_id="char-a", payload={}, created_at=ts,
    )

    r1 = await receive_perceive_event(e1)
    r2 = await receive_perceive_event(e2)

    # result.event_id must be a fresh UUID each call (used only for tracing)
    assert r1.event_id != r2.event_id, (
        "result.event_id should be unique per call (it is a tracing UUID, not the dedup key)"
    )
    assert r1.status == PerceiveStatus.ACCEPTED, f"first call should be ACCEPTED: {r1}"
    assert r2.status == PerceiveStatus.DUPLICATE, (
        f"second call with same auto-key should be DUPLICATE even though event_id differs: {r2}"
    )
    # existing_turn_id points back to the first accepted event
    assert r2.existing_turn_id == r1.event_id


# ── Test 2: scheduler — same trigger_name within 60s bucket → DUPLICATE ──────

async def test_scheduler_same_trigger_same_bucket_is_deduped(monkeypatch):
    """
    Two scheduler PerceiveEvents with same trigger_name and same 60s time bucket
    are DUPLICATE.  The auto-key (source:uid:char:system:scheduled:hash:bucket)
    is stable because payload={"trigger_name": name} is fixed.
    """
    _allow_dream_guard(monkeypatch)
    from core.perceive_event import PerceiveEvent, receive_perceive_event, PerceiveStatus

    ts = time.time()
    e1 = PerceiveEvent(
        source="scheduler", uid="u-sched1", channel="system", kind="scheduled",
        char_id="char-a", payload={"trigger_name": "morning_greeting"}, created_at=ts,
    )
    e2 = PerceiveEvent(
        source="scheduler", uid="u-sched1", channel="system", kind="scheduled",
        char_id="char-a", payload={"trigger_name": "morning_greeting"}, created_at=ts,
    )

    r1 = await receive_perceive_event(e1)
    r2 = await receive_perceive_event(e2)

    assert r1.event_id != r2.event_id, "result.event_id must differ (fresh UUID each call)"
    assert r1.status == PerceiveStatus.ACCEPTED
    assert r2.status == PerceiveStatus.DUPLICATE, (
        "same trigger_name in same 60s bucket → DUPLICATE (dedupe_key stable, not event_id-based)"
    )


# ── Test 3: different payload / trigger → NOT deduped ────────────────────────

async def test_different_trigger_name_not_deduped(monkeypatch):
    """Two events with different trigger_name → different payload hash → different auto-key → both ACCEPTED."""
    _allow_dream_guard(monkeypatch)
    from core.perceive_event import PerceiveEvent, receive_perceive_event, PerceiveStatus

    ts = time.time()
    e1 = PerceiveEvent(
        source="scheduler", uid="u-s2", channel="system", kind="scheduled",
        char_id="char-a", payload={"trigger_name": "morning_greeting"}, created_at=ts,
    )
    e2 = PerceiveEvent(
        source="scheduler", uid="u-s2", channel="system", kind="scheduled",
        char_id="char-a", payload={"trigger_name": "night_reminder"}, created_at=ts,
    )

    r1 = await receive_perceive_event(e1)
    r2 = await receive_perceive_event(e2)

    assert r1.status == PerceiveStatus.ACCEPTED, f"morning_greeting should be ACCEPTED: {r1}"
    assert r2.status == PerceiveStatus.ACCEPTED, (
        f"night_reminder has different payload hash → must NOT be killed as duplicate: {r2}"
    )


# ── Test 4: different uid → NOT deduped ──────────────────────────────────────

async def test_different_uid_not_deduped(monkeypatch):
    """Two wake events identical except uid → auto-keys differ → both ACCEPTED."""
    _allow_dream_guard(monkeypatch)
    from core.perceive_event import PerceiveEvent, receive_perceive_event, PerceiveStatus

    ts = time.time()
    e1 = PerceiveEvent(
        source="desktop_wake", uid="uid-alpha", channel="desktop", kind="wake",
        char_id="char-a", payload={}, created_at=ts,
    )
    e2 = PerceiveEvent(
        source="desktop_wake", uid="uid-beta", channel="desktop", kind="wake",
        char_id="char-a", payload={}, created_at=ts,
    )

    r1 = await receive_perceive_event(e1)
    r2 = await receive_perceive_event(e2)

    assert r1.status == PerceiveStatus.ACCEPTED
    assert r2.status == PerceiveStatus.ACCEPTED, (
        f"different uid must NOT be killed as duplicate: {r2}"
    )


# ── Test 5: different char_id → NOT deduped ──────────────────────────────────

async def test_different_char_id_not_deduped(monkeypatch):
    """Two wake events identical except char_id → auto-keys differ → both ACCEPTED."""
    _allow_dream_guard(monkeypatch)
    from core.perceive_event import PerceiveEvent, receive_perceive_event, PerceiveStatus

    ts = time.time()
    e1 = PerceiveEvent(
        source="desktop_wake", uid="u-char", channel="desktop", kind="wake",
        char_id=f'char-{TEST_CHAR_ID}', payload={}, created_at=ts,
    )
    e2 = PerceiveEvent(
        source="desktop_wake", uid="u-char", channel="desktop", kind="wake",
        char_id="char-other", payload={}, created_at=ts,
    )

    r1 = await receive_perceive_event(e1)
    r2 = await receive_perceive_event(e2)

    assert r1.status == PerceiveStatus.ACCEPTED
    assert r2.status == PerceiveStatus.ACCEPTED, (
        f"different char_id must NOT be killed as duplicate: {r2}"
    )


# ── Test 6: event_id tracing vs dedupe separation ────────────────────────────

async def test_event_id_is_tracing_only_not_dedupe_key(monkeypatch):
    """
    result.event_id is stored in the dedup registry for tracing (DUPLICATE log shows
    first_event_id), but the dedupe_key itself does NOT contain the random UUID.
    Verify: auto-key (no caller event_id) is deterministic for same inputs.
    """
    _allow_dream_guard(monkeypatch)
    from core.perceive_event import PerceiveEvent, _make_dedupe_key, _resolve_char_id

    ts = time.time()
    resolved = "char-a"

    e1 = PerceiveEvent(
        source="desktop_wake", uid="u-trace", channel="desktop", kind="wake",
        char_id=resolved, payload={}, created_at=ts,
    )
    e2 = PerceiveEvent(
        source="desktop_wake", uid="u-trace", channel="desktop", kind="wake",
        char_id=resolved, payload={}, created_at=ts,
    )

    k1 = _make_dedupe_key(e1, resolved)
    k2 = _make_dedupe_key(e2, resolved)

    assert k1 == k2, (
        f"auto-key must be deterministic for same inputs (no random UUID in key): {k1!r} vs {k2!r}"
    )
    assert "uuid" not in k1.lower() and "4-" not in k1, (
        f"dedupe_key must not contain a UUID: {k1!r}"
    )
    # Key format check: source:uid:char:channel:kind:hash:bucket
    parts = k1.split(":")
    assert len(parts) == 7, f"auto-key should have 7 colon-separated parts: {k1!r}"
    assert parts[0] == "desktop_wake"
    assert parts[1] == "u-trace"
    assert parts[2] == "char-a"


async def test_duplicate_result_references_first_event_id(monkeypatch):
    """
    When a DUPLICATE is detected, result.existing_turn_id must equal the result.event_id
    of the first ACCEPTED call — proving event_id is used for tracing, not dedup selection.
    """
    _allow_dream_guard(monkeypatch)
    from core.perceive_event import PerceiveEvent, receive_perceive_event, PerceiveStatus

    ts = time.time()
    e1 = PerceiveEvent(
        source="scheduler", uid="u-eid", channel="system", kind="scheduled",
        char_id="char-a", payload={"trigger_name": "diary_reminder"}, created_at=ts,
    )
    e2 = PerceiveEvent(
        source="scheduler", uid="u-eid", channel="system", kind="scheduled",
        char_id="char-a", payload={"trigger_name": "diary_reminder"}, created_at=ts,
    )

    r1 = await receive_perceive_event(e1)
    r2 = await receive_perceive_event(e2)

    assert r1.status == PerceiveStatus.ACCEPTED
    assert r2.status == PerceiveStatus.DUPLICATE
    assert r2.existing_turn_id == r1.event_id, (
        "existing_turn_id must reference the first accepted event's tracing event_id"
    )
    assert r2.event_id != r1.event_id, (
        "the duplicate's own event_id is a fresh UUID (separate tracing ID)"
    )


# ── Test 7: desktop_wake handler strips last_seen (payload={} fixed) ──────────

async def test_desktop_wake_payload_is_empty_in_handler(monkeypatch):
    """
    The desktop_wake HTTP handler must NOT pass last_seen or other per-request
    dynamic fields into PerceiveEvent.payload.
    Two rapid calls with different last_seen values produce the same dedupe_key.
    """
    _allow_dream_guard(monkeypatch)
    from core.perceive_event import PerceiveEvent, _make_dedupe_key

    resolved_char = "char-a"
    ts = time.time()

    # Simulate what the FIXED handler sends: payload={}
    e_call1 = PerceiveEvent(
        source="desktop_wake", uid="u-ls", channel="desktop", kind="wake",
        char_id=resolved_char, payload={}, created_at=ts,  # last_seen NOT in payload
    )
    e_call2 = PerceiveEvent(
        source="desktop_wake", uid="u-ls", channel="desktop", kind="wake",
        char_id=resolved_char, payload={}, created_at=ts,  # different last_seen, still {}
    )

    k1 = _make_dedupe_key(e_call1, resolved_char)
    k2 = _make_dedupe_key(e_call2, resolved_char)

    assert k1 == k2, (
        "desktop_wake dedupe_key must be stable even if last_seen varies in the HTTP body; "
        f"got {k1!r} vs {k2!r}"
    )


async def test_desktop_wake_with_last_seen_in_body_deduped(monkeypatch):
    """
    End-to-end: desktop_wake called twice within TTL, second body includes last_seen.
    Path B queues one signal and the second request is rejected before enqueue.
    """
    from core.autonomy import store
    from core.perceive_event import clear_dedup_registry_for_test
    clear_dedup_registry_for_test()

    monkeypatch.setattr(
        "core.config_loader.get_config",
        lambda: {"scheduler": {"owner_id": "owner-ls"}},
    )
    state = store.load("owner-ls", "char-a")
    state["config"]["enabled"] = True
    assert store.save("owner-ls", "char-a", state)
    monkeypatch.setattr("core.perceive_event._resolve_char_id", lambda uid, cid: "char-a")
    import json as _json

    class _FakeAPA:
        def read_text(self, encoding="utf-8"):
            return _json.dumps({"active_character": "char-a"})

    monkeypatch.setattr("core.sandbox.DataPaths.active_prompt_assets", lambda self: _FakeAPA())
    monkeypatch.setattr("core.memory.short_term.load", lambda uid, char_id=None: [])
    monkeypatch.setattr("channels.desktop_ws.get_connect_time", lambda: 0.0)
    _allow_dream_guard(monkeypatch)

    from admin.routers.chat import desktop_wake
    from tests.fixtures.session_grants import issue
    grant = issue(monkeypatch, owner="owner-ls", char="char-a")

    r1 = await desktop_wake({}, x_presence_session=grant.session_id)
    r2 = await desktop_wake({"last_seen": time.time() - 5}, x_presence_session=grant.session_id)

    assert r1.get("source") == "queued_autonomy_signal"
    assert r1.get("reply") is None
    assert r2 == {"reply": None, "source": "duplicate_wake"}
    assert len(store.load("owner-ls", "char-a")["pending_signals"]) == 1


# ── Test 8: dedupe_key composition check for both sources ────────────────────

def test_desktop_wake_dedupe_key_composition():
    """
    desktop_wake dedupe_key (no event_id) = desktop_wake:uid:char:desktop:wake:hash({}):bucket
    Verify all 7 components are present and last_seen is NOT embedded.
    """
    from core.perceive_event import PerceiveEvent, _make_dedupe_key

    ts = 1748000000.0  # fixed timestamp for reproducible bucket
    e = PerceiveEvent(
        source="desktop_wake", uid="testuid", channel="desktop", kind="wake",
        char_id="testchar", payload={}, created_at=ts,
    )
    key = _make_dedupe_key(e, "testchar")

    assert key.startswith("desktop_wake:testuid:testchar:desktop:wake:"), (
        f"key must start with source:uid:char:channel:kind: — got {key!r}"
    )
    parts = key.split(":")
    assert len(parts) == 7, f"auto-key must have 7 parts: {key!r}"
    # last part = time bucket (int of ts // 60)
    expected_bucket = str(int(ts // 60))
    assert parts[6] == expected_bucket, (
        f"last part must be time bucket {expected_bucket}, got {parts[6]!r}"
    )


def test_scheduler_dedupe_key_composition():
    """
    scheduler dedupe_key (no event_id) = scheduler:uid:char:system:scheduled:hash({"trigger_name":...}):bucket
    """
    from core.perceive_event import PerceiveEvent, _make_dedupe_key, _payload_hash

    ts = 1748000000.0
    e = PerceiveEvent(
        source="scheduler", uid="testuid", channel="system", kind="scheduled",
        char_id="testchar", payload={"trigger_name": "morning_greeting"}, created_at=ts,
    )
    key = _make_dedupe_key(e, "testchar")

    assert key.startswith("scheduler:testuid:testchar:system:scheduled:"), (
        f"key must start with source:uid:char:channel:kind: — got {key!r}"
    )
    parts = key.split(":")
    assert len(parts) == 7, f"auto-key must have 7 parts: {key!r}"
    expected_hash = _payload_hash({"trigger_name": "morning_greeting"})
    assert parts[5] == expected_hash, (
        f"hash part must match stable payload hash; got {parts[5]!r}, expected {expected_hash!r}"
    )
    # Same trigger → same hash (stability check)
    e2 = PerceiveEvent(
        source="scheduler", uid="testuid", channel="system", kind="scheduled",
        char_id="testchar", payload={"trigger_name": "morning_greeting"}, created_at=ts,
    )
    assert _make_dedupe_key(e2, "testchar") == key, "scheduler key must be stable for same trigger"
