"""Bounded, credential-blind Reality browser capability (Brief 236)."""
from __future__ import annotations

import time
from dataclasses import dataclass
from urllib.parse import urlparse
from typing import Any, Callable

from core.agent_runtime.models import TaskPrincipal, CausationRef
from core.agent_runtime import task_manager
from core.deployment_capabilities import is_remote_server

BROWSER_CAPABILITY = "browser.automation"
_SAFE_OPS = frozenset({"navigate", "read_page", "click", "fill", "select"})
_CONFIRM_OPS = frozenset({"login", "pay", "post", "delete", "send_email", "change_password", "upload", "download"})
_adapter: Callable[..., dict[str, Any]] | None = None


class BrowserError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code); self.code = code


@dataclass(frozen=True)
class BrowserPolicy:
    enabled: bool = False
    allowed_domains: tuple[str, ...] = ()
    download_enabled: bool = False
    upload_enabled: bool = False


def set_adapter(adapter: Callable[..., dict[str, Any]] | None) -> None:
    global _adapter
    _adapter = adapter


def policy() -> BrowserPolicy:
    from core.config_loader import get_config
    raw = get_config().get("browser", {})
    domains = raw.get("allowed_domains", ()) if isinstance(raw, dict) else ()
    return BrowserPolicy(bool(raw.get("enabled", False)) if isinstance(raw, dict) else False,
                         tuple(str(x).lower().strip() for x in domains if str(x).strip()),
                         bool(raw.get("download_enabled", False)) if isinstance(raw, dict) else False,
                         bool(raw.get("upload_enabled", False)) if isinstance(raw, dict) else False)


def _check(principal: TaskPrincipal, url: str, operation: str, *, confirmed: bool = False) -> BrowserPolicy:
    if principal.realm != "reality": raise BrowserError("realm_forbidden")
    if is_remote_server(): raise BrowserError("disabled_remote_server_local_capability")
    p = policy()
    if not p.enabled: raise BrowserError("browser_disabled")
    if operation not in _SAFE_OPS | _CONFIRM_OPS: raise BrowserError("operation_forbidden")
    if operation in _CONFIRM_OPS and not confirmed: raise BrowserError("confirmation_required")
    if operation == "download" and not p.download_enabled: raise BrowserError("download_disabled")
    if operation == "upload" and not p.upload_enabled: raise BrowserError("upload_disabled")
    parsed = urlparse(str(url))
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme not in {"http", "https"} or not host: raise BrowserError("invalid_url")
    if not any(host == d or host.endswith("." + d) for d in p.allowed_domains): raise BrowserError("domain_not_allowed")
    return p


def create_task(principal: TaskPrincipal, *, url: str, operation: str, idempotency_key: str,
                confirmed: bool = False, ttl_seconds: int = 900, causation_ref: CausationRef | None = None) -> dict[str, Any]:
    _check(principal, url, operation, confirmed=confirmed)
    receipt, _ = task_manager.create_task(principal, capability=BROWSER_CAPABILITY, source="browser",
        idempotency_key=idempotency_key, ttl_seconds=ttl_seconds, causation_ref=causation_ref)
    return receipt


def run_task(principal: TaskPrincipal, task_id: str, *, url: str, operation: str,
             confirmed: bool = False, params: dict[str, Any] | None = None) -> dict[str, Any]:
    _check(principal, url, operation, confirmed=confirmed)
    lease = task_manager.claim_next(principal, task_id=task_id)
    if lease is None: raise BrowserError("task_not_queued")
    if _adapter is None: return task_manager.fail_task(principal, lease, error_code="browser_adapter_unavailable")
    try:
        result = _adapter(url=url, operation=operation, params=params or {})
        metadata = {"outcome_code": "ok", "truncated": True}
        if isinstance(result, dict) and result.get("ok") is False: return task_manager.fail_task(principal, lease, error_code="browser_operation_failed")
        return task_manager.complete_task(principal, lease, result_metadata=metadata)
    except Exception:
        return task_manager.fail_task(principal, lease, error_code="browser_operation_failed")


def cancel_task(principal: TaskPrincipal, task_id: str) -> dict[str, Any]:
    return task_manager.request_cancel(principal, task_id, reason_code="user_requested")


def observability_snapshot() -> dict[str, Any]:
    p = policy()
    return {"schema_version": "agent-runtime-browser-observability.v1", "enabled": p.enabled,
            "allowed_domain_count": len(p.allowed_domains), "download_enabled": p.download_enabled,
            "upload_enabled": p.upload_enabled, "adapter_available": _adapter is not None,
            "credentials_exposed": False, "profile_exposed": False}
