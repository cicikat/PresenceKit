"""Fail CI for empty suites, failures and unclassified skips in JUnit evidence."""

import argparse
from pathlib import Path
import xml.etree.ElementTree as ET


def skip_category(case):
    skipped = case.find("skipped")
    reason = skipped.get("message", "") if skipped is not None else ""
    module = case.get("classname", "")
    if "test_windows_accept_recovery" in module and "Windows IOCP regression" in reason:
        return "windows_only"
    if "test_activity_contract" in module and reason.startswith("SKIP_CROSS_REPO=1:"):
        return "cross_repo_not_verified"
    if "test_vector_store" in module and reason == "sqlite_vec extension not installed":
        return "optional_extension"
    if any(name in module for name in ("test_agent_runtime_workspace", "test_character_self", "test_fs_browse")) and reason.startswith(("symlink unavailable:", "hardlink unavailable:")):
        return "optional_filesystem_capability"
    return None


def validate(path, *, no_skips=False):
    cases = list(ET.parse(path).getroot().iter("testcase"))
    problems = [] if cases else ["empty test evidence"]
    for case in cases:
        name = case.get("classname", "") + "::" + case.get("name", "")
        if case.find("failure") is not None or case.find("error") is not None:
            problems.append(f"failed: {name}")
        skipped = case.find("skipped")
        if skipped is not None:
            # Expected failures remain visible; pytest strict xfail governs XPASS.
            if skipped.get("type") == "pytest.xfail" and not no_skips:
                print(f"XFAIL: {name}: {skipped.get('message')}")
                continue
            category = skip_category(case)
            print(f"SKIP [{category or 'unexpected'}]: {name}: {skipped.get('message')}")
            if no_skips or category is None:
                problems.append(f"unverified: {name}")
    return problems


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("--no-skips", action="store_true")
    args = parser.parse_args()
    problems = validate(args.path, no_skips=args.no_skips)
    for problem in problems:
        print(problem)
    return int(bool(problems))


if __name__ == "__main__":
    raise SystemExit(main())
