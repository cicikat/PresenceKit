"""Prove existing business tests kill five narrowly specified regressions."""

import json
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

from tests.quality_mutations import MUTATIONS


def main():
    root = Path(__file__).resolve().parents[1]
    output = root / ".tmp" / "quality-mutations"
    output.mkdir(parents=True, exist_ok=True)
    results = {}
    env = {key: value for key, value in os.environ.items()
           if not any(word in key.upper() for word in ("GOOGLE", "GMAIL", "SECRET", "TOKEN", "API_KEY"))}
    env["PYTHONIOENCODING"] = "utf-8"
    for name, (*_, target) in MUTATIONS.items():
        xml = output / f"{name}.xml"
        result = subprocess.run([sys.executable, "-m", "pytest", "-q", target,
                                 "-p", "tests.quality_mutations", "--quality-mutation", name,
                                 f"--junitxml={xml}", "--tb=short"], cwd=root, env=env,
                                capture_output=True, text=True, encoding="utf-8", timeout=90)
        (output / f"{name}.log").write_text(result.stdout + result.stderr, encoding="utf-8", newline="\n")
        cases = list(ET.parse(xml).getroot().iter("testcase")) if xml.exists() else []
        # Errors, missing tests and infrastructure failures cannot kill a mutant.
        killed = (result.returncode == 1 and len(cases) == 1
                  and cases[0].find("error") is None and cases[0].find("failure") is not None
                  and any(marker in cases[0].find("failure").get("message", "")
                          for marker in ("AssertionError", "assert ", "DID NOT RAISE")))
        results[name] = {"test": target, "killed": killed, "exit_code": result.returncode}
        print(f"{name}: {'KILLED' if killed else 'NOT VERIFIED'}")
    (output / "results.json").write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8", newline="\n")
    return 0 if all(item["killed"] for item in results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
