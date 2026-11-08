"""Recompute every number in a proposed plan from the data. The LLM's numbers are never trusted."""

from __future__ import annotations

from .. import balance_sheet, balances, finance, pnc_data

MIN_EMERGENCY_MONTHS = 3.0


class PlanRejected(ValueError):
    """Raised with one message (v1) or a list of every reason (v2). `str(e)` joins them; `e.reasons` lists them."""

    def __init__(self, reasons: str | list[str]):
        self.reasons = [reasons] if isinstance(reasons, str) else list(reasons)
        super().__init__("; ".join(self.reasons))


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


# ---------------- v2: whole balance sheet, net worth ----------------

MAX_HOW_WORDS = 8
NEVER_SOURCES = {"ira", "retirement", "property", "401k", "home"}


def validate_plan_v2(customer_id: str, plan: dict, *, live: bool = True) -> dict:
    """Check a v2 plan against the data and assumptions; return wealth.simulate_plan(...) or raise PlanRejected
    listing every reason. Recomputes every number: the plan's own `current` figures are ignored."""
    from .. import assumptions as A
    from .. import wealth

    if not isinstance(plan, dict):
        raise PlanRejected("plan must be an object")
    reasons: list[str] = []
    if plan.get("version", 2) != 2:
        reasons.append("plan version must be 2")

    keep = plan.get("keep_emergency_months")
    keep = A.get("default_emergency_months") if keep is None else float(keep)
    if keep < A.get("min_emergency_months"):
        reasons.append(f"keep_emergency_months {keep:g} is below the {A.get('min_emergency_months'):g}-month minimum")
        keep = A.get("min_emergency_months")
    ctx = wealth.context(customer_id, keep_months=keep, live=live)
    debts = {d["account_id"]: d for d in ctx["debts"]}
    eligible = [d["account_id"] for d in ctx["debts"] if d["extra_eligible"] and d["balance"] > 0]

    # Lump sums: source tier, availability, target debt.
    gross = {"cash": 0.0, "cd": 0.0, "brokerage": 0.0}
    per_debt: dict[str, float] = {}
    for ls in plan.get("lump_sums") or []:
        src, amt, to = str(ls.get("source", "")).lower(), float(ls.get("amount") or 0), ls.get("to_debt")
        if src in NEVER_SOURCES:
            reasons.append(f"lump sum from {src}: retirement and property are never a source")
            continue
        if src not in wealth.LUMP_SOURCES:
            reasons.append(f"lump sum source {src!r} unknown (cash, cd or brokerage)")
            continue
        if amt <= 0:
            reasons.append("lump sum amount must be positive")
            continue
        if to not in debts:
            reasons.append(f"lump sum to unknown debt {to!r}")
            continue
        if not debts[to]["extra_eligible"]:
            reasons.append(f"lump sum to {debts[to]['name']} ({debts[to]['apr_pct']:g}%): below the "
                           f"{ctx['hurdle_pct']:g}% hurdle or secured, minimum only")
        gross[src] += wealth._gross_needed(src, amt, ctx)
        per_debt[to] = per_debt.get(to, 0.0) + amt
    for to, amt in per_debt.items():
        if amt > debts[to]["balance"] + 0.005:
            reasons.append(f"lump sums ${amt:,.0f} exceed {debts[to]['name']} balance ${debts[to]['balance']:,.0f}")
    for key, g in gross.items():
        avail = ctx["ladder"][key]["available"]
        if g > avail + 0.5:
            what = f"above the {keep:g}-month buffer (${ctx['emergency_buffer']:,.0f})" if key == "cash" else "available"
            reasons.append(f"{key}: needs ${g:,.0f} but only ${avail:,.0f} {what}")

    # Redirects.
    for r in plan.get("redirects") or []:
        if r.get("flow") != wealth.FLOW_KEY:
            reasons.append(f"redirect flow {r.get('flow')!r} unknown; only {wealth.FLOW_KEY}")
        if float(r.get("amount_per_month") or 0) <= 0:
            reasons.append("redirect amount_per_month must be positive")
    redirect = sum(float(r.get("amount_per_month") or 0) for r in plan.get("redirects") or [])
    if redirect > ctx["redirectable_outflow"] + 0.5:
        reasons.append(f"redirects ${redirect:,.0f}/mo exceed the ${ctx['redirectable_outflow']:,.0f}/mo unexplained outflow "
                       f"left after linked minimums")

    # Spending targets.
    seen = set()
    for t in plan.get("spending_targets") or []:
        reasons += _check_target(customer_id, ctx, t, seen)

    # Budget: covers every minimum, and no more than its sources.
    budget = float(plan.get("monthly_debt_budget") or 0)
    lim = wealth.budget_limit(ctx, plan)
    if budget < lim["minimums"] - 0.005:
        reasons.append(f"monthly_debt_budget ${budget:,.0f} is below the ${lim['minimums']:,.0f} sum of minimums "
                       f"(every debt gets at least its minimum)")
    if budget > lim["max_monthly_debt_budget"] + 0.5:
        reasons.append(f"monthly_debt_budget ${budget:,.0f} is over its sources: ${lim['current_debt_payments']:,.0f} current "
                       f"payments + ${lim['redirects']:,.0f} redirects + ${lim['checking_cuts']:,.0f} checking cuts = "
                       f"${lim['max_monthly_debt_budget']:,.0f} (card cuts lower card charges, they don't add budget)")

    # Extra order: exactly the debts above the hurdle, highest APR first.
    order = list(plan.get("extra_order") or [])
    for x in order:
        if x not in debts:
            reasons.append(f"extra_order: unknown debt {x!r}")
        elif not debts[x]["extra_eligible"]:
            d = debts[x]
            reasons.append(f"extra_order includes {d['name']} ({d['apr_pct']:g}%): below the {ctx['hurdle_pct']:g}% hurdle "
                           f"or secured, minimum only")
    for x in eligible:
        if x not in order:
            reasons.append(f"extra_order is missing {debts[x]['name']} ({debts[x]['apr_pct']:g}%)")
    if len(set(order)) != len(order):
        reasons.append("extra_order has duplicates")
    aprs = [debts[x]["apr_pct"] for x in order if x in debts]
    if aprs != sorted(aprs, reverse=True):
        reasons.append("extra_order must be highest APR first")

    for x in plan.get("autopay_full_cards") or []:
        if x not in debts or not debts[x]["is_card"]:
            reasons.append(f"autopay_full_cards: {x!r} is not a credit card with a balance")

    if reasons:
        raise PlanRejected(reasons)
    sim = wealth.simulate_plan(customer_id, plan, ctx=ctx)
    if not sim["feasible"]:
        raise PlanRejected(sim["problems"])
    return sim


def _check_target(customer_id: str, ctx: dict, t: dict, seen: set) -> list[str]:
    from .. import assumptions as A
    from .. import wealth

    out: list[str] = []
    cat = t.get("category")
    c = ctx["spend"].get(cat)
    if c is None:
        return [f"spending target: unknown category {cat!r}"]
    if cat in seen:
        return [f"spending target for {cat} given twice"]
    seen.add(cat)
    if cat in finance.ESSENTIAL_CATEGORIES or c["essential"]:
        return [f"{cat} is essential; essentials are never cut"]
    try:
        target = float(t.get("target"))
    except (TypeError, ValueError):
        return [f"{cat}: target must be a number"]
    cur = c["avg"]
    if target < 0:
        return [f"{cat}: target must be non-negative"]
    if target >= cur:
        return out
    how = [str(h) for h in (t.get("how") or []) if str(h).strip()]
    how_text = " ".join(how).lower()
    meta = wealth._detail_meta(customer_id, cat)
    cancelled = [r for r in meta["recurring"] if "cancel" in how_text and str(r.get("name", "")).lower() in how_text]
    cancel_amt = sum(float(r.get("amount") or 0) for r in cancelled)
    cut_pct = (cur - target) / cur * 100 if cur else 0
    if cut_pct > A.get("max_cut_pct") + 0.01 and not cancelled:
        out.append(f"{cat}: cut {cut_pct:.0f}% is over {A.get('max_cut_pct'):g}% (allowed only when `how` cancels a "
                   f"recurring charge)")
    floor = c["min"] - (cancel_amt if cancelled else 0.0)
    if target < floor - 0.5:
        out.append(f"{cat}: target ${target:,.0f} is below the customer's own lowest month "
                   f"(${c['min']:,.0f} in {c['min_month']})")
    pm = t.get("proof_month")
    if not pm:
        out.append(f"{cat}: proof_month is required (a month they already spent <= ${target:,.0f}; lowest was "
                   f"{c['min_month']} at ${c['min']:,.0f})")
    else:
        row = next((m for m in c["monthly"] if m["month"] == pm), None)
        if row is None:
            out.append(f"{cat}: proof_month {pm!r} is not one of the last {len(c['monthly'])} months")
        elif row["amount"] > target + 0.5 + (cancel_amt if cancelled else 0.0):
            out.append(f"{cat}: in proof_month {pm} they spent ${row['amount']:,.0f}, more than the ${target:,.0f} target")
    if not how:
        out.append(f"{cat}: `how` must list concrete actions (e.g. \"cook 2 more dinners a week\")")
    for h in how:
        if len(h.split()) > MAX_HOW_WORDS:
            out.append(f"{cat}: each `how` action must be at most {MAX_HOW_WORDS} words: {h[:60]!r}")
    return out
