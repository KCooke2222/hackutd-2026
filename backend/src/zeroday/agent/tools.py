"""Tools the agent may call. All read-only except propose_plan, which only saves a PENDING proposal.

Nothing here imports zeroday.nessie.write.
"""

from __future__ import annotations

from langchain_core.tools import tool

from .. import balance_sheet, finance, pnc_data, store
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

    def _sheet(keep_emergency_months: float = balance_sheet.DEFAULT_EMERGENCY_MONTHS) -> dict:
        return balance_sheet.balance_sheet(customer_id, keep_months=max(keep_emergency_months, 3.0))

    @tool
    def get_balance_sheet() -> dict:
        """Every account: PNC (live Nessie balances where seeded) plus linked external accounts (student loans,
        brokerage, property, other cards). Returns net worth, total assets/debts and each account's source."""
        s = _sheet()
        keep = ("account_id", "name", "account_type", "kind", "source", "side", "balance", "apr_pct",
                "annual_yield_pct", "min_payment", "cost_basis", "secured")
        return {"totals": s["totals"], "profile": s["profile"], "accounts": [{k: a.get(k) for k in keep} for a in s["accounts"]]}

    @tool
    def liquidity_ladder(amount_needed: float = 0.0, keep_emergency_months: float = 3.0) -> dict:
        """Assets ordered by how cheaply they become cash: 1 cash above the emergency buffer (free), 2 CDs
        (interest penalty), 3 brokerage (capital-gains tax), 4 retirement (flag only, never propose),
        5 property (mention only). With amount_needed, returns the cheapest draws from tiers 1-3 and their cost."""
        lad = _sheet(keep_emergency_months)["ladder"]
        lad = {**lad, "tiers": [{k: v for k, v in t.items() if k != "accounts"} for t in lad["tiers"]]}
        if amount_needed > 0:
            lad["tap"] = balance_sheet.tap(lad, amount_needed)
        return lad

    @tool
    def debt_priority() -> dict:
        """All debts ranked: minimums first in order of consequence (secured, then federal student loans, then
        unsecured), extra money to the highest APR. Low-rate and secured debt get minimums only."""
        return _sheet()["priority"]

    @tool
    def simulate_payoff(
        monthly_payment: float,
        monthly_card_spending: float,
        lump_sum_from_savings: float = 0.0,
        lump_sum_from_cd: float = 0.0,
        lump_sum_from_brokerage: float = 0.0,
        keep_emergency_months: float = 3.0,
    ) -> dict:
        """Simulate the PNC card payoff for a candidate plan. The lump sum can come from savings (cash above the
        emergency buffer), CDs and brokerage. Returns months to $0 (or never), interest, and the one-time cost
        of the draws (CD penalty, capital-gains tax). Use this to compare options before proposing one."""
        card = pnc_data.card_account(customer_id)
        lad = _sheet(keep_emergency_months)["ladder"]
        try:
            draws = balance_sheet.draw_costs(lad, {"cash": lump_sum_from_savings, "cd": lump_sum_from_cd, "brokerage": lump_sum_from_brokerage})
        except ValueError as e:
            return {"error": str(e)}
        lump = sum(d["amount"] for d in draws)
        sim = finance.simulate_payoff(card["balance"], card["apr_pct"], monthly_payment, monthly_card_spending, min(lump, card["balance"]))
        sim["schedule"] = sim["schedule"][:: max(1, len(sim["schedule"]) // 12)]
        sim["draws"] = draws
        sim["tap_cost_total"] = round(sum(d["cost"] for d in draws), 2)
        return sim

    @tool
    def propose_plan(
        lump_sum_from_savings: float,
        monthly_payment: float,
        monthly_card_spending_cap: float,
        keep_emergency_months: float,
        rationale: str,
        lump_sum_from_cd: float = 0.0,
        lump_sum_from_brokerage: float = 0.0,
        autopay_full: bool = True,
    ) -> dict:
        """Save ONE proposed plan for the customer to approve, edit or reject. This does NOT move money.
        monthly_payment: what goes to the card until it hits $0 (at least the card minimum; usually the current
        payment). autopay_full: after $0, pay the full statement every payday so the card stays at $0.
        monthly_card_spending_cap: at least essentials (refill_breakdown monthly_avg.essentials); trim the
        discretionary categories, never cut to $0.
        The server recomputes all numbers and rejects unsafe plans (cash below the emergency buffer, draws
        larger than a ladder tier, retirement or property draws, cap below essentials, payment below minimum)."""
        plan = {
            "lump_sum_from_savings": lump_sum_from_savings,
            "lump_sum_from_cd": lump_sum_from_cd,
            "lump_sum_from_brokerage": lump_sum_from_brokerage,
            "monthly_payment": monthly_payment,
            "monthly_card_spending_cap": monthly_card_spending_cap,
            "keep_emergency_months": keep_emergency_months,
            "autopay_full": autopay_full,
        }
        try:
            sim = validate_plan(customer_id, plan)
        except PlanRejected as e:
            return {"saved": False, "error": str(e), "hint": "adjust the plan and call propose_plan again"}
        p = store.create_proposal(customer_id, plan, sim, rationale)
        return {
            "saved": True,
            "proposal_id": p["id"],
            "after": {k: sim["after"][k] for k in ("pays_off", "months_to_zero", "total_interest")},
            "tap_cost_total": sim["tap_cost_total"],
        }

    return [get_accounts, get_balance_sheet, liquidity_ladder, debt_priority, get_card_activity, refill_breakdown,
            simulate_payoff, propose_plan]
