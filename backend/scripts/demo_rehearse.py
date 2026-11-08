"""Rehearse the demo against a running backend so hidden demo mode (agent/demo.py) can replay every model call.

    cd backend && uv run python scripts/demo_rehearse.py [--customers C001 C027] [--mode record|replay] [--base URL]

Per customer, the demo script: a first run, the chat question "what is income driven repayment" (and its
"Try IDR in my plan" edit when offered), the What-if picks (keep 2 / 3 / 4 months, each undone with Keep previous),
and a check-in for each off-plan Demo-month scenario on the approved plan (tracking reset afterwards).
--mode record (default) calls the model and saves data/demo_cache/<customer>.json. --mode replay times the same
script from the cache and prints the hit/miss counters (/api/_demo); a replay should show no misses.

It leaves what a normal demo leaves: pending proposals. It never approves and never moves Nessie money. Options
it adds (IDR) are put back as they were.
"""

from __future__ import annotations

import argparse
import json
import sys
import time

import httpx

from paidway.planning import options

ASK = "what is income driven repayment"
CUSHIONS = (2, 3, 4)
SCENARIOS = ("overspend", "income_drop", "missed_min", "windfall")


def sse(c: httpx.Client, url: str, **params) -> dict:
    """Read an SSE stream to its `done` (or `error`) event; returns that event."""
    last: dict = {}
    with c.stream("GET", url, params=params, timeout=600) as r:
        r.raise_for_status()
        event = None
        for line in r.iter_lines():
            if line.startswith("event: "):
                event = line[7:]
            elif line.startswith("data: ") and event in ("done", "error"):
                last = {"type": event, **json.loads(line[6:])}
                if event in ("done", "error"):
                    break
    return last


def adjust(c: httpx.Client, pid: str, message: str) -> str | None:
    t = time.monotonic()
    ev = sse(c, f"/api/proposals/{pid}/adjust/stream", message=message)
    new = ev.get("proposal_id") or (ev.get("proposal") or {}).get("id")
    print(f"   edit {message!r}: {ev.get('type')} {new or ev.get('error') or ev.get('clarify') or ''} ({time.monotonic() - t:.1f}s)")
    return new if ev.get("type") == "done" else None


def rehearse(c: httpx.Client, cid: str) -> float:
    t0 = time.monotonic()
    print(f"== {cid}")
    t = time.monotonic()
    ev = sse(c, "/api/agent/stream", customer_id=cid)
    pid = ev.get("proposal_id") or (ev.get("proposal") or {}).get("id")
    print(f"   run: {ev.get('type')} {pid or ev.get('error')} ({time.monotonic() - t:.1f}s)")
    if not pid:
        return time.monotonic() - t0

    t = time.monotonic()
    a = c.post(f"/api/customers/{cid}/ask", json={"message": ASK, "proposal_id": pid}, timeout=300).json()
    print(f"   ask: {a.get('answer', '')[:70]!r}… ({time.monotonic() - t:.1f}s)")
    before = options.applied(cid)
    base = pid
    try:
        f = a.get("followup")
        if f:
            c.post(f"/api/customers/{cid}/options/add", json={"key": f["option"]}, timeout=60).raise_for_status()
            base = adjust(c, pid, f["message"]) or pid

        for n in CUSHIONS:
            new = adjust(c, base, f"Keep {n} months of expenses in savings")
            if new and new != base:
                c.post(f"/api/proposals/{new}/restore", timeout=60)  # Keep previous: base is pending again
    finally:
        options.save_applied(cid, before)

    tr = c.get(f"/api/customers/{cid}/tracking", timeout=60).json()
    if tr.get("active"):
        for s in SCENARIOS:
            t = time.monotonic()
            r = c.post(f"/api/customers/{cid}/tracking/checkin", json={"scenario": s}, timeout=300)
            ci = (r.json() if r.status_code == 200 else {}).get("checkin") or {}
            print(f"   check-in {s}: {r.status_code} {ci.get('drift', '')[:60]!r} ({time.monotonic() - t:.1f}s)")
            c.post(f"/api/customers/{cid}/tracking/reset", timeout=60)
    return time.monotonic() - t0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--customers", nargs="+", default=["C001", "C027"])
    ap.add_argument("--mode", choices=["record", "replay"], default="record")
    ap.add_argument("--base", default="http://127.0.0.1:8710")
    a = ap.parse_args()
    with httpx.Client(base_url=a.base, headers={"X-Paidway-Demo": a.mode}) as c:
        s0 = c.get("/api/_demo").json()["stats"]
        for cid in a.customers:
            print(f"   {cid} total {rehearse(c, cid):.1f}s")
        s1 = c.get("/api/_demo").json()
    diff = {k: v - s0.get(k, 0) for k, v in s1["stats"].items() if v - s0.get(k, 0)}
    print("model calls this rehearsal:", diff, "| recordings:", s1["recordings"])
    return 1 if a.mode == "replay" and diff.get("miss") else 0


if __name__ == "__main__":
    sys.exit(main())
