"""Check that architecture debt only shrinks relative to an explicit Git baseline.

Only the Python standard library and Git are used. This check complements the
AST/ledger equality test; neither check substitutes for the other or for review.
"""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys


DEBT_FILE = "docs/refactors/migration-cutover-dependencies.json"
KEY_FIELDS = ("source", "rule", "owner", "target")


def git(root, *args):
    return subprocess.run(["git", *args], cwd=root, check=True,
                          capture_output=True, text=True, encoding="utf-8").stdout.strip()


def debt_rows(document):
    result = {}
    for row in document["dependencies"]:
        if set(row) != {*KEY_FIELDS, "count"}:
            raise ValueError("invalid debt row fields")
        key = tuple(row[field] for field in KEY_FIELDS)
        if not all(isinstance(value, str) and value for value in key):
            raise ValueError("invalid debt identity")
        if type(row["count"]) is not int or row["count"] < 1 or key in result:
            raise ValueError("invalid count or duplicate debt identity")
        result[key] = row["count"]
    return result


def ci_baseline(root, environ):
    event = json.loads(Path(environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
    name = environ["GITHUB_EVENT_NAME"]
    if name == "push" and event["ref"] == "refs/heads/main":
        after = event["after"]
        if not re.fullmatch(r"[0-9a-f]{40}", after):
            raise ValueError("invalid push head")
        # First parent of the pushed head, not origin/main (already advanced).
        return git(root, "rev-parse", after + "^1")
    branch = event["pull_request"]["base"]["ref"] if name == "pull_request" else "main"
    git(root, "check-ref-format", "--branch", branch)
    # Resolve the current target HEAD, not the PR's possibly stale base SHA.
    git(root, "fetch", "--no-tags", "origin",
        "+refs/heads/" + branch + ":refs/remotes/origin/" + branch)
    return git(root, "rev-parse", "refs/remotes/origin/" + branch)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--baseline-ref", help="Explicit Git revision; local reproduction uses origin/main")
    group.add_argument("--ci", action="store_true", help="Choose/fetch baseline from GitHub event")
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    try:
        reference = ci_baseline(root, os.environ) if args.ci else args.baseline_ref
        revision = git(root, "rev-parse", "--verify", "--end-of-options", reference + "^{commit}")
        baseline = debt_rows(json.loads(git(root, "show", revision + ":" + DEBT_FILE)))
        current = debt_rows(json.loads((root / DEBT_FILE).read_text(encoding="utf-8")))
        expanded = [key for key, count in current.items() if key not in baseline or count > baseline[key]]
        if expanded:
            raise ValueError("debt expansion relative to " + revision + ": " + repr(expanded))
        print(f"Debt convergence passed: {len(baseline)} -> {len(current)} rows; baseline {revision}")
    except (ValueError, KeyError, OSError, subprocess.CalledProcessError) as exc:
        print("Architecture debt check failed: " + str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
