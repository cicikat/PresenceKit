"""Bounded read-only QZone producer; decisions and delivery belong to autonomy."""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from threading import RLock

from core import qzone_service as service
from core.autonomy import store
from core.autonomy.models import Signal, ActionMode
from core.safe_write import safe_write_json
from core.sandbox import get_paths

TTL = 20 * 60
_lock = RLock()
_poll_lock = asyncio.Lock()


def revision(cfg=None):
    cfg = cfg or service.settings()
    # Every source/permission change invalidates queued observations.
    values = [cfg.enabled, cfg.events_enabled, cfg.replies_enabled, cfg.account_id,
              cfg.base_url, cfg.allowed_char_ids, service.watched_users(cfg)]
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()[:24]


def _load(uid, char_id):
    path = get_paths().qzone_inbox(uid, char_id=char_id)
    if not path.exists():
        return {}
    # Corrupt source state must fail closed, rather than replaying history.
    result = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(result, dict):
        raise ValueError("invalid QZone inbox")
    return result


def _save(uid, char_id, state):
    path = get_paths().qzone_inbox(uid, char_id=char_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    state["seen"] = state.get("seen", [])[-2000:]
    state["baselines"] = state.get("baselines", [])[-128:]
    if not safe_write_json(path, state):
        raise OSError("QZone inbox persistence failed")


def _save_scan(uid, char_id, state):
    # A hot update may have revoked this scan while its REST call awaited.
    with _lock:
        if _load(uid, char_id).get("epoch") != state.get("epoch"):
            return
        _save(uid, char_id, state)


def observe(uid, char_id):
    with _lock:
        try:
            data = _load(uid, char_id)
            return {key: data.get(key) for key in (
                "last_poll_at", "last_success_at", "last_code", "queued", "duplicates",
                "baseline_count", "partial", "started_at")}
        except (ValueError, OSError):
            return {"last_code": "inbox_unavailable"}


def invalidate(uid, char_id):
    """Make both pending and already claimed observations stale on hot update."""
    store.discard_pending_signals_by_source(uid, char_id, sources={"qzone"})
    with _lock:
        data = _load(uid, char_id)
        data.update(revision="", epoch=uuid.uuid4().hex, last_poll_at=0,
                    last_code="settings_changed")
        _save(uid, char_id, data)


def source_active(uid, char_id, signals):
    relevant = [s for s in signals if isinstance(s, dict) and s.get("source") == "qzone"]
    if not relevant:
        return True
    cfg = service.settings()
    try:
        epoch = _load(uid, char_id).get("epoch")
    except (ValueError, OSError):
        return False
    return bool(cfg.events_enabled and service.allowed(uid, char_id) and epoch and all(
        isinstance(fact, dict) and fact.get("source_revision") == revision(cfg)
        and fact.get("source_epoch") == epoch
        for signal in relevant for fact in signal.get("evidence", [])))


def _hash(value):
    return hashlib.sha256(str(value).encode()).hexdigest()[:32]


def _rows(data, *keys):
    if not isinstance(data, dict):
        return []
    for key in keys:
        if isinstance(data.get(key), list):
            return [item for item in data[key][:20] if isinstance(item, dict)]
    return []


def _timestamp(item):
    try:
        return float(item.get("createdTime") or item.get("created_time") or 0)
    except (TypeError, ValueError):
        return 0.0


def _receipt(state, *, group, identity, fact, now, timestamp, uid, char_id, warm=False):
    key = _hash(identity)
    if key in state["seen"]:
        state["duplicates"] += 1
        return
    # Remember ignored history and our own actions, too, to avoid feedback.
    state["seen"].append(key)
    if (group not in state["baselines"] and not warm) or (timestamp and (timestamp < now - TTL or timestamp > now + 60)):
        return
    if timestamp and timestamp < state["started_at"]:
        return
    if not fact or not service.settings().events_enabled or revision() != state["revision"]:
        return
    if _load(uid, char_id).get("epoch") != state["epoch"]:
        return
    from core.autonomy.effective_state import autonomy_enabled
    if not autonomy_enabled(uid, char_id, store.load(uid, char_id)):
        state["last_code"] = "autonomy_disabled"
        return
    signal = Signal(source="qzone", evidence=[{**fact, "source_revision": state["revision"], "source_epoch": state["epoch"],
                    "trust": "external_untrusted", "observed_at": now}],
                    reason="QQ 空间有新的说说或收到的评论/回复，可选择互动、对话或静默。",
                    signal_id="qzone:" + key, created_at=now, expires_at=now + TTL,
                    priority=0.35, confidence=1.0, action_mode=ActionMode.USE_TOOLS.value)
    queued, _ = store.enqueue_signal(uid, char_id, signal, dedupe_key=signal.id)
    state["queued"] += int(queued)


async def tick(uid, char_id):
    """One scheduler-owned scan; no new worker, LLM or send outlet."""
    if _poll_lock.locked():
        return
    async with _poll_lock:
        await _tick(uid, char_id)


async def _tick(uid, char_id):
    cfg = service.settings()
    if not cfg.events_enabled or not service.allowed(uid, char_id):
        store.discard_pending_signals_by_source(uid, char_id, sources={"qzone"})
        return
    now = time.time()
    with _lock:
        state = _load(uid, char_id)
        if state.get("revision") != revision(cfg):
            store.discard_pending_signals_by_source(uid, char_id, sources={"qzone"})
            state = {"revision": revision(cfg), "epoch": uuid.uuid4().hex, "started_at": now, "seen": [], "baselines": [],
                     "queued": 0, "duplicates": 0, "cursor": 0}
        if now - state.get("last_poll_at", 0) < cfg.poll_interval_seconds:
            return
        state.update(last_poll_at=now, last_code="scanning", partial=False)
        _save(uid, char_id, state)
    watched = service.watched_users(cfg)
    # Rotate authors fairly; at most one watched author plus the login per scan.
    selected = [watched[state["cursor"] % len(watched)]] if watched else []
    authors = list(dict.fromkeys(selected + ([cfg.account_id] if cfg.replies_enabled else [])))
    try:
        for author in authors:
            result = await service.call("get_emotion_list", {
                "user_id": author, "num": 5, "pos": 0, "max_pages": 1,
                "include_image_data": False}, uid=uid, char_id=char_id)
            if not result.get("ok"):
                state.update(last_code=result.get("code", "scan_failed"), partial=True)
                continue
            posts = _rows(result.get("data"), "msglist")[:5]
            group = _hash("posts:" + author)
            for post in posts:
                tid = str(post.get("tid") or "")[:128]
                if not tid:
                    continue
                created = _timestamp(post)
                if author in watched and author != cfg.account_id:
                    _receipt(state, group=group, identity=f"post:{author}:{tid}",
                             fact={"kind": "post", "author_id": author, "post_id": tid,
                                   "abstime": created, "content": str(post.get("content") or "")[:2000]},
                             now=now, timestamp=created, uid=uid, char_id=char_id)
                if not cfg.replies_enabled:
                    continue
                cgroup = _hash(f"comments:{author}:{tid}")
                # An explicit zero is a valid empty baseline; avoid expensive
                # best-effort HTML fallback for posts with no comments.
                if post.get("cmtnum") in (0, "0"):
                    if cgroup not in state["baselines"]:
                        state["baselines"].append(cgroup)
                    continue
                comments = await service.call("get_comment_list", {
                    "user_id": author, "tid": tid, "num": 20, "pos": 0}, uid=uid, char_id=char_id)
                data = comments.get("data") or {}
                if not comments.get("ok") or data.get("availability") == "not_embedded":
                    state.update(partial=True, last_code=comments.get("code") or "comments_unavailable")
                    continue
                if data.get("availability") == "available_partial":
                    state.update(partial=True, last_code="comments_partial")
                for comment in _rows(data, "commentlist", "comments", "comment_list"):
                    candidates = [(comment, "")]
                    candidates += [(reply, str(comment.get("uin") or "")) for reply in (comment.get("replies") or [])[:20] if isinstance(reply, dict)]
                    for row, parent_author in candidates:
                        sender = str(row.get("uin") or "")
                        cid = str(row.get("commentId") or "")[:128]
                        mention = row.get("replyToMention")
                        reply_to = str(row.get("replyToUin") or (mention.get("uin") if isinstance(mention, dict) else "") or parent_author)
                        received = sender and sender != cfg.account_id and (author == cfg.account_id or reply_to == cfg.account_id)
                        fact = {"kind": "reply", "author_id": author, "post_id": tid,
                                "comment_id": cid, "sender_id": sender,
                                "reply_to_id": reply_to, "content": str(row.get("content") or "")[:2000]}
                        identity = f"comment:{author}:{tid}:{sender}:{cid or _hash([_timestamp(row), row.get('content')])}"
                        _receipt(state, group=cgroup, identity=identity, fact=fact if received else None,
                                 now=now, timestamp=_timestamp(row), uid=uid, char_id=char_id,
                                 warm=created > state["started_at"])
                if cgroup not in state["baselines"]:
                    state["baselines"].append(cgroup)
                # Save each successful comment baseline even if a later call stalls.
                _save_scan(uid, char_id, state)
            if group not in state["baselines"]:
                state["baselines"].append(group)
        state["cursor"] += 1
        state["last_success_at"] = time.time()
        if state["last_code"] == "scanning":
            state["last_code"] = "ready" if watched else "watch_binding_missing"
    finally:
        state["baseline_count"] = len(state["baselines"])
        if state["last_code"] == "scanning":
            state.update(last_code="scan_interrupted", partial=True)
        _save_scan(uid, char_id, state)
