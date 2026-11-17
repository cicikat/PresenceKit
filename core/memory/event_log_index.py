"""Rebuildable, process-local fingerprints for append-only Markdown projections.

Read each unchanged file once, streaming records rather than loading full_log.
The eight-entry LRU is invalidated by replacement, truncation or external edits.
It is an acceleration cache, never evidence or a second durable truth source.
"""
from collections import OrderedDict
from hashlib import sha256
from pathlib import Path
import re
from threading import RLock

lock = RLock()
_cache: OrderedDict = OrderedDict()
_META = re.compile(r"^> .*\bspeaker:(user|assistant)\b.*\bturn_id:(\S+)")
_BODY = re.compile(r"^\*\*[^\n]*?\*\*：")


def _fingerprint(path):
    stat = path.stat()
    return stat.st_ino, stat.st_size, stat.st_mtime_ns


def records(lines):
    body = []
    for line in lines:
        meta = _META.match(line)
        if meta:
            if body:
                yield (meta[2], meta[1], sha256("".join(body).encode("utf-8")).digest())
            body = []
        elif _BODY.match(line):
            body = [_BODY.sub("", line, count=1)]
        elif body:
            body.append(line)


def contains(path: Path, entry: str, turn_id: str) -> bool:
    with lock:
        stamp = _fingerprint(path)
        cached = _cache.get(path)
        if cached is None or cached[0] != stamp:
            with path.open(encoding="utf-8") as stream:
                keys = set(records(stream))
            cached = (stamp, keys)
            _cache[path] = cached
        _cache.move_to_end(path)
        while len(_cache) > 8:
            _cache.popitem(last=False)
        return any(key in cached[1] for key in records(entry.splitlines(keepends=True)) if key[0] == turn_id)


def appended(path: Path, entry: str) -> None:
    """Called under lock only after the append stream successfully closes."""
    cached = _cache.get(path)
    if cached is not None:
        cached[1].update(records(entry.splitlines(keepends=True)))
        _cache[path] = (_fingerprint(path), cached[1])
