"""
garden_water trigger — 每 30 分钟 roll 一次自动浇水。
"""

import logging

from core.scheduler.loop import _is_ready, _mark
from core.garden import manager as garden_manager

logger = logging.getLogger(__name__)


async def _check_garden_water() -> None:
    if not _is_ready("garden_water"):
        return
    _mark("garden_water")
    try:
        garden_manager.auto_water_tick()
    except Exception:
        logger.exception("[garden] auto_water_tick failed")
