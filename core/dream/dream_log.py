"""
Dream session log — current_dream.jsonl writer/reader.

Every record is tagged with DREAM_ARTIFACT_SENTINEL so reality loaders
can never retrieve it.

Active session:  dreams/{char_id}/tmp/current_dream_{uid}.jsonl
After close:     dreams/{char_id}/archive/dream_{dream_id}.jsonl  (dead storage, never loaded)
"""

import json
import logging
import time
from pathlib import Path
from typing import Any

from core.safe_write import safe_append_jsonl
from core.sandbox import get_paths, safe_user_id
from core.dream.dream_state import apply_dream_artifact_sentinel
from core.data_paths import DEFAULT_CHAR_ID

logger = logging.getLogger(__name__)


def _tmp_path(user_id: str | int, *, char_id: str = DEFAULT_CHAR_ID) -> Path:
    d = get_paths().dreams_tmp_dir(char_id=char_id)
    d.mkdir(parents=True, exist_ok=True)
    return d / f"current_dream_{safe_user_id(user_id)}.jsonl"


def _archive_dir(*, char_id: str = DEFAULT_CHAR_ID) -> Path:
    d = get_paths().dreams_archive_dir(char_id=char_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def append_turn(
    user_id: str | int,
    dream_id: str,
    role: str,
    content: str,
    extra: dict[str, Any] | None = None,
    *,
    char_id: str = DEFAULT_CHAR_ID,
) -> bool:
    """Append one dream turn to current_dream.jsonl with sentinel fields."""
    record: dict[str, Any] = {
        "dream_id": dream_id,
        "ts": time.time(),
        "role": role,
        "content": content,
    }
    if extra:
        record.update(extra)
    record = apply_dream_artifact_sentinel(record)
    return safe_append_jsonl(_tmp_path(user_id, char_id=char_id), record)


def read_current(user_id: str | int, *, char_id: str = DEFAULT_CHAR_ID) -> list[dict[str, Any]]:
    """Read all turns from the active dream session."""
    path = _tmp_path(user_id, char_id=char_id)
    if not path.exists():
        return []
    turns: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            turns.append(json.loads(line))
        except Exception:
            pass
    return turns


def archive_current(user_id: str | int, dream_id: str, *, char_id: str = DEFAULT_CHAR_ID) -> bool:
    """Move current_dream.jsonl to archive/dream_{dream_id}.jsonl (dead storage)."""
    tmp = _tmp_path(user_id, char_id=char_id)
    if not tmp.exists():
        return True
    dest = _archive_dir(char_id=char_id) / f"dream_{dream_id}.jsonl"
    try:
        current_bytes = tmp.read_bytes()
        if dest.exists():
            # A retry after a partial close must never overwrite an existing
            # archive.  Only the exact same transcript can be acknowledged.
            if dest.read_bytes() != current_bytes:
                logger.error("[dream_log] archive collision uid=%s dream_id=%s", user_id, dream_id)
                return False
            tmp.unlink()
            logger.info("[dream_log] archive already present uid=%s dream_id=%s", user_id, dream_id)
            return True
        dest.write_bytes(current_bytes)
        tmp.unlink()
        logger.info(f"[dream_log] archived uid={user_id} dream_id={dream_id} -> {dest.name}")
        return True
    except Exception as e:
        logger.error(f"[dream_log] archive failed uid={user_id}: {e}")
        return False


def prune_archive(max_files: int = 200, *, char_id: str = DEFAULT_CHAR_ID) -> int:
    """当 archive 文件数超过 max_files 时，按 mtime 删除最旧的。返回删除数。
    archive 是 write-once dead storage，distill/summary 仅在 close 时读一次，之后无 loader 读取。
    """
    archive_dir = get_paths().dreams_archive_dir(char_id=char_id)
    if not archive_dir.exists():
        return 0
    files = sorted(archive_dir.glob("dream_*.jsonl"), key=lambda f: f.stat().st_mtime)
    excess = len(files) - max_files
    if excess <= 0:
        return 0
    count = 0
    for f in files[:excess]:
        try:
            f.unlink()
            count += 1
            logger.info("[dream_log] archive pruned: %s", f.name)
        except Exception as e:
            logger.error("[dream_log] archive prune 失败 %s: %s", f.name, e)
    return count


def clear_current(user_id: str | int, *, char_id: str = DEFAULT_CHAR_ID) -> bool:
    """Delete current_dream.jsonl without archiving (emergency force-clear)."""
    tmp = _tmp_path(user_id, char_id=char_id)
    try:
        if tmp.exists():
            tmp.unlink()
        return True
    except Exception as e:
        logger.error(f"[dream_log] clear failed uid={user_id}: {e}")
        return False


# Minimum user turns for a dream to count as "valid" (≤ this → test/discard).
VALID_DREAM_MIN_USER_TURNS = 3


def count_valid_dreams(*, char_id: str = DEFAULT_CHAR_ID) -> dict:
    """Count valid archived dreams for the given character.

    Scans archive directory; a dream is valid if it contains more than
    VALID_DREAM_MIN_USER_TURNS user turns. Corrupt/missing files are skipped.
    Returns {"total_valid": int, "total_archived": int, "last_dream_at": float|None}.
    Pure read-only; all paths via get_paths().
    """
    archive_dir = get_paths().dreams_archive_dir(char_id=char_id)
    if not archive_dir.exists():
        return {"total_valid": 0, "total_archived": 0, "last_dream_at": None}

    files = list(archive_dir.glob("dream_*.jsonl"))
    total_archived = len(files)
    total_valid = 0
    last_dream_at: float | None = None

    for f in files:
        user_turns = 0
        file_last_ts: float | None = None
        try:
            for line in f.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except Exception:
                    continue
                if record.get("role") == "user":
                    user_turns += 1
                ts = record.get("ts")
                if isinstance(ts, (int, float)) and ts > 0:
                    if file_last_ts is None or ts > file_last_ts:
                        file_last_ts = float(ts)
        except Exception as e:
            logger.warning("[dream_log] count_valid_dreams: skipping %s: %s", f.name, e)
            continue

        if user_turns > VALID_DREAM_MIN_USER_TURNS:
            total_valid += 1
            if file_last_ts is not None:
                if last_dream_at is None or file_last_ts > last_dream_at:
                    last_dream_at = file_last_ts
            else:
                # fall back to file mtime when no ts field
                mtime = f.stat().st_mtime
                if last_dream_at is None or mtime > last_dream_at:
                    last_dream_at = mtime

    return {
        "total_valid": total_valid,
        "total_archived": total_archived,
        "last_dream_at": last_dream_at,
    }
