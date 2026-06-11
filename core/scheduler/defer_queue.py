"""
Deferred proposal queue for the scheduler.

Tracks proposals that were deferred (active_window_behavior="defer") because the
user was active.  Provides expiry-aware release and force_send logic driven by
TriggerPolicy.max_defer_age_secs / on_defer_expire.

Lifecycle: in-memory only.  Queue is cleared on process restart.
Limitation: if the bot restarts while items are deferred, those items will be
re-deferred from scratch (enqueue_ts resets to now).  This is acceptable because
all current defer triggers have max_defer_age_secs short enough (10 min – 4 h)
relative to typical uptime that a restart-induced reset is operationally harmless.
"""

from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass
class DeferredItem:
    trigger_name: str
    uid: str
    enqueue_ts: float


# {(uid, trigger_name): DeferredItem}
_defer_queue: dict[tuple[str, str], DeferredItem] = {}


def enqueue_defer(uid: str, trigger_name: str) -> None:
    """Record that a proposal was deferred.

    Idempotent: enqueue_ts is only set on the FIRST deferral call; subsequent
    calls for the same (uid, trigger_name) pair are no-ops so the age keeps
    accumulating from the original deferral moment.
    """
    key = (uid, trigger_name)
    if key not in _defer_queue:
        _defer_queue[key] = DeferredItem(
            trigger_name=trigger_name,
            uid=uid,
            enqueue_ts=time.time(),
        )


def release_defer(uid: str, trigger_name: str) -> None:
    """Remove a deferred item (called when the trigger is finally sent or explicitly dropped)."""
    _defer_queue.pop((uid, trigger_name), None)


def scan_expired(uid: str, now: float | None = None) -> tuple[frozenset[str], frozenset[str]]:
    """Scan deferred queue for *uid* and handle expired items.

    Returns ``(force_send_names, dropped_names)`` where:

    * ``force_send_names`` — triggers whose age > max_defer_age_secs and
      on_defer_expire == "force_send".  Caller should bypass active_window
      filter for these.
    * ``dropped_names`` — triggers whose age > max_defer_age_secs and
      on_defer_expire == "drop".  Already removed from the queue.

    Expired items are removed from the queue regardless of outcome.
    Items with max_defer_age_secs == 0 are treated as "no expiry" and skipped.
    """
    from core.scheduler.policy import POLICY_TABLE

    now_ts = now if now is not None else time.time()
    force_send: set[str] = set()
    dropped: set[str] = set()
    to_remove: list[tuple[str, str]] = []

    for (defer_uid, trigger_name), item in _defer_queue.items():
        if defer_uid != uid:
            continue
        policy = POLICY_TABLE.get(trigger_name)
        if not policy or policy.active_window_behavior != "defer":
            # Stale entry (policy changed or trigger unregistered) — clean up.
            to_remove.append((defer_uid, trigger_name))
            continue
        if policy.max_defer_age_secs <= 0:
            continue  # no expiry configured
        age = now_ts - item.enqueue_ts
        if age > policy.max_defer_age_secs:
            if policy.on_defer_expire == "force_send":
                force_send.add(trigger_name)
            else:
                dropped.add(trigger_name)
            to_remove.append((defer_uid, trigger_name))

    for key in to_remove:
        _defer_queue.pop(key, None)

    return frozenset(force_send), frozenset(dropped)


def get_queue_snapshot(uid: str | None = None) -> list[dict]:
    """Return an observable snapshot of the current defer queue.

    If *uid* is None, all entries are returned.  Each dict contains:
    ``uid``, ``trigger_name``, ``enqueue_ts``, ``age_secs``.
    """
    now_ts = time.time()
    result = []
    for (defer_uid, trigger_name), item in _defer_queue.items():
        if uid is not None and defer_uid != uid:
            continue
        result.append({
            "uid": item.uid,
            "trigger_name": item.trigger_name,
            "enqueue_ts": item.enqueue_ts,
            "age_secs": now_ts - item.enqueue_ts,
        })
    return result


def clear_uid(uid: str) -> None:
    """Remove all deferred items for *uid*.  Used in tests."""
    to_remove = [k for k in _defer_queue if k[0] == uid]
    for k in to_remove:
        del _defer_queue[k]


def clear_all() -> None:
    """Remove all deferred items.  Used in tests."""
    _defer_queue.clear()
