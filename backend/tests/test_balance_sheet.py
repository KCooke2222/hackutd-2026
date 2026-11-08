"""Liquidity ladder, debt priority and linked accounts."""

import pytest

from paidway.planning import balance_sheet as bs


def acc(account_type, balance, **kw):
    side = "debt" if account_type in {"credit_card", "personal_loan", "auto_loan", "mortgage", "student_loan"} else "asset"
    from paidway.data.linked import SECURED_TYPES, consequence_tier

    return {
        "account_id": kw.pop("account_id", f"X-{account_type}-{balance}"),
        "name": account_type, "account_type": account_type, "kind": kw.get("kind"), "source": "pnc", "side": side,
        "balance": balance, "apr_pct": kw.get("apr_pct", 0.0), "annual_yield_pct": kw.get("annual_yield_pct", 0.0),
        "min_payment": kw.get("min_payment", 0.0), "cost_basis": kw.get("cost_basis"),
        "secured": account_type in SECURED_TYPES, "consequence_tier": consequence_tier(account_type, kw.get("kind")),
        "secures_account_id": kw.get("secures_account_id"),
    }


@pytest.fixture()
def rows():
    return [
        acc("checking", 2_000), acc("savings", 10_000, annual_yield_pct=2.5),
        acc("cd", 4_000, annual_yield_pct=4.0),
        acc("brokerage", 10_000, cost_basis=6_000),
        acc("ira", 20_000),
        acc("mortgage", 150_000, apr_pct=6.5, min_payment=1_200, account_id="M1"),
        acc("property", 250_000, secures_account_id="M1"),
        acc("credit_card", 5_000, apr_pct=22.5, min_payment=150),
        acc("credit_card", 1_000, apr_pct=27.0, min_payment=40, account_id="CARD2"),
        acc("student_loan", 20_000, apr_pct=5.5, kind="federal", min_payment=220),
        acc("student_loan", 8_000, apr_pct=9.0, kind="private", min_payment=100),
        acc("personal_loan", 3_000, apr_pct=11.0, min_payment=90),
    ]


def tier(lad, key):
    return next(t for t in lad["tiers"] if t["key"] == key)


def test_ladder_keeps_emergency_buffer(rows):
    lad = bs.ladder(rows, monthly_expenses=3_000, keep_months=3)
    assert lad["emergency_buffer"] == 9_000
    assert tier(lad, "cash")["available"] == 3_000  # 12k cash - 9k buffer


def test_ladder_costs(rows):
    lad = bs.ladder(rows, 3_000)
    assert tier(lad, "cash")["cost_pct"] == 0
    assert tier(lad, "cd")["cost_pct"] == pytest.approx(2.0)  # 6 months of 4% interest
    assert tier(lad, "brokerage")["cost_pct"] == pytest.approx(6.0)  # 40% gain x 15% LTCG
    assert tier(lad, "retirement")["proposable"] is False
    assert tier(lad, "property")["available"] == 100_000  # equity, not value
    assert lad["proposable_available"] == 3_000 + 4_000 + 10_000


def test_tap_draws_cheapest_first(rows):
    lad = bs.ladder(rows, 3_000)
    t = bs.tap(lad, 8_000)
    assert [(d["key"], d["amount"]) for d in t["draws"]] == [("cash", 3_000), ("cd", 4_000), ("brokerage", 1_000)]
    assert t["total_cost"] == pytest.approx(0 + 80 + 60)
    assert t["shortfall"] == 0


def test_tap_never_touches_retirement(rows):
    lad = bs.ladder(rows, 3_000)
    t = bs.tap(lad, 50_000)
    assert {d["key"] for d in t["draws"]} == {"cash", "cd", "brokerage"}
    assert t["shortfall"] == 50_000 - 17_000


def test_draw_costs_rejects_overdraw_and_retirement(rows):
    lad = bs.ladder(rows, 3_000)
    with pytest.raises(ValueError):
        bs.draw_costs(lad, {"cash": 3_500})
    with pytest.raises(ValueError):
        bs.draw_costs(lad, {"retirement": 100})


def test_priority_minimums_by_consequence(rows):
    p = bs.debt_priority(rows, monthly_income=8_000, monthly_expenses=3_000)
    tiers = [d["consequence_tier"] for d in p["debts"]]
    assert tiers == sorted(tiers)
    assert p["debts"][0]["account_type"] == "mortgage"
    assert p["debts"][1]["kind"] == "federal"
    assert p["monthly_minimums"] == 1_200 + 150 + 40 + 220 + 100 + 90
    assert p["monthly_surplus"] == 8_000 - 3_000 - p["monthly_minimums"]


def test_priority_extra_goes_to_highest_apr_never_secured_or_cheap(rows):
    p = bs.debt_priority(rows, 8_000, 3_000)
    ranked = sorted((d for d in p["debts"] if d["extra_rank"]), key=lambda d: d["extra_rank"])
    assert [d["apr_pct"] for d in ranked] == [27.0, 22.5, 11.0, 9.0]
    by_type = {(d["account_type"], d["kind"]): d for d in p["debts"]}
    assert by_type[("mortgage", None)]["extra_rank"] is None
    assert by_type[("student_loan", "federal")]["extra_rank"] is None


def test_linked_accounts_deterministic():
    from paidway.data import linked

    a, b = linked.generate(), linked.generate()
    assert a == b
    types = {r["account_type"] for r in a}
    assert {"student_loan", "brokerage", "property", "credit_card"} <= types
    assert all(r["source"] == "linked" for r in a)
    cards = [r["apr_pct"] for r in a if r["account_type"] == "credit_card"]
    assert min(cards) >= 18 and max(cards) <= 29


def test_c001_sheet_merges_pnc_and_linked(monkeypatch, tmp_path):
    from paidway.data import store

    monkeypatch.setattr(store, "DB_PATH", tmp_path / "t.sqlite3")
    bs.clear_cache()
    s = bs.balance_sheet("C001", live=False)
    sources = {a["source"] for a in s["accounts"]}
    assert sources == {"pnc", "linked"}
    assert {a["account_type"] for a in s["accounts"]} >= {"checking", "savings", "ira", "student_loan", "brokerage", "property"}
    assert s["totals"]["net_worth"] == pytest.approx(s["totals"]["assets"] - s["totals"]["debts"])
    bs.clear_cache()


def test_plan_can_draw_from_brokerage_with_cost(monkeypatch, tmp_path):
    from paidway.agent.validator import PlanRejected, validate_plan
    from paidway.data import balances, store

    monkeypatch.setattr(store, "DB_PATH", tmp_path / "t.sqlite3")
    monkeypatch.setattr(balances.read, "enabled", lambda: False)
    bs.clear_cache()
    plan = {"lump_sum_from_savings": 0, "lump_sum_from_brokerage": 4_000, "monthly_payment": 0,
            "monthly_card_spending_cap": None, "keep_emergency_months": 3}
    sim = validate_plan("C001", plan)
    assert sim["inputs"]["lump_sum_total"] == 4_000
    assert sim["tap_cost_total"] == pytest.approx(4_000 * 0.28 * 0.15, abs=0.01)
    with pytest.raises(PlanRejected):
        validate_plan("C001", {**plan, "lump_sum_from_brokerage": 50_000})
    bs.clear_cache()
