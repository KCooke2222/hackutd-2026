"""PR 4: adjust helpers (compare, plan diff) and proposal lineage in the store. No LLM, no keys."""

import copy

import pytest

from zeroday import store, wealth
from zeroday.agent.adjust import compare, plan_changes

from test_wealth import CID, REFERENCE


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "t.sqlite3")


def sim(nw, interest, free):
    return {"net_worth_24m": nw, "interest_total": interest, "high_cost_debt_free": free, "debts": []}


def test_compare_flags_worse_with_cost_and_reasons():
    c = compare(sim(30000, 900, "2026-03"), sim(29000, 1300, "2026-06"))
    assert c["worse"] and c["cost"] == 1000 and c["net_worth_24m"]["delta"] == -1000
    assert c["high_cost_debt_free"]["months_later"] == 3
    assert c["why"] == ["$1,000 less net worth in 2 years", "high-cost debt $0 3 months later", "$400 more interest"]


def test_compare_small_cost_is_not_worse_and_better_is_free():
    assert not compare(sim(30000, 900, "2026-03"), sim(29900, 910, "2026-03"))["worse"]
    better = compare(sim(30000, 900, "2026-03"), sim(30500, 800, "2026-02"))
    assert not better["worse"] and better["cost"] == 0 and better["why"] == []


def test_compare_never_reaching_zero_is_worse():
    c = compare(sim(30000, 900, "2026-03"), sim(29950, 950, None))
    assert c["worse"] and "no longer reaches $0" in c["why"][0]


def test_plan_changes_lists_each_lever():
    a = copy.deepcopy(REFERENCE)
    b = copy.deepcopy(REFERENCE)
    b["monthly_debt_budget"] = 3800
    b["redirects"][0]["amount_per_month"] = 2000
    b["spending_targets"] = [t for t in b["spending_targets"] if t["category"] != "shopping"]
    b["spending_targets"][0]["target"] = 300
    b["lump_sums"] = [{"source": "cd", "amount": 500, "to_debt": "X"}]
    b["keep_emergency_months"] = 6
    lines = plan_changes(a, b, {"X": "Store card"})
    assert lines == [
        "Paid to debts: $4,300 → $3,800/mo",
        "Redirect unknown transfers: $2,500 → $2,000/mo",
        "CD → Store card: added $500",
        "Dining: $330 → $300/mo",
        "Shopping: no cut (was $330/mo)",
        "Emergency cash: 3 → 6 months",
    ]
    assert plan_changes(a, copy.deepcopy(a)) == []


def test_compare_on_real_simulations():
    s0 = wealth.simulate_plan(CID, REFERENCE, live=False)
    p1 = copy.deepcopy(REFERENCE)
    p1["redirects"][0]["amount_per_month"] = 1000
    p1["monthly_debt_budget"] = REFERENCE["monthly_debt_budget"] - 1500
    s1 = wealth.simulate_plan(CID, p1, live=False)
    c = compare(s0, s1, REFERENCE, p1)
    assert c["net_worth_24m"]["before"] == s0["net_worth_24m"] and c["net_worth_24m"]["after"] == s1["net_worth_24m"]
    assert "Redirect unknown transfers: $2,500 → $1,000/mo" in c["changes"]


def test_adjust_supersedes_parent_and_restore_swaps_back(db):
    parent = store.create_proposal(CID, REFERENCE, {"x": 1}, "first")
    child = store.create_proposal(CID, REFERENCE, {"x": 2}, "second", parent_id=parent["id"],
                                  constraints={"forbid_sources": ["cash"]}, request="don't touch savings")
    assert store.get_proposal(parent["id"])["status"] == "superseded"
    assert child["status"] == "pending" and child["parent_id"] == parent["id"]
    assert child["constraints"] == {"forbid_sources": ["cash"]} and child["request"] == "don't touch savings"
    assert [p["id"] for p in store.list_proposals(CID) if p["status"] == "pending"] == [child["id"]]

    back = store.restore(child["id"])
    assert back["id"] == parent["id"] and back["status"] == "pending"
    assert store.get_proposal(child["id"])["status"] == "superseded"
    assert store.restore(child["id"]) is None  # child no longer pending
    assert store.restore(parent["id"]) is None  # fresh run has no parent


def test_old_database_gets_new_columns(tmp_path, monkeypatch):
    import sqlite3

    path = tmp_path / "old.sqlite3"
    c = sqlite3.connect(path)
    c.execute("create table proposals (id text primary key, customer_id text not null, status text not null default 'pending',"
              " created_at text not null, decided_at text, plan json not null, simulation json not null, rationale text,"
              " execution json)")
    c.commit()
    c.close()
    monkeypatch.setattr(store, "DB_PATH", path)
    p = store.create_proposal(CID, REFERENCE, {}, "r", constraints={"max_redirect": 1})
    assert p["constraints"] == {"max_redirect": 1} and p["parent_id"] is None


def test_second_adjust_of_same_parent_conflicts(db):
    parent = store.create_proposal(CID, REFERENCE, {}, "first")
    store.create_proposal(CID, REFERENCE, {}, "a", parent_id=parent["id"])
    with pytest.raises(store.Conflict):
        store.create_proposal(CID, REFERENCE, {}, "b", parent_id=parent["id"])
    assert len(store.list_proposals(CID)) == 2  # the conflicting insert was rolled back
