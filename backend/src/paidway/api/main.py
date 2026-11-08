"""FastAPI app (Paidway): customer data, agent, proposals.

Safety: the Nessie write client is imported lazily, only inside agent/execute.approve() (approve route).
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..config import NEMOTRON_MODEL, NESSIE_KEY, NVIDIA_API_KEY
from ..data import linked, pnc_data, store
from ..planning import balance_sheet, finance


@asynccontextmanager
async def _lifespan(_app):
    from ..agent import replays

    try:  # committed replay fixtures (data/replays/*.json) -> SQLite; a bad fixture must not stop the API
        replays.import_dir()
    except Exception as e:  # noqa: BLE001
        print(f"replay fixtures not imported: {e}")
    yield


app = FastAPI(title="Paidway", lifespan=_lifespan)

from .guard import install as _install_guard  # noqa: E402

_install_guard(app)  # rate limits + idle-reset stamp, public deploy only (PAIDWAY_PUBLIC=1)
from ..agent.demo import install as _install_demo  # noqa: E402

_install_demo(app)  # hidden demo mode (pw_demo cookie / X-Paidway-Demo / DEMO_REPLAY): replays model calls

from .insights import router as _insights_router  # noqa: E402

app.include_router(_insights_router)
from .timeline import router as _timeline_router  # noqa: E402

app.include_router(_timeline_router)
from .tracking import router as _tracking_router  # noqa: E402

app.include_router(_tracking_router)
from .scenarios import router as _scenarios_router  # noqa: E402

app.include_router(_scenarios_router)
from .plan_view import router as _plan_view_router  # noqa: E402

app.include_router(_plan_view_router)
from .approve_view import router as _approve_view_router  # noqa: E402

app.include_router(_approve_view_router)


def _need_customer(cid: str) -> dict:
    p = pnc_data.profile(cid)
    if not p:
        raise HTTPException(404, f"unknown customer {cid}")
    return p


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "nessie": bool(NESSIE_KEY), "nvidia": bool(NVIDIA_API_KEY), "model": NEMOTRON_MODEL}


# ---------- customers + balance sheet ----------

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


@app.get("/api/customers/{cid}/balance-sheet")
def customer_balance_sheet(cid: str, keep_months: float = Query(3, ge=3, le=24)) -> dict:
    _need_customer(cid)
    return balance_sheet.balance_sheet(cid, keep_months=keep_months)


@app.get("/api/customers/{cid}/debts")
def customer_debts(cid: str) -> dict:
    _need_customer(cid)
    return balance_sheet.balance_sheet(cid)["priority"]


@app.get("/api/customers/{cid}/reality-check")
def customer_reality_check(cid: str) -> dict:
    """Can the debt shrink with this income and spending, even after every lever? If not, the monthly gap."""
    _need_customer(cid)
    from ..planning import wealth

    return wealth.reality_check(cid)


class OptionsIn(BaseModel):
    keys: list[str] = []
    extra_income: float | None = None


@app.get("/api/customers/{cid}/options")
def customer_options(cid: str) -> dict:
    """Underwater option cards: what each closes of the monthly gap (solver-checked) or that it can't be modelled."""
    _need_customer(cid)
    from ..planning import options

    return options.underwater_options(cid)


@app.post("/api/customers/{cid}/options/check")
def customer_options_check(cid: str, body: OptionsIn) -> dict:
    """Re-run the reality check with the picked options together: does the gap close? Read-only."""
    _need_customer(cid)
    from ..planning import options

    return options.check(cid, body.keys, body.extra_income)


@app.get("/api/customers/{cid}/options/applied")
def customer_options_applied(cid: str) -> dict:
    """The options this customer chose to plan with (the plan assumes them; the customer does each one)."""
    _need_customer(cid)
    from ..planning import options

    return {"options": options.applied_cards(cid)}


@app.post("/api/customers/{cid}/options/apply")
def customer_options_apply(cid: str, body: OptionsIn) -> dict:
    """Plan with these options: saved only when, together, they close the gap. Empty keys = plan without any."""
    _need_customer(cid)
    from ..planning import options

    keys = [k for k in body.keys if k in options.APPLYABLE]
    if not keys:
        options.save_applied(cid, [])
        return {"options": [], "check": None}
    r = options.check(cid, keys)
    if not r["closes"]:
        raise HTTPException(422, f"Those still leave ${r['gap_after']:,.0f}/mo short")
    options.save_applied(cid, keys)
    return {"options": options.applied_cards(cid), "check": r}


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
            from ..planning import wealth

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
    from ..agent.graph import AgentUnavailable, run

    try:
        result = run(body.customer_id, body.instruction)
    except AgentUnavailable as e:
        raise HTTPException(503, str(e))
    store.log_run(body.customer_id, result["messages"])
    proposals = store.list_proposals(body.customer_id)
    return {**result, "proposal": next((p for p in proposals if p["status"] == "pending"), None)}


def _sse(evs) -> StreamingResponse:
    import json

    def gen():
        for ev in evs:
            if ev is None:  # keep-alive while a background run is quiet
                yield ": ping\n\n"
                continue
            yield f"event: {ev['type']}\ndata: {json.dumps(ev, default=str)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/agent/stream")
def agent_stream(customer_id: str, instruction: str | None = None) -> StreamingResponse:
    """Server-sent events for one agent run: `step` per tool call/result, then `done` with the proposal.
    Each finished run is recorded (replays.py) so it can be replayed later without calling the model."""
    _need_customer(customer_id)
    from ..agent import replays, runs

    # The run lives on a background thread, so closing the page (or switching customer) doesn't stop it;
    # /api/agent/runs/{id}/stream re-attaches. The first event, `run`, carries the id to re-attach with.
    run = runs.start(customer_id, lambda: replays.record(customer_id, replays.live_events(customer_id, instruction), model=NEMOTRON_MODEL))
    return _sse(_attach(run))


def _attach(run):
    yield {"type": "run", **run.meta()}
    yield from run.tail()


@app.get("/api/agent/runs/latest")
def agent_run_latest(customer_id: str) -> dict | None:
    """The customer's most recent run in this server process (running or finished), or null."""
    from ..agent import runs

    r = runs.latest(customer_id)
    return r.meta() if r else None


@app.post("/api/agent/runs/forget")
def agent_runs_forget() -> dict:
    """Demo reset (`make fresh`): the next page load opens on the Snapshot instead of the last run."""
    from ..agent import runs

    runs.forget(["C001", "C027"])
    return {"ok": True}


@app.get("/api/agent/runs/{run_id}")
def agent_run_meta(run_id: str) -> dict:
    from ..agent import runs

    r = runs.get(run_id)
    if r is None:
        raise HTTPException(404, "no such run (the server may have restarted)")
    return r.meta()


@app.get("/api/agent/runs/{run_id}/stream")
def agent_run_stream(run_id: str) -> StreamingResponse:
    """Re-attach to a run: its buffered events at once, then live ones until it ends (same format as /api/agent/stream)."""
    from ..agent import runs

    r = runs.get(run_id)
    if r is None:
        raise HTTPException(404, "no such run (the server may have restarted)")
    return _sse(_attach(r))


# --- recorded runs: replay a past agent run's event stream at N× speed ---

@app.get("/api/replays")
def replays_list(customer_id: str | None = None) -> list[dict]:
    return store.list_replays(customer_id)


@app.get("/api/replays/{rid}")
def replay_meta(rid: str) -> dict:
    r = store.get_replay(rid)
    if r is None:
        raise HTTPException(404, "no such replay")
    return {k: v for k, v in r.items() if k not in ("events", "proposal")} | {"events": len(r["events"])}


@app.get("/api/replays/{rid}/stream")
def replay_stream(rid: str, speed: float = Query(8.0, ge=0, le=1000)) -> StreamingResponse:
    """The recorded run's events in the live stream's format, paced at `speed`× (0 = instant)."""
    from ..agent import replays

    if store.get_replay(rid) is None:
        raise HTTPException(404, "no such replay")
    return _sse(replays.replay(rid, speed))


class PlanIn(BaseModel):
    plan: dict
    constraints: dict | None = None  # the proposal's customer rules (PR 4), so the preview matches approve


@app.post("/api/customers/{cid}/plan/simulate")
def plan_simulate(cid: str, body: PlanIn) -> dict:
    """Live preview for the plan sliders: validator verdict + the plan and baseline paths (plan v2)."""
    _need_customer(cid)
    from ..agent.validator import PlanRejected
    from ..planning import wealth

    plan = {**body.plan, "version": 2}
    try:
        sim = wealth.simulate_plan(cid, plan)
    except (ValueError, KeyError, TypeError) as e:
        raise HTTPException(422, str(e))
    try:
        from ..agent.validator import validate_plan_v2

        validate_plan_v2(cid, plan, constraints=body.constraints)
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
    from ..agent.execute import ApproveError, approve

    try:
        return approve(pid, edits.plan if edits else None)
    except ApproveError as e:
        raise HTTPException(e.status, e.detail)


@app.post("/api/proposals/{pid}/retry")
def retry_proposal(pid: str) -> dict:
    """Send again the Nessie calls of an approved plan that didn't go through (never the ones that did)."""
    from ..agent.execute import ApproveError, retry

    try:
        return retry(pid)
    except ApproveError as e:
        raise HTTPException(e.status, e.detail)


@app.post("/api/proposals/{pid}/reject")
def reject_proposal(pid: str) -> dict:
    p = store.get_proposal(pid)
    if not p:
        raise HTTPException(404, "no such proposal")
    return store.set_status(pid, "rejected")


# ---------- talk to the plan (PR 4) ----------

class AdjustIn(BaseModel):
    message: str


def _adjust_events(pid: str, message: str):
    from ..agent.graph import stream_adjust

    p = store.get_proposal(pid)
    for ev in stream_adjust(pid, message):
        msgs = ev.pop("messages", None)
        if msgs and p:
            store.log_run(p["customer_id"], msgs)
        if ev["type"] in ("done", "saved"):  # `saved`: the new plan, before its headline is written
            ev["proposal"] = store.get_proposal(ev["proposal_id"]) if ev.get("proposal_id") else None
        yield ev


def _check_adjustable(pid: str) -> None:
    from ..agent.graph import AdjustError, adjustable

    try:
        adjustable(pid)
    except AdjustError as e:
        raise HTTPException(e.status, e.detail)


@app.get("/api/proposals/{pid}/adjust/stream")
def adjust_stream(pid: str, message: str = Query(..., min_length=1, max_length=500)) -> StreamingResponse:
    """Server-sent events for one "talk to the plan" change; same events as /api/agent/stream plus
    `constraints`, `clarify` and `compare`. `done` carries the new pending proposal (the old one is superseded)."""
    _check_adjustable(pid)

    def events():
        import json

        for ev in _adjust_events(pid, message):
            yield f"event: {ev['type']}\ndata: {json.dumps(ev, default=str)}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/proposals/{pid}/adjust")
def adjust_proposal(pid: str, body: AdjustIn) -> dict:
    """Blocking version of adjust/stream (scripts, eval). Returns the `done` event, or 422 with the error."""
    _check_adjustable(pid)
    last: dict = {}
    for ev in _adjust_events(pid, body.message):
        if ev["type"] in ("done", "error"):
            last = ev
    if last.get("type") != "done":
        raise HTTPException(422, last.get("error") or "adjust failed")
    return last


@app.post("/api/proposals/{pid}/restore")
def restore_proposal(pid: str) -> dict:
    """"Keep previous": supersede this adjusted plan and make its parent pending again."""
    p = store.restore(pid)
    if not p:
        raise HTTPException(409, "only the latest adjusted plan can be undone")
    return p


class RuleIn(BaseModel):
    key: str
    item: str | None = None


@app.post("/api/proposals/{pid}/constraints/remove")
def remove_constraint(pid: str, body: RuleIn) -> dict:
    """Drop one customer rule. The plan itself doesn't change; ask for a change to re-plan without it."""
    from ..agent import constraints as C

    p = store.get_proposal(pid)
    if not p:
        raise HTTPException(404, "no such proposal")
    if p["status"] != "pending":
        raise HTTPException(409, f"proposal is {p['status']}, not pending")
    out = store.set_constraints(pid, C.remove(p.get("constraints"), body.key, body.item))
    sim = {**(out["simulation"] or {}), "constraint_labels": C.describe(out.get("constraints"))}
    store.annotate(pid, constraint_labels=sim["constraint_labels"])
    return {**out, "simulation": sim}
