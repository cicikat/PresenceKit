"""Admin-owned configuration and control surface for the Reality browser worker."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from admin.auth import require_scopes
from admin.config_control import read_config_file, write_config_file
from core.config_loader import get_config, get_config_path, reload_config

router = APIRouter()


class BrowserSettingsUpdate(BaseModel):
    enabled: bool | None = None
    allowed_domains: list[str] | None = None
    download_enabled: bool | None = None
    upload_enabled: bool | None = None
    max_page_chars: int | None = Field(None, ge=100, le=100000)
    max_links: int | None = Field(None, ge=1, le=100)
    worker_timeout_seconds: int | None = Field(None, ge=1, le=300)
    adapter: str | None = None


class BrowserTaskRequest(BaseModel):
    url: str
    operation: str
    idempotency_key: str
    confirmed: bool = False
    ttl_seconds: int = Field(900, ge=1, le=3600)
    params: dict[str, Any] = Field(default_factory=dict)


def _scope() -> tuple[str, str]:
    cfg = get_config()
    uid = str((cfg.get("scheduler") or {}).get("owner_id") or "").strip()
    if not uid:
        raise HTTPException(status_code=503, detail="owner_id_not_configured")
    try:
        from core.scheduler.loop import _active_char_id_or_none
        char_id = str(_active_char_id_or_none() or "").strip()
    except Exception:
        char_id = ""
    if not char_id:
        from core.data_paths import DEFAULT_CHAR_ID
        char_id = DEFAULT_CHAR_ID
    return uid, char_id


def _snapshot() -> dict[str, Any]:
    from core.agent_runtime.browser import observability_snapshot, policy
    uid, char_id = _scope()
    result = observability_snapshot()
    result["allowed_domains"] = list(policy().allowed_domains)
    result["scope"] = {"uid_configured": bool(uid), "char_id": char_id}
    return result


@router.get("/settings/agent-runtime-browser", summary="读取浏览器能力配置与 worker 状态")
async def get_browser_settings(_auth=Depends(require_scopes("admin"))):
    return _snapshot()


@router.put("/settings/agent-runtime-browser", summary="更新浏览器 allowlist 与 worker 配置")
async def update_browser_settings(body: BrowserSettingsUpdate, _auth=Depends(require_scopes("admin"))):
    raw = read_config_file(get_config_path())
    block = raw.setdefault("browser", {})
    if body.adapter is not None and body.adapter.lower() not in {"none", "playwright"}:
        raise HTTPException(status_code=422, detail="adapter_not_supported")
    if body.allowed_domains is not None:
        domains = []
        for value in body.allowed_domains:
            domain = str(value).strip().lower().rstrip(".")
            if not domain or any(ch in domain for ch in "/:@?#"):
                raise HTTPException(status_code=422, detail="invalid_allowed_domain")
            domains.append(domain)
        block["allowed_domains"] = sorted(set(domains))
    for key in ("enabled", "download_enabled", "upload_enabled", "max_page_chars", "max_links", "worker_timeout_seconds", "adapter"):
        value = getattr(body, key)
        if value is not None:
            block[key] = value.lower() if key == "adapter" else value
    write_config_file(get_config_path(), raw)
    reload_config()
    from core.agent_runtime.browser import start_worker, stop_worker
    await stop_worker()
    await start_worker()
    return _snapshot()


@router.get("/settings/agent-runtime-browser/tasks", summary="读取当前 owner 的浏览器任务")
async def get_browser_tasks(_auth=Depends(require_scopes("admin"))):
    from core.agent_runtime.task_manager import observability_snapshot
    uid, char_id = _scope()
    return observability_snapshot(uid=uid, char_id=char_id, capability="browser.automation", limit=100)


@router.post("/settings/agent-runtime-browser/tasks", summary="创建并执行安全浏览器任务")
async def create_browser_task(body: BrowserTaskRequest, _auth=Depends(require_scopes("admin"))):
    from core.agent_runtime.browser import BrowserError, create_task, run_task
    from core.agent_runtime.models import TaskPrincipal
    uid, char_id = _scope()
    principal = TaskPrincipal.reality(uid, char_id)
    try:
        receipt = create_task(principal, url=body.url, operation=body.operation, idempotency_key=body.idempotency_key, confirmed=body.confirmed, ttl_seconds=body.ttl_seconds, params=body.params)
        if receipt.get("status") == "queued" and body.operation not in {"login", "pay", "post", "delete", "send_email", "change_password", "upload", "download"}:
            return await run_task(principal, receipt["task_id"], url=body.url, operation=body.operation, params=body.params)
        return {"receipt": receipt, "result": None}
    except BrowserError as exc:
        raise HTTPException(status_code=422, detail={"code": exc.code}) from exc


@router.post("/settings/agent-runtime-browser/tasks/{task_id}/confirm", summary="确认并执行高风险浏览器任务")
async def confirm_browser_task(task_id: str, body: BrowserTaskRequest, _auth=Depends(require_scopes("admin"))):
    from core.agent_runtime.browser import BrowserError, confirm_task, run_task
    from core.agent_runtime.models import TaskPrincipal
    uid, char_id = _scope()
    try:
        principal = TaskPrincipal.reality(uid, char_id)
        confirm_task(principal, task_id)
        return await run_task(principal, task_id, url=body.url, operation=body.operation, confirmed=True, params=body.params)
    except BrowserError as exc:
        raise HTTPException(status_code=422, detail={"code": exc.code}) from exc
