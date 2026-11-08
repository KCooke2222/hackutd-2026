"""What approve does in Nessie (docs/agent-v3/actions.md): the schedule split, the exact calls, the receipt,
the read-only preview and the demo reset. Every Nessie request is mocked; nothing here reaches Nessie."""

import pytest
from fastapi.testclient import TestClient

CARD, LOAN, STORE, FED = "A-C001-CREDIT_CARD", "A-C001-PERSONAL_LOAN", "L-C001-CREDIT_CARD-2", "L-C001-STUDENT_LOAN-1"
DEBTS = {
    CARD: {"account_id": CARD, "name": "PNC Credit card", "account_type": "credit_card", "source": "nessie"},
    LOAN: {"account_id": LOAN, "name": "PNC Personal loan", "account_type": "personal_loan", "source": "pnc"},
    STORE: {"account_id": STORE, "name": "Store card", "account_type": "credit_card", "source": "linked"},
    FED: {"account_id": FED, "name": "Federal Direct Loan", "account_type": "student_loan", "source": "linked"},
}
PLAN = {"version": 2, "monthly_debt_budget": 2378.29, "extra_order": [STORE, CARD],
        "lump_sums": [{"source": "cash", "amount": 1240.55, "to_debt": STORE},
                      {"source": "cash", "amount": 1935.93, "to_debt": CARD},
                      {"source": "brokerage", "amount": 300.0, "to_debt": CARD}],
        "redirects": [{"flow": "brokerage_auto_invest", "amount_per_month": 225.0}],
        "spending_targets": [{"category": "travel", "current": 711.0, "target": 427.0}],
        "autopay_full_cards": [CARD, STORE], "keep_emergency_months": 3.0}
SIM = {"feasible": True, "problems": [], "net_worth_24m": 1.0, "baseline_net_worth_24m": 0.0,
       "high_cost_debt_free": "2027-05", "interest_total": 10.0, "monthly_card_spending": 1422.79, "months": [],
       "debts": [{"account_id": CARD, "first_month_payment": 2154.05, "payoff_month": "2027-05"},
                 {"account_id": LOAN, "first_month_payment": 29.17, "payoff_month": "2029-01"},
                 {"account_id": STORE, "first_month_payment": 0.0, "payoff_month": "2026-11"},
                 {"account_id": FED, "first_month_payment": 195.07, "payoff_month": None}]}
CHK, SAV, BRK = "A-C001-CHECKING", "A-C001-SAVINGS", "L-C001-BROKERAGE-1"


def _row(aid, t, name, bal, side, source="pnc", **kw):
    return {"account_id": aid, "account_type": t, "name": name, "balance": bal, "side": side, "source": source,
            "institution": "PNC" if source == "pnc" else "Other bank", "cost_basis": None, **kw}


ROWS = [_row(CHK, "checking", "PNC Checking", 1604.0, "asset"), _row(SAV, "savings", "PNC Savings", 28000.0, "asset"),
        _row(CARD, "credit_card", "PNC Credit card", 6571.46, "debt"),
        _row(LOAN, "personal_loan", "PNC Personal loan", 1050.28, "debt"),
        _row(BRK, "brokerage", "Brokerage (index funds)", 9800.0, "asset", "linked", cost_basis=7056.0),
        _row(STORE, "credit_card", "Store card", 1240.55, "debt", "linked"),
        _row(FED, "student_loan", "Federal Direct Loan", 18400.0, "debt", "linked")]
MIRROR = {CHK: "chk1", SAV: "sav1", CARD: "cc1", LOAN: "pl1", BRK: "brk1", STORE: "st1", FED: "fed1"}
NICK = {CHK: "C001 checking", SAV: "C001 savings", CARD: "C001 credit_card", LOAN: "PNC Personal loan",
        BRK: "Brokerage", STORE: "Store card", FED: "Federal Direct Loan"}
IDS = {"customer": "cust1", "checking": "chk1", "savings": "sav1", "credit_card": "cc1", "complete": True,
       "accounts": {k: {"id": v, "nickname": NICK[k]} for k, v in MIRROR.items()}}
GROSS = round(300 / (1 - (9800 - 7056) / 9800 * 0.15), 2)  # $300 net from a brokerage sale, 15% tax on the gain


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """Fake debts, sim and ids; every Nessie request is recorded, none is sent."""
    from paidway.agent import execute, validator
    from paidway.data import balances, store
    from paidway.nessie import _http, read, write
    from paidway.planning import finance

    monkeypatch.setattr(store, "DB_PATH", tmp_path / "t.sqlite3")
    from paidway.planning import balance_sheet

    monkeypatch.setattr(execute, "_debts", lambda cid, live=True: DEBTS)
    monkeypatch.setattr(balance_sheet, "accounts", lambda cid, live=True: [dict(r) for r in ROWS])
    monkeypatch.setattr(execute, "_ids", lambda cid: dict(IDS))
    monkeypatch.setattr(finance, "today", lambda: "2026-11-07")
    monkeypatch.setattr(validator, "validate_plan_v2", lambda cid, plan, **kw: dict(SIM), raising=False)
    monkeypatch.setattr(balances, "snapshot", lambda cid: {"pnc": {"savings": 1.0}, "nessie": None})
    sent, paid = [], []

    def fake_request(method, path, json=None, params=None):
        sent.append((method, path, json))
        if method == "GET":
            return {"balance": 100.0}
        return {"code": 201, "objectCreated": {"_id": f"id{len(sent)}"}}

    for m in (_http, read, write):
        monkeypatch.setattr(m, "request", fake_request)
    monkeypatch.setattr(store, "pay_linked", lambda a, amt: paid.append((a, amt)))
    return {"sent": sent, "paid": paid, "execute": execute, "store": store, "monkeypatch": monkeypatch}


def _proposal(store):
    return store.create_proposal("C001", PLAN, {**SIM, "balances_snapshot": {"pnc": {"savings": 1.0}, "nessie": None}}, "test")


def test_schedule_tags_every_step(env):
    transfers, steps = env["execute"].schedule("C001", PLAN, SIM)
    assert [(t["from"], t["to"], t["to_debt"], t["amount"]) for t in transfers] == [  # old keys kept, one per lump
        ("savings", STORE, STORE, 1240.55), ("savings", "credit_card", CARD, 1935.93), ("brokerage", "credit_card", CARD, 300.0)]
    assert all(s["runs"] in ("nessie", "recorded", "manual") for s in steps)
    by = {(s["kind"], s.get("to_debt") or s.get("account_id") or s.get("flow") or s.get("category"), s.get("source")): s
          for s in steps}
    for key in (("lump_sum", STORE, "cash"), ("lump_sum", CARD, "cash"), ("lump_sum", CARD, "brokerage")):
        assert by[key]["runs"] == "nessie" and by[key]["nessie"]["op"] == "transfer"
    assert "mirrored in Nessie" in by[("lump_sum", STORE, "cash")]["note"]
    sold = by[("lump_sum", CARD, "brokerage")]
    assert sold["nessie"]["from"] == BRK and sold["gross"] == GROSS and sold["nessie"]["tax"] == round(GROSS - 300, 2)
    assert "Brokerage" in sold["note"] and "capital-gains" in sold["note"]
    for k in (CARD, LOAN, FED):  # every debt's monthly payment is a bill on checking, linked ones too
        assert by[("payment", k, None)]["runs"] == "nessie" and by[("payment", k, None)]["nessie"]["from"] == CHK
    assert ("payment", STORE, None) not in by  # $0 month-1 payment: no bill
    assert by[("redirect", "brokerage_auto_invest", None)]["runs"] == "manual"
    assert by[("spending_target", "travel", None)]["runs"] == "recorded"
    auto = by[("autopay", CARD, None)]
    assert auto["runs"] == "nessie" and auto["nessie"]["payment_date"] == "2027-06-07"  # the month after payoff
    assert auto["nessie"]["amount"] == 1422.79
    assert by[("autopay", STORE, None)]["runs"] == "recorded"  # no new charges on the store card: nothing to bill


def test_without_sim_payments_are_not_billed(env):
    _, steps = env["execute"].schedule("C001", PLAN)
    assert not [s for s in steps if s["kind"] == "payment"]
    assert {s["runs"] for s in steps if s["kind"] == "autopay"} == {"recorded"}


def test_nessie_calls_are_exact_and_ordered(env):
    ex = env["execute"]
    calls = ex.nessie_calls("C001", *ex.schedule("C001", PLAN, SIM), IDS)
    assert [(c["method"], c["path"]) for c in calls] == [
        ("POST", "/accounts/sav1/transfers"), ("POST", "/accounts/sav1/transfers"), ("POST", "/accounts/brk1/transfers"),
        ("POST", "/accounts/brk1/withdrawals"),
        *[("POST", "/accounts/chk1/bills")] * 4]
    assert [(c["from"], c["to"], c["amount"]) for c in calls[:4]] == [
        ("PNC Savings", "Store card", 1240.55), ("PNC Savings", "PNC Credit card", 1935.93),
        ("Brokerage (index funds)", "PNC Credit card", 300.0),
        ("Brokerage (index funds)", "Estimated tax / penalty (modelled)", round(GROSS - 300, 2))]
    assert (calls[2]["from_nickname"], calls[2]["to_nickname"]) == ("Brokerage", "C001 credit_card")
    assert calls[0]["body"] == {"medium": "balance", "payee_id": "st1", "transaction_date": "2026-11-07", "amount": 1240.55,
                                "description": "Paidway approved payoff: Store card"}
    assert calls[1]["body"]["payee_id"] == "cc1"
    bill = calls[4]
    assert bill["body"] == {"status": "recurring", "payee": "PNC Credit card", "nickname": "Paidway plan: PNC Credit card",
                            "payment_date": "2026-12-07", "recurring_date": 7, "payment_amount": 2154.05}
    assert bill["recurring_date"] == 7 and bill["tool"] == "nessie.write.create_bill"
    assert [c["to"] for c in calls[4:]] == ["PNC Credit card", "PNC Personal loan", "Federal Direct Loan", "PNC Credit card"]


def test_unseeded_accounts_show_placeholders(env):
    ex = env["execute"]
    calls = ex.nessie_calls("C001", *ex.schedule("C001", PLAN, SIM, ids={}), {})
    assert calls[0]["path"] == f"/accounts/<{SAV}>/transfers" and calls[0]["body"]["payee_id"] == f"<{STORE}>"
    assert all(ex._unseeded(c) for c in calls)


def test_recurring_day_is_clamped(env):
    env["monkeypatch"].setattr("paidway.planning.finance.today", lambda: "2026-01-31")
    first, day = env["execute"]._next_month()
    assert (first, day) == ("2026-02-28", 28)


def test_approve_sends_calls_and_records_receipt(env):
    ex, store = env["execute"], env["store"]
    env["monkeypatch"].setattr(ex, "NESSIE_KEY", "test")
    p = ex.approve(_proposal(store)["id"])
    assert p["status"] == "executed"
    rec = p["execution"]["nessie_calls"]
    posts = [s for s in env["sent"] if s[0] == "POST"]
    assert [(r["method"], r["path"], r["body"]) for r in rec] == posts and len(rec) == 8
    assert all(r["status"] == 201 and r["id"] for r in rec)
    assert {"from", "to", "amount", "recurring_date", "tool"} <= set(rec[0])
    # our store mirrors the linked balances too: the store card and the brokerage come down locally
    assert (STORE, 1240.55) in env["paid"] and (BRK, GROSS) in env["paid"]
    gets = [s[1] for s in env["sent"] if s[0] == "GET"]
    assert set(gets) == {f"/accounts/{MIRROR[k]}" for k in (SAV, STORE, CARD, BRK)} and len(gets) == 8  # before + after
    check = {c["account_id"]: c for c in p["execution"]["nessie_check"]}
    assert check[CARD]["expected_change"] == round(-1935.93 - 300.0, 2) and check[CARD]["actual_change"] == 0.0
    assert check[CARD]["ok"] is False  # the fake Nessie never moved: flagged, not assumed
    assert p["execution"]["nessie_balances_after"][SAV] == 100.0


def test_approve_stops_at_first_failure(env):
    ex, store = env["execute"], env["store"]
    env["monkeypatch"].setattr(ex, "NESSIE_KEY", "test")
    from paidway.nessie import write

    def flaky(method, path, json=None, params=None):
        if path == "/accounts/brk1/transfers":
            raise RuntimeError("Nessie 500")
        return {"code": 201, "objectCreated": {"_id": "x"}}

    env["monkeypatch"].setattr(write, "request", flaky)
    p = ex.approve(_proposal(store)["id"])
    assert p["status"] == "failed"
    assert [r["status"] for r in p["execution"]["nessie_calls"]] == [201, 201, "error", *["not_sent"] * 5]
    assert env["paid"] == [(STORE, 1240.55), (SAV, 1240.55), (CARD, 1935.93), (SAV, 1935.93)]


def test_approve_without_key_sends_nothing(env):
    ex, store = env["execute"], env["store"]
    env["monkeypatch"].setattr(ex, "NESSIE_KEY", "")
    p = ex.approve(_proposal(store)["id"])
    assert p["status"] == "approved" and p["execution"]["mode"] == "local_only"
    assert len(p["execution"]["nessie_pending"]) == 8 and not env["sent"]


def test_preview_makes_no_nessie_call(env):
    from paidway.api import main as api

    p = _proposal(env["store"])
    r = TestClient(api.app).get(f"/api/proposals/{p['id']}/preview")
    assert r.status_code == 200, r.text
    body = r.json()
    assert not env["sent"]  # not even a GET
    assert [c["tool"] for c in body["nessie_calls"]] == [*["nessie.write.create_transfer"] * 3,
                                                         "nessie.write.create_withdrawal", *["nessie.write.create_bill"] * 4]
    assert {s["runs"] for s in body["recorded"]} == {"recorded"} and {s["runs"] for s in body["manual"]} == {"manual"}
    assert env["store"].get_proposal(p["id"])["status"] == "pending"
    # what the preview shows is exactly what approve sends
    env["monkeypatch"].setattr(env["execute"], "NESSIE_KEY", "test")
    sent = env["execute"].approve(p["id"])["execution"]["nessie_calls"]
    assert [(c["method"], c["path"], c["body"]) for c in body["nessie_calls"]] == [(c["method"], c["path"], c["body"]) for c in sent]


def test_demo_reset_recreates_every_mirror(env, tmp_path):
    from paidway.data import seed

    mp = env["monkeypatch"]
    ids_path = tmp_path / "ids.json"
    ids_path.write_text(__import__("json").dumps({"C001": IDS, "_merchants": {"travel": "m1"}}))
    mp.setattr(seed, "IDS_PATH", ids_path)
    mp.setattr(seed, "NESSIE_KEY", "test")
    restored = []
    mp.setattr(env["store"], "replace_linked_for", lambda cid, rows: restored.append(cid) or len(rows))
    seeded = []
    mp.setattr(seed, "seed", lambda cids, reload_local=True: seeded.append((cids, reload_local, seed._load()[cids[0]])) or
               {"C001": {"accounts": {SAV: {"id": "sav2", "nickname": "C001 savings", "balance_nessie": 28000}}}})
    r = seed.reset_customer("C001")
    assert sorted(s[1] for s in env["sent"]) == sorted(f"/accounts/{v}" for v in MIRROR.values())
    assert all(s[0] == "DELETE" for s in env["sent"])
    assert seeded == [(["C001"], False, {"customer": "cust1"})] and restored == ["C001"]  # same Nessie customer
    assert r["deleted_accounts"] == len(MIRROR) and r["mirrors"][SAV]["id"] == "sav2"


def test_seed_mirrors_every_account(env, tmp_path):
    """Seeding a customer seeded before (primary three only) adds a mirror for every other account."""
    from paidway.data import pnc_data, seed

    mp = env["monkeypatch"]
    ids_path = tmp_path / "ids.json"
    old = {k: IDS[k] for k in ("customer", "checking", "savings", "credit_card", "complete")}
    ids_path.write_text(__import__("json").dumps({"C001": old}))
    mp.setattr(seed, "IDS_PATH", ids_path)
    mp.setattr(seed, "NESSIE_KEY", "test")
    mp.setattr(pnc_data, "card_account", lambda cid: None)
    rows = [*ROWS, _row("A-C001-MORTGAGE", "mortgage", "PNC Mortgage", 1000.5, "debt", min_payment=9.0),
            _row("L-C001-PROPERTY-1", "property", "2019 Honda Civic", 11500.0, "asset", "linked")]
    mp.setattr("paidway.planning.balance_sheet.accounts", lambda cid, live=True: [dict(r) for r in rows])
    out = seed.seed(["C001"], reload_local=False)["C001"]
    posts = [(p, b) for m, p, b in env["sent"] if m == "POST"]
    made = {b["nickname"]: b for p, b in posts if p == "/customers/cust1/accounts"}
    assert set(made) == {"PNC Personal loan", "Brokerage", "Store card", "Federal Direct Loan", "PNC Mortgage"}
    assert made["Brokerage"]["type"] == "Savings" and made["Store card"]["type"] == "Credit Card"
    assert made["Federal Direct Loan"]["type"] == "Credit Card" and made["Store card"]["balance"] == 1240.55
    assert any(p.endswith("/loans") and b["type"] == "home" for p, b in posts)  # mortgage: a Nessie home loan too
    assert out["accounts"][SAV]["id"] == "sav1" and out["accounts"][CHK]["id"] == "chk1"  # adopted, not recreated
    assert len(out["accounts"]) == len(rows) - 1 and out["mirrors_complete"]  # property is not mirrored


def test_overlay_reads_every_mirror(monkeypatch):
    from paidway.data import seed
    from paidway.nessie import read
    from paidway.planning import balance_sheet

    monkeypatch.setattr(read, "enabled", lambda: True)
    monkeypatch.setattr(seed, "nessie_ids", lambda cid: IDS)
    monkeypatch.setattr(read, "customer_accounts", lambda c: [{"_id": v, "balance": 7, "nickname": NICK[k]}
                                                               for k, v in MIRROR.items()])
    rows = [dict(r) for r in ROWS]
    balance_sheet._overlay_nessie("C001", rows)
    assert all(r["balance"] == 7.0 and r["mirrored"] for r in rows)
    assert {r["account_id"]: r["source"] for r in rows}[STORE] == "linked"  # still a linked account, Nessie-backed


def test_option_cards_say_how():
    from paidway.planning import options

    assert options.HOW["idr_student_loan"][1] == "https://studentaid.gov/idr/"
    assert all(len(how) < 120 for how, _ in options.HOW.values())
