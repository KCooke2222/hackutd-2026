"""Recompute every number in a proposed plan from the data. The LLM's numbers are never trusted."""

from __future__ import annotations

from .. import balance_sheet, balances, finance, pnc_data

MIN_EMERGENCY_MONTHS = 3.0


class PlanRejected(ValueError):
    pass


PLAN_DRAWS = {"lump_sum_from_savings": "cash", "lump_sum_from_cd": "cd", "lump_sum_from_brokerage": "brokerage"}


def validate_plan(customer_id: str, plan: dict) -> dict:
    """Check the plan is feasible and safe; return the authoritative simulation (before vs after).

    The lump sum can come from three ladder tiers: cash above the emergency buffer (moved from savings),
    CDs and brokerage. Retirement and property are never allowed.
    """
    prof = pnc_data.profile(customer_id)
    rows = balance_sheet.accounts(customer_id)
    card = next((a for a in rows if a["account_type"] == "credit_card" and a["source"] != "linked"), None)
    savings = next((a for a in rows if a["account_type"] == "savings"), None)
    if not card or not prof:
        raise PlanRejected("customer has no credit card or profile")

    payment = float(plan.get("monthly_payment", 0) or 0)
    spend_cap = plan.get("monthly_card_spending_cap")
    keep_months = float(plan.get("keep_emergency_months", MIN_EMERGENCY_MONTHS) or MIN_EMERGENCY_MONTHS)
    draws = {tier: float(plan.get(field, 0) or 0) for field, tier in PLAN_DRAWS.items()}
    lump = sum(draws.values())

    refill = finance.refill_breakdown(customer_id)
    current_spend = refill["monthly_avg"]["new_spending"]
    current_payment = refill["monthly_avg"]["payment"]
    new_spend = float(spend_cap) if spend_cap is not None else current_spend
    payment = payment or current_payment

    if min(draws.values()) < 0 or payment <= 0 or new_spend < 0:
        raise PlanRejected("amounts must be positive")
    if keep_months < MIN_EMERGENCY_MONTHS:
        raise PlanRejected(f"must keep at least {MIN_EMERGENCY_MONTHS:g} months of expenses in cash")
    if lump > card["balance"] + 0.005:
        raise PlanRejected(f"lump sum {lump:.2f} exceeds card balance {card['balance']:.2f}")
    sav_bal = savings["balance"] if savings else 0.0
    if draws["cash"] > sav_bal + 0.005:
        raise PlanRejected(f"lump sum from savings {draws['cash']:.2f} exceeds the savings balance {sav_bal:.2f}")

    lad = balance_sheet.ladder(rows, prof["monthly_non_debt_expenses"], keep_months)
    try:
        costs = balance_sheet.draw_costs(lad, draws)
    except ValueError as e:
        raise PlanRejected(str(e)) from None
    floor = lad["emergency_buffer"]

    before = finance.simulate_payoff(card["balance"], card["apr_pct"], current_payment, current_spend)
    after = finance.simulate_payoff(card["balance"], card["apr_pct"], payment, new_spend, lump_sum=lump)
    for sim in (before, after):
        sim["schedule"] = sim["schedule"][:61]
    return {
        "balances_snapshot": balances.snapshot(customer_id),
        "card_balance": card["balance"],
        "apr_pct": card["apr_pct"],
        "savings_balance": sav_bal,
        "savings_after": round(sav_bal - draws["cash"], 2),
        "emergency_floor": round(floor, 2),
        "draws": costs,
        "tap_cost_total": round(sum(d["cost"] for d in costs), 2),
        "inputs": {
            "lump_sum_from_savings": draws["cash"],
            "lump_sum_from_cd": draws["cd"],
            "lump_sum_from_brokerage": draws["brokerage"],
            "lump_sum_total": round(lump, 2),
            "monthly_payment": round(payment, 2),
            "monthly_card_spending": round(new_spend, 2),
            "keep_emergency_months": keep_months,
            "current_monthly_payment": current_payment,
            "current_monthly_spending": current_spend,
        },
        "before": before,
        "after": after,
    }
