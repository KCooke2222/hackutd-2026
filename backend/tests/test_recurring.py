"""Recurring bills in Nessie (planning/recurring.py, data/seed.py, agent/execute.py): the baseline, the plan as a
diff against it (keep / change / pause / end / new), the calls approve sends for it, and the check-in's bill
upkeep. Every Nessie request is mocked."""

import pytest
from fastapi.testclient import TestClient
from test_nessie_actions import CARD, FED, IDS, LOAN, PLAN, SIM, STORE, env  # noqa: F401 - env is a fixture

BILLS = {f"debt:{CARD}": {"id": "b1", "amount": 1520, "kind": "debt", "name": "PNC Credit card"},
         f"debt:{LOAN}": {"id": "b2", "amount": 29, "kind": "debt", "name": "PNC Personal loan"},
         f"debt:{STORE}": {"id": "b3", "amount": 41, "kind": "debt", "name": "Store card"},
         f"debt:{FED}": {"id": "b4", "amount": 195, "kind": "debt", "name": "Federal Direct Loan"},
         "contrib:brokerage": {"id": "b5", "amount": 225, "kind": "contribution", "payee": "Brokerage",
                               "recurring_date": 5, "nickname": "Paidway: Brokerage auto-invest"},
         "bill:rent": {"id": "b6", "amount": 2065, "kind": "bill", "name": "Rent"}}
IDS_B = {**IDS, "bills": BILLS, "paused": {}}


@pytest.fixture()
def seeded(env):  # noqa: F811
    env["monkeypatch"].setattr(env["execute"], "_ids", lambda cid: dict(IDS_B))
    return env


def test_plan_is_a_diff_against_the_bills_in_nessie(seeded):
    ex = seeded["execute"]
    _, steps = ex.schedule("C001", PLAN, SIM)
    diff = {r["key"]: r for r in ex.bill_diff("C001", steps)}
    assert diff[f"debt:{CARD}"]["action"] == "change" and diff[f"debt:{CARD}"]["to"] == 2154
    assert diff[f"debt:{LOAN}"]["action"] == diff[f"debt:{FED}"]["action"] == diff["bill:rent"]["action"] == "keep"
    assert diff[f"debt:{STORE}"]["action"] == "end"  # paid off today, no charges planned: its bill stops
    pause = diff["contrib:brokerage"]
    assert pause["action"] == "pause" and pause["resume"] == "2027-06"  # the month after high-cost $0
    assert "until Jun 2027; restarts automatically" in pause["step"]


def test_approve_sends_only_the_diff(seeded):
    ex = seeded["execute"]
    _, steps = ex.schedule("C001", PLAN, SIM)
    bills = [(c["method"], c["path"], c.get("bill_action")) for c in ex.nessie_calls("C001", [], steps, IDS_B)
             if "bills" in c["path"]]
    # keep -> no call; change -> PUT; end and pause -> DELETE; nothing is created again
    assert sorted(bills) == [("DELETE", "/bills/b3", "end"), ("DELETE", "/bills/b5", "pause"),
                             ("PUT", "/bills/b1", "change")]


def test_without_seeded_bills_approve_creates_them(env):  # noqa: F811
    ex = env["execute"]
    _, steps = ex.schedule("C001", PLAN, SIM)
    assert {c["method"] for c in ex.nessie_calls("C001", [], steps, IDS) if "bills" in c["path"]} == {"POST"}


def _month(label, debts, payments):
    return {"month": label, "balances": {"debts": debts, "payments": payments}}


def test_bill_schedule_moves_the_money_when_a_debt_hits_zero(seeded):
    ex = seeded["execute"]
    _, steps = ex.schedule("C001", PLAN, SIM)
    sim = {**SIM, "months": [_month("2026-10", {CARD: 3000, LOAN: 1000}, {}),
                             _month("2026-11", {CARD: 900, LOAN: 980}, {CARD: 2154, LOAN: 29}),
                             _month("2026-12", {CARD: 0, LOAN: 960}, {CARD: 900, LOAN: 29}),
                             _month("2027-01", {CARD: 0, LOAN: 0}, {CARD: 1423, LOAN: 960})]}
    sched = ex.bill_schedule(sim, steps, [CARD])
    card = next(e for e in sched if e["key"] == f"debt:{CARD}")
    assert card["month"] == "2027-01" and card["action"] == "change" and card["amount"] == 1423
    assert "now the statement in full" in card["why"]
    loan = next(e for e in sched if e["key"] == f"debt:{LOAN}")
    assert loan["action"] == "change" and loan["amount"] == 960  # the freed money moves to the next debt
    resume = next(e for e in sched if e["action"] == "resume")
    assert resume["key"] == "contrib:brokerage" and resume["month"] == "2027-06" and resume["amount"] == 225


def test_checkin_runs_only_the_approved_schedule(seeded):
    ex, store = seeded["execute"], seeded["store"]
    from paidway.data import seed

    p = store.create_proposal("C001", PLAN, dict(SIM), "test")
    sched = [{"month": "2027-01", "key": f"debt:{CARD}", "action": "change", "amount": 1423, "was": 2154,
              "name": "PNC Credit card", "why": "paid off", "done": False},
             {"month": "2027-06", "key": "contrib:brokerage", "action": "resume", "amount": 225, "was": None,
              "name": "Brokerage", "why": "high-cost debt is $0", "to": "L-C001-BROKERAGE-1", "done": False}]
    store.set_execution(p["id"], {"bill_schedule": sched})
    recorded = []
    seeded["monkeypatch"].setattr(seed, "nessie_ids", lambda cid: {**IDS_B, "paused": {"contrib:brokerage": BILLS["contrib:brokerage"]}})
    seeded["monkeypatch"].setattr(seed, "record_bills", lambda cid, ch: recorded.append(ch))
    seeded["monkeypatch"].setattr(ex, "NESSIE_KEY", "test")
    assert ex.apply_due_bills("C001", p["id"], "2026-12") == []  # nothing due yet
    ran = ex.apply_due_bills("C001", p["id"], "2027-01")
    assert [(r["method"], r["path"]) for r in ran] == [("PUT", "/bills/b1")]
    assert ("PUT", "/bills/b1", {"payment_amount": 1423}) in seeded["sent"]
    ran = ex.apply_due_bills("C001", p["id"], "2027-07")  # the pause ends: the bill is created again
    assert [(r["method"], r["path"]) for r in ran] == [("POST", "/accounts/chk1/bills")]
    body = seeded["sent"][-1][2]
    assert body["payment_date"] == "2027-06-05" and body["payment_amount"] == 225
    ex_ = store.get_proposal(p["id"])["execution"]
    assert all(e["done"] for e in ex_["bill_schedule"]) and len(ex_["bill_log"]) == 2
    assert recorded[-1][0]["action"] == "resume"
    assert ex.apply_due_bills("C001", p["id"], "2027-08") == []  # each entry runs once


def test_seed_bills_posts_the_baseline(env, tmp_path):  # noqa: F811
    from paidway.data import seed
    from paidway.planning import recurring

    mp = env["monkeypatch"]
    ids_path = tmp_path / "ids.json"
    ids_path.write_text(__import__("json").dumps({"C001": {**IDS, "mirrors_complete": True}}))
    mp.setattr(seed, "IDS_PATH", ids_path)
    mp.setattr(seed, "NESSIE_KEY", "test")
    rows = [{"key": f"debt:{CARD}", "kind": "debt", "name": "PNC Credit card", "payee": "PNC Credit card",
             "amount": 1520, "day": 28, "to": CARD, "category": "debt_payment"},
            {"key": "bill:rent", "kind": "bill", "name": "Rent", "payee": "Rent", "amount": 2065, "day": 1, "to": None,
             "category": "housing"}]
    mp.setattr(recurring, "baseline", lambda cid: rows)
    seed.seed_bills("C001")
    posts = [(p, b) for m, p, b in env["sent"] if m == "POST"]
    assert [p for p, _ in posts] == ["/accounts/chk1/bills"] * 2
    assert {b["payee"]: b["payment_amount"] for _, b in posts} == {"PNC Credit card": 1520, "Rent": 2065}
    saved = seed.nessie_ids("C001")
    assert saved["bills_complete"] and set(saved["bills"]) == {f"debt:{CARD}", "bill:rent"}
    seed.seed_bills("C001")  # idempotent: nothing posted twice
    assert len([1 for m, p, _ in env["sent"] if m == "POST"]) == 2
    out = recurring.in_nessie("C001")
    assert out["seeded"] and out["total"] == 3585 and {r["status"] for r in out["bills"]} == {"active"}


def test_recurring_route(monkeypatch):
    from paidway.api.main import app
    from paidway.data import seed

    monkeypatch.setattr(seed, "nessie_ids", lambda cid: {})
    r = TestClient(app).get("/api/customers/C001/nessie-bills").json()
    assert r["customer_id"] == "C001" and not r["seeded"] and r["bills"]
    assert {b["kind"] for b in r["bills"]} >= {"debt", "bill"}
