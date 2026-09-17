"""Read-only spend audit endpoints. Configuration remains deliberately file-managed."""
import json

from fastapi import APIRouter, Depends, HTTPException, Query
from admin.auth import require_scopes
from core.actions.spend_ledger import budget_usage, read_ledger

router = APIRouter()

@router.get("/spend/ledger")
async def get_spend_ledger(limit: int = Query(100, ge=1, le=500), _auth=Depends(require_scopes("admin"))):
    return {"entries": read_ledger(limit)}

@router.get("/spend/budget")
async def get_spend_budget(_auth=Depends(require_scopes("admin"))):
    return budget_usage()


@router.post("/spend/check")
async def run_spend_check(_auth=Depends(require_scopes("admin"))):
    """Authenticated manual trigger for one read-only balance observation."""
    from core.scheduler.triggers.spend_monitor import check_spend_monitor
    return {"outcomes": await check_spend_monitor(force=True)}


@router.get("/spend/mandates")
async def get_spend_mandates(
    status: str = "",
    limit: int = Query(100, ge=1, le=500),
    before: float | None = None,
    _auth=Depends(require_scopes("admin")),
):
    """Reserved read-only view for Brief 63 intent records.

    There is still no production writer, confirm/reject path, purchase executor,
    or merchant adapter. Missing files return empty; historical jsonl rows are
    never deleted by this endpoint.
    """
    if status and status not in {"draft", "confirmed", "rejected", "expired", "failed"}:
        raise HTTPException(status_code=422, detail="未知 mandate status")
    from core.sandbox import get_paths
    path = get_paths().spend_mandates()
    if not path.exists():
        return {"entries": [], "count": 0}
    entries: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict) or (status and row.get("status") != status):
            continue
        ts = row.get("ts", row.get("created_at", 0))
        if before is not None and isinstance(ts, (int, float)) and ts >= before:
            continue
        entries.append(row)
    entries = entries[-limit:]
    return {"entries": entries, "count": len(entries)}
