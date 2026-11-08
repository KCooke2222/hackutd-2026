"""Approve a plan v2: the ONLY path from a proposal to a money-moving call.

Called from the approve route only (never from graph.py or tools.py; test_safety checks that).
1. Atomically claim pending -> executing (double clicks get 409, never a second transfer).
2. Merge edits, re-validate with validate_plan_v2 (including the customer's own constraints, PR 4).
3. Re-read balances; if they moved since the proposal, mark it stale.
4. Nessie transfers only for cash lump sums into a Nessie-backed debt (the PNC card). Everything else
   (CD / brokerage draws, linked debts, budget, redirects, cuts, autopay) is recorded as scheduled.
"""

from __future__ import annotations

from ..config import NESSIE_KEY
from ..data import balances, categories, store
from ..planning import balance_sheet, finance

EDITABLE = ("monthly_debt_budget", "extra_order", "lump_sums", "redirects", "spending_targets",
            "autopay_full_cards", "keep_emergency_months")


class ApproveError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


def _debts(customer_id: str) -> dict[str, dict]:
    return {d["account_id"]: d for d in balance_sheet.balance_sheet(customer_id)["priority"]["debts"]}


def schedule(customer_id: str, plan: dict) -> tuple[list[dict], list[dict]]:
    """Split a plan into (nessie transfers, scheduled steps). Pure: no I/O beyond reading balances."""
    debts = _debts(customer_id)
    transfers, steps = [], []
    for ls in plan.get("lump_sums") or []:
        d = debts.get(ls["to_debt"], {})
        name = d.get("name", ls["to_debt"])
        if ls["source"] == "cash" and d.get("account_type") == "credit_card" and d.get("source") in ("nessie", "pnc"):
            transfers.append({"from": "savings", "to": "credit_card", "to_debt": ls["to_debt"], "amount": round(float(ls["amount"]), 2)})
        else:
            what = {"cash": "Move cash", "cd": "Break CD",
                    "brokerage": "Sell from brokerage"}[ls["source"]]
            steps.append({"kind": "lump_sum", "step": f"{what}: pay ${float(ls['amount']):,.2f} to {name}", **ls})
    steps.append({"kind": "budget", "step": f"Pay ${plan['monthly_debt_budget']:,.2f}/mo across all debts, extra in order",
                  "extra_order": [debts.get(a, {}).get("name", a) for a in plan.get("extra_order") or []]})
    for r in plan.get("redirects") or []:
        meta = categories.flow_flag(r["flow"]) or {"flag": "redirect", "label": r["flow"]}
        verb = "Pause" if meta["flag"] == categories.PAUSE else "Redirect"
        steps.append({"kind": "redirect", "step": f"{verb} ${float(r['amount_per_month']):,.2f}/mo of {meta['label'].lower()} "
                                                  f"to debt", **r})
    for t in plan.get("spending_targets") or []:
        steps.append({"kind": "spending_target", "step": f"{t['category']}: ${t['current']:,.0f} -> ${t['target']:,.0f}/mo",
                      "category": t["category"], "target": t["target"], "how": t.get("how") or []})
    for a in plan.get("autopay_full_cards") or []:
        steps.append({"kind": "autopay", "step": f"Autopay full statement on {debts.get(a, {}).get('name', a)} once at $0", "account_id": a})
    return transfers, steps


def approve(pid: str, edits: dict | None = None) -> dict:
    p = store.get_proposal(pid)
    if not p:
        raise ApproveError(404, "no such proposal")
    if (p.get("plan") or {}).get("version") != 2:
        raise ApproveError(422, "proposal is a v1 plan; re-run the agent")
    if not store.claim(pid):
        raise ApproveError(409, f"proposal is {store.get_proposal(pid)['status']}, not pending")

    cid = p["customer_id"]
    plan = dict(p["plan"])
    if edits:
        plan.update({k: v for k, v in edits.items() if k in EDITABLE and v is not None})
    from .validator import PlanRejected, validate_plan_v2

    try:
        sim = validate_plan_v2(cid, plan, constraints=p.get("constraints"))
    except PlanRejected as e:
        store.release(pid)
        raise ApproveError(422, "; ".join(getattr(e, "reasons", None) or [str(e)]))
    except (ValueError, KeyError, TypeError) as e:
        store.release(pid)
        raise ApproveError(422, str(e))

    fresh = balances.snapshot(cid)
    diffs = balances.changed(p["simulation"].get("balances_snapshot"), fresh)
    if diffs:
        store.set_status(pid, "stale", {"error": "balances changed since the proposal; re-run the check", "diffs": diffs})
        raise ApproveError(409, "stale, re-run: balances changed since the proposal (" + "; ".join(diffs) + ")")

    transfers, steps = schedule(cid, plan)
    record = {"plan": plan, "scheduled": steps, "simulation": {k: sim.get(k) for k in
              ("net_worth_24m", "baseline_net_worth_24m", "high_cost_debt_free", "interest_total")}}
    total = round(sum(t["amount"] for t in transfers), 2)
    if not transfers:
        return store.set_status(pid, "approved", {**record, "mode": "local_only", "note": "no cash lump sum to a Nessie-backed card"})
    if not NESSIE_KEY:
        return store.set_status(pid, "approved", {**record, "mode": "local_only", "note": "NESSIE_KEY not set", "transfers_pending": transfers})

    from ..data.seed import nessie_ids

    ids = nessie_ids(cid)
    if not ids.get("savings") or not ids.get("credit_card"):
        return store.set_status(pid, "approved", {**record, "mode": "local_only", "note": "customer not seeded into Nessie (run reseed)",
                                                  "transfers_pending": transfers})

    # The only money-moving import in the app (lazy, approve path only; test_safety allows it here alone).
    from ..nessie import write

    try:
        res = write.create_transfer(ids["savings"], ids["credit_card"], total, finance.today(), "Zeroday approved payoff")
    except Exception as e:  # noqa: BLE001
        return store.set_status(pid, "failed", {**record, "error": str(e)})
    finally:
        balance_sheet.clear_cache()
    return store.set_status(pid, "executed", {**record, "mode": "nessie", "transfer": res, "amount": total})
