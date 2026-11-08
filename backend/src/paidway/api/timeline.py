"""Plan-over-time route (read-only): per-account balances and payments, before vs after a plan."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..data import pnc_data
from ..planning import timeline

router = APIRouter()


class PlanIn(BaseModel):
    plan: dict


@router.post("/api/customers/{cid}/plan/timeline")
def plan_timeline(cid: str, body: PlanIn) -> dict:
    if not pnc_data.profile(cid):
        raise HTTPException(404, f"unknown customer {cid}")
    try:
        return timeline.timeline(cid, {**body.plan, "version": 2})
    except (ValueError, KeyError, TypeError) as e:
        raise HTTPException(422, str(e))
