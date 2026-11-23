"""Validation and redacted setup status for configured owner identity."""
import re
from datetime import date
from core.config_loader import get_config

OWNER_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_OWNER_BIRTHDAY_RE = re.compile(r"^(\d{2})-(\d{2})$")


def valid_owner_birthday(raw: str) -> bool:
    m = _OWNER_BIRTHDAY_RE.match(raw)
    if not m:
        return False
    try:
        date(2000, int(m.group(1)), int(m.group(2)))  # 2000 闰年，允许 02-29
        return True
    except ValueError:
        return False


def owner_status(cfg: dict | None = None) -> dict:
    """owner_id / owner_birthday 的配置状态视图，供 /settings/setup-status 复用（Brief 95 §1）。"""
    sched = (cfg if cfg is not None else get_config()).get("scheduler", {})
    owner_id = str(sched.get("owner_id") or "").strip()
    birthday_raw = str(sched.get("owner_birthday") or "").strip()
    birthday_ok = valid_owner_birthday(birthday_raw)
    return {
        "owner_id": owner_id,
        "configured": bool(owner_id) and bool(OWNER_ID_RE.match(owner_id)),
        "owner_birthday": birthday_raw if birthday_ok else "",
        "owner_birthday_set": birthday_ok,
    }
