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
    samples = health_state.load(uid).get("hds_samples") or []
    recent = [s for s in samples if 0 <= now - s.get("received_at", 0) <= 180]
    if len(recent) < 3:
        return None
    tail = recent[-3:]
    if tail[-1]["received_at"] - tail[0]["received_at"] < 15:
        return None
    value = int(statistics.median(s["value"] for s in tail))
    baseline = [s["value"] for s in samples if 180 < now - s.get("received_at", 0) <= 1800]
    previous = int(statistics.median(baseline)) if len(baseline) >= 3 else None
    if value < 110 and (previous is None or abs(value - previous) < 25):
        return None
    if previous is None and value < 125:
        return None
    direction = "up" if previous is None or value >= previous else "down"
    last = health_state.load(uid).get("hds_last_signal") or {}
    if now - float(last.get("at") or 0) < 1200 and last.get("direction") == direction:
        return None
    health_state.mutate(uid, lambda state: state.__setitem__("hds_last_signal", {"at": now, "direction": direction}))
    return {
        "value": value,
        "previous_value": previous,
        "measured_at": tail[-1]["received_at"],
        "urgency": 0.8 if value >= 125 else 0.5,
        "confidence": 0.75,
    }
