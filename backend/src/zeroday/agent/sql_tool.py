"""Read-only SQL for the agent, over ONE customer's data.

Each call builds a fresh in-memory SQLite database holding only that customer's rows, then locks it:
- the app DB file is never opened (the data is copied in from pnc_data / balance_sheet / insights);
- a single SELECT / WITH statement only (comments stripped before the check);
- PRAGMA query_only=ON;
- an authorizer that allows only SELECT, READ, RECURSIVE and allow-listed functions
  (no ATTACH, PRAGMA, DDL, DML, load_extension, ...);
- length limits, a progress-handler timeout and a row cap.
"""

from __future__ import annotations

import re
import sqlite3
import time

from .. import balance_sheet, insights, pnc_data

TIMEOUT_S = 2.0
MAX_SQL_CHARS = 4_000

SCHEMA_DOC = """Read-only SQL (SQLite) over this customer's data only. One SELECT or WITH statement.
Views:
- v_accounts(account_id, account_type, kind, name, institution, side, balance, apr_pct, annual_yield_pct,
  min_payment, cost_basis, secured, consequence_tier, source)       side = 'asset' | 'debt'
- v_debts(account_id, account_type, kind, name, balance, apr_pct, min_payment, secured, consequence_tier, source)
- v_assets(account_id, account_type, kind, name, balance, annual_yield_pct, cost_basis, source)
- v_transactions(transaction_id, account_id, account_type, posted_date, month, transaction_type, category,
  description, amount, balance_change, flow_class)
    amount > 0 always; balance_change is signed (+ raises that account's balance).
    flow_class: income | expense | internal_transfer | external_transfer | non_cash
    card purchases: account_type='credit_card' AND flow_class='expense'
- v_monthly_cashflow(month, income, essentials, discretionary, debt_payments, savings_invest,
  transfers_out_unknown, surplus, debt_payments_gross, card_purchases)
    debt_payments = payments to debts minus new card purchases; surplus = change in cash.
Dates are 'YYYY-MM-DD' text; month is 'YYYY-MM'. Example:
  SELECT month, SUM(amount) FROM v_transactions WHERE flow_class='expense' GROUP BY month ORDER BY month"""

ACC_COLS = ["account_id", "account_type", "kind", "name", "institution", "side", "balance", "apr_pct",
            "annual_yield_pct", "min_payment", "cost_basis", "secured", "consequence_tier", "source"]
TX_COLS = ["transaction_id", "account_id", "account_type", "posted_date", "month", "transaction_type", "category",
           "description", "amount", "balance_change", "flow_class"]
CF_COLS = ["month", "income", "essentials", "discretionary", "debt_payments", "savings_invest",
           "transfers_out_unknown", "surplus", "debt_payments_gross", "card_purchases"]

ALLOWED_FUNCTIONS = {
    "abs", "avg", "count", "max", "min", "sum", "total", "round", "coalesce", "ifnull", "nullif", "iif",
    "lower", "upper", "length", "substr", "substring", "instr", "replace", "trim", "ltrim", "rtrim", "printf",
    "format", "group_concat", "string_agg", "date", "time", "datetime", "julianday", "strftime", "unixepoch",
    "like", "glob", "typeof", "cast", "sign", "floor", "ceil", "ceiling",
    "row_number", "rank", "dense_rank", "lag", "lead", "first_value", "last_value", "ntile", "percent_rank",
    "cume_dist", "nth_value",
}
_ALLOWED_ACTIONS = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, getattr(sqlite3, "SQLITE_RECURSIVE", 33)}

_COMMENTS = re.compile(r"--[^\n]*|/\*.*?(\*/|$)", re.S)


class _Denied(Exception):
    pass


def _authorizer(action, arg1, arg2, db_name, trigger):
    if action in _ALLOWED_ACTIONS:
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_FUNCTION and (arg2 or "").lower() in ALLOWED_FUNCTIONS:
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


def _build(customer_id: str, live: bool) -> sqlite3.Connection:
    db = sqlite3.connect(":memory:", isolation_level=None, check_same_thread=False)
    db.execute(f"CREATE TABLE _accounts ({', '.join(ACC_COLS)})")
    db.execute(f"CREATE TABLE _transactions ({', '.join(TX_COLS)})")
    db.execute(f"CREATE TABLE _cashflow ({', '.join(CF_COLS)})")
    accs = [a for a in balance_sheet.accounts(customer_id, live=live) if a.get("customer_id", customer_id) == customer_id]
    db.executemany(f"INSERT INTO _accounts VALUES ({', '.join('?' * len(ACC_COLS))})",
                   [[a.get(c) if c != "secured" else int(bool(a.get(c))) for c in ACC_COLS] for a in accs])
    txs = [t for t in pnc_data.customer_transactions(customer_id) if t["customer_id"] == customer_id]
    db.executemany(f"INSERT INTO _transactions VALUES ({', '.join('?' * len(TX_COLS))})",
                   [[t["posted_date"][:7] if c == "month" else t.get(c) for c in TX_COLS] for t in txs])
    cf = insights.cash_flow(customer_id, months=0)["months"] if txs else []
    db.executemany(f"INSERT INTO _cashflow VALUES ({', '.join('?' * len(CF_COLS))})",
                   [[m[c] for c in CF_COLS] for m in cf])
    db.executescript("""
        CREATE VIEW v_accounts AS SELECT * FROM _accounts;
        CREATE VIEW v_debts AS SELECT account_id, account_type, kind, name, balance, apr_pct, min_payment, secured,
            consequence_tier, source FROM _accounts WHERE side = 'debt';
        CREATE VIEW v_assets AS SELECT account_id, account_type, kind, name, balance, annual_yield_pct, cost_basis,
            source FROM _accounts WHERE side = 'asset';
        CREATE VIEW v_transactions AS SELECT * FROM _transactions;
        CREATE VIEW v_monthly_cashflow AS SELECT * FROM _cashflow;
    """)
    # Lock it down. Order matters: the authorizer would block these setup statements.
    db.execute("PRAGMA query_only = ON")
    db.execute("PRAGMA trusted_schema = OFF")
    if hasattr(db, "enable_load_extension"):
        db.enable_load_extension(False)
    if hasattr(db, "setlimit"):
        db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 1_000_000)
        db.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, MAX_SQL_CHARS)
        db.setlimit(sqlite3.SQLITE_LIMIT_ATTACHED, 0)
    db.set_authorizer(_authorizer)
    return db


def _check(query: str) -> str | None:
    if not isinstance(query, str) or not query.strip():
        return "empty query"
    if len(query) > MAX_SQL_CHARS:
        return f"query longer than {MAX_SQL_CHARS} characters"
    bare = _COMMENTS.sub(" ", query).strip().rstrip(";").strip()
    if ";" in bare:
        return "one statement only"
    if not re.match(r"(?is)^(select|with)\b", bare):
        return "only SELECT or WITH queries are allowed"
    return None


def run_sql(customer_id: str, query: str, max_rows: int = 200, live: bool = True) -> dict:
    """Run one read-only query over this customer's views. Returns columns/rows, or {"error"}."""
    err = _check(query)
    if err:
        return {"error": err}
    max_rows = max(1, min(int(max_rows), 1000))
    db = _build(customer_id, live)
    deadline = time.monotonic() + TIMEOUT_S
    db.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10_000)
    try:
        cur = db.execute(query)
        cols = [d[0] for d in cur.description or []]
        rows = cur.fetchmany(max_rows + 1)
    except sqlite3.OperationalError as e:
        msg = str(e)
        if "interrupted" in msg:
            msg = f"query timed out after {TIMEOUT_S:g}s"
        return {"error": msg}
    except (sqlite3.DatabaseError, sqlite3.ProgrammingError, sqlite3.Warning) as e:
        return {"error": str(e)}
    finally:
        db.close()
    truncated = len(rows) > max_rows
    return {"columns": cols, "rows": [list(r) for r in rows[:max_rows]], "truncated": truncated}
