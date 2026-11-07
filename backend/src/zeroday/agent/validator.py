"""Recompute every number in a proposed plan from the data. The LLM's numbers are never trusted."""

from __future__ import annotations

from .. import balances, finance, pnc_data

MIN_EMERGENCY_MONTHS = 3.0


class PlanRejected(ValueError):
    pass


def validate_plan(customer_id: str, plan: dict) -> dict:
    """Check the plan is feasible and safe; return the authoritative simulation (before vs after)."""
    accs = pnc_data.customer_accounts(customer_id)
    card = next((a for a in accs if a["account_type"] == "credit_card"), None)
    savings = next((a for a in accs if a["account_type"] == "savings"), None)
    prof = pnc_data.profile(customer_id)
    if not card or not prof:
        raise PlanRejected("customer has no credit card or profile")

    lump = float(plan.get("lump_sum_from_savings", 0) or 0)
    payment = float(plan.get("monthly_payment", 0) or 0)
    spend_cap = plan.get("monthly_card_spending_cap")
    keep_months = float(plan.get("keep_emergency_months", MIN_EMERGENCY_MONTHS) or MIN_EMERGENCY_MONTHS)

    refill = finance.refill_breakdown(customer_id)
    current_spend = refill["monthly_avg"]["new_spending"]
    current_payment = refill["monthly_avg"]["payment"]
    new_spend = float(spend_cap) if spend_cap is not None else current_spend
    payment = payment or current_payment

    if lump < 0 or payment <= 0 or new_spend < 0:
        raise PlanRejected("amounts must be positive")
    if lump > card["balance"]:
        raise PlanRejected(f"lump sum {lump:.2f} exceeds card balance {card['balance']:.2f}")
    sav_bal = savings["balance"] if savings else 0.0
    floor = keep_months * prof["monthly_non_debt_expenses"]
    if keep_months < MIN_EMERGENCY_MONTHS:
        raise PlanRejected(f"must keep at least {MIN_EMERGENCY_MONTHS:g} months of expenses in savings")
    if sav_bal - lump < floor:
        raise PlanRejected(
            f"savings would drop to {sav_bal - lump:.2f}, below the {keep_months:g}-month emergency floor of {floor:.2f}"
        )

    before = finance.simulate_payoff(card["balance"], card["apr_pct"], current_payment, current_spend)
    after = finance.simulate_payoff(card["balance"], card["apr_pct"], payment, new_spend, lump_sum=lump)
    for sim in (before, after):
        sim["schedule"] = sim["schedule"][:61]
    return {
        "balances_snapshot": balances.snapshot(customer_id),
        "card_balance": card["balance"],
        "apr_pct": card["apr_pct"],
        "savings_balance": sav_bal,
        "savings_after": round(sav_bal - lump, 2),
        "emergency_floor": round(floor, 2),
        "inputs": {
            "lump_sum_from_savings": lump,
            "monthly_payment": round(payment, 2),
            "monthly_card_spending": round(new_spend, 2),
            "keep_emergency_months": keep_months,
            "current_monthly_payment": current_payment,
            "current_monthly_spending": current_spend,
        },
        "before": before,
        "after": after,
    }
