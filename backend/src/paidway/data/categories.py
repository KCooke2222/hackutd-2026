"""What a plan may move, per spending category and named cash flow. One table; the validator enforces it.

    fixed     never moved (rent, utilities, insurance, groceries, gas, debt minimums)
    ask       never moved by a plan; the agent asks the customer instead (childcare, healthcare, family)
    trim      a spending target may lower it by at most `max_pct` of its average, never below the lowest month
    cancel    only by cancelling named recurring charges (subscriptions)
    redirect  a named flow that may go to debt instead (the monthly surplus: money that would stay in cash)
    pause     a named contribution that may stop for a while and go to debt (brokerage auto-invest, CD)

Spending categories are the `category` of expense transactions. Flows are the keys of `redirects[].flow`.
"""

from __future__ import annotations

FIXED, ASK, TRIM, CANCEL, REDIRECT, PAUSE = "fixed", "ask", "trim", "cancel", "redirect", "pause"

SPENDING: dict[str, dict] = {
    "housing": {"flag": FIXED, "label": "Rent / housing"},
    "utilities": {"flag": FIXED, "label": "Utilities"},
    "insurance": {"flag": FIXED, "label": "Insurance"},
    "groceries": {"flag": FIXED, "label": "Groceries"},
    "transport": {"flag": FIXED, "label": "Gas & transport"},
    "healthcare": {"flag": ASK, "label": "Healthcare"},
    "childcare": {"flag": ASK, "label": "Childcare"},
    "family_support": {"flag": ASK, "label": "Money to family"},
    "car_maintenance": {"flag": FIXED, "label": "Car repairs & maintenance"},
    "annual_bills": {"flag": FIXED, "label": "Annual bills (registration, memberships, premiums)"},
    "travel": {"flag": TRIM, "max_pct": 40.0, "label": "Travel"},
    "gifts_holidays": {"flag": TRIM, "max_pct": 40.0, "label": "Gifts & holidays"},
    "home_household": {"flag": TRIM, "max_pct": 30.0, "label": "Home & household"},
    "phone_internet": {"flag": TRIM, "max_pct": 25.0, "label": "Phone & internet"},
    "other_spending": {"flag": TRIM, "max_pct": 20.0, "label": "Household & other"},
    "dining": {"flag": TRIM, "max_pct": 30.0, "label": "Dining & delivery"},
    "shopping": {"flag": TRIM, "max_pct": 30.0, "label": "Shopping"},
    "subscriptions": {"flag": CANCEL, "label": "Subscriptions"},
}

# Named non-spending flows out of checking. `source` says how wealth._flows measures each one.
FLOWS: dict[str, dict] = {
    "debt_minimums": {"flag": FIXED, "label": "Debt payments (minimums)"},
    "monthly_surplus": {"flag": REDIRECT, "label": "Monthly surplus left in checking",
                        "note": "income left after spending, payments and contributions; it would otherwise stay "
                                "in cash, so redirecting it only gains the APR minus the cash yield"},
    "brokerage_auto_invest": {"flag": PAUSE, "label": "Brokerage auto-invest", "dest": "brokerage"},
    "cd_contribution": {"flag": PAUSE, "label": "CD contributions", "dest": "cd"},
    "ira_contribution": {"flag": PAUSE, "label": "IRA contributions", "dest": "ira",
                         "note": "paused while any debt costs more than 7% APR, restarts automatically at high-cost $0; "
                                 "an employer 401(k) match is payroll, not a flow here, and is never paused"},
}

MOVABLE_FLOWS = tuple(k for k, v in FLOWS.items() if v["flag"] in (REDIRECT, PAUSE))
ESSENTIAL = {k for k, v in SPENDING.items() if v["flag"] in (FIXED, ASK)}


def spending_flag(category: str) -> dict:
    """Flag for a spending category; unknown categories are fixed (never cut what we can't name)."""
    return SPENDING.get(category, {"flag": FIXED, "label": category.replace("_", " ")})


def flow_flag(flow: str) -> dict | None:
    return FLOWS.get(flow)


def table() -> list[dict]:
    """The whole table, for tools and the UI."""
    rows = [{"kind": "spending", "key": k, **v} for k, v in SPENDING.items()]
    return rows + [{"kind": "flow", "key": k, **v} for k, v in FLOWS.items()]
