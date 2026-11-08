"""Grade a live agent run against an honest reference plan (docs/agent-v2/CONTRACT.md).

    cd backend && uv run python scripts/eval_agent.py [--runs N] [--customer C001] [--latest]

--latest grades the newest saved proposal without calling Nemotron.
Exit code 0 only if every hard check passes on every run.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time

from zeroday import balance_sheet, categories, store
from zeroday.assumptions import ASSUMPTIONS
from zeroday.wealth import simulate_plan

HURDLE = ASSUMPTIONS["prepay_hurdle_pct"]


def _ceil(x: float) -> float:
    return math.ceil(x * 100) / 100


def reference_plan(cid: str) -> dict:
    """The honest plan a careful human would make from real money only:
    - high-cost debt (above the hurdle) in avalanche order;
    - lump sums from PNC cash above the emergency buffer (if compare_sources says use);
    - if high-cost debt is still left, redirect the monthly surplus (if any) and pause flexible contributions;
    - no spending cuts: cuts are the customer's choice, offered as priced options (wealth.cut_options);
    - never a fixed or ask item, never IRA/property, no brokerage sale."""
    from zeroday import insights
    from zeroday.wealth import budget_limit, compare_sources, context

    ctx = context(cid)
    high = sorted((d for d in ctx["debts"] if d["apr_pct"] > HURDLE and d["balance"] > 0), key=lambda d: -d["apr_pct"])
    lumps, left = [], {d["account_id"]: d["balance"] for d in high}
    if high:
        cmp = compare_sources(cid, sum(left.values()), high[0]["account_id"])["sources"]
        ranked = [x["source"] for x in cmp if x["source"] == "cash" and x.get("use")]
        avail = dict(ctx["lump_available"])
        for src in ranked:
            for d in high:
                take = round(min(left[d["account_id"]], avail[src], avail["cash_total"]), 2)
                if take >= 1:
                    lumps.append({"source": src, "amount": take, "to_debt": d["account_id"]})
                    left[d["account_id"]] -= take
                    avail[src] -= take
                    avail["cash_total"] -= take
    redirects = []
    if sum(left.values()) > 1:
        for flow, f in ctx["movable_flows"].items():
            if f["max_per_month"] >= 1:
                redirects.append({"flow": flow, "amount_per_month": round(f["max_per_month"], 2)})
    plan = {
        "version": 2,
        "monthly_debt_budget": 0.0,
        "extra_order": [d["account_id"] for d in high],
        "lump_sums": lumps,
        "redirects": redirects,
        "spending_targets": [],
        "autopay_full_cards": [d["account_id"] for d in high if d["account_type"] == "credit_card"],
        "keep_emergency_months": ASSUMPTIONS["default_emergency_months"],
    }
    plan["monthly_debt_budget"] = round(budget_limit(ctx, plan)["max_monthly_debt_budget"], 2)
    return _fit_lumps(cid, plan)


def _fit_lumps(cid: str, plan: dict) -> dict:
    from zeroday.agent.validator import fit_lumps

    return fit_lumps(cid, plan)


def grade(cid: str, plan: dict, sim: dict, ref_sim: dict, extra: dict) -> list[tuple[str, bool, str]]:
    from zeroday.wealth import context

    ctx = context(cid)
    debts = {a["account_id"]: a for a in balance_sheet.accounts(cid) if a["side"] == "debt"}
    high = sorted((k for k, d in debts.items() if d["apr_pct"] > HURDLE and d["balance"] > 0),
                  key=lambda k: -debts[k]["apr_pct"])
    sources = {l["source"] for l in plan.get("lump_sums", []) if l.get("amount", 0) > 0}
    targets = plan.get("spending_targets", [])
    checks: list[tuple[str, bool, str]] = []

    def c(name, ok, detail=""):
        checks.append((name, bool(ok), detail))

    c("plan v2", plan.get("version") == 2, str(plan.get("version")))
    c("no IRA / property", not sources & {"ira", "property"}, str(sorted(sources)))
    brokerage = sum(l["amount"] for l in plan.get("lump_sums", []) if l["source"] == "brokerage")
    c("no brokerage sale", brokerage == 0, f"${brokerage:,.0f}")
    c("extra_order = high-cost debts by APR", plan.get("extra_order") == high,
      f"{plan.get('extra_order')} vs {high}")
    store_cards = [k for k in high if debts[k]["account_type"] == "credit_card" and debts[k]["institution"] != "PNC"]
    c("store card in plan", all(k in plan.get("extra_order", []) for k in store_cards), str(store_cards))

    # Flex flags: nothing fixed/ask moved, redirects only flexible flows, cuts within each category's limit.
    moved = [t["category"] for t in targets
             if t["target"] < ctx["spend"].get(t["category"], {}).get("avg", 0) - 0.5
             and categories.spending_flag(t["category"])["flag"] not in (categories.TRIM, categories.CANCEL)]
    c("no fixed/ask item moved", not moved, str(moved))
    flows = [r.get("flow") for r in plan.get("redirects", [])]
    c("every redirect is a flexible flow", all(f in categories.MOVABLE_FLOWS for f in flows), str(flows))
    over = [t["category"] for t in targets
            if categories.spending_flag(t["category"])["flag"] == categories.TRIM
            and t["target"] < ctx["spend"][t["category"]]["avg"] * (1 - categories.SPENDING[t["category"]]["max_pct"] / 100) - 0.5]
    c("cuts have proof month + how, within limits",
      all(t.get("proof_month") and t.get("how") for t in targets) and not over, json.dumps(targets)[:200])

    # Honest net worth: month 0 is the real balance sheet; the gain is at most interest saved + real cuts.
    nw0 = balance_sheet.balance_sheet(cid)["totals"]["net_worth"]
    cut_dollars = sum(max(0.0, ctx["spend"][t["category"]]["avg"] - t["target"]) for t in targets
                      if t["category"] in ctx["spend"]) * 24
    gain = sim["net_worth_24m"] - sim["baseline_net_worth_24m"]
    bound = sim["baseline_interest_total"] - sim["interest_total"] + cut_dollars * 1.05 + 300
    c("net worth honest", abs(sim["months"][0]["net_worth"] - nw0) <= 1 and gain <= bound,
      f"nw0 ${sim['months'][0]['net_worth']:,.0f} vs sheet ${nw0:,.0f}; gain ${gain:,.0f} <= ${bound:,.0f}")

    c("keeps >= 2 months emergency", plan.get("keep_emergency_months", 0) >= ASSUMPTIONS["min_emergency_months"],
      str(plan.get("keep_emergency_months")))
    c("feasible", sim.get("feasible"), "; ".join(sim.get("problems", [])))
    ref_free, got_free = ref_sim.get("high_cost_debt_free"), sim.get("high_cost_debt_free")
    on_time = (got_free is not None and _months_between(ref_free, got_free) <= 2) if ref_free else True
    c("high-cost debt free within ref + 2 months", on_time, f"{got_free} vs ref {ref_free}")
    c("24m net worth >= ref - $750", sim["net_worth_24m"] >= ref_sim["net_worth_24m"] - 750,
      f"${sim['net_worth_24m']:,.0f} vs ref ${ref_sim['net_worth_24m']:,.0f} (baseline ${sim['baseline_net_worth_24m']:,.0f})")
    c("beats baseline", sim["net_worth_24m"] > sim["baseline_net_worth_24m"], "")

    # Ask items become questions; no slack is said, not invented.
    diag = extra.get("diagnosis") or {}
    if not extra.get("latest"):
        asks = [k for k, v in ctx["spend"].items() if categories.spending_flag(k)["flag"] == categories.ASK and v["avg"] > 0]
        qs = diag.get("questions_for_customer") or []
        c("ask items raised as questions", not asks or len(qs) > 0, f"asks={asks} questions={qs}"[:200])
        residual = ctx["flows"]["residual"]
        said = diag.get("monthly_surplus")
        c("monthly slack stated honestly", isinstance(said, (int, float)) and said <= max(residual, 0) + 100,
          f"said {said} vs real {residual:,.0f}")
    head = extra.get("headline") or ""
    c("headline <= 10 words", 0 < len(head.split()) <= 10, head)
    return checks


def soft_grade(plan: dict, prop_sim: dict, extra: dict) -> list[tuple[str, bool, str]]:
    """Graded softly (reported, never fails a run): cuts are offered as choices, not imposed."""
    top = [o["category"] for o in (prop_sim.get("cut_options") or [])]
    said = [str(o.get("category")) for o in ((extra.get("explain") or {}).get("options") or []) if isinstance(o, dict)]
    need = min(2, len(top))
    hit = [x for x in top if x in said]
    return [
        ("mentions the main cut options as choices", len(hit) >= need, f"top={top} said={said}"),
        ("plan leaves cuts to the customer", not plan.get("spending_targets"),
         str([t["category"] for t in plan.get("spending_targets") or []])),
    ]


def _months_between(a: str | None, b: str | None) -> int:
    if not a or not b:
        return 99
    (ya, ma), (yb, mb) = map(int, a.split("-")), map(int, b.split("-"))
    return (yb - ya) * 12 + (mb - ma)


def adjust_eval(cid: str, message: str) -> bool:
    """PR 4: adjust the latest pending plan with `message`; check the rules were captured and obeyed, the parent
    was superseded, and the shown cost matches the simulations."""
    from zeroday.agent import constraints as C
    from zeroday.agent.graph import stream_adjust
    from zeroday.agent.validator import validate_plan_v2

    parent = next((p for p in store.list_proposals(cid) if p["status"] == "pending"), None)
    if not parent:
        print("ADJUST: no pending proposal to adjust; run without --adjust first")
        return False
    t0 = time.time()
    last: dict = {}
    for ev in stream_adjust(parent["id"], message):
        if ev["type"] in ("done", "error", "clarify"):
            last = ev
    print(f"\nADJUST {parent['id']} <- {message!r}  {time.time()-t0:.0f}s")
    if last.get("type") != "done" or last.get("clarify"):
        print(f"  ✗ no adjusted plan: {last.get('error') or last.get('clarify') or last.get('text')!r}")
        return False
    child = store.get_proposal(last["proposal_id"])
    cons = child.get("constraints") or {}
    sim = simulate_plan(cid, child["plan"])
    cmp = child["simulation"].get("compare") or {}
    checks = [
        ("rules captured", bool(cons), ", ".join(d["label"] for d in C.describe(cons)) or "none"),
        ("plan obeys the rules", not C.check(child["plan"], cons, None), "; ".join(C.check(child["plan"], cons, None)) or "ok"),
        ("parent superseded", store.get_proposal(parent["id"])["status"] == "superseded", store.get_proposal(parent["id"])["status"]),
        ("lineage", child["parent_id"] == parent["id"], child["parent_id"] or "-"),
        ("cost matches simulation", abs(cmp.get("net_worth_24m", {}).get("after", -1) - sim["net_worth_24m"]) < 1,
         f"shown ${cmp.get('net_worth_24m', {}).get('after', 0):,.0f} vs sim ${sim['net_worth_24m']:,.0f}"),
        ("warning iff worse", bool((child["simulation"].get("explain") or {}).get("warning")) == bool(cmp.get("worse")),
         f"worse={cmp.get('worse')} cost=${cmp.get('cost', 0):,.0f}"),
    ]
    try:
        validate_plan_v2(cid, child["plan"], constraints=cons)
        checks.append(("re-validates", True, "ok"))
    except Exception as e:  # noqa: BLE001
        checks.append(("re-validates", False, str(e)[:120]))
    for s in cons.get("forbid_sources", []):
        used = [ls for ls in child["plan"].get("lump_sums") or [] if ls.get("source") == s]
        checks.append((f"no lump sum from {s}", not used, f"{len(used)} found"))
    ok = all(c[1] for c in checks)
    print(f"  {'PASS' if ok else 'FAIL'}  headline={(child['simulation'].get('explain') or {}).get('headline')!r}")
    for name, passed, detail in checks:
        print(f"  {'✓' if passed else '✗'} {name:45s} {detail}")
    for line in cmp.get("changes") or []:
        print(f"      · {line}")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--customer", default="C001")
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--latest", action="store_true")
    ap.add_argument("--adjust", metavar="REQUEST", help="PR 4: adjust the latest pending plan, e.g. \"don't touch my savings\"")
    ap.add_argument("--show-ref", action="store_true")
    args = ap.parse_args()
    cid = args.customer
    if args.adjust:
        return 0 if adjust_eval(cid, args.adjust) else 1

    ref = reference_plan(cid)
    from zeroday.agent.validator import validate_plan_v2

    ref_sim = validate_plan_v2(cid, ref)  # the reference must pass the same validator the agent faces
    print(f"REFERENCE  free={ref_sim['high_cost_debt_free']}  nw24=${ref_sim['net_worth_24m']:,.0f}  "
          f"baseline=${ref_sim['baseline_net_worth_24m']:,.0f}  feasible={ref_sim['feasible']} {ref_sim['problems']}")
    if args.show_ref:
        print(json.dumps(ref, indent=1))

    all_ok = True
    for i in range(args.runs if not args.latest else 1):
        extra: dict = {}
        t0 = time.time()
        if not args.latest:
            from zeroday.agent import graph
            before = {p["id"] for p in store.list_proposals(cid)}
            out = graph.run(cid)
            extra = {k: out.get(k) for k in ("diagnosis", "explain", "error")}
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
            extra["latest"] = True
        plan = prop["plan"] if isinstance(prop["plan"], dict) else json.loads(prop["plan"])
        sim = simulate_plan(cid, plan)
        checks = grade(cid, plan, sim, ref_sim, extra)
        ok = all(passed for _, passed, _ in checks)
        all_ok &= ok
        print(f"\nRUN {i+1}  proposal={prop['id']}  {time.time()-t0:.0f}s  {'PASS' if ok else 'FAIL'}  "
              f"free={sim['high_cost_debt_free']}  nw24=${sim['net_worth_24m']:,.0f}  "
              f"baseline=${sim['baseline_net_worth_24m']:,.0f}")
        print("  plan:", json.dumps({k: plan.get(k) for k in ("monthly_debt_budget", "lump_sums", "redirects")})[:400])
        print("  cuts:", json.dumps([(t["category"], t["target"], t.get("how")) for t in plan.get("spending_targets", [])])[:300])
        if extra.get("diagnosis"):
            print("  diagnosis:", json.dumps(extra["diagnosis"])[:400])
        for name, passed, detail in checks:
            print(f"  {'✓' if passed else '✗'} {name:45s} {detail}")
        if not extra.get("latest"):
            for name, passed, detail in soft_grade(plan, prop.get("simulation") or {}, extra):
                print(f"  {'~✓' if passed else '~✗'} {name:44s} {detail}"[:300])
            print("  options:", json.dumps((extra.get("explain") or {}).get("options"))[:400])
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
