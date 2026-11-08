"""Talk to the plan (PR 4): code-only helpers for adjusting an existing proposal.

compare() is the pushback: it diffs the adjusted plan against the plan it replaces. If the change costs
more than WORSE_NW_USD of 24-month net worth, or delays the high-cost $0 month, it is flagged `worse` and the
customer sees the cost. We apply it anyway: the customer decides.
"""

from __future__ import annotations

WORSE_NW_USD = 250.0
SOURCE_NAME = {"cash": "Savings", "cd": "CD", "brokerage": "Brokerage"}


def _usd(n: float) -> str:
    return f"${n:,.0f}"


def _months_between(a: str | None, b: str | None) -> int | None:
    """Months from a to b ('YYYY-MM'); None if either is missing."""
    if not a or not b:
        return None
    return (int(b[:4]) * 12 + int(b[5:7])) - (int(a[:4]) * 12 + int(a[5:7]))


def plan_changes(before: dict, after: dict, names: dict[str, str] | None = None) -> list[str]:
    """Human lines for every lever that changed, e.g. "Debt budget: $4,300 → $3,800/mo"."""
    names = names or {}
    out: list[str] = []
    b0, b1 = float(before.get("monthly_debt_budget") or 0), float(after.get("monthly_debt_budget") or 0)
    if abs(b1 - b0) >= 1:
        out.append(f"Paid to debts: {_usd(b0)} → {_usd(b1)}/mo")

    def red(p):
        return sum(float(r.get("amount_per_month") or 0) for r in p.get("redirects") or [])

    r0, r1 = red(before), red(after)
    if abs(r1 - r0) >= 1:
        out.append(f"Redirect unknown transfers: {_usd(r0)} → {_usd(r1)}/mo")

    def lumps(p):
        d: dict[tuple, float] = {}
        for ls in p.get("lump_sums") or []:
            k = (ls.get("source"), ls.get("to_debt"))
            d[k] = d.get(k, 0.0) + float(ls.get("amount") or 0)
        return d

    l0, l1 = lumps(before), lumps(after)
    for k in sorted(set(l0) | set(l1), key=str):
        a, b = l0.get(k, 0.0), l1.get(k, 0.0)
        if abs(b - a) >= 1:
            what = f"{SOURCE_NAME.get(k[0], k[0])} → {names.get(k[1], k[1])}"
            out.append(f"{what}: {_usd(a)} → {_usd(b)}" if a and b else f"{what}: {'removed' if a else 'added'} {_usd(a or b)}")

    def cuts(p):
        return {t["category"]: t for t in p.get("spending_targets") or [] if t.get("category")}

    c0, c1 = cuts(before), cuts(after)
    for cat in sorted(set(c0) | set(c1)):
        a, b = c0.get(cat), c1.get(cat)
        label = cat.replace("_", " ").capitalize()
        if a and b and abs(float(a["target"]) - float(b["target"])) >= 1:
            out.append(f"{label}: {_usd(float(a['target']))} → {_usd(float(b['target']))}/mo")
        elif b and not a:
            out.append(f"{label}: cut to {_usd(float(b['target']))}/mo")
        elif a and not b:
            out.append(f"{label}: no cut (was {_usd(float(a['target']))}/mo)")

    k0, k1 = float(before.get("keep_emergency_months") or 3), float(after.get("keep_emergency_months") or 3)
    if abs(k1 - k0) > 1e-9:
        out.append(f"Emergency cash: {k0:g} → {k1:g} months")
    if list(before.get("extra_order") or []) != list(after.get("extra_order") or []):
        out.append("Payoff order: " + " → ".join(names.get(x, x) for x in after.get("extra_order") or []))
    return out


def compare(before_sim: dict, after_sim: dict, before_plan: dict | None = None, after_plan: dict | None = None) -> dict:
    """Outcome of the adjusted plan vs the plan it replaces. All numbers come from the simulations."""
    nw0, nw1 = float(before_sim.get("net_worth_24m") or 0), float(after_sim.get("net_worth_24m") or 0)
    i0, i1 = float(before_sim.get("interest_total") or 0), float(after_sim.get("interest_total") or 0)
    f0, f1 = before_sim.get("high_cost_debt_free"), after_sim.get("high_cost_debt_free")
    if f0 and f1:
        delay = _months_between(f0, f1)
    elif f0 and not f1:
        delay = None  # now never within the horizon
    else:
        delay = 0 if f0 == f1 else None
    later = (f0 and not f1) or (delay is not None and delay > 0)
    nw_delta = round(nw1 - nw0, 2)
    worse = nw_delta < -WORSE_NW_USD or bool(later)

    why: list[str] = []
    if nw_delta < -WORSE_NW_USD:
        why.append(f"{_usd(-nw_delta)} less net worth in 2 years")
    if f0 and not f1:
        why.append("high-cost debt no longer reaches $0 within 2 years")
    elif later:
        why.append(f"high-cost debt $0 {delay} month{'s' if delay != 1 else ''} later")
    if i1 - i0 >= 1:
        why.append(f"{_usd(i1 - i0)} more interest")

    names = {d["account_id"]: d.get("name", d["account_id"]) for d in after_sim.get("debts") or []}
    return {
        "net_worth_24m": {"before": nw0, "after": nw1, "delta": nw_delta},
        "interest_total": {"before": i0, "after": i1, "delta": round(i1 - i0, 2)},
        "high_cost_debt_free": {"before": f0, "after": f1, "months_later": delay},
        "worse": worse,
        "cost": round(max(0.0, -nw_delta), 2),
        "why": why,
        "changes": plan_changes(before_plan or {}, after_plan or {}, names) if before_plan and after_plan else [],
    }
