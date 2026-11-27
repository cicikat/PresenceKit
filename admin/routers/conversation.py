"""Session-scoped conversation control; no caller supplied owner/character."""
import sqlite3

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

from pydantic import BaseModel, ConfigDict, Field


class DeleteTurn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    operation_id: str = Field(min_length=1,max_length=128)
    expected_revision: int | None = Field(default=None,ge=0)


class RestoreTurn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    operation_id: str = Field(min_length=1,max_length=128)
    expected_revision: int = Field(ge=0)


async def _async_call(fn, *args, **kwargs):
    try:
        return await fn(*args, **kwargs)
    except control.ControlError as exc:
        raise HTTPException(exc.status,exc.code) from None
    except (OSError, sqlite3.Error):
        raise HTTPException(503,'conversation_projection_unavailable') from None


@router.post('/turns/{turn_id}/delete')
async def delete_turn(turn_id: str, body: DeleteTurn, session=Depends(grant)):
    from core.conversation_recovery import delete
    return await _async_call(delete,session.memory_scope,turn_id=turn_id,label=session.token_label,
                             operation_id=body.operation_id,expected_revision=body.expected_revision)


@router.get('/deletions',dependencies=[Depends(require_scopes('chat.recovery'))])
def deletion_list(response: Response, before: float | None=None, limit: int=Query(30,ge=1,le=100),session=Depends(grant)):
    from core.conversation_recovery import list_deleted
    response.headers['Cache-Control']='no-store'
    return _call(list_deleted,session.memory_scope,before=before,limit=limit)


@router.get('/deletions/{deletion_id}',dependencies=[Depends(require_scopes('chat.recovery'))])
def deletion_detail(deletion_id: str,response: Response,session=Depends(grant)):
    from core.conversation_recovery import detail
    response.headers['Cache-Control']='no-store'
    return _call(detail,session.memory_scope,deletion_id)


@router.post('/deletions/{deletion_id}/restore',dependencies=[Depends(require_scopes('chat.recovery'))])
async def restore_turn(deletion_id: str,body: RestoreTurn,session=Depends(grant)):
    from core.conversation_recovery import restore
    return await _async_call(restore,session.memory_scope,deletion_id=deletion_id,
                             operation_id=body.operation_id,expected_revision=body.expected_revision)

@router.post('/deletions/{deletion_id}/repair',dependencies=[Depends(require_scopes('chat.recovery'))])
async def repair_deletion(deletion_id: str,session=Depends(grant)):
    from core.conversation_recovery import repair
    return await _async_call(repair,session.memory_scope,deletion_id)
