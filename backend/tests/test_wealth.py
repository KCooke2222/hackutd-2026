"""Engine v2 on C001: money sources, whole-balance-sheet simulation, and validate_plan_v2."""

import copy

import pytest

from zeroday import wealth
from zeroday.agent.validator import PlanRejected, validate_plan, validate_plan_v2

CID = "C001"
STORE, PNC_CARD, LOAN, STUDENT = "L-C001-CREDIT_CARD-2", "A-C001-CREDIT_CARD", "A-C001-PERSONAL_LOAN", "L-C001-STUDENT_LOAN-1"

REFERENCE = {
    "version": 2,
    "monthly_debt_budget": 4300,
    "extra_order": [STORE, PNC_CARD, LOAN],
    "lump_sums": [],
    "redirects": [{"flow": "external_cash_management", "amount_per_month": 2500}],
    "spending_targets": [
        {"category": "dining", "current": 356, "target": 330, "proof_month": "2026-01", "how": ["cook 2 more dinners a week"]},
        {"category": "shopping", "current": 356, "target": 330, "proof_month": "2026-01", "how": ["skip one online order a month"]},
    ],
    "autopay_full_cards": [STORE, PNC_CARD],
    "keep_emergency_months": 3,
}


def plan(**kw):
    p = copy.deepcopy(REFERENCE)
    p.update(kw)
    return p


def reasons(p) -> str:
    with pytest.raises(PlanRejected) as e:
        validate_plan_v2(CID, p, live=False)
    assert e.value.reasons
    return " | ".join(e.value.reasons)


@pytest.fixture(scope="module")
def sources():
    return {s["source"]: (i, s) for i, s in enumerate(wealth.compare_sources(CID, 7812, PNC_CARD, live=False)["sources"])}


# ---------- compare_sources ----------

def test_ira_loses_net_worth(sources):
    _, ira = sources["ira"]
    assert ira["nw_delta_24m"] < 0
    assert ira["use"] is False
    assert ira["upfront_cost"] > 0


def test_brokerage_ranks_below_surplus_and_cash(sources):
    b = sources["brokerage"][0]
    assert sources["surplus"][0] < b and sources["cash"][0] < b
    assert sources["brokerage"][1]["use"] is False  # surplus covers it within a few months
    assert sources["surplus"][1]["use"] is True


def test_compare_sources_unknown_debt():
    assert "error" in wealth.compare_sources(CID, 100, "nope", live=False)


# ---------- simulate_plan ----------

@pytest.fixture(scope="module")
def baseline():
    return wealth.simulate_plan(CID, None, live=False)


@pytest.fixture(scope="module")
def reference():
    return validate_plan_v2(CID, REFERENCE, live=False)


def test_baseline_shape(baseline):
    assert len(baseline["months"]) == 25
    assert baseline["months"][0]["month"] == "2026-10" and baseline["months"][-1]["month"] == "2028-10"
    assert baseline["net_worth_24m"] == baseline["baseline_net_worth_24m"]
    assert {d["account_id"] for d in baseline["debts"]} >= {STORE, PNC_CARD, LOAN, STUDENT}


def test_reference_plan_feasible_and_better(reference, baseline):
    assert reference["feasible"] and not reference["problems"]
    assert reference["high_cost_debt_free"] is not None and reference["high_cost_debt_free"] <= "2027-02"  # within 4 months
    assert reference["net_worth_24m"] > reference["baseline_net_worth_24m"] == baseline["net_worth_24m"]
    assert reference["interest_total"] < reference["baseline_interest_total"]
    by_id = {d["account_id"]: d for d in reference["debts"]}
    assert by_id[STUDENT]["payoff_month"] is None  # minimum only
    assert all(m["cash"] >= 19_000 for m in reference["months"])  # emergency buffer kept


# ---------- validate_plan_v2 rejections ----------

def test_rejects_ira():
    assert "never a source" in reasons(plan(lump_sums=[{"source": "ira", "amount": 1000, "to_debt": PNC_CARD}]))


def test_rejects_cut_below_lowest_month():
    t = [{"category": "dining", "target": 300, "proof_month": "2026-01", "how": ["cook at home"]}]
    assert "lowest month" in reasons(plan(spending_targets=t))


def test_rejects_missing_proof_month():
    t = [{"category": "dining", "target": 330, "how": ["cook at home"]}]
    assert "proof_month" in reasons(plan(spending_targets=t))


def test_rejects_budget_over_sources():
    assert "over its sources" in reasons(plan(monthly_debt_budget=6000))


def test_rejects_student_loan_in_extra_order():
    assert "below the 8% hurdle" in reasons(plan(extra_order=[STORE, PNC_CARD, LOAN, STUDENT]))


def test_rejects_other_rules():
    assert "missing" in reasons(plan(extra_order=[STORE, PNC_CARD]))
    assert "highest APR first" in reasons(plan(extra_order=[PNC_CARD, STORE, LOAN]))
    assert "essential" in reasons(plan(spending_targets=[{"category": "groceries", "target": 1050,
                                                          "proof_month": "2026-01", "how": ["buy store brand"]}]))
    assert "minimum" in reasons(plan(monthly_debt_budget=100))
    assert "keep_emergency_months" in reasons(plan(keep_emergency_months=1))
    assert "unexplained outflow" in reasons(plan(redirects=[{"flow": "external_cash_management", "amount_per_month": 5000}]))
    assert "at most 8 words" in reasons(plan(spending_targets=[{"category": "dining", "target": 330, "proof_month": "2026-01",
                                                                "how": ["one two three four five six seven eight nine"]}]))
    assert "buffer" in reasons(plan(lump_sums=[{"source": "cash", "amount": 5000, "to_debt": PNC_CARD}]))


def test_plan_rejected_backward_compatible():
    e = PlanRejected("one message")
    assert str(e) == "one message" and e.reasons == ["one message"]
    assert isinstance(e, ValueError)


def test_v1_still_works():
    r = validate_plan(CID, {"monthly_payment": 2000}, snapshot=False)
    assert r["inputs"]["monthly_payment"] == 2000 and "before" in r and "after" in r
