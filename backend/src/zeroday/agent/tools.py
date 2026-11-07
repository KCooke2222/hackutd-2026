"""Tools the agent may call. All read-only except propose_plan, which only saves a PENDING proposal.

Nothing here imports zeroday.nessie.write.
"""

from __future__ import annotations

from langchain_core.tools import tool

from .. import finance, pnc_data, store
from .validator import PlanRejected, validate_plan


def make_tools(customer_id: str) -> list:
    """Tools bound to one customer, so the agent can't read other customers' data."""

    @tool
    def get_accounts() -> dict:
        """All of the customer's accounts (type, balance, APR, yield, monthly payment) plus profile and totals."""
        return pnc_data.customer_summary(customer_id)

    @tool
    def get_card_activity(months: int = 3) -> list[dict]:
        """Credit card ledger entries for the last N months (purchases, interest, payments, fees)."""
        card = pnc_data.card_account(customer_id)
        if not card:
            return []
        txs = pnc_data.customer_transactions(customer_id, account_id=card["account_id"])
        keys = sorted({t["posted_date"][:7] for t in txs})[-max(1, min(months, 24)):]
        keep = ("posted_date", "transaction_type", "category", "description", "amount", "balance_after")
        return [{k: t[k] for k in keep} for t in txs if t["posted_date"][:7] in keys]

    @tool
    def refill_breakdown(months: int = 12) -> dict:
        """Why the card balance isn't going down: average monthly payment vs new spending by category vs interest."""
        return finance.refill_breakdown(customer_id, months=max(1, min(months, 24)))

    @tool
    def simulate_payoff(
        monthly_payment: float,
        monthly_card_spending: float,
        lump_sum_from_savings: float = 0.0,
    ) -> dict:
        """Simulate the card payoff for a candidate plan. Returns months to $0 (or never) and interest.
        Use this to compare options before proposing one."""
        card = pnc_data.card_account(customer_id)
        sim = finance.simulate_payoff(card["balance"], card["apr_pct"], monthly_payment, monthly_card_spending, lump_sum_from_savings)
        sim["schedule"] = sim["schedule"][:: max(1, len(sim["schedule"]) // 12)]
        return sim

    @tool
    def propose_plan(
        lump_sum_from_savings: float,
        monthly_payment: float,
        monthly_card_spending_cap: float,
        keep_emergency_months: float,
        rationale: str,
    ) -> dict:
        """Save ONE proposed plan for the customer to approve, edit or reject. This does NOT move money.
        The server recomputes all numbers and rejects unsafe plans (e.g. savings below the emergency floor)."""
        plan = {
            "lump_sum_from_savings": lump_sum_from_savings,
            "monthly_payment": monthly_payment,
            "monthly_card_spending_cap": monthly_card_spending_cap,
            "keep_emergency_months": keep_emergency_months,
        }
        try:
            sim = validate_plan(customer_id, plan)
        except PlanRejected as e:
            return {"saved": False, "error": str(e), "hint": "adjust the plan and call propose_plan again"}
        p = store.create_proposal(customer_id, plan, sim, rationale)
        return {"saved": True, "proposal_id": p["id"], "after": {k: sim["after"][k] for k in ("pays_off", "months_to_zero", "total_interest")}}

    return [get_accounts, get_card_activity, refill_breakdown, simulate_payoff, propose_plan]
