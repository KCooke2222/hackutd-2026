"""The agent's SQL tool is read-only, single-statement, single-customer and time-boxed."""

import sqlite3

import pytest

from paidway.agent import sql_tool
from paidway.agent.sql_tool import run_sql

C = "C001"


def q(sql, **kw):
    return run_sql(C, sql, live=False, **kw)


def test_c001_queries_work():
    r = q("SELECT name, balance, apr_pct FROM v_debts ORDER BY apr_pct DESC")
    assert "error" not in r
    assert r["columns"] == ["name", "balance", "apr_pct"]
    names = [row[0] for row in r["rows"]]
    assert "Store card" in names and "PNC Credit card" in names
    r = q("SELECT month, income, surplus FROM v_monthly_cashflow ORDER BY month")
    assert len(r["rows"]) >= 12 and all(row[1] > 0 for row in r["rows"])
    r = q("WITH m AS (SELECT month, SUM(amount) s FROM v_transactions WHERE flow_class='expense' GROUP BY month) "
          "SELECT COUNT(*), ROUND(AVG(s), 2) FROM m")
    assert r["rows"][0][0] >= 12
    r = q("SELECT account_type, SUM(balance) FROM v_assets GROUP BY account_type")
    assert {row[0] for row in r["rows"]} >= {"checking", "savings", "brokerage", "ira"}


def test_row_cap():
    r = q("SELECT * FROM v_transactions", max_rows=5)
    assert len(r["rows"]) == 5 and r["truncated"] is True


@pytest.mark.parametrize("sql", [
    "DROP VIEW v_accounts",
    "DROP TABLE _accounts",
    "DELETE FROM _accounts",
    "INSERT INTO _accounts (account_id) VALUES ('x')",
    "UPDATE _accounts SET balance = 0",
    "CREATE TABLE t (x)",
    "CREATE TEMP VIEW t AS SELECT 1",
    "ATTACH DATABASE 'app.db' AS x",
    "DETACH DATABASE main",
    "PRAGMA query_only = OFF",
    "PRAGMA table_info(_accounts)",
    "VACUUM",
    "VACUUM INTO 'copy.db'",
    "REINDEX",
    "ANALYZE",
    "BEGIN",
    "SELECT load_extension('evil')",
    "SELECT zeroblob(100000000)",
    "SELECT * FROM pragma_table_info('_accounts')",
    "SELECT * FROM sqlite_master; DROP VIEW v_accounts",
    "SELECT 1; SELECT 2",
    "/* comment */ DELETE FROM _accounts",
    "-- comment\nDELETE FROM _accounts",
    "SELECT 1 /* ; */ ; DELETE FROM _accounts",
    "SELECT '/*' ; DELETE FROM _accounts; --*/'",
    "WITH x AS (SELECT 1) DELETE FROM _accounts",
    "WITH x AS (SELECT 1) INSERT INTO _accounts (account_id) SELECT 1",
    "WITH x AS (SELECT 1) UPDATE _accounts SET balance = 0",
    "",
])
def test_rejected(sql):
    r = q(sql)
    assert "error" in r, r
    # and nothing changed
    assert q("SELECT COUNT(*) FROM v_accounts")["rows"][0][0] > 0


def test_authorizer_blocks_writes_even_without_the_prefix_check():
    """Defense in depth: the locked connection itself refuses writes, PRAGMA and ATTACH."""
    db = sql_tool._build(C, live=False)
    for stmt in ["DELETE FROM _accounts", "DROP VIEW v_accounts", "UPDATE _accounts SET balance=0",
                 "INSERT INTO _accounts (account_id) VALUES ('x')", "CREATE TABLE t(x)",
                 "PRAGMA query_only=OFF", "ATTACH DATABASE ':memory:' AS x", "SELECT load_extension('x')"]:
        with pytest.raises(sqlite3.DatabaseError):
            db.execute(stmt)
    assert db.execute("SELECT COUNT(*) FROM v_accounts").fetchone()[0] > 0
    db.close()


def test_runaway_recursive_cte_times_out(monkeypatch):
    monkeypatch.setattr(sql_tool, "TIMEOUT_S", 0.3)
    r = q("WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c) SELECT COUNT(*) FROM c")
    assert "timed out" in r["error"]


def test_cartesian_blowup_times_out(monkeypatch):
    monkeypatch.setattr(sql_tool, "TIMEOUT_S", 0.3)
    r = q("SELECT COUNT(*) FROM v_transactions a, v_transactions b, v_transactions c, v_transactions d")
    assert "timed out" in r["error"]


def test_only_this_customers_rows():
    ids = q("SELECT DISTINCT account_id FROM v_transactions")["rows"]
    assert ids and all(r[0].startswith("A-C001-") for r in ids)
    accs = q("SELECT account_id FROM v_accounts")["rows"]
    assert all("-C001-" in r[0] for r in accs)
    assert q("SELECT * FROM v_transactions WHERE account_id LIKE '%C002%'")["rows"] == []
    assert q("SELECT * FROM _transactions WHERE account_id NOT LIKE '%C001%'")["rows"] == []
    other = run_sql("C002", "SELECT DISTINCT account_id FROM v_accounts", live=False)["rows"]
    assert other and all("-C002-" in r[0] for r in other)


def test_query_runs_on_a_private_in_memory_db():
    """Data is copied in (store/pnc reads happen in Python); the query only sees our tables and views."""
    names = {r[0] for r in q("SELECT name FROM sqlite_master")["rows"]}
    assert names == {"_accounts", "_transactions", "_cashflow",
                     "v_accounts", "v_debts", "v_assets", "v_transactions", "v_monthly_cashflow"}
    assert "error" in q("SELECT * FROM proposals")
    assert "error" in q("SELECT * FROM main.customers")
