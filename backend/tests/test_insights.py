"""Cash-flow buckets add up and match C001's known shape."""

import pytest

from zeroday import insights

BUCKETS = ["essentials", "discretionary", "debt_payments", "savings_invest"]


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
    assert abs(cf["saved_per_month"]) < 300                # the old "unknown" outflow is now named spending
    spend = {c["category"]: c["monthly_avg"] for c in cf["spending_by_category"]}
    assert all(spend.get(k, 0) > 0 for k in ("travel", "home_household", "gifts_holidays", "car_maintenance",
                                             "annual_bills"))
    assert 0 < a["debt_payments"] < 600
    assert abs(a["surplus"]) < 300
    assert {"dining", "groceries", "shopping", "transport"} <= set(cf["card_spending_by_category"])
    assert cf["essentials_by_category"]["housing"] > 2_000
    assert cf["linked_debt_minimums_per_month"] == pytest.approx(195.07 + 40.57, abs=0.01)
    flows = {f["flow"]: f for f in cf["named_flows"]}
    assert flows["monthly_surplus"]["flag"] == "redirect" and flows["monthly_surplus"]["per_month"] == 0
    assert flows["brokerage_auto_invest"]["flag"] == "pause" and flows["brokerage_auto_invest"]["per_month"] == 225
    assert flows["debt_minimums"]["flag"] == "fixed"
    flags = {c["category"]: c["flag"] for c in cf["spending_by_category"]}
    assert flags["housing"] == "fixed" and flags["dining"] == "trim" and flags["subscriptions"] == "cancel"
    assert flags["travel"] == "trim" and flags["car_maintenance"] == "fixed" and flags["healthcare"] == "ask"


def test_spending_detail_dining():
    d = insights.spending_detail("C001", "dining")
    assert d["essential"] is False and d["flag"] == "trim" and d["max_cut_pct"] == 30
    assert d["min"] <= d["p25"] <= d["avg"] <= d["max"]
    assert d["min_month"] and any(m["amount"] == d["min"] for m in d["monthly"])
    assert len(d["monthly"]) == 12
    assert d["merchant_detail_available"] is True  # merchant-named transactions
    assert any(r["name"] == "Uber Eats" for r in d["recurring"])


def test_spending_detail_essential_and_unknown():
    assert insights.spending_detail("C001", "housing")["essential"] is True
    none = insights.spending_detail("C001", "no_such_category")
    assert none["avg"] == 0 and none["min_month"] is None


def test_recurring():
    rec = insights.find_recurring("C001")
    kinds = {r["kind"] for r in rec}
    assert {"spending", "debt_payment", "saving"} <= kinds
    assert any(r["category"] == "housing" for r in rec)
    assert any(r["category"] == "subscriptions" and r["name"] == "Audible" for r in rec)
    linked = [r for r in rec if r["kind"] == "debt_payment" and "student loan" in r["name"]]
    assert len(linked) == 1  # linked minimums appear once, from the ledger
    assert all(r["seen_in_history"] for r in rec)
