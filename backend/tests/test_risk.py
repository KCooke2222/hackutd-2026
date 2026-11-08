"""Risk profile (planning/wealth.py merton_share / invest_share) and the money order's last step as a Nessie bill
(agent/execute.py _invest_step + bill_schedule). Offline: no Nessie calls."""

import pytest

from paidway.agent import execute
from paidway.data import seed
from paidway.planning import solver, wealth


def test_merton_share():
    # (mu - r) / (gamma * sigma^2) with mu 7%, sigma 16%: r = 5% -> 0.02 / (4 * 0.0256) for low
    assert wealth.merton_share("low", 5.0) == pytest.approx(0.02 / (4 * 0.0256))
    assert wealth.merton_share("high", 0.0) == 1.0  # clamped
    assert wealth.merton_share("medium", 9.0) == 0.0  # a loan above 7% beats stocks
    assert wealth.invest_share("medium", 6.0) == wealth.invest_share("high", 6.0) == 1.0
    assert wealth.invest_share("low", 6.0) < 1.0


@pytest.fixture(scope="module")
def c001():
    return {r: solver.solve("C001", {"risk_profile": r}, live=False, with_tradeoffs=False) for r in ("low", "medium")}


def test_low_puts_part_on_the_loan(c001):
    lo, med = c001["low"]["sim"]["investing"], c001["medium"]["sim"]["investing"]
    assert c001["low"]["plan"]["risk_profile"] == "low" and lo["risk_profile"] == "low"
    assert med["per_month"] > lo["per_month"] > 0
    assert med["total_24m"] > lo["total_24m"]
    assert "split between investing and the highest-rate loan" in c001["low"]["sim"]["money_order"]


def test_money_order_line(c001):
    line = c001["medium"]["sim"]["money_order"]
    assert line.startswith("Cards") and "cushion" in line and "into investing at ~7%" in line


def test_investing_is_a_scheduled_nessie_bill(c001):
    r = c001["medium"]
    _, steps = execute.schedule("C001", r["plan"], r["sim"], live=False)
    inv = next(s for s in steps if s["kind"] == "invest")
    assert inv["account_id"] == "L-C001-BROKERAGE-1" and "est. 7%/yr" in inv["step"]
    assert inv["runs"] == "agent" and "once high-cost debt is $0" in inv["step"]  # starts after the cards
    e = next(e for e in execute.bill_schedule(r["sim"], steps) if e["key"] == execute.INVEST_KEY)
    assert e["action"] == "new" and e["month"] == r["sim"]["investing"]["from"] and e["amount"] >= 1


def test_no_brokerage_gets_an_empty_mirror():
    plan = seed.mirror_plan("C002")
    m = next(m for m in plan if m["account_id"] == "N-C002-BROKERAGE")
    assert m["balance"] == 0 and m["nickname"] == "Brokerage"
    assert not any(m["account_id"].startswith("N-") for m in seed.mirror_plan("C001"))  # C001 has one
