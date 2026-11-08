"""FastAPI app: PNC data explorer API + agent + proposals.

Safety: the Nessie write client is imported lazily, only inside agent/execute.approve() (approve route).
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from . import balance_sheet, finance, linked, pnc_data, store
from .config import NESSIE_KEY, NVIDIA_API_KEY, NEMOTRON_MODEL

app = FastAPI(title="Zeroday")

from .api_insights import router as _insights_router  # noqa: E402
app.include_router(_insights_router)


def _need_customer(cid: str) -> dict:
    p = pnc_data.profile(cid)
    if not p:
        raise HTTPException(404, f"unknown customer {cid}")
    return p


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "nessie": bool(NESSIE_KEY), "nvidia": bool(NVIDIA_API_KEY), "model": NEMOTRON_MODEL}


# ---------- PNC data explorer ----------

@app.get("/api/overview")
def overview() -> dict:
    return pnc_data.overview()


@app.get("/api/readme")
def readme() -> dict:
    return {"markdown": pnc_data.readme()}


@app.get("/api/customers")
def customers() -> list[dict]:
    out = []
    for cid in pnc_data.customer_ids():
        s = pnc_data.customer_summary(cid)
        full = balance_sheet.totals(balance_sheet.accounts(cid, live=False))
        out.append({
            "customer_id": cid, **s["profile"], **{k: s["totals"][k] for k in ("net_worth", "card_balance", "liquid")},
            "net_worth_all": full["net_worth"], "linked_count": len(linked.for_customer(cid)),
        })
    return out


@app.get("/api/customers/{cid}")
def customer(cid: str) -> dict:
    _need_customer(cid)
    return {**pnc_data.customer_summary(cid), "statements": pnc_data.customer_statements(cid), "linked_accounts": linked.for_customer(cid)}


# ---------- linked external accounts + balance sheet ----------

@app.get("/api/linked")
def linked_accounts(customer_id: str | None = None) -> dict:
    """Accounts Nessie/PNC can't hold, from SQLite: student loans, brokerage, property, other banks' cards."""
    rows = linked.for_customer(customer_id or None)
    by_type: dict[str, dict] = {}
    for r in rows:
        k = f"{r['account_type']}:{r['kind']}"
        b = by_type.setdefault(k, {"account_type": r["account_type"], "kind": r["kind"], "count": 0, "total_balance": 0.0, "aprs": []})
        b["count"] += 1
        b["total_balance"] = round(b["total_balance"] + r["balance"], 2)
        if r["apr_pct"]:
            b["aprs"].append(r["apr_pct"])
    types = [
        {**{k: v for k, v in b.items() if k != "aprs"}, "apr_min": min(b["aprs"], default=None), "apr_max": max(b["aprs"], default=None)}
        for b in sorted(by_type.values(), key=lambda b: -b["count"])
    ]
    return {"count": len(rows), "types": types, "rows": rows}


@app.get("/api/customers/{cid}/balance-sheet")
def customer_balance_sheet(cid: str, keep_months: float = Query(3, ge=3, le=24)) -> dict:
    _need_customer(cid)
    return balance_sheet.balance_sheet(cid, keep_months=keep_months)


@app.get("/api/customers/{cid}/ladder")
def customer_ladder(cid: str, amount: float = Query(0, ge=0), keep_months: float = Query(3, ge=3, le=24)) -> dict:
    _need_customer(cid)
    lad = balance_sheet.balance_sheet(cid, keep_months=keep_months)["ladder"]
    return {**lad, "tap": balance_sheet.tap(lad, amount) if amount else None}


@app.get("/api/customers/{cid}/debts")
def customer_debts(cid: str) -> dict:
    _need_customer(cid)
    return balance_sheet.balance_sheet(cid)["priority"]


@app.get("/api/customers/{cid}/reality-check")
def customer_reality_check(cid: str) -> dict:
    """Can the debt shrink with this income and spending, even after every lever? If not, the monthly gap."""
    _need_customer(cid)
    from . import wealth

    return wealth.reality_check(cid)


@app.get("/api/customers/{cid}/transactions")
def customer_transactions(
    cid: str,
    account_id: str | None = None,
    transaction_type: str | None = None,
    category: str | None = None,
    flow_class: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    order: str = Query("desc", pattern="^(asc|desc)$"),
) -> dict:
    _need_customer(cid)
    rows = pnc_data.customer_transactions(
        cid, account_id=account_id or None, transaction_type=transaction_type or None, category=category or None,
        flow_class=flow_class or None, date_from=date_from or None, date_to=date_to or None,
    )
    if order == "desc":
        rows = rows[::-1]
    start = (page - 1) * page_size
    all_rows = pnc_data.customer_transactions(cid)
    return {
        "total": len(rows),
        "page": page,
        "page_size": page_size,
        "rows": rows[start : start + page_size],
        "facets": {
            "account_id": sorted({t["account_id"] for t in all_rows}),
            "transaction_type": sorted({t["transaction_type"] for t in all_rows}),
            "category": sorted({t["category"] for t in all_rows}),
            "flow_class": sorted({t["flow_class"] for t in all_rows}),
        },
    }


# ---------- agent-facing numbers ----------

@app.get("/api/customers/{cid}/summary")
def customer_summary(cid: str) -> dict:
    _need_customer(cid)
    card = pnc_data.card_account(cid)
    card_history = []
    if card:
        card_history = [
            {"month": s["month"], "balance": s["closing_balance"]}
            for s in pnc_data.customer_statements(cid) if s["account_id"] == card["account_id"]
        ]
    return {**pnc_data.customer_summary(cid), "card_history": card_history, "refill": finance.refill_breakdown(cid)}


@app.get("/api/customers/{cid}/refill")
def customer_refill(cid: str, months: int = Query(12, ge=1, le=24)) -> dict:
    _need_customer(cid)
    return finance.refill_breakdown(cid, months)


class SimIn(BaseModel):
    """Plan v2 simulation ({customer_id, plan}); the old single-card body (balance, apr_pct...) still works."""
    customer_id: str | None = None
    plan: dict | None = None
    balance: float | None = None
    apr_pct: float | None = None
    monthly_payment: float | None = None
    monthly_new_spending: float = 0.0
    lump_sum: float = 0.0


@app.post("/api/simulate")
def simulate(body: SimIn) -> dict:
    try:
        if body.customer_id:
            _need_customer(body.customer_id)
            from . import wealth

            return wealth.simulate_plan(body.customer_id, body.plan)
        if body.balance is None or body.apr_pct is None or body.monthly_payment is None:
            raise HTTPException(400, "send {customer_id, plan} or {balance, apr_pct, monthly_payment}")
        return finance.simulate_payoff(body.balance, body.apr_pct, body.monthly_payment, body.monthly_new_spending, body.lump_sum)
    except (ValueError, KeyError, TypeError) as e:
        raise HTTPException(400, str(e))


# ---------- agent + proposals ----------

class RunIn(BaseModel):
    customer_id: str
    instruction: str | None = None


@app.post("/api/agent/run")
def agent_run(body: RunIn) -> dict:
    _need_customer(body.customer_id)
    from .agent.graph import AgentUnavailable, run

    try:
        result = run(body.customer_id, body.instruction)
    except AgentUnavailable as e:
        raise HTTPException(503, str(e))
    store.log_run(body.customer_id, result["messages"])
    proposals = store.list_proposals(body.customer_id)
    return {**result, "proposal": next((p for p in proposals if p["status"] == "pending"), None)}


@app.get("/api/agent/stream")
def agent_stream(customer_id: str, instruction: str | None = None) -> StreamingResponse:
    """Server-sent events for one agent run: `step` per tool call/result, then `done` with the proposal."""
    _need_customer(customer_id)
    from .agent.graph import stream_run

    def events():
        import json

        for ev in stream_run(customer_id, instruction):
            msgs = ev.pop("messages", None)
            if msgs:
                store.log_run(customer_id, msgs)
            if ev["type"] == "done":
                ps = store.list_proposals(customer_id)
                ev["proposal"] = next((p for p in ps if p["status"] == "pending"), None)
            yield f"event: {ev['type']}\ndata: {json.dumps(ev, default=str)}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


class PlanIn(BaseModel):
    plan: dict


@app.post("/api/customers/{cid}/plan/simulate")
def plan_simulate(cid: str, body: PlanIn) -> dict:
    """Live preview for the plan sliders: validator verdict + the plan and baseline paths (plan v2)."""
    _need_customer(cid)
    from . import wealth
    from .agent.validator import PlanRejected

    plan = {**body.plan, "version": 2}
    try:
        sim = wealth.simulate_plan(cid, plan)
    except (ValueError, KeyError, TypeError) as e:
        raise HTTPException(422, str(e))
    try:
        from .agent.validator import validate_plan_v2

        validate_plan_v2(cid, plan)
        reasons: list[str] = []
    except PlanRejected as e:
        reasons = list(getattr(e, "reasons", None) or [str(e)])
    return {"valid": not reasons and bool(sim.get("feasible", True)), "reasons": reasons, "sim": sim,
            "baseline": wealth.simulate_plan(cid, None)}


@app.get("/api/proposals")
def proposals(customer_id: str | None = None) -> list[dict]:
    return store.list_proposals(customer_id)


class EditIn(BaseModel):
    plan: dict | None = None  # edited plan v2 fields; omitted = approve as proposed


@app.post("/api/proposals/{pid}/approve")
def approve_proposal(pid: str, edits: EditIn | None = None) -> dict:
    """Human approval is the ONLY path to a money-moving call; see agent/execute.py."""
    from .agent.execute import ApproveError, approve

    try:
        return approve(pid, edits.plan if edits else None)
    except ApproveError as e:
        raise HTTPException(e.status, e.detail)


@app.post("/api/proposals/{pid}/reject")
def reject_proposal(pid: str) -> dict:
    p = store.get_proposal(pid)
    if not p:
        raise HTTPException(404, "no such proposal")
    return store.set_status(pid, "rejected")
