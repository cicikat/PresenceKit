"""Exact owner-message anchors backed by the existing reality event ledger."""
from __future__ import annotations

from datetime import datetime
from core.memory import event_query
from core.memory.scope import MemoryScope
from core.tools.event_tools import _success, _unknown, _query_error


def get_message_event(scope,message_id):
    from core.memory import source_policy
    event=event_query.get_event(scope,message_id,include_isolated=True)
    if event and event['source'] not in source_policy.ISOLATED_SOURCES-{'tool_pin'} and event['kind'] in {'user_message','assistant_message','trigger_assistant'}:
        return event
    return None


def message_item(event):
    from core.memory.short_term import _sanitize_assistant_message
    text = str(event.get('visible_text') or event.get('memory_text') or '')
    if event['actor'] == 'assistant':
        text = _sanitize_assistant_message(text)
    ts = event['occurred_at']
    return {'message_id': event['event_id'], 'turn_id': event['turn_id'],
            'author': event['actor'], 'speaker_id': 'owner' if event['actor'] == 'user' else event['char_id'],
            'timestamp': ts, 'datetime': datetime.fromtimestamp(ts).astimezone().isoformat(timespec='seconds'),
            'text': text[:1200], 'truncated': len(text) > 1200 or bool(event.get('truncated_fields'))}


async def read_message_context(user_id, message_id, direction='before', count=5, *, char_id):
    if not isinstance(message_id, str) or not 1 <= len(message_id) <= 256:
        return _unknown('invalid_message_id')
    if direction not in {'before', 'after'} or isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 10:
        return _unknown('invalid_direction_or_count')
    try:
        result = event_query.window(MemoryScope.reality_scope(str(user_id), char_id), message_id,
            before=count if direction == 'before' else 0, after=count if direction == 'after' else 0,
            messages_only=True)
    except event_query.EventQueryError as exc:
        return _query_error(exc, event_id=message_id)
    if result is None:
        return _unknown('message_unavailable', event_id=message_id,
                        detail='消息未入账、已遗忘或不属于当前现实对话；不按同文猜测。')
    return _success({'status': 'ok', 'anchor': message_item(result['event']),
        'direction': direction, 'messages': [message_item(e) for e in result[direction]],
        'coverage': 'retained_reality_ledger', 'truncation_reason': result['truncation_reason']})
