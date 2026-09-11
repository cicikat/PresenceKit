"""
Prompt layer capture ring buffer (memory-only, never persisted to disk).

Stores the last RING_SIZE build() snapshots per uid so the admin panel can
inspect which layers were activated, their sizes, and which were pruned.
Also captures the LLM output for the same turn via update_llm_output().

Usage:
    from core.observe.prompt_capture import capture, get_snapshots, set_capture_origin

    # Before build_prompt() at each entry point (optional — defaults to {"origin":"user"}):
    set_capture_origin({"origin": "proactive", "trigger_name": "random_message", ...})

    # After build_prompt() in pipeline:
    capture(uid, messages, meta)

    # After run_llm() in pipeline/call sites:
    update_llm_output(uid, reply_text)

    # In the /observe/prompt-layers/{uid} endpoint:
    snaps = get_snapshots(uid)
"""

from collections import deque
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any

# Per-async-task origin metadata for the current build_prompt() call.
# Each caller sets this before invoking build_prompt(); capture() reads it.
# Default = user-originated turn so existing call sites need no changes.
_capture_origin: ContextVar[dict] = ContextVar(
    "_capture_origin", default={"origin": "user"}
)


def set_capture_origin(info: dict) -> None:
    """Set origin metadata for the upcoming capture() call in this async context.

    Call this immediately before pipeline.build_prompt() at each entry point.
    Proactive/scheduler callers set {"origin":"proactive","trigger_name":...,
    "seed_prompt":...,"search_query":...}.  Desktop callers set {"origin":"desktop"}.
    User QQ paths rely on the default {"origin":"user"}.
    """
    _capture_origin.set(info)

RING_SIZE = 10
SOFT_WARN = 15000
HARD_TRIGGER = 20000
PRUNE_TARGET = 18000

# per-uid ring:  uid → deque of snapshot dicts
_rings: dict[str, deque] = {}
_current_capture: ContextVar[Any] = ContextVar("_current_prompt_capture", default=None)


def capture(uid: str, messages: list[dict], meta: dict, *, _replace: dict | None = None) -> None:
    """Record one build() result into the ring buffer for uid."""
    if uid not in _rings:
        _rings[uid] = deque(maxlen=RING_SIZE)

    layers = []
    for i, msg in enumerate(messages):
        layer = msg.get("_layer", "unknown")
        chars = len(msg.get("content", ""))
        prov = msg.get("_provenance")
        if prov is None:
            # No explicit provenance — infer "always" for layers without conditions
            provenance = {"mode": "always"}
        else:
            # Preserve layer-specific selection/budget metadata instead of
            # flattening it to the older six-field subset.  This is an
            # in-memory admin-only capture, and builders must still avoid
            # putting raw private profile text in provenance.
            provenance = dict(prov) if isinstance(prov, dict) else {"mode": "always"}
            provenance.setdefault("mode", "always")
        layers.append({
            "layer": layer,
            "position": i,
            "chars": chars,
            "est_tokens": msg.get("_estimated_tokens", round(chars / 1.7, 1)),
            "budget_chars": msg.get("_budget_chars"),
            "budget_items": msg.get("_budget_items"),
            "over_char_budget": (
                msg.get("_budget_chars") is not None
                and chars > msg["_budget_chars"]
            ),
            "drop_priority": msg.get("_drop_priority"),
            "role": msg.get("role", "system"),
            "content": msg.get("content", ""),
            "provenance": provenance,
        })

    # token_estimate is preserved for existing callers, but it has always been
    # a character estimate.  New fields make the unit explicit.
    char_estimate = meta.get("char_estimate", sum(m["chars"] for m in layers))
    token_estimate = meta.get("token_estimate", char_estimate)
    estimated_tokens = meta.get("estimated_tokens", round(char_estimate / 1.7, 1))
    removed = meta.get("removed_layers", [])

    # Mark which layers were pruned
    pruned_set = set(removed)
    for lyr in layers:
        lyr["pruned"] = lyr["layer"] in pruned_set
        lyr["gated_in"] = not lyr["pruned"]

    # Compute total chars for percentage
    total_chars = sum(m["chars"] for m in layers) or 1

    for lyr in layers:
        lyr["pct"] = round(lyr["chars"] / total_chars * 100, 1)

    snap = {
        "uid": uid,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "origin": _capture_origin.get(),   # set by caller before build_prompt
        "token_estimate": token_estimate,
        "char_estimate": char_estimate,
        "estimated_tokens": estimated_tokens,
        "soft_warn_threshold": SOFT_WARN,
        "hard_trigger_threshold": HARD_TRIGGER,
        "prune_target": PRUNE_TARGET,
        "active_tags": meta.get("tags", []),
        "layers_activated": meta.get("layers_activated", []),
        "removed_layers": removed,
        "ablated_layers": meta.get("ablated_layers", []),
        "pruning_triggered": token_estimate > HARD_TRIGGER or bool(removed),
        "layers": layers,
        "llm_output": None,  # filled by update_llm_output after run_llm
    }
    if _replace is None:
        _rings[uid].append(snap)
    else:
        snap["captured_at"] = _replace["captured_at"]
        snap["origin"] = _replace["origin"]
        snap["llm_output"] = _replace["llm_output"]
        _replace.update(snap)
        snap = _replace
    _current_capture.set((uid, messages, dict(meta), snap))


def capture_injected_messages(before: list[dict], after: list[dict]) -> None:
    """Refresh only the snapshot belonging to this exact prompt, never another turn."""
    current = _current_capture.get()
    if current is None or current[1] is not before:
        return
    uid, _, meta, snap = current
    meta = {k: v for k, v in meta.items()
            if k not in {"token_estimate", "char_estimate", "estimated_tokens"}}
    meta["layers_activated"] = [m.get("_layer", "unknown") for m in after]
    capture(uid, after, meta, _replace=snap)


def update_llm_output(uid: str, reply: str) -> None:
    """Pair the LLM reply with the latest prompt snapshot for uid (in-place update)."""
    ring = _rings.get(uid)
    if ring:
        ring[-1]["llm_output"] = reply


def get_snapshots(uid: str) -> list[dict]:
    """Return snapshots for uid, newest last."""
    ring = _rings.get(uid)
    if ring is None:
        return []
    return list(ring)


def list_uids() -> list[str]:
    """Return all uids that have at least one snapshot."""
    return [uid for uid, ring in _rings.items() if ring]


def get_latest_proactive_by_trigger() -> dict[str, dict]:
    """Return the most recent proactive snapshot per trigger_name across all uids."""
    result: dict[str, dict] = {}
    for ring in _rings.values():
        for snap in ring:
            origin = snap.get("origin") or {}
            if origin.get("origin") != "proactive":
                continue
            tname = origin.get("trigger_name", "")
            if not tname:
                continue
            if tname not in result or snap["captured_at"] > result[tname]["captured_at"]:
                result[tname] = snap
    return result
