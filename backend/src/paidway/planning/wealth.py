"""Net-worth math for agent v2: where should each dollar come from, and what does a plan do to net worth?

Deterministic: every number comes from the data plus assumptions.py and categories.py. The LLM never computes any of it.

Cash-flow model (monthly averages, last 12 months, checking + savings):
    residual = income - cash expenses - all debt payments (PNC and linked) - investment contributions
The residual is what is left each month: it stays in PNC cash (negative residual = drawing cash down).
Every other outflow is named spending (including lumpy irregular spending: travel, repairs, gifts, bills).

Cash pool: PNC checking + savings at their yield. The emergency buffer is checked on it.

Baseline (plan None) = current behaviour: every debt gets its observed payment (linked: its minimum),
the card keeps today's spending, contributions continue, the residual stays in cash.

Plan: `monthly_debt_budget` pays every debt. Each month: autopay cards that were at $0 pay the full
statement, every other debt gets its minimum, the rest goes down `extra_order`; anything left is saved.
The budget's sources are today's debt payments + redirected surplus + paused contributions + checking cuts.
Redirecting the surplus moves money that would have stayed in cash, so cash grows that much less (the honest
gain is only the APR minus the cash yield). Pausing a contribution stops that asset's deposits.
Card cuts lower card charges, so they speed payoff inside the same budget; checking cuts free cash.
"""

from __future__ import annotations

import math
from collections import defaultdict

from ..data import categories, linked, pnc_data
from . import assumptions as A
from . import balance_sheet, finance

LUMP_SOURCES = ("cash", "cd", "brokerage")
SURPLUS = "monthly_surplus"
INVEST_MONTHS_OK = 12  # invested sources are worth tapping only if cash + flexible monthly money take longer than this


# ---------------- data ----------------

def _month_keys(txs: list[dict], months: int) -> list[str]:
    return sorted({t["posted_date"][:7] for t in txs})[-months:]


def _contribution_dest(t: dict) -> str | None:
    """Where an investment contribution from checking goes: ira / cd / brokerage (None = checking <-> savings)."""
    if t["flow_class"] == "external_transfer":
        return "brokerage"
    tid = t.get("transfer_id") or ""
    kind = tid.split("-")[2] if tid.count("-") >= 3 else ""
    return kind if kind in ("ira", "cd") else None


def _flows(customer_id: str, months: int = 12) -> dict:
    """Monthly cash-flow averages over checking + savings, every outflow named."""
    txs = [t for t in pnc_data.customer_transactions(customer_id) if t["account_type"] in ("checking", "savings")]
    keys = _month_keys(txs, months)
    n = max(len(keys), 1)
    win = [t for t in txs if t["posted_date"][:7] in keys]
    income = sum(t["amount"] for t in win if t["flow_class"] == "income")
    expenses = sum(t["amount"] for t in win if t["flow_class"] == "expense")
    debt = sum(-t["balance_change"] for t in win if t["category"] == "debt_payment" and t["account_type"] == "checking")
    contrib = {"ira": 0.0, "cd": 0.0, "brokerage": 0.0}
    for t in win:
        if t["category"] == "investment_contribution" and t["account_type"] == "checking" and t["balance_change"] < 0:
            dest = _contribution_dest(t)
            if dest:
                contrib[dest] += -t["balance_change"]
    contrib = {k: v / n for k, v in contrib.items()}
    f = {
        "income": income / n, "expenses": expenses / n, "debt_payments": debt / n,
        "contrib": contrib, "invest_contrib": sum(contrib.values()),
        "months": keys,
    }
    f["residual"] = f["income"] - f["expenses"] - f["debt_payments"] - f["invest_contrib"]
    return f


def movable_flows(ctx: dict) -> dict[str, dict]:
    """Named flows a plan may redirect or pause, with the most it can take per month."""
    f = ctx["flows"]
    out = {}
    for key in categories.MOVABLE_FLOWS:
        meta = categories.FLOWS[key]
        limit = max(f["residual"], 0.0) if key == SURPLUS else f["contrib"].get(meta.get("dest", ""), 0.0)
        out[key] = {"flow": key, "flag": meta["flag"], "label": meta["label"], "max_per_month": round(limit, 2),
                    "dest": meta.get("dest")}
    return out


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
        flag = categories.spending_flag(cat)
        out[cat] = {
            "category": cat,
            "flag": flag["flag"],
            "max_cut_pct": flag.get("max_pct"),
            "essential": cat in categories.ESSENTIAL or flag["flag"] in (categories.FIXED, categories.ASK),
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


def context(customer_id: str, keep_months: float | None = None, live: bool = True, with_options: bool = True) -> dict:
    """Everything the simulator and validator need, computed once. `with_options`: apply the underwater options
    the customer chose to plan with (options.applied: e.g. an IDR payment, a hardship rate); False = as the data is."""
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

    def pool(rs):
        total = sum(r["balance"] for r in rs)
        return total, (sum(r["balance"] * (r["annual_yield_pct"] or 0) for r in rs) / total) if total else 0.0

    cash_rows = [r for r in rows if r["side"] == "asset" and r["account_type"] in ("checking", "savings")]
    cash, cash_yield = pool(cash_rows)
    brok_rows = [r for r in rows if r["side"] == "asset" and r["account_type"] == "brokerage"]
    brok = sum(r["balance"] for r in brok_rows)
    basis = sum((r["cost_basis"] if r["cost_basis"] is not None else r["balance"]) for r in brok_rows)
    gain_frac = max(brok - basis, 0) / brok if brok else 0.0
    monthly_exp = prof.get("monthly_non_debt_expenses", 0.0)
    lad = balance_sheet.ladder(rows, monthly_exp, keep)
    tiers = {t["key"]: t for t in lad["tiers"]}
    cash_avail = tiers["cash"]["available"]  # PNC cash above the buffer
    ctx = {
        "customer_id": customer_id,
        "keep_months": keep,
        "monthly_expenses": monthly_exp,
        "emergency_buffer": round(keep * monthly_exp, 2),
        "flows": flows,
        "debts": debts,
        "linked_minimums": round(linked_min, 2),
        "current_debt_payments": round(current_payments, 2),
        "card_spend": card_spend,
        "spend": _spend_categories(customer_id),
        "assets": {
            "cash": cash, "cash_yield_pct": cash_yield,
            "cd": bal({"cd"}), "brokerage": brok, "brokerage_gain_frac": gain_frac,
            "ira": bal({"ira"}), "property": bal({"property"}),
        },
        "ladder": tiers,
        # Lump-sum limit: cash above the buffer.
        "lump_available": {"cash": round(min(cash, cash_avail), 2), "cash_total": round(cash_avail, 2)},
        "hurdle_pct": hurdle,
        "applied_options": [],
    }
    if with_options:
        from . import options

        options.apply_saved(ctx, customer_id)
    ctx["movable_flows"] = movable_flows(ctx)
    ctx["flexible_monthly"] = flexible_monthly(ctx)
    return ctx


def flexible_monthly(ctx: dict) -> float:
    """Most monthly money a plan can free without selling anything or asking for cuts: the surplus and every
    pausable contribution. Trims are the customer's choice (cut_options), so they don't count here."""
    return round(sum(f["max_per_month"] for f in ctx["movable_flows"].values()), 2)


def trim_room(ctx: dict) -> dict:
    """Per trim category: the most it can be cut each month (to its limit, never below its lowest month)."""
    out = {}
    for cat, c in ctx["spend"].items():
        flag = categories.spending_flag(cat)
        if flag["flag"] == categories.TRIM and not c["essential"]:
            floor = max(c["min"], c["avg"] * (1 - flag["max_pct"] / 100))
            out[cat] = {"room": round(max(c["avg"] - floor, 0.0), 2), "floor": round(floor, 2),
                        "max_pct": flag["max_pct"], "min_month": c["min_month"], "min": c["min"]}
    return out


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

    # Monthly surplus: what is left in checking each month (money that would otherwise sit in cash).
    oy = cy
    per = ctx["movable_flows"][SURPLUS]["max_per_month"]
    if per > 0 and amount > 0:
        left, i, nw = amount, 0, 0.0
        while left > 0.005:
            i += 1
            take = min(per, left)
            nw += take * (_f(apr, H - i) - _f(oy, H - i))
            left -= take
        out.append({"source": "surplus", "flow": SURPLUS, "available": round(per, 2), "gross_needed": round(amount, 2),
                    "upfront_cost": 0.0, "nw_delta_24m": round(nw, 2), "months_needed": i, "use": eligible and nw > 0,
                    "why": f"redirect up to ${per:,.0f}/mo of the monthly surplus left in checking; {i} mo; "
                           f"that money would otherwise earn {oy:.1f}%"})
    else:
        out.append({"source": "surplus", "flow": SURPLUS, "available": 0.0, "gross_needed": round(amount, 2),
                    "upfront_cost": 0.0, "nw_delta_24m": 0.0, "use": False,
                    "why": "no monthly surplus: income does not cover spending, payments and contributions"})

    # Cash above the emergency buffer.
    total_avail = ctx["lump_available"]["cash_total"]
    floor_month = A.get("min_emergency_months")
    at_min = max(a["cash"] - floor_month * ctx["monthly_expenses"], 0.0)
    for src, label, y in (("cash", "PNC checking + savings", cy),):
        avail = ctx["lump_available"][src]
        n = min(amount, avail)
        nw = n * (_f(apr, H) - _f(y, H))
        why = (f"${avail:,.0f} of {label} ({y:.1f}%) usable: above the "
               f"{ctx['keep_months']:g}-month buffer (${ctx['emergency_buffer']:,.0f})" if avail > 0
               else f"nothing above the {ctx['keep_months']:g}-month buffer (${ctx['emergency_buffer']:,.0f}); "
                    f"${at_min:,.0f} at {floor_month:g} months")
        out.append({"source": src, "available": round(avail, 2), "gross_needed": round(n, 2), "upfront_cost": 0.0,
                    "nw_delta_24m": round(nw, 2), "use": eligible and avail > 0 and nw > 0, "why": why})
    cheaper = total_avail + ctx["flexible_monthly"] * INVEST_MONTHS_OK

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
            why += (f"; not needed: cash above the buffer + up to ${ctx['flexible_monthly']:,.0f}/mo of "
                    f"pauses and surplus clear ${amount:,.0f} within {INVEST_MONTHS_OK} months (selling loses net worth)")
        out.append({"source": src, "available": round(net_avail, 2), "gross_needed": round(g, 2), "upfront_cost": round(cost, 2),
                    "nw_delta_24m": round(nw, 2), "use": use, "why": why})

    # Usable sources by net-worth gain; the rest in liquidity-ladder order (cheapest to tap first).
    rank = {k: i for i, k in enumerate(("surplus", "cash", "cd", "brokerage", "ira"))}
    out.sort(key=lambda s: (0, -s["nw_delta_24m"]) if s["use"] else (1, rank[s["source"]]))
    res = {"target": {"account_id": d["account_id"], "name": d["name"], "apr_pct": apr, "balance": d["balance"]},
           "amount": round(amount, 2), "horizon_months": H, "sources": out}
    if not eligible:
        res["note"] = f"{d['name']} is below the {ctx['hurdle_pct']:g}% prepay hurdle or secured: minimum only"
    lp = lump_plan(ctx, [s["source"] for s in out if s["use"] and s["source"] == "cash"])
    if lp:
        res["lump_plan"] = lp
        res["lump_plan_note"] = ("cash above the buffer can pay these high-cost debts today (highest APR first); "
                                 "simulate it as lump_sums; keep it only if simulate_plan stays feasible")
    return res


def lump_plan(ctx: dict, sources: list[str]) -> list[dict]:
    """Split cash above the buffer across every high-cost debt, highest APR first, cheapest source first."""
    avail = dict(ctx["lump_available"])
    out = []
    for d in sorted((x for x in ctx["debts"] if x["extra_eligible"] and x["balance"] > 0), key=lambda x: -x["apr_pct"]):
        left = d["balance"]
        for src in sources:
            take = round(min(left, avail[src], avail["cash_total"]), 2)
            if take >= 1:
                out.append({"source": src, "amount": take, "to_debt": d["account_id"]})
                left -= take
                avail[src] -= take
                avail["cash_total"] -= take
    return out


# ---------------- simulation ----------------

def cut_split(ctx: dict, targets: list[dict]) -> dict:
    """Monthly savings from spending targets, split into card (lower charges) and cash (checking)."""
    card_cut = cash_cut = 0.0
    for t in targets or []:
        c = ctx["spend"].get(t.get("category"))
        if not c:
            continue
        # Count the cut from the plan's own `current` (what the solver sized the budget on and the screen shows)
        # when it is within 5% of today's average, so the solver and the approve-time validator agree even if
        # the monthly averages moved a little since; otherwise from today's average.
        cur = float(t.get("current") or c["avg"])
        base = cur if c["avg"] and abs(cur - c["avg"]) <= 0.05 * c["avg"] else c["avg"]
        save = max(base - float(t.get("target", base)), 0.0)
        if c["avg"] > 0:
            card_cut += save * c["card_avg"] / c["avg"]
            cash_cut += save * c["cash_avg"] / c["avg"]
    return {"card": card_cut, "cash": cash_cut}


def plan_flows(ctx: dict, plan: dict) -> dict:
    """The plan's redirects by kind: surplus redirected, contributions paused per destination, unknown flows."""
    surplus, paused, unknown = 0.0, defaultdict(float), []
    movable = ctx["movable_flows"]
    for r in plan.get("redirects") or []:
        flow, amt = r.get("flow"), float(r.get("amount_per_month") or 0)
        if flow == SURPLUS:
            surplus += amt
        elif flow in movable:
            paused[movable[flow]["dest"]] += amt
        else:
            unknown.append(flow)
    return {"surplus": surplus, "paused": dict(paused), "total": surplus + sum(paused.values()), "unknown": unknown}


def budget_limit(ctx: dict, plan: dict) -> dict:
    """Where the monthly debt budget comes from, and how much of it actually shrinks the debt.

    Gross payments include paying off this month's new PNC card purchases, which is spending, not paydown:
        current_net_paydown_per_month = current_debt_payments - current_new_card_purchases
        net_paydown_per_month         = monthly_debt_budget - new_card_purchases (after card cuts)
    (month 1; interest is not subtracted). The simulator adds the purchases to the card balance and pays them
    from the gross budget, so nothing is counted twice."""
    pf = plan_flows(ctx, plan)
    cuts = cut_split(ctx, plan.get("spending_targets") or [])
    max_budget = ctx["current_debt_payments"] + pf["total"] + cuts["cash"]
    budget = float(plan.get("monthly_debt_budget") or 0) or max_budget
    charges = max(ctx["card_spend"] - cuts["card"], 0.0)
    return {
        "current_net_paydown_per_month": round(ctx["current_debt_payments"] - ctx["card_spend"], 2),
        "net_paydown_per_month": round(budget - charges, 2),
        "current_new_card_purchases": round(ctx["card_spend"], 2),
        "new_card_purchases": round(charges, 2),
        "monthly_debt_budget": round(budget, 2),
        "current_debt_payments": ctx["current_debt_payments"],
        "redirects": round(pf["total"], 2),
        "surplus_redirected": round(pf["surplus"], 2),
        "contributions_paused": round(sum(pf["paused"].values()), 2),
        "checking_cuts": round(cuts["cash"], 2),
        "card_cuts": round(cuts["card"], 2),
        "max_monthly_debt_budget": round(max_budget, 2),
        "minimums": round(sum(d["min_payment"] for d in ctx["debts"]), 2),
    }


def reality_check(customer_id: str, *, live: bool = True, ctx: dict | None = None) -> dict:
    """Can the debt shrink at all with this income and spending, even with the best plan the solver can build?

    Best case = solver.build_plan with every optional cut at its floor (solver.floor_target: trim to
    max(lowest month, avg - max_cut_pct), cancel the named recurring charges), every movable flow it allows,
    and the lump sums it finds worth making. Then, in month 1 (interest only falls as the debt shrinks, so
    month 1 binds):
        room      = that plan's monthly debt budget, less any monthly shortfall that drains cash
        growth    = interest (after the lump sums) + card charges (after card cuts)
        shrinks  <=> room > growth and room >= every minimum
    It is also unrealistic when that plan, simulated, takes cash below $0 within the horizon (`cash_runs_out`
    is the first such month): the simulator keeps paying debt out of negative cash, so the debt test alone
    can pass for a household that spends more than it earns.
    When it is unrealistic, `gap_per_month` is the larger of the monthly shortfall (spending after the floor
    cuts + minimums - income) and the debt gap: spend `gap` less (beyond those cuts) or earn `gap` more, or a mix.
    """
    from . import solver

    ctx = ctx or context(customer_id, live=live)
    floors = [ft for cat in ctx["spend"] if (ft := solver.floor_target(customer_id, ctx, cat))]
    targets = [{"category": ft["category"], "target": ft["target"],
                **({"how": [f"Cancel {n}" for n in ft["cancel"]]} if ft.get("cancel") else {})} for ft in floors]
    plan, _ = solver.build_plan(customer_id, {"keep_emergency_months": ctx["keep_months"], "spending_targets": targets},
                                live=live, ctx=ctx)
    return _reality(ctx, plan)


def _reality(ctx: dict, plan: dict) -> dict:
    f = ctx["flows"]
    split = cut_split(ctx, plan.get("spending_targets") or [])
    card_cut, cash_cut = split["card"], split["cash"]
    cut_total = card_cut + cash_cut
    cuts = sorted(({"category": t["category"], "current": round(ctx["spend"][t["category"]]["avg"], 2),
                    "target": round(float(t["target"]), 2),
                    "save": round(ctx["spend"][t["category"]]["avg"] - float(t["target"]), 2)}
                   for t in plan.get("spending_targets") or [] if t["category"] in ctx["spend"]), key=lambda x: -x["save"])
    disc_now = sum(c["avg"] for c in ctx["spend"].values() if not c["essential"] and c["avg"] > 0)

    names = {d["account_id"]: d["name"] for d in ctx["debts"]}
    bal = {d["account_id"]: d["balance"] for d in ctx["debts"]}
    for ls in plan.get("lump_sums") or []:
        bal[ls["to_debt"]] = max(bal.get(ls["to_debt"], 0.0) - float(ls["amount"]), 0.0)
    lumps = [{**x, "name": names.get(x["to_debt"], x["to_debt"])} for x in plan.get("lump_sums") or []]
    redirect = sum(float(r["amount_per_month"]) for r in plan.get("redirects") or [])

    interest_now = sum(d["balance"] * d["apr_pct"] / 1200 for d in ctx["debts"])
    interest = sum(bal[d["account_id"]] * d["apr_pct"] / 1200 for d in ctx["debts"])
    minimums = sum(d["min_payment"] for d in ctx["debts"] if bal[d["account_id"]] > 0.005)
    charges = max(ctx["card_spend"] - card_cut, 0.0)
    room = float(plan["monthly_debt_budget"]) + min(f["residual"], 0.0)  # a negative residual drains cash first
    growth = interest + charges
    paydown = room - growth
    needed = max(minimums, growth)
    debt_gap = max(needed - room, 0.0)
    shrinks = paydown > 0.5 and room >= minimums - 0.005
    # Spending after the floor cuts (cash + card) + minimums - income, written from the residual
    # (residual = income - cash expenses - debt payments - contributions; card spend is paid inside debt payments).
    # Contributions are left out: they can be paused.
    all_mins = sum(d["min_payment"] for d in ctx["debts"])
    shortfall = (ctx["card_spend"] - card_cut - cash_cut + all_mins
                 - f["residual"] - f["debt_payments"] - f["invest_contrib"])
    sim = _run(ctx, plan)
    runs_out = next((m["month"] for m in sim["months"] if m["cash"] < -0.5), None)
    realistic = shrinks and runs_out is None
    gap = max(debt_gap, shortfall, 0.0)
    if not realistic and gap < 1:
        gap = 1.0  # break-even: the debt doesn't shrink, so they still need a little more room
    disc_left = max(disc_now - cut_total, 0.0)

    if realistic:
        msg = f"The debt can shrink: with the best plan, about ${paydown:,.0f}/mo goes to principal."
    else:
        why = (f"cash runs out by {runs_out}" if runs_out and shrinks
               else f"the debt keeps growing and cash runs out by {runs_out}" if runs_out
               else "the debt keeps growing")
        msg = (f"Even after every realistic change you are ${gap:,.0f}/mo short, so {why}. "
               f"To close it, spend ${gap:,.0f}/mo less or earn ${gap:,.0f}/mo more (or a mix).")
        if gap > disc_left + 0.5:
            msg += (f" Spending cuts alone can't do it: only ${disc_left:,.0f}/mo of discretionary spending is left, "
                    f"so income must rise by at least ${gap - disc_left:,.0f}/mo.")

    r = lambda x: round(x, 2)  # noqa: E731
    return {
        "realistic": realistic,
        "message": msg,
        "gap_per_month": r(gap) if not realistic else 0.0,
        "cash_runs_out": runs_out,  # first month the best plan's cash goes below $0, else None
        "close_gap": None if realistic else {
            "spend_less_per_month": r(gap),
            "earn_more_per_month": r(gap),
            "spending_target_per_month": r(max(f["expenses"] - cut_total - gap, 0.0)),
            "income_target_per_month": r(f["income"] + gap),
            "spending_alone_can_close": gap <= disc_left + 0.5,
            "min_extra_income_if_spending_maxed": r(max(gap - disc_left, 0.0)),
        },
        "monthly": {
            "income": r(f["income"]),
            "spending": r(f["expenses"]),
            "debt_room": r(room),            # the best plan's debt budget that keeps cash flat
            "interest": r(interest),         # after the lump sums
            "card_charges": r(charges),      # after the card cuts
            "minimums": r(minimums),
            "principal_paydown": r(paydown),  # negative = the debt grows
            "shortfall": r(shortfall),        # spending after floor cuts + minimums - income; > 0 = cash drains
            "lowest_cash": r(min(m["cash"] for m in sim["months"])),
        },
        "today": {  # no changes at all
            "debt_change_per_month": r(interest_now + ctx["card_spend"] - ctx["current_debt_payments"]),
            "cash_change_per_month": r(f["residual"]),
        },
        "levers": {
            "spending_cuts_per_month": r(cut_total),
            "cuts": cuts,
            "redirect_per_month": r(redirect),
            "lump_sums": lumps,
            "discretionary_left_per_month": r(disc_left),
        },
    }


PAYOFF_SEARCH_MONTHS = 60  # how far past the horizon to look for the high-cost $0 month


def simulate_plan(customer_id: str, plan: dict | None, *, live: bool = True, ctx: dict | None = None) -> dict:
    """Month-by-month: every debt, cash, invested, net worth. plan None = baseline (current behaviour)."""
    keep = (plan or {}).get("keep_emergency_months")
    ctx = ctx or context(customer_id, keep_months=keep, live=live)
    base = _run(ctx, None)
    if plan is None:
        res = base
    else:
        res = _run(ctx, plan, base_cash=[m["cash"] for m in base["months"]])
    res["baseline_net_worth_24m"] = base["net_worth_24m"]
    res["baseline_interest_total"] = base["interest_total"]
    res["baseline_high_cost_debt_free"] = base["high_cost_debt_free"]
    if plan is not None:
        res["budget"] = budget_limit(ctx, plan)
    return res


def _run(ctx: dict, plan: dict | None, base_cash: list[float] | None = None) -> dict:
    H = A.get("horizon_months")
    a = ctx["assets"]
    f = ctx["flows"]
    problems: list[str] = []
    debts = {d["account_id"]: dict(d, bal=d["balance"], interest=0.0, payoff=None) for d in ctx["debts"]}
    cash = a["cash"]
    inv = {"cd": a["cd"], "brokerage": a["brokerage"], "ira": a["ira"]}
    rets = {k: A.ret(k) for k in inv}
    contrib = dict(f["contrib"])
    prop = a["property"]
    upfront = 0.0

    if plan is None:
        charges = ctx["card_spend"]
        budget = None
        order: list[str] = []
        autopay: set[str] = set()
        saved = f["residual"]  # stays in cash each month
    else:
        cuts = cut_split(ctx, plan.get("spending_targets") or [])
        charges = max(ctx["card_spend"] - cuts["card"], 0.0)
        budget = float(plan.get("monthly_debt_budget") or 0)
        order = [x for x in plan.get("extra_order") or [] if x in debts]
        for x in plan.get("extra_order") or []:
            if x not in debts:
                problems.append(f"extra_order: unknown debt {x}")
        autopay = {x for x in plan.get("autopay_full_cards") or [] if x in debts}
        pf = plan_flows(ctx, plan)
        for flow in pf["unknown"]:
            problems.append(f"redirect flow {flow!r} is not a movable flow")
        lim = ctx["movable_flows"][SURPLUS]["max_per_month"]
        if pf["surplus"] > lim + 0.5:
            problems.append(f"redirecting ${pf['surplus']:,.0f}/mo exceeds the ${lim:,.0f}/mo surplus left in checking")
        for dest, amt in pf["paused"].items():
            if amt > contrib.get(dest, 0.0) + 0.5:
                problems.append(f"pausing ${amt:,.0f}/mo of {dest} contributions exceeds the ${contrib.get(dest, 0):,.0f}/mo made")
            contrib[dest] = max(contrib.get(dest, 0.0) - amt, 0.0)
        # The budget is paid from today's payments + freed money; whatever it doesn't use is still saved.
        saved = f["residual"] + ctx["current_debt_payments"] + sum(pf["paused"].values()) + cuts["cash"] - budget
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
        if cash < -0.5:
            problems.append("lump sums take more cash than there is")

    floor = min(ctx["emergency_buffer"], a["cash"]) - 1
    cash_problem = False

    def snapshot(i: int) -> dict:
        total = sum(d["bal"] for d in debts.values())
        high = sum(d["bal"] for d in debts.values() if d["high_cost"])
        invested = sum(inv.values())
        return {"month": _label(i), "total_debt": round(total, 2), "high_cost_debt": round(high, 2),
                "cash": round(cash, 2), "invested": round(invested, 2),
                "net_worth": round(cash + invested + prop - total, 2),
                # per-account balances for the plan timeline (timeline.py); not sent to the LLM
                "balances": {"debts": {k: round(d["bal"], 2) for k, d in debts.items()},
                             "assets": {"cash": round(cash, 2), **{k: round(v, 2) for k, v in inv.items()},
                                        "property": round(prop, 2)}},
                # the same balances, rounded to dollars (plan-view breakdown)
                "parts": {"cash": round(cash), **{k: round(v) for k, v in inv.items()}, "property": round(prop),
                          "debts": {k: round(d["bal"]) for k, d in debts.items()}}}

    months = [snapshot(0)]
    first_pay: dict[str, float] = {}  # month-1 payment per debt (the recurring bills set up on approve)
    high_free = 0 if months[0]["high_cost_debt"] <= 0.01 else None
    short_reported = False
    # Past the horizon, keep stepping (up to PAYOFF_SEARCH_MONTHS) only to find when high-cost debt reaches $0;
    # the reported months, net worth, interest and problems stay within the horizon.
    for i in range(1, max(H, PAYOFF_SEARCH_MONTHS) + 1):
        if i > H and high_free is not None:
            break
        cash *= 1 + a["cash_yield_pct"] / 1200
        for k in inv:
            inv[k] *= 1 + (rets[k] or 0) / 1200
            inv[k] += contrib.get(k, 0.0)
        was_zero = {k for k, d in debts.items() if d["bal"] <= 0.005}
        for d in debts.values():
            it = d["bal"] * d["apr_pct"] / 1200
            d["bal"] += it
            if i <= H:
                d["interest"] += it
            if d["is_pnc_card"]:
                d["bal"] += charges
        left = 0.0
        if budget is None:
            for d in debts.values():
                d["bal"] -= min(d["baseline_payment"], d["bal"])
        else:
            left = budget
            for k, d in debts.items():  # autopay statement in full, then minimums
                due = d["bal"] if (k in autopay and k in was_zero) else min(d["min_payment"], d["bal"])
                pay = min(due, left)
                if pay + 0.005 < due and not short_reported and i <= H:
                    problems.append(f"{_label(i)}: budget can't cover {d['name']}'s payment")
                    short_reported = True
                d["bal"] -= pay
                left -= pay
                if i == 1:
                    first_pay[k] = first_pay.get(k, 0.0) + pay
            for k in order:
                d = debts[k]
                pay = min(d["bal"], left)
                d["bal"] -= pay
                left -= pay
                if i == 1:
                    first_pay[k] = first_pay.get(k, 0.0) + pay
        cash += saved + left  # the residual (and any unused budget) stays in cash
        for d in debts.values():
            if d["bal"] <= 0.005:
                d["bal"] = 0.0
                if d["payoff"] is None:
                    d["payoff"] = i
            elif d["payoff"] is not None and d["is_card"]:
                d["payoff"] = None  # card refilled
        # A plan may not take cash below the buffer, nor lower than where current behaviour already leaves it
        # (a household that is already short is not made shorter; a plan cannot fix a negative monthly residual).
        if i > H:
            if sum(d["bal"] for d in debts.values() if d["high_cost"]) <= 0.01:
                high_free = i
            continue
        floor_i = min(floor, base_cash[i] - 1) if base_cash else floor
        if plan is not None and cash < floor_i and not cash_problem:
            problems.append(f"{_label(i)}: cash ${cash:,.0f} falls below the {ctx['keep_months']:g}-month buffer "
                            f"(${ctx['emergency_buffer']:,.0f})"
                            + (f" and below the ${base_cash[i]:,.0f} current behaviour leaves" if base_cash and base_cash[i] < floor else ""))
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
                   "interest": round(d["interest"], 2),
                   "first_month_payment": round(first_pay.get(d["account_id"], 0.0), 2)} for d in debts.values()],
        "months": months,
        "net_worth_24m": months[-1]["net_worth"],
        "net_worth_now": months[0]["net_worth"] + (upfront if plan else 0.0),
        "interest_total": round(sum(d["interest"] for d in debts.values()), 2),
        "high_cost_debt_free": _label(high_free) if high_free is not None else None,
        "upfront_cost": round(upfront, 2),
        "monthly_card_spending": round(charges, 2),
        "monthly_saved": round(saved, 2),
    }


# ---------------- optional cuts (the customer's choice) ----------------

def cut_options(customer_id: str, plan: dict, *, n: int = 3, live: bool = True, ctx: dict | None = None,
                sim: dict | None = None) -> list[dict]:
    """Spending cuts the plan does NOT make, each priced on its own: the plan plus that one cut (trim to its
    limit, never below the lowest month; subscriptions by cancelling the biggest recurring charge), every freed
    dollar to debt. Cuts are the customer's choice, so a plan never needs them; these are the tradeoffs to offer.
    Returns the `n` that buy the most 24-month net worth."""
    ctx = ctx or context(customer_id, keep_months=plan.get("keep_emergency_months"), live=live)
    base = sim or simulate_plan(customer_id, plan, ctx=ctx)
    have = {t.get("category") for t in plan.get("spending_targets") or []}
    rooms = trim_room(ctx)
    out = []
    for cat, c in ctx["spend"].items():
        if cat in have or c["avg"] <= 0:
            continue
        flag = categories.spending_flag(cat)
        meta = _detail_meta(customer_id, cat)
        names = [m["name"] for m in meta["merchants"][:2]]
        if flag["flag"] == categories.TRIM and rooms.get(cat, {}).get("room", 0) >= 5:
            target = math.ceil(rooms[cat]["floor"] * 100) / 100
            how = [f"Scale back {' and '.join(names)}" if names else "Scale back"]
        elif flag["flag"] == categories.CANCEL and meta["recurring"]:
            r = max(meta["recurring"], key=lambda r: float(r.get("amount") or 0))
            target = math.ceil(max(0.0, c["avg"] - float(r["amount"])) * 100) / 100
            how = [f"Cancel {r['name']}"]
        else:
            continue
        proof = next((m["month"] for m in c["monthly"] if m["amount"] <= target + 0.5), c["min_month"])
        t = {"category": cat, "current": c["avg"], "target": target, "proof_month": proof, "how": how}
        cand = {**plan, "spending_targets": [*(plan.get("spending_targets") or []), t]}
        if cand.get("extra_order"):
            cand["monthly_debt_budget"] = round(budget_limit(ctx, cand)["max_monthly_debt_budget"], 2)
        s = simulate_plan(customer_id, cand, ctx=ctx)
        if not s.get("feasible"):
            continue
        sooner = None
        if base.get("high_cost_debt_free") and s.get("high_cost_debt_free"):
            (ya, ma), (yb, mb) = (map(int, x.split("-")) for x in (base["high_cost_debt_free"], s["high_cost_debt_free"]))
            sooner = (ya - yb) * 12 + (ma - mb)
        out.append({
            "category": cat, "label": flag["label"], "flag": flag["flag"], "current": round(c["avg"], 2),
            "target": target, "cut_per_month": round(c["avg"] - target, 2), "how": how, "merchants": names,
            "proof_month": proof, "high_cost_debt_free": s.get("high_cost_debt_free"), "months_sooner": sooner,
            "net_worth_24m_gain": round(s["net_worth_24m"] - base["net_worth_24m"], 2),
            "interest_saved": round(base["interest_total"] - s["interest_total"], 2),
            "spending_target": t,
        })
    out.sort(key=lambda o: -o["net_worth_24m_gain"])
    return out[:n]
