"""Recorded agent runs: save a live run's SSE events with time offsets, replay them at N× speed.

Live runs take minutes on Nemotron; UI work and the demo replay a recorded one instead. Each run is stored
in SQLite (`run_replays`) and can be exported to `data/replays/<customer>-<run_id>.json` (committed fixtures),
which are imported on startup so any checkout has them. A fixture carries its proposal, so replay works even
when that proposal isn't in the local DB.

    uv run python scripts/record_runs.py --customer C001 --runs 2   # record live runs + write fixtures
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator

from ..config import BACKEND_DIR
from ..data import store

REPLAY_DIR = BACKEND_DIR / "data" / "replays"


def live_events(customer_id: str, instruction: str | None = None) -> Iterator[dict]:
    """One live agent run as UI events (what /api/agent/stream sends): logs messages, attaches the proposal to `done`."""
    from .graph import stream_run

    for ev in stream_run(customer_id, instruction):
        msgs = ev.pop("messages", None)
        if msgs:
            store.log_run(customer_id, msgs)
        if ev["type"] == "done":
            pid = ev.get("proposal_id")
            p = store.get_proposal(pid) if pid else None
            if p is None:
                ps = store.list_proposals(customer_id)
                p = next((x for x in ps if x["status"] == "pending"), None)
            ev["proposal"] = p
        yield ev


def _headline(done: dict) -> str:
    explain = done.get("explain") or (((done.get("proposal") or {}).get("simulation") or {}).get("explain")) or {}
    return str(explain.get("headline") or (done.get("proposal") or {}).get("rationale") or done.get("final") or "")[:200]


def record(customer_id: str, events: Iterable[dict], model: str = "", clock=time.monotonic) -> Iterator[dict]:
    """Pass events through unchanged while recording them. A run that ends in `done` is saved; errors are not."""
    t0 = clock()
    log: list[dict] = []
    for ev in events:
        log.append({"t": int((clock() - t0) * 1000), "type": ev["type"], "data": ev})
        yield ev
        if ev["type"] == "done":
            store.save_replay({
                "id": uuid.uuid4().hex[:12], "customer_id": customer_id,
                "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "duration_ms": log[-1]["t"], "model": model, "headline": _headline(ev),
                "proposal_id": (ev.get("proposal") or {}).get("id") or ev.get("proposal_id"),
                "proposal": ev.get("proposal"), "events": log,
            })


def last_recorded(customer_id: str) -> dict | None:
    rs = store.list_replays(customer_id)
    return store.get_replay(rs[0]["id"]) if rs else None


def delays(events: list[dict], speed: float) -> list[float]:
    """Seconds to wait before each event at `speed`× (0 = instant)."""
    if speed <= 0:
        return [0.0] * len(events)
    out, prev = [], 0
    for e in events:
        out.append(max(0, e["t"] - prev) / 1000 / speed)
        prev = max(prev, e["t"])
    return out


def replay(rid: str, speed: float, sleep=time.sleep) -> Iterator[dict]:
    """The recorded events in order, paced at `speed`×. `done` gets the proposal's current state if it's still in the DB."""
    r = store.get_replay(rid)
    if r is None:
        raise KeyError(rid)
    for wait, e in zip(delays(r["events"], speed), r["events"]):
        if wait:
            sleep(wait)
        ev = dict(e["data"])
        if ev.get("type") == "done":
            pid = r.get("proposal_id")
            ev["proposal"] = (store.get_proposal(pid) if pid else None) or r.get("proposal")
        yield ev


def export(rid: str, out_dir: Path = REPLAY_DIR) -> Path:
    r = store.get_replay(rid)
    if r is None:
        raise KeyError(rid)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{r['customer_id']}-{rid}.json"
    path.write_text(json.dumps(r, indent=1, default=str) + "\n")
    return path


def import_dir(src: Path = REPLAY_DIR) -> dict:
    """Load every fixture in `src` into SQLite (idempotent); restores each fixture's proposal if missing."""
    added = restored = 0
    for f in sorted(Path(src).glob("*.json")) if Path(src).is_dir() else []:
        r = json.loads(f.read_text())
        if store.get_replay(r["id"]) is None:
            store.save_replay(r)
            added += 1
        if r.get("proposal") and store.restore_proposal(r["proposal"]):
            restored += 1
    return {"replays": added, "proposals": restored}
