"""The recorder's backend (scripts/demo_worker.py) must never send a Nessie write."""

import importlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))


@pytest.fixture
def worker(monkeypatch):
    from paidway.nessie import _http, read, write

    w = importlib.import_module("demo_worker")  # patches the Nessie client on import
    for m in (_http, read, write):
        m.request = w._real  # undo the import's patch, then patch again so teardown restores the real client
        monkeypatch.setattr(m, "request", w._dry)
    sent = []

    def real(method, path, json=None, params=None):
        sent.append((method, path))
        return [{"_id": "live1"}] if method == "GET" else {"code": 201, "objectCreated": {"_id": "real"}}

    monkeypatch.setattr(w, "_real", real)
    monkeypatch.setattr(w, "NESSIE_KEY", "test")
    monkeypatch.setattr(w, "_made", {})
    return w, sent


def test_writes_are_faked_and_reads_see_them(worker):
    w, sent = worker
    from paidway.nessie import _http, read, write

    assert _http.request is w._dry and write.request is w._dry and read.request is w._dry
    r = write.send({"method": "POST", "path": "/accounts/a1/transfers", "body": {"amount": 5}})
    assert r["objectCreated"]["_id"].startswith("dryrun") and r["objectCreated"]["amount"] == 5
    write.create_deposit("a1", 10, "2026-11-08", "x")
    w._dry("DELETE", "/transfers/t1")
    assert sent == []  # nothing left the process
    got = w._dry("GET", "/accounts/a1/transfers")
    assert sent == [("GET", "/accounts/a1/transfers")] and [g["_id"] for g in got][0] == "live1" and len(got) == 2
