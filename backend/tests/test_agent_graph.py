"""Offline parts of the agent graph: JSON parsing of model output and the gather snapshot."""

from paidway.agent.graph import gather_snapshot, parse_json


def test_parse_json_handles_think_fences_and_prose():
    assert parse_json('<think>maybe {"x": 0}</think>Sure! {"a": 1, "b": "}"} done') == {"a": 1, "b": "}"}
    assert parse_json('reasoning...</think>\n```json\n{"headline": "ok"}\n```') == {"headline": "ok"}
    assert parse_json('Here: {"causes": [{"label": "x"}], "needs_customer_input": true}') == {"causes": [{"label": "x"}], "needs_customer_input": True}
    assert parse_json("no json here") is None


def test_gather_snapshot_covers_all_debts():
    s = gather_snapshot("C001")
    assert len(s["debts"]) >= 4 and s["cash_flow"].get("saved_per_month") is not None
    assert s["assumptions"].get("prepay_hurdle_pct") == 7.0
