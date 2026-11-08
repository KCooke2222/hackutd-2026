"""The greedy solver against an LP optimum (scipy milp / HiGHS) of the same 24-month problem.

The LP has the plan's rules but none of its heuristics: monthly payment variables per debt, linear interest,
card charges, the cash buffer every month, lump sums from any allowed source split any way, any surplus
redirect and any contribution pause up to their limits, the monthly budget cap, low-rate / secured debt on
minimums only, and the minimum rule on high-cost debt (each month pay at least the minimum or pay it off: one
binary per debt-month). Objective: net worth at 24 months. Payments may split any way (no avalanche order), so
the LP is an upper bound on any plan and the greedy must come within $50 of it.
"""

from __future__ import annotations

import numpy as np
import pytest
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import lil_matrix

from paidway.agent.validator import validate_plan_v2
from paidway.data import categories
from paidway.planning import assumptions as A
from paidway.planning import solver, wealth

CUSTOMERS = ["C001", "C002", "C003"]
TOL = 50.0


class LP:
    def __init__(self):
        self.n, self.lo, self.hi, self.names, self.ints = 0, [], [], [], []
        self.eq, self.ub = [], []  # (coeffs {var: c}, rhs)

    def var(self, name, lo=0.0, hi=None, integer=False):
        self.lo.append(-np.inf if lo is None else lo)
        self.hi.append(np.inf if hi is None else hi)
        self.names.append(name)
        self.ints.append(1 if integer else 0)
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
        Au, bu = mat(self.ub)
        cons = [LinearConstraint(Ae, be, be), LinearConstraint(Au, -np.inf, bu)]
        res = milp(c, constraints=cons, integrality=np.array(self.ints), bounds=Bounds(self.lo, self.hi),
                   options={"mip_rel_gap": 1e-9})
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
    # Outside savings (when the data has it) is O; without it O is just the cash the monthly flows land in.
    has_out = "outside_savings" in ctx["lump_available"]
    out_bal, out_yield = a.get("outside_savings", 0.0), a.get("outside_yield_pct", a["cash_yield_pct"])
    rc, ro = 1 + a["cash_yield_pct"] / 1200, 1 + out_yield / 1200
    rk = {k: 1 + (A.ret(k) or 0) / 1200 for k in ("cd", "brokerage", "ira")}
    base_cash = [m["cash"] for m in wealth.simulate_plan(cid, None, ctx=ctx)["months"]]
    floor = min(ctx["emergency_buffer"], a["cash"] + out_bal) - 1

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
    srcs = ["cash", *(["outside_savings"] if has_out else []), "cd", "brokerage"]
    gross = {s: wealth._gross_needed(s, 1.0, ctx) for s in srcs}
    L = {(s, d["account_id"]): lp.var(f"L_{s}_{d['account_id']}") for s in srcs for d in elig}
    mov = ctx["movable_flows"]
    redirect = [k for k, m in mov.items() if m["flag"] == categories.REDIRECT]
    sweep = lp.var("sweep", 0, sum(mov[k]["max_per_month"] for k in redirect))
    pause = {m["dest"]: lp.var(f"pause_{k}", 0, m["max_per_month"]) for k, m in mov.items()
             if m["flag"] == categories.PAUSE and m["max_per_month"] > 0}

    def lumps(s):
        return {L[s, d["account_id"]]: 1.0 for d in elig}

    lp.ub.append(({**lumps("cash")}, ctx["lump_available"]["cash"]))
    if has_out:
        lp.ub.append(({**lumps("outside_savings")}, ctx["lump_available"]["outside_savings"]))
        lp.ub.append(({**lumps("cash"), **lumps("outside_savings")}, ctx["lump_available"]["cash_total"]))
    else:
        lp.ub.append(({**lumps("cash")}, ctx["lump_available"]["cash_total"]))
    for s in ("cd", "brokerage"):
        lp.ub.append(({j: gross[s] for j in lumps(s)}, ctx["ladder"][s]["available"]))
    for d in elig:
        lp.ub.append(({L[s, d["account_id"]]: 1.0 for s in srcs}, d["balance"]))

    # State: C (PNC cash, only yields), O (savings), investments, eligible balances. Expressions as {var: c} + const.
    C = ({j: -1.0 for j in lumps("cash")}, a["cash"])
    O = ({j: -1.0 for j in lumps("outside_savings")} if has_out else {}, out_bal)
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
            # The plan's minimum rule: pay at least the minimum, or pay the debt off (z = 0 means b = 0).
            z = lp.var(f"z_{d['account_id']}_{t}", 0, 1, integer=True)
            big = d["balance"] * 2 + charges * (H + 1) + 1
            lp.ub.append(({p: -1.0, z: d["min_payment"]}, 0.0))
            lp.ub.append(({**bal[d["account_id"]][0], z: -big}, -bal[d["account_id"]][1]))
        # Budget cap: debt payments this month <= today's payments + surplus redirect + pauses + checking cuts.
        lp.ub.append(({**{p: 1.0 for p in pays.values()}, sweep: -1.0, **{v: -1.0 for v in pause.values()}},
                      budget_const - inel_pay[t]))
        co, c0 = scale(O, ro)
        co = {**co, **{p: co.get(p, 0.0) - 1.0 for p in pays.values()}}
        for v in pause.values():
            co[v] = co.get(v, 0.0) + 1.0
        # With an outside account O >= 0 (short months draw it first; residuals there are non-negative). Without
        # one, O is part of PNC cash and may go negative: only C + O (the buffer row below) is bounded.
        O = fresh(f"O_{t}", co, c0 + inflow_const - inel_pay[t], lo=0.0 if has_out else None)
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
    r = solver.solve(cid, {"spending_targets": targets, "keep_emergency_months": keep, "suggest_cuts": False},
                     live=False, with_tradeoffs=False)
    greedy = r["sim"]["net_worth_24m"]
    opt = lp_optimum(cid, targets, keep=keep)["net_worth_24m"]
    assert greedy >= opt - TOL, f"{cid}: greedy ${greedy:,.2f} vs LP ${opt:,.2f}"
    assert greedy <= opt + 1, f"{cid}: greedy beats the LP; the LP is mis-modelled (${greedy:,.2f} vs ${opt:,.2f})"


@pytest.mark.parametrize("cid", CUSTOMERS)
def test_plan_is_valid_and_only_suggests_cuts(cid):
    assert solver.solve(cid, {"suggest_cuts": False}, live=False, with_tradeoffs=False)["plan"]["spending_targets"] == []
    r = solver.solve(cid, live=False)
    validate_plan_v2(cid, r["plan"], live=False)
    assert all(t["source"] == "suggested" for t in r["plan"]["spending_targets"])
    assert len(r["plan"]["spending_targets"]) <= solver.SUGGEST_MAX
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
    agreed = {t["category"] for t in r["plan"]["spending_targets"] if t["source"] == "agreed"}
    assert agreed == {t["category"] for t in targets}


# ---------------- suggested cuts ----------------

def _suggested(r):
    return [t for t in r["plan"]["spending_targets"] if t["source"] == "suggested"]


@pytest.mark.parametrize("cid", CUSTOMERS)
def test_suggested_cuts_stay_within_floor_and_limits(cid):
    ctx = wealth.context(cid, live=False)
    r = solver.solve(cid, live=False, with_tradeoffs=False)
    for t in _suggested(r):
        c, flag = ctx["spend"][t["category"]], categories.spending_flag(t["category"])
        assert flag["flag"] in (categories.TRIM, categories.CANCEL) and not c["essential"]
        assert t["target"] >= solver.floor_target(cid, ctx, t["category"])["target"] - 0.01
        if flag["flag"] == categories.TRIM:
            assert t["target"] >= c["min"] - 0.01
            assert t["target"] >= c["avg"] * (1 - flag["max_pct"] / 100) - 0.01
        assert c["avg"] - t["target"] >= solver.SUGGEST_MIN_CUT - 0.01
        assert t["how"] and all(len(h.split()) <= 8 for h in t["how"])
        assert t["buys"]["net_worth_gain"] > 0
    validate_plan_v2(cid, r["plan"], live=False)  # floors and validator agree


def test_suggestions_prefer_lumpy_and_beat_no_cuts():
    r = solver.solve("C001", live=False, with_tradeoffs=False)
    sugg = _suggested(r)
    assert sugg and all(t["lumpy"] for t in sugg)  # C001: travel / gifts / home over dining and shopping
    assert "travel" in {t["category"] for t in sugg}
    nc = r["sim"]["no_cuts"]
    assert r["sim"]["net_worth_24m"] > nc["net_worth_24m"]
    assert nc["suggested_buys"]["months_sooner"] >= 1


def test_suggestions_respect_declines_agreed_and_minimums():
    base = {t["category"] for t in _suggested(solver.solve("C001", live=False, with_tradeoffs=False))}
    declined = sorted(base)[:2]
    r = solver.solve("C001", {"declined_cuts": declined}, live=False, with_tradeoffs=False)
    assert not {t["category"] for t in r["plan"]["spending_targets"]} & set(declined)
    # an agreed target is kept as given, never replaced by a suggestion
    r = solver.solve("C001", {"spending_targets": [{"category": "travel", "target": 600}]}, live=False, with_tradeoffs=False)
    travel = [t for t in r["plan"]["spending_targets"] if t["category"] == "travel"]
    assert len(travel) == 1 and travel[0]["source"] == "agreed" and travel[0]["target"] == 600
    # a relayed suggestion is not an agreement: it is remade
    r2 = solver.solve("C001", {"spending_targets": [{"category": "travel", "target": 600, "source": "suggested"}]},
                      live=False, with_tradeoffs=False)
    assert [t["target"] for t in _suggested(r2) if t["category"] == "travel"] != [600]
    # category_min floors a suggestion
    r3 = solver.solve("C001", {"category_min": {"travel": 650}}, live=False, with_tradeoffs=False)
    assert all(t["target"] >= 650 for t in r3["plan"]["spending_targets"] if t["category"] == "travel")


def test_fixed_and_ask_never_suggested():
    for cid in CUSTOMERS:
        ctx = wealth.context(cid, live=False)
        for cat, c in ctx["spend"].items():
            if c["essential"] or categories.spending_flag(cat)["flag"] not in (categories.TRIM, categories.CANCEL):
                assert solver.suggest_target(cid, ctx, cat) is None


def test_net_paydown_is_gross_minus_new_card_purchases():
    r = solver.solve("C001", live=False, with_tradeoffs=False)
    ctx = wealth.context("C001", live=False)
    b = r["sim"]["budget"]
    assert b["current_net_paydown_per_month"] == pytest.approx(ctx["current_debt_payments"] - ctx["card_spend"], abs=0.01)
    assert b["net_paydown_per_month"] == pytest.approx(b["monthly_debt_budget"] - r["sim"]["monthly_card_spending"], abs=0.01)
    assert r["brief"]["paydown"]["net_paydown_per_month"] == b["net_paydown_per_month"]
    # no double count: the baseline's month-1 debt change is purchases + interest - gross payments
    base = wealth.simulate_plan("C001", None, ctx=ctx)
    m0, m1 = base["months"][0]["total_debt"], base["months"][1]["total_debt"]
    interest = sum(d["balance"] * d["apr_pct"] / 1200 for d in ctx["debts"])
    paid = sum(min(d["baseline_payment"], d["balance"] + d["balance"] * d["apr_pct"] / 1200 + (ctx["card_spend"] if d["is_pnc_card"] else 0))
               for d in ctx["debts"])
    assert m1 - m0 == pytest.approx(ctx["card_spend"] + interest - paid, abs=1)


def test_underwater_still_gets_a_sensible_plan():
    rc0 = wealth.reality_check("C027", live=False)
    r = solver.solve("C027", live=False, with_tradeoffs=False)
    validate_plan_v2("C027", r["plan"], live=False)
    assert r["sim"]["net_worth_24m"] >= r["sim"]["no_cuts"]["net_worth_24m"]
    assert r["sim"]["no_cuts"]["net_worth_24m"] >= r["sim"]["baseline_net_worth_24m"]
    assert wealth.reality_check("C027", live=False) == rc0  # suggestions live in solve(); reality_check is untouched
