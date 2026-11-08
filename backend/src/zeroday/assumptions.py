"""Deterministic variables we own. Every wealth number in a plan is computed from these, never by the LLM."""

from __future__ import annotations

ASSUMPTIONS = {
    "horizon_months": 24,
    "prepay_hurdle_pct": 8.0,          # debts above this get extra money
    "default_emergency_months": 3.0,
    "min_emergency_months": 2.0,       # validator floor
    "returns_pct": {"cash": None, "cd": 4.8, "brokerage": 7.0, "ira": 7.0},  # cash None = account yield
    "cd_penalty_months_interest": 3,
    "ltcg_pct": 15.0, "stcg_pct": 22.0,
    "ira_penalty_pct": 10.0, "income_tax_pct": 22.0,
    "max_cut_pct": 30.0,               # per discretionary category, unless cancelling a recurring charge
}


def get(key: str):
    return ASSUMPTIONS[key]


def ret(source: str) -> float | None:
    """Expected annual return % for a source (None for cash: use the account's own yield)."""
    return ASSUMPTIONS["returns_pct"].get(source)


def ira_cost_frac() -> float:
    return (ASSUMPTIONS["ira_penalty_pct"] + ASSUMPTIONS["income_tax_pct"]) / 100
