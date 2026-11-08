"""Net-worth math for agent v2: where should each dollar come from, and what does a plan do to net worth?

Deterministic: every number comes from the data plus assumptions.py. The LLM never computes any of it.

Cash-flow model (monthly averages, last 12 months, checking + savings):
    residual = income - expenses - observed PNC debt payments - investment contributions - unknown outflow
Linked debts (store card, student loan) aren't in PNC's data; we assume their minimums are paid out of
the unknown outflow, so the outflow that can be redirected is `unknown - linked minimums`.

Baseline (plan None) = current behaviour: every debt gets its observed payment (linked: its minimum),
the card keeps today's spending, the unknown outflow keeps leaving.

Plan: `monthly_debt_budget` pays every debt. Each month: autopay cards that were at $0 pay the full
statement, every other debt gets its minimum, the rest goes down `extra_order`; anything left returns
to cash. Cash changes by residual + current debt payments + redirects + checking cuts - budget (+ leftover).
Card cuts lower card charges, so they speed payoff inside the same budget; checking cuts free cash.
"""

from __future__ import annotations

import math
from collections import defaultdict

from . import assumptions as A
from . import balance_sheet, finance, linked, pnc_data

LUMP_SOURCES = ("cash", "cd", "brokerage")
FLOW_KEY = "external_cash_management"
SURPLUS_MONTHS_OK = 3  # invested sources are worth tapping only if surplus + cash take longer than this


# ---------------- data ----------------

def _month_keys(txs: list[dict], months: int) -> list[str]:
    return sorted({t["posted_date"][:7] for t in txs})[-months:]


def _local_flows(customer_id: str, months: int = 12) -> dict:
    txs = [t for t in pnc_data.customer_transactions(customer_id) if t["account_type"] in ("checking", "savings")]
    keys = _month_keys(txs, months)
    n = max(len(keys), 1)
    win = [t for t in txs if t["posted_date"][:7] in keys]
    income = sum(t["amount"] for t in win if t["flow_class"] == "income")
    expenses = sum(t["amount"] for t in win if t["flow_class"] == "expense")
    debt = sum(t["amount"] for t in win if t["category"] == "debt_payment" and t["account_type"] == "checking")
    invest = sum(t["amount"] for t in win if t["category"] == "investment_contribution")
    unknown = sum(-t["balance_change"] for t in win if t["flow_class"] == "external_transfer")
    return {
        "income": income / n, "expenses": expenses / n, "debt_payments": debt / n,
        "invest_contrib": invest / n, "unknown_out": unknown / n, "source": "local", "months": keys,
    }


def _flows(customer_id: str) -> dict:
    """Monthly cash-flow averages. Uses insights.cash_flow when present and self-consistent."""
    local = _local_flows(customer_id)
    try:
        from .insights import cash_flow  # fork B
    except ImportError:
        cash_flow = None
    f = local
    if cash_flow is not None:
        try:
            cf = cash_flow(customer_id)
            a = cf["monthly_avg"]
            cand = {
                "income": a["income"], "expenses": a["essentials"] + a["discretionary"],
                "debt_payments": a["debt_payments"], "invest_contrib": a["savings_invest"],
                "unknown_out": cf.get("unknown_outflow_per_month", a["transfers_out_unknown"]),
                "source": "insights", "months": local["months"],
            }
            resid = cand["income"] - cand["expenses"] - cand["debt_payments"] - cand["invest_contrib"] - cand["unknown_out"]
            if abs(resid) <= 0.1 * max(cand["income"], 1):  # guard against a different accounting basis
                f = cand
        except Exception:  # noqa: BLE001 - fall back to local math
            f = local
    f = dict(f)
    f["residual"] = f["income"] - f["expenses"] - f["debt_payments"] - f["invest_contrib"] - f["unknown_out"]
    return f


def _spend_categories(customer_id: str, months: int = 12) -> dict[str, dict]:
    """Per category: card + checking monthly spend, min month, p25. Card purchases + checking/savings expenses."""
    txs = pnc_data.customer_transactions(customer_id)
    keys = _month_keys(txs, months)
    card = pnc_data.card_account(customer_id)
    card_id = card["account_id"] if card else None
    per: dict[str, dict[str, defaultdict]] = defaultdict(lambda: {"card": defaultdict(float), "cash": defaultdict(float)})
    for t in txs:
        m = t["posted_date"][:7]
        if m not in keys:
            continue
        if t["account_id"] == card_id and t["transaction_type"] == "purchase":
            per[t["category"]]["card"][m] += t["amount"]
        elif t["account_type"] in ("checking", "savings") and t["flow_class"] == "expense":
            per[t["category"]]["cash"][m] += t["amount"]
    n = max(len(keys), 1)
    out = {}
    for cat, d in per.items():
        monthly = [{"month": m, "amount": round(d["card"][m] + d["cash"][m], 2)} for m in keys]
        amounts = sorted(x["amount"] for x in monthly)
        lo = min(monthly, key=lambda x: x["amount"])
        card_avg = sum(d["card"].values()) / n
        cash_avg = sum(d["cash"].values()) / n
        out[cat] = {
            "category": cat,
            "essential": cat in finance.ESSENTIAL_CATEGORIES,
            "avg": round(card_avg + cash_avg, 2),
            "card_avg": round(card_avg, 2),
            "cash_avg": round(cash_avg, 2),
            "min": lo["amount"],
            "min_month": lo["month"],
            "p25": amounts[max(0, math.ceil(0.25 * len(amounts)) - 1)],
            "monthly": monthly,
        }
    return out


def _detail_meta(customer_id: str, category: str) -> dict:
    """Merchants / recurring charges from insights.spending_detail (fork B), if available."""
    try:
        from .insights import spending_detail
    except ImportError:
        return {"merchants": [], "recurring": []}
    try:
        d = spending_detail(customer_id, category)
        return {"merchants": d.get("merchants") or [], "recurring": d.get("recurring") or []}
    except Exception:  # noqa: BLE001
        return {"merchants": [], "recurring": []}


def _observed_payment(customer_id: str, account_id: str, months: int = 12) -> float | None:
    txs = pnc_data.customer_transactions(customer_id, account_id=account_id, transaction_type="payment")
    if not txs:
        return None
    keys = _month_keys(pnc_data.customer_transactions(customer_id, account_id=account_id), months)
    return sum(t["amount"] for t in txs if t["posted_date"][:7] in keys) / max(len(keys), 1)


def _start_month() -> tuple[int, int]:
    y, m = int(linked.AS_OF[:4]), int(linked.AS_OF[5:7])
    return y, m


def _label(i: int) -> str:
    y, m = _start_month()
    k = y * 12 + (m - 1) + i
    return f"{k // 12}-{k % 12 + 1:02d}"


def context(customer_id: str, keep_months: float | None = None, live: bool = True) -> dict:
    """Everything the simulator and validator need, computed once."""
    keep = A.get("default_emergency_months") if keep_months is None else float(keep_months)
    prof = pnc_data.profile(customer_id) or {}
    rows = balance_sheet.accounts(customer_id, live=live)
    hurdle = A.get("prepay_hurdle_pct")
    flows = _flows(customer_id)
    card = pnc_data.card_account(customer_id)
    refill = finance.refill_breakdown(customer_id) if card else {}
    card_spend = (refill.get("monthly_avg") or {}).get("new_spending", 0.0)

    debts = []
    for r in rows:
        if r["side"] != "debt" or r["balance"] <= 0:
            continue
        pnc = r["source"] != "linked"
        obs = _observed_payment(customer_id, r["account_id"]) if pnc else None
        if card and r["account_id"] == card["account_id"]:
            obs = (refill.get("monthly_avg") or {}).get("payment", obs)
        debts.append({
            "account_id": r["account_id"], "name": r["name"], "account_type": r["account_type"],
            "apr_pct": r["apr_pct"], "balance": r["balance"], "min_payment": float(r["min_payment"] or 0),
            "secured": bool(r["secured"]), "linked": not pnc,
            "is_card": r["account_type"] == "credit_card",
            "is_pnc_card": bool(card and r["account_id"] == card["account_id"]),
            "high_cost": r["apr_pct"] >= hurdle,
            "extra_eligible": (not r["secured"]) and r["apr_pct"] >= hurdle,
            "baseline_payment": round(max(obs or 0.0, float(r["min_payment"] or 0)), 2),
        })
    debts.sort(key=lambda d: (-d["apr_pct"], d["balance"]))
    linked_min = sum(d["min_payment"] for d in debts if d["linked"])
    current_payments = sum(d["baseline_payment"] for d in debts)

    def bal(types):
        return sum(r["balance"] for r in rows if r["side"] == "asset" and r["account_type"] in types)

    cash_rows = [r for r in rows if r["side"] == "asset" and r["account_type"] in ("checking", "savings")]
    cash = sum(r["balance"] for r in cash_rows)
    cash_yield = (sum(r["balance"] * (r["annual_yield_pct"] or 0) for r in cash_rows) / cash) if cash else 0.0
    brok_rows = [r for r in rows if r["side"] == "asset" and r["account_type"] == "brokerage"]
    brok = sum(r["balance"] for r in brok_rows)
    basis = sum((r["cost_basis"] if r["cost_basis"] is not None else r["balance"]) for r in brok_rows)
    gain_frac = max(brok - basis, 0) / brok if brok else 0.0
    monthly_exp = prof.get("monthly_non_debt_expenses", 0.0)
    lad = balance_sheet.ladder(rows, monthly_exp, keep)
    return {
        "customer_id": customer_id,
        "keep_months": keep,
        "monthly_expenses": monthly_exp,
        "emergency_buffer": round(keep * monthly_exp, 2),
        "flows": flows,
        "debts": debts,
        "linked_minimums": round(linked_min, 2),
        "current_debt_payments": round(current_payments, 2),
        "redirectable_outflow": round(max(flows["unknown_out"] - linked_min, 0.0), 2),
        "card_spend": card_spend,
        "spend": _spend_categories(customer_id),
        "assets": {
            "cash": cash, "cash_yield_pct": cash_yield,
            "cd": bal({"cd"}), "brokerage": brok, "brokerage_gain_frac": gain_frac,
            "ira": bal({"ira"}), "property": bal({"property"}),
        },
        "ladder": {t["key"]: t for t in lad["tiers"]},
        "hurdle_pct": hurdle,
    }


# ---------------- money-source math ----------------

def _f(apr_pct: float, months: float) -> float:
    return (1 + (apr_pct or 0) / 1200) ** max(months, 0)


def _gross_needed(source: str, net: float, ctx: dict) -> float:
    a = ctx["assets"]
    if source == "cd":
        rate = A.ret("cd") / 100
        return net / (1 - rate * A.get("cd_penalty_months_interest") / 12)
    if source == "brokerage":
        return net / (1 - a["brokerage_gain_frac"] * A.get("ltcg_pct") / 100)
    if source == "ira":
        return net / (1 - A.ira_cost_frac())
    return net


def compare_sources(customer_id: str, amount: float, target_debt: str, *, live: bool = True) -> dict:
    """Rank each way to fund `amount` toward `target_debt` by net-worth change at the horizon vs leaving it."""
    ctx = context(customer_id, live=live)
    H = A.get("horizon_months")
    d = next((x for x in ctx["debts"] if x["account_id"] == target_debt or x["name"].lower() == str(target_debt).lower()), None)
    if d is None:
        return {"error": f"unknown debt {target_debt!r}; use an account_id from debts()",
                "debts": [x["account_id"] for x in ctx["debts"]]}
    apr = d["apr_pct"]
    amount = max(min(float(amount), d["balance"]), 0.0)
    a = ctx["assets"]
    cy = a["cash_yield_pct"]
    eligible = d["extra_eligible"]
    out = []

    # Monthly surplus: the unexplained outflow, net of linked minimums.
    per = ctx["redirectable_outflow"]
    if per > 0 and amount > 0:
        left, i, nw = amount, 0, 0.0
        while left > 0.005:
            i += 1
            take = min(per, left)
            nw += take * (_f(apr, H - i) - _f(cy, H - i))
            left -= take
        out.append({"source": "surplus", "available": round(per, 2), "gross_needed": round(amount, 2), "upfront_cost": 0.0,
                    "nw_delta_24m": round(nw, 2), "months_needed": i, "use": eligible and nw > 0,
                    "why": f"redirect up to ${per:,.0f}/mo of the unexplained outflow; {i} mo; assumes it would otherwise earn {cy:.1f}%"})
    else:
        out.append({"source": "surplus", "available": 0.0, "gross_needed": round(amount, 2), "upfront_cost": 0.0,
                    "nw_delta_24m": 0.0, "use": False, "why": "no unexplained outflow to redirect"})

    # Cash above the emergency buffer.
    avail = ctx["ladder"]["cash"]["available"]
    n = min(amount, avail)
    nw = n * (_f(apr, H) - _f(cy, H))
    floor_month = A.get("min_emergency_months")
    at_min = max(a["cash"] - floor_month * ctx["monthly_expenses"], 0.0)
    why = (f"${avail:,.0f} above the {ctx['keep_months']:g}-month buffer (${ctx['emergency_buffer']:,.0f})" if avail > 0
           else f"nothing above the {ctx['keep_months']:g}-month buffer (${ctx['emergency_buffer']:,.0f}); "
                f"${at_min:,.0f} at {floor_month:g} months")
    out.append({"source": "cash", "available": round(avail, 2), "gross_needed": round(n, 2), "upfront_cost": 0.0,
                "nw_delta_24m": round(nw, 2), "use": eligible and avail > 0 and nw > 0, "why": why})
    cheaper = avail + max(per, 0.0) * SURPLUS_MONTHS_OK

    # Invested sources: lose expected return, pay penalty/tax up front.
    for src, bal_key, label in (("cd", "cd", "CD"), ("brokerage", "brokerage", "brokerage"), ("ira", "ira", "IRA")):
        bal = a[bal_key]
        if bal <= 0:
            out.append({"source": src, "available": 0.0, "gross_needed": 0.0, "upfront_cost": 0.0, "nw_delta_24m": 0.0,
                        "use": False, "why": f"no {label}"})
            continue
        net_avail = bal / _gross_needed(src, 1.0, ctx)
        n = min(amount, net_avail)
        g = _gross_needed(src, n, ctx)
        r = A.ret(src)
        nw = n * _f(apr, H) - g * _f(r, H)
        cost = g - n
        if src == "ira":
            why = f"never proposed: {A.get('ira_penalty_pct'):g}% penalty + {A.get('income_tax_pct'):g}% tax = ${cost:,.0f} up front"
            use = False
        elif src == "brokerage":
            why = f"{a['brokerage_gain_frac']:.0%} of it is gains, {A.get('ltcg_pct'):g}% tax = ${cost:,.0f}; gives up {r:g}%/yr"
            use = eligible and nw > 0
        else:
            why = f"{A.get('cd_penalty_months_interest')} months' interest penalty = ${cost:,.0f}; gives up {r:g}%/yr"
            use = eligible and nw > 0
        if use and amount <= cheaper + 0.5:
            use = False
            why += f"; not needed: surplus + cash cover ${amount:,.0f} within {SURPLUS_MONTHS_OK} months"
        out.append({"source": src, "available": round(net_avail, 2), "gross_needed": round(g, 2), "upfront_cost": round(cost, 2),
                    "nw_delta_24m": round(nw, 2), "use": use, "why": why})

    # Usable sources by net-worth gain; the rest in liquidity-ladder order (cheapest to tap first).
    rank = {k: i for i, k in enumerate(("surplus", "cash", "cd", "brokerage", "ira"))}
    out.sort(key=lambda s: (0, -s["nw_delta_24m"]) if s["use"] else (1, rank[s["source"]]))
    res = {"target": {"account_id": d["account_id"], "name": d["name"], "apr_pct": apr, "balance": d["balance"]},
           "amount": round(amount, 2), "horizon_months": H, "sources": out}
    if not eligible:
        res["note"] = f"{d['name']} is below the {ctx['hurdle_pct']:g}% prepay hurdle or secured: minimum only"
    return res


# ---------------- simulation ----------------

def cut_split(ctx: dict, targets: list[dict]) -> dict:
    """Monthly savings from spending targets, split into card (lower charges) and cash (checking)."""
    card_cut = cash_cut = 0.0
    for t in targets or []:
        c = ctx["spend"].get(t.get("category"))
        if not c:
            continue
        save = max(c["avg"] - float(t.get("target", c["avg"])), 0.0)
        if c["avg"] > 0:
            card_cut += save * c["card_avg"] / c["avg"]
            cash_cut += save * c["cash_avg"] / c["avg"]
    return {"card": card_cut, "cash": cash_cut}


def budget_limit(ctx: dict, plan: dict) -> dict:
    redirect = sum(float(r.get("amount_per_month") or 0) for r in plan.get("redirects") or [])
    cuts = cut_split(ctx, plan.get("spending_targets") or [])
    return {
        "current_debt_payments": ctx["current_debt_payments"],
        "redirects": round(redirect, 2),
        "checking_cuts": round(cuts["cash"], 2),
        "card_cuts": round(cuts["card"], 2),
        "max_monthly_debt_budget": round(ctx["current_debt_payments"] + redirect + cuts["cash"], 2),
        "minimums": round(sum(d["min_payment"] for d in ctx["debts"]), 2),
    }


def simulate_plan(customer_id: str, plan: dict | None, *, live: bool = True, ctx: dict | None = None) -> dict:
    """Month-by-month: every debt, cash, invested, net worth. plan None = baseline (current behaviour)."""
    keep = (plan or {}).get("keep_emergency_months")
    ctx = ctx or context(customer_id, keep_months=keep, live=live)
    base = _run(ctx, None)
    if plan is None:
        res = base
    else:
        res = _run(ctx, plan)
    res["baseline_net_worth_24m"] = base["net_worth_24m"]
    res["baseline_interest_total"] = base["interest_total"]
    res["baseline_high_cost_debt_free"] = base["high_cost_debt_free"]
    if plan is not None:
        res["budget"] = budget_limit(ctx, plan)
    return res


def _run(ctx: dict, plan: dict | None, shock: dict | None = None) -> dict:
    """shock (scenarios.py): {"cash_delta": [per month], "pause_extra": [per month]}; None = no shock.
    Under a shock, cash that would go negative is borrowed on the PNC card instead."""
    H = A.get("horizon_months")
    s_delta = (shock or {}).get("cash_delta") or []
    s_pause = (shock or {}).get("pause_extra") or []
    a = ctx["assets"]
    f = ctx["flows"]
    problems: list[str] = []
    debts = {d["account_id"]: dict(d, bal=d["balance"], interest=0.0, payoff=None) for d in ctx["debts"]}
    cash = a["cash"]
    inv = {"cd": a["cd"], "brokerage": a["brokerage"], "ira": a["ira"]}
    rets = {k: A.ret(k) for k in inv}
    prop = a["property"]
    upfront = 0.0

    if plan is None:
        charges = ctx["card_spend"]
        budget = None
        order: list[str] = []
        autopay: set[str] = set()
        cash_in = f["residual"]
    else:
        cuts = cut_split(ctx, plan.get("spending_targets") or [])
        charges = max(ctx["card_spend"] - cuts["card"], 0.0)
        budget = float(plan.get("monthly_debt_budget") or 0)
        order = [x for x in plan.get("extra_order") or [] if x in debts]
        for x in plan.get("extra_order") or []:
            if x not in debts:
                problems.append(f"extra_order: unknown debt {x}")
        autopay = {x for x in plan.get("autopay_full_cards") or [] if x in debts}
        redirect = sum(float(r.get("amount_per_month") or 0) for r in plan.get("redirects") or [])
        if redirect > ctx["redirectable_outflow"] + 0.5:
            problems.append(f"redirects ${redirect:,.0f}/mo exceed the ${ctx['redirectable_outflow']:,.0f}/mo outflow left after linked minimums")
        cash_in = f["residual"] + ctx["current_debt_payments"] + redirect + cuts["cash"] - budget
        minimums = sum(d["min_payment"] for d in debts.values())
        if budget + 0.005 < minimums:
            problems.append(f"monthly_debt_budget ${budget:,.0f} is below the ${minimums:,.0f} of minimums")
        # Lump sums at month 0.
        for ls in plan.get("lump_sums") or []:
            src, amt, to = ls.get("source"), float(ls.get("amount") or 0), ls.get("to_debt")
            if amt <= 0:
                continue
            if src not in LUMP_SOURCES:
                problems.append(f"lump sum source {src!r} not allowed")
                continue
            if to not in debts:
                problems.append(f"lump sum to unknown debt {to!r}")
                continue
            d = debts[to]
            if amt > d["bal"] + 0.005:
                problems.append(f"lump sum ${amt:,.0f} exceeds {d['name']} balance ${d['bal']:,.0f}")
                amt = d["bal"]
            if src == "cash":
                cash -= amt
            else:
                g = _gross_needed(src, amt, ctx)
                if g > inv[src] + 0.5:
                    problems.append(f"{src}: needs ${g:,.0f} but only ${inv[src]:,.0f}")
                inv[src] -= g
                upfront += g - amt
            d["bal"] -= amt
            if d["bal"] <= 0.005 and d["payoff"] is None:
                d["payoff"] = 0

    floor = min(ctx["emergency_buffer"], a["cash"]) - 1
    cash_problem = False
    borrow_to = next((k for k, d in debts.items() if d["is_pnc_card"]), None) or \
        next((k for k, d in sorted(debts.items(), key=lambda kv: kv[1]["apr_pct"]) if d["is_card"]), None)
    borrowed = 0.0
    min_cash, min_cash_i = cash, 0

    def snapshot(i: int) -> dict:
        total = sum(d["bal"] for d in debts.values())
        high = sum(d["bal"] for d in debts.values() if d["high_cost"])
        invested = sum(inv.values())
        return {"month": _label(i), "total_debt": round(total, 2), "high_cost_debt": round(high, 2),
                "cash": round(cash, 2), "invested": round(invested, 2),
                "net_worth": round(cash + invested + prop - total, 2)}

    months = [snapshot(0)]
    high_free = 0 if months[0]["high_cost_debt"] <= 0.01 else None
    short_reported = False
    for i in range(1, H + 1):
        cash *= 1 + a["cash_yield_pct"] / 1200
        for k in inv:
            inv[k] *= 1 + (rets[k] or 0) / 1200
        inv["ira"] += f["invest_contrib"]
        was_zero = {k for k, d in debts.items() if d["bal"] <= 0.005}
        for d in debts.values():
            it = d["bal"] * d["apr_pct"] / 1200
            d["bal"] += it
            d["interest"] += it
            if d["is_pnc_card"]:
                d["bal"] += charges
        if budget is None:
            for d in debts.values():
                d["bal"] -= min(d["baseline_payment"], d["bal"])
        else:
            left = budget
            for k, d in debts.items():  # autopay statement in full, then minimums
                due = d["bal"] if (k in autopay and k in was_zero) else min(d["min_payment"], d["bal"])
                pay = min(due, left)
                if pay + 0.005 < due and not short_reported:
                    problems.append(f"{_label(i)}: budget can't cover {d['name']}'s payment")
                    short_reported = True
                d["bal"] -= pay
                left -= pay
            for k in ([] if i < len(s_pause) and s_pause[i] else order):
                d = debts[k]
                pay = min(d["bal"], left)
                d["bal"] -= pay
                left -= pay
            cash += left  # nothing left to prepay: back to cash
        cash += cash_in
        if shock is not None:
            cash += s_delta[i] if i < len(s_delta) else 0.0
            if cash < 0 and borrow_to is not None:
                debts[borrow_to]["bal"] += -cash
                borrowed += -cash
                cash = 0.0
        if cash < min_cash:
            min_cash, min_cash_i = cash, i
        for d in debts.values():
            if d["bal"] <= 0.005:
                d["bal"] = 0.0
                if d["payoff"] is None:
                    d["payoff"] = i
            elif d["payoff"] is not None and d["is_card"]:
                d["payoff"] = None  # card refilled
        if plan is not None and cash < floor and not cash_problem:
            problems.append(f"{_label(i)}: cash ${cash:,.0f} falls below the {ctx['keep_months']:g}-month buffer "
                            f"(${ctx['emergency_buffer']:,.0f})")
            cash_problem = True
        months.append(snapshot(i))
        if months[-1]["high_cost_debt"] <= 0.01:
            if high_free is None:
                high_free = i
        else:
            high_free = None

    return {
        "feasible": not problems,
        "problems": problems,
        "debts": [{"account_id": d["account_id"], "name": d["name"], "apr_pct": d["apr_pct"], "balance": d["balance"],
                   "min_payment": d["min_payment"], "high_cost": d["high_cost"],
                   "payoff_month": _label(d["payoff"]) if d["payoff"] is not None else None,
                   "interest": round(d["interest"], 2)} for d in debts.values()],
        "months": months,
        "net_worth_24m": months[-1]["net_worth"],
        "interest_total": round(sum(d["interest"] for d in debts.values()), 2),
        "high_cost_debt_free": _label(high_free) if high_free is not None else None,
        "upfront_cost": round(upfront, 2),
        "monthly_card_spending": round(charges, 2),
        "min_cash": round(min_cash, 2),
        "min_cash_month": _label(min_cash_i),
        "borrowed_on_card": round(borrowed, 2),
    }
