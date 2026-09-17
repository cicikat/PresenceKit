"""Tracked-text line-ending guard (cc-tasks/254).

Repository contract:
  - tracked text is LF
  - *.bat / *.cmd stay CRLF for cmd.exe
  - mixed LF/CRLF is never allowed

Only git-tracked files are scanned. data/, userdata/, and other ignored
paths stay out. Non-git checkouts skip rather than fail.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent

_BINARY_EXT = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico",
    ".zip", ".gz", ".7z", ".exe", ".dll", ".so", ".dylib",
    ".pyc", ".pyo", ".db", ".sqlite", ".sqlite3",
    ".wav", ".mp3", ".mp4", ".ogg",
    ".pdf", ".docx", ".xlsx", ".pptx",
    ".woff", ".woff2", ".ttf", ".otf", ".eot",
    ".bin", ".pkl", ".pt", ".onnx", ".safetensors",
    ".whl", ".egg",
})
_CRLF_EXT = frozenset({".bat", ".cmd"})


def _tracked_files() -> list[str] | None:
    try:
        out = subprocess.check_output(
            ["git", "ls-files", "-z"],
            cwd=PROJECT_ROOT,
            stderr=subprocess.DEVNULL,
        )
    except (FileNotFoundError, subprocess.CalledProcessError, OSError):
        return None
    return [p.decode("utf-8", "surrogateescape") for p in out.split(b"\0") if p]


def _is_binary(path: Path, data: bytes) -> bool:
    if path.suffix.lower() in _BINARY_EXT:
        return True
    return b"\0" in data[:8192]


def test_tracked_text_uses_lf_except_batch_files() -> None:
    tracked = _tracked_files()
    if tracked is None:
        pytest.skip("not a git working copy")

    lf_violations: list[str] = []
    bat_violations: list[str] = []

    for rel in tracked:
        path = PROJECT_ROOT / rel
        if not path.is_file():
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        if _is_binary(path, data):
            continue

        crlf = data.count(b"\r\n")
        lone_cr = data.count(b"\r") - crlf
        lone_lf = data.count(b"\n") - crlf
        ext = path.suffix.lower()

        if ext in _CRLF_EXT:
            if lone_lf or lone_cr:
                bat_violations.append(
                    f"{rel} (crlf={crlf} lf={lone_lf} cr={lone_cr})"
                )
        elif crlf or lone_cr:
            lf_violations.append(
                f"{rel} (crlf={crlf} lf={lone_lf} cr={lone_cr})"
            )

    assert not lf_violations, (
        "tracked text must be LF (see .gitattributes). "
        "CRLF/mixed files:\n  " + "\n  ".join(lf_violations)
    )
    assert not bat_violations, (
        "*.bat / *.cmd must be pure CRLF. Mixed files:\n  "
        + "\n  ".join(bat_violations)
    )
