"""The regenerated PNC ledger (scripts/gen_history.py) reconciles and keeps PNC's snapshot."""

import csv
import importlib.util
from collections import defaultdict
from pathlib import Path

import pytest

from zeroday import linked

DATA = Path(__file__).resolve().parents[1] / "data"


def _rows(name, base=DATA / "pnc"):
    with open(base / name, newline="") as f:
        return list(csv.DictReader(f))


def _cents(x):
    return round(float(x) * 100)


@pytest.fixture(scope="module")
def txns():
    return _rows("transactions.csv")


def test_regenerates_identically(tmp_path):
    spec = importlib.util.spec_from_file_location("gen_history", DATA.parent / "scripts" / "gen_history.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.generate_history(out_dir=tmp_path)
    for name in ("transactions.csv", "monthly_statements.csv", "accounts.csv", "profiles.csv"):
        assert (tmp_path / name).read_text() == (DATA / "pnc" / name).read_text(), name


def test_snapshot_preserved():
    def key(r):
        return (r["customer_id"], r["account_type"])

    orig = {key(r): r for r in _rows("accounts.csv", DATA / "pnc_original")}
    new = {key(r): r for r in _rows("accounts.csv")}
    assert orig.keys() == new.keys()
    for k, r in orig.items():
        for f in ("balance", "monthly_payment", "apr_pct"):
            assert _cents(new[k][f]) == _cents(r[f]), (k, f)
    po = {r["customer_id"]: r for r in _rows("profiles.csv", DATA / "pnc_original")}
    for r in _rows("profiles.csv"):
        assert _cents(r["monthly_income"]) == _cents(po[r["customer_id"]]["monthly_income"])
        assert _cents(r["monthly_non_debt_expenses"]) >= _cents(po[r["customer_id"]]["monthly_non_debt_expenses"])


def test_ledger_reconciles(txns):
    finals = {(r["customer_id"], r["account_type"]): _cents(r["balance"]) for r in _rows("accounts.csv")}
    by_month = defaultdict(int)
    for t in txns:
        by_month[(t["customer_id"], t["account_type"], t["posted_date"][:7])] += _cents(t["balance_change"])
    last = {}
    for s in _rows("monthly_statements.csv"):
        k = (s["customer_id"], s["account_type"])
        o, c = _cents(s["opening_balance"]), _cents(s["closing_balance"])
        assert o + _cents(s["balance_increases"]) - _cents(s["balance_decreases"]) == c
        assert by_month[(*k, s["month"])] == c - o
        if k in last:
            assert last[k] == o, k
        last[k] = c
    assert last == finals


def test_no_unknown_outflow(txns):
    assert not [t for t in txns if t["category"] == "external_cash_management"]
    assert all(t["description"] and "unlisted" not in t["description"].lower() for t in txns)
    externals = {t["category"] for t in txns if t["flow_class"] == "external_transfer"}
    assert externals <= {"debt_payment", "investment_contribution"}


def test_linked_minimums_paid_from_checking(txns):
    for cid in ("C001", "C016", "C027"):
        mins = sorted(round(r["min_payment"], 2) for r in linked.base_rows(cid)
                      if r["account_type"] in linked.DEBT_TYPES and r["balance"] > 0 and r["min_payment"])
        paid = sorted({round(float(t["amount"]), 2) for t in txns if t["customer_id"] == cid
                       and t["category"] == "debt_payment" and t["flow_class"] == "external_transfer"})
        assert paid == sorted(set(mins)), cid


def test_irregular_spending_is_named_and_lumpy(txns):
    """The residual is real merchant-named spending (travel, repairs, gifts, bills), not a transfer out."""
    cats = ("travel", "gifts_holidays", "home_household", "car_maintenance", "annual_bills")
    c001 = [t for t in txns if t["customer_id"] == "C001" and t["category"] in cats]
    assert {t["category"] for t in c001} == set(cats)
    assert all(t["flow_class"] == "expense" and t["description"] for t in c001)
    by_month = defaultdict(int)
    for t in c001:
        if t["category"] == "travel":
            by_month[t["posted_date"][:7]] += _cents(t["amount"])
    assert max(by_month.values()) > 3 * (sum(by_month.values()) / len(by_month))  # lumpy: a big trip month
