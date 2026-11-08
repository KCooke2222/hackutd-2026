"""Goal tracking (tracking.py) on C001: status thresholds, the two re-plan options, seeded demo months."""

import copy

import pytest
from test_wealth import LOAN, PNC_CARD, REFERENCE, STORE

from paidway.data import store
from paidway.planning import tracking as T
from paidway.planning import wealth

CID, PID = "C001", "test-plan"


@pytest.fixture(scope="module")
def setup():
    ctx = wealth.context(CID, keep_months=3, live=False)
    contract = wealth.simulate_plan(CID, REFERENCE, ctx=ctx)
    assert contract["high_cost_debt_free"]
    return REFERENCE, ctx, contract


def run(setup, scenarios, pid=PID):
    plan, ctx, contract = setup
    hist = []
    for s in scenarios:
        hist.append(T.generate_month(CID, pid, plan, ctx, contract["months"], hist, s))
    return hist


def level(setup, hist, proj=None):
    plan, ctx, contract = setup
    proj = proj or T.project(plan, ctx, contract["months"], hist)
    return T.status(plan, ctx, contract, hist, proj)


def on_plan_month(setup, i, **debts):
    """Month i exactly on the contract, with some debt balances overridden."""
    _, ctx, contract = setup
    m = copy.deepcopy(contract["months"][i])
    m["balances"]["debts"].update(debts)
    m = T._totals(m, ctx)
    return {"month_index": i, "scenario": "on_plan", **m, "spend": {}, "events": {}}


# ---------------- status thresholds ----------------

def test_month_zero_on_track(setup):
    st = level(setup, [])
    assert st["level"] == "on_track" and st["late_months"] == 0


def test_late_one_month_drifts_two_months_off_track(setup):
    _, _, contract = setup
    goal = T._idx(contract["high_cost_debt_free"])
    hist = [on_plan_month(setup, 1)]
    base = T.project(setup[0], setup[1], contract["months"], hist)
    assert level(setup, hist, {**base, "high_cost_free": goal})["level"] == "on_track"
    assert level(setup, hist, {**base, "high_cost_free": goal + 1})["level"] == "drifting"
    assert level(setup, hist, {**base, "high_cost_free": goal + 2})["level"] == "off_track"
    assert level(setup, hist, {**base, "high_cost_free": None})["level"] == "off_track"


def test_balance_tolerance_ten_percent(setup):
    _, _, contract = setup
    exp = contract["months"][1]["balances"]["debts"][PNC_CARD]
    assert exp > 1000
    ok = level(setup, [on_plan_month(setup, 1, **{PNC_CARD: exp * 1.09})])
    over = level(setup, [on_plan_month(setup, 1, **{PNC_CARD: exp * 1.11})])
    # Within 10% is never "over"; on the solver plan +9% can still push the payoff a month (drifting).
    assert not ok["over"]
    assert level(setup, [on_plan_month(setup, 1, **{PNC_CARD: exp * 1.05})])["level"] == "on_track"
    assert over["over"] and over["over"][0]["account_id"] == PNC_CARD
    assert over["level"] in ("drifting", "off_track")
    assert any("above plan" in n for n in over["nudges"])


def test_tolerance_ignores_cents_near_zero(setup):
    _, _, contract = setup
    # Store card is $0 by month 1 in the contract; a few dollars over is not drift.
    assert contract["months"][1]["balances"]["debts"][STORE] == 0
    st = level(setup, [on_plan_month(setup, 1, **{STORE: 10.0})])
    assert not st["over"]


def test_hard_breaks_are_off_track(setup):
    m = on_plan_month(setup, 1)
    m["events"] = {"missed_min": {"account_id": LOAN, "name": "PNC Personal loan", "amount": 29.17, "fee": 0.0}}
    assert level(setup, [m])["level"] == "off_track"
    # New debt: a paid-off debt (Store card $0 at month 1) back above $50 the next month.
    hist = [on_plan_month(setup, 1), on_plan_month(setup, 2, **{STORE: 400.0})]
    st = level(setup, hist)
    assert st["level"] == "off_track" and any("New balance" in b for b in st["breaks"])


def test_income_drop_threshold(setup):
    plan, ctx, contract = setup
    hist = [on_plan_month(setup, 1)]
    base = T.project(plan, ctx, contract["months"], hist)
    small = {**base, "mods": {**base["mods"], "income_drop": 0.08, "budget_cut": 700}}
    big = {**base, "mods": {**base["mods"], "income_drop": 0.15, "budget_cut": 1400}}
    assert not level(setup, hist, small)["breaks"]
    assert level(setup, hist, big)["level"] == "off_track"


# ---------------- scenarios ----------------

def test_scenarios_are_deterministic(setup):
    seq = ["on_plan", "overspend", "windfall", "missed_min", "income_drop", "on_plan"]
    a, b = run(setup, seq), run(setup, seq)
    assert a == b
    assert run(setup, ["overspend"], pid="other")[0] != a[0]  # the seed includes the proposal
    assert run(setup, ["on_plan"])[0] != run(setup, ["overspend"])[0]


def test_on_plan_stays_on_track(setup):
    plan, ctx, contract = setup
    hist = run(setup, ["on_plan"] * 6)
    for i in range(1, 7):
        st = level(setup, hist[:i])
        assert st["level"] == "on_track", (i, st)
        assert abs(hist[i - 1]["total_debt"] - contract["months"][i]["total_debt"]) < 0.02 * contract["months"][i]["total_debt"] + 50


def test_scenario_effects(setup):
    _, _, contract = setup
    over = run(setup, ["overspend"])[0]
    assert over["events"]["card_over"] > 100
    assert over["balances"]["debts"][PNC_CARD] > contract["months"][1]["balances"]["debts"][PNC_CARD]
    assert any(s["actual"] > s["target"] for s in over["spend"].values())
    wf = run(setup, ["windfall"])[0]
    assert 1500 <= wf["events"]["windfall"]["amount"] <= 3000
    assert wf["total_debt"] < contract["months"][1]["total_debt"] - 1000
    drop = run(setup, ["income_drop"])[0]
    assert 0.13 <= drop["events"]["income_drop"]["pct"] <= 0.17
    missed = run(setup, ["missed_min"])[0]
    aid = missed["events"]["missed_min"]["account_id"]
    assert missed["balances"]["debts"][aid] > contract["months"][1]["balances"]["debts"][aid]


def test_overspend_drifts_with_nudge(setup):
    hist = run(setup, ["overspend", "overspend"])
    st = level(setup, hist)
    assert st["level"] == "drifting"
    assert any("over target; you hit it in" in n for n in st["nudges"])


# ---------------- re-plan options ----------------

def test_income_drop_options(setup):
    plan, ctx, contract = setup
    hist = run(setup, ["income_drop"])
    assert level(setup, hist)["level"] == "off_track"
    o = T.options(plan, ctx, contract, hist)
    lock, move = o["options"]
    assert [lock["key"], move["key"]] == ["lock_in", "move_date"]
    # Lock-in can't cover a ~15% income drop from cuts and flows: says how much it would take.
    assert lock["feasible"] is False and lock["needed"] > lock["available"]
    # Same effort (budget cut by the drop) -> a later month, or past the horizon (then a full re-plan).
    if move["feasible"]:
        assert move["months_later"] >= 1 and move["new_month"] > contract["high_cost_debt_free"]
        assert move["monthly_debt_budget"] < plan["monthly_debt_budget"]
    else:
        assert "horizon" in move["why"] or "buffer" in move["why"]
    assert o["replan"] is (not move["feasible"])


def test_lock_in_keeps_the_date(setup):
    plan, ctx, contract = setup
    hist = run(setup, ["overspend", "overspend", "missed_min"])
    assert level(setup, hist)["level"] == "off_track"
    lock, move = T.options(plan, ctx, contract, hist)["options"]
    assert lock["feasible"]
    kinds = [s["kind"] for s in lock["sources"]]
    assert kinds[0] == "card_cap"  # the leak is stopped first
    assert lock["extra_per_month"] >= lock["sources"][0]["amount"] - 1
    assert lock["projected_month"] <= contract["high_cost_debt_free"]
    assert lock["steps"] and "Autopay" in lock["steps"][0]
    # Every cut lock-in adds stays at or above the customer's own lowest month (the plan's own cancels may not).
    before = {t["category"]: t["target"] for t in plan["spending_targets"]}
    for t in lock["plan"]["spending_targets"]:
        if before.get(t["category"]) != t["target"]:
            assert t["target"] >= ctx["spend"][t["category"]]["min"] - 0.5
    assert move["feasible"]


def test_lock_in_needs_extra_when_late(setup):
    """Force lateness (a big new card balance): lock-in must add money and land on the goal month."""
    plan, ctx, contract = setup
    hist = run(setup, ["on_plan"])
    hist[0]["balances"]["debts"][PNC_CARD] += 900
    hist[0] = {**hist[0], **T._totals(hist[0], ctx)}
    proj = T.project(plan, ctx, contract["months"], hist)
    goal = T._idx(contract["high_cost_debt_free"])
    o = T.options(plan, ctx, contract, hist)["options"][0]
    if proj["high_cost_free"] <= goal:
        assert o["feasible"] and o["extra_per_month"] == 0
    else:
        assert (not o["feasible"]) or (o["extra_per_month"] > 0 and o["projected_month"] <= contract["high_cost_debt_free"])


# ---------------- storage + choose (temp DB) ----------------

def test_step_reset_choose_pending(setup, tmp_path, monkeypatch):
    from paidway.data import balances

    monkeypatch.setattr(store, "DB_PATH", tmp_path / "t.sqlite3")
    monkeypatch.setattr(balances, "snapshot", lambda cid: {"pnc": {}, "nessie": None})
    monkeypatch.setattr(T, "_CTX", {})
    plan, _, contract = setup
    p = store.create_proposal(CID, plan, contract, "test")
    store.set_status(p["id"], "approved")
    prop = store.get_proposal(p["id"])
    assert T.approved_proposal(CID)["id"] == prop["id"]

    d = T.step(CID, prop, "income_drop", live=False)
    assert d["month_index"] == 1 and d["status"]["level"] == "off_track"
    assert len(d["switch"]["options"]) == 2 and all("plan" not in o for o in d["switch"]["options"])
    assert d["alerts"][0]["title"] == "Off track"
    with pytest.raises(ValueError):
        T.choose(CID, prop, "lock_in", live=False)  # infeasible after an income drop
    T.reset(CID, prop["id"])
    d = T.step(CID, prop, "missed_min", live=False)
    assert d["status"]["level"] == "off_track"
    new = T.choose(CID, prop, "move_date", live=False)
    assert new["status"] == "pending" and new["id"] != prop["id"]
    assert new["simulation"]["tracking"]["from_proposal"] == prop["id"]
    assert store.get_proposal(prop["id"])["status"] == "approved"  # the tracked plan is untouched
    T.reset(CID, prop["id"])
    assert T.history(CID, prop["id"]) == []


def test_alerts_collapse_repeats():
    from paidway.planning.tracking import _collapse
    feed = [{"month_index": i, "month": f"2027-0{i}", "scenario": s, "level": lv, "title": t, "text": x}
            for i, (s, lv, t, x) in enumerate([
                ("on_plan", "on_track", "On plan", "Debt $3"), ("on_plan", "on_track", "On plan", "Debt $2"),
                ("income_drop", "off_track", "Off track", "Income down 14%"),
                ("income_drop", "off_track", "Off track", "Income down 14%"),
                ("income_drop", "off_track", "Off track", "Income down 14%")], start=1)]
    actual = months = [{"cash": 100.0 * i} for i in range(7)]
    out = _collapse(feed, actual, months)
    assert [(a["title"], a["month"], a["from_month"]) for a in out] == [
        ("On plan", "2027-02", "2027-01"), ("Off track", "2027-03", None), ("Off track", "2027-05", None)]
    assert out[1]["text"] == "Income down 14%" and out[2]["text"].startswith("Still, 2 months later")
