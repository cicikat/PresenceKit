"""Bounded, read-only Reality evidence queries. References never contain paths."""
from __future__ import annotations

import hashlib
import json
import time

from core.sandbox import get_paths
from core.activity import activity_store, store, transcript

REALITY_TYPES = ("reading", "gomoku", "chess", "minecraft")


def revision(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    default=str).encode("utf-8")).hexdigest()[:24]


def session(uid: str, char_id: str, activity_type: str, session_id: str):
    if activity_type not in REALITY_TYPES:
        raise ValueError("unsupported Reality activity")
    value = (activity_store.load_session(char_id, uid, session_id)
             if activity_type == "reading" else
             store.load_session(char_id, uid, activity_type, session_id))
    if value is None or (value.uid, value.char_id, value.session_id) != (uid, char_id, session_id):
        raise LookupError("source unavailable")
    return value


def list_sessions(uid: str, char_id: str, *, offset: int = 0, limit: int = 20,
                  active_only: bool = False) -> dict:
    if offset < 0 or not 1 <= limit <= 100:
        raise ValueError("invalid page")
    values = []
    for kind in REALITY_TYPES:
        root = (get_paths().reading_sessions_root(char_id=char_id, uid=uid)
                if kind == "reading" else
                get_paths().activity_sessions_root(char_id=char_id, uid=uid, activity_type=kind))
        if not root.exists():
            continue
        for directory in root.iterdir():
            if not directory.is_dir():
                continue
            try:
                value = session(uid, char_id, kind, directory.name)
                stale = kind != "reading" and store._session_is_stale(value)
                if active_only and (value.status != "active" or stale):
                    continue
                raw = value.to_dict()
                values.append({"activity_id": kind, "session_id": value.session_id,
                               "uid": uid, "char_id": char_id, "realm": "reality",
                               "status": "expired" if stale and value.status == "active" else value.status,
                               "created_at": value.created_at, "updated_at": value.updated_at,
                               "revision": revision(raw),
                               "source_ref": f"activity:{kind}:{value.session_id}:state"})
            except (ValueError, LookupError):
                continue
    values.sort(key=lambda item: (item["updated_at"], item["session_id"]), reverse=True)
    return {"items": values[offset:offset + limit], "total": len(values),
            "next_offset": offset + limit if offset + limit < len(values) else None}


def read(uid: str, char_id: str, source_ref: str, *, offset: int = 0,
         limit: int = 20, text_offset: int = 0, text_limit: int = 4000,
         expected_revision: str = "", keyword: str = "", since: str = "",
         until: str = "") -> dict:
    if offset < 0 or text_offset < 0 or not 1 <= limit <= 100 or not 1 <= text_limit <= 12000:
        raise ValueError("invalid page")
    parts = source_ref.split(":")
    if len(parts) not in (4, 5) or parts[0] != "activity":
        raise ValueError("invalid source reference")
    _, kind, sid, section, *number = parts
    value = session(uid, char_id, kind, sid)
    if section == "state":
        entries = [value.to_dict()]
    elif section == "moves":
        entries = list(getattr(value, "state", {}).get("move_history", []))
    elif section == "dialogue":
        entries = []
        path = transcript._path(char_id, uid, kind, sid)
        if path.exists():
            with path.open(encoding="utf-8") as stream:
                for index, line in enumerate(stream):
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    entry = {**entry, "source_index": index,
                             "source_ref": f"activity:{kind}:{sid}:entry:{index}"}
                    entries.append(entry)
    elif section == "entry" and number:
        # Exact references resolve from original lines, even outside the recent page.
        path = transcript._path(char_id, uid, kind, sid)
        if not path.exists():
            raise LookupError("source unavailable")
        entries = []
        with path.open(encoding="utf-8") as stream:
            for index, line in enumerate(stream):
                if index == int(number[0]):
                    entries = [json.loads(line)]
                    break
        if not entries:
            raise LookupError("source unavailable")
    elif section == "page" and number and kind == "reading":
        text = activity_store.load_page(char_id, uid, sid, int(number[0]))
        if text is None:
            raise LookupError("source unavailable")
        entries = [{"type": "external_content", "text": text}]
    else:
        raise ValueError("unsupported section")
    current = revision(entries)
    if expected_revision and current != expected_revision:
        raise ValueError("source revision changed; restart pagination")
    filtered = [entry for entry in entries
                if (not keyword or keyword.casefold() in json.dumps(entry, ensure_ascii=False).casefold())
                and (not since or str(entry.get("ts", "")) >= since)
                and (not until or str(entry.get("ts", "")) <= until)] if section in ("dialogue", "entry") else entries
    page = []
    for item in filtered[offset:offset + limit]:
        item = dict(item) if isinstance(item, dict) else {"value": item}
        if "text" in item:
            text = str(item["text"])
            item.update(text=text[text_offset:text_offset + text_limit], text_length=len(text),
                        next_text_offset=text_offset + text_limit if text_offset + text_limit < len(text) else None)
        page.append(item)
    return {"source_ref": source_ref, "revision": current, "items": page,
            "total": len(filtered), "next_offset": offset + limit if offset + limit < len(filtered) else None}


def tasks(uid: str, char_id: str) -> list[dict]:
    from core.agent_runtime import task_store
    values = []
    for raw in task_store.load_unlocked(uid, char_id)["tasks"]:
        if raw.get("uid") != uid or raw.get("char_id") != char_id or raw.get("realm", "reality") != "reality":
            continue
        status = raw.get("status")
        if status == "running" and (raw.get("lease_owner_instance") != task_store.process_instance_id()
                                    or float(raw.get("lease_until") or 0) <= time.time()):
            status = "interrupted"
        values.append({key: raw.get(key) for key in ("task_id", "capability", "created_at", "updated_at")}
                      | {"status": status, "realm": "reality", "revision": revision(raw)})
    return values
