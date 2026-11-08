"""One balance sheet per customer: PNC accounts (live Nessie balances where seeded) + linked accounts
from SQLite. On top of it: the liquidity ladder (what it costs to raise $X) and debt priority.

Pure math over plain dicts; the only I/O is in accounts().
"""

from __future__ import annotations

import time

from ..data import linked, pnc_data
from ..data.linked import CONSEQUENCES, DEBT_TYPES, SECURED_TYPES, consequence_tier

DEFAULT_EMERGENCY_MONTHS = 3.0
CD_PENALTY_MONTHS = 6          # early withdrawal: ~6 months of interest on a 1-year CD
LTCG_RATE = 0.15               # long-term capital gains on the gain portion
IRA_EARLY_COST = 0.10 + 0.22   # 10% penalty + ~22% income tax
PREPAY_HURDLE_PCT = 8.0        # below this APR, extra money beats prepaying (keep minimums only)

NAMES = {
    "checking": "Checking", "savings": "Savings", "cd": "CD", "ira": "IRA", "credit_card": "Credit card",
    "personal_loan": "Personal loan", "auto_loan": "Auto loan", "mortgage": "Mortgage",
}
NESSIE_TYPES = ("checking", "savings", "credit_card")

TIERS = [
    # tier, key, label, account types, proposable
    (1, "cash", "Cash above emergency buffer", {"checking", "savings"}, True),
    (2, "cd", "CDs (early-withdrawal penalty)", {"cd"}, True),
    (3, "brokerage", "Brokerage (capital-gains tax)", {"brokerage"}, True),
    (4, "retirement", "Retirement (10% penalty + income tax)", {"ira"}, False),
    (5, "property", "Property equity (illiquid)", {"property"}, False),
]

_cache: dict[str, tuple[float, list[dict]]] = {}
CACHE_SECONDS = 10


def _pnc_row(a: dict) -> dict:
    t = a["account_type"]
    return {
        "account_id": a["account_id"],
        "customer_id": a["customer_id"],
        "account_type": t,
        "kind": None,
        "name": f"PNC {NAMES.get(t, t)}",
        "institution": "PNC",
        "balance": round(a["balance"], 2),
        "apr_pct": a["apr_pct"],
        "annual_yield_pct": a["annual_yield_pct"],
        "min_payment": a["monthly_payment"],
        "cost_basis": None,
        "secured": t in SECURED_TYPES,
        "consequence_tier": consequence_tier(t),
        "secures_account_id": None,
        "source": "pnc",
        "as_of_date": a["as_of_date"],
    }


def _overlay_nessie(customer_id: str, rows: list[dict]) -> None:
    """Replace every balance with the live balance of its Nessie mirror (one GET for all of them).

    Nessie may round balances; the planner then works on Nessie's number, so the plan matches the bank."""
    from ..nessie import read

    if not read.enabled():
        return
    from ..data.seed import nessie_ids

    ids = nessie_ids(customer_id)
    mirrors = {k: m["id"] for k, m in (ids.get("accounts") or {}).items()}
    for r in rows:  # customers seeded before the full mirror: only the primary three
        if r["account_id"] not in mirrors and r["source"] == "pnc" and ids.get(r["account_type"]) \
                and r["account_type"] in NESSIE_TYPES:
            mirrors[r["account_id"]] = ids[r["account_type"]]
    if not mirrors:
        return
    try:
        live = {a["_id"]: a for a in read.customer_accounts(ids["customer"])} if ids.get("customer") else {}
    except Exception:  # noqa: BLE001 - Nessie down: fall back to our balances
        live = {}
    for r in rows:
        nid = mirrors.get(r["account_id"])
        if not nid:
            continue
        acc = live.get(nid)
        if acc is None:
            try:
                acc = read.account(nid)
            except Exception:  # noqa: BLE001
                r["nessie_error"] = True
                continue
        r["balance"] = round(float(acc["balance"]), 2)
        r["nessie_id"] = nid
        r["nessie_nickname"] = acc.get("nickname")
        r["source"] = "nessie" if r["source"] == "pnc" else r["source"]
        r["mirrored"] = True


def accounts(customer_id: str, live: bool = True) -> list[dict]:
    """Every account the customer has: PNC + linked external, each on its live Nessie mirror where seeded."""
    key = f"{customer_id}:{live}"
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_SECONDS:
        return [dict(r) for r in hit[1]]
    rows = [_pnc_row(a) for a in pnc_data.customer_accounts(customer_id)]
    rows += [dict(r) for r in linked.for_customer(customer_id)]
    if live:
        _overlay_nessie(customer_id, rows)
    for r in rows:
        r["side"] = "debt" if r["account_type"] in DEBT_TYPES else "asset"
    _cache[key] = (time.time(), rows)
    return [dict(r) for r in rows]


def clear_cache() -> None:
    _cache.clear()


# ---------- liquidity ladder ----------

def _tier_cost_pct(key: str, accs: list[dict]) -> float | None:
    """One-time cost of raising $1 from this tier, as % of the amount raised."""
    total = sum(a["balance"] for a in accs)
    if key == "cash":
        return 0.0
    if key in ("cd", "brokerage") and not total:
        return None
    if key == "cd":
        return round(sum(a["balance"] * a["annual_yield_pct"] for a in accs) / total * CD_PENALTY_MONTHS / 12, 3)
    if key == "brokerage":
        gain = sum(a["balance"] - (a["cost_basis"] if a["cost_basis"] is not None else a["balance"]) for a in accs)
        return round(max(gain, 0) / total * LTCG_RATE * 100, 3)
    if key == "retirement":
        return round(IRA_EARLY_COST * 100, 3)
    return None


def ladder(rows: list[dict], monthly_expenses: float, keep_months: float = DEFAULT_EMERGENCY_MONTHS) -> dict:
    """Assets ordered by how cheaply they turn into cash. Tiers 1-3 may be proposed; 4-5 are flag-only."""
    buffer = round(keep_months * monthly_expenses, 2)
    debts_by_id = {r["account_id"]: r for r in rows if r["side"] == "debt"}
    tiers = []
    for n, key, label, types, proposable in TIERS:
        accs = [r for r in rows if r["side"] == "asset" and r["account_type"] in types]
        balance = round(sum(a["balance"] for a in accs), 2)
        note = None
        if key == "cash":
            available = max(balance - buffer, 0.0)
            note = f"keeps {keep_months:g} months of expenses ({buffer:,.0f}) untouched"
        elif key == "property":
            equity = 0.0
            for a in accs:
                owed = debts_by_id.get(a.get("secures_account_id") or "", {}).get("balance", 0.0)
                equity += max(a["balance"] - owed, 0.0)
            available = equity
            note = "mention only: selling or borrowing against property is not a payoff plan"
        else:
            available = balance
            if key == "retirement":
                note = "flag only: never proposed"
        tiers.append({
            "tier": n,
            "key": key,
            "label": label,
            "proposable": proposable,
            "balance": balance,
            "available": round(available, 2),
            "cost_pct": _tier_cost_pct(key, accs),
            "note": note,
            "accounts": [{"account_id": a["account_id"], "name": a["name"], "balance": a["balance"], "source": a["source"]} for a in accs],
        })
    return {
        "keep_months": keep_months,
        "monthly_expenses": monthly_expenses,
        "emergency_buffer": buffer,
        "tiers": tiers,
        "proposable_available": round(sum(t["available"] for t in tiers if t["proposable"]), 2),
    }


def tap(lad: dict, amount: float) -> dict:
    """Cheapest way to raise `amount` from the proposable tiers (1 -> 3), with the one-time cost of each draw."""
    if amount < 0:
        raise ValueError("amount must be non-negative")
    left = amount
    draws = []
    for t in lad["tiers"]:
        if not t["proposable"] or left <= 0.005:
            continue
        take = min(left, t["available"])
        if take <= 0:
            continue
        draws.append({"tier": t["tier"], "key": t["key"], "amount": round(take, 2), "cost": round(take * (t["cost_pct"] or 0) / 100, 2)})
        left -= take
    return {
        "amount": round(amount, 2),
        "draws": draws,
        "total_cost": round(sum(d["cost"] for d in draws), 2),
        "shortfall": round(max(left, 0.0), 2),
    }


def draw_costs(lad: dict, draws: dict[str, float]) -> list[dict]:
    """Cost of specific draws by tier key ({'cash': 500, 'brokerage': 2000}). Raises if a draw exceeds what's available."""
    by_key = {t["key"]: t for t in lad["tiers"]}
    out = []
    for key, amount in draws.items():
        if not amount:
            continue
        t = by_key[key]
        if amount < 0:
            raise ValueError(f"{key}: amount must be non-negative")
        if not t["proposable"]:
            raise ValueError(f"{t['label']} can't be used in a plan")
        if amount > t["available"] + 0.005:
            raise ValueError(f"{t['label']}: {amount:,.2f} requested but only {t['available']:,.2f} available")
        out.append({"tier": t["tier"], "key": key, "amount": round(amount, 2), "cost": round(amount * (t["cost_pct"] or 0) / 100, 2)})
    return out


# ---------- debt priority ----------

def debt_priority(rows: list[dict], monthly_income: float, monthly_expenses: float) -> dict:
    """Minimums first, ordered by consequence of missing them; extra money to the highest APR (avalanche),
    except debts below the prepay hurdle and secured debt, which only get their minimum."""
    debts = [dict(r) for r in rows if r["side"] == "debt" and r["balance"] > 0]
    debts.sort(key=lambda d: (d["consequence_tier"] or 3, -d["apr_pct"]))
    extra = [d for d in debts if not d["secured"] and d["apr_pct"] >= PREPAY_HURDLE_PCT]
    extra.sort(key=lambda d: (-d["apr_pct"], d["balance"]))
    extra_rank = {d["account_id"]: i + 1 for i, d in enumerate(extra)}
    out = []
    for i, d in enumerate(debts, 1):
        rank = extra_rank.get(d["account_id"])
        if rank:
            action = f"Extra payments #{rank}" if rank > 1 else "Extra payments go here first"
        elif d["secured"]:
            action = f"Minimum only: secured at {d['apr_pct']:g}%, never prepay before high-APR debt"
        else:
            action = f"Minimum only: {d['apr_pct']:g}% is below the {PREPAY_HURDLE_PCT:g}% prepay hurdle"
        out.append({
            "account_id": d["account_id"],
            "name": d["name"],
            "account_type": d["account_type"],
            "kind": d.get("kind"),
            "source": d["source"],
            "balance": d["balance"],
            "apr_pct": d["apr_pct"],
            "min_payment": d["min_payment"],
            "secured": d["secured"],
            "consequence_tier": d["consequence_tier"],
            "consequence": CONSEQUENCES.get(d["consequence_tier"] or 3),
            "minimum_order": i,
            "extra_rank": rank,
            "action": action,
            "yearly_interest": round(d["balance"] * d["apr_pct"] / 100, 2),
        })
    minimums = round(sum(d["min_payment"] for d in out), 2)
    return {
        "debts": out,
        "monthly_minimums": minimums,
        "monthly_income": monthly_income,
        "monthly_expenses": monthly_expenses,
        "monthly_surplus": round(monthly_income - monthly_expenses - minimums, 2),
        "prepay_hurdle_pct": PREPAY_HURDLE_PCT,
        "rule": "Pay every minimum (worst consequence first), then put extra on the highest APR.",
    }


# ---------- the whole picture ----------

def totals(rows: list[dict]) -> dict:
    assets = sum(r["balance"] for r in rows if r["side"] == "asset")
    debts = sum(r["balance"] for r in rows if r["side"] == "debt")
    liquid = sum(r["balance"] for r in rows if r["account_type"] in {"checking", "savings"})
    return {
        "assets": round(assets, 2),
        "debts": round(debts, 2),
        "net_worth": round(assets - debts, 2),
        "liquid": round(liquid, 2),
        "by_source": {
            s: round(sum(r["balance"] * (1 if r["side"] == "asset" else -1) for r in rows if r["source"] == s), 2)
            for s in sorted({r["source"] for r in rows})
        },
    }


def balance_sheet(customer_id: str, keep_months: float = DEFAULT_EMERGENCY_MONTHS, live: bool = True) -> dict:
    prof = pnc_data.profile(customer_id) or {}
    income = prof.get("monthly_income", 0.0)
    expenses = prof.get("monthly_non_debt_expenses", 0.0)
    rows = accounts(customer_id, live=live)
    return {
        "customer_id": customer_id,
        "profile": prof,
        "accounts": rows,
        "totals": totals(rows),
        "ladder": ladder(rows, expenses, keep_months),
        "priority": debt_priority(rows, income, expenses),
    }
