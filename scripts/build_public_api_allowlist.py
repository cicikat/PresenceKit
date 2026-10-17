"""Generate a default-deny Nginx map from actual client route permissions.

Run from the installation root with its venv. This only writes the requested
map; operators still validate nginx and reload it explicitly.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PRIVATE_PREFIXES = ("/admin", "/observability", "/logs", "/system")
RUNTIME_AUTH_PATHS = {"/auth/whoami", "/ws/desktop", "/ws/device"}


def _scopes(dependant) -> list[str]:
    output = list(getattr(dependant.call, "_required_scopes", ()))
    for child in dependant.dependencies:
        output.extend(_scopes(child))
    return output


def route_rules(app) -> list[tuple[str, str, bool]]:
    """Preserve backend route order, including explicit private-route denials.

    A broad client parameter route must never expose a preceding private route.
    /settings itself is not private: some of its reads are client contracts.
    """
    rules = []
    seen = set()
    options = set()
    for route in app.routes:
        dependant = getattr(route, "dependant", None)
        if dependant is None:
            continue
        required = _scopes(dependant)
        path = route.path
        private = ("admin" in required or path == "/"
                   or any(path == p or path.startswith(p + "/") for p in PRIVATE_PREFIXES))
        allowed = not private and bool(required or path in RUNTIME_AUTH_PATHS)
        pattern = re.sub(r"\(\?P<[^>]+>", "(?:", route.path_regex.pattern)
        methods = getattr(route, "methods", None) or {"GET"}
        for method in sorted(methods):
            key = method, pattern
            if key not in seen:
                rules.append((method, pattern, allowed))
                seen.add(key)
        if allowed:
            options.add(pattern)
    for pattern in sorted(options):
        if ("OPTIONS", pattern) not in seen:
            rules.append(("OPTIONS", pattern, True))
    return rules


def nginx_map(app) -> str:
    lines = ['# Generated from route scopes; regenerate after backend upgrades.',
             'map "$request_method:$uri" $presencekit_public_allowed {', '    default 0;']
    for method, pattern, allowed in route_rules(app):
        key = f"~^{method}:{pattern.removeprefix('^')}"
        lines.append(f"    {json.dumps(key)} {int(allowed)};")
    return "\n".join([*lines, "}", ""])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    from admin.admin_server import app
    args.output.write_bytes(nginx_map(app).encode("utf-8"))
    print(f"Generated {len(route_rules(app))} method/path rules; default deny remains enabled.")


if __name__ == "__main__":
    main()
