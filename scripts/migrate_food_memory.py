"""Preview (default) or enqueue bounded historical user evidence, never profile guesses."""
from __future__ import annotations
import argparse
import asyncio
import hashlib
import json
import math
import re
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def candidates(uid, char_id, limit=20):
    from core.memory.short_term import load
    from core.food_memory import _FOOD_SIGNAL, _EAT, _NEGATIVE, _POSITIVE, _TEMPORARY
    from core.memory.scope import MemoryScope
    from core.memory.path_resolver import resolve_path
    from core.memory.source_policy import role_source_allowed
    from core.memory import event_migration, event_log
    rows = []
    seen = set()
    blocked = set()
    scope = MemoryScope.reality_scope(str(uid), char_id)

    def add(text, ts, message_id='', source=''):
        if not isinstance(text, str) or not _FOOD_SIGNAL.search(text) or not role_source_allowed(source):
            return
        if not any(pattern.search(text) for pattern in (_EAT, _NEGATIVE, _POSITIVE, _TEMPORARY)):
            return
        if isinstance(ts, bool) or not isinstance(ts, (int, float)) or not math.isfinite(ts) or ts <= 0:
            return
        fingerprint = hashlib.sha256(json.dumps([int(ts // 60), text.strip()], ensure_ascii=False).encode()).hexdigest()
        if message_id in blocked or message_id in seen or fingerprint in seen:
            return
        key = message_id or 'history_' + hashlib.sha256(json.dumps([ts, text], ensure_ascii=False).encode()).hexdigest()
        seen.update((key, fingerprint))
        rows.append({'message_id': key, 'ts': float(ts), 'text': text})

    # Prefer canonical IDs so existing live extraction jobs remain idempotent.
    path = resolve_path(scope, 'event_store')
    if path.exists():
        with sqlite3.connect(f'{path.resolve().as_uri()}?mode=ro', uri=True) as db:
            db.row_factory = sqlite3.Row
            for deleted in db.execute("SELECT event_id,turn_id FROM events WHERE uid=? AND char_id=? AND redaction_state='tombstoned'", (str(uid), char_id)):
                blocked.update(value for value in (deleted['event_id'], deleted['turn_id']) if value)
            for row in db.execute("SELECT * FROM events WHERE uid=? AND char_id=? AND realm='reality' AND actor='user' AND kind IN ('user_message','legacy_message') AND redaction_state IN ('unredacted','memory_cleaned') ORDER BY occurred_at,event_id", (str(uid), char_id)):
                metadata = json.loads(row['raw_payload_json'] or '{}')
                if isinstance(metadata, dict) and metadata.get('asr_low_confidence'):
                    continue
                add(row['raw_text'] or row['visible_text'], row['occurred_at'], row['turn_id'] or row['event_id'], row['source'])

    for entry in load(uid, char_id=char_id):
        text = entry.get('content', '')
        if entry.get('role') != 'user' or entry.get('asr_low_confidence') or not isinstance(text, str):
            continue
        ts = entry.get('ts', entry.get('timestamp'))
        if entry.get('_long_user_sequence') is not None:
            from core.memory.short_term import _load_long_messages
            text = _load_long_messages(uid, char_id=char_id).get('messages', {}).get(str(entry['_long_user_sequence']), {}).get('content', '')
        turn_id = entry.get('_turn_id')
        add(text, ts, turn_id or '', entry.get('_source', ''))

    directories = [resolve_path(scope, 'event_log')]
    legacy = event_log._legacy_read_dir_if_eligible(str(uid), char_id)
    if legacy is not None:
        directories.append(legacy)
    entries = []
    for directory in directories:
        if not directory.is_dir():
            continue
        for file in sorted(directory.glob('*.md')):
            if not re.fullmatch(r'\d{4}-\d{2}-\d{2}\.md', file.name):
                continue
            parsed, _, _ = event_migration._block_entries(file, file.read_text(encoding='utf-8'))
            entries.extend(parsed)
    unique, _, _, conflicts = event_migration._deduplicate_entries(entries)
    for entry in unique:
        if entry.actor == 'user' and entry.kind == 'legacy_message' and entry.event_id not in conflicts:
            add(entry.text, entry.occurred_at, entry.turn_id or entry.event_id, entry.source)
    return sorted(rows, key=lambda row: (row['ts'], row['message_id']))[-limit:]


def migrate(uid, char_id, *, apply=False, limit=20):
    if not 1 <= limit <= 5000:
        raise ValueError('limit must be 1..5000')
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


async def finish(uid, char_id, report):
    """Explicit CLI processing, with completion evidence rather than enqueue success."""
    if report['mode'] != 'apply':
        raise ValueError('processing requires --apply and its backup')
    from core import food_memory
    identifiers = set(report['message_ids'])
    for _ in range(3 * ((len(identifiers) + 9) // 10) + 1):
        with food_memory.connection() as db:
            jobs = [dict(row) for row in db.execute(
                'SELECT message_id,status,attempts,error FROM jobs WHERE uid=? AND char_id=?',
                (str(uid), char_id)) if row['message_id'] in identifiers]
        if not any(row['status'] == 'pending' and row['attempts'] < 3 for row in jobs):
            break
        await food_memory.process_pending(uid, char_id)
    with food_memory.connection() as db:
        jobs = [dict(row) for row in db.execute(
            'SELECT message_id,status,attempts,error FROM jobs WHERE uid=? AND char_id=?',
            (str(uid), char_id)) if row['message_id'] in identifiers]
        events = [row['kind'] for row in db.execute(
            'SELECT message_id,kind FROM events WHERE uid=? AND char_id=?',
            (str(uid), char_id)) if row['message_id'] in identifiers]
    return {**report, 'completed': sum(row['status'] == 'completed' for row in jobs),
            'pending': sum(row['status'] == 'pending' for row in jobs),
            'not_enqueued': len(identifiers) - len(jobs), 'events': len(events),
            'errors': sorted({row['error'] for row in jobs if row['error']}),
            'note': '完成抽取不代表每条都有可采纳事实；只有原话校验通过的事件入清单。'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--uid', required=True)
    parser.add_argument('--char-id', required=True)
    parser.add_argument('--limit', type=int, default=20)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--process', action='store_true', help='After backup/enqueue, run the configured food_extract route and report completion')
    args = parser.parse_args()
    if args.process and not args.apply:
        parser.error('--process requires --apply')
    report = migrate(args.uid, args.char_id, apply=args.apply, limit=args.limit)
    if args.process:
        report = asyncio.run(finish(args.uid, args.char_id, report))
    print(json.dumps(report, ensure_ascii=False, indent=2))
