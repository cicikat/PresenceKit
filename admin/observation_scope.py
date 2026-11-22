"""HTTP character selection for resolve_observation_character_id."""
from fastapi import HTTPException


def resolve_observation_character_id(requested: str) -> str:
    if requested:
        return requested
    try:
        import json
        from core.sandbox import get_paths
        p = get_paths().active_prompt_assets()
        data = json.loads(p.read_text(encoding="utf-8"))
        char_id = (data.get("active_character") or "").strip()
        if not char_id:
            raise HTTPException(status_code=503, detail="active_character 未配置")
        return char_id
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"读取 active_character 失败: {exc}") from exc
