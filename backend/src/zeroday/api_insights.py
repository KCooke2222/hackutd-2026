"""Cash-flow insight routes (read-only)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from . import insights, pnc_data

router = APIRouter()


def _need(cid: str) -> None:
    if not pnc_data.profile(cid):
        raise HTTPException(404, f"unknown customer {cid}")


@router.get("/api/customers/{cid}/cashflow")
def cashflow(cid: str, months: int = Query(12, ge=1, le=24)) -> dict:
    _need(cid)
    return insights.cash_flow(cid, months)


@router.get("/api/customers/{cid}/spending/{category}")
def spending(cid: str, category: str, months: int = Query(12, ge=1, le=24)) -> dict:
    _need(cid)
    return insights.spending_detail(cid, category, months)


@router.get("/api/customers/{cid}/recurring")
def recurring(cid: str) -> list[dict]:
    _need(cid)
    return insights.find_recurring(cid)
