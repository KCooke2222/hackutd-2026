"""Check-in re-plan (agent/replan.py): an off-track check-in runs the planning agent from the approved plan,
with the lower income applied to what its tools see. The model is replaced by a fake stream_adjust."""

import pytest
from test_wealth import REFERENCE

from paidway.agent import checkin as CI
from paidway.agent import graph, replan
from paidway.data import store
from paidway.planning import tracking as T
from paidway.planning import wealth

CID = "C001"


@pytest.fixture()
def approved(tmp_path, monkeypatch):
    from paidway.data import balances

    monkeypatch.setattr(store, "DB_PATH", tmp_path / "t.sqlite3")
    monkeypatch.setattr(balances, "snapshot", lambda cid: {"pnc": {}, "nessie": None})
    monkeypatch.setattr(T, "_CTX", {})
    ctx = wealth.context(CID, keep_months=3, live=False)
    contract = wealth.simulate_plan(CID, REFERENCE, ctx=ctx)
    p = store.create_proposal(CID, REFERENCE, contract, "test")
    store.set_status(p["id"], "approved")
    seen: list = []

    def fake_adjust(pid, request, rp=None):
        at = (wealth.AT_CHECKIN.get() or {}).get(CID) or {}
        c = wealth.context(CID, keep_months=3, live=False)
        seen.append({"pid": pid, "request": request, "replan": rp, "cut": at.get("income_cut"), "income": c["flows"]["income"],
                     "cash": c["assets"]["cash"], "debts": {d["account_id"]: d["balance"] for d in c["debts"]},
                     "max": wealth.budget_limit(c, REFERENCE)["max_monthly_debt_budget"]})
        yield {"type": "phase", "phase": "plan"}
        new = store.create_proposal(CID, REFERENCE, contract, "re-plan", parent_id=pid, supersede=False)
        yield {"type": "done", "proposal_id": new["id"], "explain": {"headline": "Lower income plan"},
               "compare": {"changes": ["budget down"]}, "messages": [{"role": "assistant", "content": "ok"}]}

    monkeypatch.setattr(graph, "stream_adjust", fake_adjust)
    return {"prop": store.get_proposal(p["id"]), "seen": seen, "income": ctx["flows"]["income"]}


def test_replan_plans_for_the_lower_income(approved):
    prop = approved["prop"]
    t = T.step(CID, prop, "income_drop", live=False)
    assert t["status"]["level"] == "off_track"
    r = replan.run(CID, prop, t)
    assert r["type"] == "done" and "messages" not in r
    s = approved["seen"][0]
    drop = s["replan"]["income_cut"]
    assert s["pid"] == prop["id"] and drop > 0 and s["cut"] == drop
    assert s["income"] == pytest.approx(approved["income"] - drop)  # the agent's tools see the lower income
    assert "income down" in s["request"] and s["replan"]["checkin"]["income_drop_per_month"] == drop
    assert wealth.AT_CHECKIN.get() is None  # only inside the run
    state = T._state(T.history(CID, prop["id"])[-1])  # ...and the tools see this month's balances
    assert s["cash"] == pytest.approx(state["assets"]["cash"], abs=0.05)
    assert all(s["debts"][k] == pytest.approx(v) for k, v in state["debts"].items() if k in s["debts"])
    c0 = wealth.context(CID, keep_months=3, live=False)
    assert s["max"] < wealth.budget_limit(c0, REFERENCE)["max_monthly_debt_budget"]
    new = store.get_proposal(r["proposal_id"])
    assert new["status"] == "pending" and new["parent_id"] == prop["id"]
    assert store.get_proposal(prop["id"])["status"] == "approved"  # stays approved until the new one is


def test_off_track_checkin_replans_by_default(approved, monkeypatch):
    monkeypatch.setattr(CI, "_llm", lambda: None)
    monkeypatch.setattr(CI, "_ask_json", lambda llm, prompt: ({"drift": "Income fell", "pick": "move_date", "why": "x"}, {}))
    monkeypatch.setattr(T, "step", lambda cid, prop, scenario, live=True, _s=T.step: _s(cid, prop, scenario, live=False))
    out = CI.checkin(CID, "income_drop", approved["prop"])
    assert out["level"] == "off_track" and out["pick"] == "replan"
    assert out["replan"]["headline"] == "Lower income plan" and out["proposal_id"] == out["replan"]["proposal_id"]


def test_adjustable_replan_needs_the_approved_plan(approved):
    pending = store.create_proposal(CID, REFERENCE, {}, "x")
    with pytest.raises(graph.AdjustError):
        graph.adjustable(pending["id"], replan=True)
    assert graph.adjustable(approved["prop"]["id"], replan=True)["id"] == approved["prop"]["id"]
