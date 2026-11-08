"""Underwater options: what could close the monthly gap when the reality check fails.

    underwater_options(cid) -> {"gap_per_month", "cash_runs_out", "options": [card, ...]}
    check(cid, keys, extra_income=None) -> the reality check re-run with the picked options applied together

Each card is either modelled (applied to a copy of the planning context, then the deterministic reality check
re-runs: `closes_per_month` is solver-checked) or stated plainly (real, but the outcome depends on a lender or a
decision the data can't price: `closes_per_month` is None). The agent words and orders the cards; the figures
are all from here.
"""

from __future__ import annotations

import copy

from . import wealth

HARDSHIP_APR_PCT = 9.9  # typical card hardship-program rate
IDR_SHARE = 0.10  # income-driven repayment: 10% of discretionary income...
IDR_POVERTY_150 = 23_475.0  # ...above 150% of the poverty line (1 person)
SMALL_CUSHION_MONTHS = 1.0


def _idr_payment(ctx: dict) -> float:
    return round(max(IDR_SHARE * (ctx["flows"]["income"] * 12 - IDR_POVERTY_150) / 12, 0.0), 2)


def _lower_min(ctx: dict, pred, new_min) -> float:
    """Lower matching debts' minimums (and the payments made today); returns the monthly cash freed."""
    freed = 0.0
    for d in ctx["debts"]:
        if pred(d):
            nm = min(new_min(d), d["min_payment"])
            new_base = max(d["baseline_payment"] - (d["min_payment"] - nm), nm)
            freed += d["baseline_payment"] - new_base
            d["min_payment"], d["baseline_payment"] = nm, round(new_base, 2)
    f = ctx["flows"]
    f["debt_payments"] -= freed
    f["residual"] += freed
    ctx["current_debt_payments"] = round(ctx["current_debt_payments"] - freed, 2)
    return freed


def _pause_ira(ctx: dict, _amt=None):
    f = ctx["flows"]
    ira = f["contrib"].get("ira", 0.0)
    f["contrib"]["ira"] = 0.0
    f["invest_contrib"] -= ira
    f["residual"] += ira


def _idr(ctx: dict, _amt=None):
    pay = _idr_payment(ctx)
    _lower_min(ctx, lambda d: d["account_type"] == "student_loan", lambda d: pay)


def _card_min(balance: float, apr_pct: float) -> float:
    """The usual card minimum: the month's interest plus 1% of the balance (at least $25)."""
    return round(max(balance * apr_pct / 1200 + 0.01 * balance, min(25.0, balance)), 2)


def _hardship(ctx: dict, _amt=None):
    """A hardship rate cuts the interest and, with it, the minimum the card asks for each month."""
    cards = [d for d in ctx["debts"] if d["is_card"] and d["apr_pct"] > HARDSHIP_APR_PCT]
    for d in cards:
        d["apr_pct"] = HARDSHIP_APR_PCT
    _lower_min(ctx, lambda d: d in cards, lambda d: _card_min(d["balance"], HARDSHIP_APR_PCT))


def _applies(ctx: dict) -> dict[str, bool]:
    return {
        "pause_ira": ctx["flows"]["contrib"].get("ira", 0) > 1,
        "idr_student_loan": any(d["account_type"] == "student_loan" and d["min_payment"] > _idr_payment(ctx) + 1 for d in ctx["debts"]),
        "card_hardship": any(d["is_card"] and d["apr_pct"] > HARDSHIP_APR_PCT for d in ctx["debts"]),
        "smaller_cushion": ctx["keep_months"] > SMALL_CUSHION_MONTHS and ctx["assets"]["cash"] > SMALL_CUSHION_MONTHS * ctx["monthly_expenses"],
        "extra_income": True,
        "mortgage_help": any(d["account_type"] == "mortgage" for d in ctx["debts"]),
        "downsize": ctx["assets"]["property"] > 0,
        "ira_withdrawal": ctx["assets"]["ira"] > 0,
    }


# key -> (title, modelled apply fn or None, plain-language detail)
OPTIONS: dict[str, tuple[str, object, str]] = {
    "pause_ira": ("Pause IRA contributions", _pause_ira, "Stop the monthly IRA deposit until the gap closes."),
    "idr_student_loan": ("Income-driven repayment", _idr, "Ask the federal loan servicer for an income-driven plan."),
    "card_hardship": ("Card hardship program", _hardship, f"Ask each card issuer for a hardship rate (often about {HARDSHIP_APR_PCT}%)."),
    "smaller_cushion": ("Smaller cash cushion", "cushion", f"Keep {SMALL_CUSHION_MONTHS:g} month of expenses as cash and put the rest on the cards."),
    "extra_income": ("Earn more each month", None, "A room rental, extra hours or side work; hard to count on, so not modelled."),
    "mortgage_help": ("Ask the mortgage lender for help", None, "A loan modification, forbearance or refinance can lower the payment; the lender decides."),
    "downsize": ("Sell or downsize the home", None, "Frees the largest payment; a big life decision with selling costs."),
    "ira_withdrawal": ("Withdraw from the IRA", None, "Last resort: taxes plus a 10% penalty before 59½, and lost retirement growth."),
}


NEVER_RECOMMEND = {"extra_income", "ira_withdrawal", "downsize"}  # the customer's call, never pre-ticked
TAIL = ["mortgage_help", "downsize", "extra_income", "ira_withdrawal"]  # stated-plainly cards, in this order
NO_HELP = {
    "pause_ira": "the gap already assumes contributions are paused",
    "smaller_cushion": "cash is already draining each month, so there's none spare to put on the debt",
    "card_hardship": "the cards' minimums are already small next to the gap",
}


def _ctx_with(customer_id: str, keys: list[str], extra_income: float | None, base: dict | None = None) -> dict:
    ctx = wealth.context(customer_id, keep_months=SMALL_CUSHION_MONTHS) if "smaller_cushion" in keys else copy.deepcopy(base or wealth.context(customer_id))
    for k in keys:
        fn = OPTIONS.get(k, (None, None, None))[1]
        if callable(fn):
            fn(ctx, extra_income)
    return ctx


def _gap(rc: dict) -> float:
    return 0.0 if rc.get("realistic") else float(rc.get("gap_per_month") or 0)


def check(customer_id: str, keys: list[str], extra_income: float | None = None) -> dict:
    """The reality check with the picked options applied together: does the gap close?"""
    base_ctx = wealth.context(customer_id)
    before = wealth.reality_check(customer_id, ctx=copy.deepcopy(base_ctx))
    keys = [k for k in keys if k in OPTIONS]
    after = wealth.reality_check(customer_id, ctx=_ctx_with(customer_id, keys, extra_income, base_ctx))
    gb, ga = _gap(before), _gap(after)
    return {"picked": keys, "extra_income": extra_income, "gap_before": round(gb, 2), "gap_after": round(ga, 2),
            "closes": bool(after.get("realistic")), "cash_runs_out": after.get("cash_runs_out"),
            "principal_paydown": (after.get("monthly") or {}).get("principal_paydown"),
            "message": after.get("message"), "not_modelled": [k for k in keys if not OPTIONS[k][1]]}


def underwater_options(customer_id: str) -> dict:
    base_ctx = wealth.context(customer_id)
    before = wealth.reality_check(customer_id, ctx=copy.deepcopy(base_ctx))
    gap = _gap(before)
    ok = _applies(base_ctx)
    cards, not_helping = [], []
    for k, (title, fn, detail) in OPTIONS.items():
        if not ok.get(k):
            continue
        card = {"key": k, "title": title, "detail": detail, "modelled": bool(fn), "closes_per_month": None,
                "cash_runs_out": None, "never_recommend": k in NEVER_RECOMMEND}
        if fn:
            rc = wealth.reality_check(customer_id, ctx=_ctx_with(customer_id, [k], None, base_ctx))
            card["closes_per_month"] = round(gap - _gap(rc), 2)
            card["closes_gap"] = bool(rc.get("realistic"))
            card["cash_runs_out"] = rc.get("cash_runs_out")
            if card["closes_per_month"] < 1:  # helps nothing on its own: one line why, not a card
                not_helping.append({"key": k, "title": title, "why": NO_HELP.get(k, "closes none of the gap")})
                continue
        if k == "idr_student_loan":
            card["new_payment"] = _idr_payment(base_ctx)
        cards.append(card)
    cards.sort(key=lambda c: (not c["modelled"], TAIL.index(c["key"]) if c["key"] in TAIL else -1))
    return {"realistic": bool(before.get("realistic")), "gap_per_month": round(gap, 2),
            "cash_runs_out": before.get("cash_runs_out"), "options": cards, "not_helping": not_helping}
