#!/usr/bin/env python3
"""存量 episodic 强度重算（工单 M1）：撤销旧版写入时的情绪/冲突标签加成。

默认 dry-run：只打印每个桶的条目数、将改动条数、强度分布前后对比，不写盘。
--apply：先整文件备份为 episodic.json.pre_rebalance_<ts>.bak，再逐条重算并用 safe_write 落盘，
并写一行 provenance_log。已重算过的条目带 rebalanced_at 标记，重复执行不会二次扣减。

规则（is_core 不动）：
  emotion_peak in (sad, angry)      -0.10
  标签命中旧冲突集合                  -0.20
  emotion_peak in (happy, surprised) -0.05
  下限 0.1；emotional_intensity 缺省时记为旧 strength（近似）。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

CONFLICT_TAGS = frozenset({"吵架", "道歉", "哭", "生气", "误会", "和好"})
FLOOR = 0.1


def rebalanced_strength(mem: dict) -> float:
    s = float(mem.get("strength", 0.5))
    ep = mem.get("emotion_peak", "neutral")
    tags = mem.get("topic_keywords") or mem.get("tags") or []
    if ep in ("sad", "angry"):
        s -= 0.1
    if any(t in CONFLICT_TAGS for t in tags):
        s -= 0.2
    if ep in ("happy", "surprised"):
        s -= 0.05
    return round(max(FLOOR, s), 3)


def plan_bucket(memories: list[dict]) -> tuple[list[dict], int]:
    """返回 (新条目列表, 改动条数)；不修改入参。"""
    out: list[dict] = []
    changed = 0
    for mem in memories:
        mem = dict(mem)
        if mem.get("is_core") or mem.get("rebalanced_at"):
            out.append(mem)
            continue
        old = float(mem.get("strength", 0.5))
        new = rebalanced_strength(mem)
        if mem.get("emotional_intensity") is None:
            mem["emotional_intensity"] = round(old, 3)
        if new != old:
            changed += 1
        mem["strength"] = new
        mem["rebalanced_at"] = time.time()
        out.append(mem)
    return out, changed


def distribution(memories: list[dict]) -> dict[str, int]:
    bins = {"<0.3": 0, "0.3-0.6": 0, "0.6-0.8": 0, ">=0.8": 0}
    for m in memories:
        s = float(m.get("strength", 0.5))
        key = "<0.3" if s < 0.3 else "0.3-0.6" if s < 0.6 else "0.6-0.8" if s < 0.8 else ">=0.8"
        bins[key] += 1
    return bins


def iter_buckets(only_char: str | None, only_uid: str | None):
    from core.sandbox import get_paths
    paths = get_paths()
    for char_id in paths._memory_character_ids():
        if only_char and char_id != only_char:
            continue
        root = paths.memory_char_root(char_id=char_id)
        for d in sorted(root.iterdir()) if root.exists() else []:
            if only_uid and d.name != only_uid:
                continue
            f = d / "episodic.json"
            if d.is_dir() and f.exists():
                yield char_id, d.name, f


def process_bucket(char_id: str, uid: str, path: Path, *, apply: bool) -> dict:
    memories = json.loads(path.read_text(encoding="utf-8"))
    new, changed = plan_bucket(memories)
    report = {
        "char_id": char_id, "uid": uid, "total": len(memories), "will_change": changed,
        "before": distribution(memories), "after": distribution(new),
    }
    if apply and memories:
        from core.memory import provenance_log
        from core.safe_write import safe_write_json
        backup = path.with_name(f"{path.name}.pre_rebalance_{time.strftime('%Y%m%d%H%M%S')}.bak")
        shutil.copy2(path, backup)
        if not safe_write_json(path, new):
            raise OSError(f"write failed: {path}")
        provenance_log.append(
            uid, char_id, artifact="episodic", field="strength",
            after_gist=f"rebalanced {changed}/{len(memories)}", trigger_signal="rebalance_strength",
        )
        report["backup"] = backup.name
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="备份后写盘；默认只 dry-run")
    parser.add_argument("--char-id")
    parser.add_argument("--uid")
    args = parser.parse_args()
    reports = [
        process_bucket(c, u, p, apply=args.apply)
        for c, u, p in iter_buckets(args.char_id, args.uid)
    ]
    print(json.dumps({"mode": "apply" if args.apply else "dry_run", "buckets": reports},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
