"""Print the solver's plan for a customer, readable.

    uv run python scripts/solve.py C001 [C002 ...] [--keep 3] [--cut dining=291 ...] [--md]

Lumps, monthly budget and order, payoff month per debt, net worth at 24 months vs baseline, interest saved,
and the tradeoffs table (what cutting each flexible category to its floor would buy).
"""

from __future__ import annotations

import argparse

from paidway.planning import solver, wealth


def money(x: float | None) -> str:
    return "-" if x is None else f"${x:,.0f}"


def render(cid: str, r: dict, ctx: dict) -> str:
    plan, sim, b = r["plan"], r["sim"], r["brief"]
    names = {d["account_id"]: d for d in ctx["debts"]}
    src_name = {"cash": "PNC cash", "outside_savings": "outside savings", "cd": "CD", "brokerage": "brokerage"}
    out = [f"## {cid}", ""]
    gain = sim["net_worth_24m"] - sim["baseline_net_worth_24m"]
    out.append(f"Net worth at 24 months: **{money(sim['net_worth_24m'])}** vs {money(sim['baseline_net_worth_24m'])} "
               f"today's way (**{'+' if gain >= 0 else ''}{money(gain)}**). Interest: {money(sim['interest_total'])} vs "
               f"{money(sim['baseline_interest_total'])} (saves {money(b['interest_saved'])}). High-cost debt free: "
               f"**{sim['high_cost_debt_free'] or 'not within 24 months'}** (today's way: "
               f"{sim.get('baseline_high_cost_debt_free') or 'not within 24 months'}).")
    out.append("")
    out.append(f"Buffer kept: {plan['keep_emergency_months']:g} months ({money(ctx['emergency_buffer'])}); "
               f"cash above it today: {money(ctx['lump_available']['cash_total'])}.")
    out += ["", "**Lump sums today**", ""]
    if plan["lump_sums"]:
        out += ["| from | amount | to | APR |", "|---|---:|---|---:|"]
        for l in plan["lump_sums"]:
            d = names[l["to_debt"]]
            out.append(f"| {src_name.get(l['source'], l['source'])} | {money(l['amount'])} | {d['name']} | {d['apr_pct']:g}% |")
        if sim["upfront_cost"]:
            out.append(f"\nOne-time tax/penalty on sales: {money(sim['upfront_cost'])}.")
    else:
        out.append("None: no cash above the buffer (or nothing worth selling).")
    lim = sim["budget"]
    out += ["", f"**Monthly debt budget: {money(plan['monthly_debt_budget'])}** = today's payments "
            f"{money(lim['current_debt_payments'])} + redirects {money(lim['redirects'])} + checking cuts "
            f"{money(lim['checking_cuts'])}. Minimums first ({money(lim['minimums'])}), then extra in this order: "
            + (" → ".join(f"{names[x]['name']} ({names[x]['apr_pct']:g}%)" for x in plan["extra_order"]) or "none") + "."]
    if plan["redirects"]:
        out.append("Redirects: " + "; ".join(f"{x['flow']} {money(x['amount_per_month'])}/mo" for x in plan["redirects"]) + ".")
    if plan["spending_targets"]:
        out.append("Cuts (the person's): " + "; ".join(f"{t['category']} {money(t['current'])} → {money(t['target'])}"
                                                     for t in plan["spending_targets"]) + ".")
    out += ["", "**Payoff by debt**", "", "| debt | APR | balance | payoff | interest |", "|---|---:|---:|---|---:|"]
    for d in sim["debts"]:
        out.append(f"| {d['name']} | {d['apr_pct']:g}% | {money(d['balance'])} | {d['payoff_month'] or 'after 24 mo'} | "
                   f"{money(d['interest'])} |")
    out += ["", "**Tradeoffs** (each flexible category cut to its floor, on top of this plan; not in the plan)", ""]
    if r["tradeoffs"]:
        out += ["| category | now | floor | cut/mo | high-cost debt free | months sooner | NW at 24 mo |",
                "|---|---:|---:|---:|---|---:|---:|"]
        for t in r["tradeoffs"]:
            ms = "-" if t["months_sooner"] is None else str(t["months_sooner"])
            out.append(f"| {t['category']} | {money(t['current'])} | {money(t['floor'])} | {money(t['cut_per_month'])} | "
                       f"{t['high_cost_debt_free'] or 'after 24 mo'} | {ms} | +{money(t['net_worth_gain'])} |")
    else:
        out.append("No flexible category can go lower than it already does.")
    return "\n".join(out) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("customers", nargs="+")
    ap.add_argument("--keep", type=float, default=None, help="emergency months")
    ap.add_argument("--cut", action="append", default=[], help="category=target (the person's choice)")
    ap.add_argument("--live", action="store_true", help="live Nessie balances (default: seeded data)")
    a = ap.parse_args()
    inputs = {"keep_emergency_months": a.keep,
              "spending_targets": [{"category": c.split("=")[0], "target": float(c.split("=")[1])} for c in a.cut]}
    for cid in a.customers:
        r = solver.solve(cid, inputs, live=a.live)
        print(render(cid, r, wealth.context(cid, keep_months=a.keep, live=a.live)))


if __name__ == "__main__":
    main()
