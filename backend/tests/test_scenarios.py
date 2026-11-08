"""PR 6 scenarios on C001: shocks through the wealth month loop, adjusted plan, verdicts, Monte Carlo range."""

import pytest

from test_wealth import CID, REFERENCE
from zeroday import scenarios as S
from zeroday import wealth


@pytest.fixture(scope="module")
def ctx():
    return wealth.context(CID, keep_months=REFERENCE["keep_emergency_months"], live=False)


@pytest.fixture(scope="module")
def run():
    return S.run_scenarios(CID, REFERENCE, live=False)


def by_id(run, sid):
    return next(r for r in run["results"] if r["id"] == sid)


def test_zero_shock_is_neutral(ctx):
    plain = S._run_shock(ctx, REFERENCE)
    shocked = S._run_shock(ctx, REFERENCE, S.build_shock(ctx, []))
    assert shocked == plain
    # The shock loop is wealth._run's loop: with no shock they agree month by month.
    solver_sim = wealth._run(ctx, REFERENCE)
    assert {k: v for k, v in plain.items() if k in solver_sim} == solver_sim
    assert wealth.simulate_plan(CID, REFERENCE, live=False)["net_worth_24m"] == plain["net_worth_24m"]


def test_build_shock_events(ctx):
    inc = ctx["flows"]["income"]
    s = S.build_shock(ctx, [{"kind": "job_loss", "start": 2, "months": 3}, {"kind": "expense", "start": 2, "amount": 500}])
    assert s["months"] == [2, 3, 4]
    assert s["cash_delta"][2] == pytest.approx(-inc - 500)
    assert s["cash_delta"][5] == 0
    with pytest.raises(ValueError):
        S.build_shock(ctx, [{"kind": "meteor"}])


def test_presets_all_run(run):
    assert {r["id"] for r in run["results"]} == {p["id"] for p in S.PRESETS}
    for r in run["results"]:
        assert r["verdict"] in ("holds", "bends", "breaks")
        assert r["why"]
        assert len(r["sim"]["months"]) == len(run["reference"]["months"])


def test_job_loss_hurts(run):
    r = by_id(run, "job_loss_3m")
    ref = run["reference"]
    assert r["sim"]["net_worth_24m"] < ref["net_worth_24m"]
    assert r["verdict"] in ("bends", "breaks")
    if ref["high_cost_debt_free"] and r["sim"]["high_cost_debt_free"]:
        assert r["sim"]["high_cost_debt_free"] >= ref["high_cost_debt_free"]


def test_emergency_costs_at_least_its_amount(run):
    assert by_id(run, "emergency_2k")["nw_delta"] <= -2000


def test_adjusted_never_borrows_more(run):
    for r in run["results"]:
        assert r["sim"]["borrowed_on_card"] >= 0
        assert r["adjusted"]["borrowed_on_card"] <= r["sim"]["borrowed_on_card"] + 0.01
        assert r["adjusted"]["advice"]


def test_negative_cash_goes_on_the_card(ctx):
    big = S.build_shock(ctx, [{"kind": "expense", "start": 1, "amount": ctx["assets"]["cash"] + 5000}])
    sim = S._run_shock(ctx, REFERENCE, big)
    assert sim["borrowed_on_card"] > 0
    assert min(m["cash"] for m in sim["months"]) >= 0


def test_monte_carlo_range():
    a = S.monte_carlo(CID, REFERENCE, runs=60, seed=3, live=False)
    b = S.monte_carlo(CID, REFERENCE, runs=60, seed=3, live=False)
    assert a == b
    for m in a["months"]:
        assert m["p10"] <= m["p50"] <= m["p90"]
    assert 0 <= a["p_high_cost_free"] <= 1
