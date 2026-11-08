"""Agent v2 tools. All read-only except propose_plan, which only saves a PENDING proposal after
validate_plan_v2. Nothing here imports zeroday.nessie.write; money moves only on human approve (execute.py).
"""

from __future__ import annotations

from typing import Literal

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from .. import balance_sheet, balances, store


# ---------- plan v2 arguments (shared by simulate_plan and propose_plan) ----------

class LumpSum(BaseModel):
    source: Literal["cash", "cd", "brokerage"]
    amount: float
    to_debt: str = Field(description="account_id of the debt it pays")


class Redirect(BaseModel):
    flow: str = Field("external_cash_management", description="the outflow to redirect, e.g. external_cash_management")
    amount_per_month: float


class SpendingTarget(BaseModel):
    category: str
    current: float = Field(description="current monthly average (from spending_detail)")
    target: float = Field(description="new monthly target, not below the customer's own lowest month")
    proof_month: str = Field(description="YYYY-MM when the customer already spent at or below target")
    how: list[str] = Field(description="1-2 concrete actions, at most 8 words each, e.g. 'Cook at home two more nights a week'")


class PlanArgs(BaseModel):
    monthly_debt_budget: float = Field(description="total paid to ALL debts per month, minimums included")
    extra_order: list[str] = Field(description="account_ids getting extra money, highest APR first; only debts above the 8% hurdle")
    lump_sums: list[LumpSum] = Field(default_factory=list)
    redirects: list[Redirect] = Field(default_factory=list)
    spending_targets: list[SpendingTarget] = Field(default_factory=list)
    autopay_full_cards: list[str] = Field(default_factory=list, description="card account_ids: pay full statement once at $0")
    keep_emergency_months: float = 3.0


class ProposeArgs(PlanArgs):
    rationale: str = Field(description="one sentence, at most 15 words")


def to_plan(args: dict) -> dict:
    """Tool kwargs (pydantic models or dicts) -> plan v2 dict."""
    def dump(x):
        return x.model_dump() if hasattr(x, "model_dump") else dict(x)

    return {
        "version": 2,
        "monthly_debt_budget": float(args.get("monthly_debt_budget") or 0),
        "extra_order": list(args.get("extra_order") or []),
        "lump_sums": [dump(x) for x in args.get("lump_sums") or []],
        "redirects": [dump(x) for x in args.get("redirects") or []],
        "spending_targets": [dump(x) for x in args.get("spending_targets") or []],
        "autopay_full_cards": list(args.get("autopay_full_cards") or []),
        "keep_emergency_months": float(args.get("keep_emergency_months") or 3.0),
    }


def sim_brief(sim: dict) -> dict:
    """What the LLM needs from a simulation (the full month table stays server-side)."""
    keys = ("feasible", "problems", "net_worth_24m", "baseline_net_worth_24m", "interest_total",
            "baseline_interest_total", "high_cost_debt_free", "upfront_cost")
    out = {k: sim.get(k) for k in keys if k in sim}
    if sim.get("net_worth_24m") is not None and sim.get("baseline_net_worth_24m") is not None:
        out["net_worth_gain_vs_baseline"] = round(sim["net_worth_24m"] - sim["baseline_net_worth_24m"], 2)
    out["debts"] = [{k: d.get(k) for k in ("account_id", "name", "apr_pct", "payoff_month", "interest")} for d in sim.get("debts", [])]
    months = sim.get("months") or []
    out["cash_low"] = round(min((m.get("cash", 0) for m in months), default=0), 2)
    return out


MAX_REJECTIONS = 2


def _try(fn):
    """Run a read tool; any failure goes back to the model as {"error"} instead of killing the run."""
    try:
        return fn()
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"[:300]}


def make_tools(customer_id: str, state: dict | None = None) -> list:
    """Tools bound to one customer, so the agent can't read other customers' data.

    `state` (optional) collects what the graph needs: proposal_id, rejections, stop.
    """
    st = state if state is not None else {}
    st.setdefault("rejections", 0)

    def _sheet(keep: float = 3.0) -> dict:
        return balance_sheet.balance_sheet(customer_id, keep_months=keep)

    @tool
    def sql(query: str) -> dict:
        """Read-only SQL (one SELECT/WITH statement) over this customer's views: v_accounts, v_transactions,
        v_debts, v_assets, v_monthly_cashflow. Use it to explore questions the other tools don't answer.
        Writes are impossible. Max 200 rows."""
        from .sql_tool import run_sql

        return _try(lambda: run_sql(customer_id, query))

    @tool
    def cash_flow(months: int = 12) -> dict:
        """Monthly income, essentials, discretionary, debt payments, savings/investing, unknown transfers out and
        surplus, per month and averaged. unknown_outflow_per_month is money leaving to an unknown destination."""
        from .. import insights

        return _try(lambda: insights.cash_flow(customer_id, months))

    @tool
    def spending_detail(category: str, months: int = 12) -> dict:
        """One spending category: monthly history, average, p25, lowest month (min_month) and recurring
        charges. Use it before any spending cut: the target must not go below the lowest month."""
        from .. import insights

        return _try(lambda: insights.spending_detail(customer_id, category, months))

    @tool
    def find_recurring() -> list[dict]:
        """Recurring charges (subscriptions, bills) with amount, cadence, account and category."""
        from .. import insights

        return _try(lambda: insights.find_recurring(customer_id))

    @tool
    def compare_sources(amount: float, target_debt: str) -> dict:
        """Rank every money source (surplus, cash above buffer, CD, brokerage, IRA) for paying `amount` into
        `target_debt` (account_id) by 24-month net-worth gain vs leaving the money where it is, with upfront tax
        and penalty. Use a source only if use=true."""
        from .. import wealth

        return _try(lambda: wealth.compare_sources(customer_id, amount, target_debt))

    @tool
    def debt_priority() -> dict:
        """All debts: minimums in order of consequence; extra money to the highest APR above the 8% hurdle.
        Low-rate and secured debt get minimums only."""
        return _try(lambda: _sheet()["priority"])

    @tool
    def liquidity_ladder(amount_needed: float = 0.0, keep_emergency_months: float = 3.0) -> dict:
        """Assets by how cheaply they become cash: cash above the emergency buffer, CDs, brokerage (proposable);
        retirement and property (flag only). With amount_needed, the cheapest split and its one-time cost."""
        def go():
            lad = _sheet(keep_emergency_months)["ladder"]
            out = {k: lad[k] for k in ("keep_months", "emergency_buffer", "proposable_available")}
            out["tiers"] = [{k: t[k] for k in ("key", "label", "proposable", "available", "cost_pct", "note")} for t in lad["tiers"]]
            if amount_needed:
                out["tap"] = balance_sheet.tap(lad, amount_needed)
            return out

        return _try(go)

    @tool(args_schema=PlanArgs)
    def simulate_plan(**kwargs) -> dict:
        """Simulate a candidate plan v2 month by month over 24 months across ALL debts: payoff month per debt,
        interest, cash low point, net worth at 24 months vs the baseline (current behavior). Compare 2-3 options."""
        from .. import wealth

        return _try(lambda: sim_brief(wealth.simulate_plan(customer_id, to_plan(kwargs))))

    @tool(args_schema=ProposeArgs)
    def propose_plan(**kwargs) -> dict:
        """Save ONE plan v2 for the customer to approve, edit or reject. This does NOT move money.
        The server recomputes everything and rejects unsafe or unrealistic plans with reasons; fix them and retry."""
        from .validator import PlanRejected, validate_plan_v2

        if st.get("proposal_id"):
            return {"saved": True, "proposal_id": st["proposal_id"], "note": "already proposed; stop"}
        plan = to_plan(kwargs)
        try:
            # The model picks sources; the server does the arithmetic: every freed dollar goes to debt
            # while any debt above the hurdle remains (an under-filled budget only delays payoff).
            from ..wealth import budget_limit, context
            if plan.get("extra_order"):
                cap = budget_limit(context(customer_id), plan)["max_monthly_debt_budget"]
                plan["monthly_debt_budget"] = max(float(plan.get("monthly_debt_budget") or 0), round(cap, 2))
            sim = validate_plan_v2(customer_id, plan)
        except (PlanRejected, ValueError, KeyError, TypeError, AttributeError) as e:
            st["rejections"] += 1
            reasons = list(getattr(e, "reasons", None) or [str(e)])
            st["last_reasons"] = reasons
            if st["rejections"] > MAX_REJECTIONS:
                st["stop"] = True
                return {"saved": False, "reasons": reasons, "stop": "rejected too many times; no plan"}
            return {"saved": False, "reasons": reasons, "hint": "fix every reason and call propose_plan again",
                    "attempts_left": MAX_REJECTIONS + 1 - st["rejections"]}
        sim = dict(sim)
        sim["balances_snapshot"] = balances.snapshot(customer_id)
        from ..wealth import reality_check
        sim["reality_check"] = _try(lambda: reality_check(customer_id))
        p = store.create_proposal(customer_id, plan, sim, str(kwargs.get("rationale") or ""))
        st["proposal_id"] = p["id"]
        return {"saved": True, "proposal_id": p["id"], **{k: v for k, v in sim_brief(sim).items() if k != "debts"}}

    return [sql, cash_flow, spending_detail, find_recurring, compare_sources, debt_priority, liquidity_ladder,
            simulate_plan, propose_plan]
