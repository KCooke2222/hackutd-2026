"""What-if scenario routes (roadmap PR 6). Read-only: nothing here writes anything."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import pnc_data, scenarios

router = APIRouter()


class ScenariosIn(BaseModel):
    plan: dict
    scenarios: list[dict] | None = None


class RangeIn(BaseModel):
    plan: dict
    runs: int = 500
    seed: int = 7


def _need(cid: str) -> None:
    if not pnc_data.profile(cid):
        raise HTTPException(404, f"unknown customer {cid}")


@router.get("/api/scenarios/presets")
def presets() -> list[dict]:
    return scenarios.PRESETS


@router.post("/api/customers/{cid}/scenarios")
def run(cid: str, body: ScenariosIn) -> dict:
    _need(cid)
    try:
        return scenarios.run_scenarios(cid, body.plan, body.scenarios)
    except (ValueError, KeyError, TypeError) as e:
        raise HTTPException(422, str(e))


@router.post("/api/customers/{cid}/scenarios/range")
def range_(cid: str, body: RangeIn) -> dict:
    _need(cid)
    try:
        return scenarios.monte_carlo(cid, body.plan, runs=body.runs, seed=body.seed)
    except (ValueError, KeyError, TypeError) as e:
        raise HTTPException(422, str(e))
