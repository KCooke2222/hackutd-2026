"""Cash-flow buckets add up and match C001's known shape."""

import pytest

from zeroday import insights

BUCKETS = ["essentials", "discretionary", "debt_payments", "savings_invest", "transfers_out_unknown"]


@pytest.mark.parametrize("cid", ["C001", "C002", "C010"])
def test_buckets_add_up(cid):
    cf = insights.cash_flow(cid)
    assert len(cf["months"]) == 12
    for m in cf["months"]:
        assert m["surplus"] == pytest.approx(m["income"] - sum(m[b] for b in BUCKETS), abs=0.05)
        assert m["debt_payments"] == pytest.approx(m["debt_payments_gross"] - m["card_purchases"], abs=0.05)


def test_c001_shape():
    cf = insights.cash_flow("C001")
    a = cf["monthly_avg"]
    assert 9_000 < a["income"] < 10_000
    assert 2_500 < cf["unknown_outflow_per_month"] < 3_300   # the leak
    assert 0 < a["debt_payments"] < 300                       # card barely shrinks
    assert abs(a["surplus"]) < 300
    assert set(cf["card_spending_by_category"]) == {"dining", "groceries", "shopping", "transport"}
    assert cf["essentials_by_category"]["housing"] > 2_000
    assert cf["linked_debt_minimums_per_month"] == pytest.approx(195.07 + 40.57, abs=0.01)


def test_spending_detail_dining():
    d = insights.spending_detail("C001", "dining")
    assert d["essential"] is False
    assert d["min"] <= d["p25"] <= d["avg"] <= d["max"]
    assert d["min_month"] and any(m["amount"] == d["min"] for m in d["monthly"])
    assert len(d["monthly"]) == 12
    assert d["merchant_detail_available"] is False  # PNC descriptions are generic


def test_spending_detail_essential_and_unknown():
    assert insights.spending_detail("C001", "housing")["essential"] is True
    none = insights.spending_detail("C001", "no_such_category")
    assert none["avg"] == 0 and none["min_month"] is None


def test_recurring():
    rec = insights.find_recurring("C001")
    kinds = {r["kind"] for r in rec}
    assert {"spending", "debt_payment", "transfer_out"} <= kinds
    assert any(r["category"] == "housing" for r in rec)
    linked = [r for r in rec if not r["seen_in_history"]]
    assert {r["name"] for r in linked} == {"Federal Direct Loan minimum", "Store card minimum"}
    assert all(r["cadence"] == "monthly" for r in rec)
