"""Background agent runs: a run keeps going when the browser disconnects, and any number of clients can
re-attach to it (buffered events first, then live ones). In memory, per process: a restart forgets them.

Read-only like the agent itself: a run only ever saves a PENDING proposal (agent/tools.py); nothing here moves money.
"""

from __future__ import annotations

import contextvars
import threading
import time
import uuid
from collections.abc import Callable, Iterable, Iterator

KEEP = 20  # finished runs kept per process (oldest dropped first)


class Run:
    def __init__(self, customer_id: str):
        self.id = uuid.uuid4().hex[:12]
        self.customer_id = customer_id
        self.started_at = time.time()
        self.ended_at: float | None = None
        self.status = "running"  # running | done | error
        self.events: list[dict] = []
        self.cond = threading.Condition()

    def meta(self) -> dict:
        return {"run_id": self.id, "customer_id": self.customer_id, "status": self.status,
                "started_at": self.started_at, "ended_at": self.ended_at}

    def _push(self, ev: dict) -> None:
        with self.cond:
            self.events.append(ev)
            self.cond.notify_all()

    def _finish(self, status: str) -> None:
        with self.cond:
            self.status, self.ended_at = status, time.time()
            self.cond.notify_all()

    def tail(self, start: int = 0, poll: float = 10.0) -> Iterator[dict | None]:
        """Every event from `start` on: the buffered ones at once, then live ones as they arrive, until the run ends.
        Yields None every `poll` seconds while idle so the caller can send a keep-alive (and notice a gone client)."""
        i = start
        while True:
            with self.cond:
                if i >= len(self.events) and self.status == "running":
                    self.cond.wait(poll)
                new, finished = self.events[i:], self.status != "running"
            i += len(new)
            yield from new
            if finished and i >= len(self.events):
                return
            if not new:
                yield None


_runs: dict[str, Run] = {}
_latest: dict[str, str] = {}
_lock = threading.Lock()


def start(customer_id: str, events: Callable[[], Iterable[dict]]) -> Run:
    """Start `events()` on a background thread; returns at once with the run (its events fill in as they come)."""
    run = Run(customer_id)
    with _lock:
        _runs[run.id] = run
        _latest[customer_id] = run.id
        done = [r for r in _runs.values() if r.status != "running"]
        for r in sorted(done, key=lambda r: r.started_at)[: max(0, len(done) - KEEP)]:
            _runs.pop(r.id, None)

    def work():
        status = "error"
        try:
            for ev in events():
                run._push(ev)
                if ev.get("type") == "done":
                    status = "done"
        except Exception as e:  # noqa: BLE001 - the run's own failure goes to its listeners
            run._push({"type": "error", "error": f"{type(e).__name__}: {e}"[:400]})
        finally:
            run._finish(status)

    ctx = contextvars.copy_context()  # carries the request's demo mode (agent/demo.py) onto the run's thread
    threading.Thread(target=ctx.run, args=(work,), name=f"agent-run-{run.id}", daemon=True).start()
    return run


def get(run_id: str) -> Run | None:
    return _runs.get(run_id)


def latest(customer_id: str) -> Run | None:
    rid = _latest.get(customer_id)
    return _runs.get(rid) if rid else None
