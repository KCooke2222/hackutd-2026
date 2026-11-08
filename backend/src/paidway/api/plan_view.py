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

        transfers, steps = execute.schedule(cid, plan, execute._bill_sim(cid, plan, None), live=False)
        return {"transfers": transfers, "steps": steps}
    except Exception:  # noqa: BLE001 - a preview must never break the plan page
        return None


class AskIn(BaseModel):
    message: str = Field(..., min_length=1, max_length=500)
    proposal_id: str | None = None


ASK_SYSTEM = """You are Paidway, a friendly debt coach. Answer in plain words, 2 to 4 short sentences, no headings,
no lists, no markdown.
- If they ask what something means (income-driven repayment, avalanche vs snowball, a balance transfer, a hardship
  program, APR vs APY, consolidation, credit utilization, ...), explain it from general knowledge in one or two
  sentences, then say what it means for THEM using the DATA below (which of their debts it applies to, their
  current payment, and the estimate in `what_ifs` when there is one, e.g. "an income-driven plan would put your
  Federal Direct Loan at about $266/mo instead of $466").
- Every dollar figure, rate or date about the customer must come from the DATA; never invent one. General facts
  (how a program works) are fine. Call an estimate an estimate (the servicer or card issuer sets the real figure),
  and only when you give one. Their debts are the ones in `accounts`; if the plan names others, go by `accounts`.
- If an option is already in their plan (`already_in_plan`), say their plan already uses it.
- Never say "DATA", "what_ifs" or other field names to them.
- Only if they ask to move money or make a payment: say nothing moves until they approve a plan. If they ask for a
  change and there is a pending plan, tell them to say it as a change ("don't touch my savings"); with no plan yet,
  tell them to press Find a plan. Otherwise don't mention what you can't do."""

def _what_ifs(cid: str) -> dict:
    """Solver-side estimates for the options a concept question may be about. Figures only from code."""
    from ..planning import options, wealth

    try:
        ctx = wealth.context(cid, with_options=False)
    except Exception:  # noqa: BLE001 - extra context must never break a question
        return {}
    applied = options.applied(cid)
    out: dict = {"options_already_in_plan": [options.OPTIONS[k][0] for k in applied]}
    loans = [d for d in ctx["debts"] if d["account_type"] == "student_loan"]
    if loans:
        idr = options._idr_payment(ctx)
        freed = sum(max(d["min_payment"] - idr, 0.0) for d in loans)
        out["income_driven_repayment"] = {
            "qualifying_loans": [{"name": d.get("name"), "balance": d["balance"], "apr_pct": d["apr_pct"],
                                  "current_min_payment": d["min_payment"]} for d in loans],
            "estimated_idr_payment_per_month": idr,
            "estimate_basis": f"10% of yearly income above ${options.IDR_POVERTY_150:,.0f} (150% of the poverty line, one person), "
                              "per month; federal loans only; the servicer sets the real figure",
            "monthly_cash_freed": round(freed, 2),
            "helps": freed > 1,
            "already_in_plan": "idr_student_loan" in applied,
        }
    cards = [d for d in ctx["debts"] if d["is_card"] and d["apr_pct"] > options.HARDSHIP_APR_PCT]
    if cards:
        out["card_hardship_program"] = {
            "typical_hardship_apr_pct": options.HARDSHIP_APR_PCT,
            "cards": [{"name": d.get("name"), "balance": d["balance"], "apr_pct": d["apr_pct"], "current_min_payment": d["min_payment"],
                       "min_payment_at_hardship_rate": options._card_min(d["balance"], options.HARDSHIP_APR_PCT)} for d in cards],
            "already_in_plan": "card_hardship" in applied,
        }
    ira = ctx["flows"]["contrib"].get("ira", 0.0)
    if ira > 1:
        out["pause_ira"] = {"monthly_ira_contribution": round(ira, 2), "already_in_plan": "pause_ira" in applied}
    return out


def _followup(question: str, wi: dict, pending: bool) -> dict | None:
    """A one-tap "try it in my plan" when the question is about an option that would help and isn't in yet."""
    import re

    from ..planning.options import CHAT_OPTIONS

    if not pending:
        return None
    for key, (pat, label, message) in CHAT_OPTIONS.items():
        if not re.search(pat, question, re.I):
            continue
        info = wi.get({"idr_student_loan": "income_driven_repayment", "card_hardship": "card_hardship_program"}.get(key, key))
        if not info or info.get("already_in_plan") or (key == "idr_student_loan" and not info.get("helps")):
            return None
        return {"option": key, "label": label, "message": message}
    return None


@router.post("/api/customers/{cid}/ask")
def ask(cid: str, body: AskIn) -> dict:
    """A question about their money, the plan, or a money concept, with or without a plan. One LLM call, no
    tools. `followup`: an option the chat can plan with, when the question is about one (else null)."""
    _need(cid)
    from ..agent.graph import AgentUnavailable, _budgeted, _llm, gather_snapshot

    wi = _what_ifs(cid)
    ctx: dict = {"snapshot": gather_snapshot(cid), "what_ifs": wi}
    p = store.get_proposal(body.proposal_id) if body.proposal_id else None
    if p and p.get("customer_id") == cid:
        s = p.get("simulation") or {}
        ctx["plan"] = {"status": p.get("status"), "plan": p.get("plan"),
                       "outcome": {k: s.get(k) for k in ("high_cost_debt_free", "net_worth_24m", "baseline_net_worth_24m",
                                                         "interest_total", "baseline_interest_total", "budget", "no_cuts")},
                       "explain": {k: (s.get("explain") or {}).get(k) for k in ("headline", "summary", "driver", "downsides", "buffer_why", "lumps_why")}}
    else:
        p = None
    try:
        llm = _budgeted(_llm(), 512)
    except AgentUnavailable as e:
        raise HTTPException(503, str(e))
    msg = llm.invoke([("system", ASK_SYSTEM + "\n\nDATA:\n" + json.dumps(ctx, default=str)), ("user", body.message)])
    text = str(getattr(msg, "content", "") or "").strip()
    if "</think>" in text:
        text = text.split("</think>", 1)[1].strip()
    return {"answer": text or "Sorry, I couldn't answer that.",
            "followup": _followup(body.message, wi, bool(p and p.get("status") == "pending"))}


class OptionIn(BaseModel):
    key: str


@router.post("/api/customers/{cid}/options/add")
def options_add(cid: str, body: OptionIn) -> dict:
    """From chat: plan with one more option (the customer does it, e.g. applies for IDR). Unlike options/apply it
    doesn't require the gap to close: the plan shows what is left. Writes only the saved option list."""
    _need(cid)
    from ..planning import options

    if body.key not in options.APPLYABLE:
        raise HTTPException(422, f"unknown option {body.key!r}")
    keys = options.save_applied(cid, [*options.applied(cid), body.key])
    return {"options": options.applied_cards(cid), "keys": keys}
