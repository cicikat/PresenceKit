"""HTTP owner and character admission for food/quote evidence reads."""
from fastapi import HTTPException
from core.config_loader import get_config


def food_owner_scope(char_id):
    uid = str(get_config().get('scheduler', {}).get('owner_id', ''))
    if not uid:
        raise HTTPException(503, 'owner_not_configured')
    from core.character_loader import load
    try:
        load(char_id)
    except Exception:
        raise HTTPException(404, 'character_not_found')
    return uid, char_id
