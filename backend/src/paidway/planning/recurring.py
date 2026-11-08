"""The customer's recurring outflows as Nessie recurring bills (the baseline), and what a plan changes in them.

Seeding (data/seed.py) creates one recurring bill on the checking mirror for each baseline row: every debt
payment, every IRA / brokerage / CD contribution, and the fixed bills (housing, utilities, phone, insurance,
subscriptions). Variable spending (groceries, dining, travel...) stays card purchases with agent-watched targets.

A plan is then a diff against that list: keep / change (amount) / pause (resumable) / end (debt paid off) /
new. Approve sends only the diff (agent/execute.py). Pure: reads our own data, sends nothing.
"""

from __future__ import annotations

import re

from ..data import categories, linked, pnc_data
from . import balance_sheet

FIXED_BILLS = ("housing", "utilities", "phone_internet", "insurance", "subscriptions")
CONTRIB_FLOW = {"ira": "ira_contribution", "brokerage": "brokerage_auto_invest", "cd": "cd_contribution"}
CONTRIB_NAME = {"ira": "IRA contribution", "brokerage": "Brokerage auto-invest", "cd": "CD contribution"}
KIND_ORDER = {"debt": 0, "contribution": 1, "bill": 2, "subscription": 3}
NICK = "Paidway: "


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:40]


def _day(d: float | int | None, default: int = 28) -> int:
    """Day of month for a recurring bill, clamped to 1..28 so it exists every month."""
    return max(1, min(int(d or default), 28))


def current_payments(customer_id: str, months: int = 3, *, with_day: bool = False) -> dict:
    """What the customer already pays each debt per month (average of the last `months` months of
    debt_payment rows): PNC debts from their own payment rows, linked debts from checking rows named after
    the lender. with_day: {account_id: (amount, day)} (median posting day)."""
    import statistics

    txs = pnc_data.customer_transactions(customer_id, category="debt_payment")
    keys = sorted({t["posted_date"][:7] for t in txs})[-months:]
    win = [t for t in txs if t["posted_date"][:7] in keys and t["balance_change"] < 0]
    n = max(len(keys), 1)
    out: dict[str, float] = {}
    days: dict[str, list[int]] = {}
    for t in win:
        if t["account_type"] != "checking":
            out[t["account_id"]] = out.get(t["account_id"], 0.0) - t["balance_change"]
            days.setdefault(t["account_id"], []).append(int(t["posted_date"][8:10]))
    for r in linked.for_customer(customer_id):
        if r["account_type"] not in balance_sheet.DEBT_TYPES:
            continue
        words = {r["account_type"].replace("_", " "), f"{r.get('kind') or ''} card".strip()}
        hits = [t for t in win if t["account_type"] == "checking" and not t.get("transfer_id")
                and (r.get("institution") or "~") in t["description"]
                and any(w and f"({w})" in t["description"] for w in words)]
        paid = sum(-t["balance_change"] for t in hits)
        if paid:
            out[r["account_id"]] = paid
            days[r["account_id"]] = [int(t["posted_date"][8:10]) for t in hits]
    if with_day:
        return {k: (round(v / n, 2), _day(statistics.median(days.get(k) or [28]))) for k, v in out.items()}
    return {k: round(v / n, 2) for k, v in out.items()}


def _asset_account(rows: list[dict], dest: str) -> str | None:
    accs = [r for r in rows if r["account_type"] == dest and r["side"] == "asset"]
    return max(accs, key=lambda r: float(r["balance"] or 0))["account_id"] if accs else None


def baseline(customer_id: str) -> list[dict]:
    """Today's recurring outflows, one row per future Nessie bill:
    {key, kind (debt | contribution | bill | subscription), name, payee, amount (whole $), day, to (our account
    id it pays into, or None for an outside payee), category, flow (contributions)}."""
    from . import insights, wealth

    rows = balance_sheet.accounts(customer_id, live=False)
    debts = balance_sheet.debt_priority(rows, 0, 0)["debts"]
    pay = current_payments(customer_id, with_day=True)
    rec = insights.find_recurring(customer_id)
    out: list[dict] = []
    for d in debts:
        amt, day = pay.get(d["account_id"]) or (d["min_payment"], 28)
        if round(amt) < 1:
            continue
        out.append({"key": f"debt:{d['account_id']}", "kind": "debt", "name": d["name"], "payee": d["name"],
                    "amount": round(amt), "day": day, "to": d["account_id"], "category": "debt_payment"})
    contrib = wealth._flows(customer_id)["contrib"]
    for dest, amt in contrib.items():
        if round(amt) < 1:
            continue
        r = next((x for x in rec if x["kind"] == "saving" and dest in x["description"].lower()), None)
        acc = _asset_account(rows, dest)
        out.append({"key": f"contrib:{dest}", "kind": "contribution", "name": CONTRIB_NAME[dest],
                    "payee": "IRA" if dest == "ira" else "Brokerage" if dest == "brokerage" else "CD",
                    "amount": round(amt), "day": _day((r or {}).get("day"), 5), "to": acc,
                    "category": "investment_contribution", "flow": CONTRIB_FLOW[dest], "dest": dest})
    seen = set()
    for r in rec:
        if r["kind"] != "spending" or r["account"] != "checking" or r["category"] not in FIXED_BILLS:
            continue
        key = f"bill:{_slug(r['name'])}"
        if key in seen or round(r["amount"]) < 1:
            continue
        seen.add(key)
        out.append({"key": key, "kind": "subscription" if r["category"] == "subscriptions" else "bill",
                    "name": r["name"], "payee": r["name"], "amount": round(r["amount"]), "day": _day(r["day"]),
                    "to": None, "category": r["category"]})
    out.sort(key=lambda x: (KIND_ORDER[x["kind"]], -x["amount"]))
    return out


def label(kind: str) -> str:
    return {"debt": "Debt payment", "contribution": "Contribution", "bill": "Bill", "subscription": "Subscription"}[kind]


def flow_key(dest: str) -> str:
    return f"contrib:{dest}"


def contrib_dest(flow: str) -> str | None:
    return (categories.FLOWS.get(flow) or {}).get("dest")


def in_nessie(customer_id: str) -> dict:
    """The "Recurring in Nessie" list: the bill map seed/approve keep (data/seed.py), else the baseline it would
    create. Each row: {key, kind, name, amount, day, to, nessie_id, status (active | paused), resume}."""
    from ..data.seed import nessie_ids

    ids = nessie_ids(customer_id)
    have, paused = ids.get("bills") or {}, ids.get("paused") or {}
    base = {r["key"]: r for r in baseline(customer_id)}
    rows = []
    if have or paused:
        for k, b in have.items():
            rows.append({"key": k, "kind": b.get("kind") or "bill", "name": b.get("name") or b.get("payee"),
                         "amount": b.get("amount"), "day": b.get("recurring_date"), "to": b.get("to"),
                         "nessie_id": b.get("id"), "status": "active"})
        for k, b in paused.items():
            rows.append({"key": k, "kind": b.get("kind") or "contribution", "name": b.get("name") or b.get("payee"),
                         "amount": b.get("amount"), "day": b.get("recurring_date"), "to": b.get("to"),
                         "nessie_id": None, "status": "paused", "resume": b.get("resume")})
    else:
        rows = [{**{k: r[k] for k in ("key", "kind", "name", "amount", "day", "to")}, "nessie_id": None,
                 "status": "active"} for r in base.values()]
    rows.sort(key=lambda x: (KIND_ORDER.get(x["kind"], 9), x["status"] != "active", -(x["amount"] or 0)))
    return {"customer_id": customer_id, "seeded": bool(have or paused), "bills": rows,
            "total": sum(r["amount"] or 0 for r in rows if r["status"] == "active")}
