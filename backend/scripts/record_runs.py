"""Record live agent runs and write them as replay fixtures (backend/data/replays/<customer>-<id>.json).

    cd backend && uv run python scripts/record_runs.py [--customer C001] [--runs 1] [--replace]
    make record CUSTOMER=C001 RUNS=1

Fixtures capture the data the run saw: re-record after the seed data or the agent changes.
--replace deletes that customer's existing fixture files first. --export-only writes fixtures for
runs already recorded in SQLite (e.g. ones made through the UI) without calling the model.
"""

from __future__ import annotations

import argparse
import sys

from zeroday import replays, store
from zeroday.config import NEMOTRON_MODEL, NVIDIA_API_KEY


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--customer", default="C001")
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--replace", action="store_true")
    ap.add_argument("--export-only", action="store_true")
    a = ap.parse_args()
    cid = a.customer

    if a.export_only:
        for r in store.list_replays(cid):
            print(replays.export(r["id"]))
        return 0
    if not NVIDIA_API_KEY:
        print("NVIDIA_API_KEY is not set (backend/.env)", file=sys.stderr)
        return 1
    if a.replace:
        for f in replays.REPLAY_DIR.glob(f"{cid}-*.json"):
            f.unlink()
            print(f"removed {f.name}")
    ok = 0
    for i in range(a.runs):
        before = {r["id"] for r in store.list_replays(cid)}
        last = None
        for ev in replays.record(cid, replays.live_events(cid), model=NEMOTRON_MODEL):
            last = ev
            if ev["type"] in ("call", "phase"):
                print(f"  {ev['type']:5s} {ev.get('label') or ev.get('name') or ev.get('phase') or ''}", flush=True)
        new = [r for r in store.list_replays(cid) if r["id"] not in before]
        if not new:
            print(f"run {i + 1}: not recorded (last event: {last and last['type']}: {str(last)[:200]})")
            continue
        r = new[0]
        print(f"run {i + 1}: {r['duration_ms'] / 1000:.0f}s  {r['headline']!r}  -> {replays.export(r['id'])}")
        ok += 1
    return 0 if ok == a.runs else 1


if __name__ == "__main__":
    sys.exit(main())
