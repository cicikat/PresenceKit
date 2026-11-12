"""Machine-readable evidence refers only to the three commits actually run."""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess


def verified(evidence, repositories):
    return bool(
        evidence.get("recorded_results") == "compatible"
        and re.fullmatch(r"https://github\.com/[^/]+/[^/]+/actions/runs/\d+", evidence.get("run_url", ""))
        and all(evidence.get("results", {}).get(role) == "success"
                and evidence.get("shas", {}).get(role) == repo["sha"]
                for role, repo in repositories.items())
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    roles = ("backend", "desktop", "mobile")
    requested = {role: os.environ[f"{role.upper()}_SHA"] for role in roles}
    actual = {}
    for role in roles:
        result = subprocess.run(["git", "-C", str(Path.cwd().parent / role), "rev-parse", "HEAD"],
                                capture_output=True, text=True, check=False)
        actual[role] = result.stdout.strip() if result.returncode == 0 else ""
    results = {role: os.environ.get(f"{role.upper()}_RESULT", "skipped") for role in roles}
    success = all(re.fullmatch(r"[0-9a-f]{40}", requested[role])
                  and actual[role] == requested[role] and results[role] == "success" for role in roles)
    evidence = {"requested_shas": requested, "shas": actual, "results": results,
                "recorded_results": "compatible" if success else "not_verified",
                "run_url": os.environ["RUN_URL"]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8", newline="\n")
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
