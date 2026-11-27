"""Session-scoped conversation control; no caller supplied owner/character."""
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response

from admin.auth import require_scopes
from admin.session_requests import session_grant
from core import conversation_control as control

router = APIRouter(prefix='/conversation', dependencies=[Depends(require_scopes('chat'))])


def grant(x_presence_session=Header(None, alias='X-Presence-Session'), auth=Depends(require_scopes('chat'))):
    return session_grant(x_presence_session, auth.label)


def _call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except control.ControlError as exc:
        raise HTTPException(exc.status, exc.code) from None


@router.get('/executions')
def executions(response: Response, session=Depends(grant)):
    response.headers['Cache-Control'] = 'no-store'
    return {'executions': _call(control.active, session.memory_scope)}


@router.get('/requests/{request_id}')
def request_status(request_id: str, response: Response, session=Depends(grant)):
    response.headers['Cache-Control'] = 'no-store'
    return _call(control.lookup, session.memory_scope, label=session.token_label, request_id=request_id)


@router.get('/mutations')
def changes(response: Response, after: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=200), session=Depends(grant)):
    response.headers['Cache-Control'] = 'no-store'
    return _call(control.mutations, session.memory_scope, after=after, limit=limit)

@router.post('/requests/{request_id}/stop')
async def stop_request(request_id: str, session=Depends(grant)):
    from core.conversation_execution import stop_request as stop
    from core.session_scope import SessionScopeError
    try:
        return _call(stop, session.memory_scope, label=session.token_label, request_id=request_id)
    except SessionScopeError as exc:
        raise HTTPException(exc.status_code, exc.code) from None


@router.post('/executions/{execution_id}/stop')
async def stop_execution(execution_id: str, session=Depends(grant)):
    from core.conversation_execution import stop_execution as stop
    return _call(stop, session.memory_scope, execution_id)
