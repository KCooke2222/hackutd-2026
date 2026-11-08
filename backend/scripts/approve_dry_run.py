"""Print the exact Nessie calls approving a fresh plan would make for a customer. Sends nothing.

Builds the plan the solver proposes on local balances, splits it with execute.schedule and prints
execute.nessie_calls with the real Nessie account ids from data/nessie_ids.json. Any Nessie request
(even a GET) raises, so a dry run can't touch Nessie.

    uv run python scripts/approve_dry_run.py C001
"""

from __future__ import annotations

import json
import sys

from paidway.nessie import _http


def _blocked(method, path, **_):
    raise RuntimeError(f"dry run: refused {method} {path}")


_http.request = _blocked  # before anything imports `request` by name

from paidway.agent import execute  # noqa: E402
from paidway.nessie import read, write  # noqa: E402
from paidway.planning import solver  # noqa: E402

read.request = write.request = _blocked  # belt and braces: modules that bound `request` already


def dry_run(cid: str) -> dict:
    plan = solver.solve(cid, live=False, with_tradeoffs=False)["plan"]
    sim = execute._bill_sim(cid, plan, None)
    transfers, steps = execute.schedule(cid, plan, sim, live=False)
    return {"customer": cid, "nessie_calls": execute.nessie_calls(cid, transfers, steps),
            "recorded": [s["step"] for s in steps if s["runs"] == "recorded"],
            "manual": [{"step": s["step"], "how": s.get("how"), "link": s.get("link")} for s in steps if s["runs"] == "manual"]}


if __name__ == "__main__":
    for c in sys.argv[1:] or ["C001"]:
        r = dry_run(c)
        from paidway.data import seed

        have = (seed.nessie_ids(c).get("accounts") or {})
        print(f"== {c}: Nessie mirrors ({len(have)} of {len(seed.mirror_plan(c))} seeded)")
        for m in seed.mirror_plan(c):
            got = have.get(m["account_id"]) or {}
            print(f"  {m['account_id']:24} {m['type']:12} {m['nickname']!r:28} ${m['balance']:>11,.2f}  "
                  + (f"id {got['id']}" if got else "NOT SEEDED") + (f" + {m['loan']} loan" if m["loan"] else ""))
        print(f"== {c}: {len(r['nessie_calls'])} Nessie calls on approve")
        for call in r["nessie_calls"]:
            print(f"  {call['method']} {call['path']}  ({call['tool']}: {call['from']} -> {call['to']}, ${call['amount']:,.2f}"
                  + (f", day {call['recurring_date']} monthly from {call['first_payment']}" if call["recurring_date"] else "") + ")")
            print("    body " + json.dumps(call["body"]))
        print("  recorded: " + " | ".join(r["recorded"]))
        for m in r["manual"]:
            print(f"  you do it: {m['step']} - {m['how']}" + (f" ({m['link']})" if m["link"] else ""))
