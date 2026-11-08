"""Goal tracking + demo fast-forward routes. Demo months live in our own DB; choose() only saves a PENDING
proposal (no approve, no execute, no Nessie writes)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

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
        return {"active": True, **tracking.step(cid, p, body.scenario)}
    except ValueError as e:
        raise HTTPException(422, str(e))


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
