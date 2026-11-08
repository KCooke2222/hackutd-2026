"""What-if scenarios (roadmap PR 6): how does a plan hold up when life happens?

Deterministic like wealth.py: a scenario is a list of events turned into a per-month cash shock,
run through _run_shock: wealth._run's month loop plus the shock (wealth._run stays the solver's). Cash that would go negative is borrowed on the
PNC card, so a broken plan shows up as card debt instead of a silently negative balance.

Each scenario returns the shocked plan, an *adjusted* plan (pause extra payments, keep minimums,
until cash is back at the emergency buffer) and a verdict: holds / bends / breaks.
`monte_carlo` runs a 3-state Markov chain (on track / drifting / shock) many times for a range.
"""

from __future__ import annotations

import random

from . import assumptions as A
from . import wealth

# Event kinds:
#   {"kind": "job_loss", "start": 1, "months": 3}            income -> 0 for `months`
#   {"kind": "income_change", "start": 1, "pct": -20, "months": None}   None = rest of horizon
#   {"kind": "expense", "start": 1, "amount": 2000}          one-time cost
PRESETS: list[dict] = [
    {"id": "job_loss_3m", "label": "Lose your job for 3 months", "events": [{"kind": "job_loss", "start": 1, "months": 3}]},
    {"id": "income_drop_20", "label": "Income drops 20%", "events": [{"kind": "income_change", "start": 1, "pct": -20}]},
    {"id": "emergency_2k", "label": "$2,000 emergency", "events": [{"kind": "expense", "start": 1, "amount": 2000}]},
]

# Markov chain for the range view. State effects are per month.
MARKOV_STATES = ("on_track", "drifting", "shock")
MARKOV_P = {  # from -> probabilities of (on_track, drifting, shock) next month
    "on_track": (0.90, 0.08, 0.02),
    "drifting": (0.50, 0.45, 0.05),
    "shock": (0.30, 0.10, 0.60),
}
DRIFT_SPEND_PCT = 15.0  # drifting: spending this much over normal


def _horizon() -> int:
    return A.get("horizon_months")


def build_shock(ctx: dict, events: list[dict]) -> dict:
    """Events -> {"cash_delta": [H+1], "pause_extra": [H+1], "months": [shocked month indexes]}."""
    H = _horizon()
    income = ctx["flows"]["income"]
    delta = [0.0] * (H + 1)
    for e in events or []:
        kind = e.get("kind")
        start = max(1, int(e.get("start") or 1))
        if kind == "job_loss":
            n = max(0, int(e.get("months") or 0))
            for i in range(start, min(start + n, H + 1)):
                delta[i] -= income
        elif kind == "income_change":
            pct = float(e.get("pct") or 0)
            n = e.get("months")
            end = H + 1 if n in (None, "") else min(start + int(n), H + 1)
            for i in range(start, end):
                delta[i] += income * pct / 100
        elif kind == "expense":
            if start <= H:
                delta[start] -= float(e.get("amount") or 0)
        else:
            raise ValueError(f"unknown scenario event kind {kind!r}")
    hit = [i for i, d in enumerate(delta) if d < -0.005]
    return {"cash_delta": delta, "pause_extra": [False] * (H + 1), "months": hit}


def adjusted_shock(ctx: dict, plan: dict, shock: dict) -> tuple[dict, dict, int | None]:
    """The adjusted plan: pause extra payments (minimums still paid) from the first bad month through
    month k, choosing k so nothing goes on the card and net worth is highest. No pause is a candidate.
    Returns (shock with pauses, its simulation, last paused month index or None)."""
    H = _horizon()
    hit = shock["months"]
    raw = _run_shock(ctx, plan, shock)
    if not hit:
        return shock, raw, None
    first = hit[0]
    best = (shock, raw, None)

    def key(sim: dict) -> tuple:
        return (round(sim["borrowed_on_card"]), -sim["net_worth_24m"])

    for k in range(first, H + 1):
        s = dict(shock, pause_extra=[first <= i <= k for i in range(H + 1)])
        sim = _run_shock(ctx, plan, s)
        if key(sim) < key(best[1]):
            best = (s, sim, k)
    return best


def _months_between(a: str | None, b: str | None) -> int | None:
    if a is None or b is None:
        return None
    return (int(b[:4]) * 12 + int(b[5:7])) - (int(a[:4]) * 12 + int(a[5:7]))


def verdict(ctx: dict, ref: dict, sim: dict) -> dict:
    H = _horizon()
    floor = min(ctx["emergency_buffer"], ctx["assets"]["cash"]) - 1
    ref_free, free = ref["high_cost_debt_free"], sim["high_cost_debt_free"]
    if ref_free is None:
        slip = None if free is None else 0
    elif free is None:
        slip = H  # pushed past the horizon
    else:
        slip = max(_months_between(ref_free, free) or 0, 0)
    if sim["borrowed_on_card"] > 0.5:
        v, why = "breaks", f"${sim['borrowed_on_card']:,.0f} goes on the card"
    elif (slip or 0) <= 1 and sim["min_cash"] >= floor:
        v, why = "holds", "on track"
    elif (slip or 0) > 1:
        v, why = "bends", ("high-cost debt not $0 within 2 years" if free is None else f"$0 date slips {slip} months")
    else:
        v, why = "bends", f"savings dip to ${sim['min_cash']:,.0f}, below the ${ctx['emergency_buffer']:,.0f} buffer"
    return {"verdict": v, "why": why, "slip_months": slip, "nw_delta": round(sim["net_worth_24m"] - ref["net_worth_24m"], 2)}


def _slim(sim: dict) -> dict:
    keep = ("months", "debts", "net_worth_24m", "interest_total", "high_cost_debt_free",
            "min_cash", "min_cash_month", "borrowed_on_card")
    return {k: sim[k] for k in keep}


_MON = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _ym(i: int) -> str:
    s = wealth._label(i)
    return f"{_MON[int(s[5:7]) - 1]} {s[:4]}"


def _advice(ctx: dict, ref: dict, adj_sim: dict, last_pause: int | None, shock: dict) -> str:
    if not shock["months"]:
        return "Nothing to change."
    first = shock["months"][0]
    cost = ref["net_worth_24m"] - adj_sim["net_worth_24m"]
    if last_pause is None:
        msg = f"Keep the plan as is; savings absorb it. Costs ${cost:,.0f} of net worth vs no shock."
    else:
        span = _ym(first) if last_pause == first else f"{_ym(first)} to {_ym(last_pause)}"
        msg = f"Pause extra debt payments {span}, keep paying every minimum"
        msg += f", resume in {_ym(last_pause + 1)}" if last_pause < _horizon() else ", minimums only after that"
        msg += f". Costs ${cost:,.0f} of net worth vs no shock."
    if adj_sim["borrowed_on_card"] > 0.5:
        msg += f" Still short: ${adj_sim['borrowed_on_card']:,.0f} goes on the card."
    m = adj_sim["months"]
    drain = (m[-7]["cash"] - m[-1]["cash"]) / 6 if len(m) > 7 else 0.0
    if drain > 25 and m[-1]["cash"] < ctx["emergency_buffer"]:
        msg += f" Cash keeps falling ~${drain:,.0f}/mo, so this needs a spending cut or more income, not just a pause."
    return msg


def run_scenarios(customer_id: str, plan: dict, scenarios: list[dict] | None = None, *, live: bool = True) -> dict:
    """Run `plan` through each scenario (default: PRESETS). Everything shares one context."""
    plan = {**plan, "version": 2}
    ctx = wealth.context(customer_id, keep_months=plan.get("keep_emergency_months"), live=live)
    ref = _run_shock(ctx, plan)
    baseline = _run_shock(ctx, None)
    out = []
    for i, sc in enumerate(scenarios or PRESETS):
        shock = build_shock(ctx, sc.get("events") or [])
        raw = _run_shock(ctx, plan, shock)
        adj_shock, adj, last_pause = adjusted_shock(ctx, plan, shock)
        out.append({
            "id": sc.get("id") or f"custom_{i}",
            "label": sc.get("label") or "Custom",
            "events": sc.get("events") or [],
            **verdict(ctx, ref, raw),
            "sim": _slim(raw),
            "adjusted": {**_slim(adj), **verdict(ctx, ref, adj),
                         "paused": [wealth._label(j) for j, p in enumerate(adj_shock["pause_extra"]) if p],
                         "advice": _advice(ctx, ref, adj, last_pause, shock)},
        })
    return {
        "customer_id": customer_id,
        "monthly_income": round(ctx["flows"]["income"], 2),
        "emergency_buffer": ctx["emergency_buffer"],
        "reference": _slim(ref),
        "baseline": _slim(baseline),
        "results": out,
    }


def monte_carlo(customer_id: str, plan: dict, runs: int = 500, seed: int = 7, *, live: bool = True) -> dict:
    """Range of outcomes: each month the customer is on track, drifting or hit by a shock (Markov chain)."""
    plan = {**plan, "version": 2}
    H = _horizon()
    runs = max(10, min(int(runs), 2000))
    ctx = wealth.context(customer_id, keep_months=plan.get("keep_emergency_months"), live=live)
    income, spend = ctx["flows"]["income"], ctx["flows"]["expenses"]
    effect = {"on_track": 0.0, "drifting": -spend * DRIFT_SPEND_PCT / 100, "shock": -income}
    rng = random.Random(seed)
    paths: list[list[float]] = []
    free = borrowed = 0
    for _ in range(runs):
        state, delta = "on_track", [0.0] * (H + 1)
        for i in range(1, H + 1):
            state = rng.choices(MARKOV_STATES, weights=MARKOV_P[state])[0]
            delta[i] = effect[state]
        sim = _run_shock(ctx, plan, {"cash_delta": delta, "pause_extra": []})
        paths.append([m["net_worth"] for m in sim["months"]])
        free += sim["high_cost_debt_free"] is not None
        borrowed += sim["borrowed_on_card"] > 0.5

    def pct(vals: list[float], q: float) -> float:
        s = sorted(vals)
        return round(s[min(len(s) - 1, max(0, round(q * (len(s) - 1))))], 2)

    months = []
    for i in range(H + 1):
        col = [p[i] for p in paths]
        months.append({"month": wealth._label(i), "p10": pct(col, 0.1), "p50": pct(col, 0.5), "p90": pct(col, 0.9)})
    return {
        "runs": runs, "seed": seed, "months": months,
        "p_high_cost_free": round(free / runs, 3),
        "p_borrowed": round(borrowed / runs, 3),
        "odds": {k: dict(zip(MARKOV_STATES, v)) for k, v in MARKOV_P.items()},
    }


# ---------- the shocked month loop ----------

def _run_shock(ctx: dict, plan: dict | None, shock: dict | None = None) -> dict:
    """wealth._run's month loop with a shock: {"cash_delta": [per month], "pause_extra": [per month]}.
    Kept apart from wealth._run (the solver's simulator) on purpose. Under a shock, cash that would go
    negative is borrowed on the PNC card instead, so a broken plan shows up as card debt.
    Adds min_cash, min_cash_month and borrowed_on_card to wealth._run's output."""
    s_delta = (shock or {}).get("cash_delta") or []
    s_pause = (shock or {}).get("pause_extra") or []
    H = A.get("horizon_months")
    a = ctx["assets"]
    f = ctx["flows"]
    problems: list[str] = []
    debts = {d["account_id"]: dict(d, bal=d["balance"], interest=0.0, payoff=None) for d in ctx["debts"]}
    cash = a["cash"]
    inv = {"cd": a["cd"], "brokerage": a["brokerage"], "ira": a["ira"]}
    rets = {k: A.ret(k) for k in inv}
    contrib = dict(f["contrib"])
    prop = a["property"]
    upfront = 0.0

    if plan is None:
        charges = ctx["card_spend"]
        budget = None
        order: list[str] = []
        autopay: set[str] = set()
        saved = f["residual"]  # stays in cash each month
    else:
        cuts = wealth.cut_split(ctx, plan.get("spending_targets") or [])
        charges = max(ctx["card_spend"] - cuts["card"], 0.0)
        budget = float(plan.get("monthly_debt_budget") or 0)
        order = [x for x in plan.get("extra_order") or [] if x in debts]
        for x in plan.get("extra_order") or []:
            if x not in debts:
                problems.append(f"extra_order: unknown debt {x}")
        autopay = {x for x in plan.get("autopay_full_cards") or [] if x in debts}
        pf = wealth.plan_flows(ctx, plan)
        for flow in pf["unknown"]:
            problems.append(f"redirect flow {flow!r} is not a movable flow")
        lim = ctx["movable_flows"][wealth.SURPLUS]["max_per_month"]
        if pf["surplus"] > lim + 0.5:
            problems.append(f"redirecting ${pf['surplus']:,.0f}/mo exceeds the ${lim:,.0f}/mo surplus left in checking")
        for dest, amt in pf["paused"].items():
            if amt > contrib.get(dest, 0.0) + 0.5:
                problems.append(f"pausing ${amt:,.0f}/mo of {dest} contributions exceeds the ${contrib.get(dest, 0):,.0f}/mo made")
            contrib[dest] = max(contrib.get(dest, 0.0) - amt, 0.0)
        # The budget is paid from today's payments + freed money; whatever it doesn't use is still saved.
        saved = f["residual"] + ctx["current_debt_payments"] + sum(pf["paused"].values()) + cuts["cash"] - budget
        minimums = sum(d["min_payment"] for d in debts.values())
        if budget + 0.005 < minimums:
            problems.append(f"monthly_debt_budget ${budget:,.0f} is below the ${minimums:,.0f} of minimums")
        # Lump sums at month 0.
        for ls in plan.get("lump_sums") or []:
            src, amt, to = ls.get("source"), float(ls.get("amount") or 0), ls.get("to_debt")
            if amt <= 0:
                continue
            if src not in wealth.LUMP_SOURCES:
                problems.append(f"lump sum source {src!r} not allowed")
                continue
            if to not in debts:
                problems.append(f"lump sum to unknown debt {to!r}")
                continue
            d = debts[to]
            if amt > d["bal"] + 0.005:
                problems.append(f"lump sum ${amt:,.0f} exceeds {d['name']} balance ${d['bal']:,.0f}")
                amt = d["bal"]
            if src == "cash":
                cash -= amt
            else:
                g = wealth._gross_needed(src, amt, ctx)
                if g > inv[src] + 0.5:
                    problems.append(f"{src}: needs ${g:,.0f} but only ${inv[src]:,.0f}")
                inv[src] -= g
                upfront += g - amt
            d["bal"] -= amt
            if d["bal"] <= 0.005 and d["payoff"] is None:
                d["payoff"] = 0
        if cash < -0.5:
            problems.append("lump sums take more cash than there is")

    floor = min(ctx["emergency_buffer"], a["cash"]) - 1
    cash_problem = False
    borrow_to = next((k for k, d in debts.items() if d["is_pnc_card"]), None) or \
        next((k for k, d in sorted(debts.items(), key=lambda kv: kv[1]["apr_pct"]) if d["is_card"]), None)
    borrowed = 0.0
    min_cash, min_cash_i = cash, 0

    def snapshot(i: int) -> dict:
        total = sum(d["bal"] for d in debts.values())
        high = sum(d["bal"] for d in debts.values() if d["high_cost"])
        invested = sum(inv.values())
        return {"month": wealth._label(i), "total_debt": round(total, 2), "high_cost_debt": round(high, 2),
                "cash": round(cash, 2), "invested": round(invested, 2),
                "net_worth": round(cash + invested + prop - total, 2),
                # per-account balances for the plan timeline (timeline.py); not sent to the LLM
                "balances": {"debts": {k: round(d["bal"], 2) for k, d in debts.items()},
                             "assets": {"cash": round(cash, 2), **{k: round(v, 2) for k, v in inv.items()},
                                        "property": round(prop, 2)}},
                # the same balances, rounded to dollars (plan-view breakdown)
                "parts": {"cash": round(cash), **{k: round(v) for k, v in inv.items()}, "property": round(prop),
                          "debts": {k: round(d["bal"]) for k, d in debts.items()}}}

    months = [snapshot(0)]
    first_pay: dict[str, float] = {}  # month-1 payment per debt, as wealth._run reports it
    high_free = 0 if months[0]["high_cost_debt"] <= 0.01 else None
    short_reported = False
    # Past the horizon, keep stepping (up to wealth.PAYOFF_SEARCH_MONTHS) only to find when high-cost debt
    # reaches $0, as wealth._run does; the reported months, net worth, interest and problems stay within it.
    for i in range(1, max(H, wealth.PAYOFF_SEARCH_MONTHS) + 1):
        if i > H and high_free is not None:
            break
        cash *= 1 + a["cash_yield_pct"] / 1200
        for k in inv:
            inv[k] *= 1 + (rets[k] or 0) / 1200
            inv[k] += contrib.get(k, 0.0)
        was_zero = {k for k, d in debts.items() if d["bal"] <= 0.005}
        for d in debts.values():
            it = d["bal"] * d["apr_pct"] / 1200
            d["bal"] += it
            if i <= H:
                d["interest"] += it
            if d["is_pnc_card"]:
                d["bal"] += charges
        left = 0.0
        if budget is None:
            for d in debts.values():
                d["bal"] -= min(d["baseline_payment"], d["bal"])
        else:
            left = budget
            for k, d in debts.items():  # autopay statement in full, then minimums
                due = d["bal"] if (k in autopay and k in was_zero) else min(d["min_payment"], d["bal"])
                pay = min(due, left)
                if pay + 0.005 < due and not short_reported and i <= H:
                    problems.append(f"{wealth._label(i)}: budget can't cover {d['name']}'s payment")
                    short_reported = True
                d["bal"] -= pay
                left -= pay
                if i == 1:
                    first_pay[k] = first_pay.get(k, 0.0) + pay
            for k in ([] if i < len(s_pause) and s_pause[i] else order):
                d = debts[k]
                pay = min(d["bal"], left)
                d["bal"] -= pay
                left -= pay
                if i == 1:
                    first_pay[k] = first_pay.get(k, 0.0) + pay
        cash += saved + left  # the residual (and any unused budget) stays in cash
        if shock is not None:
            cash += s_delta[i] if i < len(s_delta) else 0.0
            if cash < 0 and borrow_to is not None:
                debts[borrow_to]["bal"] += -cash
                if i <= H:
                    borrowed += -cash
                cash = 0.0
        if cash < min_cash and i <= H:
            min_cash, min_cash_i = cash, i
        for d in debts.values():
            if d["bal"] <= 0.005:
                d["bal"] = 0.0
                if d["payoff"] is None:
                    d["payoff"] = i
            elif d["payoff"] is not None and d["is_card"]:
                d["payoff"] = None  # card refilled
        if i > H:
            if sum(d["bal"] for d in debts.values() if d["high_cost"]) <= 0.01:
                high_free = i
            continue
        # A plan may not take cash below the buffer.
        if plan is not None and cash < floor and not cash_problem:
            problems.append(f"{wealth._label(i)}: cash ${cash:,.0f} falls below the {ctx['keep_months']:g}-month buffer "
                            f"(${ctx['emergency_buffer']:,.0f})")
            cash_problem = True
        months.append(snapshot(i))
        if months[-1]["high_cost_debt"] <= 0.01:
            if high_free is None:
                high_free = i
        else:
            high_free = None

    return {
        "feasible": not problems,
        "problems": problems,
        "debts": [{"account_id": d["account_id"], "name": d["name"], "apr_pct": d["apr_pct"], "balance": d["balance"],
                   "min_payment": d["min_payment"], "high_cost": d["high_cost"],
                   "payoff_month": wealth._label(d["payoff"]) if d["payoff"] is not None else None,
                   "interest": round(d["interest"], 2),
                   "first_month_payment": round(first_pay.get(d["account_id"], 0.0), 2)} for d in debts.values()],
        "months": months,
        "net_worth_24m": months[-1]["net_worth"],
        "net_worth_now": months[0]["net_worth"] + (upfront if plan else 0.0),
        "interest_total": round(sum(d["interest"] for d in debts.values()), 2),
        "high_cost_debt_free": wealth._label(high_free) if high_free is not None else None,
        "upfront_cost": round(upfront, 2),
        "monthly_card_spending": round(charges, 2),
        "monthly_saved": round(saved, 2),
        "min_cash": round(min_cash, 2),
        "min_cash_month": wealth._label(min_cash_i),
        "borrowed_on_card": round(borrowed, 2),
    }


# ---------------- the cushion picker (What if tab) ----------------

CUSHION_CHOICES = (2.0, 3.0, 4.0)
CASH_SOURCES = ("cash", "outside_savings")  # lump sums paid from savings (solver.CASH_POOLS)


def _cushion_plan(customer_id: str, plan: dict, months: float, live: bool) -> tuple[dict, dict]:
    """`plan` re-solved for a different cushion: same monthly budget, redirects and cuts, so only
    how much savings goes to debt today (and so the payoff dates) changes."""
    from . import solver

    budget = float(plan.get("monthly_debt_budget") or 0)
    inp = {"keep_emergency_months": months,
           "spending_targets": [{k: v for k, v in t.items() if k not in ("source", "buys", "lumpy")}
                                for t in plan.get("spending_targets") or []],
           "redirect_flows": {r["flow"]: r["amount_per_month"] for r in plan.get("redirects") or []},
           "min_debt_budget": budget, "max_debt_budget": budget}
    return solver.build_plan(customer_id, inp, live=live)


def _series(sim: dict) -> list[dict]:
    return [{"month": m["month"], "cash": round(m["cash"]), "debt": round(m["high_cost_debt"])} for m in sim["months"]]


def cushion_options(customer_id: str, plan: dict, *, live: bool = True) -> dict:
    """For a 2 / 3 / 4-month cushion (plus the plan's own, if different): what it pays from savings now, when
    high-cost debt hits $0, net worth in 2 years vs this plan, lowest savings, the month-by-month savings and
    high-cost debt with and without a 3-month job loss, and how much each preset shock puts on the card.
    The plan's own cushion uses `plan` as given; the others re-solve it (see _cushion_plan). Read-only."""
    cur = float(plan.get("keep_emergency_months") or A.get("default_emergency_months"))
    choices = sorted({*CUSHION_CHOICES, cur})
    job = next(p for p in PRESETS if p["id"] == "job_loss_3m")
    out, errors = [], {}
    for n in choices:
        try:
            if n == cur:
                p, ctx = plan, wealth.context(customer_id, keep_months=n, live=live)
            else:
                p, ctx = _cushion_plan(customer_id, plan, n, live)
        except Exception as e:  # noqa: BLE001 - a cushion the data can't support is just not offered
            errors[f"{n:g}"] = str(e)
            continue
        plain = _run_shock(ctx, p)
        shocks = {sc["id"]: _run_shock(ctx, p, build_shock(ctx, sc["events"])) for sc in PRESETS}
        lumps = p.get("lump_sums") or []
        out.append({
            "months": n,
            "current": n == cur,
            "emergency_buffer": round(ctx["emergency_buffer"], 2),
            "pay_from_savings": round(sum(float(l["amount"]) for l in lumps if l.get("source") in CASH_SOURCES), 2),
            "sell_investments": round(sum(float(l["amount"]) for l in lumps if l.get("source") not in CASH_SOURCES), 2),
            "high_cost_debt_free": plain["high_cost_debt_free"],
            "net_worth_24m": plain["net_worth_24m"],
            "min_cash": plain["min_cash"],
            "min_cash_month": plain["min_cash_month"],
            "below_buffer": plain["min_cash"] < ctx["emergency_buffer"] - 1,
            "series": _series(plain),
            "job_loss_series": _series(shocks[job["id"]]),
            "shocks": [{"id": sc["id"], "label": sc["label"], "borrowed_on_card": shocks[sc["id"]]["borrowed_on_card"],
                        "min_cash": shocks[sc["id"]]["min_cash"]} for sc in PRESETS],
        })
    base = next((o for o in out if o["current"]), None)
    for o in out:
        o["net_worth_delta"] = round(o["net_worth_24m"] - base["net_worth_24m"], 2) if base else None
    same = len({(o["pay_from_savings"], o["sell_investments"]) for o in out}) <= 1
    return {"current_months": cur, "monthly_expenses": round(base["emergency_buffer"] / cur, 2) if base and cur else None,
            "options": out, "same_plan": same, "errors": errors}
