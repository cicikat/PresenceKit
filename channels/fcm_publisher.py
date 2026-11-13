"""FCM HTTP v1 wake-only adapter. The durable mobile queue owns message content."""

import asyncio
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone
from threading import Lock
import time

import httpx

from core import mobile_push_devices as devices
from core.mobile_delivery_trace import mark

_jobs: set[asyncio.Task] = set()
_auth_lock = Lock()
_credentials = None
_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"


def _access_token() -> str:
    from google import auth
    from google.auth.transport.requests import Request

    global _credentials
    with _auth_lock:
        if _credentials is None:
            _credentials, _ = auth.default(scopes=[_SCOPE])
        if not _credentials.valid:
            request = Request()

            def bounded_request(*args, **kwargs):
                kwargs["timeout"] = 5
                return request(*args, **kwargs)

            _credentials.refresh(bounded_request)
        return _credentials.token


@dataclass(frozen=True)
class Result:
    error: str | None = None
    retry_seconds: float | None = None


def _retry_after(value: str | None, default: float) -> float:
    if not value:
        return default
    try:
        return max(0, int(value))
    except ValueError:
        try:
            return max(0, (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds())
        except (ValueError, TypeError):
            return default


async def _send(client: httpx.AsyncClient, device: devices.Device, item: dict) -> Result:
    project = devices.project()
    if not project or not project.isascii() or not project.replace("-", "").isalnum():
        return Result("configuration")
    try:
        access = await asyncio.wait_for(asyncio.to_thread(_access_token), timeout=10)
    except Exception:
        return Result("authorization")
    payload = {
        "message": {
            "token": device.registration,
            "data": {
                "type": "new_message",
                "message_id": item["id"],
                "seq": str(item["seq"]),
                "installation_id": device.installation_id,
                "generation": str(device.generation),
                "sent_at_ms": str(time.time_ns() // 1_000_000),
            },
            "android": {"priority": "high", "ttl": "300s"},
        },
    }
    try:
        response = await client.post(
            f"https://fcm.googleapis.com/v1/projects/{project}/messages:send",
            headers={"Authorization": f"Bearer {access}"},
            json=payload,
        )
    except httpx.RequestError:
        return Result("network", 1)
    if response.is_success:
        return Result()
    try:
        details = response.json()["error"].get("details", [])
        unregistered = any(
            isinstance(detail, dict) and detail.get("errorCode") == "UNREGISTERED"
            for detail in details
        )
    except (ValueError, KeyError, AttributeError, TypeError):
        unregistered = False
    if response.status_code == 404 and unregistered:
        return Result("unregistered")
    if response.status_code == 429:
        return Result("quota", max(60, _retry_after(response.headers.get("Retry-After"), 60)))
    if response.status_code >= 500:
        return Result("service", _retry_after(response.headers.get("Retry-After"), 1))
    return Result("authorization" if response.status_code in {401, 403} else "rejected")


async def _deliver(client: httpx.AsyncClient, device: devices.Device, item: dict) -> None:
    for attempt in range(3):
        if not devices.current(device):
            return
        result = await _send(client, device, item)
        mark(item["id"], "fcm_server_accepted" if result.error is None else "fcm_send_failed",
             outcome=result.error or "ok", attempt=attempt + 1)
        if result.retry_seconds is None or result.retry_seconds > 60 or attempt == 2:
            devices.record_outcome(device, result.error)
            return
        await asyncio.sleep(max(1, result.retry_seconds, 2 ** attempt))


async def _publish(targets: list[devices.Device], item: dict) -> None:
    async with httpx.AsyncClient(trust_env=False, timeout=8) as client:
        await asyncio.gather(*(_deliver(client, device, item) for device in targets))


def schedule(item: dict) -> bool:
    char_id = item.get("char_id")
    if not char_id:
        return False
    targets = devices.targets(str(item.get("user_id") or ""), char_id)
    if not targets:
        return False
    if len(_jobs) >= 32:
        mark(item["id"], "fcm_send_failed", outcome="busy")
        return True
    # Keep the async job independent of the queue row and its content.
    wake = {"id": item["id"], "seq": item["seq"]}
    mark(item["id"], "push_enqueued")
    task = asyncio.create_task(_publish(targets, wake))
    _jobs.add(task)

    def done(job: asyncio.Task) -> None:
        _jobs.discard(job)
        if not job.cancelled() and job.exception() is not None:
            mark(wake["id"], "fcm_send_failed", outcome="unexpected")

    task.add_done_callback(done)
    return True
