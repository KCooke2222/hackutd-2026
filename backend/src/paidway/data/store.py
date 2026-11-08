"""Our own small state: proposals, agent run log, linked external accounts. Nessie stays the bank of record."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone

from ..config import DB_PATH

SCHEMA = """
create table if not exists proposals (
  id text primary key,
  customer_id text not null,
  status text not null default 'pending',  -- pending | executing | approved | executed | rejected | stale | failed | superseded
  created_at text not null,
  decided_at text,
  plan json not null,           -- what the agent proposed (validated)
  simulation json not null,     -- recomputed by the validator, not the LLM
  rationale text,
  execution json,
  parent_id text,               -- the proposal this one adjusts ("talk to the plan"); null for a fresh run
  constraints json,             -- the customer's accumulated rules (agent/constraints.py)
  request text                  -- what the customer asked for, for the adjust history
);
create table if not exists agent_runs (
  id text primary key,
  customer_id text not null,
  started_at text not null,
  messages json not null
);
-- Accounts Nessie can't hold (student loans, brokerage, property, other banks' cards). See linked.py.
create table if not exists linked_accounts (
  account_id text primary key,
  customer_id text not null,
  account_type text not null,   -- student_loan | brokerage | property | credit_card
  kind text,                    -- federal/private, taxable, home/vehicle, store
  name text not null,
  institution text,
  balance real not null,        -- debt: amount owed; asset: market value
  apr_pct real not null default 0,
  annual_yield_pct real not null default 0,
  min_payment real not null default 0,
  cost_basis real,              -- brokerage only
  secured integer not null default 0,
  consequence_tier integer,     -- debts only: 1 secured, 2 federal student loan, 3 unsecured
  secures_account_id text,      -- property backing a PNC mortgage / auto loan
  source text not null default 'linked',
  as_of_date text
);
create index if not exists linked_accounts_customer on linked_accounts (customer_id);
-- Recorded agent runs (the SSE event stream with time offsets), replayable without calling the model. See replays.py.
create table if not exists run_replays (
  id text primary key,
  customer_id text not null,
  recorded_at text not null,
  duration_ms integer not null,
  model text,
  headline text,
  proposal_id text,
  proposal json,                -- the proposal as it was when the run finished (restored on import)
  events json not null          -- [{"t": ms since start, "type": str, "data": {...}}]
);
"""

LINKED_COLS = [
    "account_id", "customer_id", "account_type", "kind", "name", "institution", "balance", "apr_pct",
    "annual_yield_pct", "min_payment", "cost_basis", "secured", "consequence_tier", "secures_account_id",
    "source", "as_of_date",
]


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    have = {r[1] for r in c.execute("pragma table_info(proposals)")}
    for col, typ in (("parent_id", "text"), ("constraints", "json"), ("request", "text")):
        if col not in have:  # databases created before PR 4
            c.execute(f"alter table proposals add column {col} {typ}")
    return c


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _row(r: sqlite3.Row) -> dict:
    d = dict(r)
    for k in ("plan", "simulation", "execution", "constraints"):
        if d.get(k):
            d[k] = json.loads(d[k])
    return d


class Conflict(RuntimeError):
    """The proposal being adjusted is no longer pending (approved, rejected or adjusted meanwhile)."""


def create_proposal(customer_id: str, plan: dict, simulation: dict, rationale: str, *, parent_id: str | None = None,
                    constraints: dict | None = None, request: str | None = None) -> dict:
    """Save a pending proposal. With parent_id (an adjustment), the parent goes pending -> superseded in the
    same transaction, so the customer has one pending plan at a time."""
    pid = uuid.uuid4().hex[:12]
    with _conn() as c:
        c.execute(
            "insert into proposals (id, customer_id, created_at, plan, simulation, rationale, parent_id, constraints, request)"
            " values (?,?,?,?,?,?,?,?,?)",
            (pid, customer_id, _now(), json.dumps(plan), json.dumps(simulation), rationale, parent_id,
             json.dumps(constraints) if constraints else None, request),
        )
        if parent_id:
            cur = c.execute("update proposals set status = 'superseded', decided_at = ? where id = ? and status = 'pending'",
                            (_now(), parent_id))
            if cur.rowcount != 1:  # raising inside the `with` rolls back the insert too
                raise Conflict(f"proposal {parent_id} is no longer pending")
    return get_proposal(pid)


def restore(pid: str) -> dict | None:
    """"Keep previous": the adjusted child becomes superseded and its parent is pending again. Only from the
    pending child of a superseded parent; returns the parent, or None if that isn't the state."""
    with _conn() as c:
        r = c.execute("select parent_id, status from proposals where id = ?", (pid,)).fetchone()
        if not r or not r["parent_id"] or r["status"] != "pending":
            return None
        cur = c.execute("update proposals set status = 'pending', decided_at = null where id = ? and status = 'superseded'",
                        (r["parent_id"],))
        if cur.rowcount != 1:
            return None
        c.execute("update proposals set status = 'superseded', decided_at = ? where id = ?", (_now(), pid))
    return get_proposal(r["parent_id"])


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


def set_execution(pid: str, execution: dict) -> dict | None:
    """Replace a proposal's execution record, keeping its status and decided_at (check-in bill upkeep)."""
    with _conn() as c:
        c.execute("update proposals set execution = ? where id = ?", (json.dumps(execution), pid))
    return get_proposal(pid)


def annotate(pid: str, **fields) -> dict | None:
    """Merge display fields (e.g. explain labels) into a proposal's simulation json."""
    with _conn() as c:
        r = c.execute("select simulation from proposals where id = ?", (pid,)).fetchone()
        if not r:
            return None
        sim = json.loads(r["simulation"] or "{}")
        sim.update(fields)
        c.execute("update proposals set simulation = ? where id = ?", (json.dumps(sim), pid))
    return get_proposal(pid)


def set_constraints(pid: str, constraints: dict | None) -> dict | None:
    """Replace a pending proposal's constraints (the customer removed a rule tag)."""
    with _conn() as c:
        c.execute("update proposals set constraints = ? where id = ? and status = 'pending'",
                  (json.dumps(constraints) if constraints else None, pid))
    return get_proposal(pid)


def claim(pid: str) -> bool:
    """Atomically flip pending -> executing. False if someone else already claimed it (double click)."""
    with _conn() as c:
        cur = c.execute("update proposals set status = 'executing' where id = ? and status = 'pending'", (pid,))
        return cur.rowcount == 1


def claim_retry(pid: str) -> bool:
    """Atomically flip approved -> executing for a retry of failed Nessie calls (double clicks get False)."""
    with _conn() as c:
        cur = c.execute("update proposals set status = 'executing' where id = ? and status = 'approved'", (pid,))
        return cur.rowcount == 1


def delete_proposal(pid: str) -> None:
    """Remove one proposal row (scripts/approve_stress.py cleans up the proposals it made)."""
    with _conn() as c:
        c.execute("delete from proposals where id = ?", (pid,))


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


def replace_linked_accounts(rows: list[dict]) -> int:
    with _conn() as c:
        c.execute("delete from linked_accounts")
        c.executemany(
            f"insert into linked_accounts ({','.join(LINKED_COLS)}) values ({','.join('?' * len(LINKED_COLS))})",
            [tuple(r.get(k) for k in LINKED_COLS) for r in rows],
        )
    return len(rows)


def replace_linked_for(customer_id: str, rows: list[dict]) -> int:
    """Replace one customer's linked accounts (demo reset puts back what approve paid down)."""
    with _conn() as c:
        c.execute("delete from linked_accounts where customer_id = ?", (customer_id,))
        c.executemany(
            f"insert into linked_accounts ({','.join(LINKED_COLS)}) values ({','.join('?' * len(LINKED_COLS))})",
            [tuple(r.get(k) for k in LINKED_COLS) for r in rows],
        )
    return len(rows)


def pay_linked(account_id: str, amount: float) -> None:
    """Lower a linked account's balance after approve moved money for it (a debt paid, brokerage sold)."""
    with _conn() as c:
        c.execute("update linked_accounts set balance = max(balance - ?, 0) where account_id = ?", (amount, account_id))


def linked_count() -> int:
    with _conn() as c:
        return c.execute("select count(*) from linked_accounts").fetchone()[0]


def linked_accounts(customer_id: str | None = None) -> list[dict]:
    with _conn() as c:
        if customer_id:
            rows = c.execute("select * from linked_accounts where customer_id = ? order by account_type, account_id", (customer_id,)).fetchall()
        else:
            rows = c.execute("select * from linked_accounts order by customer_id, account_type, account_id").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["secured"] = bool(d["secured"])
        out.append(d)
    return out


# --- recorded runs (replays.py) ---

REPLAY_COLS = ["id", "customer_id", "recorded_at", "duration_ms", "model", "headline", "proposal_id", "proposal", "events"]


def save_replay(r: dict) -> dict:
    """Insert a recorded run; ignored if that id already exists (fixture import is idempotent)."""
    row = {**r, "proposal": json.dumps(r.get("proposal")) if r.get("proposal") is not None else None,
           "events": json.dumps(r["events"], default=str)}
    with _conn() as c:
        c.execute(f"insert or ignore into run_replays ({','.join(REPLAY_COLS)}) values ({','.join('?' * len(REPLAY_COLS))})",
                  tuple(row.get(k) for k in REPLAY_COLS))
    return get_replay(r["id"])


def get_replay(rid: str) -> dict | None:
    with _conn() as c:
        r = c.execute("select * from run_replays where id = ?", (rid,)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["events"] = json.loads(d["events"])
    d["proposal"] = json.loads(d["proposal"]) if d["proposal"] else None
    return d


def list_replays(customer_id: str | None = None) -> list[dict]:
    q = "select id, customer_id, recorded_at, duration_ms, model, headline, proposal_id from run_replays"
    with _conn() as c:
        if customer_id:
            rows = c.execute(q + " where customer_id = ? order by recorded_at desc", (customer_id,)).fetchall()
        else:
            rows = c.execute(q + " order by recorded_at desc").fetchall()
    return [dict(r) for r in rows]


def restore_proposal(p: dict) -> bool:
    """Put a proposal from a replay fixture back, as it was recorded. No-op if that id exists. True if inserted."""
    cols = ["id", "customer_id", "status", "created_at", "decided_at", "plan", "simulation", "rationale", "execution"]
    vals = [p.get(k) for k in cols]
    for i, k in enumerate(cols):
        if k in ("plan", "simulation", "execution") and vals[i] is not None and not isinstance(vals[i], str):
            vals[i] = json.dumps(vals[i])
    vals[2] = vals[2] or "pending"
    with _conn() as c:
        cur = c.execute(f"insert or ignore into proposals ({','.join(cols)}) values ({','.join('?' * len(cols))})", vals)
        return cur.rowcount == 1
