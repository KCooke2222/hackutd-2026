"""Reality check: can the debt shrink with this income and spending, even after every lever?"""

import copy

import pytest

from zeroday import wealth


@pytest.fixture(scope="module")
def ctx():
    return wealth.context("C003", live=False)


def short(ctx, residual: float) -> dict:
    """The same customer, but spending `residual` more than they earn (no unknown outflow to redirect)."""
    c = copy.deepcopy(ctx)
    c["flows"]["residual"] = residual
    c["redirectable_outflow"] = 0.0
    return c


@pytest.mark.parametrize("cid", ["C001", "C002", "C003"])
def test_demo_customers_can_shrink(cid):
    rc = wealth.reality_check(cid, live=False)
    assert rc["realistic"] is True
    assert rc["gap_per_month"] == 0 and rc["close_gap"] is None
    m = rc["monthly"]
    assert m["principal_paydown"] == pytest.approx(m["debt_room"] - m["interest"] - m["card_charges"], abs=0.02)
    assert m["debt_room"] >= m["minimums"]
    assert "can shrink" in rc["message"]


def test_cuts_respect_validator_limits(ctx):
    rc = wealth._reality(ctx)
    for c in rc["levers"]["cuts"]:
        s = ctx["spend"][c["category"]]
        assert not s["essential"]
        assert c["target"] >= s["min"] - 0.01
        assert c["target"] >= s["avg"] * 0.7 - 0.01


def test_gap_when_spending_exceeds_income(ctx):
    base = wealth._reality(short(ctx, 0.0))
    # Push the residual down until the room is 400 below what the debt needs each month.
    need = max(base["monthly"]["minimums"], base["monthly"]["interest"] + base["monthly"]["card_charges"])
    residual = need - 400 - (base["monthly"]["debt_room"])
    rc = wealth._reality(short(ctx, residual))
    assert rc["realistic"] is False
    assert rc["gap_per_month"] == pytest.approx(400, abs=0.05)
    g = rc["close_gap"]
    assert g["spend_less_per_month"] == g["earn_more_per_month"] == rc["gap_per_month"]
    assert g["income_target_per_month"] == pytest.approx(ctx["flows"]["income"] + 400, abs=0.05)
    assert rc["message"].startswith("Even after every realistic change you are $400/mo short")


def test_gap_too_big_for_cuts_needs_income(ctx):
    rc = wealth._reality(short(ctx, -20_000))
    g = rc["close_gap"]
    assert g["spending_alone_can_close"] is False
    left = rc["levers"]["discretionary_left_per_month"]
    assert g["min_extra_income_if_spending_maxed"] == pytest.approx(rc["gap_per_month"] - left, abs=0.05)
    assert "Spending cuts alone can't do it" in rc["message"]


def test_break_even_is_not_realistic(ctx):
    base = wealth._reality(short(ctx, 0.0))
    m = base["monthly"]
    growth = m["interest"] + m["card_charges"]
    rc = wealth._reality(short(ctx, max(growth, m["minimums"]) - m["debt_room"]))
    assert rc["realistic"] is False and rc["gap_per_month"] >= 1


def test_flags_reliance_on_redirect(ctx):
    base = wealth._reality(short(ctx, 0.0))
    m = base["monthly"]
    need = max(m["minimums"], m["interest"] + m["card_charges"])
    c = short(ctx, need - m["debt_room"] - 300)  # 300 short without the redirect
    c["redirectable_outflow"] = 1000.0
    rc = wealth._reality(c)
    assert rc["realistic"] is True and rc["relies_on_redirect"] is True
    assert "unknown account" in rc["message"]


def test_api_endpoint():
    from fastapi.testclient import TestClient

    from zeroday.api import app

    c = TestClient(app)
    r = c.get("/api/customers/C001/reality-check")
    assert r.status_code == 200 and "realistic" in r.json()
    assert c.get("/api/customers/NOPE/reality-check").status_code == 404
