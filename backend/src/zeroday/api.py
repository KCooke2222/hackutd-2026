"""FastAPI app: PNC data explorer API + agent + proposals.

Safety: the Nessie write client is imported lazily, only inside approve_proposal().
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

from . import finance, pnc_data, store
from .config import NESSIE_KEY, NVIDIA_API_KEY, NEMOTRON_MODEL

app = FastAPI(title="Zeroday")


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
        out.append({"customer_id": cid, **s["profile"], **{k: s["totals"][k] for k in ("net_worth", "card_balance", "liquid")}})
    return out


@app.get("/api/customers/{cid}")
def customer(cid: str) -> dict:
    _need_customer(cid)
    return {**pnc_data.customer_summary(cid), "statements": pnc_data.customer_statements(cid)}


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
    balance: float
    apr_pct: float
    monthly_payment: float
    monthly_new_spending: float = 0.0
    lump_sum: float = 0.0


@app.post("/api/simulate")
def simulate(body: SimIn) -> dict:
    try:
        return finance.simulate_payoff(**body.model_dump())
    except ValueError as e:
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


@app.get("/api/proposals")
def proposals(customer_id: str | None = None) -> list[dict]:
    return store.list_proposals(customer_id)


class EditIn(BaseModel):
    lump_sum_from_savings: float | None = None
    monthly_payment: float | None = None
    monthly_card_spending_cap: float | None = None
    keep_emergency_months: float | None = None


@app.post("/api/proposals/{pid}/approve")
def approve_proposal(pid: str, edits: EditIn | None = None) -> dict:
    """Human approval is the ONLY path to a money-moving call.

    1. Atomically claim pending -> executing (double clicks get 409, never a second transfer).
    2. Re-validate the (possibly edited) plan.
    3. Re-read the balances; if they moved since the proposal, mark it stale.
    4. Execute in Nessie (lazy write import) or record a local-only approval.
    """
    p = store.get_proposal(pid)
    if not p:
        raise HTTPException(404, "no such proposal")
    if not store.claim(pid):
        raise HTTPException(409, f"proposal is {store.get_proposal(pid)['status']}, not pending")

    plan = dict(p["plan"])
    if edits:
        plan.update({k: v for k, v in edits.model_dump().items() if v is not None})
    from .agent.validator import PlanRejected, validate_plan

    try:
        sim = validate_plan(p["customer_id"], plan)
    except PlanRejected as e:
        store.release(pid)
        raise HTTPException(422, str(e))

    from . import balances

    diffs = balances.changed(p["simulation"].get("balances_snapshot"), sim["balances_snapshot"])
    if diffs:
        store.set_status(pid, "stale", {"error": "balances changed since the proposal; re-run the check", "diffs": diffs})
        raise HTTPException(409, "stale, re-run: balances changed since the proposal (" + "; ".join(diffs) + ")")

    lump = sim["inputs"]["lump_sum_from_savings"]
    if not NESSIE_KEY or lump <= 0:
        why = "NESSIE_KEY not set" if not NESSIE_KEY else "no lump-sum transfer in plan"
        return store.set_status(pid, "approved", {"mode": "local_only", "note": why, "plan": plan, "simulation": sim})

    from .seed import nessie_ids

    ids = nessie_ids(p["customer_id"])
    if not ids.get("savings") or not ids.get("credit_card"):
        return store.set_status(pid, "approved", {"mode": "local_only", "note": "customer not seeded into Nessie (run reseed)", "plan": plan})

    # Lazy import: the only place in the app that can move money.
    from .nessie import write

    try:
        res = write.create_transfer(ids["savings"], ids["credit_card"], lump, finance.today(), "Zeroday approved payoff")
    except Exception as e:  # noqa: BLE001
        return store.set_status(pid, "failed", {"error": str(e), "plan": plan})
    return store.set_status(pid, "executed", {"mode": "nessie", "transfer": res, "plan": plan, "simulation": sim})


@app.post("/api/proposals/{pid}/reject")
def reject_proposal(pid: str) -> dict:
    p = store.get_proposal(pid)
    if not p:
        raise HTTPException(404, "no such proposal")
    return store.set_status(pid, "rejected")
