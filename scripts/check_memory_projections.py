"""Report scoped canonical vector reconciliation counts without touching legacy ownership."""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.memory.episodic_memory import EpisodicCorruptError
from core.memory.scope import MemoryScope
from core.memory.vector_reconciliation import inspect_vector_projections


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner", required=True)
    parser.add_argument("--character", required=True)
    args = parser.parse_args()
    try:
        report = inspect_vector_projections(MemoryScope.reality_scope(args.owner, args.character))
    except (EpisodicCorruptError, OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
        print(json.dumps({"status": "unavailable", "category": type(exc).__name__}), file=sys.stderr)
        return 1
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
