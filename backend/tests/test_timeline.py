"""Plan over time (timeline.py) on C001: per-account series, payments that match the simulation, change list."""

from fastapi.testclient import TestClient
from test_wealth import LOAN, PNC_CARD, REFERENCE, STORE, STUDENT

from paidway.api.main import app
from paidway.planning import timeline, wealth

CID = "C001"


def test_series_match_simulation():
    t = timeline.timeline(CID, REFERENCE, live=False)
    sim = wealth.simulate_plan(CID, REFERENCE, live=False)
    assert len(t["months"]) == len(sim["months"])
    debts = [a for a in t["accounts"] if a["side"] == "debt"]
    assert {a["id"] for a in debts} == {STORE, PNC_CARD, LOAN, STUDENT}
    for i, m in enumerate(sim["months"]):  # per-account balances add up to the simulator's totals
        assert abs(sum(a["plan"][i] for a in debts) - m["total_debt"]) < 0.05
        assets = sum(a["plan"][i] for a in t["accounts"] if a["side"] == "asset")
        assert abs(assets - m["total_debt"] - m["net_worth"]) < 0.1
    assert next(a for a in debts if a["id"] == STORE)["payoff_plan"] == next(
        d for d in sim["debts"] if d["account_id"] == STORE)["payoff_month"]


def test_payments_before_vs_plan():
    t = timeline.timeline(CID, REFERENCE, live=False)
    ctx = wealth.context(CID, keep_months=3, live=False)
    pay = {p["account_id"]: p for p in t["payments"]}
    for d in ctx["debts"]:  # month 1 before = what the customer pays now
        assert abs(pay[d["account_id"]]["before_now"] - d["baseline_payment"]) < 1
    # The plan spends its budget (or less, once debts are gone) every month.
    for i in range(len(t["months"]) - 1):
        assert sum(p["plan"][i] for p in t["payments"]) <= REFERENCE["monthly_debt_budget"] + 0.5
    assert pay[STORE]["plan_now"] > pay[STORE]["before_now"]
    assert abs(pay[STUDENT]["plan_now"] - pay[STUDENT]["min_payment"]) < 1


def test_changes_and_non_changes():
    t = timeline.timeline(CID, REFERENCE, live=False)
    by = {(c["group"], c["label"]): c for c in t["changes"]}
    student = next(c for c in t["changes"] if c["group"] == "Debts" and not c["changed"])
    assert "minimum" in student["text"]
    assert by[("Spending", "Dining")]["changed"] and "→" in by[("Spending", "Dining")]["text"]
    assert not by[("Spending", "Groceries")]["changed"] and "no change" in by[("Spending", "Groceries")]["text"]
    assert by[("Saving", "Brokerage auto-invest")]["changed"] and "$225 → $0" in by[("Saving", "Brokerage auto-invest")]["text"]
    assert by[("Saving", "Investing")]["changed"]
    assert not by[("Income", "Income")]["changed"]


def test_paused_contribution_moves_from_invested_to_debt():
    t = timeline.timeline(CID, REFERENCE, live=False)
    b, p = t["cashflow"]["before"][0], t["cashflow"]["plan"][0]
    assert abs((b["savings_invest"] - p["savings_invest"]) - 225) < 0.5
    assert p["debt_payments"] > b["debt_payments"]
    assert "transfers_out" not in p


def test_endpoint(monkeypatch):
    real = timeline.timeline
    monkeypatch.setattr(timeline, "timeline", lambda cid, plan, **kw: real(cid, plan, live=False))
    r = TestClient(app).post(f"/api/customers/{CID}/plan/timeline", json={"plan": REFERENCE})
    assert r.status_code == 200
    assert r.json()["changes"]
    assert TestClient(app).post("/api/customers/NOPE/plan/timeline", json={"plan": REFERENCE}).status_code == 404
