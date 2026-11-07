"""Read-only access to PNC's synthetic dataset (CSV files in data/pnc/)."""

from __future__ import annotations

import csv
from collections import Counter, defaultdict
from functools import lru_cache
from typing import Any

from .config import PNC_DATA_DIR

NUMERIC = {
    "monthly_income", "monthly_non_debt_expenses", "credit_limit",
    "balance", "monthly_payment", "apr_pct", "opening_balance", "annual_yield_pct",
    "amount", "balance_change", "balance_after",
    "balance_increases", "balance_decreases", "closing_balance", "transaction_count",
}

LIABILITIES = {"credit_card", "auto_loan", "personal_loan", "mortgage"}
LIQUID_ASSETS = {"checking", "savings"}  # PNC excludes CDs and IRAs from liquid assets


def _read(name: str) -> list[dict[str, Any]]:
    with open(PNC_DATA_DIR / name, newline="") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        for k, v in r.items():
            if k in NUMERIC and v not in ("", None):
                r[k] = float(v) if k != "transaction_count" else int(float(v))
    return rows


@lru_cache(maxsize=None)
def profiles() -> list[dict]:
    return _read("profiles.csv")


@lru_cache(maxsize=None)
def accounts() -> list[dict]:
    return _read("accounts.csv")


@lru_cache(maxsize=None)
def transactions() -> list[dict]:
    rows = _read("transactions.csv")
    rows.sort(key=lambda r: (r["posted_date"], r["transaction_id"]))
    return rows


@lru_cache(maxsize=None)
def statements() -> list[dict]:
    return _read("monthly_statements.csv")


@lru_cache(maxsize=None)
def readme() -> str:
    return (PNC_DATA_DIR / "README.md").read_text()


def customer_ids() -> list[str]:
    return [p["customer_id"] for p in profiles()]


def profile(customer_id: str) -> dict | None:
    return next((p for p in profiles() if p["customer_id"] == customer_id), None)


def customer_accounts(customer_id: str) -> list[dict]:
    return [a for a in accounts() if a["customer_id"] == customer_id]


def customer_statements(customer_id: str) -> list[dict]:
    return [s for s in statements() if s["customer_id"] == customer_id]


def customer_transactions(
    customer_id: str,
    *,
    account_id: str | None = None,
    transaction_type: str | None = None,
    category: str | None = None,
    flow_class: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict]:
    out = []
    for t in transactions():
        if t["customer_id"] != customer_id:
            continue
        if account_id and t["account_id"] != account_id:
            continue
        if transaction_type and t["transaction_type"] != transaction_type:
            continue
        if category and t["category"] != category:
            continue
        if flow_class and t["flow_class"] != flow_class:
            continue
        if date_from and t["posted_date"] < date_from:
            continue
        if date_to and t["posted_date"] > date_to:
            continue
        out.append(t)
    return out


def card_account(customer_id: str) -> dict | None:
    return next((a for a in customer_accounts(customer_id) if a["account_type"] == "credit_card"), None)


def customer_summary(customer_id: str) -> dict:
    accs = customer_accounts(customer_id)
    assets = sum(a["balance"] for a in accs if a["account_type"] not in LIABILITIES)
    debts = sum(a["balance"] for a in accs if a["account_type"] in LIABILITIES)
    liquid = sum(a["balance"] for a in accs if a["account_type"] in LIQUID_ASSETS)
    card = card_account(customer_id)
    card_interest_year = sum(
        t["amount"]
        for t in customer_transactions(customer_id, account_id=card["account_id"], transaction_type="interest_accrual")
        if t["posted_date"] >= "2025-11-01"
    ) if card else 0.0
    return {
        "customer_id": customer_id,
        "profile": profile(customer_id),
        "accounts": accs,
        "totals": {
            "assets": round(assets, 2),
            "debts": round(debts, 2),
            "net_worth": round(assets - debts, 2),
            "liquid": round(liquid, 2),
            "card_balance": card["balance"] if card else 0.0,
            "card_apr_pct": card["apr_pct"] if card else 0.0,
            "card_interest_last_12m": round(card_interest_year, 2),
        },
    }


@lru_cache(maxsize=None)
def overview() -> dict:
    accs = accounts()
    txs = transactions()
    by_type: dict[str, dict] = defaultdict(lambda: {"count": 0, "total_balance": 0.0, "aprs": Counter(), "yields": Counter()})
    for a in accs:
        b = by_type[a["account_type"]]
        b["count"] += 1
        b["total_balance"] += a["balance"]
        if a["apr_pct"]:
            b["aprs"][a["apr_pct"]] += 1
        if a["annual_yield_pct"]:
            b["yields"][a["annual_yield_pct"]] += 1
    account_types = [
        {
            "account_type": k,
            "count": v["count"],
            "total_balance": round(v["total_balance"], 2),
            "avg_balance": round(v["total_balance"] / v["count"], 2),
            "apr_pct_values": dict(v["aprs"]),
            "yield_pct_values": dict(v["yields"]),
        }
        for k, v in sorted(by_type.items(), key=lambda kv: -kv[1]["count"])
    ]

    cat_totals: dict[tuple, float] = defaultdict(float)
    type_counts = Counter()
    flow_counts = Counter()
    for t in txs:
        cat_totals[(t["category"], t["flow_class"])] += t["amount"]
        type_counts[t["transaction_type"]] += 1
        flow_counts[t["flow_class"]] += 1
    categories = sorted(
        ({"category": c, "flow_class": f, "total": round(v, 2)} for (c, f), v in cat_totals.items()),
        key=lambda r: -r["total"],
    )

    # The 360° pattern: card debt at high APR while savings sit idle.
    pattern = []
    for cid in customer_ids():
        accs_c = customer_accounts(cid)
        card = next((a for a in accs_c if a["account_type"] == "credit_card"), None)
        sav = next((a for a in accs_c if a["account_type"] == "savings"), None)
        prof = profile(cid)
        if not card:
            continue
        interest = sum(
            t["amount"]
            for t in customer_transactions(cid, account_id=card["account_id"], transaction_type="interest_accrual")
            if t["posted_date"] >= "2025-11-01"
        )
        pattern.append({
            "customer_id": cid,
            "card_balance": card["balance"],
            "card_apr_pct": card["apr_pct"],
            "savings_balance": sav["balance"] if sav else 0.0,
            "savings_yield_pct": sav["annual_yield_pct"] if sav else 0.0,
            "monthly_expenses": prof["monthly_non_debt_expenses"] if prof else 0.0,
            "card_interest_last_12m": round(interest, 2),
            "could_pay_off_card_from_savings": bool(sav and sav["balance"] >= card["balance"]),
        })

    dates = [t["posted_date"] for t in txs]
    return {
        "counts": {
            "customers": len(profiles()),
            "accounts": len(accs),
            "transactions": len(txs),
            "statements": len(statements()),
        },
        "date_range": {"from": min(dates), "to": max(dates)},
        "account_types": account_types,
        "transaction_types": dict(type_counts),
        "flow_classes": dict(flow_counts),
        "categories": categories,
        "card_vs_savings": pattern,
    }
