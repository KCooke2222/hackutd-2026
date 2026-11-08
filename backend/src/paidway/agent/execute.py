"""Approve a plan v2: the ONLY path from a proposal to a money-moving call.

Called from the approve route only (never from graph.py or tools.py; test_safety checks that).
1. Atomically claim pending -> executing (double clicks get 409, never a second transfer).
2. Merge edits, re-validate with validate_plan_v2 (including the customer's own constraints, PR 4).
3. Re-read balances; if they moved since the proposal, mark it stale.
4. What runs in Nessie (docs/agent-v3/actions.md): every account has a Nessie mirror (data/seed.py), so each
   lump sum is a transfer from the source mirror to the debt mirror (plus the modelled tax of a sale), and each
   monthly payment and autopay-in-full is a recurring bill on checking. Pausing contributions is the
   customer's ("manual"); spending cuts are recorded and tracked by the check-in.
5. Read the touched balances before and after, and record expected vs actual change.
"""

from __future__ import annotations

from ..config import NESSIE_KEY
from ..data import balances, categories, store
from ..planning import balance_sheet, finance

EDITABLE = ("monthly_debt_budget", "extra_order", "lump_sums", "redirects", "spending_targets",
            "autopay_full_cards", "keep_emergency_months")


class ApproveError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


def _debts(customer_id: str, live: bool = True) -> dict[str, dict]:
    return {d["account_id"]: d for d in balance_sheet.balance_sheet(customer_id, live=live)["priority"]["debts"]}


# Every account has a Nessie mirror (data/seed.py), so every money move is a Nessie call: a lump sum is a transfer
# from the source account's mirror to the debt's mirror, every monthly payment and autopay is a recurring bill
# on checking. Selling an asset (brokerage, CD) leaves its mirror as the transfer plus the modelled tax/penalty.
SOURCE_TYPES = {"cash": ("savings",), "brokerage": ("brokerage",), "cd": ("cd",), "ira": ("ira",)}
SALE_NOTE = {"brokerage": "the sale's capital-gains tax is modelled by the solver and leaves the mirror too",
             "cd": "the early-withdrawal penalty is modelled by the solver and leaves the mirror too",
             "ira": "the early-withdrawal penalty and income tax are modelled and leave the mirror too"}
VERB = {"cash": "Pay", "brokerage": "Sell", "cd": "Break CD", "ira": "Withdraw IRA"}
MANUAL = {"pause": ("Lower or pause the contribution in your employer's benefits portal or at your broker.", None)}


def _ids(customer_id: str) -> dict:
    from ..data.seed import nessie_ids  # the id map only; seed's write import is lazy

    return nessie_ids(customer_id)


def mirrors(customer_id: str, rows: list[dict], ids: dict | None = None) -> dict[str, dict]:
    """account_id -> {id, nickname} of its Nessie mirror. Unseeded accounts get a placeholder id "<account_id>"
    (shown in the dry-run; approve sends nothing while any call has one)."""
    from ..data.seed import PRIMARY, mirror_nickname

    ids = ids if ids is not None else _ids(customer_id)
    have = ids.get("accounts") or {}
    out = {}
    for r in rows:
        m = have.get(r["account_id"])
        key = PRIMARY.get(r["account_id"].replace(customer_id, "{cid}"))
        nid = (m or {}).get("id") or (ids.get(key) if key else None)
        out[r["account_id"]] = {"id": nid or f"<{r['account_id']}>", "name": r["name"],
                                "nickname": (m or {}).get("nickname") or mirror_nickname(customer_id, r)}
    return out


def _source_account(rows: list[dict], source: str) -> dict | None:
    """The asset a lump sum leaves: the savings account for cash, else the largest account of that type."""
    accs = [r for r in rows if r["side"] == "asset" and r["account_type"] in SOURCE_TYPES.get(source, ())]
    return max(accs, key=lambda r: float(r["balance"] or 0), default=None)


def _gross(source: str, net: float, acc: dict) -> float:
    from ..planning import wealth

    bal = float(acc.get("balance") or 0)
    basis = acc.get("cost_basis")
    gain_frac = max(bal - float(basis), 0.0) / bal if bal and basis is not None else 0.0
    return round(wealth._gross_needed(source, net, {"assets": {"brokerage_gain_frac": gain_frac}}), 2)


def _next_month(day_from: str | None = None) -> tuple[str, int]:
    """(first payment date, day of month) for a recurring bill: one month from today (or the month after
    `day_from` = "YYYY-MM"), day clamped to 28 so it exists every month."""
    from datetime import date

    t = date.fromisoformat(finance.today())
    day = min(t.day, 28)
    if day_from:
        y, m = (int(x) for x in day_from.split("-"))
    else:
        y, m = t.year, t.month
    y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return date(y, m, day).isoformat(), day


def _mirror_note(row: dict, nick: str) -> str | None:
    if row.get("source") == "linked" or row.get("institution") not in (None, "PNC"):
        return f"Linked account ({row.get('institution') or 'other bank'}) mirrored in Nessie as \"{nick}\"; APR and terms stay in Paidway's data."
    return None


def schedule(customer_id: str, plan: dict, sim: dict | None = None, *, live: bool = True,
             ids: dict | None = None) -> tuple[list[dict], list[dict]]:
    """Split a plan into (transfers, steps). Pure: no I/O beyond reading balances and the Nessie id map.

    Every step has `runs`: "nessie" (a real Nessie call on approve, in step["nessie"]), "recorded" (the app
    tracks it) or "manual" (the customer does it; `how`, maybe `link`). `transfers` keeps its old shape
    ({from, to, to_debt, amount}; to = "credit_card" for the PNC card, else the debt's account id), one per lump
    sum. `sim` (the validated simulation) sizes the recurring bills; without it the monthly payments are
    recorded only."""
    rows = balance_sheet.accounts(customer_id, live=live)
    by_id = {r["account_id"]: r for r in rows}
    mir = mirrors(customer_id, rows, ids)
    debts = _debts(customer_id, live=live)
    sim_debts = {d["account_id"]: d for d in (sim or {}).get("debts") or []}
    checking = next((r for r in rows if r["account_id"] == f"A-{customer_id}-CHECKING"), None) \
        or next((r for r in rows if r["account_type"] == "checking"), None)
    transfers, steps = [], []
    for ls in plan.get("lump_sums") or []:
        d = debts.get(ls["to_debt"]) or by_id.get(ls["to_debt"]) or {}
        name = d.get("name", ls["to_debt"])
        amt = round(float(ls["amount"]), 2)
        src = _source_account(rows, ls["source"])
        if not src or ls["to_debt"] not in mir:
            steps.append({"kind": "lump_sum", "step": f"Pay ${amt:,.2f} to {name}", "runs": "recorded", **ls})
            continue
        gross = _gross(ls["source"], amt, src) if ls["source"] != "cash" else amt
        to_m, from_m = mir[ls["to_debt"]], mir[src["account_id"]]
        text = (f"Pay ${amt:,.2f} from savings to {name}" if ls["source"] == "cash" else
                f"{VERB.get(ls['source'], 'Use')} ${gross:,.2f} from {src['name']}: pay ${amt:,.2f} to {name}")
        notes = [n for n in (_mirror_note(src, from_m["nickname"]), _mirror_note(d, to_m["nickname"]),
                             SALE_NOTE.get(ls["source"]) if gross - amt >= 0.01 else None) if n]
        is_pnc_card = ls["to_debt"] == f"A-{customer_id}-CREDIT_CARD"
        transfers.append({"from": "savings" if ls["source"] == "cash" else ls["source"],
                          "to": "credit_card" if is_pnc_card else ls["to_debt"], "to_debt": ls["to_debt"],
                          "from_account": src["account_id"], "amount": amt})
        nessie = {"op": "transfer", "from": src["account_id"], "to": ls["to_debt"], "amount": amt,
                  "local": [(ls["to_debt"], amt), (src["account_id"], gross)]}
        if gross - amt >= 0.01:
            nessie["tax"] = round(gross - amt, 2)
        steps.append({"kind": "lump_sum", "step": text, "runs": "nessie", "debt_name": name, "gross": gross,
                      "note": " ".join(notes) or None, "nessie": nessie, **ls})
    steps.append({"kind": "budget", "step": f"Pay ${plan['monthly_debt_budget']:,.2f}/mo across all debts, extra in order",
                  "runs": "recorded",
                  "extra_order": [debts.get(a, {}).get("name", a) for a in plan.get("extra_order") or []]})
    for k, sd in sim_debts.items():
        amt = round(float(sd.get("first_month_payment") or 0), 2)
        d = debts.get(k) or by_id.get(k)
        if amt < 1 or not d:
            continue
        name = d.get("name", k)
        if checking and k in mir:
            first, day = _next_month()
            steps.append({"kind": "payment", "step": f"Pay {name} ${amt:,.2f}/mo from checking", "runs": "nessie",
                          "account_id": k, "amount": amt, "note": _mirror_note(d, mir[k]["nickname"]),
                          "nessie": {"op": "bill", "from": checking["account_id"], "to": k, "payee": name,
                                     "nickname": f"Paidway plan: {name}", "payment_date": first,
                                     "recurring_date": day, "amount": amt}})
        else:
            steps.append({"kind": "payment", "step": f"Pay {name} ${amt:,.2f}/mo", "runs": "recorded",
                          "account_id": k, "amount": amt})
    for r in plan.get("redirects") or []:
        meta = categories.flow_flag(r["flow"]) or {"flag": "redirect", "label": r["flow"]}
        paused = meta["flag"] == categories.PAUSE
        verb = "Pause" if paused else "Redirect"
        step = {"kind": "redirect", "step": f"{verb} ${float(r['amount_per_month']):,.2f}/mo of {meta['label'].lower()} "
                                            f"to debt", "runs": "manual" if paused else "recorded", **r}
        if paused:
            step["how"], step["link"] = MANUAL["pause"]
        steps.append(step)
    for t in plan.get("spending_targets") or []:
        steps.append({"kind": "spending_target", "step": f"{t['category']}: ${t['current']:,.0f} -> ${t['target']:,.0f}/mo",
                      "runs": "recorded", "category": t["category"], "target": t["target"], "how": t.get("how") or []})
    for a in plan.get("autopay_full_cards") or []:
        d = debts.get(a) or by_id.get(a) or {}
        name = d.get("name", a)
        step = {"kind": "autopay", "step": f"Autopay full statement on {name} once at $0", "account_id": a, "runs": "recorded"}
        payoff = (sim_debts.get(a) or {}).get("payoff_month")
        # New charges land on the PNC card (the simulation's card spending); other cards get no new charges.
        stmt = round(float((sim or {}).get("monthly_card_spending") or 0), 2) if a == f"A-{customer_id}-CREDIT_CARD" else 0.0
        if checking and a in mir and payoff and stmt >= 1:
            first, day = _next_month(payoff)
            step.update(runs="nessie", note=_mirror_note(d, mir[a]["nickname"]),
                        nessie={"op": "bill", "from": checking["account_id"], "to": a,
                                "payee": f"{name} (statement in full)", "nickname": f"Paidway autopay: {name}",
                                "payment_date": first, "recurring_date": day, "amount": stmt})
        elif payoff and stmt < 1:
            step["note"] = "no new charges planned on this card, so there is no statement to pay"
        steps.append(step)
    return transfers, steps


def nessie_calls(customer_id: str, transfers: list[dict], steps: list[dict], ids: dict | None = None,
                 *, live: bool = False) -> list[dict]:
    """The exact Nessie write calls approve makes, in order (pure: builds them, sends nothing): one transfer per
    lump sum (source mirror -> debt mirror), the modelled tax/penalty of an asset sale as a withdrawal from the
    source mirror, then the recurring bills on checking. Each is {tool, method, path, body, from, to, amount,
    recurring_date, step, local}; `from`/`to` are the accounts' names (`*_nickname`: their Nessie mirrors); `local` lists the linked balances
    approve lowers in our store once the call succeeds. Unseeded accounts show as <account_id>."""
    from ..nessie import calls

    rows = balance_sheet.accounts(customer_id, live=live)
    mir = mirrors(customer_id, rows, ids)
    today = finance.today()
    ops = [(s, s.get("nessie") or {}) for s in steps]
    out = []
    for s, n in ops:
        if n.get("op") == "transfer":
            f, t = mir[n["from"]], mir[n["to"]]
            out.append({"tool": "nessie.write.create_transfer",
                        **calls.transfer(f["id"], t["id"], n["amount"], today, f"Paidway approved payoff: {s['debt_name']}"),
                        "from": f["name"], "to": t["name"], "from_nickname": f["nickname"], "to_nickname": t["nickname"],
                        "amount": n["amount"], "recurring_date": None,
                        "step": s["step"], "local": n["local"],
                        "expect": {n["from"]: -n["amount"], n["to"]: -n["amount"]}})
    for s, n in ops:
        if n.get("tax"):  # the mirror keeps matching the asset's value: the modelled tax/penalty leaves it too
            f = mir[n["from"]]
            out.append({"tool": "nessie.write.create_withdrawal",
                        **calls.withdrawal(f["id"], n["tax"], today, f"Estimated {s['source']} sale cost (modelled)"),
                        "from": f["name"], "to": "Estimated tax / penalty (modelled)", "from_nickname": f["nickname"],
                        "to_nickname": None, "amount": n["tax"],
                        "recurring_date": None, "step": s["step"], "local": [], "expect": {n["from"]: -n["tax"]}})
    for s, n in ops:
        if n.get("op") == "bill":
            f = mir[n["from"]]
            out.append({"tool": "nessie.write.create_bill",
                        **calls.bill(f["id"], n["payee"], n["nickname"], n["payment_date"], n["recurring_date"], n["amount"]),
                        "from": f["name"], "to": mir[n["to"]]["name"], "from_nickname": f["nickname"],
                        "to_nickname": mir[n["to"]]["nickname"], "amount": n["amount"],
                        "recurring_date": n["recurring_date"], "first_payment": n["payment_date"], "step": s["step"],
                        "local": [], "expect": {}})
    return out


def _unseeded(call: dict) -> bool:
    return "<" in call["path"] or "<" in str(call.get("body"))


def _bill_sim(customer_id: str, plan: dict, constraints: dict | None) -> dict | None:
    """The simulation that sizes the recurring bills, on local balances so the preview and approve agree."""
    from .validator import PlanRejected, validate_plan_v2

    try:
        return validate_plan_v2(customer_id, plan, live=False, constraints=constraints)
    except (PlanRejected, ValueError, KeyError, TypeError):
        return None


def _created_id(res) -> str | None:
    return ((res or {}).get("objectCreated") or {}).get("_id") if isinstance(res, dict) else None


def _send_all(planned: list[dict]) -> list[dict]:
    """Send each planned call; returns the receipt [{tool, method, path, body, status, id, ...}]. Stops at the
    first failure (its row has status "error" and the message); later calls are marked "not_sent"."""
    # The only money-moving import in the app (lazy, approve path only; test_safety allows it here alone).
    from ..nessie import write

    receipt, failed = [], False
    for c in planned:
        row = {k: c.get(k) for k in ("tool", "method", "path", "body", "from", "to", "from_nickname", "to_nickname",
                                     "amount", "recurring_date")}
        if failed:
            receipt.append({**row, "status": "not_sent", "id": None})
            continue
        try:
            res = write.send(c)
        except Exception as e:  # noqa: BLE001 - recorded, then approve marks the proposal failed
            receipt.append({**row, "status": "error", "id": None, "error": str(e)})
            failed = True
            continue
        receipt.append({**row, "status": (res or {}).get("code", 201) if isinstance(res, dict) else 201, "id": _created_id(res)})
        for account_id, amount in c.get("local") or []:  # linked balances live in our store: bring them down
            store.pay_linked(account_id, amount)
    return receipt


def _read_balances(mir: dict[str, dict], account_ids: list[str]) -> dict[str, float] | dict:
    from ..nessie import read

    try:
        return {k: round(float(read.account(mir[k]["id"])["balance"]), 2) for k in account_ids}
    except Exception as e:  # noqa: BLE001 - a failed re-read must not hide what already ran
        return {"error": str(e)}


def _check(planned: list[dict], receipt: list[dict], mir: dict, before: dict, after: dict) -> list[dict]:
    """Expected vs actual balance change per touched mirror (Nessie's transfer-into-a-card direction is checked,
    not assumed). Debts: positive balance owed, so a payment should lower it, same as an asset it leaves."""
    exp: dict[str, float] = {}
    for c, r in zip(planned, receipt):
        if r["status"] not in ("error", "not_sent"):
            for k, v in (c.get("expect") or {}).items():
                exp[k] = round(exp.get(k, 0.0) + v, 2)
    if "error" in before or "error" in after:
        return []
    return [{"account_id": k, "nickname": mir[k]["nickname"], "before": before[k], "after": after[k],
             "expected_change": exp.get(k, 0.0), "actual_change": round(after[k] - before[k], 2),
             "ok": abs(after[k] - before[k] - exp.get(k, 0.0)) < 1.0}  # Nessie may keep whole dollars
            for k in before]


def preview(pid: str) -> dict:
    """What approving this proposal would do, read-only and with no Nessie call at all (not even a GET):
    the planned Nessie calls, then the recorded and manual steps."""
    p = store.get_proposal(pid)
    if not p:
        raise ApproveError(404, "no such proposal")
    if (p.get("plan") or {}).get("version") != 2:
        raise ApproveError(422, "proposal is a v1 plan; re-run the agent")
    cid, plan = p["customer_id"], dict(p["plan"])
    sim = _bill_sim(cid, plan, p.get("constraints"))
    transfers, steps = schedule(cid, plan, sim, live=False)
    calls_ = nessie_calls(cid, transfers, steps, _ids(cid))
    return {"proposal_id": pid, "customer_id": cid, "status": p["status"],
            "nessie_enabled": bool(NESSIE_KEY), "seeded": not any(_unseeded(c) for c in calls_),
            "nessie_calls": calls_,
            "recorded": [s for s in steps if s["runs"] == "recorded"],
            "manual": [s for s in steps if s["runs"] == "manual"]}


def approve(pid: str, edits: dict | None = None) -> dict:
    p = store.get_proposal(pid)
    if not p:
        raise ApproveError(404, "no such proposal")
    if (p.get("plan") or {}).get("version") != 2:
        raise ApproveError(422, "proposal is a v1 plan; re-run the agent")
    if not store.claim(pid):
        raise ApproveError(409, f"proposal is {store.get_proposal(pid)['status']}, not pending")

    cid = p["customer_id"]
    plan = dict(p["plan"])
    if edits:
        plan.update({k: v for k, v in edits.items() if k in EDITABLE and v is not None})
    from .validator import PlanRejected, validate_plan_v2

    try:
        sim = validate_plan_v2(cid, plan, constraints=p.get("constraints"))
    except PlanRejected as e:
        store.release(pid)
        raise ApproveError(422, "; ".join(getattr(e, "reasons", None) or [str(e)]))
    except (ValueError, KeyError, TypeError) as e:
        store.release(pid)
        raise ApproveError(422, str(e))

    fresh = balances.snapshot(cid)
    diffs = balances.changed(p["simulation"].get("balances_snapshot"), fresh)
    if diffs:
        store.set_status(pid, "stale", {"error": "balances changed since the proposal; re-run the check", "diffs": diffs})
        raise ApproveError(409, "stale, re-run: balances changed since the proposal (" + "; ".join(diffs) + ")")

    transfers, steps = schedule(cid, plan, _bill_sim(cid, plan, p.get("constraints")))
    record = {"plan": plan, "scheduled": steps, "simulation": {k: sim.get(k) for k in
              ("net_worth_24m", "baseline_net_worth_24m", "high_cost_debt_free", "interest_total")}}
    planned = nessie_calls(cid, transfers, steps)
    if not planned:
        return store.set_status(pid, "approved", {**record, "mode": "local_only", "note": "no step runs in Nessie",
                                                  "nessie_calls": []})
    pending = {"transfers_pending": transfers, "nessie_pending": planned}
    if not NESSIE_KEY:
        return store.set_status(pid, "approved", {**record, "mode": "local_only", "note": "NESSIE_KEY not set", **pending})
    if any(_unseeded(c) for c in planned):
        return store.set_status(pid, "approved", {**record, "mode": "local_only",
                                                  "note": "customer not seeded into Nessie (run reseed)", **pending})
    mir = mirrors(cid, balance_sheet.accounts(cid, live=False))
    touched = list(dict.fromkeys(k for c in planned for k in (c.get("expect") or {})))
    before = _read_balances(mir, touched)
    try:
        receipt = _send_all(planned)
    finally:
        balance_sheet.clear_cache()
    after = _read_balances(mir, touched)
    done = {"nessie_calls": receipt, "nessie_balances_before": before, "nessie_balances_after": after,
            "nessie_check": _check(planned, receipt, mir, before, after)}
    errors = [r for r in receipt if r["status"] == "error"]
    if errors:
        return store.set_status(pid, "failed", {**record, **done, "error": errors[0]["error"]})
    total = round(sum(t["amount"] for t in transfers), 2)
    return store.set_status(pid, "executed", {**record, **done, "mode": "nessie", "amount": total,
                                              "transfer": next((r for r in receipt if r["tool"].endswith("create_transfer")), None)})
