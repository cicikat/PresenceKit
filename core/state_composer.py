"""Shadow StatePacket: a content-free manifest of what each prompt layer gave and why.

Pure function, no IO.  It never changes the prompt; it only reads the fetched
context (``ctx``) and the build result and returns ids/metrics (no text).
Gate: ``state_composer.shadow`` (same semantics as ``event_shadow_recall``).
"""
from __future__ import annotations

from typing import Any

from core.config_loader import get_config
from core.memory.authority import authority_of
from core.memory.event_shadow_recall import enabled_for as _enabled_for

MAX_ITEM_IDS = 10
_CONFLICT_AUTHORITIES = {"user_stated_fact", "identity_authority", "topic_authority"}
REASONS = (
    "query_match", "time_intent", "long_term_intent", "always",
    "tag_gated", "query_free", "unknown",
)

_TAG_GATED = {
    "3.5_period", "3.6_watch", "3.8_activity", "3.8_growth_self", "6d_diary_context",
    "coplay_recall", "coplay_residue_soft_hint", "coplay_context", "6h_storyline",
    "6f_dream_afterglow", "dream_afterglow_soft_hint", "6g_dream_impression",
}
_QUERY_LAYERS = {"6b_event_search", "6b_memory_dossiers", "web_recall"}


def shadow_config() -> dict[str, Any]:
    try:
        raw = get_config().get("state_composer") or {}
        shadow = raw.get("shadow") if isinstance(raw, dict) else {}
    except Exception:
        shadow = {}
    result: dict[str, Any] = {"enabled": False, "uids": [], "char_ids": []}
    if isinstance(shadow, dict):
        result.update(shadow)
    return result


def enabled_for(uid: str, char_id: str) -> bool:
    return _enabled_for(uid, char_id, shadow_config())


def _ids(values: Any) -> list[str]:
    out: list[str] = []
    for v in values or []:
        s = str(v or "").strip()
        if s and s not in out:
            out.append(s)
    return out


def _episodic_reason(trace: dict, selected_matches: list[str]) -> str:
    if "keyword" in selected_matches or "semantic" in selected_matches:
        return "query_match"
    if "time" in selected_matches or (trace.get("time_intent") and selected_matches):
        return "time_intent"
    if "long_term_fill" in selected_matches or trace.get("long_term"):
        return "long_term_intent"
    return "unknown"


def _layer_reason(name: str, report: str, trace: dict, ep_matches: list[str]) -> str:
    if report == "6c_episodic_fallback":
        return "query_free"
    if name in ("6c_episodic", "9.5_episodic_top"):
        return _episodic_reason(trace, ep_matches)
    if name == "2.56_relationship_span":
        return "long_term_intent"
    if name in _QUERY_LAYERS:
        return "query_match"
    if name in _TAG_GATED:
        return "tag_gated"
    if name in ("mid_term", "6e_inner_diary"):
        return "always"
    from core.memory.authority import LAYER_AUTHORITY
    if name in LAYER_AUTHORITY:
        return "always"
    return "unknown"


def _layer_items(name: str, report: str, trace: dict) -> tuple[list[str], list[str]]:
    """Return (item_ids, event_ids) for a layer."""
    if name in ("6c_episodic", "9.5_episodic_top"):
        if report == "6c_episodic_fallback":
            return _ids(trace.get("fallback_ids")), _ids(trace.get("fallback_event_ids"))
        return _ids(trace.get("episodic_ids")), _ids(trace.get("episodic_event_ids"))
    if name == "6b_event_search":
        return _ids(trace.get("event_log_ids")), _ids(trace.get("event_log_event_ids"))
    if name == "6b_memory_dossiers":
        return _ids(trace.get("dossier_ids")), _ids(trace.get("dossier_event_ids"))
    return [], []


def compose_shadow_packet(ctx: dict, build_result: dict, *, query: str) -> dict:
    """Build the packet.  ``build_result`` = {"messages": [...], "debug_info": {...}}."""
    trace = (ctx or {}).get("_state_trace") or {}
    messages = build_result.get("messages") or []
    debug = build_result.get("debug_info") or {}
    removed = set(debug.get("removed_layers") or [])
    ablated = set(debug.get("ablated_layers") or [])
    ep_matches = list(trace.get("episodic_matches") or [])

    layers: list[dict] = []
    seen_order: set[tuple[str, str]] = set()
    event_owner: dict[str, set[str]] = {}
    for msg in messages:
        name = msg.get("_layer", "unknown")
        report = msg.get("_report_layer") or name
        if (name, report) in seen_order:
            # multi-message layers (history) collapse into one entry
            for entry in layers:
                if entry["layer"] == report:
                    entry["chars"] += len(msg.get("content") or "")
            continue
        seen_order.add((name, report))
        reg = authority_of(name)
        item_ids, event_ids = _layer_items(name, report, trace)
        for eid in event_ids:
            event_owner.setdefault(eid, set()).add(report)
        layers.append({
            "layer": report,
            "source": reg.get("source", "unknown"),
            "authority": reg["authority"],
            "chars": len(msg.get("content") or ""),
            "dropped": name in removed or name in ablated,
            "reason": _layer_reason(name, report, trace, ep_matches),
            "item_ids": (item_ids or event_ids)[:MAX_ITEM_IDS],
            "_lineage": reg.get("lineage", "none"),
        })
    for name in sorted((removed | ablated) - {e["layer"] for e in layers}):
        reg = authority_of(name)
        layers.append({
            "layer": name, "source": reg.get("source", "unknown"),
            "authority": reg["authority"], "chars": 0, "dropped": True,
            "reason": "unknown", "item_ids": [], "_lineage": reg.get("lineage", "none"),
        })

    by_authority: dict[str, int] = {}
    for entry in layers:
        by_authority[entry["authority"]] = by_authority.get(entry["authority"], 0) + entry["chars"]
    untraced = [
        e["layer"] for e in layers
        if not e["dropped"] and e["chars"] and not e["item_ids"]
        and e["authority"] not in ("system", "character_self")
    ]
    conflict_layers = [
        e["layer"] for e in layers
        if not e["dropped"] and e["chars"] and e["authority"] in _CONFLICT_AUTHORITIES
    ]
    for entry in layers:
        entry.pop("_lineage", None)
    return {
        "layers": layers,
        "layers_total": len(layers),
        "query_free_layers": [e["layer"] for e in layers if e["reason"] == "query_free"],
        "duplicate_event_ids": sum(1 for owners in event_owner.values() if len(owners) >= 2),
        "untraced_sources": untraced,
        "by_authority": by_authority,
        "conflict_candidates": conflict_layers if len(conflict_layers) >= 2 else [],
    }


def packet_dir(uid: str, char_id: str):
    """Sibling of the recall_trace dir: ``.../state_packet`` (JSONL per day)."""
    from core.memory.path_resolver import resolve_path
    from core.memory.scope import MemoryScope

    scope = MemoryScope.reality_scope(uid, char_id)
    return resolve_path(scope, "recall_trace").parent / "state_packet"


def record_shadow_packet(uid: str, char_id: str, ctx: dict, messages: list,
                         debug_info: dict, *, query: str) -> None:
    """Gate, compose and append one packet.  Fail-open; never raises."""
    import json
    import logging
    import time
    from datetime import datetime

    log = logging.getLogger(__name__)
    try:
        if not enabled_for(uid, char_id):
            return
        started = time.perf_counter()
        packet = compose_shadow_packet(
            ctx, {"messages": messages, "debug_info": debug_info}, query=query,
        )
        packet.update({
            "ts": datetime.now().isoformat(timespec="seconds"),
            "uid": uid,
            "char_id": char_id,
        })
        directory = packet_dir(uid, char_id)
        directory.mkdir(parents=True, exist_ok=True)
        line = json.dumps(packet, ensure_ascii=False, default=str)
        with open(directory / f"{datetime.now():%Y-%m-%d}.jsonl", "a", encoding="utf-8") as f:
            f.write(line + "\n")
        elapsed_ms = (time.perf_counter() - started) * 1000
        if elapsed_ms > 5:
            log.warning("[state_composer] shadow packet took %.1fms", elapsed_ms)
    except Exception as exc:
        log.warning("[state_composer] shadow packet failed uid=%s: %s", uid, exc)
