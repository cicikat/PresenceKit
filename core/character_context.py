"""Read selected character identities for their existing, distinct contracts.

Strict activity admission, authored-card editing, and routing diagnostics retain
their own error/default policies. No runtime state or second authority is stored.
"""
import json
import logging
from pathlib import Path

from core.config_loader import get_config
from core.sandbox import get_paths as _get_paths

logger = logging.getLogger(__name__)


class ActiveCharacterError(ValueError):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def active_character_id() -> str:
    try:
        raw = json.loads(_get_paths().active_prompt_assets().read_text(encoding="utf-8"))
        cid = (raw.get("active_character") or "").strip()
    except Exception:
        raise ActiveCharacterError(status_code=503, detail="active character unavailable")

    if not cid:
        raise ActiveCharacterError(status_code=503, detail="active_character missing")

    from core.asset_registry import get_registry
    try:
        get_registry().resolve(cid, "character")
    except ValueError:
        raise ActiveCharacterError(status_code=422, detail=f"unknown character id: {cid!r}")

    return cid


def configured_character_id() -> str:
    """Return the current active character id.

    Priority: active_prompt_assets.json > config.yaml character.default.
    """
    import json as _json
    from core.sandbox import get_paths as _get_paths
    try:
        active_id = _json.loads(
            _get_paths().active_prompt_assets().read_text(encoding="utf-8")
        ).get("active_character", "")
        if active_id:
            return active_id
    except Exception:
        pass
    raw = get_config().get("character", {}).get("default", "")
    if not raw:
        return ""
    return Path(raw).stem if "." in raw else raw


def routing_character_id() -> str | None:
    """Read the selected character without making a failed admin read fatal."""
    try:
        from core.sandbox import get_paths

        active_id = json.loads(
            get_paths().active_prompt_assets().read_text(encoding="utf-8")
        ).get("active_character")
        if isinstance(active_id, str) and active_id:
            return active_id
    except Exception as exc:
        logger.debug("[model-routing] unable to read active prompt assets: %s", exc)

    raw = get_config().get("character", {}).get("default")
    if not isinstance(raw, str) or not raw:
        return None
    return Path(raw).stem
