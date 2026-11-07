"""Approve is idempotent and refuses stale plans. Runs without any keys (local-only mode)."""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from zeroday import api, balances, store

    monkeypatch.setattr(store, "DB_PATH", tmp_path / "t.sqlite3")
    monkeypatch.setattr(api, "NESSIE_KEY", "")
    monkeypatch.setattr(balances.read, "enabled", lambda: False)
    return TestClient(api.app)


def _proposal(customer_id="C002"):
    from zeroday import store
    from zeroday.agent.validator import validate_plan

    plan = {"lump_sum_from_savings": 500, "monthly_payment": 0, "monthly_card_spending_cap": None, "keep_emergency_months": 3}
    return store.create_proposal(customer_id, plan, validate_plan(customer_id, plan), "test")


def test_double_approve_only_executes_once(client):
    p = _proposal()
    first = client.post(f"/api/proposals/{p['id']}/approve")
    second = client.post(f"/api/proposals/{p['id']}/approve")
    assert first.status_code == 200 and first.json()["status"] == "approved"
    assert second.status_code == 409


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
    r = client.post(f"/api/proposals/{p['id']}/approve", json={"lump_sum_from_savings": 10_000_000})
    assert r.status_code == 422
    assert client.get("/api/proposals").json()[0]["status"] == "pending"
