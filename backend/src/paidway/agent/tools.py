"""Agent v2 tools. All read-only except propose_plan, which only saves a PENDING proposal after
validate_plan_v2. Nothing here imports paidway.nessie.write; money moves only on human approve (execute.py).
"""

from __future__ import annotations

from typing import Literal

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from ..data import balances, categories, store
from ..planning import balance_sheet

# ---------- plan v2 arguments (shared by simulate_plan and propose_plan) ----------

class LumpSum(BaseModel):
    source: Literal["cash", "cd", "brokerage"] = Field(
        description="cash = PNC checking+savings above the emergency buffer; cd / brokerage = break or sell")
    amount: float
    to_debt: str = Field(description="account_id of the debt it pays")


class Redirect(BaseModel):
    flow: Literal[categories.MOVABLE_FLOWS] = Field(  # type: ignore[valid-type]
        description="a movable named flow: monthly_surplus (redirect the surplus left in checking to debt) or "
                    "brokerage_auto_invest / cd_contribution (pause the contribution). Never fixed or ask items.")
    amount_per_month: float


class SpendingTarget(BaseModel):
    category: str
    current: float = Field(description="current monthly average (from spending_detail)")
    target: float = Field(description="new monthly target, not below the customer's own lowest month")
    proof_month: str = Field(description="YYYY-MM when the customer already spent at or below target")
    how: list[str] = Field(description="1-2 concrete actions, at most 8 words each, e.g. 'Cook at home two more nights a week'")
    source: Literal["agreed", "suggested"] | None = Field(
        None, description="suggested = the solver's proposal (it remakes these itself; no need to relay them); "
                          "agreed / omitted = a cut the customer chose")


class PlanArgs(BaseModel):
    monthly_debt_budget: float = Field(description="total paid to ALL debts per month, minimums included")
    extra_order: list[str] = Field(description="account_ids getting extra money, highest APR first; only debts above the 8% hurdle")
    lump_sums: list[LumpSum] = Field(default_factory=list)
    redirects: list[Redirect] = Field(default_factory=list)
    spending_targets: list[SpendingTarget] = Field(default_factory=list)
    autopay_full_cards: list[str] = Field(default_factory=list, description="card account_ids: pay full statement once at $0")
    keep_emergency_months: float = 3.0


class Cut(BaseModel):
    category: str
    target: float = Field(description="the monthly target the PERSON agreed to")
    how: list[str] = Field(default_factory=list, description="optional; 'Cancel <name>' for subscriptions")


DECLINED = Field(default_factory=list, description="categories the customer said not to cut: never suggested")


class OptimizeArgs(BaseModel):
    keep_emergency_months: float = Field(3.0, description="months of expenses kept as cash (min 2)")
    spending_targets: list[Cut] = Field(default_factory=list, description="only cuts the person chose; the solver "
                                        "adds up to 3 suggested cuts of its own on top (source=suggested)")
    declined_cuts: list[str] = DECLINED
    redirect_flows: list[str] | None = Field(None, description="limit which movable flows may go to debt; omit = all their flags allow")


class ProposeArgs(PlanArgs):
    rationale: str = Field(description="one sentence, at most 15 words")
    declined_cuts: list[str] = DECLINED


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
    b = sim.get("budget") or {}
    if b.get("net_paydown_per_month") is not None:  # lead with net: gross minus this month's new card purchases
        out["paydown"] = {k: b.get(k) for k in ("current_net_paydown_per_month", "net_paydown_per_month",
                                                "current_debt_payments", "monthly_debt_budget",
                                                "current_new_card_purchases", "new_card_purchases")}
    if sim.get("no_cuts"):
        out["no_cuts"] = sim["no_cuts"]
    return out


def _declined(kwargs: dict, inputs: dict) -> list[str]:
    """Categories never to suggest a cut in: the tool argument plus the customer's protect_categories."""
    return sorted({str(x).strip().lower() for x in [*(kwargs.get("declined_cuts") or []), *(inputs.get("declined_cuts") or [])]
                   if str(x).strip()})


MAX_REJECTIONS = 2


def _try(fn):
    """Run a read tool; any failure goes back to the model as {"error"} instead of killing the run."""
    try:
        return fn()
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"[:300]}


def make_tools(customer_id: str, state: dict | None = None, constraints: dict | None = None) -> list:
    """Tools bound to one customer, so the agent can't read other customers' data.

    `state` (optional) collects what the graph needs: proposal_id, rejections, stop. For an adjustment it also
    carries parent_id and request, saved on the new proposal. `constraints` are the customer's own rules
    (agent/constraints.py): simulate_plan reports breaks, propose_plan rejects them.
    """
    st = state if state is not None else {}
    st.setdefault("rejections", 0)
    cons = constraints or {}

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
        """Monthly income, essentials, discretionary, debt payments, savings/investing and surplus (the change in
        PNC cash), per month and averaged. Also every spending
        category and named flow with its flag (fixed/ask: never move; trim: max_cut_pct; cancel: only named
        subscriptions; redirect/pause: may go to debt). saved_per_month is the real monthly slack; if it is
        <= 0 there is no monthly money to redirect."""
        from ..planning import insights

        return _try(lambda: insights.cash_flow(customer_id, months))

    @tool
    def spending_detail(category: str, months: int = 12) -> dict:
        """One spending category: monthly history, average, p25, lowest month (min_month) and recurring
        charges. Use it before any spending cut: the target must not go below the lowest month."""
        from ..planning import insights

        return _try(lambda: insights.spending_detail(customer_id, category, months))

    @tool
    def find_recurring() -> list[dict]:
        """Recurring charges (subscriptions, bills) with merchant, amount, cadence, account, category and flag.
        A subscriptions cut must name the merchants it cancels."""
        from ..planning import insights

        return _try(lambda: insights.find_recurring(customer_id))

    @tool
    def compare_sources(amount: float, target_debt: str) -> dict:
        """Rank every money source (surplus, PNC cash, CD, brokerage, IRA) for paying `amount` into
        `target_debt` (account_id) by 24-month net-worth gain vs leaving the money where it is, with upfront tax
        and penalty. Use a source only if use=true. lump_plan (when present) is the ready split of cash above
        the buffer across all high-cost debts: simulate it as lump_sums."""
        from ..planning import wealth

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
        from ..planning import wealth

        def go():
            plan = to_plan(kwargs)
            out = sim_brief(wealth.simulate_plan(customer_id, plan))
            if cons:
                from .constraints import check

                out["breaks_customer_rules"] = check(plan, cons, wealth.context(customer_id)["spend"])
            return out

        return _try(go)

    @tool(args_schema=ProposeArgs)
    def propose_plan(**kwargs) -> dict:
        """Save ONE plan v2 for the customer to approve, edit or reject. This does NOT move money.
        The server recomputes everything and rejects unsafe or unrealistic plans with reasons; fix them and retry."""
        from .validator import PlanRejected, validate_plan_v2

        if st.get("proposal_id"):
            return {"saved": True, "proposal_id": st["proposal_id"], "note": "already proposed; stop"}
        # The numbers come from the solver: the model only relays the person's soft inputs (buffer months, the
        # cuts they agreed to); lump sums, budget, order, redirects and autopay are computed, never guessed.
        from ..planning import solver

        given = to_plan(kwargs)
        try:
            # The customer's rules become solver inputs, so the solved plan obeys them by construction;
            # validate_plan_v2(constraints=...) re-checks them as a safety net.
            from ..planning import wealth
            from .constraints import solver_inputs
            inputs = solver_inputs(cons, given["keep_emergency_months"], given["spending_targets"],
                                   wealth.context(customer_id)["spend"] if cons else None)
            declined = _declined(kwargs, inputs)
            inputs["declined_cuts"] = declined
            solved = solver.solve(customer_id, inputs, with_tradeoffs=False)
            plan = solved["plan"]
            sim = validate_plan_v2(customer_id, plan, constraints=cons) if cons else solved["sim"]
            if solved["sim"].get("no_cuts"):
                sim = {**sim, "no_cuts": solved["sim"]["no_cuts"]}
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
        try:  # optional cuts, priced on top of this plan: the customer's tradeoffs, never required
            from ..planning.wealth import cut_options
            protect = set(cons.get("protect_categories") or []) | set(declined)
            sim["cut_options"] = [o for o in cut_options(customer_id, plan, sim=sim) if o.get("category") not in protect]
        except Exception:  # noqa: BLE001
            sim["cut_options"] = []
        sim["balances_snapshot"] = balances.snapshot(customer_id)
        from ..planning.wealth import reality_check
        sim["reality_check"] = _try(lambda: reality_check(customer_id))
        try:
            p = store.create_proposal(customer_id, plan, sim, str(kwargs.get("rationale") or ""),
                                      parent_id=st.get("parent_id"), constraints=cons or None, request=st.get("request"))
        except store.Conflict as e:
            st["stop"], st["last_reasons"] = True, [str(e)]
            return {"saved": False, "stop": str(e)}
        st["proposal_id"] = p["id"]
        out = {"saved": True, "proposal_id": p["id"], **{k: v for k, v in sim_brief(sim).items() if k != "debts"}}
        out["server_filled"] = {k: plan[k] for k in ("monthly_debt_budget", "extra_order", "lump_sums", "redirects")}
        return out

    @tool(args_schema=OptimizeArgs)
    def optimize_plan(**kwargs) -> dict:
        """The optimal plan for the person's soft inputs (buffer months, cuts they chose, cuts they declined,
        flows they allow): lump sums, monthly budget and order, payoff month per debt, net worth vs baseline,
        interest saved, `paydown` (net = what actually shrinks the debt each month, after new card purchases),
        up to 3 SUGGESTED cuts (spending_targets with source=suggested, each with `buys`; `no_cuts` is the plan
        without them) and `tradeoffs`: what cutting each flexible category to its floor would buy.
        Deterministic and read-only. propose_plan uses the same solver and keeps the suggestions."""
        from ..planning import solver

        def go():
            inp = {"keep_emergency_months": kwargs.get("keep_emergency_months"),
                   "spending_targets": [c.model_dump() if hasattr(c, "model_dump") else dict(c)
                                        for c in kwargs.get("spending_targets") or []],
                   "redirect_flows": kwargs.get("redirect_flows")}
            if cons:  # the same rules propose_plan applies
                from ..planning import wealth
                from .constraints import solver_inputs
                inp.update(solver_inputs(cons, inp["keep_emergency_months"], inp["spending_targets"],
                                         wealth.context(customer_id)["spend"]))
            inp["declined_cuts"] = _declined(kwargs, inp)
            r = solver.solve(customer_id, inp)
            return {"plan": r["plan"], "brief": r["brief"]}

        return _try(go)

    return [sql, cash_flow, spending_detail, find_recurring, compare_sources, debt_priority, liquidity_ladder,
            simulate_plan, optimize_plan, propose_plan]
