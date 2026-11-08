"""Reset a customer's LOCAL app state (SQLite only) so the demo opens on the Snapshot again.

Deletes the customer's proposals and demo tracking months. Proposals a recorded replay points to are kept
but set back to pending, so replays still play. Nothing is sent to Nessie and no money moves.

    uv run python scripts/reset_local.py C001 C027
"""

from __future__ import annotations

import sys

from paidway.data import store


def reset_local(customer_id: str) -> dict:
    with store._conn() as c:
        keep = {r[0] for r in c.execute("select proposal_id from run_replays where customer_id=? and proposal_id is not null",
                                        (customer_id,))}
        marks = ",".join("?" * len(keep)) or "''"
        deleted = c.execute(f"delete from proposals where customer_id=? and id not in ({marks})",
                            (customer_id, *keep)).rowcount
        reopened = c.execute(f"update proposals set status='pending', decided_at=null, execution=null "
                             f"where customer_id=? and id in ({marks}) and status!='pending'", (customer_id, *keep)).rowcount
        has_months = c.execute("select 1 from sqlite_master where type='table' and name='demo_months'").fetchone()
        months = c.execute("delete from demo_months where customer_id=?", (customer_id,)).rowcount if has_months else 0
        has_opts = c.execute("select 1 from sqlite_master where type='table' and name='applied_options'").fetchone()
        opts = c.execute("delete from applied_options where customer_id=?", (customer_id,)).rowcount if has_opts else 0
    return {"customer": customer_id, "proposals_deleted": deleted, "replay_proposals_kept": len(keep),
            "reopened": reopened, "demo_months_deleted": months, "applied_options_cleared": opts}


if __name__ == "__main__":
    for cid in sys.argv[1:] or ["C001", "C027"]:
        print(reset_local(cid))
