#!/usr/bin/env python3
"""Dry-run-first, re-entrant import of leftover reminder JSON into Runtime schedules."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from core.reminder_migration import (
    apply_legacy_reminder_import,
    inventory_legacy_reminders,
    rollback_legacy_reminder_import,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uid", help="owner uid; required to claim an unclaimed leftover file")
    parser.add_argument("--char-id", help="historical owner char_id; freeze once, never from active")
    parser.add_argument("--apply", action="store_true", help="import after writing a local leftover backup")
    parser.add_argument("--backup-dir", type=Path, help="directory to copy leftover JSON into")
    parser.add_argument("--rollback", type=Path, help="restore leftover JSON from a previous backup dir")
    args = parser.parse_args()

    if args.rollback:
        report = rollback_legacy_reminder_import(args.rollback)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report.get("ok") else 2
    if not args.apply:
        report = inventory_legacy_reminders(char_id=args.char_id, uid=args.uid)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    report = apply_legacy_reminder_import(char_id=args.char_id, uid=args.uid, backup_dir=args.backup_dir)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report.get("owner", {}).get("claimed"):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
