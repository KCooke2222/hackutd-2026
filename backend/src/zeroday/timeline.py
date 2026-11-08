"""Plan over time: the home page (accounts, income vs spending), month by month, before vs after a plan.

Read-only. Built on wealth.simulate_plan (per-account balances per month) plus the context the simulator
used, so every number here matches the simulation. Generic over account ids, spending categories and
redirect flow names: nothing is hard-coded to one customer or one flow.

    timeline(cid, plan) -> {months, accounts, payments, cashflow, changes, ...}
"""

from __future__ import annotations

from . import wealth

ASSET_NAMES = {"cash": "Cash (checking + savings)", "cd": "CD", "brokerage": "Brokerage", "ira": "IRA",
               "property": "Property"}
SOURCE_NAMES = {"cash": "Savings", "cd": "CD", "brokerage": "Brokerage"}


def _usd(x: float) -> str:
    return f"${x:,.0f}"


def _title(s: str) -> str:
    s = str(s).replace("_", " ")
    return s[:1].upper() + s[1:]


def _payments(sim: dict, ctx: dict) -> dict[str, list[float]]:
    """Per debt, what was paid each month 1..H: last balance + interest + new card charges - new balance."""
    months = sim["months"]
    charges = float(sim.get("monthly_card_spending") or 0)
    out: dict[str, list[float]] = {}
    for d in ctx["debts"]:
        k, r = d["account_id"], d["apr_pct"] / 1200
        add = charges if d["is_pnc_card"] else 0.0
        bals = [m["balances"]["debts"][k] for m in months]
        out[k] = [round(max(bals[i - 1] * (1 + r) + add - bals[i], 0.0), 2)
                  for i in range(1, len(bals))]
    return out


_MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _ym(s: str | None) -> str:
    return f"{_MON[int(s[5:7]) - 1]} {s[:4]}" if s else "never"


def _steps(pay: list[float], labels: list[str], payoff: str | None, autopay: bool) -> str:
    """'$1,269 in Nov 2026, then paid off' / '$29/mo to Dec 2026, then ~$512/mo to Feb 2027, then paid off'."""
    def seg(xs: list[float], start: int) -> str:
        if len(xs) == 1:
            return f"{_usd(xs[0])} in {_ym(labels[start])}"
        same = all(abs(x - xs[0]) <= max(5.0, 0.03 * xs[0]) for x in xs)
        avg = sum(xs) / len(xs)
        return f"{'' if same else '~'}{_usd(avg)}/mo to {_ym(labels[start + len(xs) - 1])}"

    end = labels.index(payoff) + 1 if payoff in labels else len(pay)
    pre, post = pay[:end], pay[end:]
    parts: list[str] = []
    if pre:
        n = 1
        while n < len(pre) and abs(pre[n] - pre[0]) <= max(5.0, 0.03 * pre[0]):
            n += 1
        if n == len(pre) and payoff not in labels:
            parts.append(f"{_usd(pre[0])}/mo")
        else:
            parts.append(seg(pre[:n], 0))
            if n < len(pre):
                parts.append(seg(pre[n:], n))
    if payoff in labels:
        rest = [x for x in post if x >= 1]
        if not rest:
            parts.append("paid off")
        else:
            parts.append(f"{_usd(sum(rest) / len(rest))}/mo{' (statement in full)' if autopay else ''}")
    return ", then ".join(parts)


def _to_cash(sim: dict, ctx: dict) -> list[float]:
    """Money added to cash each month, net of the interest cash earns."""
    y = ctx["assets"]["cash_yield_pct"] / 1200
    c = [m["cash"] for m in sim["months"]]
    return [round(c[i] - c[i - 1] * (1 + y), 2) for i in range(1, len(c))]


def _spend_split(cid: str, ctx: dict) -> dict:
    """Essentials / discretionary monthly averages (home page basis), falling back to one spending bucket."""
    try:
        from .insights import cash_flow

        a = cash_flow(cid)["monthly_avg"]
        return {"essentials": float(a["essentials"]), "discretionary": float(a["discretionary"])}
    except Exception:  # noqa: BLE001
        return {"essentials": float(ctx["flows"]["expenses"]), "discretionary": 0.0}


def timeline(customer_id: str, plan: dict, *, live: bool = True) -> dict:
    ctx = wealth.context(customer_id, keep_months=plan.get("keep_emergency_months"), live=live)
    base = wealth.simulate_plan(customer_id, None, ctx=ctx)
    sim = wealth.simulate_plan(customer_id, plan, ctx=ctx)
    labels = [m["month"] for m in sim["months"]]
    f = ctx["flows"]

    # ---- accounts: balance per month, before vs plan ----
    payoff = {d["account_id"]: d["payoff_month"] for d in sim["debts"]}
    base_payoff = {d["account_id"]: d["payoff_month"] for d in base["debts"]}
    accounts = []
    for d in ctx["debts"]:
        k = d["account_id"]
        accounts.append({
            "id": k, "name": d["name"], "side": "debt", "kind": d["account_type"], "apr_pct": d["apr_pct"],
            "plan": [m["balances"]["debts"][k] for m in sim["months"]],
            "before": [m["balances"]["debts"][k] for m in base["months"]],
            "payoff_plan": payoff.get(k), "payoff_before": base_payoff.get(k),
        })
    for k in ("cash", "cd", "brokerage", "ira", "property"):
        p = [m["balances"]["assets"][k] for m in sim["months"]]
        b = [m["balances"]["assets"][k] for m in base["months"]]
        if any(abs(x) > 0.005 for x in p + b):
            accounts.append({"id": k, "name": ASSET_NAMES[k], "side": "asset", "kind": k, "plan": p, "before": b})

    # ---- payments per debt, before vs plan ----
    pay, base_pay = _payments(sim, ctx), _payments(base, ctx)
    order = {k: i for i, k in enumerate(plan.get("extra_order") or [])}
    autopay = set(plan.get("autopay_full_cards") or [])
    lumps: dict[str, float] = {}
    for ls in plan.get("lump_sums") or []:
        if float(ls.get("amount") or 0) > 0:
            lumps[ls.get("to_debt")] = lumps.get(ls.get("to_debt"), 0.0) + float(ls["amount"])
    charges_plan = float(sim.get("monthly_card_spending") or 0)
    charges_base = float(base.get("monthly_card_spending") or 0)
    payments = []
    for d in ctx["debts"]:
        k = d["account_id"]
        payments.append({
            "account_id": k, "name": d["name"], "apr_pct": d["apr_pct"], "min_payment": d["min_payment"],
            "before": base_pay[k], "plan": pay[k],
            "before_now": base_pay[k][0], "plan_now": pay[k][0],
            "extra_rank": order[k] + 1 if k in order else None,
            "autopay": k in autopay, "lump_sum": round(lumps.get(k, 0.0), 2),
            "card_charges": {"before": round(charges_base, 2), "plan": round(charges_plan, 2)} if d["is_pnc_card"] else None,
            "payoff_plan": payoff.get(k), "payoff_before": base_payoff.get(k),
        })

    # ---- money each month (home page buckets), before vs plan ----
    cuts = {t.get("category"): t for t in plan.get("spending_targets") or []}
    cut_by: dict[str, float] = {"essentials": 0.0, "discretionary": 0.0}
    for cat, t in cuts.items():
        c = ctx["spend"].get(cat)
        if c:
            cut_by["essentials" if c.get("essential") else "discretionary"] += max(c["avg"] - float(t.get("target", c["avg"])), 0.0)
    split = _spend_split(customer_id, ctx)
    redirect = sum(float(r.get("amount_per_month") or 0) for r in plan.get("redirects") or [])
    flow_names = [str(r.get("flow")) for r in plan.get("redirects") or [] if r.get("flow")]
    out_name = _title(flow_names[0]) if len(flow_names) == 1 else "Transfers out"

    def rows(s: dict, after: bool) -> list[dict]:
        tc = _to_cash(s, ctx)
        ess = max(split["essentials"] - (cut_by["essentials"] if after else 0), 0.0)
        dis = max(split["discretionary"] - (cut_by["discretionary"] if after else 0), 0.0)
        out = max(f["unknown_out"] - (redirect if after else 0), 0.0)
        inv = f["invest_contrib"]
        res = []
        for i, lab in enumerate(labels[1:]):
            debt = f["income"] - ess - dis - inv - out - tc[i]
            res.append({"month": lab, "income": round(f["income"], 2), "essentials": round(ess, 2),
                        "discretionary": round(dis, 2), "debt_payments": round(max(debt, 0.0), 2),
                        "savings_invest": round(inv, 2), "transfers_out": round(out, 2), "surplus": round(tc[i], 2)})
        return res

    cashflow = {"before": rows(base, False), "plan": rows(sim, True), "transfers_out_name": out_name}

    # ---- every change and every non-change ----
    changes: list[dict] = []

    def add(group: str, label: str, text: str, changed: bool, sub: str = "", before=None, after=None):
        changes.append({"group": group, "label": label, "text": text, "changed": changed, "sub": sub,
                        "before": before, "after": after})

    for p in payments:
        b, a = p["before_now"], p["plan_now"]
        subs = []
        if p["lump_sum"]:
            subs.append(f"{_usd(p['lump_sum'])} once now")
        if p["extra_rank"]:
            subs.append(f"gets extra money (#{p['extra_rank']})")
        if p["payoff_plan"] != p["payoff_before"]:
            subs.append(f"paid off {_ym(p['payoff_plan']) if p['payoff_plan'] else 'after 2 yrs'} (now: {_ym(p['payoff_before']) if p['payoff_before'] else 'not within 2 yrs'})")
        if p["autopay"] and not p["card_charges"]:
            subs.append("then pay the statement in full each month")
        if p["card_charges"]:
            subs.append(f"includes ~{_usd(p['card_charges']['plan'])}/mo of new card spending")
        if all(abs(x - y) < 1 for x, y in zip(p["plan"], p["before"])):
            minimum = abs(b - p["min_payment"]) < 1
            add("Debts", p["name"], f"keep paying the {_usd(b)}{' minimum' if minimum else ''}", False, "; ".join(subs), b, a)
        else:
            add("Debts", p["name"], f"{_usd(b)} → {_steps(p['plan'], labels[1:], p['payoff_plan'], p['autopay'])}", True, "; ".join(subs), b, a)

    for r in plan.get("redirects") or []:
        amt = float(r.get("amount_per_month") or 0)
        if amt > 0:
            add("Money out", _title(r.get("flow") or "transfers"), f"{_usd(f['unknown_out'])} → {_usd(max(f['unknown_out'] - amt, 0))}/mo",
                True, f"{_usd(amt)}/mo goes to debt instead", f["unknown_out"], max(f["unknown_out"] - amt, 0))
    if redirect <= 0 and f["unknown_out"] > 0.5:
        add("Money out", "Transfers out", f"no change ({_usd(f['unknown_out'])}/mo)", False, "", f["unknown_out"], f["unknown_out"])
    for ls in plan.get("lump_sums") or []:
        amt = float(ls.get("amount") or 0)
        if amt > 0:
            name = next((d["name"] for d in ctx["debts"] if d["account_id"] == ls.get("to_debt")), ls.get("to_debt"))
            add("Money out", f"{SOURCE_NAMES.get(ls.get('source'), _title(ls.get('source') or ''))} → {name}",
                f"{_usd(amt)} once", True)

    for cat, c in sorted(ctx["spend"].items(), key=lambda kv: -kv[1]["avg"]):
        if c["avg"] < 1:
            continue
        t = cuts.get(cat)
        if t and float(t.get("target", c["avg"])) < c["avg"] - 0.5:
            add("Spending", _title(cat), f"{_usd(c['avg'])} → {_usd(float(t['target']))}/mo", True,
                "; ".join(t.get("how") or []), c["avg"], float(t["target"]))
        else:
            add("Spending", _title(cat), f"no change ({_usd(c['avg'])}/mo)", False, "", c["avg"], c["avg"])

    add("Income", "Income", f"no change ({_usd(f['income'])}/mo, assumed flat)", False, "", f["income"], f["income"])
    add("Saving", "Investing", f"no change ({_usd(f['invest_contrib'])}/mo)", False, "", f["invest_contrib"], f["invest_contrib"])
    sources = {ls.get("source") for ls in plan.get("lump_sums") or [] if float(ls.get("amount") or 0) > 0}
    for k in ("cd", "brokerage", "ira"):
        bal = ctx["assets"][k]
        if bal > 0.5 and k not in sources:
            add("Saving", ASSET_NAMES[k], f"keep it invested ({_usd(bal)})", False, "", bal, bal)
    tc_b, tc_p = _to_cash(base, ctx), _to_cash(sim, ctx)
    avg_b, avg_p = sum(tc_b[:12]) / 12, sum(tc_p[:12]) / 12
    add("Saving", "Added to cash", f"{_usd(avg_b)} → {_usd(avg_p)}/mo (first year avg)", abs(avg_p - avg_b) >= 1,
        f"keep at least {_usd(ctx['emergency_buffer'])} ({ctx['keep_months']:g} months of expenses)", avg_b, avg_p)

    return {
        "customer_id": customer_id,
        "months": labels,
        "accounts": accounts,
        "payments": payments,
        "cashflow": cashflow,
        "changes": changes,
        "totals": {
            "debt_payments": {"before": round(sum(p["before_now"] for p in payments), 2),
                              "plan": round(sum(p["plan_now"] for p in payments), 2)},
            "net_worth": {"before": [m["net_worth"] for m in base["months"]], "plan": [m["net_worth"] for m in sim["months"]]},
        },
        "feasible": sim["feasible"],
        "problems": sim["problems"],
    }
