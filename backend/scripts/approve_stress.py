"""Stress the live approve path against Nessie (LIVE writes on the demo customer's mirrors only).

Each case: demo reset, a fresh proposal from the solver, approve, then check every call went through and every
mirror's ledger balance moved as planned. Cases:
  clean      the normal bodies
  malformed  old classic-Nessie bodies (payee_id, medium on a transfer, float amounts, no status): repair path
  retry      the first payoff's deposit leg fails once (network error): partial approve, then retry
  llm        the code repair is switched off, so the model rewrites the body (slow; --llm to include)
The proposals it makes are deleted afterwards and the customer is reset at the end, so the user's own pending
proposal is untouched.

    uv run python scripts/approve_stress.py C001 --runs 3 [--llm]
"""

from __future__ import annotations

import argparse
import time
from contextlib import contextmanager

from paidway.agent import execute
from paidway.agent.validator import validate_plan_v2
from paidway.data import balances, seed, store
from paidway.nessie import calls, repair, write
from paidway.planning import balance_sheet, solver


def fresh_proposal(cid: str) -> str:
    balance_sheet.clear_cache()
    plan = solver.solve(cid, live=True, with_tradeoffs=False)["plan"]
    plan["version"] = 2
    sim = dict(validate_plan_v2(cid, plan))
    sim["balances_snapshot"] = balances.snapshot(cid)
    return store.create_proposal(cid, plan, sim, "approve_stress.py test proposal")["id"]


@contextmanager
def classic_bodies():
    """Transfers and bills built the classic-Nessie way, which this Nessie rejects."""
    t, b = calls.transfer, calls.bill

    def transfer(f, amount, date, desc):
        c = t(f, amount, date, desc)
        c["body"] = {"medium": "balance", "payee_id": "x", "transaction_date": date, "amount": float(amount) + 0.3,
                     "description": desc}
        return c

    def bill(*a):
        c = b(*a)
        c["body"].pop("status")
        c["body"]["payment_amount"] = float(c["body"]["payment_amount"]) + 0.2
        return c

    calls.transfer, calls.bill = transfer, bill
    try:
        yield
    finally:
        calls.transfer, calls.bill = t, b


@contextmanager
def deposit_fails_once():
    send, state = write.send, {"failed": False}

    def flaky(c):
        if not state["failed"] and c["path"].endswith("/deposits"):
            state["failed"] = True
            raise RuntimeError("simulated network error")
        return send(c)

    write.send = flaky
    try:
        yield
    finally:
        write.send = send


@contextmanager
def no_code_repair():
    fix = repair.fix
    repair.fix = lambda body, text, today: (None, [])
    try:
        with classic_bodies():
            yield
    finally:
        repair.fix = fix


def check(p: dict, want_repairs: bool = False) -> list[str]:
    ex = p.get("execution") or {}
    rows = ex.get("nessie_calls") or []
    probs = [f"status {p['status']}"] if p["status"] != "executed" else []
    probs += [f"{r['tool']} {r['status']}: {r.get('error')}" for r in rows if not execute._ok(r)]
    probs += [f"{c['name']} moved {c['actual_change']} not {c['expected_change']}" for c in ex.get("nessie_check") or [] if not c["ok"]]
    if not ex.get("nessie_check"):
        probs.append("no balance check")
    if want_repairs and not any(r.get("repairs") for r in rows):
        probs.append("expected repairs, none logged")
    return probs


def case(cid: str, name: str, made: list[str]) -> tuple[bool, str]:
    seed.reset_customer(cid)
    balance_sheet.clear_cache()
    pid = fresh_proposal(cid)
    made.append(pid)
    t0 = time.time()
    if name == "clean":
        p = execute.approve(pid)
        probs = check(p)
    elif name == "malformed":
        with classic_bodies():
            p = execute.approve(pid)
        probs = check(p, want_repairs=True)
    elif name == "llm":
        with no_code_repair():
            p = execute.approve(pid)
        probs = check(p, want_repairs=True)
    elif name == "retry":
        with deposit_fails_once():
            p = execute.approve(pid)
        first = p["status"]
        held = [r["status"] for r in p["execution"]["nessie_calls"] if not execute._ok(r)]
        probs = [] if first == "approved" and p["execution"].get("retry_available") else [f"first try {first}"]
        p = execute.retry(pid)
        probs += check(p)
        name = f"retry (first try {first}, held back {held})"
    else:
        raise ValueError(name)
    rows = p["execution"].get("nessie_calls") or []
    reps = sorted({n for r in rows for n in r.get("repairs") or []})
    detail = f"{len(rows)} calls, {time.time() - t0:.1f}s" + (f"; repairs: {' | '.join(reps)}" if reps else "")
    return not probs, f"{name}: {'PASS' if not probs else 'FAIL ' + '; '.join(probs)} ({detail})"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("customers", nargs="*", default=["C001"])
    ap.add_argument("--runs", type=int, default=3, help="clean approves per customer")
    ap.add_argument("--llm", action="store_true", help="also run the model-repair case")
    a = ap.parse_args()
    passed = failed = 0
    for cid in a.customers:
        made: list[str] = []
        names = ["clean"] * a.runs + ["malformed", "retry"] + (["llm"] if a.llm else [])
        try:
            for n in names:
                try:
                    ok, line = case(cid, n, made)
                except Exception as e:  # noqa: BLE001
                    ok, line = False, f"{n}: FAIL {type(e).__name__}: {e}"
                passed, failed = passed + ok, failed + (not ok)
                print(f"{cid} {line}", flush=True)
        finally:
            for pid in made:
                store.delete_proposal(pid)
            seed.reset_customer(cid)
            balance_sheet.clear_cache()
    print(f"== {passed} passed, {failed} failed")


if __name__ == "__main__":
    main()
