"""PR 4: customer constraints are enforced by the validator, not just the prompt."""

import copy

import pytest

from zeroday.agent import constraints as C
from zeroday.agent.validator import PlanRejected, validate_plan_v2

from test_wealth import CID, PNC_CARD, REFERENCE


def plan(**kw):
    p = copy.deepcopy(REFERENCE)
    p.update(kw)
    return p


def rejected(p, cons) -> str:
    with pytest.raises(PlanRejected) as e:
        validate_plan_v2(CID, p, live=False, constraints=cons)
    return " | ".join(e.value.reasons)


def test_reference_plan_passes_with_compatible_constraints():
    cons = {"forbid_sources": ["cash", "cd", "brokerage"], "min_emergency_months": 3, "protect_categories": ["transport"],
            "category_max": {"dining": 340}, "max_redirect": 2500}
    validate_plan_v2(CID, REFERENCE, live=False, constraints=cons)
    validate_plan_v2(CID, REFERENCE, live=False, constraints=None)


def test_forbid_sources():
    p = plan(lump_sums=[{"source": "cash", "amount": 100, "to_debt": PNC_CARD}])
    assert "savings, which you said not to touch" in rejected(p, {"forbid_sources": ["cash"]})


def test_min_emergency_months():
    assert "you said at least 6" in rejected(REFERENCE, {"min_emergency_months": 6})


def test_protect_category():
    assert "cuts dining, which you said not to cut" in rejected(REFERENCE, {"protect_categories": ["dining"]})


def test_category_max_applies_even_without_a_target():
    # Plan cuts dining to 330 but customer said they can reach 300.
    assert "dining target $330/mo" in rejected(REFERENCE, {"category_max": {"dining": 300}})
    # No target for transport in the plan: current spending (~$356) is over the cap.
    assert "transport target" in rejected(REFERENCE, {"category_max": {"transport": 200}})


def test_category_min_and_redirect_and_budget():
    assert "no lower than $350" in rejected(REFERENCE, {"category_min": {"dining": 350}})
    assert "at most $2,000" in rejected(REFERENCE, {"max_redirect": 2000})
    assert "at most $3,000" in rejected(REFERENCE, {"max_debt_budget": 3000})
    assert "at least $5,000" in rejected(REFERENCE, {"min_debt_budget": 5000})


def test_normalize_drops_junk_and_reports_it():
    c, dropped = C.normalize({"forbid_sources": ["Cash", "ira"], "category_max": {"Dining": "200", "x": -1},
                              "max_redirect": "abc", "bogus": 1})
    assert c == {"forbid_sources": ["cash"], "category_max": {"dining": 200.0}}
    assert len(dropped) == 4
    assert C.normalize(None) == ({}, [])


def test_merge_accumulates_and_latest_wins():
    a = {"forbid_sources": ["cash"], "category_max": {"dining": 200}, "max_redirect": 2000}
    b = {"forbid_sources": ["cd"], "max_redirect": 1500, "protect_categories": ["dining"]}
    m = C.merge(a, b)
    assert m["forbid_sources"] == ["cash", "cd"] and m["max_redirect"] == 1500
    assert m["protect_categories"] == ["dining"] and "category_max" not in m
    back = C.merge(m, {"category_max": {"dining": 250}})
    assert back["category_max"] == {"dining": 250} and "protect_categories" not in back
    assert a == {"forbid_sources": ["cash"], "category_max": {"dining": 200}, "max_redirect": 2000}  # not mutated


def test_describe_and_remove():
    c = {"forbid_sources": ["cash", "cd"], "category_max": {"dining": 200}, "min_emergency_months": 6}
    labels = [d["label"] for d in C.describe(c)]
    assert "Don't use savings" in labels and "Dining ≤ $200/mo" in labels and "Keep 6+ months cash" in labels
    assert C.remove(c, "forbid_sources", "cash")["forbid_sources"] == ["cd"]
    assert "category_max" not in C.remove(c, "category_max", "dining")
    assert "min_emergency_months" not in C.remove(c, "min_emergency_months")
