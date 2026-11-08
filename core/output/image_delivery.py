"""QQ delivery for generated images: send each turn's image artifacts once."""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def send_turn_images(
    artifacts: list[dict],
    target_id: str | None,
    is_group: bool = False,
) -> None:
    images = [a for a in (artifacts or []) if isinstance(a, dict) and a.get("kind") == "image"]
    if not images or not target_id:
        return
    from core.qq_adapter import send_image
    from core.tools.chat_artifacts import artifact_file_path, get_artifact_record

    for item in images:
        record = None
        try:
            # The record keeps the filesystem path out of the public payload.
            from core.tools.chat_artifacts import resolve_artifact_scope

            scope = resolve_artifact_scope(str(item.get("id") or ""))
            if scope:
                record = get_artifact_record(scope["id"], uid=scope["uid"], char_id=scope["char_id"])
        except Exception:
            record = None
        if record is None:
            record = item
        try:
            path = str(artifact_file_path(record))
        except Exception:
            logger.warning("[image_delivery] no file for artifact %s", item.get("id"))
            continue
        await send_image(target_id, path, is_group)
