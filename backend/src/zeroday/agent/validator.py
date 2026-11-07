"""Recompute every number in a proposed plan from the data. The LLM's numbers are never trusted."""

from __future__ import annotations

from .. import balance_sheet, balances, finance, pnc_data

MIN_EMERGENCY_MONTHS = 3.0


class PlanRejected(ValueError):
    pass


PLAN_DRAWS = {"lump_sum_from_savings": "cash", "lump_sum_from_cd": "cd", "lump_sum_from_brokerage": "brokerage"}


def validate_plan(customer_id: str, plan: dict, snapshot: bool = True) -> dict:
    """Check the plan is feasible and safe; return the authoritative simulation (before vs after).

    The lump sum can come from three ladder tiers: cash above the emergency buffer (moved from savings),
    CDs and brokerage. Retirement and property are never allowed. The spending cap can't go below essentials
    (groceries, transport...) and the payment can't go below the card's minimum.
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
    essentials = refill["monthly_avg"]["essentials"]
    min_payment = float(card.get("min_payment") or 0)
    autopay = bool(plan.get("autopay_full", True))
    new_spend = float(spend_cap) if spend_cap is not None else current_spend
    payment = payment or current_payment

    if min(draws.values()) < 0 or payment <= 0 or new_spend < 0:
        raise PlanRejected("amounts must be positive")
    if new_spend < essentials - 0.5:
        names = " + ".join(_essential_names(refill)) or "essentials"
        raise PlanRejected(f"spending cap ${new_spend:,.0f} is below essentials ({names} ${essentials:,.0f}/mo)")
    if payment < min_payment - 0.005:
        raise PlanRejected(f"payment ${payment:,.0f} is below the card minimum ${min_payment:,.0f}")
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
        "balances_snapshot": balances.snapshot(customer_id) if snapshot else None,
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
            "autopay_full": autopay,
            "essentials": essentials,
            "min_payment": min_payment,
            "keep_emergency_months": keep_months,
            "current_monthly_payment": current_payment,
            "current_monthly_spending": current_spend,
        },
        "before": before,
        "after": after,
    }


def _essential_names(refill: dict) -> list[str]:
    return [k for k, v in refill.get("new_spending_by_category", {}).items() if v.get("essential")]


def outlook(customer_id: str, plan: dict, months: int = 24) -> dict:
    """What the plan does over the next `months`, for the plan sliders and the outcome chart.

    Three engine paths (finance.project, with refill): today (current payment and spending),
    paydown (plan payment and cap, no lump sum) and plan (lump sum + payment + cap).
    With autopay_full the card is paid in full once at $0; the old payment minus the new spending is freed.
    Also returns the validator's verdict, so the UI can show why an edit would be refused.
    """
    prof = pnc_data.profile(customer_id)
    rows = balance_sheet.accounts(customer_id)
    card = next((a for a in rows if a["account_type"] == "credit_card" and a["source"] != "linked"), None)
    if not card or not prof:
        raise PlanRejected("customer has no credit card or profile")
    keep_months = max(float(plan.get("keep_emergency_months") or MIN_EMERGENCY_MONTHS), MIN_EMERGENCY_MONTHS)
    lad = balance_sheet.ladder(rows, prof["monthly_non_debt_expenses"], keep_months)
    limits = {t["key"]: round(t["available"], 2) for t in lad["tiers"] if t["proposable"]}
    cost_pct = {t["key"]: t["cost_pct"] or 0 for t in lad["tiers"] if t["proposable"]}

    refill = finance.refill_breakdown(customer_id)
    cur_pay, cur_spend = refill["monthly_avg"]["payment"], refill["monthly_avg"]["new_spending"]
    payment = float(plan.get("monthly_payment") or 0) or cur_pay
    cap = plan.get("monthly_card_spending_cap")
    spend = float(cap) if cap is not None else cur_spend
    lump = min(sum(float(plan.get(f) or 0) for f in PLAN_DRAWS), card["balance"])
    autopay = bool(plan.get("autopay_full", True))

    bal, apr = card["balance"], card["apr_pct"]
    today = finance.project(bal, apr, cur_pay, cur_spend, 0, months)
    paydown = finance.project(bal, apr, payment, spend, 0, months, autopay)
    with_plan = finance.project(bal, apr, payment, spend, lump, months, autopay)
    stays_zero = with_plan["payoff_month"] is not None and with_plan["relapse_month"] is None
    cats = [{"category": k, "monthly_avg": v["monthly_avg"], "essential": v["essential"]}
            for k, v in refill.get("new_spending_by_category", {}).items()]

    try:
        sim = validate_plan(customer_id, plan, snapshot=False)
        error = None
    except PlanRejected as e:
        sim, error = None, str(e)
    return {
        "valid": sim is not None,
        "error": error,
        "card_balance": bal,
        "apr_pct": apr,
        "current": {"monthly_payment": cur_pay, "monthly_spending": cur_spend},
        "autopay_full": autopay,
        "categories": cats,
        "limits": {**limits, "card_balance": bal, "keep_emergency_months": keep_months, "emergency_buffer": lad["emergency_buffer"],
                   "spend_floor": refill["monthly_avg"]["essentials"], "min_payment": float(card.get("min_payment") or 0)},
        "cost_pct": cost_pct,
        "draws": sim["draws"] if sim else [],
        "tap_cost_total": sim["tap_cost_total"] if sim else None,
        "scenarios": {"today": today, "paydown": paydown, "plan": with_plan},
        "summary": {
            "debt_free_month": with_plan["payoff_month"],
            "paydown_only_month": paydown["payoff_month"],
            "relapse_month": with_plan["relapse_month"],
            "interest_saved_12m": round(today["interest_first_12m"] - with_plan["interest_first_12m"], 2),
            "interest_saved_total": round(today["interest"] - with_plan["interest"], 2),
            "monthly_paydown": round(payment - spend, 2),
            # Once at $0: the old payment minus what the card now costs (the statement) goes to savings.
            "freed_monthly": round(max(cur_pay - spend, 0), 2) if stays_zero else 0.0,
            "months": months,
        },
    }
