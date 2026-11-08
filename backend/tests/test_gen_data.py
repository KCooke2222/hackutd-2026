"""The PNC ledger generated from data/customers.yaml (scripts/gen_data.py) reconciles and keeps PNC's snapshot."""

import csv
import importlib.util
from collections import defaultdict
from pathlib import Path

import pytest

from paidway.data import linked

DATA = Path(__file__).resolve().parents[1] / "data"


def _rows(name, base=DATA / "pnc"):
    with open(base / name, newline="") as f:
        return list(csv.DictReader(f))


def _cents(x):
    return round(float(x) * 100)


@pytest.fixture(scope="module")
def txns():
    return _rows("transactions.csv")


def _gen_data():
    spec = importlib.util.spec_from_file_location("gen_data", DATA.parent / "scripts" / "gen_data.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def regenerated(tmp_path_factory):
    out = tmp_path_factory.mktemp("pnc")
    _gen_data().generate(_gen_data().load_spec(), out)
    return out


def test_regenerates_identically(regenerated):
    """data/pnc is exactly what the spec generates: rerun `make data` after editing customers.yaml."""
    for name in ("transactions.csv", "monthly_statements.csv", "accounts.csv", "profiles.csv", "history_manifest.json"):
        assert (regenerated / name).read_text() == (DATA / "pnc" / name).read_text(), name


def test_monthly_aggregates_match_committed(regenerated):
    """Per customer and month: income, essentials, discretionary, debt payments, investing and every closing
    balance within 1% of the committed CSVs, with the same customer, account and category ids."""
    result = _gen_data().compare(regenerated, DATA / "pnc")
    assert all(result["ids_match"].values()), result["ids_match"]
    assert not result["over"], result["worst"]


def test_spec_covers_every_customer():
    spec = _gen_data().load_spec()
    assert list(spec["customers"]) == [r["customer_id"] for r in _rows("profiles.csv")]
    for cid, c in spec["customers"].items():
        assert {"income", "spending", "accounts", "linked", "bills", "transfers", "lumpy"} <= set(c), cid
        assert linked.base_rows(cid) == linked.seeded_rows(cid), cid  # the spec froze the seeded linked accounts


def test_spec_knob_changes_only_that_customer(tmp_path):
    """Editing one customer's spec leaves every other customer's history byte-identical."""
    mod = _gen_data()
    spec = mod.load_spec()
    spec["customers"]["C004"]["bills"]["subscriptions"].append({"name": "Netflix", "amount": 15.49, "day": 9})
    mod.generate(spec, tmp_path)
    new = [r for r in _rows("transactions.csv", tmp_path) if r["customer_id"] != "C004"]
    old = [r for r in _rows("transactions.csv") if r["customer_id"] != "C004"]
    strip = lambda rows: [{k: v for k, v in r.items() if k != "transaction_id"} for r in rows]  # noqa: E731
    assert strip(new) == strip(old)
    c004 = [r for r in _rows("transactions.csv", tmp_path) if r["customer_id"] == "C004" and r["category"] == "subscriptions"]
    assert sum(r["description"] == "Netflix" and r["posted_date"].endswith("-09") for r in c004) == 24


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
