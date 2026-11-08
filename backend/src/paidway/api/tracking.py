"""Goal tracking + demo fast-forward routes. Demo months live in our own DB; choose() only saves a PENDING
proposal (no approve, no execute). The one Nessie write here is bill upkeep: when a month passes (step,
check-in), the bill schedule the customer approved runs for that month (execute.apply_due_bills: a paid-off
debt's bill ends or moves to the next debt, a paused contribution restarts). Nothing new is decided here."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..agent import execute
from ..data import pnc_data
from ..planning import tracking

router = APIRouter()


class StepIn(BaseModel):
    scenario: str = "on_plan"


class ChooseIn(BaseModel):
    option: str


def _prop(cid: str) -> dict | None:
    if not pnc_data.profile(cid):
        raise HTTPException(404, f"unknown customer {cid}")
    return tracking.approved_proposal(cid)


def _need(cid: str) -> dict:
    p = _prop(cid)
    if not p:
        raise HTTPException(409, "no approved plan to track")
    return p


@router.get("/api/customers/{cid}/tracking")
def get_tracking(cid: str) -> dict:
    p = _prop(cid)
    if not p:
        return {"active": False}
    return {"active": True, **tracking.dashboard(cid, p)}


@router.post("/api/customers/{cid}/tracking/step")
def step(cid: str, body: StepIn) -> dict:
    p = _need(cid)
    try:
        t = tracking.step(cid, p, body.scenario)
    except ValueError as e:
        raise HTTPException(422, str(e))
    return {"active": True, **t, "bills_applied": execute.apply_due_bills(cid, p["id"], t["now"])}


@router.post("/api/customers/{cid}/tracking/reset")
def reset(cid: str) -> dict:
    p = _need(cid)
    tracking.reset(cid, p["id"])
    return {"active": True, **tracking.dashboard(cid, p)}


@router.post("/api/customers/{cid}/tracking/choose")
def choose(cid: str, body: ChooseIn) -> dict:
    from ..agent.validator import PlanRejected

    p = _need(cid)
    try:
        new = tracking.choose(cid, p, body.option)
    except PlanRejected as e:
        raise HTTPException(422, "; ".join(e.reasons))
    except ValueError as e:
        raise HTTPException(422, str(e))
    return {"proposal_id": new["id"], "status": new["status"], "headline": new["simulation"]["explain"]["headline"]}


class CheckinIn(BaseModel):
    scenario: str | None = None


@router.post("/api/customers/{cid}/tracking/checkin")
def checkin(cid: str, body: CheckinIn) -> dict:
    """Advance a demo month (optional) and let the agent name the drift and propose a fix (PENDING only)."""
    from ..agent.checkin import checkin as run_checkin
    from ..agent.graph import AgentUnavailable

    p = _need(cid)
    try:
        r = run_checkin(cid, body.scenario, p)
    except (ValueError, AgentUnavailable) as e:
        raise HTTPException(422, str(e))
    t = r.pop("tracking")
    ran = execute.apply_due_bills(cid, p["id"], t["now"]) if body.scenario else []
    return {"active": True, **t, "checkin": r, "bills_applied": ran}
