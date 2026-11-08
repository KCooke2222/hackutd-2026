"""Linked external accounts: what PNC's data and Nessie can't hold.

PNC's dataset has checking, savings, CD, IRA, credit card, personal/auto loan and mortgage.
Nessie only has Checking / Savings / Credit Card accounts (+ home/auto/small-business loans).
Student loans, brokerage accounts, property and cards at other banks come from here, as if the
customer had linked them through an aggregator. Deterministic synthetic data per customer.

Each customer's linked accounts are written out in data/customers.yaml (`linked:`), next to the rest of
their spec; seeded_rows() is how those were first drawn and is the fallback for a customer the spec
doesn't list. The rows live in SQLite (store.linked_accounts). `make data` (and `make reseed`) load them:

    uv run python -m zeroday.linked      # load the spec's linked accounts into SQLite
"""

from __future__ import annotations

import random

from . import customer_spec, pnc_data

AS_OF = "2026-10-31"
DEBT_TYPES = {"credit_card", "personal_loan", "auto_loan", "mortgage", "student_loan"}
SECURED_TYPES = {"mortgage", "auto_loan"}

# What happens if you miss payments. 1 = worst. Minimums are paid in this order.
CONSEQUENCES = {
    1: "Secured: missed payments lead to foreclosure or repossession",
    2: "Federal student loan: wage garnishment and tax-refund offset; rarely discharged in bankruptcy",
    3: "Unsecured: late fees, penalty APR, credit-score damage, collections",
}


def consequence_tier(account_type: str, kind: str | None = None) -> int | None:
    if account_type not in DEBT_TYPES:
        return None
    if account_type in SECURED_TYPES:
        return 1
    if account_type == "student_loan" and kind == "federal":
        return 2
    return 3


FEDERAL_RATES = [4.53, 4.99, 5.5, 6.53]


def amortized_payment(balance: float, apr_pct: float, months: int) -> float:
    r = apr_pct / 100 / 12
    if r == 0:
        return round(balance / months, 2)
    return round(balance * r / (1 - (1 + r) ** -months), 2)


def _row(cid: str, n: int, account_type: str, kind: str, name: str, institution: str, balance: float, **kw) -> dict:
    return {
        "customer_id": cid,
        "account_id": f"L-{cid}-{account_type.upper()}-{n}",
        "account_type": account_type,
        "kind": kind,
        "name": name,
        "institution": institution,
        "balance": round(balance, 2),
        "apr_pct": kw.get("apr_pct", 0.0),
        "annual_yield_pct": kw.get("annual_yield_pct", 0.0),
        "min_payment": kw.get("min_payment", 0.0),
        "cost_basis": kw.get("cost_basis"),
        "secured": account_type in SECURED_TYPES,
        "consequence_tier": consequence_tier(account_type, kind),
        "secures_account_id": kw.get("secures_account_id"),
        "source": "linked",
        "as_of_date": AS_OF,
    }


def _student_loan(cid: str, rng: random.Random, balance: float | None = None, federal: bool | None = None) -> dict:
    federal = rng.random() < 0.7 if federal is None else federal
    balance = balance or round(rng.uniform(6_000, 42_000), -1)
    if federal:
        apr = rng.choice(FEDERAL_RATES)
        return _row(cid, 1, "student_loan", "federal", "Federal Direct Loan", "Dept. of Education (servicer)",
                    balance, apr_pct=apr, min_payment=amortized_payment(balance, apr, 120))
    apr = round(rng.uniform(6.5, 9.5), 2)
    return _row(cid, 1, "student_loan", "private", "Private student loan", "Linked lender",
                balance, apr_pct=apr, min_payment=amortized_payment(balance, apr, 120))


def _brokerage(cid: str, rng: random.Random, balance: float | None = None, gain: float | None = None) -> dict:
    balance = balance or round(rng.uniform(1_500, 40_000), -1)
    gain = gain if gain is not None else rng.uniform(0.02, 0.4)
    return _row(cid, 1, "brokerage", "taxable", "Brokerage (index funds)", "Linked brokerage",
                balance, cost_basis=round(balance * (1 - gain), 2))


def _card(cid: str, rng: random.Random, balance: float | None = None, apr: float | None = None) -> dict:
    apr = apr or round(rng.uniform(18, 29), 2)
    balance = balance or round(rng.uniform(300, 3_500), 2)
    minimum = max(35.0, round(balance * 0.01 + balance * apr / 1200, 2))
    return _row(cid, 2, "credit_card", "store", "Store card", "Other bank", balance, apr_pct=apr, min_payment=minimum)


def from_spec(cid: str, spec_rows: list[dict]) -> list[dict]:
    """Rows from a customer's `linked:` list in the spec."""
    rows = []
    for r in spec_rows:
        row = _row(cid, 0, r["type"], r["kind"], r["name"], r["institution"], r["balance"],
                   apr_pct=r.get("apr_pct", 0.0), min_payment=r.get("min_payment", 0.0),
                   cost_basis=r.get("cost_basis"), secures_account_id=r.get("secures"))
        row["account_id"] = r["id"]
        rows.append(row)
    return rows


def to_spec(rows: list[dict]) -> list[dict]:
    """The spec form of linked rows (inverse of from_spec)."""
    out = []
    for r in rows:
        s = {"id": r["account_id"], "type": r["account_type"], "kind": r["kind"], "name": r["name"],
             "institution": r["institution"], "balance": r["balance"]}
        for key in ("apr_pct", "min_payment", "cost_basis"):
            if r[key]:
                s[key] = r[key]
        if r["secures_account_id"]:
            s["secures"] = r["secures_account_id"]
        out.append(s)
    return out


def base_rows(cid: str) -> list[dict]:
    """Linked rows that don't depend on the ledger: the spec's, else the seeded draw."""
    spec = customer_spec.customer(cid)
    if spec is not None and "linked" in spec:
        return from_spec(cid, spec["linked"] or [])
    return seeded_rows(cid)


def seeded_rows(cid: str) -> list[dict]:
    """How the linked accounts were first drawn (deterministic per customer); data/customers.yaml froze these."""
    rng = random.Random(f"zeroday-linked-{cid}")
    accs = {a["account_type"]: a for a in pnc_data.customer_accounts(cid)}
    rows: list[dict] = []

    if cid == "C001":  # demo customer: one of everything, so the ladder and priority have something to show
        rows += [
            _student_loan(cid, rng, balance=18_400, federal=True),
            _brokerage(cid, rng, balance=9_800, gain=0.28),
            _card(cid, rng, balance=1_240.55, apr=27.24),
            _row(cid, 1, "property", "vehicle", "2019 Honda Civic", "Estimated value", 11_500),
        ]
        return rows

    if "mortgage" in accs:
        m = accs["mortgage"]
        value = round(m["balance"] / rng.uniform(0.45, 0.8), -3)
        rows.append(_row(cid, 1, "property", "home", "Home", "Estimated value", value, secures_account_id=m["account_id"]))
    if "auto_loan" in accs:
        a = accs["auto_loan"]
        value = round(a["balance"] / rng.uniform(0.6, 1.1), -2)
        rows.append(_row(cid, 2, "property", "vehicle", "Car", "Estimated value", value, secures_account_id=a["account_id"]))
    elif rng.random() < 0.5:
        rows.append(_row(cid, 2, "property", "vehicle", "Car (owned)", "Estimated value", round(rng.uniform(4_000, 15_000), -2)))
    if rng.random() < 0.55:
        rows.append(_student_loan(cid, rng))
    if rng.random() < 0.6:
        rows.append(_brokerage(cid, rng))
    if rng.random() < 0.35:
        rows.append(_card(cid, rng))
    return rows


def generate_customer(cid: str) -> list[dict]:
    return base_rows(cid)


def generate() -> list[dict]:
    return [r for cid in pnc_data.customer_ids() for r in generate_customer(cid)]


def load() -> int:
    """Regenerate every customer's linked accounts and replace them in SQLite."""
    from . import store

    return store.replace_linked_accounts(generate())


def for_customer(customer_id: str | None = None) -> list[dict]:
    """Linked accounts from SQLite (all customers if None). Read-only: if the table is empty (fresh clone,
    no `make linked` yet) it returns the deterministic generated rows without writing them."""
    from . import store

    if not store.linked_count():
        rows = generate() if customer_id is None else generate_customer(customer_id)
        return rows
    return store.linked_accounts(customer_id)


if __name__ == "__main__":
    print(f"loaded {load()} linked accounts into SQLite")
