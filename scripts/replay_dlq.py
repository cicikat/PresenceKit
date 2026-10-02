#!/usr/bin/env python3
"""Replay dead-letter-queue slow tasks (工单 G): dry-run first, small batches, re-entrant.

Only the two memory-fixation task types are replayable here; practice_session / toy_autogrow entries are
tied to a past moment and stay in place untouched.

  dry-run (default)   classify every entry, change nothing
  --apply             replay at most --batch-size entries, then move each *succeeded* entry to
                      ``<dlq>/replayed/`` (never deleted).  A failed entry stays where it is.

Why these choices:
* ``consolidate_to_identity`` works on a whole (uid, char) scope, so 48 queued copies for one scope are
  one unit of work: it is replayed once and the other copies are archived as ``covered``.
* ``reflect_to_episodic`` is idempotent (promoted mid-term entries are skipped).  Entries whose mid-term
  items no longer exist or were already promoted are archived as ``already_handled`` without a model call.
* ``--apply`` refuses to run while the service is running: the per-user locks are in-process, so a second
  process would race with live conversation writes.  It also stops after ``--max-failures`` consecutive
  failures instead of burning the backlog on a still-broken upstream.
Re-running is safe: archived entries are no longer in the queue.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

REPLAYABLE = ("consolidate_to_identity", "reflect_to_episodic")


def _service_pid() -> int | None:
    """PID from the lifecycle marker if that process is alive, else None."""
    from core.runtime_service_state import marker_path
    try:
        pid = int(json.loads(marker_path().read_text(encoding="utf-8")).get("pid"))
    except (OSError, ValueError, TypeError):
        return None
    try:
        import psutil
        return pid if psutil.pid_exists(pid) else None
    except ImportError:
        import subprocess
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"], capture_output=True, text=True).stdout
        return pid if str(pid) in out else None


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def plan(dlq: Path) -> list[dict]:
    from core.memory import mid_term
    from core.memory.fixation_pipeline import _get_scope_from_payload
    rows: list[dict] = []
    seen_scopes: set[tuple[str, str]] = set()
    for path in sorted(dlq.glob("*.json")):
        row = {"file": path.name, "path": path, "type": "", "action": "", "detail": ""}
        rows.append(row)
        try:
            task = _load(path)["task"]
            row["type"] = task.get("task_type", "")
            payload = task.get("payload") or {}
        except (OSError, ValueError, KeyError):
            row.update(action="skip", detail="unreadable")
            continue
        if row["type"] not in REPLAYABLE:
            row.update(action="skip", detail="type_not_replayed")
            continue
        try:
            scope = _get_scope_from_payload(payload, "dlq_replay")
        except Exception as exc:  # noqa: BLE001
            row.update(action="skip", detail=f"bad_scope:{type(exc).__name__}")
            continue
        key = (scope.uid, scope.character_id)
        row["scope"] = key
        if row["type"] == "consolidate_to_identity":
            if key in seen_scopes:
                row.update(action="archive", detail="covered")
            else:
                seen_scopes.add(key)
                row.update(action="replay", detail="scope_first")
            continue
        wanted = set(payload.get("mid_ids") or [])
        live = [e for e in mid_term.load(scope.uid, char_id=scope.character_id)
                if e.get("mid_id") in wanted and not e.get("promoted_to_episodic_id") and not e.get("is_trigger_turn")]
        if live:
            row.update(action="replay", detail=f"{len(live)}_mid_term_items_pending")
        else:
            row.update(action="archive", detail="already_handled")
    return rows


async def _replay_one(row: dict) -> None:
    from core.memory.fixation_pipeline import handler_consolidate_to_identity, handler_reflect_to_episodic
    payload = _load(row["path"])["task"]["payload"]
    handler = handler_consolidate_to_identity if row["type"] == "consolidate_to_identity" else handler_reflect_to_episodic
    await asyncio.wait_for(handler(payload), timeout=240)


def _archive(row: dict, outcome: str) -> None:
    target = row["path"].parent / "replayed"
    target.mkdir(exist_ok=True)
    shutil.move(str(row["path"]), str(target / f"{row['file'][:-5]}.{outcome}.json"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--batch-size", type=int, default=5, help="max replays (model calls) per run")
    parser.add_argument("--max-failures", type=int, default=2, help="stop after this many consecutive failures")
    args = parser.parse_args()

    from core.sandbox import get_paths
    dlq = get_paths().dead_letter_queue()
    rows = plan(dlq)
    summary: dict[str, int] = {}
    for row in rows:
        k = f"{row['action']}:{row['type'] or '?'}:{row['detail'].split(':')[0]}"
        summary[k] = summary.get(k, 0) + 1
    print(json.dumps({"mode": "apply" if args.apply else "dry_run", "queue": len(rows),
                      "plan": dict(sorted(summary.items()))}, ensure_ascii=False, indent=2))
    if not args.apply:
        return 0
    pid = _service_pid()
    if pid is not None:
        print(f"拒绝执行：服务正在运行（pid {pid}）。进程内的用户锁管不到本进程，会和在线对话抢写记忆；请先停服务。", file=sys.stderr)
        return 3

    done = failed = consecutive = 0
    archived = {"covered": 0, "already_handled": 0}
    replayed_scopes: set[tuple] = set()
    for row in rows:
        if row["action"] == "archive" and row["detail"] == "already_handled":
            _archive(row, "already_handled")
            archived["already_handled"] += 1
    for row in rows:
        if row["action"] != "replay":
            continue
        if done + failed >= args.batch_size:
            break
        t0 = time.perf_counter()
        try:
            asyncio.run(_replay_one(row))
        except Exception as exc:  # noqa: BLE001 - reported, entry stays in the queue
            failed += 1
            consecutive += 1
            print(f"FAIL {row['file']} {type(exc).__name__}: {str(exc)[:100]}")
            if consecutive >= args.max_failures:
                print(f"连续失败 {consecutive} 次，停止（上游可能仍不可用）。")
                break
            continue
        consecutive = 0
        done += 1
        _archive(row, "replayed")
        replayed_scopes.add(row["scope"])
        print(f"OK   {row['file']} {time.perf_counter() - t0:.1f}s")
    # Copies covered by a scope that was actually replayed (this or an earlier run) are archived too.
    for row in rows:
        if row["action"] == "archive" and row["detail"] == "covered" and row["scope"] in replayed_scopes:
            _archive(row, "covered")
            archived["covered"] += 1
    print(json.dumps({"replayed": done, "failed": failed, "archived": archived,
                      "remaining": len(list(dlq.glob('*.json')))}, ensure_ascii=False))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
