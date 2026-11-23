"""Authenticated HTTP ingress for normalized external Wake Bridge stimuli."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from admin.auth import require_scopes

router = APIRouter()




@router.get("/observability/wake-bridge", summary="读取 Wake Bridge 来源 checkpoint")
async def wake_bridge_state(
    uid: str = "",
    char_id: str = "",
    provider: str = "",
    _auth=Depends(require_scopes("state.read")),
):
    from core.wake_bridge import query_state

    entries = query_state(uid=uid, char_id=char_id, provider=provider)
    return {"entries": entries, "count": len(entries)}


@router.post("/integrations/forum/events", summary="接收标准化论坛外部刺激")
async def receive_forum_events(
    body: dict[str, Any],
    _auth=Depends(require_scopes("integration.write")),
):
    """Accept one normalized event or ``{\"events\": [...]}`` small batch.

    The route intentionally delegates all scope/content/dedupe checks to WakeBridge;
    request authentication runs first, so failed auth cannot create runtime state.
    """
    from core.wake_bridge import WakeBridge

    raw_events = body.get("events") if isinstance(body.get("events"), list) else [body]
    # This is an ingress, not a bulk import queue. Bound the work retained in one request.
    raw_events = raw_events[:20]
    bridge = WakeBridge()
    results = []
    for item in raw_events:
        if not isinstance(item, dict):
            results.append({"status": "malformed", "reason": "each event must be an object"})
            continue
        results.append((await bridge.submit_mapping(item)).to_dict())
    return {"results": results, "count": len(results)}


@router.post("/integrations/garden/wake", summary="接收 Galatea Garden level wake hint")
async def receive_garden_wake(
    body: dict[str, Any],
    _auth=Depends(require_scopes("integration.write")),
):
    """Durably receive a Garden state hint; scheduler tick performs any wake later."""
    from core.wake_bridge import WakeBridge
    return (await WakeBridge().submit_garden_mapping(body)).to_dict()
