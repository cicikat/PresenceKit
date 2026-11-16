"""Dream scenarios API responsibilities; legacy names re-exported by dream.py."""

import logging
import json
import re
import re as _re
import time
from pathlib import Path

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from admin.auth import require_scopes
from admin.routers._common import requested_char_id, resolve_requested_char_id
from core.config_loader import get_config
from core.sandbox import get_paths

router = APIRouter()
logger = logging.getLogger("admin.routers.dream")

_SAFE_SCRIPT_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def _validate_scenario_id(script_id: str) -> str:
    from admin.routers import dream as _runtime
    if not _runtime._SAFE_SCRIPT_ID_RE.match(script_id):
        raise _runtime.HTTPException(status_code=422, detail=f"剧本 id 不合法: {script_id!r}")
    return script_id


def _scenario_write_path(script_id: str) -> Path:
    from admin.routers import dream as _runtime
    from core.sandbox import get_paths
    return get_paths().dream_scenario_write_path(_runtime._validate_scenario_id(script_id))


def _scenario_read_path(script_id: str) -> tuple[Path, str] | None:
    from admin.routers import dream as _runtime
    from core.sandbox import get_paths
    safe_id = _runtime._validate_scenario_id(script_id)
    primary, fallback = get_paths().dream_scenario_read_dirs()
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
    from admin.routers import dream as _runtime
    from core.dream.dream_state import read_state, DreamStatus
    uid = _runtime._owner_uid()
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
    """解析 + 用剧本加载器的真实 schema 校验，失败返回具体字段错误而不是 500。"""
    from admin.routers import dream as _runtime
    import yaml as _yaml
    from core.dream.scenario_loader import _validate_script

    try:
        data = _yaml.safe_load(_runtime._normalize_field_outline_scenario_yaml(yaml_text))
    except Exception as e:
        raise _runtime.HTTPException(status_code=422, detail=f"YAML 解析失败: {e}")
    if not isinstance(data, dict):
        raise _runtime.HTTPException(status_code=422, detail="剧本必须是 YAML 映射（mapping），不能是列表或标量")
    if script_id is not None and data.get("id") != script_id:
        raise _runtime.HTTPException(
            status_code=422,
            detail=f"YAML 内 id={data.get('id')!r} 与剧本 id={script_id!r} 不一致",
        )
    try:
        _validate_script(data, require_progressable=require_progressable)
    except ValueError as e:
        raise _runtime.HTTPException(status_code=422, detail=f"剧本 schema 校验失败: {e}")
    return data


def _scenario_document_and_yaml(script_id: str, body: dict) -> tuple[dict, str]:
    from admin.routers import dream as _runtime
    document = body.get("document")
    if document is not None:
        if not isinstance(document, dict):
            raise _runtime.HTTPException(status_code=422, detail="document 必须是 JSON object")
        data = dict(document)
        if data.get("id") and data["id"] != script_id:
            raise _runtime.HTTPException(status_code=422, detail="document.id 与剧本 id 不一致")
        data["id"] = script_id
        from core.dream.scenario_loader import _validate_script
        try:
            _validate_script(data, require_progressable=True)
        except ValueError as exc:
            raise _runtime.HTTPException(status_code=422, detail=f"剧本 schema 校验失败: {exc}")
        return data, _runtime._canonical_scenario_yaml(data)

    yaml_text = body.get("yaml") or ""
    data = _runtime._parse_and_validate_scenario_yaml(script_id, yaml_text)
    return data, _runtime._canonical_scenario_yaml(data)


@router.get("/dream/scenarios", summary="列出剧本")
async def list_dream_scenarios(_auth=Depends(require_scopes("activity"))):
    from admin.routers import dream as _runtime
    from core.sandbox import get_paths
    import yaml as _yaml

    primary, fallback = get_paths().dream_scenario_read_dirs()
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
    from admin.routers import dream as _runtime
    yaml_text = body.get("yaml")
    document = body.get("document")
    requested_id = body.get("id")

    if yaml_text is not None:
        if not isinstance(yaml_text, str) or not yaml_text.strip():
            raise _runtime.HTTPException(status_code=422, detail="yaml 不能为空")
        script_id = str(requested_id).strip() if requested_id is not None else None
        if script_id == "":
            script_id = None
        parsed = _runtime._parse_and_validate_scenario_yaml(script_id, yaml_text, require_progressable=True)
        canonical = _runtime._canonical_scenario_yaml(parsed)
    elif document is not None:
        if not isinstance(document, dict):
            raise _runtime.HTTPException(status_code=422, detail="document 必须是 JSON object")
        script_id = str(requested_id or document.get("id") or "").strip()
        if not script_id:
            raise _runtime.HTTPException(status_code=422, detail="id 不能为空")
        parsed, canonical = _runtime._scenario_document_and_yaml(script_id, {"document": document})
    else:
        raise _runtime.HTTPException(status_code=422, detail="需要提供 yaml 或 document")

    return {"ok": True, "id": parsed["id"], "document": parsed, "yaml": canonical}


@router.get("/dream/scenarios/{script_id}", summary="读取剧本 YAML 原文")
async def get_dream_scenario(script_id: str, _auth=Depends(require_scopes("activity"))):
    from admin.routers import dream as _runtime
    resolved = _runtime._scenario_read_path(script_id)
    if resolved is None:
        raise _runtime.HTTPException(status_code=404, detail=f"剧本 {script_id} 不存在")
    p, source = resolved
    document = _runtime._parse_and_validate_scenario_yaml(script_id, p.read_text(encoding="utf-8"))
    from core.dream.scenario_loader import unprogressable_stage_ids
    unprogressable = unprogressable_stage_ids(document)
    yaml_text = _runtime._canonical_scenario_yaml(document)
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
    from admin.routers import dream as _runtime
    script_id = (body.get("id") or "").strip()
    if not script_id:
        raise _runtime.HTTPException(status_code=422, detail="id 不能为空")
    if _runtime._scenario_read_path(script_id) is not None:
        raise _runtime.HTTPException(status_code=409, detail=f"剧本 {script_id} 已存在")
    _, yaml_text = _runtime._scenario_document_and_yaml(script_id, body)
    p = _runtime._scenario_write_path(script_id)

    from core.safe_write import safe_write_text
    p.parent.mkdir(parents=True, exist_ok=True)
    _runtime._log_dream_write(kind="dream_scenario", read_path=p, canonical=p, source="new")
    safe_write_text(p, yaml_text)
    return {"ok": True, "id": script_id}


@router.put("/dream/scenarios/{script_id}", summary="修改剧本")
async def update_dream_scenario(
    script_id: str, body: dict, _auth=Depends(require_scopes("activity"))
):
    from admin.routers import dream as _runtime
    resolved = _runtime._scenario_read_path(script_id)
    if resolved is None:
        raise _runtime.HTTPException(status_code=404, detail=f"剧本 {script_id} 不存在")
    if _runtime._scenario_active(script_id):
        raise _runtime.HTTPException(status_code=409, detail="剧本正在被进行中的梦引用，梦醒后再编辑")

    _, yaml_text = _runtime._scenario_document_and_yaml(script_id, body)
    p = _runtime._scenario_write_path(script_id)

    from core.safe_write import safe_write_text
    p.parent.mkdir(parents=True, exist_ok=True)
    _runtime._log_dream_write(
        kind="dream_scenario",
        read_path=resolved[0],
        canonical=p,
        source=resolved[1],
    )
    safe_write_text(p, yaml_text)
    return {"ok": True, "id": script_id, "source": "user"}


@router.delete("/dream/scenarios/{script_id}", summary="删除剧本")
async def delete_dream_scenario(script_id: str, _auth=Depends(require_scopes("activity"))):
    from admin.routers import dream as _runtime
    resolved = _runtime._scenario_read_path(script_id)
    if resolved is None:
        raise _runtime.HTTPException(status_code=404, detail=f"剧本 {script_id} 不存在")
    if _runtime._scenario_active(script_id):
        raise _runtime.HTTPException(status_code=409, detail="剧本正在被进行中的梦引用，梦醒后再删除")

    p, source = resolved
    if source != "user":
        raise _runtime.HTTPException(status_code=409, detail="旧路径剧本为只读兼容来源；编辑后会写入 userdata 覆盖副本")
    p.unlink()
    return {"ok": True, "deleted": script_id}
