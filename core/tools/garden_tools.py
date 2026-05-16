"""garden_tools — 被动浇水工具。用户催角色浇花时由探针/dispatcher 调用。"""

import logging

from core.config_loader import _char_name
from core.garden import manager as garden_manager
from core.garden.constants import FLOWERS

logger = logging.getLogger(__name__)

_FLOWER_NAMES = {f["id"]: f["name"] for f in FLOWERS}

_STAGE_CN = {
    "seed":    "种子",
    "sprout":  "嫩芽",
    "budding": "花苞",
    "bloom":   "盛开",
}


async def water_garden() -> str:
    """
    用户催角色去浇花时调用。角色会根据当前心情选择花园里对应那一株来浇。
    无参数，无冷却。返回一句给 LLM 的状态描述，角色基于这句话自然回复。
    """
    char = _char_name()
    result = garden_manager.force_water()

    if not result.get("ok"):
        reason = result.get("reason", "unknown")
        logger.info("[garden tool] force_water failed: %s", result)
        if reason == "already_bloomed":
            return f"{char}走到花园，发现今天对应心情的那株花已经开了，暂时没什么需要浇的。"
        if reason == "no_slot_for_mood":
            mood = result.get("mood", "?")
            return f"{char}站在花园边愣了一下——此刻的心情（{mood}）好像找不到对应的那株花。"
        return f"{char}想去浇水，但花园那边出了点说不清的状况，没浇成。"

    flower_cn = _FLOWER_NAMES.get(result["flower_id"], result["flower_id"])
    stage_cn = _STAGE_CN.get(result["stage"], result["stage"])
    bloomed = result.get("bloomed", False)

    if bloomed:
        return f"{char}按当前的心情，去花园给{flower_cn}浇了水——它刚才开了。{char}看了一会儿，然后回来。"
    return f"{char}按当前的心情，去花园给{flower_cn}浇了一次水。它现在是{stage_cn}。"
