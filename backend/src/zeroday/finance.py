"""Deterministic money math. The LLM never does arithmetic; it calls these."""

from __future__ import annotations

from collections import defaultdict

from . import pnc_data

MAX_MONTHS = 600
# Card spending categories a plan never cuts; everything else is discretionary.
ESSENTIAL_CATEGORIES = {"groceries", "transport", "gas", "utilities", "healthcare", "health", "insurance", "rent", "housing", "childcare"}


def simulate_payoff(
    balance: float,
    apr_pct: float,
    monthly_payment: float,
    monthly_new_spending: float = 0.0,
    lump_sum: float = 0.0,
) -> dict:
    """Month-by-month card balance: interest accrues, new spending is added, payment is made.

    Returns months to $0 (None if it never pays off), interest totals, and the schedule.
    """
    if min(balance, monthly_payment, monthly_new_spending, lump_sum) < 0:
        raise ValueError("amounts must be non-negative")
    rate = apr_pct / 100 / 12
    b = max(balance - lump_sum, 0.0)
    total_interest = 0.0
    interest_12m = 0.0
    schedule = [{"month": 0, "balance": round(b, 2)}]
    months = 0
    while b > 0.005 and months < MAX_MONTHS:
        interest = b * rate
        total_interest += interest
        if months < 12:
            interest_12m += interest
        b = max(b + interest + monthly_new_spending - monthly_payment, 0.0)
        months += 1
        schedule.append({"month": months, "balance": round(b, 2)})
        # Not shrinking over a year -> it never pays off.
        if months >= 12 and b > 0.005 and b >= schedule[months - 12]["balance"] - 0.005:
            return {
                "pays_off": False,
                "months_to_zero": None,
                "total_interest": None,
                "interest_first_12m": round(interest_12m, 2),
                "start_balance": schedule[0]["balance"],
                "schedule": schedule,
            }
    pays_off = b <= 0.005
    return {
        "pays_off": pays_off,
        "months_to_zero": months if pays_off else None,
        "total_interest": round(total_interest, 2) if pays_off else None,
        "interest_first_12m": round(interest_12m, 2),
        "start_balance": schedule[0]["balance"],
        "schedule": schedule,
    }


def project(
    balance: float,
    apr_pct: float,
    monthly_payment: float,
    monthly_new_spending: float = 0.0,
    lump_sum: float = 0.0,
    months: int = 24,
    autopay_full: bool = False,
) -> dict:
    """Fixed-horizon card path with refill: unlike simulate_payoff it keeps going after $0.

    Each month: interest on the carried balance, new spending is charged, then the payment is made,
    capped at what is owed. If spending exceeds the payment the paid-off card refills (relapse).
    autopay_full: once the card is at $0, the whole statement is paid each month, so it stays at $0.
    balances[0] is the balance right after the lump sum; start_balance is before it.
    """
    if min(balance, monthly_payment, monthly_new_spending, lump_sum) < 0:
        raise ValueError("amounts must be non-negative")
    rate = apr_pct / 100 / 12
    b = max(balance - lump_sum, 0.0)
    out = [round(b, 2)]
    interest = interest_12m = 0.0
    payoff_month = 0 if b <= 0.005 else None
    relapse_month = None
    for m in range(1, months + 1):
        i = b * rate
        interest += i
        if m <= 12:
            interest_12m += i
        owed = b + i + monthly_new_spending
        paid = owed if autopay_full and b <= 0.005 else min(monthly_payment, owed)
        b = owed - paid
        out.append(round(b, 2))
        if b <= 0.005 and payoff_month is None:
            payoff_month = m
        elif b > 0.005 and payoff_month is not None and relapse_month is None:
            relapse_month = m
    return {
        "start_balance": round(balance, 2),
        "balances": out,
        "interest": round(interest, 2),
        "interest_first_12m": round(interest_12m, 2),
        "payoff_month": payoff_month,
        "relapse_month": relapse_month,
        "end_balance": out[-1],
    }


def refill_breakdown(customer_id: str, months: int = 12) -> dict:
    """Why the card balance isn't going down: payments vs new spending vs interest, per month."""
    card = pnc_data.card_account(customer_id)
    if not card:
        return {"customer_id": customer_id, "has_card": False}
    txs = pnc_data.customer_transactions(customer_id, account_id=card["account_id"])
    if not txs:
        return {"customer_id": customer_id, "has_card": True, "months": 0}
    month_keys = sorted({t["posted_date"][:7] for t in txs})[-months:]
    window = [t for t in txs if t["posted_date"][:7] in month_keys]
    n = len(month_keys)

    payments = sum(t["amount"] for t in window if t["transaction_type"] == "payment")
    interest = sum(t["amount"] for t in window if t["transaction_type"] == "interest_accrual")
    fees = sum(t["amount"] for t in window if t["transaction_type"] == "fee")
    by_cat: dict[str, float] = defaultdict(float)
    for t in window:
        if t["transaction_type"] == "purchase":
            by_cat[t["category"]] += t["amount"]
    purchases = sum(by_cat.values())
    essentials = sum(v for k, v in by_cat.items() if k in ESSENTIAL_CATEGORIES)

    stmts = [s for s in pnc_data.customer_statements(customer_id) if s["account_id"] == card["account_id"] and s["month"] in month_keys]
    stmts.sort(key=lambda s: s["month"])
    start = stmts[0]["opening_balance"] if stmts else None
    end = stmts[-1]["closing_balance"] if stmts else card["balance"]

    avg_pay = payments / n
    return {
        "customer_id": customer_id,
        "has_card": True,
        "account_id": card["account_id"],
        "apr_pct": card["apr_pct"],
        "months": n,
        "period": {"from": month_keys[0], "to": month_keys[-1]},
        "balance_start": start,
        "balance_end": end,
        "net_change": round(end - start, 2) if start is not None else None,
        "monthly_avg": {
            "payment": round(avg_pay, 2),
            "new_spending": round(purchases / n, 2),
            "essentials": round(essentials / n, 2),
            "discretionary": round((purchases - essentials) / n, 2),
            "interest": round(interest / n, 2),
            "fees": round(fees / n, 2),
        },
        "new_spending_by_category": {
            k: {"monthly_avg": round(v / n, 2), "essential": k in ESSENTIAL_CATEGORIES, "share_of_payment": round(v / payments, 3) if payments else None}
            for k, v in sorted(by_cat.items(), key=lambda kv: -kv[1])
        },
        "share_of_payment_refilled": round((purchases + interest + fees) / payments, 3) if payments else None,
        "totals": {
            "payments": round(payments, 2),
            "new_spending": round(purchases, 2),
            "interest": round(interest, 2),
            "fees": round(fees, 2),
        },
    }


def today() -> str:
    from datetime import date

    return date.today().isoformat()
