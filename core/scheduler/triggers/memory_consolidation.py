"""Silent dossier consolidation maintenance trigger (Brief 258 D)."""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def _check_memory_consolidation() -> None:
    from core.memory.consolidation_worker import tick
    from core.scheduler.loop import _is_ready, _mark

    if not _is_ready("memory_consolidation"):
        return
    _mark("memory_consolidation")
    result = await tick()
    status = str(result.get("status") or "unknown")
    if status not in {"disabled", "outside_window", "no_scope", "no_work"}:
        logger.info("[memory_consolidation] scheduler tick status=%s", status)
