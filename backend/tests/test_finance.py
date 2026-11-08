import pytest

from paidway.planning import finance


def test_stuck_balance_never_pays_off():
    # $6,400 at 22.5%, paying $500 but adding $380/mo: interest (~$120) eats the rest.
    r = finance.simulate_payoff(6400, 22.5, 500, 380)
    assert r["pays_off"] is False
    assert r["months_to_zero"] is None
    assert r["interest_first_12m"] == pytest.approx(1440, abs=15)


def test_lump_sum_and_cap_pays_off():
    r = finance.simulate_payoff(6400, 22.5, 500, 250, lump_sum=3000)
    assert r["pays_off"] is True
    assert r["months_to_zero"] == 16
    assert r["total_interest"] == pytest.approx(562, abs=25)


def test_zero_apr_is_simple_division():
    r = finance.simulate_payoff(1000, 0, 100)
    assert r["months_to_zero"] == 10
    assert r["total_interest"] == 0


def test_negative_input_rejected():
    with pytest.raises(ValueError):
        finance.simulate_payoff(-1, 22.5, 100)


def test_refill_breakdown_c001_shows_refill():
    r = finance.refill_breakdown("C001")
    assert r["has_card"] is True
    avg = r["monthly_avg"]
    assert avg["payment"] > 1000
    # The balance barely moves: new spending + interest refill most of the payment.
    assert avg["new_spending"] + avg["interest"] > 0.9 * avg["payment"]
    assert r["share_of_payment_refilled"] > 0.9
    assert set(r["new_spending_by_category"]) >= {"groceries", "dining"}


def test_refill_breakdown_no_card():
    from paidway.data import pnc_data

    no_card = [c for c in pnc_data.customer_ids() if not pnc_data.card_account(c)]
    if not no_card:
        pytest.skip("every customer has a card")
    assert finance.refill_breakdown(no_card[0])["has_card"] is False


def test_project_stuck_card_stays_flat():
    # C001-like: payment barely covers spending plus interest.
    r = finance.project(6571, 22.5, 1541.12, 1422.79, months=24)
    assert len(r["balances"]) == 25
    assert r["payoff_month"] is None
    assert r["balances"][-1] == pytest.approx(6571, abs=200)
    assert r["interest"] == pytest.approx(24 * 123, abs=150)


def test_project_lump_sum_with_paid_in_full_stays_at_zero():
    r = finance.project(6571, 22.5, 1541.12, 500, lump_sum=6571)
    assert r["payoff_month"] == 0
    assert r["relapse_month"] is None
    assert max(r["balances"]) == 0
    assert r["interest"] == 0


def test_project_refills_when_spending_beats_payment():
    # Paid off by the lump sum, but $400 > $300 payment: the card refills.
    r = finance.project(2000, 24, 300, 400, lump_sum=2000, months=12)
    assert r["payoff_month"] == 0
    assert r["relapse_month"] == 1
    assert r["balances"][1] == pytest.approx(100)
    assert r["end_balance"] > 1000


def test_project_matches_simulate_payoff_until_zero():
    sim = finance.simulate_payoff(6400, 22.5, 500, 250, lump_sum=3000)
    r = finance.project(6400, 22.5, 500, 250, lump_sum=3000, months=24)
    assert r["payoff_month"] == sim["months_to_zero"] == 16
    assert [p["balance"] for p in sim["schedule"]] == r["balances"][:17]
    assert r["interest"] == pytest.approx(sim["total_interest"], abs=0.05)


def test_project_autopay_full_keeps_card_at_zero():
    p = finance.project(1000, 24, 100, 300, lump_sum=1000, months=6, autopay_full=True)
    assert p["payoff_month"] == 0 and p["relapse_month"] is None and p["end_balance"] == 0
