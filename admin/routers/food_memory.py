"""Food extraction controls and owner-only evidence inspection."""
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from admin.auth import require_scopes
from admin.config_control import read_config_file, write_config_file
from core import food_memory as store
from pathlib import Path
from admin import food_requests

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




@router.get('/observability/food-memory')
async def observe(char_id: str = Query(min_length=1, max_length=100), query: str = Query('', max_length=100),
                  limit: int = Query(20, ge=1, le=50), _auth=Depends(require_scopes('state.read'))):
    uid, char_id = food_requests.food_owner_scope(char_id)
    return store.snapshot(uid, char_id, query, limit)
