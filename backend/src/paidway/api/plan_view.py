"""Plan screen extras and app-wide questions. Read-only: nothing here writes anything or moves money."""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..data import pnc_data, store

router = APIRouter()


def _need(cid: str) -> None:
    if not pnc_data.profile(cid):
        raise HTTPException(404, f"unknown customer {cid}")


class PlanOnly(BaseModel):
    plan: dict


@router.post("/api/customers/{cid}/plan/extras")
def plan_extras(cid: str, body: PlanOnly) -> dict:
    """For the plan page: the same plan without its suggested cuts (a 24-month series for the net worth
    chart), and what approve would do (execute.schedule is pure: Nessie transfers vs recorded steps)."""
    _need(cid)
    plan = {**body.plan, "version": 2}
    return {"no_cuts": _no_cuts_sim(cid, plan), "schedule": _schedule_preview(cid, plan)}


def _no_cuts_sim(cid: str, plan: dict) -> dict | None:
    """Drop the suggested cuts; their checking share comes off the debt budget (card cuts never fed it)."""
    from ..planning import wealth

    targets = plan.get("spending_targets") or []
    sugg = [t for t in targets if t.get("source") == "suggested"]
    if not sugg:
        return None
    try:
        sim = wealth.simulate_plan(cid, plan)
        gap = lambda ts: sum(max(0.0, float(t["current"]) - float(t["target"])) for t in ts)  # noqa: E731
        checking = float((sim.get("budget") or {}).get("checking_cuts") or 0.0) * gap(sugg) / (gap(targets) or 1.0)
        bare = {**plan, "spending_targets": [t for t in targets if t.get("source") != "suggested"],
                "monthly_debt_budget": round(float(plan["monthly_debt_budget"]) - checking, 2)}
        return wealth.simulate_plan(cid, bare)
    except (ValueError, KeyError, TypeError):
        return None


def _schedule_preview(cid: str, plan: dict) -> dict | None:
    try:
        from ..agent import execute

        transfers, steps = execute.schedule(cid, plan)
        return {"transfers": transfers, "steps": steps}
    except Exception:  # noqa: BLE001 - a preview must never break the plan page
        return None


class AskIn(BaseModel):
    message: str = Field(..., min_length=1, max_length=500)
    proposal_id: str | None = None


ASK_SYSTEM = """You are Paidway, a debt coach. Answer the customer's question from the JSON below only.
Plain words, at most 4 short sentences, no headings. Use only figures that appear in the data; never invent one.
You cannot change anything or move money from here. If they ask for a change and there is a plan, tell them to
say it as a change ("don't touch my savings"); if there is no plan yet, tell them to press Find a plan."""


@router.post("/api/customers/{cid}/ask")
def ask(cid: str, body: AskIn) -> dict:
    """A question about their money or the current plan, with or without a plan. One LLM call, no tools."""
    _need(cid)
    from ..agent.graph import AgentUnavailable, _llm, gather_snapshot

    ctx: dict = {"snapshot": gather_snapshot(cid)}
    p = store.get_proposal(body.proposal_id) if body.proposal_id else None
    if p and p.get("customer_id") == cid:
        s = p.get("simulation") or {}
        ctx["plan"] = {"status": p.get("status"), "plan": p.get("plan"),
                       "outcome": {k: s.get(k) for k in ("high_cost_debt_free", "net_worth_24m", "baseline_net_worth_24m",
                                                         "interest_total", "baseline_interest_total", "budget", "no_cuts")},
                       "explain": {k: (s.get("explain") or {}).get(k) for k in ("headline", "summary", "driver", "downsides", "buffer_why", "lumps_why")}}
    try:
        llm = _llm()
    except AgentUnavailable as e:
        raise HTTPException(503, str(e))
    msg = llm.invoke([("system", ASK_SYSTEM + "\n\nDATA:\n" + json.dumps(ctx, default=str)), ("user", body.message)])
    text = str(getattr(msg, "content", "") or "").strip()
    if "</think>" in text:
        text = text.split("</think>", 1)[1].strip()
    return {"answer": text or "Sorry, I couldn't answer that."}
