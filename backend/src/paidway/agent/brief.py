"""Agent v3 brief: everything the first-run agent needs, built by code in a couple of seconds.

    build(customer_id) -> {"snapshot", "plan" (solver brief at the default buffer), "buffers" (the same plan at
                           other cushion sizes), "flags" (odd spending worth a look), "reality"}

The agent reads it once and makes the judgement calls (which suggested cuts to keep, cushion size, what drives
the debt); every number in it comes from the solver and simulator, never from the model.
"""

from __future__ import annotations

from ..planning import assumptions as A
from ..planning import solver, wealth

BUFFER_CHOICES = (2.0, 3.0, 4.0)  # months of expenses kept as cash the agent may pick from
SPIKE_RATIO = 1.6  # latest month at or above this x the average...
SPIKE_MIN = 150.0  # ...and at least this many dollars over it


def _outcome(b: dict) -> dict:
    return {k: b.get(k) for k in ("high_cost_debt_free", "net_worth_24m", "net_worth_gain", "interest_total",
                                  "upfront_cost", "lump_sums", "monthly_debt_budget")} | {
        "net_paydown_per_month": (b.get("paydown") or {}).get("net_paydown_per_month"),
        "suggested_cuts": [t["category"] for t in b.get("spending_targets") or [] if t.get("source") == "suggested"]}


def flags(ctx: dict) -> list[dict]:
    """Flexible categories whose latest month jumped well above their average: worth a look before cutting."""
    out = []
    for cat, c in ctx["spend"].items():
        if c["essential"] or not c["monthly"]:
            continue
        last = c["monthly"][-1]
        if last["amount"] >= SPIKE_RATIO * c["avg"] and last["amount"] - c["avg"] >= SPIKE_MIN:
            out.append({"kind": "spending_spike", "category": cat, "month": last["month"],
                        "amount": round(last["amount"], 2), "avg": round(c["avg"], 2)})
    return sorted(out, key=lambda f: -(f["amount"] - f["avg"]))[:3]


def build(customer_id: str, *, live: bool = True) -> dict:
    from .graph import gather_snapshot

    snap = gather_snapshot(customer_id)
    snap.pop("assumptions", None)  # the solver applies them; the agent doesn't need the table
    default = float(A.get("default_emergency_months"))
    base = solver.solve(customer_id, {"keep_emergency_months": default}, live=live)
    buffers = {f"{default:g}": _outcome(base["brief"])}
    for m in BUFFER_CHOICES:
        if m == default:
            continue
        try:
            r = solver.solve(customer_id, {"keep_emergency_months": m}, live=live, with_tradeoffs=False)
            buffers[f"{m:g}"] = _outcome(r["brief"])
        except Exception:  # noqa: BLE001 - a cushion the data can't support is just not offered
            continue
    ctx = wealth.context(customer_id, keep_months=default, live=live)
    under = None
    if (snap.get("reality_check") or {}).get("realistic") is False:
        from ..planning import options

        under = options.underwater_options(customer_id)
    return {"underwater": under, "snapshot": snap, "plan": base["brief"], "default_buffer_months": default, "buffers": buffers,
            "flags": flags(ctx), "spending_avg": {c: round(v["avg"], 2) for c, v in ctx["spend"].items()}, "reality": snap.get("reality_check") or {}}
