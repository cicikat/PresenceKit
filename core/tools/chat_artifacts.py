"""Sandbox chat artifacts: bounded files the character can write for the user.

Product files live under get_paths() only. They are not workspace, repo, or
data/ memory. Tool results and channel payloads never include absolute paths
or file bodies.
"""

from __future__ import annotations

import json
import re
import threading
import time
import uuid
from contextvars import ContextVar
from pathlib import Path

from core.data_paths import DEFAULT_CHAR_ID, safe_user_id
from core.safe_write import safe_write_json, safe_write_text
from core.sandbox import get_paths

MAX_CONTENT_CHARS = 256_000
MAX_READ_CHARS = 12_000
MAX_FILES_PER_SCOPE = 50
MAX_FILES_PER_TURN = 4
MAX_FILENAME_CHARS = 80
MAX_LIST_ITEMS = 20

_ARTIFACT_ID_RE = re.compile(r"^[A-Fa-f0-9]{32}$")
_UNSAFE_FILENAME_CHARS = re.compile(r'[\\/:\0<>"|?*]')

ALLOWED_EXTENSIONS: dict[str, str] = {
    ".txt": "text/plain; charset=utf-8",
    ".md": "text/markdown; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".htm": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".csv": "text/csv; charset=utf-8",
    ".py": "text/x-python; charset=utf-8",
    ".yaml": "text/yaml; charset=utf-8",
    ".yml": "text/yaml; charset=utf-8",
    ".xml": "application/xml; charset=utf-8",
    ".toml": "text/plain; charset=utf-8",
}

PREVIEWABLE_EXTENSIONS = frozenset({
    ".txt", ".md", ".html", ".htm", ".css", ".json", ".csv", ".xml", ".yaml", ".yml", ".toml",
})

PREVIEW_CSP = (
    "sandbox; default-src 'none'; img-src data:; style-src 'unsafe-inline'; "
    "font-src 'none'; connect-src 'none'; script-src 'none'; frame-ancestors 'self'"
)

_turn_artifacts: ContextVar[list[dict] | None] = ContextVar("chat_artifact_turn", default=None)
_index_locks: dict[str, threading.Lock] = {}
_index_locks_guard = threading.Lock()


class ArtifactError(ValueError):
    """User-visible artifact tool failure."""


def begin_turn_collection() -> None:
    _turn_artifacts.set([])


def drain_turn_artifacts() -> list[dict]:
    items = list(_turn_artifacts.get() or [])
    _turn_artifacts.set(None)
    return items


def peek_turn_artifacts() -> list[dict]:
    return list(_turn_artifacts.get() or [])


def public_payload(record: dict) -> dict:
    artifact_id = str(record.get("id") or "")
    filename = str(record.get("filename") or "")
    ext = Path(filename).suffix.lower()
    previewable = ext in PREVIEWABLE_EXTENSIONS
    payload = {
        "id": artifact_id,
        "filename": filename,
        "mime": str(record.get("mime") or "application/octet-stream"),
        "size": int(record.get("size") or 0),
        "previewable": previewable,
        "download_url": f"/chat/artifacts/{artifact_id}",
    }
    if previewable:
        payload["preview_url"] = f"/chat/artifacts/{artifact_id}/preview"
    return payload


def _lock_for(uid: str, char_id: str) -> threading.Lock:
    key = f"{uid}:{char_id}"
    with _index_locks_guard:
        lock = _index_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _index_locks[key] = lock
        return lock


def _assert_within(root: Path, candidate: Path) -> None:
    root_resolved = root.resolve()
    try:
        candidate.resolve().relative_to(root_resolved)
    except ValueError as exc:
        raise ArtifactError("产物路径越过了沙盒边界") from exc


def _safe_filename(raw: str) -> str:
    name = str(raw or "").strip()
    if not name or name in {".", ".."}:
        raise ArtifactError("文件名不合法")
    if len(name) > MAX_FILENAME_CHARS:
        raise ArtifactError(f"文件名不能超过 {MAX_FILENAME_CHARS} 个字符")
    if _UNSAFE_FILENAME_CHARS.search(name) or name.startswith("."):
        raise ArtifactError("文件名不合法")
    ext = Path(name).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        allowed = ", ".join(sorted({e.lstrip(".") for e in ALLOWED_EXTENSIONS}))
        raise ArtifactError(f"只支持这些文本扩展名：{allowed}")
    return name


def _require_scope(user_id: str | None, char_id: str | None) -> tuple[str, str]:
    if not user_id or not char_id:
        raise ArtifactError("当前对话没有可用的用户与角色范围，无法访问产物文件")
    try:
        uid = safe_user_id(user_id)
        cid = safe_user_id(char_id)
    except ValueError as exc:
        raise ArtifactError("用户或角色标识不合法") from exc
    return uid, cid


def _validate_artifact_id(artifact_id: str) -> str:
    value = str(artifact_id or "").strip()
    if not _ARTIFACT_ID_RE.fullmatch(value):
        raise ArtifactError("产物编号不合法")
    return value.lower()


def _load_index(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return []
    items = data.get("items") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict) and item.get("id")]


def _store_index(path: Path, items: list[dict]) -> None:
    if not safe_write_json(path, {"items": items}):
        raise OSError("产物索引写入失败")


def _file_path(root: Path, artifact_id: str, filename: str) -> Path:
    ext = Path(filename).suffix.lower()
    target = root / f"{artifact_id}{ext}"
    _assert_within(root, target)
    _assert_within(root, target.with_suffix(target.suffix + ".tmp"))
    return target


def _lookup_path(artifact_id: str) -> Path:
    return get_paths().chat_artifact_lookup(artifact_id)


def _record_lookup(artifact_id: str, uid: str, char_id: str) -> None:
    path = _lookup_path(artifact_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not safe_write_json(path, {"uid": uid, "char_id": char_id, "id": artifact_id}):
        raise OSError("产物查找表写入失败")


def resolve_artifact_scope(artifact_id: str) -> dict | None:
    try:
        artifact_id = _validate_artifact_id(artifact_id)
    except ArtifactError:
        return None
    path = _lookup_path(artifact_id)
    if not path.exists() or not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    uid = str(data.get("uid") or "")
    char_id = str(data.get("char_id") or "")
    if not uid or not char_id:
        return None
    return {"id": artifact_id, "uid": uid, "char_id": char_id}


def _collect(record: dict) -> None:
    bucket = _turn_artifacts.get()
    if bucket is None:
        return
    if len(bucket) >= MAX_FILES_PER_TURN:
        raise ArtifactError(f"这一轮最多写 {MAX_FILES_PER_TURN} 个文件")
    bucket.append(public_payload(record))


def write_artifact(
    filename: str,
    content: str,
    *,
    user_id: str | None = None,
    char_id: str | None = None,
) -> str:
    if not isinstance(content, str):
        raise ArtifactError("产物只接受文本内容")
    if len(content) > MAX_CONTENT_CHARS:
        raise ArtifactError(f"单次写入不能超过 {MAX_CONTENT_CHARS} 个字符")

    uid, cid = _require_scope(user_id, char_id)
    filename = _safe_filename(filename)
    mime = ALLOWED_EXTENSIONS[Path(filename).suffix.lower()]
    artifact_id = uuid.uuid4().hex
    paths = get_paths()
    root = paths.chat_artifacts_dir(uid, char_id=cid)
    index_path = paths.chat_artifacts_index(uid, char_id=cid)
    target = _file_path(root, artifact_id, filename)

    with _lock_for(uid, cid):
        root.mkdir(parents=True, exist_ok=True)
        items = _load_index(index_path)
        pending = _turn_artifacts.get()
        if pending is not None and len(pending) >= MAX_FILES_PER_TURN:
            raise ArtifactError(f"这一轮最多写 {MAX_FILES_PER_TURN} 个文件")
        if not safe_write_text(target, content):
            raise OSError("产物文件写入失败")
        record = {
            "id": artifact_id,
            "filename": filename,
            "mime": mime,
            "size": len(content.encode("utf-8")),
            "created_at": time.time(),
            "uid": uid,
            "char_id": cid,
        }
        items.append(record)
        overflow = items[:-MAX_FILES_PER_SCOPE] if len(items) > MAX_FILES_PER_SCOPE else []
        kept = items[-MAX_FILES_PER_SCOPE:]
        for old in overflow:
            try:
                old_id = str(old.get("id") or "")
                old_name = str(old.get("filename") or "")
                old_file = _file_path(root, old_id, old_name)
                if old_file.exists():
                    old_file.unlink()
                lookup = _lookup_path(old_id)
                if lookup.exists():
                    lookup.unlink()
            except Exception:
                pass
        _store_index(index_path, kept)
        _record_lookup(artifact_id, uid, cid)
        _collect(record)

    payload = public_payload(record)
    return json.dumps({
        "status": "written",
        "id": payload["id"],
        "filename": payload["filename"],
        "mime": payload["mime"],
        "size": payload["size"],
        "note": "文件已写好。用户会在聊天气泡里看到下载卡片；不要说出工具名，也不要编造本机路径。",
    }, ensure_ascii=False)


def read_artifact(
    artifact_id: str,
    *,
    user_id: str | None = None,
    char_id: str | None = None,
) -> str:
    uid, cid = _require_scope(user_id, char_id)
    artifact_id = _validate_artifact_id(artifact_id)
    record = get_artifact_record(artifact_id, uid=uid, char_id=cid)
    if record is None:
        raise ArtifactError("找不到这个产物文件")
    text = read_artifact_text(record)
    truncated = len(text) > MAX_READ_CHARS
    body = text[:MAX_READ_CHARS]
    return json.dumps({
        "id": record["id"],
        "filename": record["filename"],
        "mime": record["mime"],
        "size": record["size"],
        "truncated": truncated,
        "content": body,
    }, ensure_ascii=False)


def list_artifacts(
    *,
    user_id: str | None = None,
    char_id: str | None = None,
) -> str:
    uid, cid = _require_scope(user_id, char_id)
    items = list_artifact_records(uid=uid, char_id=cid, limit=MAX_LIST_ITEMS)
    return json.dumps({
        "count": len(items),
        "items": [
            {
                "id": item["id"],
                "filename": item["filename"],
                "mime": item["mime"],
                "size": item["size"],
            }
            for item in items
        ],
    }, ensure_ascii=False)


def list_artifact_records(
    *,
    uid: str,
    char_id: str,
    limit: int = MAX_LIST_ITEMS,
) -> list[dict]:
    uid, char_id = _require_scope(uid, char_id)
    index_path = get_paths().chat_artifacts_index(uid, char_id=char_id)
    items = _load_index(index_path)
    items.reverse()
    return items[: max(1, min(int(limit), MAX_FILES_PER_SCOPE))]


def get_artifact_record(artifact_id: str, *, uid: str, char_id: str) -> dict | None:
    artifact_id = _validate_artifact_id(artifact_id)
    uid, char_id = _require_scope(uid, char_id)
    for item in _load_index(get_paths().chat_artifacts_index(uid, char_id=char_id)):
        if str(item.get("id") or "").lower() == artifact_id:
            return item
    return None


def artifact_file_path(record: dict) -> Path:
    uid = str(record.get("uid") or "")
    char_id = str(record.get("char_id") or DEFAULT_CHAR_ID)
    root = get_paths().chat_artifacts_dir(uid, char_id=char_id)
    return _file_path(root, str(record["id"]), str(record["filename"]))


def read_artifact_text(record: dict) -> str:
    target = artifact_file_path(record)
    if not target.exists() or not target.is_file():
        raise ArtifactError("找不到这个产物文件")
    try:
        return target.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ArtifactError("产物文件不是 UTF-8 文本") from exc


def observability_snapshot(*, uid: str = "", char_id: str = "", limit: int = 50) -> dict:
    limit = max(1, min(int(limit), MAX_FILES_PER_SCOPE))
    if uid and char_id:
        items = list_artifact_records(uid=uid, char_id=char_id, limit=limit)
        return {
            "retention": f"fifo_{MAX_FILES_PER_SCOPE}",
            "max_chars": MAX_CONTENT_CHARS,
            "count": len(items),
            "items": [
                {
                    **public_payload(item),
                    "created_at": item.get("created_at"),
                    "uid": item.get("uid"),
                    "char_id": item.get("char_id"),
                }
                for item in items
            ],
        }
    return {
        "retention": f"fifo_{MAX_FILES_PER_SCOPE}",
        "max_chars": MAX_CONTENT_CHARS,
        "count": 0,
        "items": [],
        "note": "提供 uid 与 char_id 后返回该范围的产物元数据，不含正文。",
    }


def register_tools(registry: dict) -> None:
    """Path C artifact tools; scope is injected only by the dispatcher."""
    async def write(filename, content, *, user_id, char_id):
        return write_artifact(filename, content, user_id=user_id, char_id=char_id)

    async def read(artifact_id, *, user_id, char_id):
        return read_artifact(artifact_id, user_id=user_id, char_id=char_id)

    async def listing(*, user_id, char_id):
        return list_artifacts(user_id=user_id, char_id=char_id)

    for name, func, description, properties, required, keywords in (
        ("write_artifact", write, "创建给用户下载的文本产物，不能访问工作区或执行代码。",
         {"filename": {"type": "string", "description": "产物文件名，含扩展名。"},
          "content": {"type": "string", "maxLength": MAX_CONTENT_CHARS, "description": "要写入产物的文本内容。"}},
         ["filename", "content"], ["生成文件", "做成文件"]),
        ("read_artifact", read, "读取当前用户与角色的既有产物。",
         {"artifact_id": {"type": "string", "description": "要读取的产物 ID。"}}, ["artifact_id"], ["读取产物"]),
        ("list_artifacts", listing, "列出当前用户与角色的产物元数据。",
         {}, [], ["列出产物"]),
    ):
        registry[name] = {
            "func": func, "description": description, "category": "artifacts",
            "parameters": {"type": "object", "properties": properties, "required": required},
            "dangerous": False, "effect": "write" if name == "write_artifact" else "read",
            "examples": keywords, "keywords": keywords, "trace_args": [],
            "trace_result": False, "echo_event_log": False,
        }
