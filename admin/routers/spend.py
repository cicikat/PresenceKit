"""Read-only spend audit endpoints. Configuration remains deliberately file-managed."""
from fastapi import APIRouter, Depends, Query
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
