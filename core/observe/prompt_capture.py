"""
Prompt layer capture ring buffer (memory-only, never persisted to disk).

Stores the last RING_SIZE build() snapshots per uid so the admin panel can
inspect which layers were activated, their sizes, and which were pruned.
Also captures the LLM output for the same turn via update_llm_output().

Usage:
    from core.observe.prompt_capture import capture, get_snapshots

    # After build_prompt() in pipeline:
    capture(uid, messages, meta)

    # After run_llm() in pipeline/call sites:
    update_llm_output(uid, reply_text)

    # In the /observe/prompt-layers/{uid} endpoint:
    snaps = get_snapshots(uid)
"""

from collections import deque
from datetime import datetime, timezone
from typing import Any

RING_SIZE = 5
SOFT_WARN = 15000
HARD_TRIGGER = 20000
PRUNE_TARGET = 18000

# per-uid ring:  uid → deque of snapshot dicts
_rings: dict[str, deque] = {}


def capture(uid: str, messages: list[dict], meta: dict) -> None:
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
            provenance = {
                "mode": prov.get("mode", "always"),
                "triggers_checked": prov.get("triggers_checked", []),
                "matched_tags": prov.get("matched_tags", []),
                "rag_query": prov.get("rag_query", ""),
            }
        layers.append({
            "layer": layer,
            "position": i,
            "chars": chars,
            "est_tokens": round(chars / 1.7, 1),
            "drop_priority": msg.get("_drop_priority"),
            "role": msg.get("role", "system"),
            "content": msg.get("content", ""),
            "provenance": provenance,
        })

    token_estimate = meta.get("token_estimate", sum(m["chars"] for m in layers))
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
        "token_estimate": token_estimate,
        "soft_warn_threshold": SOFT_WARN,
        "hard_trigger_threshold": HARD_TRIGGER,
        "prune_target": PRUNE_TARGET,
        "active_tags": meta.get("tags", []),
        "layers_activated": meta.get("layers_activated", []),
        "removed_layers": removed,
        "pruning_triggered": token_estimate > HARD_TRIGGER or bool(removed),
        "layers": layers,
        "llm_output": None,  # filled by update_llm_output after run_llm
    }
    _rings[uid].append(snap)


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
