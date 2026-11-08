"""Approve a plan v2: the ONLY path from a proposal to a money-moving call.

Called from the approve route only (never from graph.py or tools.py; test_safety checks that).
1. Atomically claim pending -> executing (double clicks get 409, never a second transfer).
2. Merge edits, re-validate with validate_plan_v2 (including the customer's own constraints, PR 4).
3. Re-read balances; if they moved since the proposal, mark it stale.
4. What runs in Nessie (docs/agent-v3/actions.md): every account has a Nessie mirror (data/seed.py), so each
   lump sum is a transfer from the source mirror to the debt mirror (plus the modelled tax of a sale), and each
   monthly payment of every remaining debt and autopay-in-full is a recurring bill on checking. Pausing
   contributions is the customer's ("manual"); spending cuts and the leftover are watched by the agent ("agent",
   the monthly check-in).
5. Read the touched balances before and after, and record expected vs actual change.
"""

from __future__ import annotations

import json

from ..config import NESSIE_KEY
from ..data import balances, categories, store
from ..nessie.calls import dollars
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
        out[r["account_id"]] = {"id": nid or f"<{r['account_id']}>", "name": r["name"], "debt": r.get("side") == "debt"
                                or r["account_type"] in balance_sheet.DEBT_TYPES,
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


def _month_name(ym: str) -> str:
    """"2026-12" -> "Dec 2026"."""
    from datetime import date

    y, m = (int(x) for x in ym.split("-"))
    return date(y, m, 1).strftime("%b %Y")


def _mirror_note(row: dict, nick: str) -> str | None:
    if row.get("source") == "linked" or row.get("institution") not in (None, "PNC"):
        return f"Linked account ({row.get('institution') or 'other bank'}) mirrored in Nessie as \"{nick}\"; APR and terms stay in Paidway's data."
    return None


def current_payments(customer_id: str, months: int = 3) -> dict[str, float]:
    """What the customer already pays each debt per month (planning/recurring.py). A plan bill equal to this
    changes nothing."""
    from ..planning import recurring

    return recurring.current_payments(customer_id, months)


def schedule(customer_id: str, plan: dict, sim: dict | None = None, *, live: bool = True,
             ids: dict | None = None) -> tuple[list[dict], list[dict]]:
    """Split a plan into (transfers, steps). Pure: no I/O beyond reading balances and the Nessie id map.

    Every step has `runs`: "nessie" (a real Nessie call on approve, in step["nessie"]), "agent" (the agent
    watches it at the monthly check-in; `watch` says what it checks), "manual" (the customer does it; `how`, maybe
    `link`), "recorded" (no simulation or no mirror: noted only) or "summary" (the month's split, not a step). `transfers` keeps its old shape
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
        if src and ls["to_debt"] in mir:
            amt = float(dollars(amt))  # Nessie moves whole dollars; the preview shows what is sent
        if not src or ls["to_debt"] not in mir:
            steps.append({"kind": "lump_sum", "step": f"Pay ${amt:,.2f} to {name}", "runs": "recorded", **ls})
            continue
        gross = float(dollars(_gross(ls["source"], amt, src))) if ls["source"] != "cash" else amt
        to_m, from_m = mir[ls["to_debt"]], mir[src["account_id"]]
        text = (f"Pay ${amt:,.0f} from savings to {name}" if ls["source"] == "cash" else
                f"{VERB.get(ls['source'], 'Use')} ${gross:,.0f} from {src['name']}: pay ${amt:,.0f} to {name}")
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
    order = [debts.get(a, {}).get("name", a) for a in plan.get("extra_order") or []]
    if not sim_debts:  # no simulation to size the bills: the budget is recorded only
        steps.append({"kind": "budget", "step": f"Pay ${plan['monthly_debt_budget']:,.2f}/mo across all debts, extra in order",
                      "runs": "recorded", "extra_order": order})
    now_pay = current_payments(customer_id) if sim_debts else {}
    first_bill, _ = _next_month()
    # One recurring bill per payee: a card that autopays in full from the first bill month needs no payment bill.
    autopay_now = {a for a in plan.get("autopay_full_cards") or []
                   if (sim_debts.get(a) or {}).get("payoff_month") and _next_month(sim_debts[a]["payoff_month"])[0] <= first_bill}
    for k, sd in sim_debts.items():
        amt = round(float(sd.get("first_month_payment") or 0), 2)
        d = debts.get(k) or by_id.get(k)
        if amt < 1 or not d or k in autopay_now:
            continue
        name = d.get("name", k)
        cur = now_pay.get(k)
        mn = float(sd.get("min_payment") or d.get("min_payment") or 0)
        if checking and k in mir:  # every remaining debt's monthly payment is a Nessie bill, "no change" ones too
            first, day = _next_month()
            amt = float(dollars(amt))
            extra = amt - mn
            what = ("" if mn < 1 else "(minimum)" if abs(extra) < 1 else
                    f"(minimum ${mn:,.0f} + ${extra:,.0f} extra)" if extra > 0 else "")
            same = cur is not None and abs(cur - amt) <= 1
            steps.append({"kind": "payment", "step": f"{name} ${amt:,.0f}/mo {what}".strip(), "runs": "nessie",
                          "account_id": k, "amount": amt, "current": cur, "minimum": mn,
                          "replaces": (f"Takes over your current ${cur:,.0f}/mo payment" if same else
                                       f"Replaces your current ${cur:,.0f}/mo payment" if cur else None),
                          "note": _mirror_note(d, mir[k]["nickname"]),
                          "nessie": {"op": "bill", "from": checking["account_id"], "to": k, "key": f"debt:{k}", "payee": name,
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
                                            f"to debt", "runs": "manual" if paused else "agent",
                **({} if paused else {"watch": "The monthly check-in checks it reached your debt bills"}), **r}
        if paused:
            step["how"], step["link"] = MANUAL["pause"]
        steps.append(step)
    for t in plan.get("spending_targets") or []:
        steps.append({"kind": "spending_target", "step": f"{t['category']}: ${t['current']:,.0f} -> ${t['target']:,.0f}/mo",
                      "runs": "agent", "watch": "The monthly check-in compares your spending to the target",
                      "category": t["category"], "target": t["target"], "how": t.get("how") or []})
    for a in plan.get("autopay_full_cards") or []:
        d = debts.get(a) or by_id.get(a) or {}
        name = d.get("name", a)
        step = {"kind": "autopay", "step": f"Autopay full statement on {name} once at $0", "account_id": a, "runs": "agent"}
        payoff = (sim_debts.get(a) or {}).get("payoff_month")
        # New charges land on the PNC card (the simulation's card spending); other cards get no new charges.
        stmt = round(float((sim or {}).get("monthly_card_spending") or 0), 2) if a == f"A-{customer_id}-CREDIT_CARD" else 0.0
        has_bill = any(x.get("kind") == "payment" and x.get("account_id") == a and x["runs"] == "nessie" for x in steps)
        if has_bill:  # one bill per payee: its payment bill pays it down; at payoff it should pay the statement instead
            step["watch"] = (f"Its bill pays it down now; when it hits $0{' (' + _month_name(payoff) + ')' if payoff else ''} the bill "
                             "should switch to the statement in full (not automated yet)")
        elif checking and a in mir and payoff and stmt >= 1:
            first, day = _next_month(payoff)
            stmt = float(dollars(stmt))
            text = (f"{name} statement in full ~${stmt:,.0f}/mo: your new purchases, not debt" if a in autopay_now
                    else f"Autopay full statement on {name} once at $0")
            step.update(step=text, runs="nessie", amount=stmt, note=_mirror_note(d, mir[a]["nickname"]),
                        nessie={"op": "bill", "from": checking["account_id"], "to": a, "key": f"debt:{a}",
                                "payee": f"{name} (statement in full)", "nickname": f"Paidway autopay: {name}",
                                "payment_date": first, "recurring_date": day, "amount": stmt})
        elif payoff and stmt < 1:  # nothing to bill: a Nessie bill is a fixed amount, and no charges are planned
            step.update(step=f"{name} stays at $0: no new charges planned, so no bill",
                        watch="The monthly check-in reads its balance; any new charge shows up there")
        else:
            step["watch"] = "Not paid off in the plan's horizon yet"
        steps.append(step)
    if sim_debts:  # where the month's budget really goes (the simulation's month 1): debt, new purchases, cash
        bud = (sim or {}).get("budget") or {}
        paid = round(sum(float(x.get("first_month_payment") or 0) for x in sim_debts.values()), 2)
        buys = round(float((sim or {}).get("monthly_card_spending") or 0), 2)
        left = round(max(float(plan.get("monthly_debt_budget") or 0) - paid, 0.0), 2)
        net = round(float(bud.get("net_paydown_per_month", paid - buys)), 2)
        if left >= 1:
            steps.append({"kind": "leftover", "step": f"${left:,.0f}/mo left over -> stays in cash", "runs": "agent",
                          "amount": left, "watch": "Budgeted but no debt needs it this month; the plan keeps it in cash, not debt"})
        steps.append({"kind": "budget", "runs": "summary", "extra_order": order,
                      "step": f"Debt shrinks ${net:,.0f}/mo", "budget": float(plan.get("monthly_debt_budget") or 0),
                      "paid": paid, "new_purchases": buys, "net_paydown": net, "left_over": left})
    base = ((ids if ids is not None else _ids(customer_id)).get("bills") or {}) if sim_debts else {}
    if base and checking:
        steps = against_baseline(customer_id, steps, base, sim, debts, checking["account_id"])
    return transfers, steps


def _resume_month(sim: dict | None) -> str | None:
    """A paused contribution restarts the month after high-cost debt reaches $0."""
    hf = (sim or {}).get("high_cost_debt_free")
    if not hf:
        return None
    y, m = (int(x) for x in hf.split("-"))
    return f"{y + 1}-01" if m == 12 else f"{y}-{m + 1:02d}"


def against_baseline(customer_id: str, steps: list[dict], base: dict, sim: dict | None, debts: dict,
                     checking_id: str) -> list[dict]:
    """Turn the plan's bills into a diff against the recurring bills already in Nessie (data/seed.py "bills"):
    a debt's bill is kept (same amount: no call), changed (PUT) or new (POST); a debt paid off today ends its
    bill (DELETE); a paused contribution deletes its bill and keeps the spec to re-create it on resume."""
    out, used = [], set()
    for s in steps:
        n = s.get("nessie") or {}
        if n.get("op") == "bill" and n.get("key") in base:
            b = base[n["key"]]
            used.add(n["key"])
            same = abs(float(b.get("amount") or 0) - float(n["amount"])) < 1
            n.update(action="keep" if same else "change", bill_id=b.get("id"), was=b.get("amount"))
            if same:
                s["replaces"] = f"Your ${n['amount']:,.0f}/mo bill in Nessie stays as it is"
            else:
                s["replaces"] = f"Changes your ${float(b.get('amount') or 0):,.0f}/mo bill in Nessie to ${n['amount']:,.0f}"
        elif n.get("op") == "bill":
            n["action"] = "new"
        if s.get("kind") == "redirect" and (categories.FLOWS.get(s["flow"]) or {}).get("dest"):  # a contribution
            dest = (categories.FLOWS.get(s["flow"]) or {}).get("dest")
            key = f"contrib:{dest}"
            b = base.get(key)
            if b:
                from ..planning.recurring import CONTRIB_NAME

                b = {**b, "name": b.get("name") or CONTRIB_NAME.get(dest, dest)}
                used.add(key)
                left = round(float(b.get("amount") or 0) - float(s["amount_per_month"]))
                resume = _resume_month(sim)
                when = f" until {_month_name(resume)}; restarts automatically" if resume else ""
                if left < 1:
                    s.update(step=f"Pause {b.get('name')} ${b.get('amount'):,.0f}/mo{when}", runs="nessie", resume=resume,
                             nessie={"op": "bill", "action": "pause", "key": key, "bill_id": b.get("id"),
                                     "from": checking_id, "to": b.get("to"), "payee": b.get("payee"),
                                     "amount": b.get("amount"), "was": b.get("amount"), "resume": resume})
                else:
                    s.update(step=f"Lower {b.get('name')} ${b.get('amount'):,.0f} -> ${left:,.0f}/mo{when}", runs="nessie",
                             resume=resume,
                             nessie={"op": "bill", "action": "change", "key": key, "bill_id": b.get("id"),
                                     "from": checking_id, "to": b.get("to"), "payee": b.get("payee"), "amount": left,
                                     "was": b.get("amount"), "resume": resume})
                s.pop("how", None)
                s.pop("link", None)
        out.append(s)
    for key, b in base.items():  # a debt bill with no payment left: the debt is paid off today
        if key in used or not key.startswith("debt:"):
            continue
        aid = key.split(":", 1)[1]
        name = (debts.get(aid) or {}).get("name") or b.get("name")
        out.insert(next((i for i, x in enumerate(out) if x["runs"] == "summary"), len(out)),
                   {"kind": "bill_end", "step": f"Stop the ${b.get('amount'):,.0f}/mo {name} bill: paid off today",
                    "runs": "nessie", "account_id": aid,
                    "nessie": {"op": "bill", "action": "end", "key": key, "bill_id": b.get("id"), "from": checking_id,
                               "to": aid, "payee": b.get("payee"), "amount": 0, "was": b.get("amount")}})
    return out


def bill_diff(customer_id: str, steps: list[dict], ids: dict | None = None) -> list[dict]:
    """The plan as a diff against "Recurring in Nessie": one row per bill, {key, name, kind, action (keep |
    change | pause | end | new), from, to, resume}."""
    ids = ids if ids is not None else _ids(customer_id)
    base = ids.get("bills") or {}
    by_key = {(s.get("nessie") or {}).get("key"): s for s in steps if (s.get("nessie") or {}).get("op") == "bill"}
    rows = []
    for k, b in base.items():
        s = by_key.get(k)
        n = (s or {}).get("nessie") or {}
        act = n.get("action") or "keep"
        rows.append({"key": k, "name": b.get("name") or b.get("payee") or n.get("payee"),
                     "kind": b.get("kind") or k.split(":", 1)[0].replace("contrib", "contribution"), "action": act,
                     "from": b.get("amount"), "to": 0 if act in ("pause", "end") else n.get("amount", b.get("amount")),
                     "resume": n.get("resume"), "step": (s or {}).get("step")})
    for k, s in by_key.items():
        if k not in base and s["nessie"].get("action") == "new":
            rows.append({"key": k, "name": s["nessie"].get("payee"), "kind": "debt", "action": "new", "from": None,
                         "to": s["nessie"]["amount"], "resume": None, "step": s["step"]})
    order = {"change": 0, "pause": 1, "end": 2, "new": 3, "keep": 4}
    rows.sort(key=lambda r: order.get(r["action"], 9))
    return rows


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
    for i, (s, n) in enumerate(ops):
        if n.get("op") == "transfer":  # this Nessie's transfer has no payee: out of the source, then a payment in
            f, t = mir[n["from"]], mir[n["to"]]
            names = {"from": f["name"], "to": t["name"], "from_nickname": f["nickname"], "to_nickname": t["nickname"],
                     "amount": n["amount"], "recurring_date": None, "step": s["step"], "group": f"payoff-{i}"}
            out.append({"tool": "nessie.write.create_transfer",
                        **calls.transfer(f["id"], n["amount"], today, f"Paidway payoff to {s['debt_name']}"),
                        **names, "leg": 1, "local": [], "expect": {n["from"]: -n["amount"]}})
            out.append({"tool": "nessie.write.create_deposit",
                        **calls.deposit(t["id"], n["amount"], today, f"Paidway payoff from {f['name']}"),
                        **names, "leg": 2, "local": n["local"], "expect": {n["to"]: -n["amount"]}})
    for i, (s, n) in enumerate(ops):
        if n.get("tax"):  # the mirror keeps matching the asset's value: the modelled tax/penalty leaves it too
            f = mir[n["from"]]
            out.append({"tool": "nessie.write.create_withdrawal",
                        **calls.withdrawal(f["id"], n["tax"], today, f"Paidway: estimated {s['source']} sale cost (modelled)"),
                        "from": f["name"], "to": "Estimated tax / penalty (modelled)", "from_nickname": f["nickname"],
                        "to_nickname": None, "amount": n["tax"],
                        "recurring_date": None, "step": s["step"], "local": [], "expect": {n["from"]: -n["tax"]},
                        "group": f"payoff-{i}"})
    for s, n in ops:
        if n.get("op") == "bill" and n.get("action") in ("change", "pause", "end"):
            f = mir[n["from"]]
            to = mir.get(n.get("to")) or {}
            call = calls.update_bill(n["bill_id"], n["amount"]) if n["action"] == "change" else calls.delete_bill(n["bill_id"])
            out.append({"tool": "nessie.write.update_bill" if n["action"] == "change" else "nessie.write.delete_bill",
                        **call, "from": f["name"], "to": to.get("name") or n.get("payee"), "from_nickname": f["nickname"],
                        "to_nickname": to.get("nickname"), "amount": n["amount"], "was": n.get("was"),
                        "recurring_date": None, "step": s["step"], "bill_key": n["key"], "bill_action": n["action"],
                        "resume": n.get("resume"),
                        "note": {"change": f"was ${float(n.get('was') or 0):,.0f}/mo",
                                 "pause": "paused; the spec is kept so it restarts on its own",
                                 "end": "paid off today"}[n["action"]],
                        "local": [], "expect": {}})
        elif n.get("op") == "bill" and n.get("action") != "keep":
            f = mir[n["from"]]
            out.append({"tool": "nessie.write.create_bill", "bill_key": n.get("key"), "bill_action": "new",
                        **calls.bill(f["id"], n["payee"], n["nickname"], n["payment_date"], n["recurring_date"], n["amount"]),
                        "from": f["name"], "to": mir[n["to"]]["name"], "from_nickname": f["nickname"],
                        "to_nickname": mir[n["to"]]["nickname"], "amount": n["amount"],
                        "recurring_date": n["recurring_date"], "first_payment": n["payment_date"], "step": s["step"],
                        "note": s.get("replaces") or ("autopay, statement in full" if s.get("kind") == "autopay" else None),
                        "local": [], "expect": {}})
    return out


def _unseeded(call: dict) -> bool:
    return "<" in call["path"] or "<" in str(call.get("body")) or "/None" in call["path"]


def _bill_sim(customer_id: str, plan: dict, constraints: dict | None) -> dict | None:
    """The simulation that sizes the recurring bills, on local balances so the preview and approve agree."""
    from .validator import PlanRejected, validate_plan_v2

    try:
        return validate_plan_v2(customer_id, plan, live=False, constraints=constraints)
    except (PlanRejected, ValueError, KeyError, TypeError):
        return None


def _created_id(res) -> str | None:
    return ((res or {}).get("objectCreated") or {}).get("_id") if isinstance(res, dict) else None


RECEIPT_KEYS = ("tool", "method", "path", "body", "from", "to", "from_nickname", "to_nickname", "amount",
                "recurring_date", "first_payment", "step", "note", "group", "leg", "local", "expect", "bill_key",
                "bill_action", "was", "resume")
MAX_REPAIRS = 2  # deterministic re-formats per call, then one model try


def _llm_fix(call: dict, body: dict, err: str) -> dict | None:
    """Last resort: one quick model call that rewrites the body from Nessie's error. Checked by repair.safe."""
    try:
        from .graph import _llm, parse_json

        llm = _llm()
        try:
            llm = llm.bind(timeout=20)
        except Exception:  # noqa: BLE001
            pass
        msg = llm.invoke(
            "A banking API rejected this JSON request body. Return ONLY the corrected JSON body, no prose. "
            "Keep every amount the same (whole dollars only), keep descriptions, drop fields it says are not "
            f"permitted, add fields it says are required, and where it lists permitted values use one of them "
            "exactly.\n\nRequest: {call['method']} {call['path']}\n"
            f"Body: {json.dumps(body)}\nError: {err[:1500]}")
        out = parse_json(getattr(msg, "content", msg))
        return out if isinstance(out, dict) else None
    except Exception:  # noqa: BLE001 - no model, or it failed: the call stays failed
        return None


def _send_one(c: dict, today: str) -> tuple[dict | None, dict, list[str], str | None]:
    """Send one call; on a 400/422 validation error re-format the body from the message and retry (up to
    MAX_REPAIRS times in code, then one model try). Returns (result, body sent last, repair notes, error)."""
    from ..nessie import repair, write
    from ..nessie._http import NessieError

    body, notes, llm_tries = dict(c.get("body") or {}), [], 0
    for _ in range(2 * MAX_REPAIRS + 1):
        try:
            return write.send({**c, "body": body}), body, notes, None
        except NessieError as e:
            err = str(e)
            if e.status not in (400, 422):
                return None, body, notes, err
            fixed, what = repair.fix(body, e.text or err, today)
            if fixed is not None and len([n for n in notes if n.startswith("reformatted")]) < MAX_REPAIRS:
                notes.append(f"reformatted after {e.status}: {', '.join(what)}")
                body = fixed
                continue
            if llm_tries >= MAX_REPAIRS:
                return None, body, notes, err
            llm_tries += 1
            cand = _llm_fix(c, body, e.text or err)
            if not cand or cand == body or not repair.safe(c.get("body") or {}, cand):
                notes.append(f"model could not fix the {e.status}")
                return None, body, notes, err
            notes.append(f"rewritten by the model after {e.status}: {', '.join(sorted(set(cand) ^ set(body))) or 'values'}")
            body = cand
        except Exception as e:  # noqa: BLE001 - network or other: recorded, approve marks the call failed
            return None, body, notes, str(e)
    return None, body, notes, "gave up after repairs"


def _send_all(planned: list[dict], *, today: str | None = None) -> list[dict]:
    """Send each planned call; returns the receipt [{tool, method, path, body, status, id, repairs, ...}].
    Calls are independent except the two legs of a payoff (same `group`): if the first leg fails, the second is
    "skipped" so a debt is never credited with money that didn't leave. Everything else is still sent."""
    # The only money-moving import in the app (lazy, approve path only; test_safety allows it here alone).
    from ..nessie import write  # noqa: F401 - imported here so the approve path is the only one that loads it

    today = today or finance.today()
    receipt, broken = [], set()
    for c in planned:
        row = {k: c.get(k) for k in RECEIPT_KEYS}
        if c.get("group") and c["group"] in broken:
            receipt.append({**row, "status": "skipped", "id": None, "error": "first leg failed, not sent"})
            continue
        res, body, notes, err = _send_one(c, today)
        row["body"], row["repairs"] = body, notes
        if err:
            receipt.append({**row, "status": "error", "id": None, "error": err})
            if c.get("group"):
                broken.add(c["group"])
            continue
        receipt.append({**row, "status": (res or {}).get("code", 201) if isinstance(res, dict) else 201, "id": _created_id(res)})
        for account_id, amount in c.get("local") or []:  # linked balances live in our store: bring them down
            store.pay_linked(account_id, amount)
    return receipt


def _ok(r: dict) -> bool:
    return r.get("status") not in ("error", "not_sent", "skipped")


def _read_balances(mir: dict[str, dict], account_ids: list[str]) -> dict[str, float] | dict:
    """Ledger balance of each mirror, read from Nessie (raw balance plus Paidway's own transactions)."""
    from concurrent.futures import ThreadPoolExecutor

    from ..nessie import read

    try:
        with ThreadPoolExecutor(8) as ex:
            vals = list(ex.map(lambda k: read.ledger_balance(mir[k]["id"], mir[k]["debt"]), account_ids))
        return {k: round(v, 2) for k, v in zip(account_ids, vals)}
    except Exception as e:  # noqa: BLE001 - a failed re-read must not hide what already ran
        return {"error": str(e)}


def _check(planned: list[dict], receipt: list[dict], mir: dict, before: dict, after: dict) -> list[dict]:
    """Expected vs actual balance change per touched mirror (Nessie's transfer-into-a-card direction is checked,
    not assumed). Debts: positive balance owed, so a payment should lower it, same as an asset it leaves."""
    exp: dict[str, float] = {}
    for c, r in zip(planned, receipt):
        if _ok(r):
            for k, v in (c.get("expect") or {}).items():
                exp[k] = round(exp.get(k, 0.0) + v, 2)
    if "error" in before or "error" in after:
        return []
    return [{"account_id": k, "name": mir[k]["name"], "nickname": mir[k]["nickname"], "nessie_id": mir[k]["id"],
             "debt": mir[k]["debt"], "before": before[k], "after": after[k],
             "expected_change": exp.get(k, 0.0), "actual_change": round(after[k] - before[k], 2),
             "ok": abs(after[k] - before[k] - exp.get(k, 0.0)) < 1.0}  # Nessie may keep whole dollars
            for k in before]


def preview(pid: str) -> dict:
    """What approving this proposal would do, read-only and with no Nessie call at all (not even a GET):
    the planned Nessie calls, then what the agent watches, the recorded and manual steps, and the month's split."""
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
            "agent": [s for s in steps if s["runs"] == "agent"],
            "recorded": [s for s in steps if s["runs"] == "recorded"],
            "manual": [s for s in steps if s["runs"] == "manual"],
            "summary": next((s for s in steps if s["runs"] == "summary"), None),
            "recurring": bill_diff(cid, steps),
            "bill_schedule": bill_schedule(sim, steps, plan.get("autopay_full_cards"))}


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

    bsim = _bill_sim(cid, plan, p.get("constraints"))
    transfers, steps = schedule(cid, plan, bsim)
    record = {"plan": plan, "scheduled": steps, "simulation": {k: sim.get(k) for k in
              ("net_worth_24m", "baseline_net_worth_24m", "high_cost_debt_free", "interest_total")},
              "recurring": bill_diff(cid, steps),
              "bill_schedule": bill_schedule(bsim, steps, plan.get("autopay_full_cards"))}
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
    shown = [k for k, m in mir.items() if not m["id"].startswith("<")]  # every mirrored account, for the Done screen
    before = _read_balances(mir, shown)
    receipt: list[dict] = []
    try:
        receipt = _send_all(planned)
    finally:
        _mark_posted(cid, receipt)
        _record_bills(cid, planned, receipt)
        balance_sheet.clear_cache()
    return store.set_status(pid, *_outcome(cid, record, planned, receipt, mir, before, shown, transfers))


def _record_bills(cid: str, planned: list[dict], receipt: list[dict]) -> None:
    """Keep the "Recurring in Nessie" map in step with the bill calls that went through."""
    from ..data.seed import record_bills

    changes = []
    for c, r in zip(planned, receipt):
        if not _ok(r) or not c.get("bill_key"):
            continue
        a = c["bill_action"]
        ch = {"key": c["bill_key"], "action": a, "amount": c["amount"], "resume": c.get("resume")}
        if a == "new":
            b = r.get("body") or c.get("body") or {}
            ch.update(id=r.get("id"), payee=b.get("payee"), nickname=b.get("nickname"), name=b.get("payee"),
                      payment_date=b.get("payment_date"), recurring_date=b.get("recurring_date"),
                      amount=b.get("payment_amount"), kind="debt", to=c["bill_key"].split(":", 1)[1])
        changes.append(ch)
    if changes:
        record_bills(cid, changes)


def bill_schedule(sim: dict | None, steps: list[dict], autopay: list[str] | None = None) -> list[dict]:
    """What the monthly check-in does to the approved bills, decided now and shown on the Done screen:
    when a debt hits $0 its bill ends (or pays the statement, for an autopay card) and the money moves to the
    next debt's bill (sized by the simulation's own monthly payments); a paused contribution is created again
    in its resume month. Each entry: {month, key, action (change | end | new | resume), amount, why, done}."""
    months = (sim or {}).get("months") or []
    active: dict[str, float] = {}
    meta: dict[str, dict] = {}
    for s in steps:
        n = s.get("nessie") or {}
        if n.get("op") != "bill" or not str(n.get("key") or "").startswith("debt:"):
            continue
        if n.get("action") != "end":
            active[n["key"]] = float(n["amount"])
        meta[n["key"]] = {"payee": n.get("payee"), "from": n.get("from"), "to": n.get("to")}
    names = {d["account_id"]: d.get("name") or d["account_id"] for d in (sim or {}).get("debts") or []}
    out = []
    for i in range(1, len(months) - 1):
        prev, cur = months[i - 1]["balances"]["debts"], months[i]["balances"]["debts"]
        done_now = [a for a, v in cur.items() if v <= 0.005 and prev.get(a, 0) > 0.005]
        if not done_now:
            continue
        nxt = months[i + 1]
        pays = (nxt.get("balances") or {}).get("payments") or {}
        why = ", ".join(names.get(a, a) for a in done_now) + " paid off"
        for aid in sorted(set(pays) | {k.split(":", 1)[1] for k in active}):
            key, new = f"debt:{aid}", float(dollars(pays.get(aid, 0.0)))
            cur_amt = active.get(key)
            if cur_amt is not None and new < 1:
                out.append({"month": nxt["month"], "key": key, "action": "end", "amount": 0, "was": cur_amt,
                            "name": names.get(aid, aid), "why": why})
                active.pop(key)
            elif cur_amt is not None and abs(new - cur_amt) >= 1:
                stmt = aid in (autopay or []) and cur.get(aid, 0) <= 0.005
                out.append({"month": nxt["month"], "key": key, "action": "change", "amount": new, "was": cur_amt,
                            "name": names.get(aid, aid), "why": why + ("; now the statement in full" if stmt else "")})
                active[key] = new
            elif cur_amt is None and new >= 1:
                out.append({"month": nxt["month"], "key": key, "action": "new", "amount": new, "was": None,
                            "name": names.get(aid, aid), "why": why, "to": aid})
                active[key] = new
    for s in steps:
        n = s.get("nessie") or {}
        if n.get("op") == "bill" and str(n.get("key") or "").startswith("contrib:") and n.get("resume"):
            out.append({"month": n["resume"], "key": n["key"], "action": "resume", "amount": float(n["was"] or 0),
                        "was": None if n["action"] == "pause" else n["amount"], "name": n.get("payee"),
                        "why": "high-cost debt is $0", "to": n.get("to")})
    for e in out:
        e["done"] = False
    out.sort(key=lambda e: e["month"])
    return out


def apply_due_bills(cid: str, pid: str, now: str) -> list[dict]:
    """Check-in upkeep: send the approved bill schedule's entries due by month `now` ("YYYY-MM"). Only what the
    customer approved (the schedule saved at approve) runs; nothing new is decided here. Returns what ran."""
    from ..data.seed import nessie_ids, record_bills
    from ..nessie import calls

    p = store.get_proposal(pid)
    ex = dict((p or {}).get("execution") or {})
    sched = ex.get("bill_schedule") or []
    due = [e for e in sched if not e.get("done") and e["month"] <= now]
    if not due:
        return []
    ids = nessie_ids(cid)
    bills, paused, chk = ids.get("bills") or {}, ids.get("paused") or {}, ids.get("checking")
    planned = []
    for e in due:
        b = bills.get(e["key"]) or {}
        if e["action"] == "change" and b.get("id"):
            planned.append((e, calls.update_bill(b["id"], e["amount"])))
        elif e["action"] == "end" and b.get("id"):
            planned.append((e, calls.delete_bill(b["id"])))
        elif e["action"] in ("new", "resume") and chk:
            spec = paused.get(e["key"]) or {}
            day = int(spec.get("recurring_date") or 28)
            y, m = (int(x) for x in e["month"].split("-"))
            first = f"{y:04d}-{m:02d}-{day:02d}"
            payee = spec.get("payee") or e.get("name") or e["key"]
            nick = spec.get("nickname") or f"Paidway: {payee}"
            planned.append((e, calls.bill(chk, payee, nick, first, day, e["amount"])))
        else:
            e["done"], e["result"] = True, "skipped: no bill to change"
    ran = []
    if planned and NESSIE_KEY and not any("<" in c["path"] for _, c in planned):
        receipt = _send_all([{**c, "tool": f"nessie.write.{e['action']}_bill", "step": e["why"]} for e, c in planned])
        changes = []
        for (e, c), r in zip(planned, receipt):
            e["done"], e["result"] = _ok(r), r.get("status")
            if _ok(r):
                ch = {"key": e["key"], "action": e["action"], "amount": e["amount"]}
                if e["action"] in ("new", "resume"):
                    body = c["body"]
                    ch.update(id=r.get("id"), payee=body["payee"], nickname=body["nickname"],
                              payment_date=body["payment_date"], recurring_date=body["recurring_date"],
                              amount=body["payment_amount"], kind="debt" if e["key"].startswith("debt:") else "contribution",
                              to=e.get("to") or (paused.get(e["key"]) or {}).get("to"))
                changes.append(ch)
            ran.append({**e, "method": c["method"], "path": c["path"]})
        record_bills(cid, changes)
    else:
        for e, c in planned:  # no Nessie (tests, local): the schedule still advances, recorded only
            e["done"], e["result"] = True, "recorded (Nessie off)"
            ran.append({**e, "method": c["method"], "path": c["path"]})
    ex["bill_schedule"] = sched
    ex.setdefault("bill_log", []).extend({k: r.get(k) for k in ("month", "key", "action", "amount", "was", "name",
                                                                  "why", "result", "method")} for r in ran)
    store.set_execution(pid, ex)
    return ran


def _mark_posted(cid: str, receipt: list[dict]) -> None:
    from ..data.seed import mark_posted

    nids = [r["path"].split("/")[2] for r in receipt if _ok(r) and r["path"].startswith("/accounts/")
            and not r["tool"].endswith("create_bill")]
    if nids:
        mark_posted(cid, nids)


def _outcome(cid, record, planned, receipt, mir, before, shown, transfers) -> tuple[str, dict]:
    """(status, execution). Any call that didn't go through leaves the plan approved (the dashboard tracks it)
    with `retry_available`; all through is executed."""
    after = _read_balances(mir, shown)
    done = {"nessie_calls": receipt, "nessie_balances_before": before, "nessie_balances_after": after,
            "nessie_check": _check(planned, receipt, mir, before, after), "nessie_planned": planned,
            "bills": [{"payee": r["body"].get("payee"), "amount": r["body"].get("payment_amount"),
                       "day": r["body"].get("recurring_date"), "first": r["body"].get("payment_date"), "id": r["id"],
                       "from": r.get("from_nickname") or r.get("from"), "action": "new"}
                      for r in receipt if r["tool"].endswith("create_bill") and _ok(r)]
                     + [{"payee": r.get("to"), "amount": r.get("amount"), "was": r.get("was"), "id": r["path"].split("/")[2],
                         "action": r.get("bill_action"), "resume": r.get("resume"),
                         "from": r.get("from_nickname") or r.get("from")}
                        for r in receipt if r["tool"].endswith(("update_bill", "delete_bill")) and _ok(r)]}
    bad = [r for r in receipt if not _ok(r)]
    total = round(sum(t["amount"] for t in transfers), 2)
    if bad:
        errs = [r for r in bad if r["status"] == "error"]
        return "approved", {**record, **done, "mode": "nessie_partial", "amount": total, "retry_available": True,
                            "error": f"{len(bad)} of {len(receipt)} Nessie calls didn't go through"
                                     + (f": {errs[0]['error']}" if errs else "")}
    return "executed", {**record, **done, "mode": "nessie", "amount": total,
                        "transfer": next((r for r in receipt if r["tool"].endswith("create_transfer")), None)}


def retry(pid: str) -> dict:
    """Send again only the calls of an approved plan that didn't go through (errors, and the second legs they
    held back). Calls that went through are never sent twice."""
    p = store.get_proposal(pid)
    if not p:
        raise ApproveError(404, "no such proposal")
    ex = p.get("execution") or {}
    receipt = ex.get("nessie_calls") or []
    if not ex.get("retry_available") or not any(not _ok(r) for r in receipt):
        raise ApproveError(409, "nothing to retry")
    if not store.claim_retry(pid):
        raise ApproveError(409, "a retry is already running")
    cid = p["customer_id"]
    mir = mirrors(cid, balance_sheet.accounts(cid, live=False))
    shown = [k for k, m in mir.items() if not m["id"].startswith("<")]
    todo = [i for i, r in enumerate(receipt) if not _ok(r)]
    try:
        again = _send_all([{**receipt[i], "body": (ex.get("nessie_planned") or receipt)[i].get("body") or receipt[i]["body"]}
                           for i in todo])
    except Exception:
        store.set_status(pid, "approved")
        raise
    finally:
        balance_sheet.clear_cache()
    for i, r in zip(todo, again):
        r["retried"] = True
        receipt[i] = r
    _mark_posted(cid, again)
    planned = ex.get("nessie_planned") or receipt
    record = {k: ex.get(k) for k in ("plan", "scheduled", "simulation")}
    transfers = [{"amount": r["amount"]} for r in planned if r.get("leg") == 1]
    status, execution = _outcome(cid, record, planned, receipt, mir, ex.get("nessie_balances_before") or {}, shown, transfers)
    return store.set_status(pid, status, execution)
