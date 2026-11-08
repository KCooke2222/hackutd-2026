"""Approve preview (read-only, no Nessie call) and the demo reset."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

router = APIRouter()


@router.get("/api/proposals/{pid}/preview")
def preview_proposal(pid: str) -> dict:
    """Exactly which Nessie calls approving would make, plus the recorded and "you do it" steps. Sends nothing."""
    from ..agent.execute import ApproveError, preview

    try:
        return preview(pid)
    except ApproveError as e:
        raise HTTPException(e.status, e.detail)


@router.post("/api/demo/{cid}/reset")
def demo_reset(cid: str) -> dict:
    """Undo an approve for the demo: recreate the customer's Nessie accounts from the seed, restore linked balances."""
    from ..data import pnc_data, seed
    from ..planning import balance_sheet

    if not pnc_data.customer_accounts(cid):
        raise HTTPException(404, "no such customer")
    try:
        return seed.reset_customer(cid)
    finally:
        balance_sheet.clear_cache()
