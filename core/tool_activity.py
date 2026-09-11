"""Owner-only tool display events; arguments and results never leave this boundary."""
from contextvars import ContextVar
from functools import wraps
import time
from uuid import uuid4

_chain = ContextVar('tool_display_chain', default=None)
_call = ContextVar('tool_display_call', default=None)


def display_chain(function):
    @wraps(function)
    async def wrapped(*args, **kwargs):
        token = _chain.set(uuid4().hex)
        try:
            return await function(*args, **kwargs)
        finally:
            _chain.reset(token)
    return wrapped


def current_call():
    return _call.get()


async def execute_visible(function, tool_name, tool_args, user_id, target_id,
                          is_group, session_state, **kwargs):
    # Keep group, admin and Dream execution out of the single-owner transcript.
    from core.config_loader import get_config
    owner = str(get_config().get('scheduler', {}).get('owner_id') or '')
    origin = kwargs['origin']
    if is_group or str(user_id) != owner or origin not in {
        'user_live', 'assistant_loop', 'assistant_loop_relay', 'autonomy_loop',
        'autonomy_self_management', 'assistant_self_management',
    }:
        return await function(tool_name, tool_args, user_id, target_id, is_group, session_state, **kwargs)
    event = {
        'type': 'tool_activity', 'event_id': uuid4().hex,
        'chain_id': _chain.get() or uuid4().hex,
        'char_id': kwargs['char_id'], 'source': 'reality',
        'tool_name': tool_name[:128], 'status': 'running', 'ts': time.time(),
        'origin': 'autonomy' if origin.startswith('autonomy') else 'chat',
    }
    token = _call.set(event)
    async def publish():
        try:
            from channels.desktop_ws import _send_json
            await _send_json(dict(event))
        except Exception:
            pass  # Display cannot change tool execution.
    await publish()
    try:
        outcome = await function(tool_name, tool_args, user_id, target_id, is_group, session_state, **kwargs)
        event['status'] = ('pending_confirmation' if outcome.confirmation_request else
                           'success' if outcome.status == 'tool_executed' else
                           'unknown' if outcome.status == 'outcome_unknown' else 'error')
        return outcome
    except BaseException:
        event['status'] = 'unknown'
        raise
    finally:
        try:
            from core.memory.action_trace import finalize_display
            finalize_display(str(user_id), kwargs['char_id'], event)
        except Exception:
            pass
        await publish()
        _call.reset(token)
