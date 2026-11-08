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
import json
import sqlite3
from datetime import datetime, timezone

from . import wealth

HARDSHIP_APR_PCT = 9.9  # typical card hardship-program rate
IDR_SHARE = 0.10  # income-driven repayment: 10% of discretionary income...
IDR_POVERTY_150 = 23_475.0  # ...above 150% of the poverty line (1 person)
SMALL_CUSHION_MONTHS = 1.0

HOW = {  # what the customer does for each card (one line), and where to go when an official site exists
    "idr_student_loan": ("Apply for an income-driven repayment plan; it takes about 10 minutes with your FSA ID.",
                         "https://studentaid.gov/idr/"),
    "card_hardship": ("Call the number on the back of each card and ask for their hardship program.", None),
    "mortgage_help": ("Call your servicer and ask about a loan modification or forbearance; a HUD counselor is free.",
                      "https://www.consumerfinance.gov/find-a-housing-counselor/"),
    "pause_ira": ("Lower or pause the contribution at your IRA provider.", None),
    "downsize": ("Talk to a real-estate agent about selling costs before deciding.", None),
    "extra_income": ("Only count it once it's steady.", None),
    "ira_withdrawal": ("Last resort: ask your IRA provider about the taxes and 10% penalty first.",
                       "https://www.irs.gov/retirement-plans/hardships-early-withdrawals-and-loans"),
}


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


# Chat: a question about one of these offers "try it in my plan"; the button saves the option, then sends
# `message` as a chat edit, which re-checks the current plan with the option applied (no re-plan by the model).
CHAT_OPTIONS: dict[str, tuple[str, str, str]] = {  # key -> (question pattern, button label, chat-edit message)
    "idr_student_loan": (r"income.?(driven|based)|\bidr\b|\bibr\b|student.?loan", "Try IDR in my plan",
                         "Plan with income-driven repayment on my federal student loan"),
    "card_hardship": (r"hardship", "Try a hardship rate in my plan", "Plan with a card hardship rate"),
    "pause_ira": (r"\bira\b", "Try pausing my IRA in my plan", "Plan with my IRA contributions paused"),
}


def chat_option_request(customer_id: str, request: str) -> str | None:
    """The applied option a chat edit like "Plan with income-driven repayment..." asks to plan with, else None."""
    r = (request or "").strip().lower()
    for k, (_, _, msg) in CHAT_OPTIONS.items():
        if r == msg.lower() and k in applied(customer_id):
            return k
    return None


NEVER_RECOMMEND = {"extra_income", "ira_withdrawal", "downsize"}  # the customer's call, never pre-ticked
TAIL = ["mortgage_help", "downsize", "extra_income", "ira_withdrawal"]  # stated-plainly cards, in this order
NO_HELP = {
    "pause_ira": "the gap already assumes contributions are paused",
    "smaller_cushion": "cash is already draining each month, so there's none spare to put on the debt",
    "card_hardship": "the cards' minimums are already small next to the gap",
}


APPLYABLE = {k for k, (_, fn, _) in OPTIONS.items() if callable(fn)}  # options a plan can be built on


# ---- options the customer said they'll do: the planner builds on them (wealth.context applies them) ----

def _db() -> sqlite3.Connection:
    from ..data import store

    c = sqlite3.connect(store.DB_PATH)
    c.execute("create table if not exists applied_options (customer_id text primary key, keys json not null, at text not null)")
    return c


def applied(customer_id: str) -> list[str]:
    """The modelled options this customer chose to plan with (e.g. IDR, a hardship rate), else []."""
    try:
        with _db() as c:
            r = c.execute("select keys from applied_options where customer_id=?", (customer_id,)).fetchone()
    except sqlite3.Error:
        return []
    return [k for k in json.loads(r[0]) if k in APPLYABLE] if r else []


def save_applied(customer_id: str, keys: list[str]) -> list[str]:
    keys = [k for k in dict.fromkeys(keys) if k in APPLYABLE]
    with _db() as c:
        if keys:
            c.execute("insert or replace into applied_options values (?, ?, ?)",
                      (customer_id, json.dumps(keys), datetime.now(timezone.utc).isoformat(timespec="seconds")))
        else:
            c.execute("delete from applied_options where customer_id=?", (customer_id,))
    return keys


def apply_saved(ctx: dict, customer_id: str) -> None:
    """Apply the saved options to a fresh planning context (called by wealth.context)."""
    keys = applied(customer_id)
    for k in keys:
        OPTIONS[k][1](ctx, None)
    ctx["applied_options"] = keys


def applied_cards(customer_id: str) -> list[dict]:
    return [{"key": k, "title": OPTIONS[k][0], "how": HOW.get(k, (None, None))[0], "link": HOW.get(k, (None, None))[1]}
            for k in applied(customer_id)]


def _raw(customer_id: str, keep_months: float | None = None) -> dict:
    return wealth.context(customer_id, keep_months=keep_months, with_options=False)


def _ctx_with(customer_id: str, keys: list[str], extra_income: float | None, base: dict | None = None) -> dict:
    ctx = _raw(customer_id, SMALL_CUSHION_MONTHS) if "smaller_cushion" in keys else copy.deepcopy(base or _raw(customer_id))
    for k in keys:
        fn = OPTIONS.get(k, (None, None, None))[1]
        if callable(fn):
            fn(ctx, extra_income)
    return ctx


def _gap(rc: dict) -> float:
    return 0.0 if rc.get("realistic") else float(rc.get("gap_per_month") or 0)


def check(customer_id: str, keys: list[str], extra_income: float | None = None) -> dict:
    """The reality check with the picked options applied together: does the gap close?"""
    base_ctx = _raw(customer_id)
    before = wealth.reality_check(customer_id, ctx=copy.deepcopy(base_ctx))
    keys = [k for k in dict.fromkeys(keys) if k in OPTIONS]
    gb = _gap(before)
    # One option at a time, in the order given: what each adds on top of the ones before it (the gap bar).
    steps, prev, acc, after = [], gb, [], before
    for k in keys:
        acc.append(k)
        if OPTIONS[k][1]:
            after = wealth.reality_check(customer_id, ctx=_ctx_with(customer_id, acc, extra_income, base_ctx))
        g = _gap(after)
        steps.append({"key": k, "title": OPTIONS[k][0], "closes": round(max(prev - g, 0.0), 2), "gap_after": round(g, 2)})
        prev = g
    ga = _gap(after)
    return {"picked": keys, "extra_income": extra_income, "gap_before": round(gb, 2), "gap_after": round(ga, 2), "steps": steps,
            "closes": bool(after.get("realistic")), "cash_runs_out": after.get("cash_runs_out"),
            "principal_paydown": (after.get("monthly") or {}).get("principal_paydown"),
            "message": after.get("message"), "not_modelled": [k for k in keys if not OPTIONS[k][1]]}


def underwater_options(customer_id: str) -> dict:
    base_ctx = _raw(customer_id)
    before = wealth.reality_check(customer_id, ctx=copy.deepcopy(base_ctx))
    gap = _gap(before)
    ok = _applies(base_ctx)
    cards, not_helping = [], []
    for k, (title, fn, detail) in OPTIONS.items():
        if not ok.get(k):
            continue
        card = {"key": k, "title": title, "detail": detail, "modelled": bool(fn), "closes_per_month": None,
                "cash_runs_out": None, "never_recommend": k in NEVER_RECOMMEND,
                "runs": "manual" if k in HOW else "recorded", "how": HOW.get(k, (None, None))[0], "link": HOW.get(k, (None, None))[1]}
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
