"""Preview (default) or enqueue bounded historical user evidence, never profile guesses."""
from __future__ import annotations
import argparse
import hashlib
import json
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def candidates(uid, char_id, limit=20):
    from core.memory.short_term import load
    from core.food_memory import _FOOD_SIGNAL
    rows = []
    for entry in load(uid, char_id=char_id):
        text = entry.get('content', '')
        if entry.get('role') != 'user' or entry.get('asr_low_confidence') or not isinstance(text, str):
            continue
        if not _FOOD_SIGNAL.search(text):
            continue
        ts = entry.get('ts', entry.get('timestamp'))
        if isinstance(ts, bool) or not isinstance(ts, (int, float)):
            continue
        key = 'history_' + hashlib.sha256(json.dumps([ts, text], ensure_ascii=False).encode()).hexdigest()
        rows.append({'message_id': key, 'ts': float(ts), 'text': text})
    return rows[-limit:]


def migrate(uid, char_id, *, apply=False, limit=20):
    if not 1 <= limit <= 50:
        raise ValueError('limit must be 1..50')
    rows = candidates(uid, char_id, limit)
    backup = None
    if apply and rows:
        from core import food_memory
        root = food_memory.get_paths().food_memory_db().parent
        root.mkdir(parents=True, exist_ok=True)
        backup = root / ('migration_' + str(time.time_ns()))
        backup.mkdir()
        (backup / 'source.json').write_bytes(json.dumps({'uid': uid, 'char_id': char_id, 'rows': rows}, ensure_ascii=False).encode())
        with food_memory.connection() as source, sqlite3.connect(backup / 'records.sqlite3') as target:
            source.backup(target)
        for row in rows:
            food_memory.enqueue(uid, char_id, row['message_id'], row['text'], row['ts'])
    return {'mode': 'apply' if apply else 'dry-run', 'candidate_count': len(rows),
            'message_ids': [r['message_id'] for r in rows], 'backup_created': backup is not None,
            'note': '仅排队原始用户证据；不从概要计数，不自动改写原记忆。下一轮或管理面重试执行抽取。'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--uid', required=True)
    parser.add_argument('--char-id', required=True)
    parser.add_argument('--limit', type=int, default=20)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    print(json.dumps(migrate(args.uid, args.char_id, apply=args.apply, limit=args.limit), ensure_ascii=False, indent=2))
