"""Encrypted, bounded Android push registrations for the single configured owner."""

from dataclasses import dataclass, field
import hashlib
import json
import os
import secrets
import time
from threading import Lock

from cryptography.fernet import Fernet, InvalidToken

from admin.token_registry import find_by_hash
from core.config_loader import get_config
from core.safe_write import safe_write_json
from core.sandbox import get_paths

LEASE_SECONDS = 14 * 86400
MAX_DEVICES = 8
_lock = Lock()


@dataclass(frozen=True)
class Device:
    installation_id: str
    owner_id: str
    char_id: str
    credential: str = field(repr=False)
    generation: int
    seen_at: float
    registration: str = field(repr=False)


def project() -> str:
    return os.environ.get("PRESENCEKIT_FCM_PROJECT", "").strip()


def _cipher() -> Fernet:
    return Fernet(os.environ.get("PRESENCEKIT_FCM_REGISTRY_KEY", "").strip().encode())


def configured() -> bool:
    try:
        _cipher()
        return bool(project())
    except ValueError:
        return False


def owner_id() -> str:
    return str(get_config().get("scheduler", {}).get("owner_id", "")).strip()


def _load() -> dict:
    path = get_paths().mobile_push_devices()
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(rows, dict):
            raise ValueError("invalid push registry")
        return rows
    except FileNotFoundError:
        return {}


def _save(rows: dict) -> None:
    path = get_paths().mobile_push_devices()
    if not safe_write_json(path, rows, keep_bak=False):
        raise OSError("push registry write failed")


def _active(row: dict, now: float) -> bool:
    if now - row["seen_at"] >= LEASE_SECONDS or row["owner_id"] != owner_id():
        return False
    credential = find_by_hash(row["credential"])
    return credential is not None and credential.profiles == {"mobile"}


def register(installation_id: str, owner: str, char_id: str, credential: str, token: str) -> dict:
    with _lock:
        rows = _load()
        old = rows.get(installation_id)
        now = time.time()
        if old and old["credential"] != credential and _active(old, now):
            raise PermissionError("installation_in_use")
        rows = {key: row for key, row in rows.items() if _active(row, now)}
        fingerprint = hashlib.sha256(token.encode()).hexdigest()
        for key, row in list(rows.items()):
            if key != installation_id and row["fingerprint"] == fingerprint:
                if row["credential"] != credential:
                    raise PermissionError("registration_in_use")
                del rows[key]
        if installation_id not in rows and len(rows) >= MAX_DEVICES:
            raise OverflowError("device_limit")
        generation = old["generation"] if old else secrets.randbelow(2 ** 52)
        same_binding = old and (
            old["fingerprint"], old["credential"], old["owner_id"], old["char_id"]
        ) == (fingerprint, credential, owner, char_id)
        if not same_binding:
            generation += 1
        rows[installation_id] = {
            "owner_id": owner, "char_id": char_id, "credential": credential,
            "generation": generation, "seen_at": now, "fingerprint": fingerprint,
            "encrypted_registration": _cipher().encrypt(token.encode()).decode(),
            "last_delivery_error": None,
        }
        _save(rows)
        return {"registered": True, "generation": generation, "lease_seconds": LEASE_SECONDS}


def remove(installation_id: str, credential: str) -> None:
    with _lock:
        rows = _load()
        row = rows.get(installation_id)
        if row and row["credential"] == credential:
            del rows[installation_id]
            _save(rows)


def status(installation_id: str, credential: str) -> dict:
    with _lock:
        row = _load().get(installation_id)
        if row and row["credential"] == credential and _active(row, time.time()):
            return {
                "registered": True, "generation": row["generation"],
                "last_delivery_error": row["last_delivery_error"],
            }
        return {"registered": False}


def targets(owner: str, char_id: str) -> list[Device]:
    if not configured() or not owner or not char_id:
        return []
    with _lock:
        rows = _load()
        now = time.time()
        cipher = _cipher()
        devices = []
        for key, row in rows.items():
            if (row["owner_id"], row["char_id"]) != (owner, char_id) or not _active(row, now):
                continue
            try:
                registration = cipher.decrypt(row["encrypted_registration"].encode()).decode()
            except InvalidToken:
                continue
            devices.append(Device(key, owner, char_id, row["credential"], row["generation"], row["seen_at"], registration))
        return devices


def record_outcome(device: Device, error: str | None) -> None:
    with _lock:
        rows = _load()
        current = rows.get(device.installation_id)
        if current is None or (
            current["generation"], current["credential"], current["seen_at"]
        ) != (device.generation, device.credential, device.seen_at):
            return
        if error == "unregistered":
            del rows[device.installation_id]
        elif current["last_delivery_error"] != error:
            current["last_delivery_error"] = error
        else:
            return
        _save(rows)


def current(device: Device) -> bool:
    with _lock:
        row = _load().get(device.installation_id)
        return bool(
            row and row["credential"] == device.credential and row["generation"] == device.generation
            and _active(row, time.time())
        )


def bound_character(installation_id: str, credential: str, generation: int) -> str | None:
    with _lock:
        row = _load().get(installation_id)
        if row and row["credential"] == credential and row["generation"] == generation and _active(row, time.time()):
            return row["char_id"]
        return None
