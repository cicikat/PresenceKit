"""HTTP error mapping for existing optional session grants and request receipts."""
from fastapi import HTTPException
from core import session_scope


def session_grant(session_id: str | None, token_label: str):
    # Direct unit calls see FastAPI's Header descriptor instead of an injected
    # value; only an actual non-empty string is a session handle.
    if not isinstance(session_id, str) or not session_id:
        return None
    from core.session_scope import SessionScopeError, resolve_session

    try:
        return resolve_session(session_id, token_label=token_label)
    except SessionScopeError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.code) from None


async def run_session_request(*, grant, request_id, payload, executor):
    from core.session_scope import SessionScopeError, execute_request

    try:
        _rid, result = await execute_request(
            grant=grant, request_id=request_id, payload=payload, executor=executor,
        )
        return result
    except SessionScopeError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.code) from None



def owner_media_scope() -> tuple[str, str]:
    """Translate the existing unscoped HTTP media admission failure."""
    try:
        return session_scope.owner_media_scope()
    except session_scope.SessionScopeError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.code) from None
