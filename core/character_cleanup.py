"""Previewed, recoverable removal of private character cards and scoped data."""
from __future__ import annotations

import hashlib
import asyncio
import json
import shutil
import uuid
from pathlib import Path

from core.sandbox import get_paths
from core.userdata_assets import validate_character_id


class CleanupConflict(ValueError):
    pass


def _checked(path: Path, base: Path) -> Path:
    # Never move junctions/symlinks or follow them outside the installation.
    if not path.resolve().is_relative_to(base.resolve()):
        raise CleanupConflict("目标路径超出允许范围")
    for item in (path, *path.parents):
        if item.is_symlink() or (hasattr(item, "is_junction") and item.is_junction()):
            raise CleanupConflict("含符号链接或目录联接，不能自动清理")
        if item == base:
            break
    return path


def _targets(char_id: str, include_data: bool) -> dict[str, Path]:
    char_id = validate_character_id(char_id)
    p = get_paths()
    result = {}
    for layer, root in (("card_user", p.user_character_cards_dir()), ("card_legacy", p.legacy_character_cards_dir())):
        for ext in ("json", "txt", "md"):
            target = _checked(root / f"{char_id}.{ext}", root)
            if target.exists():
                result[f"{layer}_{ext}"] = target
    if include_data:
        result.update(p.character_cleanup_runtime_roots(char_id=char_id))
        # Authored path accessors use ASCII runtime IDs; cleanup also supports
        # historical Unicode card stems without relaxing normal writer policy.
        result.update({
            "authored_user": p.user_authored_character_dir(char_id="cleanup-anchor").parent / char_id,
            "authored_legacy": p.legacy_authored_character_dir(char_id="cleanup-anchor").parent / char_id,
            "live2d": p.user_live2d_root() / char_id,
            "model3d": p.user_model3d_root() / char_id,
        })
        for layer, root in (("avatar_user", p.user_reality_dir()), ("avatar_legacy", p.legacy_reality_dir())):
            for ext in ("png", "jpg", "jpeg", "webp"):
                result[f"{layer}_{ext}"] = root / "avatars" / f"{char_id}.{ext}"
        schedules = p.agent_runtime_schedules_root()
        if schedules.exists():
            for owner in schedules.iterdir():
                if owner.is_dir():
                    result[f"schedule_{owner.name}"] = owner / f"{char_id}.json"
    result = {key: value for key, value in result.items() if value.exists()}
    for path in result.values():
        # DataPaths already bounds runtime roots; check resolved parents and
        # descendants before any recursive move, including Windows junctions.
        _checked(path, path.parent)
        if path.is_dir():
            for child in path.rglob("*"):
                _checked(child, path)
    return result


def _active_reasons(char_id: str) -> list[str]:
    from core.config_loader import get_config
    from core.pipeline_registry import get
    from core.conversation_gate import locked_conversation_uids
    from core.post_process import slow_queue
    from core.memory.locks import locked_uids, locked_globals
    p = get_paths()
    cfg = get_config()
    reasons = []
    if cfg.get("character", {}).get("default") in (char_id, f"{char_id}.json"):
        reasons.append("该角色仍是默认角色，请先更换默认角色")
    active_path = p.active_prompt_assets()
    if active_path.exists():
        active = json.loads(active_path.read_text(encoding="utf-8"))
        if active.get("active_character") == char_id:
            reasons.append("该角色仍是当前角色，请先切换角色")
    pipeline = get()
    if pipeline and getattr(pipeline, "_active_character_id", None) == char_id:
        reasons.append("该角色仍由当前对话运行时使用")
    if (locked_conversation_uids() or locked_uids() or locked_globals()
            or slow_queue.queue_size() or slow_queue.current_task_type()):
        reasons.append("对话或后处理正在运行，请稍后重试")
    try:
        current_task = asyncio.current_task()
        for task in asyncio.all_tasks():
            if task is current_task or task.done():
                continue
            coro = task.get_coro()
            while coro is not None:
                frame = getattr(coro, 'cr_frame', None) or getattr(coro, 'gi_frame', None)
                if frame and any(frame.f_locals.get(key) == char_id for key in ('char_id', '_char_id', 'active_character_id')):
                    reasons.append("该角色仍有后台处理正在运行，请稍后重试")
                    break
                coro = getattr(coro, 'cr_await', None) or getattr(coro, 'gi_yieldfrom', None)
    except RuntimeError:
        pass  # Offline/test callers have no event loop.
    # Shared histories are preserved. References must be explicitly removed
    # rather than rewriting another character's group transcript during cleanup.
    groups = p.stage_group_dir(group_id="cleanup-anchor").parent
    if groups.exists():
        for meta in groups.glob("*/meta.json"):
            data = json.loads(meta.read_text(encoding="utf-8"))
            if char_id in data.get("roster", []):
                reasons.append("该角色仍在群聊成员中，请先移出群聊")
                break
    tasks = p.character_cleanup_runtime_roots(char_id=char_id)["agent_tasks"]
    sessions = p.character_cleanup_runtime_roots(char_id=char_id)["agent_work_sessions"]
    from core.agent_runtime.models import TERMINAL_STATUSES
    for root, field in ((tasks, "tasks"), (sessions, "sessions")):
        if root.exists():
            for state in root.glob("*/state.json"):
                data = json.loads(state.read_text(encoding="utf-8"))
                if any(row.get("status") not in TERMINAL_STATUSES | {"completed", "done", "failed", "canceled", "cancelled", "closed", "rejected", "succeeded"}
                       for row in data.get(field, [])):
                    reasons.append("该角色还有未结束的任务或工作会话，请先结束")
                    break
    schedules = p.agent_runtime_schedules_root()
    if schedules.exists() and any(schedules.glob(f"*/{char_id}.json")):
        reasons.append("该角色仍有定时任务配置，请先撤销定时任务")
    return list(dict.fromkeys(reasons))


def preview(char_id: str, include_data: bool = True) -> dict:
    char_id = validate_character_id(char_id)
    targets = _targets(char_id, include_data)
    blockers = _active_reasons(char_id)
    from core.asset_registry import get_registry
    try:
        entry = get_registry().resolve(char_id, "character")
        if entry.path().resolve().is_relative_to(get_paths().bundled_root().resolve()):
            blockers.append("内置角色卡不可删除")
    except ValueError:
        pass  # Orphan data is a first-class cleanup target.
    if include_data:
        from core.userdata_assets import _configured_tts_bindings
        asset_files = [file for key, path in targets.items()
                       if key in {"authored_user", "authored_legacy", "live2d", "model3d"}
                       for file in path.rglob('*') if file.is_file()]
        if any(_configured_tts_bindings(file.stem) for file in asset_files):
            blockers.append("专属素材仍被语音配置引用，请先解除绑定")
        asset_names = {value for file in asset_files for value in (file.name, file.stem)}
        def values(data):
            if isinstance(data, dict):
                for value in data.values(): yield from values(value)
            elif isinstance(data, list):
                for value in data: yield from values(value)
            elif isinstance(data, str): yield data
        for other in get_registry().list_all('character'):
            if other.id == char_id or other.path().suffix != '.json':
                continue
            card = json.loads(other.path().read_text(encoding='utf-8'))
            if any(Path(value).name in asset_names for value in values(card.get('presence_ext', {}))):
                blockers.append("专属素材仍被其他角色引用，请先解除绑定")
                break
    rows, signature = [], []
    for key, path in targets.items():
        files = sorted([x for x in path.rglob("*") if x.is_file()] if path.is_dir() else [path])
        rows.append({"kind": key, "files": len(files), "bytes": sum(x.stat().st_size for x in files)})
        signature.append((key, str(path.resolve()), path.stat().st_mtime_ns,
                          [(str(x.relative_to(path) if path.is_dir() else x.name), x.stat().st_size, x.stat().st_mtime_ns) for x in files]))
    fingerprint = hashlib.sha256(json.dumps([char_id, include_data, signature], ensure_ascii=False).encode()).hexdigest()
    return {"char_id": char_id, "include_data": include_data, "items": rows,
            "fingerprint": fingerprint, "blockers": blockers, "can_delete": bool(targets) and not blockers,
            "preserved": ["用户全局数据", "共享模型、语音及表情包素材", "其他角色数据", "群聊及关系历史证据"]}


def candidates() -> list[dict]:
    from core.asset_registry import get_registry
    entries = {e.id: e for e in get_registry().list_all("character")}
    ids = set(entries)
    p = get_paths()
    roots = [*p.character_cleanup_runtime_roots(char_id="cleanup-anchor").values(),
             p.user_authored_character_dir(char_id="cleanup-anchor"),
             p.legacy_authored_character_dir(char_id="cleanup-anchor"),
             p.user_live2d_root() / "cleanup-anchor", p.user_model3d_root() / "cleanup-anchor"]
    for anchor in roots:
        if anchor.parent.exists():
            for child in anchor.parent.iterdir():
                if child.is_dir() and anchor.name == "cleanup-anchor" and not (anchor.parent.name == 'activity' and child.name == 'reading'):
                    try:
                        ids.add(validate_character_id(child.name))
                    except ValueError:
                        pass
                elif anchor.suffix == '.json' and child.is_file() and child.suffix == '.json':
                    try:
                        ids.add(validate_character_id(child.stem))
                    except ValueError:
                        pass
    return [{"id": cid, "label": entries[cid].label if cid in entries else cid,
             "orphan": cid not in entries} for cid in sorted(ids) if cid not in {"_stage", "_private"}]


def _write_manifest(folder: Path, value: dict) -> None:
    from core.safe_write import safe_write_json
    if not safe_write_json(folder / "manifest.json", value, keep_bak=False):
        raise OSError("cleanup manifest write failed")


def remove(char_id: str, include_data: bool, fingerprint: str) -> dict:
    plan = preview(char_id, include_data)
    if plan["fingerprint"] != fingerprint:
        raise CleanupConflict("数据已变化，请重新预览")
    if not plan["can_delete"]:
        raise CleanupConflict("；".join(plan["blockers"]) or "没有可删除内容")
    targets = _targets(char_id, include_data)
    operation = uuid.uuid4().hex
    folder = get_paths().character_cleanup_archive_dir() / operation
    folder.mkdir(parents=True)
    # Source locators are private archive metadata, never exposed by the API.
    manifest = {"id": operation, "char_id": char_id, "include_data": include_data,
                "status": "prepared", "sources": {key: str(path.resolve()) for key, path in targets.items()}}
    _write_manifest(folder, manifest)
    moved = []
    try:
        for key, source in targets.items():
            destination = _checked(folder / key, folder)
            shutil.move(str(source), str(destination))
            moved.append((key, source))
        from core.asset_registry import reload_registry
        reload_registry()
        manifest["status"] = "deleted"
        _write_manifest(folder, manifest)
    except Exception:
        rollback_failed = False
        for key, source in reversed(moved):
            try:
                source.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(folder / key), str(source))
            except OSError:
                rollback_failed = True
        manifest["status"] = "rollback_failed" if rollback_failed else "rolled_back"
        _write_manifest(folder, manifest)
        from core.asset_registry import reload_registry
        reload_registry()
        raise
    return {"id": operation, "char_id": char_id, "status": "deleted", "recoverable": True}


def history() -> list[dict]:
    root = get_paths().character_cleanup_archive_dir()
    rows = []
    if root.exists():
        for path in root.glob("*/manifest.json"):
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
                rows.append({key: value[key] for key in ("id", "char_id", "include_data", "status")})
            except (OSError, ValueError, KeyError):
                rows.append({"id": path.parent.name, "status": "unreadable"})
    return rows


def restore(operation: str) -> dict:
    if not (len(operation) == 32 and all(c in "0123456789abcdef" for c in operation)):
        raise ValueError("invalid operation id")
    root = get_paths().character_cleanup_archive_dir()
    folder = _checked(root / operation, root)
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    if manifest["status"] not in {"deleted", "rollback_failed", "prepared"}:
        raise CleanupConflict("该记录不可恢复")
    char_id = validate_character_id(manifest["char_id"])
    reasons = _active_reasons(char_id)
    if reasons:
        raise CleanupConflict("；".join(reasons))
    # Derive destinations afresh. Never trust arbitrary stored absolute paths.
    sources = manifest["sources"]
    destinations = _restore_targets(char_id, manifest["include_data"], sources)
    if manifest['status'] != 'deleted':
        destinations = {key: destination for key, destination in destinations.items() if (folder / key).exists()}
    if any(key.startswith('card_') for key in destinations) and _targets(char_id, False):
        raise CleanupConflict("已有同 ID 角色卡，不能覆盖恢复")
    for key, destination in destinations.items():
        _checked(destination, destination.parent)
        _checked(folder / key, folder)
        if destination.exists():
            raise CleanupConflict("已有同 ID 数据，恢复会覆盖，请先处理冲突")
    moved = []
    try:
        for key, destination in destinations.items():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(folder / key), str(destination))
            moved.append((key, destination))
        manifest["status"] = "restored"
        _write_manifest(folder, manifest)
    except Exception:
        for key, destination in reversed(moved):
            shutil.move(str(destination), str(folder / key))
        raise
    from core.asset_registry import reload_registry
    reload_registry()
    return {"id": operation, "char_id": char_id, "status": "restored"}


def _restore_targets(char_id: str, include_data: bool, sources: dict) -> dict[str, Path]:
    # Rebuild the complete allowlist using the same accessors as removal.
    p = get_paths()
    allowed = p.character_cleanup_runtime_roots(char_id=char_id) if include_data else {}
    for layer, root in (("card_user", p.user_character_cards_dir()), ("card_legacy", p.legacy_character_cards_dir())):
        for ext in ("json", "txt", "md"):
            allowed[f"{layer}_{ext}"] = root / f"{char_id}.{ext}"
    if include_data:
        allowed.update({"authored_user": p.user_authored_character_dir(char_id="cleanup-anchor").parent / char_id,
                        "authored_legacy": p.legacy_authored_character_dir(char_id="cleanup-anchor").parent / char_id,
                        "live2d": p.user_live2d_root() / char_id, "model3d": p.user_model3d_root() / char_id})
        for layer, root in (("avatar_user", p.user_reality_dir()), ("avatar_legacy", p.legacy_reality_dir())):
            for ext in ("png", "jpg", "jpeg", "webp"):
                allowed[f"{layer}_{ext}"] = root / "avatars" / f"{char_id}.{ext}"
    if not set(sources).issubset(allowed):
        raise CleanupConflict("备份范围不符合清理合同")
    return {key: allowed[key] for key in sources}
