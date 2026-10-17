"""Bounded, single-use desktop collection leases; never samples the backend OS."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import time
import uuid
from typing import Any

from core.config_loader import get_config, get_config_path
from core.deployment_capabilities import is_remote_server
from core.coplay import session

TTL = 20
MAX_IMAGE_BYTES = 1024 * 1024
_rows: dict[tuple[str, str], dict[str, Any]] = {}


def moment_scope(uid: str, char_id: str) -> str:
    return f"{uid}:{char_id}" if is_remote_server() else uid


def _policy(uid: str, char_id: str) -> tuple[str, dict, dict]:
    cfg = get_config()
    block = cfg.get("coplay") or {}
    state = session.read_state(uid, char_id=char_id)
    try:
        stamp = get_config_path().stat().st_mtime_ns
    except OSError:
        stamp = 0
    from core.sandbox import get_paths
    try:
        auth_stamp = get_paths().auth_tokens_file().stat().st_mtime_ns
    except OSError:
        auth_stamp = 0
    fingerprint = hashlib.sha256(json.dumps(
        [block, cfg.get("deployment"), cfg.get("screen_observation"),
         cfg.get("client_channels"), state.get("collection_epoch"), stamp, auth_stamp],
        sort_keys=True, default=str,
    ).encode()).hexdigest()
    return fingerprint, block, state


def _active(uid: str, char_id: str) -> bool:
    from core.pipeline_registry import get
    pipeline = get()
    return bool((get_config().get("client_channels") or {}).get("desktop", True)
                and pipeline and pipeline._active_character_id == char_id
                and str((get_config().get("scheduler") or {}).get("owner_id", "")) == uid)


def revoke(uid: str, char_id: str) -> None:
    row = _rows.pop((uid, char_id), {})
    task = row.get("analysis")
    if task and not task.done():
        task.cancel()
    from core.coplay import observer
    observer.clear_moments_for_test(f"{uid}:{char_id}")
    observer.reset_diff_state_for_test(f"{uid}:{char_id}")


def projection(uid: str, char_id: str) -> dict:
    row = _rows.get((uid, char_id), {})
    now = time.monotonic()
    age = now - row.get("reported", 0)
    online = bool(row.get("seen") and now - row["seen"] <= TTL)
    current = not is_remote_server() or (_active(uid, char_id) and row.get("revision") == _policy(uid, char_id)[0])
    fresh = bool(online and current and row.get("reported") and age <= TTL)
    return {"source": "desktop" if is_remote_server() else "backend_local",
            "fresh": fresh, "online": online,
            "age_seconds": round(age, 1) if row.get("reported") else None,
            "blocked_reason": row.get("blocked_reason") if fresh else "device_offline_or_stale",
            "metadata_authorized": bool(row.get("metadata_authorized") and fresh),
            "frame_authorized": bool(row.get("frame_authorized") and fresh),
            "lease_pending": bool(row.get("lease") and time.monotonic() < row.get("deadline", 0)),
            "accepted": row.get("accepted", 0), "rejected": row.get("rejected", 0)}


def poll(uid: str, char_id: str, identity: str, client_id: str,
         metadata_available: bool, frame_available: bool) -> dict:
    revision, block, state = _policy(uid, char_id)
    if not is_remote_server() or not block.get("enabled", True) or not _active(uid, char_id):
        revoke(uid, char_id)
        return {"enabled": False, "reason": "local_or_disabled"}
    if state.get("status") not in {"armed", "active"}:
        revoke(uid, char_id)
        return {"enabled": False, "reason": "session_not_collecting"}
    key = uid, char_id
    now = time.monotonic()
    for old_key, old_row in list(_rows.items()):
        if old_key != key and (now - old_row.get("seen", 0) > 2 * TTL or len(_rows) >= 64):
            revoke(*old_key)
    row = _rows.setdefault(key, {})
    if row.get("identity") != identity or row.get("client_id") != client_id:
        if row.get("lease") and now < row.get("deadline", 0):
            return {"enabled": False, "reason": "another_device_lease"}
        revoke(uid, char_id)
        row = _rows.setdefault(key, {})
    row.update(identity=identity, client_id=client_id, seen=now,
               metadata_authorized=metadata_available,
               frame_authorized=bool(frame_available and (get_config().get("screen_observation") or {}).get("enabled", False)),
               blocked_reason=None if metadata_available else "local_consent_or_locked")
    if not metadata_available:
        row.pop("lease", None)
        return {"enabled": False, "reason": row["blocked_reason"]}
    if row.get("analysis") and not row["analysis"].done():
        return {"enabled": False, "reason": "analysis_busy"}
    if row.get("lease") and now < row.get("deadline", 0) and row.get("revision") == revision:
        return {"enabled": False, "reason": "lease_pending"}
    if now - row.get("last_issued", 0) < max(0, float(block.get("screenshot_interval", 8))):
        return {"enabled": False, "reason": "sample_cooldown"}
    lease = uuid.uuid4().hex
    row.update(lease=lease, revision=revision, issued=time.time(), deadline=now + TTL, last_issued=now)
    games = []
    for item in (block.get("game_whitelist") or [])[:100]:
        if isinstance(item, dict) and item.get("process_name"):
            process = str(item["process_name"]).strip().lower().removesuffix(".exe")
            games.append({"game_id": process, "name": str(item.get("name") or process)[:200],
                          "process_name": process, "save_dir": str(item.get("save_dir") or "")})
    return {"enabled": True, "protocol_version": 1, "lease_id": lease,
            "char_id": char_id, "revision": revision, "ttl_seconds": TTL,
            "server_time": row["issued"], "max_image_bytes": MAX_IMAGE_BYTES,
            "frame_allowed": row["frame_authorized"], "games": games,
            "steam_library_paths": (block.get("steam_library_paths") or [])[:20]}


def accept(uid: str, char_id: str, identity: str, body: dict) -> dict:
    row = _rows.get((uid, char_id), {})
    revision, block, state = _policy(uid, char_id)
    valid = (is_remote_server() and _active(uid, char_id) and block.get("enabled", True)
             and row.get("lease") == body["lease_id"] and row.get("identity") == identity
             and row.get("client_id") == body["client_id"] and row.get("revision") == revision
             and body["revision"] == revision and body["char_id"] == char_id
             and time.monotonic() < row.get("deadline", 0)
             and row.get("issued", float("inf")) - 1 <= body["captured_at"] <= time.time() + 5
             and time.time() - body["captured_at"] <= TTL
             and state.get("status") in {"armed", "active"})
    if not valid:
        row["rejected"] = row.get("rejected", 0) + 1
        return {"accepted": False, "reason": "expired_or_mismatched_lease"}
    # Consume before image decoding or awaiting: duplicate submissions cannot execute twice.
    row.pop("lease", None)
    if body["status"] != "ok":
        row["blocked_reason"] = body["status"]
        return {"accepted": True, "reason": body["status"]}
    game_id = body.get("game_id") or ""
    allowed = {str(g.get("process_name", "")).strip().lower().removesuffix(".exe"):
               str(g.get("name") or "") for g in block.get("game_whitelist", [])}
    steam = game_id.startswith("steam:") and game_id[6:].isdigit() and len(game_id) <= 32
    if game_id and game_id not in allowed and not steam:
        return {"accepted": False, "reason": "unknown_game"}
    frame = None
    if body.get("image_base64"):
        if not row.get("frame_authorized") or not game_id or not body.get("foreground"):
            return {"accepted": False, "reason": "frame_not_authorized"}
        try:
            from PIL import Image
            frame = base64.b64decode(body["image_base64"], validate=True)
            if len(frame) > MAX_IMAGE_BYTES:
                raise ValueError("oversized frame")
            with Image.open(io.BytesIO(frame)) as image:
                if image.format != "JPEG" or max(image.size) > 1280 or min(image.size) < 1:
                    raise ValueError("invalid frame")
                image.verify()
        except Exception:
            return {"accepted": False, "reason": "invalid_frame"}
    if state["status"] == "armed" and game_id:
        name = allowed.get(game_id) or body.get("game_name") or game_id
        state = session.enter_active(uid, game_id=game_id, game_name=name[:200], char_id=char_id)
    elif state["status"] == "active" and game_id != state.get("game_id"):
        session.enter_closing(uid, char_id=char_id)
        frame = None
    report_at = time.monotonic()
    row.update(accepted=row.get("accepted", 0) + 1, blocked_reason=None, seen=report_at, reported=report_at)
    if game_id and state.get("game_id") == game_id and (frame or body.get("save_changed")):
        epoch = state.get("collection_epoch")
        def still_valid() -> bool:
            current_revision, _, current = _policy(uid, char_id)
            return (_active(uid, char_id) and current_revision == revision
                    and current.get("status") == "active" and current.get("game_id") == game_id
                    and current.get("collection_epoch") == epoch
                    and time.monotonic() - report_at <= TTL
                    and bool(row.get("metadata_authorized"))
                    and (not frame or bool(row.get("frame_authorized"))))
        from core.coplay.observer import analyze_frame
        row["analysis"] = asyncio.create_task(analyze_frame(
            uid, char_id=char_id, frame=frame, save_changed=body.get("save_changed", False), valid=still_valid))
    return {"accepted": True, "status": session.read_state(uid, char_id=char_id)["status"]}
