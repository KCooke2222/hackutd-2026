"""Background agent runs: survive a dropped client, re-attach replays buffered events then tails live ones."""

import json
import threading

import pytest
from fastapi.testclient import TestClient

from zeroday import api, runs, store


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "t.sqlite3")
    return TestClient(api.app)


def sse(text):
    out = []
    for block in text.strip().split("\n\n"):
        if block.startswith(":"):
            continue
        lines = dict(l.split(": ", 1) for l in block.splitlines())
        out.append((lines["event"], json.loads(lines["data"])))
    return out


def gated(gate: threading.Event):
    def events():
        yield {"type": "call", "id": "1", "name": "cash_flow"}
        gate.wait(5)
        yield {"type": "result", "id": "1", "result": {"surplus": 1}}
        yield {"type": "done", "final": "ok"}
    return events


def test_tail_buffered_then_live():
    gate = threading.Event()
    run = runs.start("C001", gated(gate))
    it = run.tail(poll=0.05)
    assert next(it)["type"] == "call"          # buffered
    assert next(it) is None                    # idle keep-alive while the run waits
    gate.set()
    rest = [e for e in it if e is not None]
    assert [e["type"] for e in rest] == ["result", "done"]
    assert run.status == "done" and run.ended_at is not None
    # a late re-attach gets everything at once and ends
    assert [e["type"] for e in run.tail() if e] == ["call", "result", "done"]
    assert runs.latest("C001") is run and runs.get(run.id) is run


def test_run_survives_dropped_client_and_reattaches(client, monkeypatch):
    import zeroday.agent.graph as graph

    gate = threading.Event()
    steps = gated(gate)
    monkeypatch.setattr(graph, "stream_run", lambda cid, i=None: steps())
    rid = None
    with client.stream("GET", "/api/agent/stream", params={"customer_id": "C001"}) as r:
        for line in r.iter_lines():
            if line.startswith("data: ") and rid is None:
                rid = json.loads(line[6:])["run_id"]
                break  # client goes away mid-run
    assert rid
    meta = client.get("/api/agent/runs/latest", params={"customer_id": "C001"}).json()
    assert meta["run_id"] == rid and meta["status"] == "running"
    gate.set()
    evs = sse(client.get(f"/api/agent/runs/{rid}/stream").text)
    assert [t for t, _ in evs] == ["run", "call", "result", "done"]
    assert client.get(f"/api/agent/runs/{rid}").json()["status"] == "done"


def test_errors_and_unknown_runs(client):
    def boom():
        yield {"type": "call", "id": "1", "name": "sql"}
        raise RuntimeError("model down")

    run = runs.start("C002", boom)
    evs = [e for e in run.tail() if e]
    assert evs[-1]["type"] == "error" and "model down" in evs[-1]["error"] and run.status == "error"
    assert client.get("/api/agent/runs/nope").status_code == 404
    assert client.get("/api/agent/runs/nope/stream").status_code == 404
    assert client.get("/api/agent/runs/latest", params={"customer_id": "C999"}).json() is None
