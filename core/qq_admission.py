"""QQ reply admission, before media processing or conversation side effects."""
from core.config_loader import get_config


def allowed(user_id: str, group_id=None) -> bool:
    cfg = get_config()
    qq = cfg.get("qq") or {}
    if group_id:
        return qq.get("group_enabled", False) is True
    owner = str((cfg.get("scheduler") or {}).get("owner_id") or "").strip()
    return bool(owner and str(user_id) == owner) or qq.get("allow_other_users", False) is True
