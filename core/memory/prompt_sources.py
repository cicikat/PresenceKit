"""Bounded references to actual evidence, never synthesized from derived IDs."""
import json


def evidence_hint(event_ids=None, *, uid="", char_id="", turn_id="") -> str:
    ids = [value for value in (event_ids or [])
           if isinstance(value, str) and 0 < len(value) <= 256]
    if not ids and uid and char_id and turn_id:
        from core.memory.event_store import event_ids_for_turn
        from core.memory.scope import MemoryScope
        ids = event_ids_for_turn(MemoryScope.reality_scope(str(uid), char_id), turn_id)
    ids = list(dict.fromkeys(ids))[:3]
    if not ids:
        return ""
    return "（原始证据event_id=" + json.dumps(ids, ensure_ascii=False) + "）"


def activity_hint(refs=None) -> str:
    refs = [ref for ref in (refs or []) if isinstance(ref, str)
            and ref.startswith("activity:") and len(ref) <= 256]
    return ("（活动原记录=" + json.dumps(list(dict.fromkeys(refs))[:3], ensure_ascii=False)
            + "；用 query_activity_records(source_ref=记录引用)回读）") if refs else ""
