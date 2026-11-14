"""Short-lived ambient observations scoped to a Reality session and device credential."""

from collections import OrderedDict
from hashlib import sha256
import json
import logging
import time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from admin.token_registry import find_by_hash

Device = Literal["desktop", "phone"]
TTL_SECONDS = 120
MAX_DEVICES = 128
logger = logging.getLogger(__name__)


class Fact(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    status: Literal["available", "unavailable", "permission_denied", "not_supported"]
    observed_at: float
    confidence: float = Field(default=0, ge=0, le=1)

    @model_validator(mode="after")
    def hide_unavailable_details(self):
        if self.status != "available":
            for name in type(self).model_fields.keys() - Fact.model_fields.keys():
                if getattr(self, name) is not None:
                    raise ValueError("unavailable observations cannot include measurements")
        return self


class Light(Fact):
    level: Literal["dark", "dim", "moderate", "bright", "unknown"] | None = None
    trend: Literal["brighter", "darker", "stable", "unknown"] | None = None
    relative_brightness: float | None = Field(default=None, ge=0, le=100)
    lux: float | None = Field(default=None, ge=0, le=300_000)


class Sound(Fact):
    level: Literal["quiet", "normal", "loud", "unknown"] | None = None
    sudden_sound: bool | None = None


class Motion(Fact):
    kind: Literal["face_up", "face_down", "upright", "moving"] | None = None


class Usage(Fact):
    window_start: float | None = None
    screen_sessions: int | None = Field(default=None, ge=0, le=20_000)
    interactive_seconds: int | None = Field(default=None, ge=0, le=86_400)
    category_seconds: dict[Literal["creative", "games", "media", "reading", "productivity", "other"], int] | None = None

    @model_validator(mode="after")
    def validate_window(self):
        if self.status == "available":
            if self.window_start is None or not 0 < self.observed_at - self.window_start <= 90_000:
                raise ValueError("invalid observation window")
            if any(not 0 <= seconds <= 90_000 for seconds in (self.category_seconds or {}).values()):
                raise ValueError("invalid category duration")
        return self


class AmbientUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    allow_light_candidate: bool = False
    light: Light | None = None
    sound: Sound | None = None
    motion: Motion | None = None
    usage: Usage | None = None


_sources = {
    "desktop": {"light": "camera_relative_brightness", "sound": "microphone_mixed_level"},
    "phone": {"light": "android_light_sensor", "motion": "android_gravity", "usage": "android_usage_events"},
}
_devices = OrderedDict()


def update(uid: str, char_id: str, device: Device, credential_hash: str, session_expires: float, body: AmbientUpdate) -> None:
    now = time.time()
    fields = body.model_dump(exclude_none=True, exclude={"allow_light_candidate"})
    if not fields or fields.keys() - _sources[device].keys():
        raise ValueError("unsupported ambient source")
    light = body.light
    if light and (device == "desktop" and light.lux is not None or device == "phone" and light.relative_brightness is not None):
        raise ValueError("brightness source mismatch")
    if any(not now - TTL_SECONDS < fact["observed_at"] <= now + 5 for fact in fields.values()):
        raise ValueError("observation outside freshness window")
    key = (uid, char_id, device, credential_hash)
    facts, last_signal = _devices.setdefault(key, ({}, 0.0))
    for name, fact in fields.items():
        old = facts.get(name)
        if old and fact["observed_at"] <= old["observed_at"]:
            continue
        facts[name] = {
            **fact,
            "source": _sources[device][name],
            "received_at": now,
            "expires_at": min(now + TTL_SECONDS, fact["observed_at"] + TTL_SECONDS, session_expires),
            "permission_state": "denied" if fact["status"] == "permission_denied" else (
                "consented" if fact["status"] == "available" else "unavailable"
            ),
        }
        if (
            device == "desktop" and name == "light" and body.allow_light_candidate
            and old and old["expires_at"] > now and old["status"] == "available"
            and fact["status"] == "available" and fact["confidence"] >= 0.6
            and fact.get("trend") in {"brighter", "darker"} and now - last_signal >= 600
        ):
            try:
                from core.autonomy.signal_adapters import enqueue_ambient_light_signal

                if enqueue_ambient_light_signal(uid, char_id, facts[name]):
                    last_signal = now
            except Exception:
                logger.debug("Ambient candidate unavailable", exc_info=True)
    _devices[key] = (facts, last_signal)
    _devices.move_to_end(key)
    if len(_devices) > MAX_DEVICES:
        _devices.popitem(last=False)


def snapshot(uid: str, char_id: str, device: Device) -> dict:
    now = time.time()
    result = {"version": "ambient-state.v1", "device": device}
    candidates = {}
    for (owner, character, kind, credential_hash), (facts, _) in reversed(_devices.items()):
        if (owner, character, kind) != (uid, char_id, device):
            continue
        credential = find_by_hash(credential_hash)
        expected_profile = "desktop" if device == "desktop" else "mobile"
        if credential is None or credential.profiles != {expected_profile}:
            continue
        for name, fact in facts.items():
            candidate = candidates.get(name)
            if candidate is None or (
                fact["expires_at"] > now, fact["received_at"]
            ) > (
                candidate["expires_at"] > now, candidate["received_at"]
            ):
                candidates[name] = {**fact, "device_id": sha256(credential_hash.encode()).hexdigest()[:16]}
    for name in _sources[device]:
        fact = candidates.get(name)
        if fact is None:
            result[name] = {"status": "unavailable"}
        elif fact["expires_at"] <= now:
            result[name] = {"status": "stale", "source": fact["source"], "observed_at": fact["observed_at"]}
        else:
            result[name] = fact
    return result


async def read_tool(user_id: str, char_id: str, device: Device = "desktop") -> str:
    from core.config_loader import get_config

    owner = str(get_config().get("scheduler", {}).get("owner_id", "")).strip()
    if not owner or user_id != owner or not char_id or device not in _sources:
        return json.dumps({"status": "unavailable"})
    return json.dumps(snapshot(user_id, char_id, device))
