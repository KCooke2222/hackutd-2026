"""Engine v2 on C001: money sources, whole-balance-sheet simulation, and validate_plan_v2."""

import copy

import pytest

from zeroday import wealth
from zeroday.agent.validator import PlanRejected, missing_lumps, validate_plan, validate_plan_v2

CID = "C001"
STORE, PNC_CARD, LOAN, STUDENT = "L-C001-CREDIT_CARD-2", "A-C001-CREDIT_CARD", "A-C001-PERSONAL_LOAN", "L-C001-STUDENT_LOAN-1"

# The honest reference: C001's PNC cash ($19k) is below the 3-month buffer ($26.5k) and the monthly residual is
# about -$40, so there is no idle cash and no surplus. The only real money is named trims (top merchant, within each
# limit), cancelling Audible and pausing the $225/mo brokerage auto-invest. Autopay the cards.
CUTS = [
    {"category": "subscriptions", "current": 17.94, "target": 3.0, "proof_month": "2025-11", "how": ["Cancel Audible"]},
    {"category": "dining", "current": 416.04, "target": 348.59, "proof_month": "2026-10", "how": ["Fewer Uber Eats orders"]},
    {"category": "other_spending", "current": 1280.85, "target": 1024.68, "proof_month": "2026-01", "how": ["Fewer Amazon household orders"]},
    {"category": "shopping", "current": 422.13, "target": 337.48, "proof_month": "2026-01", "how": ["Fewer Amazon purchases"]},
    {"category": "home_household", "current": 548.54, "target": 383.98, "proof_month": "2026-03", "how": ["Fewer Lowe's projects"]},
    {"category": "gifts_holidays", "current": 316.47, "target": 189.89, "proof_month": "2026-01", "how": ["Smaller holiday gift budget"]},
    {"category": "travel", "current": 718.65, "target": 431.19, "proof_month": "2025-11", "how": ["One fewer Southwest trip"]},
]
REFERENCE = {
    "version": 2,
    "monthly_debt_budget": 2880.64,
    "extra_order": [STORE, PNC_CARD, LOAN],
    "lump_sums": [],
    "redirects": [{"flow": "brokerage_auto_invest", "amount_per_month": 225.0}],
    "spending_targets": CUTS,
    "autopay_full_cards": [STORE, PNC_CARD],
    "keep_emergency_months": 3,
}
C002 = {"version": 2, "monthly_debt_budget": 1503.52, "extra_order": ["A-C002-CREDIT_CARD", "A-C002-PERSONAL_LOAN"],
        "lump_sums": [], "redirects": [], "spending_targets": [], "autopay_full_cards": ["A-C002-CREDIT_CARD"],
        "keep_emergency_months": 3}


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


def test_no_idle_cash_or_surplus_so_brokerage_is_usable(sources):
    """C001 has nothing above the buffer and no monthly surplus. Cuts are optional, and the $225/mo pause alone
    takes over a year, so selling brokerage at 7% to clear 20%+ debt is on the table (the solver sells ~$6.9k)."""
    assert sources["cash"][1]["use"] is False and sources["cash"][1]["available"] == 0
    assert sources["surplus"][1]["use"] is False and "no monthly surplus" in sources["surplus"][1]["why"]
    assert sources["brokerage"][1]["use"] is True and sources["brokerage"][1]["nw_delta_24m"] > 0


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
    assert reference["high_cost_debt_free"] == "2027-05"  # paid from trims + paused investing
    assert reference["net_worth_24m"] > reference["baseline_net_worth_24m"] == baseline["net_worth_24m"]
    assert reference["interest_total"] < reference["baseline_interest_total"]
    by_id = {d["account_id"]: d for d in reference["debts"]}
    assert by_id[STUDENT]["payoff_month"] is None  # minimum only
    base_cash = [m["cash"] for m in baseline["months"]]
    assert all(m["cash"] >= c - 1 for m, c in zip(reference["months"], base_cash))  # cash never below today's path


def test_net_worth_is_honest(reference, baseline):
    """Month 0 matches the balance sheet, and the gain is no more than interest saved + cuts (+ a little yield)."""
    from zeroday import balance_sheet

    assert reference["months"][0]["net_worth"] == pytest.approx(balance_sheet.balance_sheet(CID)["totals"]["net_worth"], abs=1)
    gain = reference["net_worth_24m"] - baseline["net_worth_24m"]
    cuts = sum(t["current"] - t["target"] for t in CUTS) * 24
    assert gain <= baseline["interest_total"] - reference["interest_total"] + cuts + 300


def test_lumpy_trims_have_limits():
    """Travel / gifts / household are trim at 40/40/30%; car repairs and annual bills are fixed."""
    assert "40% limit" in reasons(_cut("travel", 300, ("One fewer trip",), "2025-11"))
    assert "is fixed" in reasons(_cut("car_maintenance", 100))
    assert "is fixed" in reasons(_cut("annual_bills", 100))


# ---------- validate_plan_v2 rejections ----------

def test_rejects_ira():
    assert "never a source" in reasons(plan(lump_sums=[{"source": "ira", "amount": 1000, "to_debt": PNC_CARD}]))


def test_rejects_cut_below_lowest_month():
    t = [{"category": "dining", "target": 300, "proof_month": "2026-10", "how": ["cook at home"]}]
    assert "lowest month" in reasons(plan(spending_targets=t))


def test_rejects_missing_proof_month():
    t = [{"category": "dining", "target": 349, "how": ["cook at home"]}]
    assert "proof_month" in reasons(plan(spending_targets=t))


def test_rejects_budget_over_sources():
    assert "over its sources" in reasons(plan(monthly_debt_budget=6000))


def test_rejects_student_loan_in_extra_order():
    assert "below the 8% hurdle" in reasons(plan(extra_order=[STORE, PNC_CARD, LOAN, STUDENT]))


def test_rejects_other_rules():
    assert "missing" in reasons(plan(extra_order=[STORE, PNC_CARD]))
    assert "highest APR first" in reasons(plan(extra_order=[PNC_CARD, STORE, LOAN]))
    assert "minimum" in reasons(plan(monthly_debt_budget=100))
    assert "keep_emergency_months" in reasons(plan(keep_emergency_months=1))
    assert "at most 8 words" in reasons(plan(spending_targets=[{"category": "dining", "target": 349, "proof_month": "2026-10",
                                                                "how": ["one two three four five six seven eight nine"]}]))
    with pytest.raises(PlanRejected, match="buffer"):  # C002's cash lump sums can't break a 24-month buffer
        validate_plan_v2("C002", {**C002, "keep_emergency_months": 24, "lump_sums": [
            {"source": "cash", "amount": 1755.27, "to_debt": "A-C002-CREDIT_CARD"}]}, live=False)


# ---------- flex flags (categories.py) ----------

def _cut(cat, target, how=("Spend less",), pm="2026-01"):
    return plan(spending_targets=[{"category": cat, "target": target, "proof_month": pm, "how": list(how)}])


def test_fixed_and_ask_spending_never_cut():
    assert "is fixed" in reasons(_cut("groceries", 1000))
    assert "is fixed" in reasons(_cut("housing", 2000))
    assert "raise it as a question" in reasons(_cut("healthcare", 1))


def test_trim_limit_per_category():
    # phone_internet may lose at most 25%; it never varies, so a 30% cut is over the limit and below the lowest month.
    assert "25% limit" in reasons(_cut("phone_internet", 116, pm="2025-11"))


def test_subscriptions_only_by_cancelling_named():
    assert "only cut by cancelling" in reasons(_cut("subscriptions", 10, ("Use fewer apps",), "2025-11"))
    assert "more than the" in reasons(_cut("subscriptions", 0, ("Cancel Audible",), "2025-11"))


def test_redirect_flags():
    def r(flow, amt):
        return reasons(plan(redirects=[{"flow": flow, "amount_per_month": amt}]))

    assert "unknown" in r("external_cash_management", 100)
    assert "ask the customer" in r("ira_contribution", 10)
    assert "never moved" in r("debt_minimums", 100)
    assert "exceeds" in r("monthly_surplus", 1)  # C001 has no surplus
    assert "unknown" in r("outside_savings_sweep", 100)  # the old sweep is gone
    assert "exceeds" in r("brokerage_auto_invest", 300)
    assert "exceeds" in r("cd_contribution", 1)  # C001 has no CD contributions


def test_plan_rejected_backward_compatible():
    e = PlanRejected("one message")
    assert str(e) == "one message" and e.reasons == ["one message"]
    assert isinstance(e, ValueError)


def test_v1_still_works():
    r = validate_plan(CID, {"monthly_payment": 2000}, snapshot=False)
    assert r["inputs"]["monthly_payment"] == 2000 and "before" in r and "after" in r


def test_missing_lumps_adds_idle_cash_only():
    # C001: no cash above the buffer, so nothing is invented.
    assert missing_lumps(CID, REFERENCE, validate_plan_v2(CID, REFERENCE, live=False), live=False) is None
    # C002: $6.2k above the buffer pays off the 22.5% card and the 11% loan today.
    sim = validate_plan_v2("C002", C002, live=False)
    added = missing_lumps("C002", C002, sim, live=False)
    assert added and added["net_worth_gain"] > 0
    assert {l["source"] for l in added["lump_sums"]} == {"cash"}
    assert added["sim"]["feasible"] and added["sim"]["high_cost_debt_free"] == "2026-10"



def test_plan_needs_no_cuts_and_prices_optional_ones():
    # Cuts are the customer's choice: a plan with none is valid, and the biggest ones come back priced.
    no_cuts = plan(spending_targets=[], monthly_debt_budget=0.0)
    no_cuts["monthly_debt_budget"] = round(wealth.budget_limit(wealth.context(CID, live=False), no_cuts)["max_monthly_debt_budget"], 2)
    sim = validate_plan_v2(CID, no_cuts, live=False)
    opts = wealth.cut_options(CID, no_cuts, live=False, sim=sim)
    assert 1 <= len(opts) <= 3
    assert [o["net_worth_24m_gain"] for o in opts] == sorted((o["net_worth_24m_gain"] for o in opts), reverse=True)
    assert opts[0]["category"] == "travel" and opts[0]["merchants"]
    for o in opts:
        assert o["flag"] in ("trim", "cancel") and o["net_worth_24m_gain"] > 0
        p = {**no_cuts, "spending_targets": [o["spending_target"]]}
        p["monthly_debt_budget"] = round(wealth.budget_limit(wealth.context(CID, live=False), p)["max_monthly_debt_budget"], 2)
        validate_plan_v2(CID, p, live=False)  # every offered cut is one the validator accepts
