"""Reality check: can the debt shrink with this income and spending, even with the solver's best plan
(every optional cut at its floor)? If not, the monthly gap."""

import copy

import pytest
from fastapi.testclient import TestClient

from zeroday import solver, wealth
from zeroday.api import app

CID = "C003"


@pytest.fixture(scope="module")
def ctx():
    return wealth.context(CID, live=False)


def short(ctx, residual: float) -> dict:
    """The same customer, but with `residual` left each month (negative = spending more than they earn)."""
    c = copy.deepcopy(ctx)
    c["flows"]["residual"] = residual
    surplus = c["movable_flows"].get("monthly_surplus")
    if surplus:
        surplus["max_per_month"] = max(residual, 0.0)
    return c


def rc_for(c) -> dict:
    return wealth.reality_check(CID, live=False, ctx=c)


@pytest.mark.parametrize("cid", ["C001", "C002", "C003"])
def test_demo_customers(cid):
    rc = wealth.reality_check(cid, live=False)
    m = rc["monthly"]
    assert m["principal_paydown"] == pytest.approx(m["debt_room"] - m["interest"] - m["card_charges"], abs=0.02)
    if rc["realistic"]:
        assert rc["gap_per_month"] == 0 and rc["close_gap"] is None
        assert m["debt_room"] >= m["minimums"] - 0.01
        assert "can shrink" in rc["message"]
    else:
        assert rc["gap_per_month"] >= 1
    assert "relies_on_redirect" not in rc


def test_uses_the_solver_plan_with_floors(ctx):
    rc = rc_for(ctx)
    floors = {ft["category"]: ft["target"] for c in ctx["spend"] if (ft := solver.floor_target(CID, ctx, c))}
    cuts = {c["category"]: c["target"] for c in rc["levers"]["cuts"]}
    assert cuts.keys() == floors.keys()
    for cat, t in cuts.items():
        s = ctx["spend"][cat]
        assert not s["essential"]
        assert t == pytest.approx(floors[cat], abs=0.01)
        assert t >= s["min"] - 0.01 or t == 0  # a cancelled recurring charge can go to $0


def test_gap_when_spending_exceeds_income(ctx):
    rc = rc_for(short(ctx, -20_000))
    assert rc["realistic"] is False
    gap = rc["gap_per_month"]
    g = rc["close_gap"]
    assert g["spend_less_per_month"] == g["earn_more_per_month"] == gap
    assert g["income_target_per_month"] == pytest.approx(ctx["flows"]["income"] + gap, abs=0.05)
    assert rc["message"].startswith(f"Even after every realistic change you are ${gap:,.0f}/mo short")
    assert g["spending_alone_can_close"] is False
    left = rc["levers"]["discretionary_left_per_month"]
    assert g["min_extra_income_if_spending_maxed"] == pytest.approx(gap - left, abs=0.05)
    assert "Spending cuts alone can't do it" in rc["message"]


def test_closing_the_gap_makes_it_realistic(ctx):
    res = -3000.0
    rc = rc_for(short(ctx, res))
    assert rc["realistic"] is False
    fixed = rc_for(short(ctx, res + rc["gap_per_month"] + 1))
    assert fixed["realistic"] is True


def test_break_even_is_not_realistic(ctx):
    base = rc_for(short(ctx, 0.0))
    rc = rc_for(short(ctx, -base["monthly"]["principal_paydown"]))
    assert rc["realistic"] is False and rc["gap_per_month"] >= 1


def test_api():
    c = TestClient(app)
    r = c.get(f"/api/customers/{CID}/reality-check")
    assert r.status_code == 200 and "realistic" in r.json()
    assert c.get("/api/customers/NOPE/reality-check").status_code == 404
