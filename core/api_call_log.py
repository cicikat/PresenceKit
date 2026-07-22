"""Fail-open ledger for outbound API calls; never stores request bodies or secrets."""
from __future__ import annotations

import time
from collections import Counter

from core.safe_write import rotate_jsonl_if_needed, safe_append_jsonl
from core.sandbox import get_paths

_MAX_BYTES = 5 * 1024 * 1024
_KEEP_N = 7


def append(
    *,
    caller: str,
    purpose: str,
    provider: str,
    model: str,
    duration_ms: int,
    ok: bool,
    output_hint: str = "",
) -> None:
    try:
        path = get_paths().api_call_log()
        safe_append_jsonl(path, {
            "ts": time.time(),
            "caller": caller,
            "purpose": purpose,
            "provider": provider,
            "model": model,
            "duration_ms": max(0, int(duration_ms)),
            "ok": bool(ok),
            "output_hint": str(output_hint)[:120],
        })
        rotate_jsonl_if_needed(path, _MAX_BYTES, _KEEP_N)
    except Exception:
        pass


def query(*, caller: str = "", provider: str = "", limit: int = 100) -> tuple[list[dict], dict[str, int]]:
    import json
    try:
        path = get_paths().api_call_log()
        if not path.exists():
            return [], {}
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        rows = [
            r for r in rows
            if isinstance(r, dict)
            and (not caller or r.get("caller") == caller)
            and (not provider or r.get("provider") == provider)
        ]
        rows = rows[-limit:][::-1]
        return rows, dict(Counter(str(r.get("provider") or "unknown") for r in rows))
    except Exception:
        return [], {}
