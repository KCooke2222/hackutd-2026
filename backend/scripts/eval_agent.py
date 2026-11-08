"""Grade a live agent run against the C001 reference plan (docs/agent-v2/CONTRACT.md).

    cd backend && uv run python scripts/eval_agent.py [--runs N] [--customer C001] [--latest]

--latest grades the newest saved proposal without calling Nemotron.
Exit code 0 only if every hard check passes on every run.
"""

from __future__ import annotations

import argparse
import json
import sys
import time

from zeroday import balance_sheet, store
from zeroday.assumptions import ASSUMPTIONS
from zeroday.wealth import simulate_plan

HURDLE = ASSUMPTIONS["prepay_hurdle_pct"]


def reference_plan(cid: str) -> dict:
    """The plan a careful human would make for C001: redirect the unknown outflow, avalanche the
    high-cost debts, small proven cuts, no asset sales."""
    from zeroday import insights

    debts = [a for a in balance_sheet.accounts(cid) if a["side"] == "debt"]
    high = sorted((d for d in debts if d["apr_pct"] > HURDLE), key=lambda d: -d["apr_pct"])
    from zeroday.wealth import budget_limit, context

    cf = insights.cash_flow(cid)
    redirect = round(min(2500.0, cf["unknown_outflow_per_month"] * 0.85), 2)
    cuts = []
    for cat in ("dining", "shopping"):
        sd = insights.spending_detail(cid, cat)
        target = round(max(sd["min"], sd["avg"] * 0.8), 2)
        if target < sd["avg"]:
            names = [m["name"] for m in sd["merchants"][:2]] or [cat]
            cuts.append({"category": cat, "current": sd["avg"], "target": target,
                         "proof_month": sd["min_month"], "how": [f"fewer {n} orders" for n in names]})
    plan = {
        "version": 2,
        "monthly_debt_budget": 0.0,
        "extra_order": [d["account_id"] for d in high],
        "lump_sums": [],
        "redirects": [{"flow": "external_cash_management", "amount_per_month": redirect}],
        "spending_targets": cuts,
        "autopay_full_cards": [d["account_id"] for d in debts if d["account_type"] == "credit_card"],
        "keep_emergency_months": ASSUMPTIONS["default_emergency_months"],
    }
    plan["monthly_debt_budget"] = budget_limit(context(cid), plan)["max_monthly_debt_budget"]
    return plan


def grade(cid: str, plan: dict, sim: dict, ref_sim: dict, extra: dict) -> list[tuple[str, bool, str]]:
    debts = {a["account_id"]: a for a in balance_sheet.accounts(cid) if a["side"] == "debt"}
    high = sorted((k for k, d in debts.items() if d["apr_pct"] > HURDLE), key=lambda k: -debts[k]["apr_pct"])
    sources = {l["source"] for l in plan.get("lump_sums", []) if l.get("amount", 0) > 0}
    checks: list[tuple[str, bool, str]] = []

    def c(name, ok, detail=""):
        checks.append((name, bool(ok), detail))

    c("plan v2", plan.get("version") == 2, str(plan.get("version")))
    c("no IRA / property", not sources & {"ira", "property"}, str(sorted(sources)))
    brokerage = sum(l["amount"] for l in plan.get("lump_sums", []) if l["source"] == "brokerage")
    c("no brokerage sale (surplus covers it)", brokerage == 0, f"${brokerage:,.0f}")
    c("extra_order = high-cost debts by APR", plan.get("extra_order") == high,
      f"{plan.get('extra_order')} vs {high}")
    store_cards = [k for k, d in debts.items() if d["account_type"] == "credit_card" and d["institution"] != "PNC"]
    c("store card in plan", all(k in plan.get("extra_order", []) for k in store_cards), str(store_cards))
    redirect = sum(r.get("amount_per_month", 0) for r in plan.get("redirects", []))
    c("uses the unknown outflow (redirect or flag)", redirect >= 1000 or extra.get("needs_customer_input"),
      f"redirect ${redirect:,.0f}/mo, flag={extra.get('needs_customer_input')}")
    cuts_ok = all(t.get("proof_month") and t.get("how") and t["target"] >= 0.7 * t["current"] - 0.01
                  for t in plan.get("spending_targets", []))
    c("cuts have proof month + how, <=30%", cuts_ok, json.dumps(plan.get("spending_targets", []))[:200])
    c("keeps >= 2 months emergency", plan.get("keep_emergency_months", 0) >= ASSUMPTIONS["min_emergency_months"],
      str(plan.get("keep_emergency_months")))
    c("feasible", sim.get("feasible"), "; ".join(sim.get("problems", [])))
    ref_free, got_free = ref_sim.get("high_cost_debt_free"), sim.get("high_cost_debt_free")
    c("high-cost debt free within ref + 2 months", got_free and _months_between(ref_free, got_free) <= 2,
      f"{got_free} vs ref {ref_free}")
    c("24m net worth >= ref - $750", sim["net_worth_24m"] >= ref_sim["net_worth_24m"] - 750,
      f"${sim['net_worth_24m']:,.0f} vs ref ${ref_sim['net_worth_24m']:,.0f} (baseline ${sim['baseline_net_worth_24m']:,.0f})")
    c("beats baseline", sim["net_worth_24m"] > sim["baseline_net_worth_24m"], "")
    head = extra.get("headline") or ""
    c("headline <= 10 words", 0 < len(head.split()) <= 10, head)
    return checks


def _months_between(a: str | None, b: str | None) -> int:
    if not a or not b:
        return 99
    (ya, ma), (yb, mb) = map(int, a.split("-")), map(int, b.split("-"))
    return (yb - ya) * 12 + (mb - ma)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--customer", default="C001")
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--latest", action="store_true")
    args = ap.parse_args()
    cid = args.customer

    ref = reference_plan(cid)
    ref_sim = simulate_plan(cid, ref)
    print(f"REFERENCE  free={ref_sim['high_cost_debt_free']}  nw24=${ref_sim['net_worth_24m']:,.0f}  "
          f"baseline=${ref_sim['baseline_net_worth_24m']:,.0f}  feasible={ref_sim['feasible']}")

    all_ok = True
    for i in range(args.runs if not args.latest else 1):
        extra: dict = {}
        t0 = time.time()
        if not args.latest:
            from zeroday.agent import graph
            before = {p["id"] for p in store.list_proposals(cid)}
            out = graph.run(cid)
            extra = {k: out.get(k) for k in ("diagnosis", "explain", "error")}
            extra["needs_customer_input"] = (out.get("diagnosis") or {}).get("needs_customer_input")
            extra["headline"] = (out.get("explain") or {}).get("headline")
            new = [p for p in store.list_proposals(cid) if p["id"] not in before]
            if not new:
                print(f"RUN {i+1}: no proposal saved. error={out.get('error')!r} final={str(out.get('final'))[:300]!r}")
                all_ok = False
                continue
            prop = new[0]
        else:
            prop = store.list_proposals(cid)[0]
            extra["headline"] = prop.get("rationale")
        plan = prop["plan"] if isinstance(prop["plan"], dict) else json.loads(prop["plan"])
        sim = simulate_plan(cid, plan)
        checks = grade(cid, plan, sim, ref_sim, extra)
        ok = all(passed for _, passed, _ in checks)
        all_ok &= ok
        print(f"\nRUN {i+1}  proposal={prop['id']}  {time.time()-t0:.0f}s  {'PASS' if ok else 'FAIL'}")
        for name, passed, detail in checks:
            print(f"  {'✓' if passed else '✗'} {name:45s} {detail}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
