"""Consented, debounced device facts for autonomy; no human-state inference."""
from collections import OrderedDict
from hashlib import sha256
import time

from admin.token_registry import find_by_hash
from core.autonomy import store

KINDS = ("light", "motion", "usage")
DEFAULT_POLICY = {"enabled": False, "light": True, "motion": True, "usage": True, "revision": 0}
_detectors = OrderedDict()
_latest = {}


def merge_policy(current: dict, patch: dict) -> dict:
    if not isinstance(patch, dict) or set(patch) - set(KINDS) - {"enabled"} or any(type(value) is not bool for value in patch.values()):
        raise ValueError("phone ambient policy accepts only boolean enabled/light/motion/usage")
    result = {**DEFAULT_POLICY, **current}
    if any(result[key] != value for key, value in patch.items()):
        result.update(patch)
        result["revision"] += 1
    return result


def configure(uid: str, char_id: str, patch: dict) -> dict:
    with store._scope_lock(uid, char_id):
        state = store._load_unlocked(uid, char_id)
        policy = merge_policy(state["config"].get("phone_ambient", {}), patch)
        state["config"]["phone_ambient"] = policy
        if not store._save_unlocked(uid, char_id, state):
            raise OSError("phone ambient policy unavailable")
    return policy


def policy(uid, char_id):
    state = store.load(uid, char_id)
    return {**DEFAULT_POLICY, **state["config"].get("phone_ambient", {})}, state


def _value(kind, fact):
    if kind == "light":
        return {"dark":"dark", "dim":"dark", "moderate":"bright", "bright":"bright"}.get(fact.get("level"))
    if kind == "motion":
        return fact.get("kind") if fact.get("kind") in {"upright", "face_up", "face_down"} else None
    if (fact.get("interactive_seconds") or 0) >= 2700:
        return "window_interactive_45m"
    categories = fact.get("category_seconds") or {}
    games, media = categories.get("games", 0), categories.get("media", 0)
    if games >= 120 and games > media * 1.5:
        return "window_games"
    if media >= 120 and media > games * 1.5:
        return "window_media"
    return "window_other"


def observe(uid, char_id, credential_hash, session_id, token_label, kind, fact):
    if not session_id or not token_label or kind not in KINDS:
        return
    now = time.time()
    config, state = policy(uid, char_id)
    key = (uid, char_id, credential_hash)
    marker = sha256((credential_hash + session_id).encode()).hexdigest()[:24]
    entry = _detectors.get(key)
    if entry is None or entry["marker"] != marker or entry["revision"] != config["revision"]:
        entry = {"marker":marker, "session_id":session_id, "token_label":token_label,
                 "revision":config["revision"], "kinds":{}}
        _detectors[key] = entry
    _latest[(uid, char_id)] = marker
    _detectors.move_to_end(key)
    if len(_detectors) > 128:
        removed, old = _detectors.popitem(last=False)
        if _latest.get(removed[:2]) == old["marker"]:
            _latest.pop(removed[:2], None)
    history = entry["kinds"].setdefault(kind, {"baseline":None, "candidate":None, "count":0,
        "first_at":0, "observed_at":0, "cooldown_until":0})
    observed = fact["observed_at"]
    if observed <= history["observed_at"]:
        history["reason_code"] = "duplicate"
        return
    gap = observed - history["observed_at"]
    history["observed_at"] = observed
    history["expires_at"] = fact["expires_at"]
    history["source"] = fact["source"]
    from core.autonomy.effective_state import autonomy_enabled, scheduler_enabled
    if not config["enabled"] or not config[kind] or not scheduler_enabled() or not autonomy_enabled(uid, char_id, state):
        history.update(baseline=None, candidate=None, count=0, reason_code="disabled")
        return
    value = _value(kind, fact)
    if (fact["status"] != "available" or fact.get("confidence", 0) < .7 or value is None
            or fact["expires_at"] <= now):
        history.update(baseline=None, candidate=None, count=0, reason_code="unavailable_or_uncertain")
        return
    if gap > 120:
        history.update(baseline=None, candidate=None, count=0)
    if history["candidate"] != value:
        history.update(candidate=value, count=1, first_at=observed, reason_code="debouncing")
        return
    history["count"] += 1
    if history["count"] < 2 or observed - history["first_at"] < 30:
        history["reason_code"] = "debouncing"
        return
    baseline = history["baseline"]
    history["baseline"] = value
    if baseline is None or baseline == value:
        history["reason_code"] = "baseline" if baseline is None else "stable"
        return
    if now < history["cooldown_until"]:
        history["reason_code"] = "cooldown"
        return
    evidence = {"fact":"phone_device_reported_change", "kind":kind, "source":fact["source"],
        "from":baseline, "to":value, "observed_at":observed, "expires_at":fact["expires_at"],
        "device_session":marker, "policy_revision":config["revision"], "confidence":fact["confidence"]}
    from core.autonomy.signal_adapters import enqueue_phone_ambient_signal
    queued = enqueue_phone_ambient_signal(uid, char_id, evidence)
    history["reason_code"] = "queued" if queued else "suppressed"
    if queued:
        history["cooldown_until"] = now + (3600 if kind == "usage" else 600)


def source_active(uid, char_id, signals):
    """Recheck consent, credential, session and freshness after queueing/restart."""
    relevant = [row for row in signals if row.get("source") == "phone_ambient"]
    if not relevant:
        return True
    from core.pipeline_registry import get as get_pipeline
    pipeline = get_pipeline()
    active_char = getattr(pipeline, "_active_character_id", None) if pipeline is not None else None
    if active_char and active_char != char_id:
        return False
    config, state = policy(uid, char_id)
    from core.autonomy.effective_state import autonomy_enabled, scheduler_enabled
    if not config["enabled"] or not scheduler_enabled() or not autonomy_enabled(uid, char_id, state):
        return False
    from core.session_scope import resolve_session, SessionScopeError
    from core.perception.ambient_state import snapshot
    now = time.time()
    for signal in relevant:
        if not signal.get("evidence"):
            return False
        for evidence in signal.get("evidence") or []:
            kind = evidence.get("kind") if isinstance(evidence, dict) else None
            if (kind not in KINDS or not config[kind] or evidence.get("policy_revision") != config["revision"]
                    or evidence.get("expires_at", 0) <= now or _latest.get((uid, char_id)) != evidence.get("device_session")):
                return False
            pair = next(((key, entry) for key, entry in _detectors.items()
                         if key[:2] == (uid, char_id) and entry["marker"] == evidence["device_session"]), None)
            if pair is None:
                return False
            key, entry = pair
            credential = find_by_hash(key[2])
            if credential is None or credential.profiles != {"mobile"}:
                return False
            try:
                grant = resolve_session(entry["session_id"], token_label=entry["token_label"])
            except SessionScopeError:
                return False
            if (grant.owner_id, grant.char_id, grant.domain) != (uid, char_id, "reality"):
                return False
            fact = snapshot(uid, char_id, "phone", credential_hash=key[2]).get(kind, {})
            if (fact.get("status") != "available" or fact.get("expires_at", 0) <= now
                    or fact.get("confidence", 0) < .7 or _value(kind, fact) != evidence.get("to")):
                return False
    return bool(relevant)


def effective(uid, char_id, *, credential_hash=None):
    from core.autonomy.effective_state import autonomy_enabled, scheduler_enabled
    from core.perception.ambient_state import snapshot
    config, state = policy(uid, char_id)
    facts = snapshot(uid, char_id, "phone", credential_hash=credential_hash)
    enabled = config["enabled"] and scheduler_enabled() and autonomy_enabled(uid, char_id, state)
    reasons = {kind:("disabled" if not config["enabled"] else "autonomy_disabled" if not enabled
        else "kind_disabled" if not config[kind] else facts[kind]["status"]
        if facts[kind]["status"] != "available" else "uncertain"
        if facts[kind].get("confidence", 0) < .7 or _value(kind, facts[kind]) is None else "available") for kind in KINDS}
    return {"char_id":char_id, "configured":config, "effective":{kind:reasons[kind] == "available" for kind in KINDS},
        "reason_codes":reasons,
        "source_status":{kind:facts[kind]["status"] for kind in KINDS},
        "default_enabled":False, "foreground_only":True, "ttl_seconds":120}


def observability(uid, char_id):
    return {**effective(uid, char_id), "sources":[
        {"device_session":entry["marker"], "kinds":{kind:{**values,"fresh":values.get("expires_at",0)>time.time()} for kind,values in entry["kinds"].items()}}
        for key, entry in _detectors.items() if key[:2] == (uid, char_id)]}
