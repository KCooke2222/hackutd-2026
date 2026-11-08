"""Recorded runs: record -> replay roundtrip, speed scaling, fixture export/import."""

import json
import time

import pytest
from fastapi.testclient import TestClient

from zeroday import api, replays, store


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "t.sqlite3")
    return TestClient(api.app)


def _proposal(cid="C001"):
    return store.create_proposal(cid, {"version": 2, "extra_order": ["X"]},
                                 {"explain": {"headline": "Debt free by May 2027"}}, "pay the card first")


def fake_stream(cid, instruction=None):
    yield {"type": "phase", "phase": "gather"}
    yield {"type": "call", "id": "1", "name": "cash_flow", "label": "Reading cash flow", "why": "need surplus"}
    time.sleep(0.05)
    yield {"type": "result", "id": "1", "result": {"surplus": 812.5}}
    p = _proposal(cid)
    yield {"type": "final", "text": "done", "why": "ok"}
    yield {"type": "done", "messages": [{"role": "ai", "content": "x"}], "proposal_id": p["id"],
           "explain": {"headline": "Debt free by May 2027"}, "final": "done"}


def sse(text):
    out = []
    for block in text.strip().split("\n\n"):
        lines = dict(l.split(": ", 1) for l in block.splitlines())
        out.append((lines["event"], json.loads(lines["data"])))
    return out


def test_record_then_replay_roundtrip(client, monkeypatch):
    import zeroday.agent.graph as graph

    monkeypatch.setattr(graph, "stream_run", fake_stream)
    live = sse(client.get("/api/agent/stream", params={"customer_id": "C001"}).text)
    assert [t for t, _ in live] == ["phase", "call", "result", "final", "done"]
    assert "messages" not in live[-1][1] and live[-1][1]["proposal"]["id"] == live[-1][1]["proposal_id"]

    rs = client.get("/api/replays", params={"customer_id": "C001"}).json()
    assert len(rs) == 1 and rs[0]["headline"] == "Debt free by May 2027"
    assert rs[0]["proposal_id"] == live[-1][1]["proposal_id"] and rs[0]["duration_ms"] >= 50
    assert client.get(f"/api/replays/{rs[0]['id']}").json()["events"] == 5

    again = sse(client.get(f"/api/replays/{rs[0]['id']}/stream", params={"speed": 0}).text)
    assert again == live
    assert client.get("/api/replays", params={"customer_id": "C002"}).json() == []
    assert client.get("/api/replays/nope/stream").status_code == 404


def test_failed_run_not_recorded(client, monkeypatch):
    import zeroday.agent.graph as graph

    monkeypatch.setattr(graph, "stream_run", lambda cid, i=None: iter([{"type": "call", "id": "1", "name": "sql"},
                                                                          {"type": "error", "error": "429"}]))
    client.get("/api/agent/stream", params={"customer_id": "C001"})
    assert store.list_replays() == []


def test_speed_scaling(client):
    evs = [{"t": 0}, {"t": 1000}, {"t": 1000}, {"t": 4000}]
    assert replays.delays(evs, 1) == [0, 1.0, 0, 3.0]
    assert replays.delays(evs, 8) == [0, 0.125, 0, 0.375]
    assert replays.delays(evs, 0) == [0, 0, 0, 0]

    clock = iter([0.0, 0.0, 2.0, 6.0])  # t0, then three events at 0s, 2s, 6s
    list(replays.record("C001", iter([{"type": "call"}, {"type": "result"}, {"type": "done"}]), clock=lambda: next(clock)))
    rid = store.list_replays()[0]["id"]
    slept = []
    list(replays.replay(rid, 4, sleep=slept.append))
    assert slept == [0.5, 1.0]
    t0 = time.monotonic()
    list(replays.replay(rid, 100))
    assert 0.04 < time.monotonic() - t0 < 1


def test_fixture_export_import_restores_proposal(client, tmp_path, monkeypatch):
    import zeroday.agent.graph as graph

    monkeypatch.setattr(graph, "stream_run", fake_stream)
    client.get("/api/agent/stream", params={"customer_id": "C001"})
    r = store.list_replays()[0]
    path = replays.export(r["id"], tmp_path / "replays")
    assert path.name == f"C001-{r['id']}.json"

    # a fresh checkout: empty DB, only the fixture file
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "fresh.sqlite3")
    assert store.get_proposal(r["proposal_id"]) is None
    assert replays.import_dir(tmp_path / "replays") == {"replays": 1, "proposals": 1}
    assert replays.import_dir(tmp_path / "replays") == {"replays": 0, "proposals": 0}
    p = store.get_proposal(r["proposal_id"])
    assert p["status"] == "pending" and p["plan"]["extra_order"] == ["X"]
    done = sse(client.get(f"/api/replays/{r['id']}/stream", params={"speed": 0}).text)[-1][1]
    assert done["proposal"]["id"] == r["proposal_id"]


def test_shipped_fixtures_load(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "t.sqlite3")
    files = list(replays.REPLAY_DIR.glob("*.json"))
    n = replays.import_dir()
    assert n["replays"] == len(files)
    for r in store.list_replays():
        evs = list(replays.replay(r["id"], 0))
        assert evs[-1]["type"] == "done" and evs[-1]["proposal"]
