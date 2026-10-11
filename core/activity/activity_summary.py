"""Revision-bound extractive projections; no emotional memory duplicate."""
import logging
from core.activity import records
from core.sandbox import get_paths
from core.safe_write import safe_write_json

logger = logging.getLogger(__name__)


async def generate_and_reflow(uid, char_id, activity_type, session_id):
    ref = f"activity:{activity_type}:{session_id}:dialogue"
    path = get_paths().activity_session_dir(char_id=char_id, uid=uid,
        activity_type=activity_type, session_id=session_id) / "continuity_projection.json"
    try:
        first = records.read(uid, char_id, ref, limit=20)
        version = first["revision"]
        segments = []
        page = first
        while True:
            segments.append({"source_ref": ref, "offset": len(segments) * 20,
                             "count": len(page["items"]), "revision": version,
                             "method": "extractive", "excerpt": " | ".join(
                                 f"{entry.get('type', '')}: {entry.get('text', '')[:200]}"
                                 for entry in page["items"])[:4000]})
            if page["next_offset"] is None:
                break
            page = records.read(uid, char_id, ref, offset=page["next_offset"], limit=20,
                                expected_revision=version)
        if records.read(uid, char_id, ref, limit=1)["revision"] != version:
            return None
        if not safe_write_json(path, {"status": "complete", "revision": version,
                                      "covered_entries": first["total"], "segments": segments}):
            raise OSError("projection persistence failed")
        return ref
    except Exception:
        logger.warning("[activity_projection] pending source=%s", ref, exc_info=True)
        safe_write_json(path, {"status": "pending", "source_ref": ref, "reason": "projection_failed"})
        return None
