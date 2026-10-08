"""Safe view of the existing shared routing profile, never a second preset store."""
from core.config_loader import get_config
from core.model_registry import resolve_category_info


def route_view() -> dict:
    try:
        from admin.routers._common import active_char_id
        char_id = active_char_id()
    except Exception:
        char_id = None
    try:
        info = resolve_category_info("minecraft_reaction", char_id=char_id)
        mp = get_config().get("model_presets", {})
        profile = info["effective_profile"]
        configured = mp.get("routing_profiles", {}).get(profile, {}).get("minecraft_reaction", "")
        return {"profile": profile, "configured_preset": configured, "effective": info,
                "presets": sorted(mp.get("presets", {})), "global_profile": mp.get("active_routing", "default"),
                "character_override": profile != mp.get("active_routing", "default")}
    except ValueError:
        return {"profile": "", "configured_preset": "", "presets": [], "effective": {},
                "error": "model_routes_unavailable"}
