"""HTTP error mapping for required session grants and request receipts."""
from fastapi import HTTPException
from core import session_scope


def session_grant(session_id: str | None, token_label: str):
    if not isinstance(session_id, str) or not session_id:
        raise HTTPException(status_code=422, detail="session_scope_required")
    from core.session_scope import SessionScopeError, resolve_session

    try:
        return resolve_session(session_id, token_label=token_label)
    except SessionScopeError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.code) from None


async def run_session_request(*, grant, request_id, payload, executor):
    from core.session_scope import SessionScopeError, execute_request

    from core import conversation_control, conversation_execution
    from core.session_scope import validate_request_id
    async def controlled():
        return await conversation_execution.execute(grant=grant, request_id=rid, payload=payload, executor=executor, allow_retry=True)
    try:
        rid = validate_request_id(request_id)
        _rid, result = await execute_request(
            grant=grant, request_id=rid, payload=payload, executor=controlled,
        )
        return result
    except SessionScopeError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.code) from None
    except conversation_control.ControlError as exc:
        raise HTTPException(exc.status, exc.code) from None


def owner_media_scope() -> tuple[str, str]:
    """Translate the existing unscoped HTTP media admission failure."""
    try:
        return session_scope.owner_media_scope()
    except session_scope.SessionScopeError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.code) from None


def voice_scope(arguments):
    auth = arguments.get("_auth")
    return session_grant(arguments.get("x_presence_session"), getattr(auth, "label", "legacy-admin")).memory_scope
