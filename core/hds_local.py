"""Local HDS HTTP receiver and bounded heart-rate sample analysis."""

from __future__ import annotations

import ipaddress
import json
import math
import re
import socket
import statistics
import time
from threading import Lock

from core.config_loader import get_config
from core.memory import health_state

_lock = Lock()


def config() -> dict:
    value = get_config().get("hds_local", {})
    return value if isinstance(value, dict) else {}


def enabled() -> bool:
    return config().get("enabled") is True


def character_read_enabled() -> bool:
    """Whether the character may read heart-rate samples via ``read_hds_heart_rate``.

    Receiving samples and letting the character read them are separate consents:
    the owner may want the watch feed recorded without it entering conversation.
    Defaults to True so existing installs keep the tool they already had.
    """
    return enabled() and config().get("character_read_enabled", True) is not False


def interfaces() -> list[dict]:
    """Discover LAN addresses at read time so DHCP changes need no config edit."""
    try:
        import psutil
    except ImportError:
        return []
    result = []
    for name, addresses in psutil.net_if_addrs().items():
        for item in addresses:
            if item.family != socket.AF_INET:
                continue
            try:
                address = ipaddress.IPv4Address(item.address)
                network = ipaddress.IPv4Network(f"{address}/{item.netmask}", strict=False)
            except (ValueError, TypeError):
                continue
            if not address.is_private or address.is_loopback or address.is_link_local:
                continue
            if address in ipaddress.IPv4Network("198.18.0.0/15"):
                continue
            result.append({"name": name, "address": str(address), "network": str(network)})
    return result


def allowed_source(host: str) -> bool:
    try:
        address = ipaddress.ip_address(host)
        if not (address.is_private or address.is_loopback) or address.is_link_local:
            return False
        settings = config()
        if settings.get("source_mode", "auto") == "auto":
            selected = settings.get("interface") or ""
            networks = [item["network"] for item in interfaces() if not selected or item["name"] == selected]
        else:
            networks = settings.get("allowed_subnets") or []
        return bool(networks) and any(address in ipaddress.ip_network(item, strict=False) for item in networks)
    except (ValueError, TypeError):
        return False


def _heart_rate(payload: object) -> int | None:
    if isinstance(payload, str):
        if payload.startswith("heartRate:"):
            payload = {"heartRate": payload.partition(":")[2]}
        else:
            try:
                payload = json.loads(payload)
            except ValueError:
                return None
    if not isinstance(payload, dict):
        return None
    for key in ("heartRate", "heart_rate", "heartRateBpm", "bpm", "hr"):
        value = payload.get(key)
        if isinstance(value, bool):
            continue
        try:
            number = float(value)
            result = round(number)
        except (ValueError, TypeError):
            continue
        if math.isfinite(number) and 35 <= result <= 220:
            return result
    for key in ("data", "metrics", "healthData"):
        if key in payload:
            return _heart_rate(payload[key])
    return None


def ingest(uid: str, payload: object, *, now: float | None = None) -> dict:
    data = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(data, str) and re.match(r"^[A-Za-z][A-Za-z0-9_]*:", data) and not data.startswith("heartRate:"):
        return {"accepted": False, "ignored": True}
    value = _heart_rate(payload)
    if value is None:
        raise ValueError("HDS payload has no valid heart rate")
    now = time.time() if now is None else now
    with _lock:
        state = health_state.load(uid)
        samples = list(state.get("hds_samples") or [])
        if samples and samples[-1]["value"] == value and now - samples[-1]["received_at"] < 5:
            return {"accepted": False, "value": value}

        def append(current: dict) -> None:
            current["hds_samples"] = (list(current.get("hds_samples") or []) + [
                {"value": value, "received_at": now}
            ])[-1440:]

        health_state.mutate(uid, append)
    return {"accepted": True, "value": value}


def latest_change(uid: str, *, now: float | None = None) -> dict | None:
    """Read recent samples at each autonomy tick; expose only meaningful changes."""
    if not enabled():
        return None
    now = time.time() if now is None else now
    state = health_state.load(uid)
    samples = state.get("hds_samples") or []
    recent = [s for s in samples if 0 <= now - s.get("received_at", 0) <= 180]
    if len(recent) < 3:
        return None
    tail = recent[-3:]
    if tail[-1]["received_at"] - tail[0]["received_at"] < 15:
        return None
    value = int(statistics.median(s["value"] for s in tail))
    baseline = [s["value"] for s in samples if 180 < now - s.get("received_at", 0) <= 1800]
    previous = int(statistics.median(baseline)) if len(baseline) >= 3 else None
    direction = "up" if previous is None or value >= previous else "down"
    last = state.get("hds_last_signal") or {}
    meaningful = not (value < 110 and (previous is None or abs(value - previous) < 25))
    meaningful = meaningful and not (previous is None and value < 125)
    cooldown = meaningful and now - float(last.get("at") or 0) < 1200 and last.get("direction") == direction
    outcome = "ordinary" if not meaningful else "cooldown" if cooldown else "candidate"
    analysis = {
        "evaluated_at": now,
        "measured_at": tail[-1]["received_at"],
        "value": value,
        "previous_value": previous,
        "direction": direction,
        "outcome": outcome,
    }
    if (state.get("hds_last_analysis") or {}).get("measured_at") != analysis["measured_at"] or (state.get("hds_last_analysis") or {}).get("outcome") != outcome:
        health_state.mutate(uid, lambda current: current.__setitem__("hds_last_analysis", analysis))
    if not meaningful or cooldown:
        return None
    health_state.mutate(uid, lambda current: current.__setitem__("hds_last_signal", analysis | {"at": now}))
    return {
        "value": value,
        "previous_value": previous,
        "measured_at": tail[-1]["received_at"],
        "urgency": 0.8 if value >= 125 else 0.5,
        "confidence": 0.75,
    }


def read_status(uid: str, *, now: float | None = None) -> dict:
    """Read HDS facts without running or consuming the autonomy classifier."""
    now = time.time() if now is None else now
    state = health_state.load(uid)
    samples = state.get("hds_samples") or []
    latest = samples[-1] if samples else None
    age = max(0, now - latest["received_at"]) if latest else None
    recent = [s["value"] for s in samples if 0 <= now - s.get("received_at", 0) <= 180]
    return {
        "enabled": enabled(),
        "sample_count_retained": len(samples),
        "latest": latest,
        "latest_age_seconds": round(age) if age is not None else None,
        "live": bool(enabled() and age is not None and age <= 30),
        "recent_3m": {
            "count": len(recent),
            "median_bpm": round(statistics.median(recent)) if recent else None,
            "min_bpm": min(recent) if recent else None,
            "max_bpm": max(recent) if recent else None,
        },
        "last_automation_analysis": state.get("hds_last_analysis") or None,
        "last_candidate": state.get("hds_last_signal") or None,
    }
