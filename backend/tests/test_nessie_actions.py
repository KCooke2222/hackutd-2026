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
       "debts": [{"account_id": CARD, "first_month_payment": 2154.05, "payoff_month": "2027-05", "min_payment": 197.14},
                 {"account_id": LOAN, "first_month_payment": 29.17, "payoff_month": "2029-01", "min_payment": 29.17},
                 {"account_id": STORE, "first_month_payment": 0.0, "payoff_month": "2026-11", "min_payment": 40.57},
                 {"account_id": FED, "first_month_payment": 195.07, "payoff_month": None, "min_payment": 195.07}]}
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
GROSS = float(int(300 / (1 - (9800 - 7056) / 9800 * 0.15) + 0.5))  # $300 net from a brokerage sale, 15% tax on the gain
# Nessie moves whole dollars: each payoff is a transfer out of the source plus a deposit (payment) on the debt
CALLS = [("POST", "/accounts/sav1/transfers"), ("POST", "/accounts/st1/deposits"),
         ("POST", "/accounts/sav1/transfers"), ("POST", "/accounts/cc1/deposits"),
         ("POST", "/accounts/brk1/transfers"), ("POST", "/accounts/cc1/deposits"),
         ("POST", "/accounts/brk1/withdrawals"), *[("POST", "/accounts/chk1/bills")] * 3]


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
        if method == "GET" and path.count("/") == 2:
            return {"balance": 100.0}
        if method == "GET":  # a transaction list: what was posted there (this Nessie never moves the balance)
            return [b for m, p, b in sent if m == "POST" and p == path]
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
        ("savings", STORE, STORE, 1241.0), ("savings", "credit_card", CARD, 1936.0), ("brokerage", "credit_card", CARD, 300.0)]
    assert all(s["runs"] in ("nessie", "agent", "manual", "summary") for s in steps)  # nothing left "recorded"
    by = {(s["kind"], s.get("to_debt") or s.get("account_id") or s.get("flow") or s.get("category"), s.get("source")): s
          for s in steps}
    for key in (("lump_sum", STORE, "cash"), ("lump_sum", CARD, "cash"), ("lump_sum", CARD, "brokerage")):
        assert by[key]["runs"] == "nessie" and by[key]["nessie"]["op"] == "transfer"
    assert "mirrored in Nessie" in by[("lump_sum", STORE, "cash")]["note"]
    sold = by[("lump_sum", CARD, "brokerage")]
    assert sold["nessie"]["from"] == BRK and sold["gross"] == GROSS and sold["nessie"]["tax"] == round(GROSS - 300, 2)
    assert "Brokerage" in sold["note"] and "capital-gains" in sold["note"]
    for k in (CARD, LOAN, FED):  # every remaining debt's monthly payment is a bill on checking
        assert by[("payment", k, None)]["runs"] == "nessie" and by[("payment", k, None)]["nessie"]["from"] == CHK
    fed = by[("payment", FED, None)]  # already paying this: the bill takes the payment over
    assert fed["step"] == "Federal Direct Loan $195/mo (minimum)" and fed["replaces"].startswith("Takes over your current $")
    assert ("payment", STORE, None) not in by  # $0 month-1 payment: no bill
    assert by[("redirect", "brokerage_auto_invest", None)]["runs"] == "manual"
    target = by[("spending_target", "travel", None)]
    assert target["runs"] == "agent" and "check-in" in target["watch"]  # the agent watches it, nobody ticks it
    auto = by[("autopay", CARD, None)]
    assert auto["runs"] == "agent" and "not automated yet" in auto["watch"]  # one bill per payee, honest about payoff
    store_ = by[("autopay", STORE, None)]  # paid off today, no charges planned: no fixed bill to make
    assert store_["runs"] == "agent" and store_["step"] == "Store card stays at $0: no new charges planned, so no bill"
    summary = by[("budget", None, None)]
    assert summary["runs"] == "summary" and summary["paid"] == pytest.approx(2154.05 + 29.17 + 195.07)


def test_without_sim_payments_are_not_billed(env):
    _, steps = env["execute"].schedule("C001", PLAN)
    assert not [s for s in steps if s["kind"] == "payment"]
    assert [s["runs"] for s in steps if s["kind"] == "budget"] == ["recorded"]  # nothing sizes the bills
    assert {s["runs"] for s in steps if s["kind"] == "autopay"} == {"agent"}


# C001 as approved: every high-cost debt paid off today, so month 1 pays the card's new purchases, the federal
# minimum and the cents left by whole-dollar payoffs; the rest of the budget stays in cash.
SIM_PAID = {**SIM, "monthly_card_spending": 1422.79,
            "debts": [{"account_id": CARD, "first_month_payment": 1423.26, "payoff_month": "2026-11", "min_payment": 197.14},
                      {"account_id": LOAN, "first_month_payment": 0.28, "payoff_month": "2026-11", "min_payment": 29.17},
                      {"account_id": STORE, "first_month_payment": 0.56, "payoff_month": "2026-11", "min_payment": 40.57},
                      {"account_id": FED, "first_month_payment": 195.07, "payoff_month": None, "min_payment": 195.07}]}


def test_paid_off_today_bills_the_statement_and_labels_the_leftover(env):
    ex = env["execute"]
    plan = {**PLAN, "lump_sums": [], "redirects": []}
    _, steps = ex.schedule("C001", plan, SIM_PAID)
    calls = ex.nessie_calls("C001", [], steps, IDS)
    assert [(c["to"], c["amount"], c["step"]) for c in calls] == [
        ("Federal Direct Loan", 195.0, "Federal Direct Loan $195/mo (minimum)"),
        ("PNC Credit card", 1423.0, "PNC Credit card statement in full ~$1,423/mo: your new purchases, not debt")]
    assert calls[1]["body"]["payee"] == "PNC Credit card (statement in full)" and calls[1]["first_payment"] == "2026-12-07"
    left = next(s for s in steps if s["kind"] == "leftover")  # never shown as "to debt"
    assert left["runs"] == "agent" and left["step"] == "$759/mo left over -> stays in cash"
    summary = next(s for s in steps if s["kind"] == "budget")
    assert summary["net_paydown"] == pytest.approx(1423.26 + 0.28 + 0.56 + 195.07 - 1422.79, abs=0.01)
    assert summary["left_over"] == pytest.approx(759.12, abs=0.01) and summary["step"] == "Debt shrinks $196/mo"
    assert not [s for s in steps if s["runs"] == "recorded"]


def test_nessie_calls_are_exact_and_ordered(env):
    ex = env["execute"]
    calls = ex.nessie_calls("C001", *ex.schedule("C001", PLAN, SIM), IDS)
    assert [(c["method"], c["path"]) for c in calls] == CALLS
    assert [(c["from"], c["to"], c["amount"]) for c in calls[:7:2]] == [
        ("PNC Savings", "Store card", 1241.0), ("PNC Savings", "PNC Credit card", 1936.0),
        ("Brokerage (index funds)", "PNC Credit card", 300.0),
        ("Brokerage (index funds)", "Estimated tax / penalty (modelled)", GROSS - 300)]
    assert [c.get("group") for c in calls[:7]] == ["payoff-0"] * 2 + ["payoff-1"] * 2 + ["payoff-2"] * 3
    assert calls[0]["body"] == {"transaction_date": "2026-11-07", "status": "completed", "amount": 1241,
                                "description": "Paidway payoff to Store card"}
    assert calls[1]["body"] == {"medium": "balance", "transaction_date": "2026-11-07", "status": "completed",
                                "amount": 1241, "description": "Paidway payoff from PNC Savings"}
    assert all(isinstance(c["body"].get("amount", c["body"].get("payment_amount")), int) for c in calls)
    bill = calls[7]
    assert bill["body"] == {"status": "recurring", "payee": "PNC Credit card", "nickname": "Paidway plan: PNC Credit card",
                            "payment_date": "2026-12-07", "recurring_date": 7, "payment_amount": 2154}
    assert bill["recurring_date"] == 7 and bill["tool"] == "nessie.write.create_bill"
    assert [c["to"] for c in calls[7:]] == ["PNC Credit card", "PNC Personal loan", "Federal Direct Loan"]
    assert calls[9]["note"].startswith("Takes over your current $")


def test_unseeded_accounts_show_placeholders(env):
    ex = env["execute"]
    calls = ex.nessie_calls("C001", *ex.schedule("C001", PLAN, SIM, ids={}), {})
    assert calls[0]["path"] == f"/accounts/<{SAV}>/transfers" and calls[1]["path"] == f"/accounts/<{STORE}>/deposits"
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
    assert [(r["method"], r["path"], r["body"]) for r in rec] == posts and len(rec) == len(CALLS)
    assert all(r["status"] == 201 and r["id"] and not r["repairs"] for r in rec)
    assert {"from", "to", "amount", "recurring_date", "tool"} <= set(rec[0])
    # our store mirrors the linked balances too: the store card and the brokerage come down locally
    assert (STORE, 1241.0) in env["paid"] and (BRK, GROSS) in env["paid"]
    gets = {s[1] for s in env["sent"] if s[0] == "GET"}
    assert {f"/accounts/{v}" for v in MIRROR.values()} <= gets  # every mirror read before and after
    check = {c["account_id"]: c for c in p["execution"]["nessie_check"]}
    # this Nessie never posts to the balance: the ledger (raw balance plus Paidway's transactions) moves instead
    assert check[CARD]["expected_change"] == -2236.0 and check[CARD]["actual_change"] == -2236.0 and check[CARD]["ok"]
    assert p["execution"]["nessie_balances_after"][SAV] == 100.0 - 1241 - 1936
    assert [b["payee"] for b in p["execution"]["bills"]] == ["PNC Credit card", "PNC Personal loan", "Federal Direct Loan"]


def test_failed_payoff_is_held_back_and_retried(env):
    ex, store = env["execute"], env["store"]
    env["monkeypatch"].setattr(ex, "NESSIE_KEY", "test")
    from paidway.nessie import write

    real, down = write.request, {"on": True}

    def flaky(method, path, json=None, params=None):
        if down["on"] and path == "/accounts/brk1/transfers":
            raise RuntimeError("Nessie 500")
        return real(method, path, json=json, params=params)

    env["monkeypatch"].setattr(write, "request", flaky)
    pid = _proposal(store)["id"]
    p = ex.approve(pid)
    assert p["status"] == "approved" and p["execution"]["retry_available"]  # tracked: the dashboard is reachable
    assert [r["status"] for r in p["execution"]["nessie_calls"]] == [201] * 4 + ["error", "skipped", "skipped", 201, 201, 201]
    assert (BRK, GROSS) not in env["paid"]  # the failed payoff's legs never ran
    down["on"] = False
    p = ex.retry(pid)
    assert p["status"] == "executed" and all(r["status"] == 201 for r in p["execution"]["nessie_calls"])
    assert [r.get("retried", False) for r in p["execution"]["nessie_calls"]] == [False] * 4 + [True] * 3 + [False] * 3
    assert [s[1] for s in env["sent"] if s[0] == "POST"].count("/accounts/sav1/transfers") == 2  # never sent twice


def test_bad_body_is_reformatted_and_resent(env):
    ex, store = env["execute"], env["store"]
    env["monkeypatch"].setattr(ex, "NESSIE_KEY", "test")
    from paidway.nessie import _http, write

    real = write.request

    def strict(method, path, json=None, params=None):
        if method == "POST" and path.endswith("/transfers") and "status" not in json:
            raise _http.NessieError("400", 400, '"1 validation error for TransferCreate\\nstatus\\n  field required '
                                                '(type=value_error.missing)"')
        return real(method, path, json=json, params=params)

    env["monkeypatch"].setattr(write, "request", strict)
    from paidway.nessie import calls

    orig = calls.transfer

    def no_status(*a):
        c = orig(*a)
        c["body"].pop("status")
        return c

    env["monkeypatch"].setattr(calls, "transfer", no_status)
    p = ex.approve(_proposal(store)["id"])
    assert p["status"] == "executed"
    first = p["execution"]["nessie_calls"][0]
    assert first["repairs"] == ["reformatted after 400: added status=completed"] and first["body"]["status"] == "completed"


def test_approve_without_key_sends_nothing(env):
    ex, store = env["execute"], env["store"]
    env["monkeypatch"].setattr(ex, "NESSIE_KEY", "")
    p = ex.approve(_proposal(store)["id"])
    assert p["status"] == "approved" and p["execution"]["mode"] == "local_only"
    assert len(p["execution"]["nessie_pending"]) == len(CALLS) and not env["sent"]


def test_preview_makes_no_nessie_call(env):
    from paidway.api import main as api

    p = _proposal(env["store"])
    r = TestClient(api.app).get(f"/api/proposals/{p['id']}/preview")
    assert r.status_code == 200, r.text
    body = r.json()
    assert not env["sent"]  # not even a GET
    assert [c["tool"] for c in body["nessie_calls"]] == [*["nessie.write.create_transfer", "nessie.write.create_deposit"] * 3,
                                                         "nessie.write.create_withdrawal", *["nessie.write.create_bill"] * 3]
    assert not body["recorded"] and {s["runs"] for s in body["manual"]} == {"manual"}
    assert {s["kind"] for s in body["agent"]} == {"spending_target", "autopay"} and body["summary"]["kind"] == "budget"
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
    billed = []
    mp.setattr(seed, "seed_bills", lambda cid: billed.append(cid))  # its own test below
    out = seed.seed(["C001"], reload_local=False)["C001"]
    assert billed == ["C001"]  # mirrors done, so the recurring bills are seeded next
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
