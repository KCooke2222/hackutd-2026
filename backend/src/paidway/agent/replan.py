"""Check-in re-plan: when a monthly check-in goes off track, the planning agent re-plans from the approved
plan (graph.stream_adjust with replan=...), seeded with what changed and the month's actual balances. An income
drop and the month's balances are applied to the data the agent plans with (wealth.AT_CHECKIN), so its tools
and the solver start from where the customer is now (month-0 lump sums already happened) and the debt budget
can't exceed what is paid today less the income that is gone. The new plan is a PENDING
proposal linked to the approved one, which stays approved until the customer approves the new one.

In demo replay mode the model calls are recorded and replayed like any other run (agent/demo.py keys them by
customer from the /api/customers/{cid}/... path).
"""

from __future__ import annotations

import contextvars

from ..planning import tracking, wealth


def seed(cid: str, prop: dict, t: dict) -> tuple[str, dict]:
    """(request, replan) for graph.stream_adjust from the approved proposal and a tracking payload."""
    plan, st = prop["plan"], t["status"]
    mods = tracking._persistent(tracking.history(cid, prop["id"]))
    drop = float(mods.get("budget_cut") or 0.0)
    budget = float(plan["monthly_debt_budget"])
    cons: dict = {}
    facts = [*st["breaks"], *st["reasons"]] or st["nudges"][:2]
    balances = {d["name"]: round(d["actual"], 2) for d in t["debts"] if not d["done"]}
    checkin = {"month": t["now"], "month_index": t["month_index"], "what_changed": facts,
               "income_drop_pct": round(float(mods.get("income_drop") or 0) * 100, 1), "income_drop_per_month": round(drop, 2),
               "debt_budget_was": budget,
               "actual_debt_balances": balances, "actual_cash": t["tiles"]["cash"],
               "goal_month": st["goal_month"], "projected_month": st["projected_month"]}
    if drop > 0:
        request = (f"Check-in {t['now']}: income down {checkin['income_drop_pct']:.0f}% (${drop:,.0f}/mo). "
                   f"Re-plan for the lower income, low risk while income is down.")
    else:
        request = f"Check-in {t['now']}: off track ({'; '.join(facts)[:200]}). Re-plan to get back on track."
    hist = tracking.history(cid, prop["id"])
    state = tracking._state(hist[-1]) if hist else None
    return request, {"constraints": cons, "checkin": checkin, "income_cut": round(drop, 2), "state": state}


def events(cid: str, prop: dict | None = None, t: dict | None = None):
    """The re-plan as UI events (same as /api/proposals/{pid}/adjust/stream). `done` carries proposal_id."""
    from ..data import store
    from .graph import stream_adjust

    prop = prop or tracking.approved_proposal(cid)
    if not prop:
        yield {"type": "error", "error": "no approved plan to re-plan from"}
        return
    t = t or tracking.dashboard(cid, prop)
    request, rp = seed(cid, prop, t)
    yield {"type": "replan", "request": request, "constraints": rp["constraints"], "checkin": rp["checkin"]}
    # Every step of the run happens inside one context that carries the lower income (a streaming response may
    # resume this generator from a different thread, so the variable is not set in the caller's context).
    run_ctx = contextvars.copy_context()
    run_ctx.run(wealth.AT_CHECKIN.set, {cid: {"income_cut": rp["income_cut"], "state": rp.get("state")}})
    inner = stream_adjust(prop["id"], request, rp)
    while True:
        ev = run_ctx.run(next, inner, None)
        if ev is None:
            break
        msgs = ev.pop("messages", None)
        if msgs:
            store.log_run(cid, msgs)
        if ev["type"] in ("done", "saved"):
            ev["proposal"] = store.get_proposal(ev["proposal_id"]) if ev.get("proposal_id") else None
        yield ev


def run(cid: str, prop: dict | None = None, t: dict | None = None) -> dict:
    """Blocking re-plan: the `done` event (proposal_id, compare, explain), or {"type": "error", ...}."""
    last: dict = {"type": "error", "error": "re-plan produced no result"}
    for ev in events(cid, prop, t):
        if ev["type"] in ("done", "error"):
            last = ev
    return last
