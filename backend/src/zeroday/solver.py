"""Deterministic plan solver: the numbers come from code; the model and the person only set soft inputs.

    solve(customer_id, inputs) -> {"plan": plan v2, "sim": wealth.simulate_plan(...), "tradeoffs", "brief"}

Soft inputs (all optional):
    keep_emergency_months  months of expenses kept as cash (default ASSUMPTIONS default_emergency_months)
    spending_targets       cuts the PERSON chose: [{category, target, how?, proof_month?}]. The solver never
                           invents cuts; it only fills proof_month / how and checks them with the validator.
    redirect_flows         None = every movable flow its flag allows (categories.FLOWS); a list = only these
                           flows; a dict {flow: amount_per_month} = exactly these amounts.
    expected_return        brokerage return (%) for the sell-or-keep test (default ASSUMPTIONS brokerage)

Method (greedy avalanche, checked against an LP in tests/test_solver.py):
    1. Cash above the buffer pays debts above the hurdle as lump sums today, highest APR first, lowest-yield
       pool first, only where the APR beats that pool's yield. Fitted so cash never drops below the buffer.
    2. CD / brokerage are sold only for debt left after cash, only if APR > their return + penalty/tax drag,
       and only as much as raises 24-month net worth. IRA and property are never sources.
    3. Monthly: minimums, then every freed dollar to the highest-APR debt above the hurdle. The monthly
       surplus (a REDIRECT flow) is redirected in full (what the budget doesn't use still stays as savings); a contribution is paused
       only as far as it raises 24-month net worth (debt APR beats what the money earns invested).
    4. The plan passes validate_plan_v2 and is simulated by wealth.simulate_plan.

Tradeoffs: for each flexible category (trim / cancel), what cutting it to its floor would buy in months
sooner and net worth. The floor is the customer's own lowest month, within the category's flex limit.
Fixed and ask categories never appear.
"""

from __future__ import annotations

import math

from . import assumptions as A
from . import categories, wealth

CASH_POOLS = ("cash", "outside_savings")  # cash-like lump sources; a pool missing from the data is skipped


# ---------------- inputs ----------------

def _ceil2(x: float) -> float:
    return math.ceil(x * 100 - 1e-6) / 100


def _recurring(customer_id: str, cat: str) -> list[dict]:
    return [r for r in wealth._detail_meta(customer_id, cat)["recurring"] if float(r.get("amount") or 0) > 0]


def floor_target(customer_id: str, ctx: dict, cat: str) -> dict | None:
    """The lowest target a flexible category may take: trim = max(lowest month, avg - max_pct); cancel =
    avg minus its named recurring charges. None for fixed / ask / unknown categories."""
    c = ctx["spend"].get(cat)
    flag = categories.spending_flag(cat)
    if c is None or c["essential"] or flag["flag"] not in (categories.TRIM, categories.CANCEL) or c["avg"] <= 0:
        return None
    if flag["flag"] == categories.TRIM:
        pct = flag.get("max_pct") or A.get("max_cut_pct")
        target = _ceil2(max(c["min"], c["avg"] * (1 - pct / 100)))
        return {"category": cat, "target": target} if target < c["avg"] - 0.5 else None
    rec = _recurring(customer_id, cat)
    amt = sum(float(r["amount"]) for r in rec)
    if not rec or amt < 0.5:
        return None
    return {"category": cat, "target": _ceil2(max(c["avg"] - amt, 0.0)), "cancel": [r["name"] for r in rec]}


def complete_target(customer_id: str, ctx: dict, t: dict) -> dict:
    """Fill current, proof_month and how for a cut the person chose. The target itself is never changed."""
    cat = t["category"]
    c = ctx["spend"].get(cat)
    if c is None:
        return dict(t)  # the validator names the problem
    target = float(t["target"])
    flag = categories.spending_flag(cat)
    out = {"category": cat, "current": c["avg"], "target": target}
    how = [str(h) for h in t.get("how") or [] if str(h).strip()]
    cancel_amt = 0.0
    if flag["flag"] == categories.CANCEL:
        rec = _recurring(customer_id, cat)
        named = [r for r in rec if r["name"].lower() in " ".join(how).lower()] if how else []
        if not named:  # cancel the largest charges until the cut is covered
            need, named = c["avg"] - target, []
            for r in sorted(rec, key=lambda r: -float(r["amount"])):
                if need <= 0.5:
                    break
                named.append(r)
                need -= float(r["amount"])
            how = [f"Cancel {r['name']}" for r in named]
        cancel_amt = sum(float(r["amount"]) for r in named)
    elif not how:
        how = [f"Keep {flag['label'].lower()} under ${math.ceil(target):,.0f}/mo"]
    out["how"] = how
    pm = t.get("proof_month")
    if not pm:
        ok = [m for m in c["monthly"] if m["amount"] <= target + 0.5 + cancel_amt]
        pm = (ok[-1] if ok else min(c["monthly"], key=lambda m: m["amount"]))["month"]
    out["proof_month"] = pm
    return out


# ---------------- plan pieces ----------------

def _hurdles(ctx: dict, expected_return: float) -> dict:
    """The APR a debt must beat for each source to pay it: the source's yield plus its one-time cost spread
    over the horizon."""
    a, years = ctx["assets"], A.get("horizon_months") / 12
    cd_drag = (1 - 1 / wealth._gross_needed("cd", 1.0, ctx)) * 100 / years
    tax_drag = (1 - 1 / wealth._gross_needed("brokerage", 1.0, ctx)) * 100 / years
    return {"cash": a["cash_yield_pct"], "outside_savings": a.get("outside_yield_pct", a["cash_yield_pct"]),
            "cd": A.ret("cd") + cd_drag, "brokerage": expected_return + tax_drag}


def _eligible(ctx: dict) -> list[dict]:
    return sorted((d for d in ctx["debts"] if d["extra_eligible"] and d["balance"] > 0), key=lambda d: -d["apr_pct"])


def cash_lumps(ctx: dict, hurdles: dict) -> list[dict]:
    """Cash above the buffer to the highest-APR debts, lowest-yield pool first."""
    avail = dict(ctx["lump_available"])
    pools = sorted((s for s in CASH_POOLS if s in avail), key=lambda s: hurdles[s])
    out = []
    for d in _eligible(ctx):
        left = d["balance"]
        for src in pools:
            if d["apr_pct"] <= hurdles[src]:
                continue
            take = math.floor(min(left, avail[src], avail["cash_total"]) * 100) / 100
            if take >= 1:
                out.append({"source": src, "amount": take, "to_debt": d["account_id"]})
                left -= take
                avail[src] -= take
                avail["cash_total"] -= take
    return out


def _sold_lumps(ctx: dict, src: str, net_total: float, already: list[dict], hurdle: float) -> list[dict]:
    """`net_total` of `src` (cd / brokerage) to the highest-APR debts still unpaid after `already`."""
    paid: dict[str, float] = {}
    for l in already:
        paid[l["to_debt"]] = paid.get(l["to_debt"], 0.0) + l["amount"]
    out, left = [], net_total
    for d in _eligible(ctx):
        if d["apr_pct"] <= hurdle or left < 1:
            continue
        take = math.floor(min(left, d["balance"] - paid.get(d["account_id"], 0.0)) * 100) / 100
        if take >= 1:
            out.append({"source": src, "amount": take, "to_debt": d["account_id"]})
            left -= take
    return out


def _sold_cap(ctx: dict, src: str, already: list[dict], hurdle: float) -> float:
    """Most `src` can pay: what's sellable (net of penalty/tax), and no more than is still owed above its hurdle."""
    sellable = ctx["ladder"][src]["available"] / wealth._gross_needed(src, 1.0, ctx)
    above = {d["account_id"]: d["balance"] for d in _eligible(ctx) if d["apr_pct"] > hurdle}
    owed = sum(above.values()) - sum(l["amount"] for l in already if l["to_debt"] in above)
    return max(min(sellable, owed) - 0.01, 0.0) if sellable >= 1 else 0.0


def _search(f, lo: float, hi: float, iters: int = 24) -> float:
    """Argmax of a concave (piecewise linear) f on [lo, hi]: golden-section, then check the ends."""
    g = (math.sqrt(5) - 1) / 2
    a, b = lo, hi
    c, d = b - g * (b - a), a + g * (b - a)
    fc, fd = f(c), f(d)
    for _ in range(iters):
        if b - a < 1:
            break
        if fc >= fd:
            b, d, fd = d, c, fc
            c = b - g * (b - a)
            fc = f(c)
        else:
            a, c, fc = c, d, fd
            d = a + g * (b - a)
            fd = f(d)
    best = max([(f(lo), lo), (f(hi), hi), (fc, c), (fd, d)], key=lambda x: x[0])
    return best[1]


# ---------------- solve ----------------

def _nw(customer_id: str, ctx: dict, plan: dict) -> float:
    sim = wealth.simulate_plan(customer_id, plan, ctx=ctx)
    return sim["net_worth_24m"] if sim["feasible"] else -1e12


def _assemble(ctx: dict, lumps: list[dict], redirects: list[dict], targets: list[dict]) -> dict:
    plan = {
        "version": 2,
        "monthly_debt_budget": 0.0,
        "extra_order": [d["account_id"] for d in _eligible(ctx)],
        "lump_sums": [l for l in lumps if l["amount"] >= 1],
        "redirects": [r for r in redirects if r["amount_per_month"] > 0.005],
        "spending_targets": targets,
        "autopay_full_cards": [d["account_id"] for d in ctx["debts"] if d["is_card"]],
        "keep_emergency_months": ctx["keep_months"],
    }
    # Every freed dollar is budgeted to debt; whatever the debts don't need still goes to savings.
    plan["monthly_debt_budget"] = round(wealth.budget_limit(ctx, plan)["max_monthly_debt_budget"], 2)
    return plan


def _fit(customer_id: str, ctx: dict, plan: dict) -> dict:
    """Shrink lump sums (lowest-priority first) until the simulation keeps the cash buffer."""
    if wealth.simulate_plan(customer_id, plan, ctx=ctx)["feasible"] or not plan["lump_sums"]:
        return plan
    full = plan["lump_sums"]

    def with_total(t: float) -> dict:
        left, out = t, []
        for l in full:
            take = math.floor(min(l["amount"], left) * 100) / 100
            if take >= 1:
                out.append({**l, "amount": take})
            left -= take
        return _assemble(ctx, out, plan["redirects"], plan["spending_targets"])

    lo, hi = 0.0, sum(l["amount"] for l in full)
    for _ in range(30):
        if hi - lo < 0.5:
            break
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if wealth.simulate_plan(customer_id, with_total(mid), ctx=ctx)["feasible"] else (lo, mid)
    return with_total(lo)


def build_plan(customer_id: str, inputs: dict | None = None, *, live: bool = True, ctx: dict | None = None) -> tuple[dict, dict]:
    """The greedy plan for these soft inputs. Returns (plan, ctx)."""
    inp = dict(inputs or {})
    keep = inp.get("keep_emergency_months")
    keep = A.get("default_emergency_months") if keep is None else float(keep)
    ctx = ctx or wealth.context(customer_id, keep_months=keep, live=live)
    exp_ret = float(inp.get("expected_return") if inp.get("expected_return") is not None else A.ret("brokerage"))
    targets = [complete_target(customer_id, ctx, t) for t in inp.get("spending_targets") or []]
    hurdles = _hurdles(ctx, exp_ret)
    any_debt = bool(_eligible(ctx))

    # Redirects: fixed amounts when the person gave them; else REDIRECT flows in full and pauses searched below.
    movable = ctx["movable_flows"]
    rf = inp.get("redirect_flows")
    fixed_amounts = isinstance(rf, dict)
    allowed = list(rf) if rf is not None else list(movable)
    allowed = [f for f in allowed if f in movable]
    red = {f: 0.0 for f in allowed}
    if fixed_amounts:
        red = {f: min(float(rf[f]), movable[f]["max_per_month"]) for f in allowed}
    elif any_debt:
        for f in allowed:
            if movable[f]["flag"] == categories.REDIRECT:
                red[f] = movable[f]["max_per_month"]

    def redirects(r: dict) -> list[dict]:
        return [{"flow": f, "amount_per_month": round(a, 2)} for f, a in r.items()]

    base_lumps = cash_lumps(ctx, hurdles)
    sold: dict[str, float] = {"cd": 0.0, "brokerage": 0.0}

    def lumps_for(s: dict) -> list[dict]:
        out = list(base_lumps)
        for src in ("cd", "brokerage"):
            if s[src] >= 1:
                out += _sold_lumps(ctx, src, s[src], out, hurdles[src])
        return out

    def plan_for(s: dict, r: dict) -> dict:
        return _fit(customer_id, ctx, _assemble(ctx, lumps_for(s), redirects(r), targets))

    pausable = [f for f in allowed if movable[f]["flag"] == categories.PAUSE and movable[f]["max_per_month"] > 0]
    for _ in range(2 if any_debt else 0):  # coordinate ascent: sales, then pauses, twice
        for src in ("cd", "brokerage"):
            cap = _sold_cap(ctx, src, lumps_for({**sold, src: 0.0}), hurdles[src])
            if cap >= 1:
                sold[src] = _search(lambda x: _nw(customer_id, ctx, plan_for({**sold, src: x}, red)), 0.0, cap)
        if not fixed_amounts:
            for f in pausable:
                hi = movable[f]["max_per_month"]
                red[f] = _search(lambda x: _nw(customer_id, ctx, plan_for(sold, {**red, f: x})), 0.0, hi)
    for src in sold:  # drop sales too small to matter
        if sold[src] and _nw(customer_id, ctx, plan_for({**sold, src: 0.0}, red)) >= _nw(customer_id, ctx, plan_for(sold, red)) - 1:
            sold[src] = 0.0
    if not fixed_amounts:
        for f in pausable:
            if red[f] and _nw(customer_id, ctx, plan_for(sold, {**red, f: 0.0})) >= _nw(customer_id, ctx, plan_for(sold, red)) - 1:
                red[f] = 0.0
            red[f] = round(red[f], 2)
    return plan_for(sold, red), ctx


def _months_between(a: str | None, b: str | None) -> int | None:
    if not a or not b:
        return None
    return (int(a[:4]) * 12 + int(a[5:7])) - (int(b[:4]) * 12 + int(b[5:7]))


def tradeoffs(customer_id: str, inputs: dict, sim: dict, *, live: bool = True) -> list[dict]:
    """What cutting each flexible category to its floor would add on top of the person's own cuts."""
    keep = inputs.get("keep_emergency_months")
    ctx = wealth.context(customer_id, keep_months=keep, live=live)
    chosen = {t["category"]: float(t["target"]) for t in inputs.get("spending_targets") or []}
    rows, floors = [], []
    for cat in sorted(ctx["spend"], key=lambda k: -ctx["spend"][k]["avg"]):
        fl = floor_target(customer_id, ctx, cat)
        if fl is None or chosen.get(cat, math.inf) <= fl["target"] + 0.5:
            continue
        floors.append(fl)
        rows.append(_tradeoff_row(customer_id, ctx, inputs, sim, [fl], cat))
    if len(floors) > 1:
        rows.append(_tradeoff_row(customer_id, ctx, inputs, sim, floors, "all of the above"))
    return rows


def _tradeoff_row(customer_id, ctx, inputs, sim, floors, label) -> dict:
    keep_targets = [t for t in inputs.get("spending_targets") or [] if t["category"] not in {f["category"] for f in floors}]
    new_inputs = {**inputs, "spending_targets": keep_targets + [{"category": f["category"], "target": f["target"],
                                                                 **({"how": [f"Cancel {n}" for n in f["cancel"]]} if f.get("cancel") else {})}
                                                                for f in floors]}
    plan, _ = build_plan(customer_id, new_inputs, ctx=ctx)
    s = wealth.simulate_plan(customer_id, plan, ctx=ctx)
    c = ctx["spend"].get(label) or {}
    cut = sum(ctx["spend"][f["category"]]["avg"] - f["target"] for f in floors)
    return {
        "category": label,
        "label": categories.spending_flag(label)["label"] if c else label,
        "flag": c.get("flag", "mixed"),
        "current": round(c.get("avg", sum(ctx["spend"][f["category"]]["avg"] for f in floors)), 2),
        "floor": round(floors[0]["target"], 2) if c else None,
        "cut_per_month": round(cut, 2),
        "high_cost_debt_free": s["high_cost_debt_free"],
        "months_sooner": _months_between(sim["high_cost_debt_free"], s["high_cost_debt_free"]),
        "net_worth_24m": s["net_worth_24m"],
        "net_worth_gain": round(s["net_worth_24m"] - sim["net_worth_24m"], 2),
        "feasible": s["feasible"],
    }


def solve(customer_id: str, inputs: dict | None = None, *, live: bool = True, with_tradeoffs: bool = True) -> dict:
    """Plan v2 + simulation + tradeoffs + a brief for the agent. Raises validator.PlanRejected if the
    person's own inputs (e.g. a cut below their lowest month) can't be met."""
    from .agent.validator import validate_plan_v2

    inp = dict(inputs or {})
    plan, ctx = build_plan(customer_id, inp, live=live)
    sim = validate_plan_v2(customer_id, plan, live=live)
    out = {"plan": plan, "sim": sim, "inputs": inp}
    out["tradeoffs"] = tradeoffs(customer_id, inp, sim, live=live) if with_tradeoffs else []
    out["brief"] = brief(ctx, plan, sim, out["tradeoffs"])
    return out


def brief(ctx: dict, plan: dict, sim: dict, trade: list[dict]) -> dict:
    """What the agent needs: the plan in words-sized pieces (the month table stays server-side)."""
    names = {d["account_id"]: d["name"] for d in ctx["debts"]}
    return {
        "lump_sums": [{**l, "debt": names.get(l["to_debt"])} for l in plan["lump_sums"]],
        "monthly_debt_budget": plan["monthly_debt_budget"],
        "extra_order": [names.get(x, x) for x in plan["extra_order"]],
        "redirects": plan["redirects"],
        "spending_targets": [{k: t[k] for k in ("category", "current", "target")} for t in plan["spending_targets"]],
        "payoff": {d["name"]: d["payoff_month"] for d in sim["debts"]},
        "high_cost_debt_free": sim["high_cost_debt_free"],
        "baseline_high_cost_debt_free": sim.get("baseline_high_cost_debt_free"),
        "net_worth_24m": sim["net_worth_24m"],
        "baseline_net_worth_24m": sim["baseline_net_worth_24m"],
        "net_worth_gain": round(sim["net_worth_24m"] - sim["baseline_net_worth_24m"], 2),
        "interest_total": sim["interest_total"],
        "interest_saved": round(sim["baseline_interest_total"] - sim["interest_total"], 2),
        "upfront_cost": sim["upfront_cost"],
        "tradeoffs": [{k: r[k] for k in ("category", "cut_per_month", "months_sooner", "net_worth_gain")} for r in trade],
    }
