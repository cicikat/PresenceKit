"""Dream scenarios API responsibilities; legacy names re-exported by dream.py."""

from admin.routers import dream_common
from core.dream import scenario_authoring

import logging
import json
import re
import re as _re
import time
from pathlib import Path

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from admin.auth import require_scopes
from admin.routers._common import requested_char_id, resolve_requested_char_id
from core import config_loader
from core import sandbox

router = APIRouter()
logger = logging.getLogger("admin.routers.dream")

_SAFE_SCRIPT_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def _validate_scenario_id(script_id: str) -> str:
    if not _SAFE_SCRIPT_ID_RE.match(script_id):
        raise HTTPException(status_code=422, detail=f"剧本 id 不合法: {script_id!r}")
    return script_id


def _scenario_write_path(script_id: str) -> Path:
    from core import sandbox
    return sandbox.get_paths().dream_scenario_write_path(_validate_scenario_id(script_id))


def _scenario_read_path(script_id: str) -> tuple[Path, str] | None:
    from core import sandbox
    safe_id = _validate_scenario_id(script_id)
    primary, fallback = sandbox.get_paths().dream_scenario_read_dirs()
    primary_path = primary / f"{safe_id}.yaml"
    if primary_path.exists():
        return primary_path, "user"
    if fallback is not None:
        fallback_path = fallback / f"{safe_id}.yaml"
        if fallback_path.exists():
            return fallback_path, "legacy"
    return None


def _scenario_active(script_id: str) -> bool:
    """当前是否有「进行中」的梦冻结着这个剧本（DREAM_ACTIVE / CLOSING / EXIT_REQUESTED）。"""
    from core.dream.dream_state import read_state, DreamStatus
    uid = dream_common._owner_uid()
    state = read_state(uid)
    active_statuses = {
        DreamStatus.DREAM_ACTIVE.value,
        DreamStatus.DREAM_CLOSING.value,
        DreamStatus.DREAM_EXIT_REQUESTED.value,
    }
    if state.get("status") not in active_statuses:
        return False
    scenario_core = state.get("scenario_core") or {}
    return scenario_core.get("script_id") == script_id


def _parse_and_validate_scenario_yaml(
    script_id: str | None,
    yaml_text: str,
    *,
    require_progressable: bool = False,
) -> dict:
    try:
        return scenario_authoring._parse_and_validate_scenario_yaml(script_id, yaml_text, require_progressable=require_progressable)
    except scenario_authoring.ScenarioAuthoringError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


def _scenario_document_and_yaml(script_id: str, body: dict) -> tuple[dict, str]:
    try:
        return scenario_authoring._scenario_document_and_yaml(script_id, body)
    except scenario_authoring.ScenarioAuthoringError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.get("/dream/scenarios", summary="列出剧本")
async def list_dream_scenarios(_auth=Depends(require_scopes("activity"))):
    from core import sandbox
    import yaml as _yaml

    primary, fallback = sandbox.get_paths().dream_scenario_read_dirs()
    merged: dict[str, dict] = {}
    for directory, source in ((primary, "user"), (fallback, "legacy")):
        if directory is None or not directory.exists():
            continue
        for p in sorted(directory.glob("*.yaml")):
            script_id = p.stem
            if script_id in merged:
                continue
            title = script_id
            data = {}
            try:
                data = _yaml.safe_load(p.read_text(encoding="utf-8")) or {}
                if isinstance(data, dict) and data.get("title"):
                    title = data["title"]
            except Exception:
                pass
            from core.dream.scenario_loader import unprogressable_stage_ids
            unprogressable = unprogressable_stage_ids(data) if isinstance(data, dict) else []
            merged[script_id] = {
                "id": script_id,
                "title": title,
                "source": source,
                "progressable": not bool(unprogressable),
                "unprogressable_stage_ids": unprogressable,
            }
    return {"scenarios": [merged[key] for key in sorted(merged)]}


@router.post("/dream/scenarios/validate", summary="校验并规范化剧本（不落盘）")
async def validate_dream_scenario(body: dict, _auth=Depends(require_scopes("activity"))):
    """Validate/serialize an authored draft without creating a real scenario file.

    YAML is parsed here so the browser never needs a second YAML implementation.
    The same endpoint also gives the editor a canonical export for unsaved drafts.
    """
    yaml_text = body.get("yaml")
    document = body.get("document")
    requested_id = body.get("id")

    if yaml_text is not None:
        if not isinstance(yaml_text, str) or not yaml_text.strip():
            raise HTTPException(status_code=422, detail="yaml 不能为空")
        script_id = str(requested_id).strip() if requested_id is not None else None
        if script_id == "":
            script_id = None
        parsed = _parse_and_validate_scenario_yaml(script_id, yaml_text, require_progressable=True)
        canonical = scenario_authoring._canonical_scenario_yaml(parsed)
    elif document is not None:
        if not isinstance(document, dict):
            raise HTTPException(status_code=422, detail="document 必须是 JSON object")
        script_id = str(requested_id or document.get("id") or "").strip()
        if not script_id:
            raise HTTPException(status_code=422, detail="id 不能为空")
        parsed, canonical = _scenario_document_and_yaml(script_id, {"document": document})
    else:
        raise HTTPException(status_code=422, detail="需要提供 yaml 或 document")

    return {"ok": True, "id": parsed["id"], "document": parsed, "yaml": canonical}


@router.get("/dream/scenarios/{script_id}", summary="读取剧本 YAML 原文")
async def get_dream_scenario(script_id: str, _auth=Depends(require_scopes("activity"))):
    resolved = _scenario_read_path(script_id)
    if resolved is None:
        raise HTTPException(status_code=404, detail=f"剧本 {script_id} 不存在")
    p, source = resolved
    document = _parse_and_validate_scenario_yaml(script_id, p.read_text(encoding="utf-8"))
    from core.dream.scenario_loader import unprogressable_stage_ids
    unprogressable = unprogressable_stage_ids(document)
    yaml_text = scenario_authoring._canonical_scenario_yaml(document)
    return {
        "id": script_id,
        "yaml": yaml_text,
        "document": document,
        "source": source,
        "progressable": not bool(unprogressable),
        "unprogressable_stage_ids": unprogressable,
    }


@router.post("/dream/scenarios", summary="新建剧本")
async def create_dream_scenario(body: dict, _auth=Depends(require_scopes("activity"))):
    script_id = (body.get("id") or "").strip()
    if not script_id:
        raise HTTPException(status_code=422, detail="id 不能为空")
    if _scenario_read_path(script_id) is not None:
        raise HTTPException(status_code=409, detail=f"剧本 {script_id} 已存在")
    _, yaml_text = _scenario_document_and_yaml(script_id, body)
    p = _scenario_write_path(script_id)

    dream_common._log_dream_write(kind="dream_scenario", read_path=p, canonical=p, source="new")
    scenario_authoring.write_scenario(p, yaml_text)
    return {"ok": True, "id": script_id}


@router.put("/dream/scenarios/{script_id}", summary="修改剧本")
async def update_dream_scenario(
    script_id: str, body: dict, _auth=Depends(require_scopes("activity"))
):
    resolved = _scenario_read_path(script_id)
    if resolved is None:
        raise HTTPException(status_code=404, detail=f"剧本 {script_id} 不存在")
    if _scenario_active(script_id):
        raise HTTPException(status_code=409, detail="剧本正在被进行中的梦引用，梦醒后再编辑")

    _, yaml_text = _scenario_document_and_yaml(script_id, body)
    p = _scenario_write_path(script_id)

    dream_common._log_dream_write(
        kind="dream_scenario",
        read_path=resolved[0],
        canonical=p,
        source=resolved[1],
    )
    scenario_authoring.write_scenario(p, yaml_text)
    return {"ok": True, "id": script_id, "source": "user"}


@router.delete("/dream/scenarios/{script_id}", summary="删除剧本")
async def delete_dream_scenario(script_id: str, _auth=Depends(require_scopes("activity"))):
    resolved = _scenario_read_path(script_id)
    if resolved is None:
        raise HTTPException(status_code=404, detail=f"剧本 {script_id} 不存在")
    if _scenario_active(script_id):
        raise HTTPException(status_code=409, detail="剧本正在被进行中的梦引用，梦醒后再删除")

    p, source = resolved
    if source != "user":
        raise HTTPException(status_code=409, detail="旧路径剧本为只读兼容来源；编辑后会写入 userdata 覆盖副本")
    scenario_authoring.delete_scenario(p)
    return {"ok": True, "deleted": script_id}
