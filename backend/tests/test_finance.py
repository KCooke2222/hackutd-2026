import pytest

from zeroday import finance


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
    from zeroday import pnc_data

    no_card = [c for c in pnc_data.customer_ids() if not pnc_data.card_account(c)]
    if not no_card:
        pytest.skip("every customer has a card")
    assert finance.refill_breakdown(no_card[0])["has_card"] is False
