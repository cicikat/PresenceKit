"""
scripts/clean_history_leak.py

Scans all history.json files for assistant turns that reference internal
trigger/system concepts (e.g. "触发标记", "日志残留逻辑") that leaked during
an earlier period when prompt scrubbing was not in place.

Default: dry-run (prints matches, writes nothing).
Pass --apply to actually remove matched turns (backs up the file first).

Run ONLY while the bot is stopped — concurrent writes from the live process
will race with this script.

Usage:
    python scripts/clean_history_leak.py
    python scripts/clean_history_leak.py --apply
"""
import argparse
import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MEMORY_ROOT = ROOT / "data" / "runtime" / "memory"

LEAK_PATTERNS = [
    r"触发标记",
    r"触发[:：]",
    r"日志残留",
    r"涌进来.*?标记",
    r"后台进程",
    r"_pipeline_send",
    r"桌宠.*?界面",
    r"发来的那些触发",
]

_RE = re.compile("|".join(LEAK_PATTERNS))


def _scan_file(path: Path) -> list[int]:
    """Return indices of turns whose content matches a leak pattern."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"  [skip] {path}: {exc}")
        return []

    if not isinstance(data, list):
        return []

    hits = []
    for i, turn in enumerate(data):
        content = turn.get("content", "")
        if isinstance(content, list):
            content = " ".join(
                c.get("text", "") if isinstance(c, dict) else str(c)
                for c in content
            )
        if _RE.search(content):
            hits.append(i)
    return hits


def main() -> None:
    parser = argparse.ArgumentParser(description="Clean leaked internal vocabulary from history.json files.")
    parser.add_argument("--apply", action="store_true", help="Actually remove matched turns (backup created first).")
    args = parser.parse_args()

    if not MEMORY_ROOT.exists():
        print(f"Memory root not found: {MEMORY_ROOT}")
        sys.exit(1)

    history_files = list(MEMORY_ROOT.rglob("history.json"))
    if not history_files:
        print("No history.json files found.")
        return

    total_files = 0
    total_turns = 0

    for hf in sorted(history_files):
        hits = _scan_file(hf)
        if not hits:
            continue

        total_files += 1
        total_turns += len(hits)

        data = json.loads(hf.read_text(encoding="utf-8"))
        print(f"\n{hf.relative_to(ROOT)}  — {len(hits)} hit(s)")
        for i in hits:
            turn = data[i]
            role = turn.get("role", "?")
            content = turn.get("content", "")
            if isinstance(content, list):
                content = " ".join(
                    c.get("text", "") if isinstance(c, dict) else str(c)
                    for c in content
                )
            snippet = content[:120].replace("\n", " ")
            print(f"  [{i}] {role}: {snippet!r}")

        if args.apply:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup = hf.with_suffix(f".bak_{ts}")
            shutil.copy2(hf, backup)
            cleaned = [t for j, t in enumerate(data) if j not in hits]
            tmp = hf.with_suffix(".tmp")
            tmp.write_text(json.dumps(cleaned, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(hf)
            print(f"  → applied: removed {len(hits)} turn(s), backup at {backup.name}")

    print(f"\n{'='*60}")
    if total_turns == 0:
        print("No leaking turns found.")
    elif args.apply:
        print(f"Applied: removed {total_turns} turn(s) across {total_files} file(s).")
    else:
        print(f"Dry-run: found {total_turns} turn(s) across {total_files} file(s).")
        print("Re-run with --apply to remove them (bot must be stopped first).")


if __name__ == "__main__":
    main()
