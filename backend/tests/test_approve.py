"""Approve (plan v2) is idempotent, re-validates, refuses stale plans, and only moves cash into the
Nessie-backed card. Runs without any keys; validate_plan_v2 is faked so this is independent of the engine."""

import pytest
from fastapi.testclient import TestClient

CARD = "A-C001-CREDIT_CARD"
PLAN = {"version": 2, "monthly_debt_budget": 3000.0, "extra_order": [CARD],
        "lump_sums": [{"source": "cash", "amount": 500.0, "to_debt": CARD},
                      {"source": "brokerage", "amount": 300.0, "to_debt": CARD}],
        "redirects": [{"flow": "external_cash_management", "amount_per_month": 2500.0}],
        "spending_targets": [], "autopay_full_cards": [CARD], "keep_emergency_months": 3.0}
SIM = {"feasible": True, "problems": [], "net_worth_24m": 1.0, "baseline_net_worth_24m": 0.0,
       "high_cost_debt_free": "2027-02", "interest_total": 10.0, "debts": [], "months": []}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from zeroday import api, balances, store
    from zeroday.agent import execute, validator

    monkeypatch.setattr(store, "DB_PATH", tmp_path / "t.sqlite3")
    monkeypatch.setattr(execute, "NESSIE_KEY", "")
    monkeypatch.setattr(balances.read, "enabled", lambda: False)

    def fake_validate(cid, plan):
        if plan["monthly_debt_budget"] > 1e6:
            e = validator.PlanRejected("budget over its sources")
            e.reasons = ["budget over its sources"]
            raise e
        return dict(SIM)

    monkeypatch.setattr(validator, "validate_plan_v2", fake_validate, raising=False)
    return TestClient(api.app)


def _proposal(cid="C001"):
    from zeroday import balances, store

    return store.create_proposal(cid, PLAN, {**SIM, "balances_snapshot": balances.snapshot(cid)}, "test")


def test_double_approve_only_executes_once(client):
    p = _proposal()
    first = client.post(f"/api/proposals/{p['id']}/approve")
    second = client.post(f"/api/proposals/{p['id']}/approve")
    assert first.status_code == 200 and first.json()["status"] == "approved"
    assert second.status_code == 409


def test_only_cash_to_nessie_card_is_a_transfer(client):
    p = _proposal()
    ex = client.post(f"/api/proposals/{p['id']}/approve").json()["execution"]
    assert ex["transfers_pending"] == [{"from": "savings", "to": "credit_card", "to_debt": CARD, "amount": 500.0}]
    kinds = [s["kind"] for s in ex["scheduled"]]
    assert kinds.count("lump_sum") == 1 and "redirect" in kinds and "autopay" in kinds and "budget" in kinds


def test_stale_balances_refused(client, monkeypatch):
    from zeroday import balances

    p = _proposal()
    real = balances.snapshot
    monkeypatch.setattr(balances, "snapshot", lambda cid: {**real(cid), "pnc": {"credit_card": 1.0, "savings": 2.0}})
    r = client.post(f"/api/proposals/{p['id']}/approve")
    assert r.status_code == 409 and "stale" in r.json()["detail"]
    assert client.get("/api/proposals").json()[0]["status"] == "stale"


def test_invalid_edit_keeps_pending(client):
    p = _proposal()
    r = client.post(f"/api/proposals/{p['id']}/approve", json={"plan": {"monthly_debt_budget": 1e7}})
    assert r.status_code == 422
    assert client.get("/api/proposals").json()[0]["status"] == "pending"
