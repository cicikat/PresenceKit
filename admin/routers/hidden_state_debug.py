"""
GET /debug/user-hidden-state — 隐性状态只读观测。

Security contract:
  - No write paths exposed.
  - DREAM_DIRECT_WRITABLE = frozenset() — no field mutated here.
  - Fail-closed: any error returns default values, never raises 500.
"""
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends

from admin.auth import require_scopes

logger = logging.getLogger(__name__)

router = APIRouter()

_DEFAULT_RESPONSE = {
    "schema_version": 2,
    "last_decay_tick": None,
    "sensitivity": {"baseline": 50.0, "current": 50.0, "last_update_source": "init"},
    "touch_need": {"baseline": 50.0, "deficit": 0.0, "last_update_source": "init"},
    "embodied_ease": {"value": 50.0, "last_update_source": "init"},
    "body_memory": [],
    "dream_snapshot": {
        "sensitivity": "mid",
        "touch_appetite": "mid",
        "embodied_ease": "neutral",
        "memory_cues": [],
    },
    "trigger_counts": {},
    "confidence_gating": {"enabled": False, "min_confidence": 0.3},
    "scalar_views": {},
    "evidence": {},
}


def _owner_uid() -> str:
    from core.config_loader import get_config
    return str(get_config().get("scheduler", {}).get("owner_id", "owner"))


def _active_char_id() -> str:
    """Read active_character from active_prompt_assets.json.

    Raises ValueError if the field is missing or empty (fail-loud, not silent fallback).
    """
    import json as _json
    from core.sandbox import get_paths
    p = get_paths().active_prompt_assets()
    data = _json.loads(p.read_text(encoding="utf-8"))
    char_id = (data.get("active_character") or "").strip()
    if not char_id:
        raise ValueError(
            "[hidden_state_debug] active_prompt_assets.json has empty active_character"
        )
    return char_id


def _field_scalar(raw: dict, field_name: str) -> dict:
    cur = raw
    for part in field_name.split("."):
        cur = cur[part]
    return cur


@router.get(
    "/debug/user-hidden-state",
    summary="读取 UserHiddenState + Dream Snapshot（只读）",
    description=(
        "返回当前用户的 UserHiddenState 各字段 + Dream Snapshot 投影。\n\n"
        "**只读。无任何写路径。**\n\n"
        "- `sensitivity`: 当前 / 基线敏感度\n"
        "- `touch_need`: 接触需求基线 + 当前亏欠量\n"
        "- `embodied_ease`: 体感放松度\n"
        "- `body_memory`: 身体记忆条目（按 weight 降序）\n"
        "- `dream_snapshot`: Dream 实际注入的 bucket 投影\n"
        "- `trigger_counts`: integrator 各 event_type / source 累计接受次数"
        "（进程内计数，重启清零，仅用于验收接线是否在动）\n\n"
        "异常时 fail-closed，返回默认值，不抛 500。"
    ),
    tags=["Debug"],
)
async def get_user_hidden_state_debug(auth=Depends(require_scopes("memory.read"))):
    try:
        from core.memory.user_hidden_state import FIELD_NAMES, confidence_gating_settings, scalar_view, to_dict, to_dream_snapshot
        from core.memory.user_hidden_state_store import load_hidden_state
        from core.memory.user_hidden_state_integrator import get_trigger_counts

        uid = _owner_uid()
        char_id = _active_char_id()
        now = datetime.now(timezone.utc).isoformat()

        state = load_hidden_state(uid, char_id=char_id)
        raw = to_dict(state)
        snapshot = to_dream_snapshot(state, now)

        sens = raw["sensitivity"]
        touch = raw["touch_need"]
        ee = raw["embodied_ease"]

        body_memory = sorted(
            raw["body_memory"]["entries"],
            key=lambda e: e["weight"],
            reverse=True,
        )

        return {
            "schema_version": raw["schema_version"],
            "last_decay_tick": raw.get("last_decay_tick"),
            "sensitivity": {
                "baseline": sens["baseline"]["value"],
                "current": sens["current"]["value"],
                "last_update_source": sens["current"]["last_update_source"],
            },
            "touch_need": {
                "baseline": touch["baseline"]["value"],
                "deficit": touch["deficit"]["value"],
                "last_update_source": touch["deficit"]["last_update_source"],
            },
            "embodied_ease": {
                "value": ee["value"],
                "last_update_source": ee["last_update_source"],
            },
            "body_memory": [
                {
                    "cue": e["cue"],
                    "response_tag": e["response_tag"],
                    "weight": round(e["weight"], 4),
                }
                for e in body_memory
            ],
            "dream_snapshot": snapshot,
            "trigger_counts": get_trigger_counts(),
            "confidence_gating": {
                "enabled": confidence_gating_settings()[0],
                "min_confidence": confidence_gating_settings()[1],
            },
            "scalar_views": {f: scalar_view(state, f, now) for f in FIELD_NAMES},
            "evidence": {
                f: list(_field_scalar(raw, f).get("evidence", [])) for f in FIELD_NAMES
            },
        }

    except Exception as exc:
        logger.warning(
            "[hidden_state_debug] error building response: %s — returning defaults", exc
        )
        return dict(_DEFAULT_RESPONSE)
