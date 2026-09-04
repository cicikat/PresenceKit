"""Credential-blind, Reality-only browser worker (Briefs 236/238)."""
from __future__ import annotations

import asyncio
import hashlib
import inspect
import re
import tempfile
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse

from core.agent_runtime import task_manager
from core.agent_runtime.models import CausationRef, TaskLease, TaskPrincipal, TaskStatus
from core.deployment_capabilities import is_remote_server
from core.sandbox import get_paths

BROWSER_CAPABILITY = "browser.automation"
SAFE_OPERATIONS = frozenset({"navigate", "read_page", "click", "fill", "select"})
CONFIRM_OPERATIONS = frozenset({"login", "pay", "post", "delete", "send_email", "change_password", "upload", "download"})
OPERATIONS = SAFE_OPERATIONS | CONFIRM_OPERATIONS
_SENSITIVE_KEY = re.compile(r"(?:cookie|token|secret|password|authorization|header|profile|storage|credential|path|url|href)", re.I)
_URL_WITH_QUERY = re.compile(r"https?://[^\s<>'\"]+", re.I)
_adapter: Any = None
class _WorkspaceBridge:
    @staticmethod
    def read(principal: TaskPrincipal, path: str) -> str:
        from core.agent_runtime.workspace import read_workspace
        return read_workspace(principal, path)

    @staticmethod
    def write(principal: TaskPrincipal, path: str, content: str) -> dict[str, Any]:
        from core.agent_runtime.workspace import create_workspace, update_workspace
        try:
            return create_workspace(principal, path, content)
        except Exception as exc:
            if getattr(exc, "code", "") != "already_exists":
                raise
            return update_workspace(principal, path, content, confirmed=True)


_workspace_adapter: Any = _WorkspaceBridge()
_worker: "BrowserWorker | None" = None
_stats = {"submitted": 0, "succeeded": 0, "failed": 0, "unknown": 0, "canceled": 0}


class BrowserError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class BrowserPolicy:
    enabled: bool = False
    allowed_domains: tuple[str, ...] = ()
    download_enabled: bool = False
    upload_enabled: bool = False
    max_page_chars: int = 12000
    max_links: int = 30
    worker_timeout_seconds: int = 60
    adapter: str = "none"


def policy() -> BrowserPolicy:
    from core.config_loader import get_config
    raw = get_config().get("browser", {})
    if not isinstance(raw, dict):
        raw = {}
    domains = tuple(sorted({str(x).lower().strip().rstrip(".") for x in raw.get("allowed_domains", ()) if str(x).strip()}))
    return BrowserPolicy(bool(raw.get("enabled", False)), domains, bool(raw.get("download_enabled", False)), bool(raw.get("upload_enabled", False)), max(100, min(int(raw.get("max_page_chars", 12000) or 12000), 100000)), max(1, min(int(raw.get("max_links", 30) or 30), 100)), max(1, min(int(raw.get("worker_timeout_seconds", 60) or 60), 300)), str(raw.get("adapter") or "none").lower())


def _validate_url(url: str, p: BrowserPolicy) -> str:
    raw = str(url or "")
    parsed = urlparse(raw)
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme.lower() not in {"http", "https"} or not host or parsed.username or parsed.password:
        raise BrowserError("invalid_url")
    if not any(host == domain or host.endswith("." + domain) for domain in p.allowed_domains):
        raise BrowserError("domain_not_allowed")
    return parsed._replace(scheme=parsed.scheme.lower(), netloc=parsed.netloc.lower(), fragment="").geturl()


def _canonical_params(operation: str, params: Mapping[str, Any] | None) -> dict[str, Any]:
    raw = dict(params or {})
    allowed = {"selector", "value", "path"}
    if set(raw) - allowed:
        raise BrowserError("invalid_params")
    result: dict[str, Any] = {}
    for key, limit in (("selector", 256), ("value", 2000), ("path", 1024)):
        if key not in raw:
            continue
        value = raw[key]
        if not isinstance(value, str) or len(value) > limit:
            raise BrowserError("invalid_params")
        result[key] = value
    if operation in {"click", "fill", "select"} and not result.get("selector"):
        raise BrowserError("selector_required")
    if operation in {"fill", "select"} and "value" not in result:
        raise BrowserError("value_required")
    if operation in {"upload", "download"} and not result.get("path"):
        raise BrowserError("workspace_path_required")
    return result


def _request_binding(principal: TaskPrincipal, url: str, operation: str, params: Mapping[str, Any] | None, *, confirmed: bool) -> tuple[str, dict[str, Any]]:
    canonical_url = _validate_url(url, policy())
    canonical_params = _canonical_params(operation, params)
    payload = {"schema": "browser-request.v1", "realm": principal.realm, "uid": principal.uid, "char_id": principal.char_id, "url": canonical_url, "operation": operation, "params": canonical_params}
    fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    summary = {"schema_version": "browser-request.v1", "url_digest": hashlib.sha256(canonical_url.encode()).hexdigest()[:24], "operation": operation, "confirmed": bool(confirmed), "params": {k: {"type": "string", "length": len(v), "digest": hashlib.sha256(v.encode()).hexdigest()[:24]} for k, v in canonical_params.items()}}
    return fingerprint, summary


def _check(principal: TaskPrincipal, url: str, operation: str, *, confirmed: bool = False) -> BrowserPolicy:
    if not isinstance(principal, TaskPrincipal) or principal.realm != "reality":
        raise BrowserError("realm_forbidden")
    if is_remote_server():
        raise BrowserError("disabled_remote_server_local_capability")
    p = policy()
    if not p.enabled or not p.allowed_domains:
        raise BrowserError("browser_disabled")
    if operation not in OPERATIONS:
        raise BrowserError("operation_forbidden")
    if operation == "download" and not p.download_enabled:
        raise BrowserError("download_disabled")
    if operation == "upload" and not p.upload_enabled:
        raise BrowserError("upload_disabled")
    if operation in CONFIRM_OPERATIONS and not confirmed:
        raise BrowserError("confirmation_required")
    _validate_url(url, p)
    return p


def _profile_dir(principal: TaskPrincipal, task_id: str) -> Path:
    digest = hashlib.sha256(f"{principal.uid}:{principal.char_id}:{task_id}".encode()).hexdigest()[:32]
    return Path(tempfile.gettempdir()) / "presencekit-browser-profiles" / digest


def _redact(value: Any, *, depth: int = 0) -> Any:
    if depth > 3:
        return "[truncated]"
    if isinstance(value, Mapping):
        return {str(key)[:64]: _redact(item, depth=depth + 1) for key, item in value.items() if not _SENSITIVE_KEY.search(str(key))}
    if isinstance(value, (list, tuple)):
        return [_redact(item, depth=depth + 1) for item in list(value)[:100]]
    if isinstance(value, str):
        def scrub(match: re.Match[str]) -> str:
            parsed = urlparse(match.group(0))
            return parsed._replace(query="", fragment="").geturl()
        return _URL_WITH_QUERY.sub(scrub, value[:12000])
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:256]


def _result_metadata(result: Any, p: BrowserPolicy) -> dict[str, Any]:
    clean = _redact(result)
    text = str(clean.get("text") or clean.get("content") or "") if isinstance(clean, dict) else ""
    links = clean.get("links") if isinstance(clean, dict) else []
    fields = clean.get("fields") if isinstance(clean, dict) else {}
    return {"outcome_code": "ok", "counters": {"page_chars": min(len(text), p.max_page_chars), "links": min(len(links), p.max_links) if isinstance(links, list) else 0, "fields": len(fields) if isinstance(fields, dict) else 0}, "truncated": True}


def _bounded_result(result: Any, p: BrowserPolicy) -> Any:
    clean = _redact(result)
    if not isinstance(clean, dict):
        return clean
    for key in ("text", "content"):
        if isinstance(clean.get(key), str):
            clean[key] = clean[key][:p.max_page_chars]
    if isinstance(clean.get("links"), list):
        clean["links"] = clean["links"][:p.max_links]
    if isinstance(clean.get("fields"), dict):
        clean["fields"] = dict(list(clean["fields"].items())[:50])
    return clean


class BrowserWorker:
    def __init__(self, adapter: Any = None):
        self.adapter = adapter
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._active: set[str] = set()

    @property
    def alive(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if not self.alive:
            self._stop.clear()
            self._task = asyncio.create_task(self._run(), name="agent_runtime_browser_worker")

    async def stop(self) -> None:
        self._stop.set()
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None

    async def _run(self) -> None:
        while not self._stop.is_set():
            await asyncio.sleep(0.25)

    async def execute(self, principal: TaskPrincipal, lease: TaskLease, *, url: str, operation: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        p = _check(principal, url, operation, confirmed=True)
        if self.adapter is None:
            raise BrowserError("browser_adapter_unavailable")
        self._active.add(lease.task_id)
        try:
            current = task_manager.get_task(principal, lease.task_id)
            if current.get("cancel_requested"):
                receipt = task_manager.acknowledge_cancel(principal, lease)
                _stats["canceled"] += 1
                return {"receipt": receipt, "result": None}
            profile_dir = _profile_dir(principal, lease.task_id)
            profile_dir.mkdir(parents=True, exist_ok=True)
            call_params = dict(params or {})
            if operation == "upload":
                if _workspace_adapter is None or not isinstance(call_params.get("path"), str):
                    raise BrowserError("workspace_adapter_required")
                try:
                    call_params["content"] = _workspace_adapter.read(principal, call_params.pop("path"))
                except Exception as exc:
                    raise BrowserError("workspace_read_failed") from exc
            if hasattr(self.adapter, "execute"):
                call = self.adapter.execute(url=url, operation=operation, params=call_params, profile_dir=profile_dir)
            elif inspect.iscoroutinefunction(self.adapter):
                call = self.adapter(url=url, operation=operation, params=call_params, profile_dir=profile_dir)
            else:
                call = asyncio.to_thread(self.adapter, url=url, operation=operation, params=call_params, profile_dir=profile_dir)
            result = await asyncio.wait_for(call, timeout=p.worker_timeout_seconds)
            bounded = _bounded_result(result, p)
            if operation == "download" and isinstance(bounded, dict) and isinstance(bounded.get("content"), str):
                if _workspace_adapter is None or not isinstance(call_params.get("path"), str):
                    raise BrowserError("workspace_adapter_required")
                try:
                    _workspace_adapter.write(principal, call_params["path"], bounded["content"])
                except Exception as exc:
                    raise BrowserError("workspace_write_failed") from exc
            current = task_manager.get_task(principal, lease.task_id)
            if current.get("cancel_requested"):
                receipt = task_manager.acknowledge_cancel(principal, lease)
                _stats["canceled"] += 1
            elif current.get("pause_requested"):
                receipt = task_manager.acknowledge_pause(principal, lease)
            else:
                receipt = task_manager.complete_task(principal, lease, result_metadata=_result_metadata(result, p))
                _stats["succeeded"] += 1
            return {"receipt": receipt, "result": bounded}
        except asyncio.TimeoutError:
            _stats["unknown"] += 1
            return {"receipt": task_manager.unknown_task(principal, lease, error_code="browser_timeout", recovery_reason="worker_timeout"), "result": None}
        except (ConnectionError, BrokenPipeError, EOFError):
            _stats["unknown"] += 1
            return {"receipt": task_manager.unknown_task(principal, lease, error_code="browser_disconnected", recovery_reason="browser_disconnect"), "result": None}
        except BrowserError as exc:
            _stats["failed"] += 1
            return {"receipt": task_manager.fail_task(principal, lease, error_code=exc.code), "result": None}
        except Exception:
            _stats["unknown"] += 1
            return {"receipt": task_manager.unknown_task(principal, lease, error_code="browser_result_unknown", recovery_reason="worker_failure"), "result": None}
        finally:
            self._active.discard(lease.task_id)


class PlaywrightAdapter:
    """Optional real browser adapter, loaded only when explicitly configured."""

    async def execute(self, *, url: str, operation: str, params: dict[str, Any], profile_dir: Path) -> dict[str, Any]:
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise BrowserError("playwright_unavailable") from exc
        p = policy()
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch_persistent_context(str(profile_dir), headless=True)
            try:
                page = await browser.new_page()
                await page.goto(url, wait_until="domcontentloaded", timeout=p.worker_timeout_seconds * 1000)
                final_host = (urlparse(page.url).hostname or "").lower().rstrip(".")
                if not any(final_host == domain or final_host.endswith("." + domain) for domain in p.allowed_domains):
                    raise BrowserError("redirect_domain_not_allowed")
                selector = str(params.get("selector") or "")[:256]
                if operation == "click":
                    await page.locator(selector).click(timeout=5000)
                elif operation == "fill":
                    await page.locator(selector).fill(str(params.get("value") or "")[:2000])
                elif operation == "select":
                    await page.locator(selector).select_option(str(params.get("value") or "")[:256])
                if operation == "read_page" or operation == "navigate":
                    text = (await page.locator("body").inner_text())[:p.max_page_chars]
                    return {"text": text, "links": await page.locator("a").all_inner_texts()}
                return {"ok": True, "fields": {"operation": operation}}
            finally:
                await browser.close()


def set_adapter(adapter: Any = None) -> None:
    global _adapter
    _adapter = adapter
    if _worker is not None:
        _worker.adapter = adapter


def set_workspace_adapter(adapter: Any = None) -> None:
    """Inject the Workspace capability; browser never reads host paths itself."""
    global _workspace_adapter
    _workspace_adapter = adapter


async def start_worker() -> BrowserWorker | None:
    global _worker
    p = policy()
    if not p.enabled or not p.allowed_domains or is_remote_server():
        return None
    global _adapter
    if _adapter is None and p.adapter == "playwright":
        _adapter = PlaywrightAdapter()
    if _worker is None:
        _worker = BrowserWorker(_adapter)
    await _worker.start()
    return _worker


async def stop_worker() -> None:
    global _worker
    if _worker is not None:
        await _worker.stop()
    _worker = None


def create_task(principal: TaskPrincipal, *, url: str, operation: str, idempotency_key: str, confirmed: bool = False, ttl_seconds: int = 900, causation_ref: CausationRef | None = None, params: dict[str, Any] | None = None) -> dict[str, Any]:
    # Creation of a dangerous operation is allowed only to park it in the
    # durable confirmation state; execution still requires explicit confirm.
    p = _check(principal, url, operation, confirmed=True)
    normalized_url = _validate_url(url, p)
    canonical_params = _canonical_params(operation, params)
    fingerprint, summary = _request_binding(principal, normalized_url, operation, canonical_params, confirmed=confirmed)
    receipt, _ = task_manager.create_task(principal, capability=BROWSER_CAPABILITY, source="browser", idempotency_key=idempotency_key, ttl_seconds=ttl_seconds, causation_ref=causation_ref, enqueue=operation not in CONFIRM_OPERATIONS or confirmed, request_context={"url_digest": summary["url_digest"], "operation": operation, "params": summary["params"]}, request_fingerprint=fingerprint, request_summary=summary, confirmation_required=operation in CONFIRM_OPERATIONS)
    _stats["submitted"] += 1
    if operation in CONFIRM_OPERATIONS and not confirmed:
        receipt = task_manager.mark_waiting_confirmation(principal, receipt["task_id"])
    return receipt


async def run_task(principal: TaskPrincipal, task_id: str, *, url: str, operation: str, confirmed: bool = False, params: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        task = task_manager.get_task(principal, task_id)
    except task_manager.TaskManagerError as exc:
        raise BrowserError(exc.code) from exc
    p = _check(principal, url, operation, confirmed=True)
    canonical_params = _canonical_params(operation, params)
    requested_fingerprint, _ = _request_binding(principal, url, operation, canonical_params, confirmed=bool(task.get("confirmation_required") and task.get("confirmation_granted")))
    if requested_fingerprint != task.get("request_fingerprint"):
        raise BrowserError("task_request_mismatch")
    if task["status"] == TaskStatus.WAITING_CONFIRM.value:
        raise BrowserError("confirmation_required")
    expected_confirmed = bool(
        task.get("confirmation_required")
        and (task.get("confirmation_granted") or task.get("request_summary", {}).get("confirmed"))
    )
    if bool(confirmed) != expected_confirmed:
        raise BrowserError("task_request_mismatch")
    if task["status"] != TaskStatus.QUEUED.value:
        raise BrowserError("task_not_queued")
    try:
        lease = task_manager.claim_next(principal, task_id=task_id, capabilities={BROWSER_CAPABILITY})
    except task_manager.TaskManagerError as exc:
        raise BrowserError(exc.code) from exc
    if lease is None:
        raise BrowserError("task_not_queued")
    worker = _worker or BrowserWorker(_adapter)
    return await worker.execute(principal, lease, url=url, operation=operation, params=canonical_params)


def cancel_task(principal: TaskPrincipal, task_id: str) -> dict[str, Any]:
    try:
        return task_manager.request_cancel(principal, task_id, reason_code="user_requested")
    except task_manager.TaskManagerError as exc:
        raise BrowserError(exc.code) from exc


def pause_task(principal: TaskPrincipal, task_id: str) -> dict[str, Any]:
    try:
        return task_manager.request_pause(principal, task_id)
    except task_manager.TaskManagerError as exc:
        raise BrowserError(exc.code) from exc


def resume_task(principal: TaskPrincipal, task_id: str) -> dict[str, Any]:
    try:
        return task_manager.resume_task(principal, task_id)
    except task_manager.TaskManagerError as exc:
        raise BrowserError(exc.code) from exc


def confirm_task(principal: TaskPrincipal, task_id: str) -> dict[str, Any]:
    try:
        return task_manager.confirm_task(principal, task_id)
    except task_manager.TaskManagerError as exc:
        raise BrowserError(exc.code) from exc


def observability_snapshot() -> dict[str, Any]:
    p = policy()
    remote = is_remote_server()
    effective = bool(p.enabled and p.allowed_domains and not remote)
    return {"schema_version": "agent-runtime-browser-observability.v2", "enabled": p.enabled, "effective_state": "enabled" if effective else ("disabled_remote_server" if remote else "disabled"), "allowed_domain_count": len(p.allowed_domains), "download_enabled": bool(effective and p.download_enabled), "upload_enabled": bool(effective and p.upload_enabled), "worker_alive": bool(_worker and _worker.alive), "worker_active_count": len(_worker._active) if _worker else 0, "adapter_available": _adapter is not None, "limits": {"max_page_chars": p.max_page_chars, "max_links": p.max_links, "worker_timeout_seconds": p.worker_timeout_seconds}, "counters": dict(_stats), "credentials_exposed": False, "profile_exposed": False, "url_exposed": False}
