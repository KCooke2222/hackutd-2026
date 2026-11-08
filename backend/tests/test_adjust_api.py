"""PR 4 routes and the adjust graph's code paths, with a fake LLM (no keys, no network).
The ReAct re-plan itself needs the real model; scripts/eval_agent.py covers it."""

import json

import pytest
from fastapi.testclient import TestClient

from test_wealth import CID, REFERENCE


class FakeMsg:
    type = "ai"
    tool_calls = []
    additional_kwargs = {}

    def __init__(self, content):
        self.content = content


class FakeLLM:
    """Answers each prompt with the next canned reply."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.prompts: list[str] = []

    def invoke(self, messages):
        self.prompts.append(messages[0][1])
        return FakeMsg(json.dumps(self.replies.pop(0)))


@pytest.fixture()
def env(tmp_path, monkeypatch):
    from zeroday import api, balances, store, wealth
    from zeroday.agent import graph

    monkeypatch.setattr(store, "DB_PATH", tmp_path / "t.sqlite3")
    monkeypatch.setattr(balances.read, "enabled", lambda: False)
    sim = wealth.simulate_plan(CID, REFERENCE, live=False)
    parent = store.create_proposal(CID, REFERENCE, {**sim, "explain": {"headline": "old"}}, "first")
    llm = FakeLLM()
    monkeypatch.setattr(graph, "_llm", lambda: llm)
    return {"client": TestClient(api.app), "parent": parent, "llm": llm, "store": store}


def sse(text: str) -> list[dict]:
    out = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        out.append({"event": lines["event"], **json.loads(lines["data"])})
    return out


def test_already_satisfied_rule_skips_the_replan(env):
    env["llm"].replies = [{"constraints": {"forbid_sources": ["cash"]}, "summary": "Don't touch savings",
                           "only_constraints": True, "clarify": None}]
    r = env["client"].get(f"/api/proposals/{env['parent']['id']}/adjust/stream", params={"message": "don't touch my savings"})
    evs = sse(r.text)
    kinds = [e["event"] for e in evs]
    assert "constraints" in kinds and "compare" in kinds and kinds[-1] == "done"
    assert next(e for e in evs if e["event"] == "constraints")["labels"][0]["label"] == "Don't use savings"
    done = evs[-1]
    child = done["proposal"]
    assert child["parent_id"] == env["parent"]["id"] and child["status"] == "pending"
    assert child["constraints"] == {"forbid_sources": ["cash"]} and child["request"] == "don't touch my savings"
    assert child["plan"] == REFERENCE and not done["compare"]["worse"] and done["compare"]["changes"] == []
    assert child["simulation"]["explain"]["headline"].startswith("No change needed")
    assert env["store"].get_proposal(env["parent"]["id"])["status"] == "superseded"
    assert len(env["llm"].prompts) == 1 and "don't touch my savings" in env["llm"].prompts[0]


def test_unclear_request_asks_instead_of_planning(env):
    env["llm"].replies = [{"constraints": {}, "summary": "", "only_constraints": False, "clarify": "Which category?"}]
    r = env["client"].post(f"/api/proposals/{env['parent']['id']}/adjust", json={"message": "cut that one"})
    assert r.status_code == 200 and r.json()["clarify"] == "Which category?" and r.json()["proposal"] is None
    assert env["store"].get_proposal(env["parent"]["id"])["status"] == "pending"  # nothing replaced


def test_adjust_refuses_unknown_and_non_pending(env):
    c = env["client"]
    assert c.post("/api/proposals/nope/adjust", json={"message": "x"}).status_code == 404
    env["store"].set_status(env["parent"]["id"], "rejected")
    r = c.get(f"/api/proposals/{env['parent']['id']}/adjust/stream", params={"message": "x"})
    assert r.status_code == 409


def test_restore_and_remove_rule(env):
    s, c, pid = env["store"], env["client"], env["parent"]["id"]
    child = s.create_proposal(CID, REFERENCE, {}, "c", parent_id=pid, constraints={"forbid_sources": ["cash", "cd"]})
    r = c.post(f"/api/proposals/{child['id']}/constraints/remove", json={"key": "forbid_sources", "item": "cd"})
    assert r.status_code == 200 and r.json()["constraints"] == {"forbid_sources": ["cash"]}
    assert [x["label"] for x in r.json()["simulation"]["constraint_labels"]] == ["Don't use savings"]
    assert c.post(f"/api/proposals/{child['id']}/restore").json()["id"] == pid
    assert c.post(f"/api/proposals/{child['id']}/restore").status_code == 409


def test_preview_enforces_constraints(env):
    c = env["client"]
    r = c.post(f"/api/customers/{CID}/plan/simulate", json={"plan": REFERENCE, "constraints": {"max_redirect": 100}})
    assert r.status_code == 200 and not r.json()["valid"]
    assert any("at most $100" in x for x in r.json()["reasons"])
