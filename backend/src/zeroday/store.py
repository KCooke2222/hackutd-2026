"""Our own small state: pending proposals and agent run log. Nessie stays the bank of record."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone

from .config import DB_PATH

SCHEMA = """
create table if not exists proposals (
  id text primary key,
  customer_id text not null,
  status text not null default 'pending',  -- pending | executing | approved | executed | rejected | stale | failed
  created_at text not null,
  decided_at text,
  plan json not null,           -- what the agent proposed (validated)
  simulation json not null,     -- recomputed by the validator, not the LLM
  rationale text,
  execution json
);
create table if not exists agent_runs (
  id text primary key,
  customer_id text not null,
  started_at text not null,
  messages json not null
);
"""


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    return c


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _row(r: sqlite3.Row) -> dict:
    d = dict(r)
    for k in ("plan", "simulation", "execution"):
        if d.get(k):
            d[k] = json.loads(d[k])
    return d


def create_proposal(customer_id: str, plan: dict, simulation: dict, rationale: str) -> dict:
    pid = uuid.uuid4().hex[:12]
    with _conn() as c:
        c.execute(
            "insert into proposals (id, customer_id, created_at, plan, simulation, rationale) values (?,?,?,?,?,?)",
            (pid, customer_id, _now(), json.dumps(plan), json.dumps(simulation), rationale),
        )
    return get_proposal(pid)


def get_proposal(pid: str) -> dict | None:
    with _conn() as c:
        r = c.execute("select * from proposals where id = ?", (pid,)).fetchone()
    return _row(r) if r else None


def list_proposals(customer_id: str | None = None) -> list[dict]:
    with _conn() as c:
        if customer_id:
            rows = c.execute("select * from proposals where customer_id = ? order by created_at desc", (customer_id,)).fetchall()
        else:
            rows = c.execute("select * from proposals order by created_at desc").fetchall()
    return [_row(r) for r in rows]


def set_status(pid: str, status: str, execution: dict | None = None) -> dict | None:
    with _conn() as c:
        c.execute(
            "update proposals set status = ?, decided_at = ?, execution = coalesce(?, execution) where id = ?",
            (status, _now(), json.dumps(execution) if execution is not None else None, pid),
        )
    return get_proposal(pid)


def claim(pid: str) -> bool:
    """Atomically flip pending -> executing. False if someone else already claimed it (double click)."""
    with _conn() as c:
        cur = c.execute("update proposals set status = 'executing' where id = ? and status = 'pending'", (pid,))
        return cur.rowcount == 1


def release(pid: str) -> None:
    """executing -> pending, for a recoverable refusal (e.g. an invalid edit)."""
    with _conn() as c:
        c.execute("update proposals set status = 'pending' where id = ? and status = 'executing'", (pid,))


def log_run(customer_id: str, messages: list[dict]) -> str:
    rid = uuid.uuid4().hex[:12]
    with _conn() as c:
        c.execute("insert into agent_runs (id, customer_id, started_at, messages) values (?,?,?,?)",
                  (rid, customer_id, _now(), json.dumps(messages)))
    return rid
