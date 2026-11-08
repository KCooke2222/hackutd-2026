"""The greedy solver against an LP optimum (scipy linprog) of the same 24-month problem.

The LP has the plan's rules but none of its heuristics: monthly payment variables per debt, linear interest,
card charges, the cash buffer every month, lump sums from any allowed source split any way, any sweep
redirect and any contribution pause up to their limits, the monthly budget cap, low-rate / secured debt on
minimums only. Objective: net worth at 24 months. Minimums on high-cost debt are relaxed (the LP may skip a
minimum to pay a higher APR), so the LP is an upper bound and the greedy must come within $50 of it.
"""

from __future__ import annotations

import numpy as np
import pytest
from scipy.optimize import linprog
from scipy.sparse import lil_matrix

from zeroday import assumptions as A
from zeroday import categories, solver, wealth
from zeroday.agent.validator import validate_plan_v2

CUSTOMERS = ["C001", "C002", "C003"]
TOL = 50.0


class LP:
    def __init__(self):
        self.n, self.lo, self.hi, self.names = 0, [], [], []
        self.eq, self.ub = [], []  # (coeffs {var: c}, rhs)

    def var(self, name, lo=0.0, hi=None):
        self.lo.append(lo)
        self.hi.append(hi)
        self.names.append(name)
        self.n += 1
        return self.n - 1

    def solve(self, objective: dict):
        def mat(rows):
            m = lil_matrix((len(rows), self.n))
            for i, (co, _) in enumerate(rows):
                for j, c in co.items():
                    m[i, j] += c
            return m.tocsr(), np.array([r for _, r in rows])

        c = np.zeros(self.n)
        for j, v in objective.items():
            c[j] -= v  # maximize
        Ae, be = mat(self.eq)
        Au, bu = mat(self.ub) if self.ub else (None, None)
        res = linprog(c, A_ub=Au, b_ub=bu, A_eq=Ae, b_eq=be, bounds=list(zip(self.lo, self.hi)), method="highs")
        assert res.status == 0, res.message
        return res


def lp_optimum(cid: str, targets: list[dict], keep: float | None = None) -> dict:
    ctx = wealth.context(cid, keep_months=keep, live=False)
    H, a, f = A.get("horizon_months"), ctx["assets"], ctx["flows"]
    targets = [solver.complete_target(cid, ctx, t) for t in targets]
    cuts = wealth.cut_split(ctx, targets)
    charges = max(ctx["card_spend"] - cuts["card"], 0.0)
    elig = [d for d in ctx["debts"] if d["extra_eligible"] and d["balance"] > 0]
    inel = [d for d in ctx["debts"] if d not in elig]
    rc, ro = 1 + a["cash_yield_pct"] / 1200, 1 + a["outside_yield_pct"] / 1200
    rk = {k: 1 + (A.ret(k) or 0) / 1200 for k in ("cd", "brokerage", "ira")}
    base_cash = [m["cash"] for m in wealth.simulate_plan(cid, None, ctx=ctx)["months"]]
    floor = min(ctx["emergency_buffer"], a["cash"] + a["outside_savings"]) - 1

    # Low-rate / secured debt: minimums only (the plan's rule), a fixed schedule.
    inel_pay, inel_bal = [0.0] * (H + 1), 0.0
    for d in inel:
        assert not d["is_pnc_card"]
        b = d["balance"]
        for t in range(1, H + 1):
            b *= 1 + d["apr_pct"] / 1200
            p = min(d["min_payment"], b)
            b -= p
            inel_pay[t] += p
        inel_bal += b

    lp = LP()
    srcs = ["cash", "outside_savings", "cd", "brokerage"]
    gross = {s: wealth._gross_needed(s, 1.0, ctx) for s in srcs}
    L = {(s, d["account_id"]): lp.var(f"L_{s}_{d['account_id']}") for s in srcs for d in elig}
    mov = ctx["movable_flows"]
    sweep = lp.var("sweep", 0, mov[wealth.SWEEP]["max_per_month"])
    pause = {m["dest"]: lp.var(f"pause_{k}", 0, m["max_per_month"]) for k, m in mov.items()
             if m["flag"] == categories.PAUSE and m["max_per_month"] > 0}

    def lumps(s):
        return {L[s, d["account_id"]]: 1.0 for d in elig}

    lp.ub.append(({**lumps("cash")}, ctx["lump_available"]["cash"]))
    lp.ub.append(({**lumps("outside_savings")}, ctx["lump_available"]["outside_savings"]))
    lp.ub.append(({**lumps("cash"), **lumps("outside_savings")}, ctx["lump_available"]["cash_total"]))
    for s in ("cd", "brokerage"):
        lp.ub.append(({j: gross[s] for j in lumps(s)}, ctx["ladder"][s]["available"]))
    for d in elig:
        lp.ub.append(({L[s, d["account_id"]]: 1.0 for s in srcs}, d["balance"]))

    # State: C (PNC cash, only yields), O (savings), investments, eligible balances. Expressions as {var: c} + const.
    C = ({j: -1.0 for j in lumps("cash")}, a["cash"])
    O = ({j: -1.0 for j in lumps("outside_savings")}, a["outside_savings"])
    inv = {k: ({j: -gross[k] for j in lumps(k)} if k in ("cd", "brokerage") else {}, a[k]) for k in ("cd", "brokerage", "ira")}
    bal = {d["account_id"]: ({L[s, d["account_id"]]: -1.0 for s in srcs}, d["balance"]) for d in elig}

    def scale(e, k):
        return ({j: c * k for j, c in e[0].items()}, e[1] * k)

    def fresh(name, e_co, e_const, lo=0.0):
        """New variable v = expression (keeps the LP sparse)."""
        v = lp.var(name, lo)
        lp.eq.append(({**{j: -c for j, c in e_co.items()}, v: 1.0}, e_const))
        return ({v: 1.0}, 0.0)

    inflow_const = f["residual"] + ctx["current_debt_payments"] + cuts["cash"]
    budget_const = ctx["current_debt_payments"] + cuts["cash"]
    for t in range(1, H + 1):
        C = scale(C, rc)
        for k in inv:
            co, c0 = scale(inv[k], rk[k])
            c0 += f["contrib"].get(k, 0.0)
            if k in pause:
                co = {**co, pause[k]: co.get(pause[k], 0.0) - 1.0}
            inv[k] = fresh(f"inv_{k}_{t}", co, c0, lo=None) if co else (co, c0)
        pays = {}
        for d in elig:
            co, c0 = scale(bal[d["account_id"]], 1 + d["apr_pct"] / 1200)
            if d["is_pnc_card"]:
                c0 += charges
            p = lp.var(f"p_{d['account_id']}_{t}")
            pays[d["account_id"]] = p
            co = {**co, p: co.get(p, 0.0) - 1.0}
            bal[d["account_id"]] = fresh(f"b_{d['account_id']}_{t}", co, c0)  # b >= 0
        # Budget cap: debt payments this month <= today's payments + sweep redirect + pauses + checking cuts.
        lp.ub.append(({**{p: 1.0 for p in pays.values()}, sweep: -1.0, **{v: -1.0 for v in pause.values()}},
                      budget_const - inel_pay[t]))
        co, c0 = scale(O, ro)
        co = {**co, **{p: co.get(p, 0.0) - 1.0 for p in pays.values()}}
        for v in pause.values():
            co[v] = co.get(v, 0.0) + 1.0
        O = fresh(f"O_{t}", co, c0 + inflow_const - inel_pay[t])  # O >= 0: residuals here are non-negative
        # Cash buffer every month.
        need = min(floor, base_cash[t] - 1)
        row = {j: -c for j, c in C[0].items()}
        for j, c in O[0].items():
            row[j] = row.get(j, 0.0) - c
        lp.ub.append((row, C[1] + O[1] - need))

    obj, const = {}, a["property"] - inel_bal
    for e, sign in [(C, 1), (O, 1), *[(inv[k], 1) for k in inv], *[(bal[k], -1) for k in bal]]:
        for j, c in e[0].items():
            obj[j] = obj.get(j, 0.0) + sign * c
        const += sign * e[1]
    res = lp.solve(obj)
    return {"net_worth_24m": -res.fun + const, "x": dict(zip(lp.names, res.x))}


def floor_cuts(cid: str, cats=("dining", "shopping")) -> list[dict]:
    ctx = wealth.context(cid, live=False)
    return [{"category": fl["category"], "target": fl["target"]} for c in cats if (fl := solver.floor_target(cid, ctx, c))]


@pytest.mark.parametrize("cid", CUSTOMERS)
@pytest.mark.parametrize("case", ["default", "cuts", "tight"])
def test_greedy_matches_lp(cid, case):
    """default inputs; the person cuts dining + shopping to their floors; a 9-month buffer (little spare cash, so
    CD / brokerage sales, pauses and months of avalanche payments decide the result)."""
    targets = floor_cuts(cid) if case == "cuts" else []
    keep = 9.0 if case == "tight" else None
    r = solver.solve(cid, {"spending_targets": targets, "keep_emergency_months": keep}, live=False, with_tradeoffs=False)
    greedy = r["sim"]["net_worth_24m"]
    opt = lp_optimum(cid, targets, keep=keep)["net_worth_24m"]
    assert greedy >= opt - TOL, f"{cid}: greedy ${greedy:,.2f} vs LP ${opt:,.2f}"
    assert greedy <= opt + 1, f"{cid}: greedy beats the LP; the LP is mis-modelled (${greedy:,.2f} vs ${opt:,.2f})"


@pytest.mark.parametrize("cid", CUSTOMERS)
def test_plan_is_valid_and_never_invents_cuts(cid):
    r = solver.solve(cid, live=False)
    validate_plan_v2(cid, r["plan"], live=False)
    assert r["plan"]["spending_targets"] == []
    for row in r["tradeoffs"]:
        if row["category"] != "all of the above":
            assert categories.spending_flag(row["category"])["flag"] in (categories.TRIM, categories.CANCEL)


def test_tradeoff_floor_respects_limits():
    ctx = wealth.context("C001", live=False)
    for cat, c in ctx["spend"].items():
        fl = solver.floor_target("C001", ctx, cat)
        if c["essential"]:
            assert fl is None
        elif fl and categories.spending_flag(cat)["flag"] == categories.TRIM:
            pct = categories.spending_flag(cat)["max_pct"]
            assert fl["target"] >= c["min"] - 0.01
            assert fl["target"] >= c["avg"] * (1 - pct / 100) - 0.01


def test_person_cuts_pass_validator():
    targets = floor_cuts("C001", ("dining", "shopping", "subscriptions"))
    r = solver.solve("C001", {"spending_targets": targets}, live=False, with_tradeoffs=False)
    assert {t["category"] for t in r["plan"]["spending_targets"]} == {t["category"] for t in targets}
