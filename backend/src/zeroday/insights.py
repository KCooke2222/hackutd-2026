"""Cash-flow insights over PNC history + linked accounts: where the money goes each month.

Accounting (so the buckets add up): cash accounts (checking + savings) are the household wallet.
- income: payroll into checking or savings.
- essentials / discretionary: spending from cash accounts AND card purchases, by category.
- debt_payments: payments to all debts minus new card purchases (those are already counted as spending),
  i.e. interest + principal actually retired. Negative when the card grew that month.
- savings_invest: contributions from cash to CDs / IRA (moves between checking and savings net to zero).
- transfers_out_unknown: net money sent to unlisted external accounts.
- surplus = income - all of the above = the change in cash that month.

Read-only. Pure functions over pnc_data + linked; no network.
"""

from __future__ import annotations

import statistics
from collections import defaultdict

from . import linked, pnc_data
from .finance import ESSENTIAL_CATEGORIES

CASH = {"checking", "savings"}
BUCKETS = ("income", "essentials", "discretionary", "debt_payments", "savings_invest", "transfers_out_unknown", "surplus")
# PNC's descriptions are generic; they name no merchant.
GENERIC_DESCRIPTIONS = {"purchase", "payment", "withdrawal", "deposit", "fee", "paycheck", "interest accrual"}


def _r(x: float) -> float:
    return round(x + 0.0, 2)


def _months(customer_id: str, months: int) -> tuple[list[dict], list[str]]:
    rows = pnc_data.customer_transactions(customer_id)
    all_months = sorted({t["posted_date"][:7] for t in rows})
    keep = all_months[-months:] if months > 0 else all_months
    ks = set(keep)
    return [t for t in rows if t["posted_date"][:7] in ks], keep


def _is_card_purchase(t: dict) -> bool:
    return t["account_type"] == "credit_card" and t["flow_class"] == "expense"


def _is_spending(t: dict) -> bool:
    return (t["account_type"] in CASH and t["flow_class"] == "expense") or _is_card_purchase(t)


def _spend_amount(t: dict) -> float:
    # Cash spending lowers the balance; card purchases raise the card balance. Both are money spent.
    return abs(t["balance_change"]) if t["balance_change"] else abs(t["amount"])


def _linked_minimums(customer_id: str) -> list[dict]:
    """Linked debts (student loans, other cards) have minimums but no payment history in PNC's data."""
    return [
        {"account_id": a["account_id"], "name": a["name"], "min_payment": a["min_payment"]}
        for a in linked.for_customer(customer_id)
        if a["account_type"] in linked.DEBT_TYPES and a["balance"] > 0 and a["min_payment"]
    ]


def cash_flow(customer_id: str, months: int = 12) -> dict:
    rows, keep = _months(customer_id, months)
    per = {m: defaultdict(float) for m in keep}
    ess_cat: dict[str, float] = defaultdict(float)
    card_cat: dict[str, float] = defaultdict(float)
    for t in rows:
        m = per[t["posted_date"][:7]]
        ch = t["balance_change"]
        if _is_spending(t):
            amt = _spend_amount(t)
            essential = t["category"] in ESSENTIAL_CATEGORIES
            m["essentials" if essential else "discretionary"] += amt
            if essential:
                ess_cat[t["category"]] += amt
            if _is_card_purchase(t):
                card_cat[t["category"]] += amt
                m["_card_purchases"] += amt
            continue
        if t["account_type"] not in CASH:
            continue  # loan/IRA/CD sides and non-cash accruals: counted from the cash side
        fc, cat = t["flow_class"], t["category"]
        if fc == "income":
            m["income"] += ch
        elif fc == "internal_transfer" and cat == "debt_payment":
            m["_debt_paid"] += -ch
        elif fc == "internal_transfer" and cat == "investment_contribution":
            m["savings_invest"] += -ch
        elif fc == "external_transfer":
            m["transfers_out_unknown"] += -ch
        elif fc == "internal_transfer":
            m["_internal_cash"] += ch  # checking <-> savings, nets to ~0
        elif fc == "non_cash":
            pass
        else:
            m["_other"] += ch

    out_months = []
    for k in keep:
        m = per[k]
        m["debt_payments"] = m["_debt_paid"] - m["_card_purchases"]
        m["surplus"] = (m["income"] - m["essentials"] - m["discretionary"] - m["debt_payments"]
                        - m["savings_invest"] - m["transfers_out_unknown"] + m["_internal_cash"] + m["_other"])
        out_months.append({"month": k, **{b: _r(m[b]) for b in BUCKETS},
                           "debt_payments_gross": _r(m["_debt_paid"]), "card_purchases": _r(m["_card_purchases"])})
    n = len(keep) or 1
    avg = {b: _r(sum(x[b] for x in out_months) / n) for b in BUCKETS + ("debt_payments_gross", "card_purchases")}
    lmins = _linked_minimums(customer_id)
    lsum = _r(sum(x["min_payment"] for x in lmins))
    return {
        "customer_id": customer_id,
        "period": {"from": keep[0] if keep else None, "to": keep[-1] if keep else None, "months": len(keep)},
        "months": out_months,
        "monthly_avg": avg,
        "unknown_outflow_per_month": avg["transfers_out_unknown"],
        "essentials_by_category": {c: _r(v / n) for c, v in sorted(ess_cat.items(), key=lambda kv: -kv[1])},
        "card_spending_by_category": {c: _r(v / n) for c, v in sorted(card_cat.items(), key=lambda kv: -kv[1])},
        "linked_debt_minimums": lmins,
        "linked_debt_minimums_per_month": lsum,
        "notes": [
            "debt_payments = payments to debts minus new card purchases (card purchases are counted as spending)",
            "linked debt minimums are not in PNC's payment history; they are likely paid from the unknown outflow",
        ] if lmins else [
            "debt_payments = payments to debts minus new card purchases (card purchases are counted as spending)",
        ],
    }


def _p25(xs: list[float]) -> float:
    if len(xs) < 2:
        return xs[0] if xs else 0.0
    return statistics.quantiles(xs, n=4, method="inclusive")[0]


def spending_detail(customer_id: str, category: str, months: int = 12) -> dict:
    rows, keep = _months(customer_id, months)
    monthly = {k: 0.0 for k in keep}
    by_desc: dict[str, list[float]] = defaultdict(list)
    accounts: dict[str, float] = defaultdict(float)
    for t in rows:
        if not _is_spending(t) or t["category"] != category:
            continue
        amt = _spend_amount(t)
        monthly[t["posted_date"][:7]] += amt
        by_desc[t["description"]].append(amt)
        accounts[t["account_type"]] += amt
    vals = [monthly[k] for k in keep]
    n = len(keep) or 1
    merchants = [
        {"name": d, "monthly_avg": _r(sum(a) / n), "count_per_month": round(len(a) / n, 2)}
        for d, a in sorted(by_desc.items(), key=lambda kv: -sum(kv[1]))
        if d.strip().lower() not in GENERIC_DESCRIPTIONS
    ]
    min_month = min(keep, key=lambda k: monthly[k]) if keep and any(vals) else None
    rec = [r for r in find_recurring(customer_id, months) if r["category"] == category]
    return {
        "customer_id": customer_id,
        "category": category,
        "essential": category in ESSENTIAL_CATEGORIES,
        "avg": _r(sum(vals) / n),
        "p25": _r(_p25(vals)),
        "min": _r(min(vals)) if vals else 0.0,
        "min_month": min_month,
        "max": _r(max(vals)) if vals else 0.0,
        "monthly": [{"month": k, "amount": _r(monthly[k])} for k in keep],
        "by_account": {a: _r(v / n) for a, v in accounts.items()},
        "merchants": merchants,
        "merchant_detail_available": bool(merchants),
        "recurring": [{"name": r["name"], "amount": r["amount"], "day": r["day"]} for r in rec],
    }


def _kind(t: dict) -> str | None:
    if _is_spending(t):
        return "spending"
    if t["account_type"] not in CASH or t["balance_change"] >= 0:
        return None
    fc, cat = t["flow_class"], t["category"]
    if cat == "debt_payment":
        return "debt_payment"
    if cat == "investment_contribution":
        return "saving"
    if fc == "external_transfer":
        return "transfer_out"
    return None  # internal checking -> savings moves


def find_recurring(customer_id: str, months: int = 12) -> list[dict]:
    """Outflows that show up in most months (bills, subscriptions, loan payments, transfers)."""
    rows, keep = _months(customer_id, months)
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for t in rows:
        k = _kind(t)
        if k:
            groups[(t["account_type"], t["category"], t["description"], k)].append(t)
    need = max(2, int(len(keep) * 0.75))
    out = []
    for (acct, cat, desc, kind), ts in groups.items():
        by_month: dict[str, float] = defaultdict(float)
        for t in ts:
            by_month[t["posted_date"][:7]] += _spend_amount(t) if kind == "spending" else -t["balance_change"]
        if len(by_month) < need:
            continue
        amts = list(by_month.values())
        med = statistics.median(amts)
        cv = statistics.pstdev(amts) / med if med else 0.0
        generic = desc.strip().lower() in GENERIC_DESCRIPTIONS
        clean = desc.removeprefix("Synthetic ").strip()
        label = cat.replace("_", " ").capitalize() if generic else clean[:1].upper() + clean[1:]
        out.append({
            "name": label,
            "amount": _r(med),
            "cadence": "monthly",
            "months_seen": len(by_month),
            "day": int(statistics.median(int(t["posted_date"][8:10]) for t in ts)),
            "amount_varies_pct": round(cv * 100, 1),
            "account": acct,
            "category": cat,
            "kind": kind,
            "description": desc,
            "seen_in_history": True,
        })
    for d in _linked_minimums(customer_id):
        out.append({
            "name": f"{d['name']} minimum", "amount": d["min_payment"], "cadence": "monthly", "months_seen": 0,
            "day": None, "amount_varies_pct": 0.0, "account": d["account_id"], "category": "debt_payment",
            "kind": "debt_payment", "description": "linked account minimum (not in PNC history)",
            "seen_in_history": False,
        })
    out.sort(key=lambda r: -r["amount"])
    return out
