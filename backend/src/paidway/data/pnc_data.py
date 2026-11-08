"""Read-only access to PNC's synthetic dataset.

The CSVs in data/pnc/ are the source files; they are imported into the app's SQLite DB (tables profiles,
accounts, transactions, monthly_statements) by `python -m paidway.data.seed`, and re-imported automatically
when a table is missing or its CSV changed. Rows come back exactly as the CSV reader gave them before:
numeric columns as float (transaction_count int), everything else str, empty as "".
"""

from __future__ import annotations

import csv
import sqlite3
import threading
from functools import lru_cache
from typing import Any

from ..config import PNC_DATA_DIR

NUMERIC = {
    "monthly_income", "monthly_non_debt_expenses", "credit_limit",
    "balance", "monthly_payment", "apr_pct", "opening_balance", "annual_yield_pct",
    "amount", "balance_change", "balance_after",
    "balance_increases", "balance_decreases", "closing_balance", "transaction_count",
}

LIABILITIES = {"credit_card", "auto_loan", "personal_loan", "mortgage"}
LIQUID_ASSETS = {"checking", "savings"}  # PNC excludes CDs and IRAs from liquid assets


# table -> (csv file, create statement, indexes). Column order matches the CSV header.
TABLES: dict[str, tuple[str, str, list[str]]] = {
    "profiles": ("profiles.csv", """create table profiles (
  customer_id text primary key,
  monthly_income real not null,
  monthly_non_debt_expenses real not null,
  credit_limit real not null
)""", []),
    "accounts": ("accounts.csv", """create table accounts (
  customer_id text not null references profiles (customer_id),
  account_type text not null,   -- checking | savings | cd | ira | credit_card | auto_loan | personal_loan | mortgage
  balance real not null,
  monthly_payment real not null,
  apr_pct real not null,
  account_id text primary key,
  opening_balance real not null,
  history_start text not null,
  as_of_date text not null,
  annual_yield_pct real not null
)""", ["create index accounts_customer on accounts (customer_id)"]),
    "transactions": ("transactions.csv", """create table transactions (
  transaction_id text primary key,
  customer_id text not null references profiles (customer_id),
  account_id text not null references accounts (account_id),
  account_type text not null,
  posted_date text not null,
  transaction_type text not null,
  category text not null,
  description text not null,
  amount real not null,         -- positive USD
  balance_change real not null, -- signed change to this account
  balance_after real not null,
  flow_class text not null,     -- income | expense | internal_transfer | external_transfer | non_cash
  transfer_id text              -- shared by the two legs of an internal transfer
)""", ["create index transactions_customer_date on transactions (customer_id, posted_date)",
       "create index transactions_account on transactions (account_id)"]),
    "monthly_statements": ("monthly_statements.csv", """create table monthly_statements (
  customer_id text not null references profiles (customer_id),
  account_id text not null references accounts (account_id),
  account_type text not null,
  month text not null,
  opening_balance real not null,
  balance_increases real not null,
  balance_decreases real not null,
  closing_balance real not null,
  transaction_count integer not null,
  primary key (account_id, month)
)""", ["create index monthly_statements_customer on monthly_statements (customer_id)"]),
}
CSV_TABLE = {f: t for t, (f, _, _) in TABLES.items()}


def _csv_rows(name: str) -> list[dict[str, Any]]:
    with open(PNC_DATA_DIR / name, newline="") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        for k, v in r.items():
            if k in NUMERIC and v not in ("", None):
                r[k] = float(v) if k != "transaction_count" else int(float(v))
    return rows


def _stamp(name: str) -> str:
    st = (PNC_DATA_DIR / name).stat()
    return f"{(PNC_DATA_DIR / name).resolve()}:{st.st_size}:{st.st_mtime_ns}"


def _connect() -> sqlite3.Connection:
    from . import store  # store.DB_PATH, so tests that point it at a temp file get their own copy

    store.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(store.DB_PATH)
    c.execute("create table if not exists pnc_import (table_name text primary key, source text not null, rows integer not null)")
    return c


def _import(c: sqlite3.Connection, table: str) -> int:
    name, create, indexes = TABLES[table]
    rows = _csv_rows(name)
    cols = list(rows[0]) if rows else []
    with c:
        c.execute(f"drop table if exists {table}")
        c.execute(create)
        for ix in indexes:
            c.execute(ix)
        c.executemany(
            f"insert into {table} ({', '.join(cols)}) values ({', '.join('?' * len(cols))})",
            ([None if r[k] == "" else r[k] for k in cols] for r in rows),
        )
        c.execute("insert or replace into pnc_import values (?,?,?)", (table, _stamp(name), len(rows)))
    return len(rows)


_LOAD_LOCK = threading.Lock()


def load_into_db(force: bool = False) -> dict[str, int]:
    """Import the PNC CSVs into SQLite (drop and reload). Without force, only tables that are missing or stale.
    Locked: concurrent first requests after a deploy would otherwise both drop/create the same tables."""
    with _LOAD_LOCK:
        return _load_into_db(force)


def _load_into_db(force: bool) -> dict[str, int]:
    c = _connect()
    try:
        have = dict(c.execute("select table_name, source from pnc_import"))
        out = {}
        for table, (name, _, _) in TABLES.items():
            if force or have.get(table) != _stamp(name):
                out[table] = _import(c, table)
        return out
    finally:
        c.close()


def _read(name: str) -> list[dict[str, Any]]:
    table = CSV_TABLE[name]
    load_into_db()
    c = _connect()
    try:
        cur = c.execute(f"select * from {table} order by rowid")
        cols = [d[0] for d in cur.description]
        rows = [{k: ("" if v is None else v) for k, v in zip(cols, r)} for r in cur]
    finally:
        c.close()
    for r in rows:  # SQLite keeps 2.0 as real, but make the old CSV typing explicit
        for k in NUMERIC.intersection(r):
            if r[k] != "":
                r[k] = int(r[k]) if k == "transaction_count" else float(r[k])
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
