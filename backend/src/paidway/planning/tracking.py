"""Goal tracking for an approved plan v2, plus a seeded demo that fast-forwards months.

The contract is the approved plan's expected path from wealth.simulate_plan (deterministic). Actual months are
re-simulated one month at a time from the last actual state with the scenario's deviations applied; the
projection re-simulates the rest of the horizon from the latest actual state, assuming the recent behaviour
persists. Status compares the projected high-cost-debt-free month with the goal month.

Demo months live in our own SQLite (demo_months). Nothing here touches Nessie or the PNC CSVs, and nothing
approves or executes: choose() only saves a PENDING proposal.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import random
import time

from ..data import store
from . import wealth
from .assumptions import ASSUMPTIONS as A

SCENARIOS = {
    "on_plan": "On plan",
    "overspend": "Overspend: dining + shopping",
    "income_drop": "Income drop (~15%)",
    "missed_min": "Missed minimum",
    "windfall": "Windfall",
}
TOL = 0.10          # a debt more than 10% above its expected balance is drifting
TOL_FLOOR = 25.0    # ...and at least $25 above, so near-zero balances don't trip it on cents
LATE_FEE = 35.0
HARD_INCOME_DROP = 0.10
MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


# ---------------- small helpers ----------------

def _idx(label: str | None) -> int | None:
    """'2027-02' -> months after month 0 (wealth._label(0))."""
    if not label:
        return None
    y0, m0 = wealth._start_month()
    return (int(label[:4]) * 12 + int(label[5:7])) - (y0 * 12 + m0)


def _ym(label: str | None) -> str:
    return f"{MON[int(label[5:7]) - 1]} {label[:4]}" if label else "never"


def _usd(x: float) -> str:
    return f"-${abs(x):,.0f}" if x < 0 else f"${x:,.0f}"


def _rng(*parts) -> random.Random:
    h = hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()
    return random.Random(int(h[:16], 16))


_CTX: dict[tuple, tuple[float, dict]] = {}


def _context(cid: str, keep: float | None, live: bool) -> dict:
    """wealth.context is a few Nessie reads; cache it briefly so fast-forward clicks stay quick."""
    key = (cid, keep, live)
    hit = _CTX.get(key)
    if hit and time.time() - hit[0] < 300:
        return hit[1]
    ctx = wealth.context(cid, keep_months=keep, live=live)
    _CTX[key] = (time.time(), ctx)
    return ctx


# ---------------- storage (demo tables, our DB only) ----------------

DEMO_SCHEMA = """
create table if not exists demo_months (
  customer_id text not null,
  proposal_id text not null,
  month_index integer not null,   -- 1..horizon; month 0 is the plan's starting point
  scenario text not null,
  actual json not null,           -- balances, totals, category spend, events
  created_at text not null,
  primary key (customer_id, proposal_id, month_index)
);
"""


def _db():
    c = store._conn()
    c.executescript(DEMO_SCHEMA)
    return c


def history(cid: str, pid: str) -> list[dict]:
    with _db() as c:
        rows = c.execute("select month_index, scenario, actual from demo_months where customer_id=? and proposal_id=? "
                         "order by month_index", (cid, pid)).fetchall()
    return [{"month_index": r["month_index"], "scenario": r["scenario"], **json.loads(r["actual"])} for r in rows]


def _save(cid: str, pid: str, row: dict) -> None:
    body = {k: v for k, v in row.items() if k not in ("month_index", "scenario")}
    with _db() as c:
        c.execute("insert or replace into demo_months values (?,?,?,?,?,?)",
                  (cid, pid, row["month_index"], row["scenario"], json.dumps(body), store._now()))


def reset(cid: str, pid: str) -> None:
    with _db() as c:
        c.execute("delete from demo_months where customer_id=? and proposal_id=?", (cid, pid))


def approved_proposal(cid: str) -> dict | None:
    """The newest approved/executed plan v2: the one the dashboard tracks."""
    for p in store.list_proposals(cid):  # newest first
        if p["status"] in ("approved", "executed") and (p.get("plan") or {}).get("version") == 2:
            return p
    return None


# ---------------- simulation from an actual state ----------------

def _state(m: dict) -> dict:
    b = m["balances"]
    return {"debts": dict(b["debts"]), "assets": dict(b["assets"])}


def _ctx_at(ctx: dict, state: dict, mods: dict) -> dict:
    c = copy.deepcopy(ctx)
    for d in c["debts"]:
        d["balance"] = float(state["debts"].get(d["account_id"], 0.0))
    a = state["assets"]
    c["assets"].update(cash=a["cash"], cd=a.get("cd", 0.0), brokerage=a.get("brokerage", 0.0),
                       ira=a.get("ira", 0.0), property=a.get("property", 0.0))
    c["flows"] = dict(c["flows"], residual=c["flows"]["residual"] - mods.get("residual_cut", 0.0))
    c["card_spend"] = max(c["card_spend"] + mods.get("card_extra", 0.0), 0.0)
    return c


def _plan_at(plan: dict, ctx: dict, mods: dict) -> dict:
    p = copy.deepcopy(plan)
    p["lump_sums"] = []  # month-0 lump sums already happened
    mins = sum(d["min_payment"] for d in ctx["debts"])
    p["monthly_debt_budget"] = max(float(p["monthly_debt_budget"]) - mods.get("budget_cut", 0.0), mins)
    return p


def _run_from(ctx: dict, plan: dict, state: dict, mods: dict) -> dict:
    c = _ctx_at(ctx, state, mods)
    # Same cash floor as wealth.simulate_plan: the buffer, or where current behaviour already leaves cash.
    base = wealth._run(c, None)
    return wealth._run(c, _plan_at(plan, c, mods), base_cash=[m["cash"] for m in base["months"]])


def _totals(m: dict, ctx: dict) -> dict:
    high = {d["account_id"] for d in ctx["debts"] if d["high_cost"]}
    debts, assets = m["balances"]["debts"], m["balances"]["assets"]
    total = sum(debts.values())
    m["total_debt"] = round(total, 2)
    m["high_cost_debt"] = round(sum(v for k, v in debts.items() if k in high), 2)
    m["cash"] = round(assets["cash"], 2)
    m["invested"] = round(sum(assets.get(k, 0.0) for k in ("cd", "brokerage", "ira")), 2)
    m["net_worth"] = round(sum(assets.values()) - total, 2)
    return m


def _persistent(hist: list[dict]) -> dict:
    """Behaviour the projection assumes continues: an income drop, and the average overspend so far."""
    mods = {"residual_cut": 0.0, "budget_cut": 0.0, "card_extra": 0.0, "income_drop": 0.0}
    drops = [h["events"]["income_drop"] for h in hist if h["events"].get("income_drop")]
    if drops:
        d = drops[-1]
        mods.update(residual_cut=d["amount"], budget_cut=d["amount"], income_drop=d["pct"])
    if hist:
        card = sum(h["events"].get("card_over", 0.0) for h in hist) / len(hist)
        cash = sum(h["events"].get("cash_over", 0.0) for h in hist) / len(hist)
        mods["card_extra"] = max(card, 0.0)
        mods["residual_cut"] += max(cash, 0.0)
        mods["overspend_rate"] = round(max(card, 0.0) + max(cash, 0.0), 2)
    return mods


# ---------------- demo: one month of actuals ----------------

def generate_month(cid: str, pid: str, plan: dict, ctx: dict, contract_months: list[dict], hist: list[dict],
                   scenario: str) -> dict:
    """Next month's actuals, seeded by (customer, proposal, month, scenario): same inputs, same month."""
    if scenario not in SCENARIOS:
        raise ValueError(f"unknown scenario {scenario!r}")
    k = len(hist)
    if k >= len(contract_months) - 1:
        raise ValueError("already at the end of the plan horizon")
    rng = _rng(cid, pid, k + 1, scenario)
    prev = hist[-1] if hist else contract_months[0]
    state = _state(prev)
    events: dict = {}

    # Spending in the plan's target categories: near target, or well over it when overspending.
    spend, card_over, cash_over = {}, 0.0, 0.0
    for t in plan.get("spending_targets") or []:
        cat, target = t["category"], float(t["target"])
        c = ctx["spend"].get(cat) or {"avg": target, "card_avg": target, "cash_avg": 0.0}
        if scenario == "overspend" and cat in ("dining", "shopping"):
            delta = target * rng.uniform(0.22, 0.40)
        else:
            delta = max(min(rng.gauss(0, 0.02 * target), 0.04 * target), -0.04 * target)
        delta = round(delta, 2)
        spend[cat] = {"actual": round(target + delta, 2), "target": target, "proof_month": t.get("proof_month")}
        card_share = c["card_avg"] / c["avg"] if c["avg"] else 1.0
        card_over += delta * card_share
        cash_over += delta * (1 - card_share)
    events["card_over"], events["cash_over"] = round(card_over, 2), round(cash_over, 2)

    mods = _persistent(hist)
    if scenario == "income_drop" and not mods["income_drop"]:
        pct = round(rng.uniform(0.13, 0.17), 3)
        amt = round(ctx["flows"]["income"] * pct, 2)
        events["income_drop"] = {"pct": pct, "amount": amt}
        mods.update(residual_cut=amt, budget_cut=amt, income_drop=pct)
    month_mods = {"residual_cut": mods["budget_cut"] + cash_over, "budget_cut": mods["budget_cut"],
                  "card_extra": card_over}
    run = _run_from(ctx, plan, state, month_mods)
    m = run["months"][1]
    m = {"month": wealth._label(k + 1), "balances": copy.deepcopy(m["balances"])}
    debts = m["balances"]["debts"]
    by_id = {d["account_id"]: d for d in ctx["debts"]}

    if scenario == "missed_min":
        due = [d for d in ctx["debts"] if state["debts"].get(d["account_id"], 0) > 0.005]
        due.sort(key=lambda d: (-d["high_cost"], -d["apr_pct"]))
        if due:
            d = rng.choice(due[:2])
            pay = min(d["min_payment"], state["debts"][d["account_id"]])
            fee = LATE_FEE if d["is_card"] else 0.0
            debts[d["account_id"]] = round(debts[d["account_id"]] + pay + fee, 2)
            m["balances"]["assets"]["cash"] = round(m["balances"]["assets"]["cash"] + pay, 2)
            events["missed_min"] = {"account_id": d["account_id"], "name": d["name"], "amount": round(pay, 2), "fee": fee}
    if scenario == "windfall":
        amt = round(rng.uniform(1500, 3000) / 50) * 50.0
        left, paid = amt, []
        for aid in plan.get("extra_order") or []:
            if left <= 0 or debts.get(aid, 0) <= 0.005:
                continue
            x = min(left, debts[aid])
            debts[aid] = round(debts[aid] - x, 2)
            left -= x
            paid.append({"account_id": aid, "name": by_id[aid]["name"], "amount": round(x, 2)})
        events["windfall"] = {"amount": amt, "paid": paid, "kept": round(left, 2)}
        m["balances"]["assets"]["cash"] = round(m["balances"]["assets"]["cash"] + left, 2)

    m = _totals(m, ctx)
    return {"month_index": k + 1, "scenario": scenario, **m, "spend": spend, "events": events}


# ---------------- projection + status ----------------

def project(plan: dict, ctx: dict, contract_months: list[dict], hist: list[dict], *, extra: dict | None = None,
            sim_plan: dict | None = None) -> dict:
    """Re-simulate the rest of the horizon from the latest actual month (recent behaviour persists)."""
    k = len(hist)
    cur = hist[-1] if hist else contract_months[0]
    mods = _persistent(hist)
    if extra:
        mods = {**mods, **extra}
    run = _run_from(ctx, sim_plan or plan, _state(cur), mods)
    H = len(contract_months) - 1
    months = []
    for j, m in enumerate(run["months"][: H - k + 1]):
        months.append({"month": wealth._label(k + j), "total_debt": m["total_debt"], "high_cost_debt": m["high_cost_debt"],
                       "cash": m["cash"], "net_worth": m["net_worth"], "debts": m["balances"]["debts"]})
    actual_all = [contract_months[0]] + hist

    def first_zero(key):  # first month the actual path hit 0 and stayed there
        hit = None
        for i, m in enumerate(actual_all):
            v = key(m)
            hit = (i if hit is None else hit) if v <= 0.01 else None
        return hit

    if cur["high_cost_debt"] <= 0.01:
        free = first_zero(lambda m: m["high_cost_debt"])
    else:
        rel = _idx(run["high_cost_debt_free"])
        free = None if rel is None else k + rel
    payoff = {}
    for d in run["debts"]:
        aid = d["account_id"]
        if cur["balances"]["debts"].get(aid, 0) <= 0.005:
            payoff[aid] = first_zero(lambda m, a=aid: m["balances"]["debts"].get(a, 0.0))
        else:
            rel = _idx(d["payoff_month"])
            payoff[aid] = None if rel is None else k + rel
    return {"months": months, "high_cost_free": free, "payoff": payoff, "feasible": run["feasible"],
            "problems": run["problems"], "net_worth_24m": months[-1]["net_worth"] if months else cur["net_worth"],
            "mods": mods}


def status(plan: dict, ctx: dict, contract: dict, hist: list[dict], proj: dict) -> dict:
    """on_track | drifting | off_track, with the reasons behind it (see module docstring for thresholds)."""
    months = contract["months"]
    goal = _idx(contract["high_cost_debt_free"])
    k = len(hist)
    cur = hist[-1] if hist else months[0]
    exp = months[k]
    reasons, nudges, breaks = [], [], []
    late = None
    if goal is not None:
        late = 99 if proj["high_cost_free"] is None else proj["high_cost_free"] - goal

    by_id = {d["account_id"]: d for d in ctx["debts"]}
    over = []
    for aid, v in cur["balances"]["debts"].items():
        e = exp["balances"]["debts"].get(aid, 0.0)
        if v > e * (1 + TOL) and v - e > TOL_FLOOR:
            over.append({"account_id": aid, "name": by_id[aid]["name"], "actual": v, "expected": e,
                         "pct": round((v - e) / e * 100) if e > 0.005 else None})
    for o in over:
        pct = f" ({o['pct']}%)" if o["pct"] is not None else ""
        nudges.append(f"{o['name']} is {_usd(o['actual'] - o['expected'])}{pct} above plan")

    ev = cur.get("events") or {}
    for cat, s in (cur.get("spend") or {}).items():
        if s["actual"] > s["target"] + max(10.0, 0.05 * s["target"]):
            when = MON[int(s["proof_month"][5:7]) - 1] if s.get("proof_month") else None
            tail = f"; you hit it in {when}" if when else ""
            nudges.insert(0, f"{cat.replace('_', ' ').capitalize()} {_usd(s['actual'] - s['target'])} over target{tail}")

    if ev.get("missed_min"):
        x = ev["missed_min"]
        fee = f" + {_usd(x['fee'])} late fee" if x["fee"] else ""
        breaks.append(f"Missed the {x['name']} minimum ({_usd(x['amount'])}){fee}")
    if proj["mods"].get("income_drop", 0) > HARD_INCOME_DROP:
        breaks.append(f"Income down {proj['mods']['income_drop'] * 100:.0f}% "
                      f"({_usd(proj['mods']['budget_cut'])}/mo less to debt)")
    prev = hist[-2] if len(hist) >= 2 else months[0]
    for aid, v in cur["balances"]["debts"].items():
        if v > 50 and prev["balances"]["debts"].get(aid, 0.0) <= 0.005:
            breaks.append(f"New balance on {by_id[aid]['name']}: {_usd(v)}")

    goal_reached = goal is not None and cur["high_cost_debt"] <= 0.01
    if late is not None and late >= 1 and not goal_reached:
        word = "month" if late == 1 else "months"
        reasons.append(f"High-cost debt-free now {_ym(wealth._label(proj['high_cost_free'])) if late < 99 else 'beyond the plan'}"
                       f", {late if late < 99 else '24+'} {word} after your {_ym(contract['high_cost_debt_free'])} goal")

    if breaks or (late is not None and late >= 2):
        level = "off_track"
    elif (late is not None and late == 1) or over:
        level = "drifting"
    else:
        level = "on_track"
    if level == "drifting" and late == 1 and not any("High-cost" in n for n in nudges):
        nudges.append(reasons[0])
    return {"level": level, "late_months": late, "goal_month": contract["high_cost_debt_free"],
            "projected_month": wealth._label(proj["high_cost_free"]) if proj["high_cost_free"] is not None else None,
            "goal_reached": goal_reached, "reasons": reasons, "breaks": breaks, "nudges": nudges, "over": over}


# ---------------- re-plan options (server-computed, no LLM) ----------------

def _sources(plan: dict, ctx: dict, mods: dict) -> list[dict]:
    """Where lock-in money can come from, in the order it's used."""
    out = []
    rate = mods.get("card_extra", 0.0)
    if rate >= 1:
        cuts = wealth.cut_split(ctx, plan.get("spending_targets") or [])
        cap = max(ctx["card_spend"] - cuts["card"], 0.0)
        out.append({"kind": "card_cap", "label": f"Cap PNC card spending at {_usd(cap)}/mo",
                    "detail": f"stops the {_usd(rate)}/mo over plan", "amount": round(rate, 2), "cap": round(cap, 2)})
    targets = {t["category"]: t for t in plan.get("spending_targets") or []}
    for cat, c in sorted(ctx["spend"].items(), key=lambda kv: -kv[1]["avg"]):
        if c["essential"]:
            continue
        cur = float(targets[cat]["target"]) if cat in targets else c["avg"]
        room = cur - c["min"]
        if room >= 1:
            out.append({"kind": "cut", "category": cat, "label": f"{cat.replace('_', ' ').capitalize()} {_usd(cur)} → {_usd(c['min'])}",
                        "detail": f"your {_ym(c['min_month'])} level", "amount": round(room, 2), "from": round(cur, 2),
                        "to": round(c["min"], 2), "proof_month": c["min_month"],
                        "card_share": c["card_avg"] / c["avg"] if c["avg"] else 0.0})
    have: dict[str, float] = {}
    for r in plan.get("redirects") or []:
        have[r.get("flow")] = have.get(r.get("flow"), 0.0) + float(r.get("amount_per_month") or 0)
    for key, f in ctx["movable_flows"].items():  # what each movable flow (surplus, contributions) has left
        room = f["max_per_month"] - have.get(key, 0.0)
        if room >= 1:
            out.append({"kind": "pause_flow", "flow": key, "label": f"{f['label']}: {_usd(room)}/mo more",
                        "detail": "redirect it to debt", "amount": round(room, 2)})
    return out


def _apply(plan: dict, ctx: dict, mods: dict, sources: list[dict], x: float) -> tuple[dict, dict, list[dict]]:
    """Fill `x` $/mo from sources in order -> (plan, projection mods, used sources)."""
    p = copy.deepcopy(plan)
    m = dict(mods)
    used, left = [], x
    budget_add = 0.0
    targets = {t["category"]: t for t in p.get("spending_targets") or []}
    for s in sources:
        if left <= 0.005:
            break
        take = min(left, s["amount"])
        left -= take
        used.append({**s, "amount": round(take, 2)})
        if s["kind"] == "card_cap":
            m["card_extra"] = max(m.get("card_extra", 0.0) - take, 0.0)
        elif s["kind"] == "cut":
            c = ctx["spend"][s["category"]]
            new = round(s["from"] - take, 2)
            if s["category"] in targets:
                targets[s["category"]]["target"] = new
                targets[s["category"]]["proof_month"] = s["proof_month"]
            else:
                t = {"category": s["category"], "current": round(c["avg"], 2), "target": new, "proof_month": s["proof_month"],
                     "how": [f"Hold to your {_ym(s['proof_month'])} level"]}
                p.setdefault("spending_targets", []).append(t)
                targets[s["category"]] = t
            budget_add += take * (1 - s["card_share"])
        elif s["kind"] == "pause_flow":
            rs = p.setdefault("redirects", [])
            hit = next((r for r in rs if r.get("flow") == s["flow"]), None)
            if hit:
                hit["amount_per_month"] = round(hit["amount_per_month"] + take, 2)
            else:
                rs.append({"flow": s["flow"], "amount_per_month": round(take, 2)})
            budget_add += take
    p["monthly_debt_budget"] = round(float(p["monthly_debt_budget"]) - m.get("budget_cut", 0.0) + budget_add, 2)
    m["budget_cut"] = 0.0  # folded into the plan's budget above
    return p, m, used


def _meets(proj: dict, goal: int, *, cash_ok: bool = True) -> bool:
    return proj["high_cost_free"] is not None and proj["high_cost_free"] <= goal and (proj["feasible"] or not cash_ok)


def options(plan: dict, ctx: dict, contract: dict, hist: list[dict]) -> dict:
    """Exactly two: (a) keep the date with lock-in mode, (b) move the date with the same effort."""
    goal = _idx(contract["high_cost_debt_free"])
    base = project(plan, ctx, contract["months"], hist)
    mods = base["mods"]
    srcs = _sources(plan, ctx, mods)
    pool = round(sum(s["amount"] for s in srcs), 2)

    def proj_x(x):
        p, m, used = _apply(plan, ctx, mods, srcs, x)
        return project(plan, ctx, contract["months"], hist, extra=m, sim_plan=p), p, used

    lock: dict = {"key": "lock_in", "title": f"Keep {_ym(contract['high_cost_debt_free'])}", "goal_month": contract["high_cost_debt_free"]}
    if goal is None:
        lock.update(feasible=False, why="no high-cost debt goal to keep")
    else:
        pr, p, used = proj_x(pool)
        if not _meets(pr, goal):
            # How much would it take from anywhere? (budget only, for the message)
            lo, hi = pool, pool + 20000.0
            m2 = dict(mods)
            need = None
            for _ in range(30):
                mid = (lo + hi) / 2
                q = copy.deepcopy(plan)
                q["monthly_debt_budget"] = float(plan["monthly_debt_budget"]) - mods.get("budget_cut", 0.0) + mid
                pr2 = project(plan, ctx, contract["months"], hist, extra={**m2, "budget_cut": 0.0, "card_extra": 0.0}, sim_plan=q)
                if _meets(pr2, goal, cash_ok=False):  # payoff only: what it would take from any source
                    hi, need = mid, mid
                else:
                    lo = mid
            lock.update(feasible=False, available=pool, sources=srcs,
                        needed=round(need, 2) if need is not None else None,
                        why=(f"needs about +{_usd(need)}/mo; lock-in finds {_usd(pool)}/mo" if need is not None
                             else "the date can't be kept from here"))
        else:
            # Lock-in always switches on the card cap (stop the leak), then adds only what the date needs.
            lo = next((s["amount"] for s in srcs if s["kind"] == "card_cap"), 0.0)
            hi = pool
            if _meets(proj_x(lo)[0], goal):
                hi = lo
            for _ in range(30):
                mid = (lo + hi) / 2
                if _meets(proj_x(mid)[0], goal):
                    hi = mid
                else:
                    lo = mid
            x = float(math.ceil(hi - 1e-6))  # whole dollars, rounded up
            x = min(max(x, 0.0), pool) if pool else 0.0
            pr, p, used = proj_x(x)
            if not _meets(pr, goal):
                pr, p, used = proj_x(pool)
                x = pool
            lock.update(feasible=True, extra_per_month=round(x, 2), sources=used, plan=p,
                        projected_month=wealth._label(pr["high_cost_free"]), net_worth_24m=pr["net_worth_24m"],
                        card_cap=next((s["cap"] for s in used if s["kind"] == "card_cap"), None))
    missed = [h["events"]["missed_min"] for h in hist[-1:] if h["events"].get("missed_min")]
    lock["steps"] = [f"Autopay the {m['name']} minimum ({_usd(m['amount'])})" for m in missed]

    move: dict = {"key": "move_date", "goal_month": contract["high_cost_debt_free"]}
    p = copy.deepcopy(plan)
    p["monthly_debt_budget"] = round(max(float(plan["monthly_debt_budget"]) - mods.get("budget_cut", 0.0),
                                         sum(d["min_payment"] for d in ctx["debts"])), 2)
    if base["high_cost_free"] is None or not base["feasible"]:
        move.update(title="Move the date", feasible=False,
                    why="; ".join(base["problems"]) or "high-cost debt isn't paid off within the plan horizon")
    else:
        new = wealth._label(base["high_cost_free"])
        later = base["high_cost_free"] - goal if goal is not None else 0
        move.update(title=f"Move to {_ym(new)}", feasible=True, new_month=new, months_later=later,
                    monthly_debt_budget=p["monthly_debt_budget"], plan=p, net_worth_24m=base["net_worth_24m"])
    return {"options": [lock, move], "replan": not (lock.get("feasible") or move.get("feasible"))}


def choose(cid: str, prop: dict, key: str, *, live: bool = True) -> dict:
    """Save the picked option as a NEW PENDING proposal. Never approves or executes anything."""
    from ..agent.validator import validate_plan_v2
    from ..data import balances

    plan = prop["plan"]
    ctx = _context(cid, plan.get("keep_emergency_months"), live)
    contract = wealth.simulate_plan(cid, plan, ctx=ctx)
    hist = history(cid, prop["id"])
    opts = {o["key"]: o for o in options(plan, ctx, contract, hist)["options"]}
    o = opts.get(key)
    if not o:
        raise ValueError(f"unknown option {key!r}")
    if not o.get("feasible"):
        raise ValueError(f"option not feasible: {o.get('why')}")
    new = copy.deepcopy(o["plan"])
    new["version"] = 2
    sim = dict(validate_plan_v2(cid, new, live=live))  # raises PlanRejected with reasons
    if key == "lock_in":
        head = f"Keep {_ym(o['goal_month'])}: +{_usd(o['extra_per_month'])}/mo lock-in"
    else:
        head = f"Move high-cost debt-free to {_ym(o['new_month'])}"
    sim["explain"] = {"headline": head}
    sim["tracking"] = {"from_proposal": prop["id"], "option": key, "demo_month": len(hist),
                       "goal_month": o.get("new_month") or o["goal_month"],
                       "sources": o.get("sources"), "card_cap": o.get("card_cap"), "simulated": True}
    sim["balances_snapshot"] = balances.snapshot(cid)
    return store.create_proposal(cid, new, sim, f"Re-plan from tracking (demo month {len(hist)}): {head}")


# ---------------- the dashboard payload ----------------

def dashboard(cid: str, prop: dict, *, live: bool = True, hist: list[dict] | None = None) -> dict:
    plan = prop["plan"]
    ctx = _context(cid, plan.get("keep_emergency_months"), live)
    contract = wealth.simulate_plan(cid, plan, ctx=ctx)
    hist = history(cid, prop["id"]) if hist is None else hist
    return build(cid, prop, plan, ctx, contract, hist)


def build(cid: str, prop: dict, plan: dict, ctx: dict, contract: dict, hist: list[dict]) -> dict:
    months = contract["months"]
    k = len(hist)
    proj = project(plan, ctx, months, hist)
    st = status(plan, ctx, contract, hist, proj)
    actual = [months[0]] + hist
    by_id = {d["account_id"]: d for d in ctx["debts"]}

    path = []
    for i, m in enumerate(months):
        row = {"month": m["month"], "expected": m["high_cost_debt"], "band": round(m["high_cost_debt"] * (1 + TOL), 2),
               "expected_total": m["total_debt"]}
        if i <= k:
            row["actual"] = actual[i]["high_cost_debt"]
            row["actual_total"] = actual[i]["total_debt"]
        if i >= k and i - k < len(proj["months"]):
            row["projected"] = proj["months"][i - k]["high_cost_debt"]
        path.append(row)

    cur, exp = actual[k], months[k]
    debts = []
    for d in contract["debts"]:
        aid = d["account_id"]
        goal_i = _idx(d["payoff_month"])
        p_i = proj["payoff"].get(aid)
        debts.append({"account_id": aid, "name": d["name"], "apr_pct": d["apr_pct"], "high_cost": d["high_cost"],
                      "start": d["balance"], "actual": cur["balances"]["debts"].get(aid, 0.0),
                      "expected": exp["balances"]["debts"].get(aid, 0.0),
                      "goal_month": d["payoff_month"], "projected_month": wealth._label(p_i) if p_i is not None else None,
                      "done": cur["balances"]["debts"].get(aid, 0.0) <= 0.005,
                      "late_months": (None if goal_i is None else (99 if p_i is None else p_i - goal_i))})

    alerts = []
    for i, h in enumerate(hist):
        sub = build_alert(plan, ctx, contract, hist[: i + 1])
        alerts.append(sub)
    alerts.reverse()

    out = {
        "proposal": {"id": prop["id"], "status": prop["status"], "decided_at": prop.get("decided_at"),
                     "created_at": prop["created_at"], "headline": (prop.get("simulation") or {}).get("explain", {}).get("headline")},
        "month_index": k, "horizon": len(months) - 1, "now": cur["month"],
        "goal": {"label": "High-cost debt $0", "month": contract["high_cost_debt_free"],
                 "net_worth_24m": contract["net_worth_24m"], "projected_net_worth_24m": proj["net_worth_24m"]},
        "status": st,
        "path": path,
        "debts": debts,
        "tiles": {"net_worth": cur["net_worth"], "net_worth_expected": exp["net_worth"],
                  "cash": cur["cash"], "cash_expected": exp["cash"], "total_debt": cur["total_debt"],
                  "total_debt_expected": exp["total_debt"]},
        "alerts": alerts,
        "scenarios": [{"key": k2, "label": v} for k2, v in SCENARIOS.items()],
        "history": [{"month_index": h["month_index"], "month": h["month"], "scenario": h["scenario"]} for h in hist],
        "tolerance_pct": TOL * 100,
    }
    if st["level"] == "off_track" and not st["goal_reached"]:
        o = options(plan, ctx, contract, hist)
        for x in o["options"]:
            x.pop("plan", None)
        out["switch"] = o
    return out


def build_alert(plan: dict, ctx: dict, contract: dict, hist: list[dict]) -> dict:
    """The feed entry for the latest month in `hist`."""
    proj = project(plan, ctx, contract["months"], hist)
    st = status(plan, ctx, contract, hist, proj)
    h = hist[-1]
    ev = h["events"]
    if st["breaks"]:
        title, text = "Off track", "; ".join(st["breaks"])
    elif st["level"] == "off_track":
        title, text = "Off track", "; ".join(st["reasons"])
    elif st["level"] == "drifting":
        title, text = "Drifting", st["nudges"][0] if st["nudges"] else "; ".join(st["reasons"])
    elif ev.get("windfall"):
        w = ev["windfall"]
        to = ", ".join(p["name"] for p in w["paid"]) or "cash"
        title = "Windfall"
        text = f"{_usd(w['amount'])} went to {to}; high-cost debt-free {_ym(st['projected_month'])}"
    elif st["goal_reached"]:
        title, text = "Goal reached", f"High-cost debt is $0 ({_ym(h['month'])})"
    else:
        exp = contract["months"][len(hist)]
        title, text = "On plan", f"Debt {_usd(h['total_debt'])} vs plan {_usd(exp['total_debt'])}"
    return {"month_index": h["month_index"], "month": h["month"], "scenario": h["scenario"], "level": st["level"],
            "title": title, "text": text}


def step(cid: str, prop: dict, scenario: str, *, live: bool = True) -> dict:
    plan = prop["plan"]
    ctx = _context(cid, plan.get("keep_emergency_months"), live)
    contract = wealth.simulate_plan(cid, plan, ctx=ctx)
    hist = history(cid, prop["id"])
    row = generate_month(cid, prop["id"], plan, ctx, contract["months"], hist, scenario)
    _save(cid, prop["id"], row)
    return build(cid, prop, plan, ctx, contract, hist + [row])
