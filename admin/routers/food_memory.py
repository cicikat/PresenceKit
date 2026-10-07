"""Food extraction controls and owner-only evidence inspection."""
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from admin.auth import require_scopes
from admin.config_control import read_config_file, write_config_file
from core.config_loader import get_config
from core import food_memory as store
from pathlib import Path

router = APIRouter()
CONFIG_FILE = Path('config.yaml')


class Settings(BaseModel):
    enabled: bool


@router.get('/settings/food-memory')
async def get_settings(_auth=Depends(require_scopes('admin'))):
    return store.settings()


@router.put('/settings/food-memory')
async def put_settings(body: Settings, _auth=Depends(require_scopes('admin'))):
    cfg = read_config_file(CONFIG_FILE)
    cfg.setdefault('food_memory', {})['enabled'] = body.enabled
    write_config_file(CONFIG_FILE, cfg)
    from core.config_loader import reload_config
    reload_config()
    return store.settings()


def scope(char_id):
    uid = str(get_config().get('scheduler', {}).get('owner_id', ''))
    if not uid:
        raise HTTPException(503, 'owner_not_configured')
    from core.character_loader import load
    try:
        load(char_id)
    except Exception:
        raise HTTPException(404, 'character_not_found')
    return uid, char_id


@router.get('/observability/food-memory')
async def observe(char_id: str = Query(min_length=1, max_length=100), query: str = Query('', max_length=100),
                  limit: int = Query(20, ge=1, le=50), _auth=Depends(require_scopes('state.read'))):
    uid, char_id = scope(char_id)
    return store.snapshot(uid, char_id, query, limit)


class Correction(BaseModel):
    name: str
    quote: str
    evaluation: str


@router.post('/settings/food-memory/correct')
async def correct(body: Correction, char_id: str = Query(min_length=1, max_length=100),
                  _auth=Depends(require_scopes('admin'))):
    uid, char_id = scope(char_id)
    candidate = {'name': body.name, 'quote': body.quote, 'kind': 'rating', 'value': body.evaluation}
    if store.validate(candidate, body.quote) is None:
        raise HTTPException(422, '需要包含食品名称和明确喜恶的本人评价原话')
    import uuid, time
    job = {'message_id': 'admin_' + uuid.uuid4().hex, 'ts': time.time(), 'text': body.quote}
    store.apply(uid, char_id, job, [candidate])
    return store.snapshot(uid, char_id, body.name)


@router.post('/settings/food-memory/retry')
async def retry(char_id: str = Query(min_length=1, max_length=100), _auth=Depends(require_scopes('admin'))):
    uid, char_id = scope(char_id)
    with store.connection() as db:
        db.execute("UPDATE jobs SET attempts=0 WHERE uid=? AND char_id=? AND status='pending'", (uid, char_id))
    from core.post_process.slow_queue import enqueue
    from core.memory.scope import MemoryScope
    # Explicit retry is still scoped; queue processor takes no model-supplied identity.
    enqueue('food_memory_update', {'uid': uid, 'char_id': char_id, 'scope': MemoryScope.reality_scope(uid, char_id).to_payload()})
    return {'queued': True}
