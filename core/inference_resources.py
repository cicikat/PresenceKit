"""Optional per-GPU admission for the model origins a backend dispatches to."""
from __future__ import annotations

import asyncio
import re
from collections import Counter
from contextlib import contextmanager
from urllib.parse import urlsplit

from core.config_loader import get_config

_active: Counter[str] = Counter()


class GpuResourceBusy(RuntimeError):
    """An explicitly configured GPU group reached its concurrency limit."""


def origin(url: str) -> str:
    try:
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username is not None or parts.port == 0:
            return ""
        host = f"[{parts.hostname}]" if ":" in parts.hostname else parts.hostname
        port = parts.port or (443 if parts.scheme == "https" else 80)
        return f"{parts.scheme}://{host}:{port}"
    except ValueError:
        return ""


def validate_limits(value: object) -> dict:
    if not isinstance(value, dict) or len(value) > 32:
        raise ValueError("Expected up to 32 named GPU groups")
    result, claimed = {}, set()
    for name, group in value.items():
        if not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_-]{0,63}", str(name)) or not isinstance(group, dict):
            raise ValueError("Invalid GPU group name or settings")
        limit, addresses = group.get("max_concurrency"), group.get("origins")
        if type(limit) is not int or not 1 <= limit <= 16 or not isinstance(addresses, list) or not 1 <= len(addresses) <= 32:
            raise ValueError("Each GPU group needs 1–32 origins and a concurrency limit of 1–16")
        origins = []
        for address in addresses:
            if not isinstance(address, str) or any(ord(c) <= 32 or c in "\\%@?#" for c in address):
                raise ValueError("Use an HTTP(S) origin without path, credentials or query")
            endpoint = origin(address)
            if not endpoint or urlsplit(address).path not in {"", "/"}:
                raise ValueError("Use an HTTP(S) origin without path, credentials or query")
            if endpoint in claimed:
                raise ValueError("An origin can belong to only one GPU group")
            claimed.add(endpoint)
            origins.append(endpoint)
        result[name] = {"origins": origins, "max_concurrency": limit}
    return result


def snapshot(config: dict | None = None) -> dict:
    config = get_config() if config is None else config
    try:
        groups = validate_limits(config.get("inference_gpu_limits", {}))
    except ValueError:
        return {"groups": {}, "active": dict(_active), "error": "invalid_gpu_limits"}
    return {"groups": groups, "active": dict(_active), "error": ""}


class _Lease:
    def __init__(self, group: str = ""):
        self.group = group
        self.pending = None

    def defer_until(self, pending: asyncio.Future) -> None:
        if self.group:
            self.pending = pending

    async def run(self, call, *, on_cancel=None):
        if not self.group and on_cancel is None:
            return await call
        task = asyncio.ensure_future(call)
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            # The provider may continue after a caller has left. Keep this slot
            # until the bounded outbound operation finishes or times out.
            if not task.done():
                self.defer_until(task)
                if on_cancel is not None:
                    on_cancel(task)
                if not self.group:
                    task.add_done_callback(_consume_error)
            else:
                _consume_error(task)
            raise

    def _release(self, pending=None) -> None:
        if pending is not None:
            _consume_error(pending)
        if self.pending is not None and self.pending is not pending:
            if not self.pending.done():
                self.pending.add_done_callback(self._release)
                return
            _consume_error(self.pending)
        _active[self.group] -= 1
        if _active[self.group] <= 0:
            del _active[self.group]


def _consume_error(task: asyncio.Future) -> None:
    if not task.cancelled():
        task.exception()


@contextmanager
def reserve(url: str):
    state = snapshot()
    if state["error"]:
        raise GpuResourceBusy(state["error"])
    endpoint = origin(url)
    group = next((name for name, cfg in state["groups"].items() if endpoint in cfg["origins"]), "")
    lease = _Lease(group)
    if not group:
        yield lease
        return
    if _active[group] >= state["groups"][group]["max_concurrency"]:
        raise GpuResourceBusy("gpu_group_busy")
    _active[group] += 1
    try:
        yield lease
    finally:
        if lease.pending is not None and not lease.pending.done():
            lease.pending.add_done_callback(lease._release)
        else:
            lease._release(lease.pending)
