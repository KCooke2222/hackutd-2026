"""Agent v3: a LangGraph StateGraph on Nemotron (OpenAI-compatible endpoint).

First run: gather (code: brief + solver plan) -> check (<= 2 tool calls, only on flags) -> decide (1 LLM call,
JSON: buffer, driver, whys) -> propose (code calls propose_plan; the explanation's figures come from the saved
proposal). Talk to the plan: interpret -> plan (ReAct) -> compare -> explain. Validation lives in propose_plan (validate_plan_v2); after MAX_REJECTIONS rejections
the run stops with an error. Nothing here can move money.
"""

from __future__ import annotations

import json
import re
from typing import Any, TypedDict

from ..config import NEMOTRON_MODEL, NVIDIA_API_KEY, NVIDIA_BASE_URL
from ..data import store
from ..planning import balance_sheet, finance
from . import prompts
from .tools import make_tools, sim_brief

DEFAULT_INSTRUCTION = "Run the monthly check on my money: all my debts, and propose a plan."
RECURSION_LIMIT = 40


class AgentUnavailable(RuntimeError):
    pass


def _llm():
    if not NVIDIA_API_KEY:
        raise AgentUnavailable("NVIDIA_API_KEY is not set. Get one at build.nvidia.com and add it to .env")
    from langchain_openai import ChatOpenAI

    class NemotronChat(ChatOpenAI):
        """ChatOpenAI drops NVIDIA's `reasoning_content`; keep it so the UI can show the model's thinking."""

        def _create_chat_result(self, response, generation_info=None):
            result = super()._create_chat_result(response, generation_info)
            choices = response.get("choices", []) if isinstance(response, dict) else getattr(response, "choices", [])
            for gen, ch in zip(result.generations, choices):
                msg = ch.get("message", {}) if isinstance(ch, dict) else getattr(ch, "message", None)
                rc = msg.get("reasoning_content") if isinstance(msg, dict) else (getattr(msg, "model_extra", None) or {}).get("reasoning_content")
                if rc:
                    gen.message.additional_kwargs["reasoning_content"] = rc
            return result

    return NemotronChat(model=NEMOTRON_MODEL, base_url=NVIDIA_BASE_URL, api_key=NVIDIA_API_KEY, temperature=0.2, timeout=120)


# ---------- JSON from LLM text ----------

def parse_json(text: str) -> Any:
    """Best-effort JSON from model output: strips <think> blocks and code fences, then takes the first
    balanced {...} or [...] that parses."""
    if not isinstance(text, str):
        return None
    t = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    t = re.sub(r"^.*?</think>", "", t, flags=re.S)  # unclosed opening tag
    for m in re.finditer(r"```(?:json)?\s*(.*?)```", t, flags=re.S):
        try:
            return json.loads(m.group(1))
        except ValueError:
            pass
    for start, ch in ((i, c) for i, c in enumerate(t) if c in "{["):
        close = "}" if ch == "{" else "]"
        depth, in_str, esc = 0, False, False
        for j in range(start, len(t)):
            c = t[j]
            if in_str:
                esc = (c == "\\") and not esc
                if c == '"' and not esc:
                    in_str = False
                elif c != "\\":
                    esc = False
                continue
            if c == '"':
                in_str = True
            elif c in "{[":
                depth += 1
            elif c in "}]":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(t[start : j + 1])
                    except ValueError:
                        break
        if ch == close:
            continue
    return None


# ---------- gather (code only) ----------

def gather_snapshot(customer_id: str) -> dict:
    sheet = balance_sheet.balance_sheet(customer_id)
    pr, lad = sheet["priority"], sheet["ladder"]
    try:
        from ..planning import insights

        cf = insights.cash_flow(customer_id)
        cash = {k: cf.get(k) for k in ("monthly_avg", "saved_per_month", "named_flows", "spending_by_category",
                                       "card_spending_by_category")}
        cash["last_3_months"] = (cf.get("months") or [])[-3:]
    except ImportError:  # insights not built yet
        r = finance.refill_breakdown(customer_id)
        cash = {"card_monthly_avg": r.get("monthly_avg"), "card_spending_by_category": r.get("new_spending_by_category")}
    try:
        from ..planning.assumptions import ASSUMPTIONS
    except ImportError:
        ASSUMPTIONS = {}
    return {
        "customer_id": customer_id,
        "as_of": finance.today(),
        "profile": {k: sheet["profile"].get(k) for k in ("monthly_income", "monthly_non_debt_expenses")},
        "cash_flow": cash,
        "debts": [{k: d.get(k) for k in ("account_id", "name", "account_type", "source", "balance", "apr_pct", "min_payment",
                                          "secured", "consequence", "extra_rank")} for d in pr["debts"]],
        "monthly_minimums": pr["monthly_minimums"],
        "assets": [{k: a.get(k) for k in ("account_id", "name", "account_type", "kind", "balance", "annual_yield_pct", "cost_basis")}
                   for a in sheet["accounts"] if a["side"] == "asset"],
        "ladder": {"emergency_buffer": lad["emergency_buffer"], "keep_months": lad["keep_months"],
                   "tiers": [{k: t[k] for k in ("key", "proposable", "available", "cost_pct")} for t in lad["tiers"]]},
        "totals": sheet["totals"],
        "reality_check": _reality_brief(customer_id),
        "assumptions": ASSUMPTIONS,
    }


def _reality_brief(customer_id: str) -> dict | None:
    """The reality check without the per-category detail (the LLM gets the verdict and the gap)."""
    try:
        from ..planning import wealth

        rc = wealth.reality_check(customer_id)
    except Exception:  # noqa: BLE001 - the plan still runs without it
        return None
    return _reality_fields(rc)


def _reality_fields(rc: dict | None) -> dict:
    """The verdict and the gap, for prompts (an {"error"} or a missing check gives {})."""
    if not isinstance(rc, dict) or "realistic" not in rc:
        return {}
    return {k: rc.get(k) for k in ("realistic", "message", "gap_per_month", "cash_runs_out", "close_gap", "monthly")}


# ---------- message log helpers ----------

def _entry(m) -> dict:
    entry = {"role": m.type, "content": m.content if isinstance(m.content, str) else str(m.content)}
    if getattr(m, "tool_calls", None):
        entry["tool_calls"] = [{"name": c["name"], "args": c["args"]} for c in m.tool_calls]
    if m.type == "tool":
        entry["name"] = getattr(m, "name", None)
    rc = (getattr(m, "additional_kwargs", None) or {}).get("reasoning_content")
    if rc:
        entry["reasoning"] = rc
    return entry


def _usd(n) -> str:
    return f"${float(n):,.0f}"


def summarize(name: str, args: dict, content: str) -> str:
    """One number per tool result, for the thinking chips."""
    d = parse_json(content) if isinstance(content, str) else content
    if not isinstance(d, (dict, list)):
        return "done"
    try:
        if isinstance(d, dict) and d.get("error"):
            return "error"
        if name == "sql":
            return f"{len(d.get('rows', []))} rows"
        if name == "cash_flow":
            return f"{_usd(d['saved_per_month'])}/mo saved"
        if name == "spending_detail":
            return f"avg {_usd(d['avg'])} · low {_usd(d['min'])}"
        if name == "find_recurring":
            return f"{len(d)} recurring"
        if name == "compare_sources":
            best = next((s for s in d["sources"] if s.get("use")), None)
            return f"best: {best['source']}" if best else "none worth it"
        if name == "debt_priority":
            top = next((x for x in d["debts"] if x.get("extra_rank") == 1), None)
            return f"#1 {top['name']} {top['apr_pct']:g}%" if top else f"{len(d['debts'])} debts"
        if name == "liquidity_ladder":
            return f"{_usd(d['proposable_available'])} tappable"
        if name == "simulate_plan":
            gain = d.get("net_worth_gain_vs_baseline")
            free = d.get("high_cost_debt_free") or "never"
            return f"$0 {free}" + (f" · +{_usd(gain)} NW" if gain is not None else "")
        if name == "propose_plan":
            return "saved" if d.get("saved") else f"rejected: {(d.get('reasons') or ['?'])[0]}"[:60]
    except (KeyError, TypeError, AttributeError, StopIteration, IndexError):
        pass
    return "done"


def _label(name: str, args: dict) -> str:
    try:
        return _label_raw(name, args if isinstance(args, dict) else {})
    except (TypeError, ValueError, AttributeError):  # model sent oddly shaped args; the tool schema rejects them
        return name


def _label_raw(name: str, args: dict) -> str:
    if name == "simulate_plan":
        bits = [f"budget {_usd(args.get('monthly_debt_budget', 0))}"]
        red = sum(float((r or {}).get("amount_per_month", 0)) for r in args.get("redirects") or [])
        if red:
            bits.append(f"redirect {_usd(red)}")
        lump = sum(float((x or {}).get("amount", 0)) for x in args.get("lump_sums") or [])
        if lump:
            bits.append(f"lump {_usd(lump)}")
        return "Simulate " + " · ".join(bits)
    if name == "spending_detail":
        return f"Spending: {args.get('category', '')}"
    if name == "compare_sources":
        return f"Compare sources for {_usd(args.get('amount', 0))}"
    if name == "sql":
        return "SQL query"
    return {
        "cash_flow": "Cash flow", "find_recurring": "Recurring charges", "debt_priority": "Debt ranking",
        "liquidity_ladder": "Cash ladder", "optimize_plan": "Optimize plan", "propose_plan": "Propose plan",
    }.get(name, name)


# ---------- the graph ----------

class AgentState(TypedDict, total=False):
    customer_id: str
    instruction: str
    snapshot: dict
    diagnosis: dict
    messages: list
    proposal_id: str | None
    explain: dict | None
    final: str
    error: str | None
    brief: dict
    checked: str
    decision: dict


def _writer():
    try:
        import langgraph.config as lg_config

        return lg_config.get_stream_writer()
    except Exception:  # noqa: BLE001  (outside a streaming run)
        return lambda _ev: None


def _phase(write, phase: str, label: str, result: str | None = None):
    pid = f"phase:{phase}"
    if result is None:
        write({"type": "phase", "phase": phase, "status": "start"})
        write({"type": "call", "id": pid, "name": f"phase_{phase}", "label": label, "args": {}, "why": ""})
    else:
        write({"type": "phase", "phase": phase, "status": "done", "summary": result})
        write({"type": "result", "id": pid, "name": f"phase_{phase}", "result": result})


def _ask_json(llm, prompt: str) -> tuple[Any, dict]:
    msg = llm.invoke([("user", prompt)])
    e = _entry(msg)
    data = parse_json(e["content"])
    if data is None and e.get("reasoning"):
        data = parse_json(e["reasoning"])
    return data, e


def _react(llm, tools: list, system: str, instruction: str, w, st: dict, messages: list, limit: int = RECURSION_LIMIT) -> str:
    """Run one ReAct agent, streaming `call`/`result` events and appending to `messages`. Stops once propose_plan
    saved (st["proposal_id"]) or gave up (st["stop"]). Returns the model's last text reply."""
    from langgraph.prebuilt import create_react_agent

    agent = create_react_agent(llm, tools, prompt=system)
    pending: dict[str, dict] = {}
    final = ""
    for update in agent.stream({"messages": [("user", instruction)]}, {"recursion_limit": limit}, stream_mode="updates"):
        for node in update.values():
            for m in (node or {}).get("messages", []):
                e = _entry(m)
                messages.append(e)
                if m.type == "ai":
                    why = e.get("reasoning") or e["content"]
                    for c in getattr(m, "tool_calls", None) or []:
                        pending[c["id"]] = c
                        w({"type": "call", "id": c["id"], "name": c["name"], "label": _label(c["name"], c["args"]), "args": c["args"], "why": why})
                        why = ""
                    if not m.tool_calls:
                        final = e["content"]
                elif m.type == "tool":
                    c = pending.get(m.tool_call_id, {})
                    w({"type": "result", "id": m.tool_call_id, "name": e["name"], "result": summarize(e["name"], c.get("args", {}), e["content"])})
        if st.get("proposal_id") or st.get("stop"):
            break  # saved (explain writes the headline) or out of attempts
    return final


EXPLAIN_REASONING_BUDGET = 512  # the headline is one line; reasoning stays on but capped (it ran ~10k tokens, ~60 s)
INTERPRET_REASONING_BUDGET = 1024  # interpret is usually ~2 s but spiked to 23 s unbounded


def _budgeted(llm, budget: int):
    """Reasoning on, but at most `budget` reasoning tokens, plus room for a short JSON answer."""
    return llm.bind(max_tokens=budget + 1024,
                    extra_body={"reasoning_budget": budget, "chat_template_kwargs": {"enable_thinking": True}})


def _gap_first(headline: str, sim: dict) -> str:
    rc = sim.get("reality_check") or {}
    gap = rc.get("gap_per_month") or 0
    if rc and rc.get("realistic") is False and f"{gap:,.0f}" not in headline:
        return f"${gap:,.0f}/mo short: {headline}"  # the gap always leads
    return headline


def _carry_explain(parent_explain: dict | None, child_plan: dict, headline: str, extra: dict | None) -> dict:
    """The previous plan's labels for the new plan: its cut notes for cuts that are still there, the new headline."""
    keep = {t.get("category") for t in child_plan.get("spending_targets") or []}
    pe = dict(parent_explain or {})
    pe["cuts"] = [c for c in pe.get("cuts") or [] if c.get("category") in keep]
    return {**pe, "headline": headline, **(extra or {})}


def _diff_headline(new: dict | None, cmp: dict, cap_notes: list[str] | None = None) -> str:
    """One line from the rules and the comparison, no LLM: "Travel no longer cut. Debt-free Aug 2027 → Oct 2027, −$1,240 at 2 yrs."."""
    from .adjust import SOURCE_NAME

    n = new or {}
    name = lambda c: c.replace("_", " ").capitalize()  # noqa: E731
    what = [f"{name(c)} no longer cut" for c in n.get("protect_categories") or []]
    what += [f"{name(c)} capped at ${v:,.0f}/mo" for c, v in (n.get("category_max") or {}).items()]
    what += [f"{name(c)} kept at ${v:,.0f}+/mo" for c, v in (n.get("category_min") or {}).items()]
    what += [f"{SOURCE_NAME.get(x, x)} not used" for x in n.get("forbid_sources") or []]
    if (m := n.get("min_emergency_months")) is not None:
        what.append(f"Keeping {m:g}+ months cash")
    if (v := n.get("min_debt_budget")) is not None:
        what.append(f"Paying at least ${v:,.0f}/mo to debt")
    if (v := n.get("max_debt_budget")) is not None:
        what.append(f"Paying at most ${v:,.0f}/mo to debt")
    if (v := n.get("max_redirect")) is not None:
        what.append(f"Redirecting at most ${v:,.0f}/mo")
    if not what:
        what = (cmp.get("changes") or ["Plan updated"])[:2]
    f = cmp.get("high_cost_debt_free") or {}
    f0, f1 = _month(f.get("before")), _month(f.get("after"))
    out = [f"Debt-free {f0} → {f1}" if f0 != f1 else f"Still debt-free {f1}"]
    d = (cmp.get("net_worth_24m") or {}).get("delta") or 0
    if abs(d) >= 1:
        out.append(f"{'−' if d < 0 else '+'}${abs(d):,.0f} at 2 yrs")
    line = "; ".join(what) + ". " + ", ".join(out) + "."
    return (" ".join(cap_notes) + " " + line) if cap_notes else line


def _explain(llm, w, proposal_id: str, messages: list, extra: dict | None = None, *, request: str = "",
             cmp: dict | None = None, parent_explain: dict | None = None) -> dict:
    """The explain phase after an agent re-plan: one headline from just the diff (short prompt, capped output,
    reasoning on but budgeted), stored on the proposal. Falls back to the code headline."""
    _phase(w, "explain", "Writing it up")
    p = store.get_proposal(proposal_id)
    cmp = cmp or {}
    diff = {k: cmp.get(k) for k in ("high_cost_debt_free", "net_worth_24m", "interest_total", "changes")}
    llm = _budgeted(llm, EXPLAIN_REASONING_BUDGET)
    data, e = _ask_json(llm, prompts.fill(prompts.EXPLAIN, request=request, diff=diff))
    head = data.get("headline") if isinstance(data, dict) else None
    if not isinstance(head, str) or not head.strip():
        head = _diff_headline(None, cmp)
    data = _carry_explain(parent_explain, p["plan"], _gap_first(head.strip()[:160], p["simulation"]), extra)
    try:
        store.annotate(p["id"], explain=data)
    except AttributeError:
        pass
    _phase(w, "explain", "", data["headline"][:60])
    w({"type": "final", "text": data["headline"], "why": ""})
    return {"explain": data, "final": data["headline"], "messages": [*messages, {**e, "name": "explain"}]}


# ---------- agent v3 first run: code builds the brief, one agent pass makes the calls ----------

MAX_CHECK_CALLS = 2
_FIGURE = re.compile(r"\$\s?\d|\d+(\.\d+)?\s?%|\b20\d\d\b")  # the agent writes words; code writes every figure


def _words(x, limit: int = 220) -> str:
    """Agent text with no figures in it ("" if it slipped one in: code writes the numbers)."""
    t = str(x or "").strip()
    return "" if not t or _FIGURE.search(t) else t[:limit]


def _month(ym: str | None) -> str:
    if not ym:
        return "never"
    import datetime as _dt

    try:
        return _dt.date(int(ym[:4]), int(ym[5:7]), 1).strftime("%b %Y")
    except (ValueError, IndexError):
        return str(ym)


def _for_agent(brief: dict) -> dict:
    """The brief, trimmed to what the agent's judgement needs (smaller prompt, faster first token)."""
    snap, plan = brief["snapshot"], brief["plan"]
    cf = snap.get("cash_flow") or {}
    return {
        "profile": snap.get("profile"),
        "cash_flow": {k: cf.get(k) for k in ("monthly_avg", "saved_per_month", "named_flows", "card_spending_by_category")},
        "debts": [{k: d.get(k) for k in ("name", "balance", "apr_pct", "min_payment", "secured")} for d in snap["debts"]],
        "assets": [{k: a.get(k) for k in ("name", "kind", "balance")} for a in snap["assets"]],
        "ladder": snap.get("ladder"),
        "plan": {k: plan.get(k) for k in ("paydown", "lump_sums", "extra_order", "spending_targets", "no_cuts", "payoff",
                                          "high_cost_debt_free", "baseline_high_cost_debt_free", "net_worth_gain",
                                          "interest_saved", "upfront_cost", "tradeoffs")},
        "buffers": brief["buffers"],
        "flags": brief["flags"],
        "spending_avg": brief["spending_avg"],
        "underwater": brief.get("underwater"),
        "reality": {k: (snap.get("reality_check") or {}).get(k) for k in ("realistic", "message", "gap_per_month", "close_gap")},
    }


def _option_cards(under: dict | None, d: dict) -> list[dict]:
    """Underwater cards: code's figures, the agent's order and words. Cards the agent skipped go last."""
    if not under:
        return []
    by = {o["key"]: o for o in under["options"]}
    words = {o.get("key"): o for o in d.get("options") or [] if isinstance(o, dict) and o.get("key") in by}
    rec = {k for k in d.get("recommend") or [] if k in by and not by[k].get("never_recommend")}
    modelled = [k for k in by if by[k]["modelled"]]
    order = [*[k for k in words if k in modelled], *[k for k in modelled if k not in words],
             *[k for k in by if not by[k]["modelled"]]]  # plain ones keep code's order: earn more / IRA last
    return [{**by[k], "why": _words((words.get(k) or {}).get("why"), 140), "downside": _words((words.get(k) or {}).get("downside"), 140),
             "recommended": k in rec, "agent_skipped": k not in words} for k in order]


def _explain_v3(p: dict, d: dict, under: dict | None = None) -> dict:
    """The saved proposal's explanation: every figure from the proposal's own plan and simulation, the agent's
    words (driver, whys, downsides) beside them."""
    plan, sim = p["plan"], p["simulation"]
    b = sim.get("budget") or {}
    names = {x["account_id"]: x["name"] for x in sim.get("debts") or [] if "account_id" in x}
    gain = round(sim["net_worth_24m"] - sim["baseline_net_worth_24m"])
    free = sim.get("high_cost_debt_free")
    lead = f"High-cost debt $0 by {_month(free)}" if free else "High-cost debt not paid off in 5 years"
    headline = f"{lead}, {'+' if gain >= 0 else '-'}${abs(gain):,} net worth in 2 years"
    rc = sim.get("reality_check") or {}
    if rc.get("realistic") is False:  # underwater: the gap leads, alone (a payoff date would contradict it)
        runs = rc.get("cash_runs_out")
        headline = f"${rc.get('gap_per_month') or 0:,.0f}/mo short" + (f", cash runs out {_month(runs)}" if runs else ": the debt keeps growing")
    net, gross, cur = b.get("net_paydown_per_month"), b.get("monthly_debt_budget"), b.get("current_net_paydown_per_month")
    summary = (f"You'd pay {_usd(gross)}/mo to debt; {_usd(net)} of it actually shrinks the balance (was {_usd(cur)}),"
               f" the rest covers new card purchases.") if net is not None else ""
    pp = (rc.get("monthly") or {}).get("principal_paydown")
    if rc.get("realistic") is False and pp is not None and pp < 0:  # underwater: "net" would overstate it
        summary = (f"Even with every change, interest and new card charges add more than you can pay:"
                   f" the debt grows about {_usd(-pp)}/mo until the gap closes.")
    why = {c.get("category"): _words(c.get("why"), 120) for c in d.get("cuts") or [] if isinstance(c, dict)}
    cuts, levers = [], [{"kind": "debt_payment", "label": f"{_usd(net)}/mo off the debt", "amount": net}] if net is not None else []
    for t in plan.get("spending_targets") or []:
        bu = t.get("buys") or {}
        buys = (f"{bu['months_sooner']} months sooner, +{_usd(bu['net_worth_gain'])} in 2 years"
                if t.get("source") == "suggested" and bu.get("months_sooner") is not None else "")
        cuts.append({"category": t["category"], "how": "; ".join(t.get("how") or []) if isinstance(t.get("how"), list) else (t.get("how") or ""),
                     "why": why.get(t["category"]) or "", "suggested": t.get("source") == "suggested", "buys": buys,
                     "from": t.get("current"), "to": t.get("target")})
        levers.append({"kind": "cut", "label": f"{t['category'].replace('_', ' ')} to {_usd(t.get('target'))}/mo", "amount": t.get("target")})
    for l in plan.get("lump_sums") or []:
        levers.append({"kind": "lump_sum", "label": f"{_usd(l['amount'])} {l['source']} to {names.get(l['to_debt'], l['to_debt'])}", "amount": l["amount"]})
    return {"headline": headline, "summary": summary, "driver": _words(d.get("driver"), 300),
            "buffer_months": plan.get("keep_emergency_months"), "buffer_why": _words(d.get("buffer_why")),
            "lumps_why": _words(d.get("lumps_why")), "downsides": [w for w in (_words(x, 140) for x in d.get("downsides") or []) if w][:3],
            "underwater": _words(d.get("underwater"), 300), "levers": levers, "cuts": cuts, "options": [],
            "underwater_options": _option_cards(under, d),
            "underwater_gap": (under or {}).get("gap_per_month"),
            "underwater_not_helping": (under or {}).get("not_helping") or []}


def build_graph(customer_id: str):
    """gather (code: brief + solver plan) -> check (<= 2 tool calls, only when the brief has flags)
    -> decide (1 LLM call: buffer, driver, whys) -> propose (code: propose_plan, explanation from its numbers)."""
    llm = _llm()
    from langgraph.graph import END, START, StateGraph

    from . import brief as brief_mod

    def gather(s: AgentState) -> dict:
        w = _writer()
        _phase(w, "gather", "Reading accounts")
        _phase(w, "build", "Building the plan")
        b = brief_mod.build(customer_id)
        snap = b["snapshot"]
        _phase(w, "gather", "", f"{len(snap['debts'])} debts · {len(snap['assets'])} assets")
        pl = b["plan"]
        _phase(w, "build", "", (f"$0 by {_month(pl.get('high_cost_debt_free'))}" if pl.get('high_cost_debt_free') else "not $0 in 5 yrs") + f" · {len([t for t in pl.get('spending_targets') or [] if t.get('source') == 'suggested'])} cuts suggested")
        return {"snapshot": snap, "brief": b}

    def check(s: AgentState) -> dict:
        w = _writer()
        fl = s["brief"]["flags"]
        _phase(w, "check", "Checking odd spending")
        tools = {t.name: t for t in make_tools(customer_id, {}) if t.name in ("spending_detail", "sql")}
        msgs: list = [("user", prompts.fill(prompts.CHECK, flags=fl, spending=s["brief"]["spending_avg"]))]
        log = list(s.get("messages") or [])
        ai = llm.bind_tools(list(tools.values())).invoke(msgs)
        log.append({**_entry(ai), "name": "check"})
        calls = (getattr(ai, "tool_calls", None) or [])[:MAX_CHECK_CALLS]
        from langchain_core.messages import ToolMessage

        if calls:
            why = _entry(ai).get("reasoning") or ai.content or ""
            outs = []
            for c in calls:
                w({"type": "call", "id": c["id"], "name": c["name"], "label": _label(c["name"], c["args"]), "args": c["args"], "why": why})
                why = ""
                try:
                    res = tools[c["name"]].invoke(c["args"])
                except Exception as ex:  # noqa: BLE001
                    res = {"error": str(ex)}
                content = json.dumps(res, default=str)[:6000]
                w({"type": "result", "id": c["id"], "name": c["name"], "result": summarize(c["name"], c["args"], content)})
                outs.append(ToolMessage(content=content, tool_call_id=c["id"], name=c["name"]))
                log.append({"role": "tool", "name": c["name"], "content": content[:2000]})
            ai2 = llm.invoke([*msgs, ai, *outs, ("user", "Now answer in one sentence per flag. No more tool calls.")])
            log.append({**_entry(ai2), "name": "check"})
            note = ai2.content if isinstance(ai2.content, str) else str(ai2.content)
        else:
            note = ai.content if isinstance(ai.content, str) else str(ai.content)
        _phase(w, "check", "", ", ".join(f["category"].replace("_", " ") for f in fl)[:60])
        return {"checked": note.strip()[:800], "messages": log}

    def decide(s: AgentState) -> dict:
        w = _writer()
        _phase(w, "decide", "Choosing what to suggest")
        b = s["brief"]
        data, e = _ask_json(llm, prompts.fill(prompts.DECIDE, default_buffer=f"{b['default_buffer_months']:g}",
                                              brief=_for_agent(b), checked=s.get("checked") or "no flags"))
        if not isinstance(data, dict):
            data = {}
        try:
            m = float(data.get("buffer_months"))
        except (TypeError, ValueError):
            m = b["default_buffer_months"]
        if f"{m:g}" not in b["buffers"]:
            m = b["default_buffer_months"]
        data["buffer_months"] = m
        _phase(w, "decide", "", f"keep {m:g} months of cash")
        return {"decision": data, "messages": [*(s.get("messages") or []), {**e, "name": "decide"}]}

    def propose(s: AgentState) -> dict:
        w = _writer()
        _phase(w, "propose", "Saving the plan")
        d = s.get("decision") or {}
        st: dict = {}
        tool = next(t for t in make_tools(customer_id, st) if t.name == "propose_plan")
        out = tool.invoke({"monthly_debt_budget": 0, "extra_order": [], "keep_emergency_months": d.get("buffer_months") or 3.0,
                           "spending_targets": [], "declined_cuts": [], "rationale": _words(d.get("driver"), 120) or "Solver plan"})
        log = [*(s.get("messages") or []), {"role": "tool", "name": "propose_plan", "content": json.dumps(out, default=str)[:2000]}]
        if not st.get("proposal_id"):
            reasons = "; ".join(st.get("last_reasons") or []) or str(out.get("stop") or out.get("reasons") or "no plan")
            _phase(w, "propose", "", "no plan")
            return {"messages": log, "error": f"No valid plan: {reasons}"[:700]}
        p = store.get_proposal(st["proposal_id"])
        ex = _explain_v3(p, d, s["brief"].get("underwater"))
        try:
            store.annotate(p["id"], explain=ex)
        except AttributeError:
            pass
        _phase(w, "propose", "", "Plan ready")
        w({"type": "final", "text": ex["headline"], "why": ""})
        return {"proposal_id": p["id"], "explain": ex, "final": ex["headline"], "messages": log,
                "diagnosis": {"driver": ex["driver"]}}

    g = StateGraph(AgentState)
    for name, fn in (("gather", gather), ("check", check), ("decide", decide), ("propose", propose)):
        g.add_node(name, fn)
    g.add_edge(START, "gather")
    g.add_conditional_edges("gather", lambda s: "check" if s["brief"]["flags"] else "decide", {"check": "check", "decide": "decide"})
    g.add_edge("check", "decide")
    g.add_edge("decide", "propose")
    g.add_edge("propose", END)
    return g.compile()


def _result(state: dict) -> dict:
    msgs = state.get("messages") or []
    return {
        "messages": msgs,
        "final": state.get("final") or (msgs[-1]["content"] if msgs else ""),
        "diagnosis": state.get("diagnosis"),
        "explain": state.get("explain"),
        "error": state.get("error"),
        "proposal_id": state.get("proposal_id"),
    }


def run(customer_id: str, instruction: str | None = None) -> dict:
    graph = build_graph(customer_id)
    try:
        state = graph.invoke({"customer_id": customer_id, "instruction": instruction or DEFAULT_INSTRUCTION, "messages": []})
    except Exception as ex:  # noqa: BLE001
        import traceback

        tb = traceback.format_exc(limit=-3).replace("\n", " | ")
        return {"messages": [], "final": "", "diagnosis": None, "explain": None, "error": f"{type(ex).__name__}: {ex} @ {tb}"[:900], "proposal_id": None}
    return _result(state)


def stream_run(customer_id: str, instruction: str | None = None):
    """Yield UI events: `phase`, `call` (tool or phase starts), `result` (one-number summary), `final`, then
    `done` (with messages, diagnosis, explain) or `error`."""
    try:
        graph = build_graph(customer_id)
    except AgentUnavailable as e:
        yield {"type": "error", "error": str(e)}
        return
    state: dict = {"messages": []}
    try:
        for mode, chunk in graph.stream({"customer_id": customer_id, "instruction": instruction or DEFAULT_INSTRUCTION, "messages": []},
                                        stream_mode=["custom", "values"]):
            if mode == "custom":
                yield chunk
            else:
                state = chunk
    except Exception as ex:  # noqa: BLE001
        yield {"type": "error", "error": f"{type(ex).__name__}: {ex}"[:400], "messages": state.get("messages") or []}
        return
    r = _result(state)
    if r["error"]:
        yield {"type": "error", "error": r["error"], "messages": r["messages"]}
        return
    yield {"type": "done", **r}


# ---------- talk to the plan (PR 4) ----------
#
# load (code: parent plan + snapshot) -> interpret (LLM, JSON: request -> enforced constraints)
#   -> clarify? END | already satisfied? keep (code, no re-plan) | plan (ReAct from the current plan)
#   -> compare (code: cost vs the parent) -> explain
# A worse change is applied with a warning; the customer decides.

ADJUST_RECURSION_LIMIT = 30


class AdjustError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


class AdjustState(AgentState, total=False):
    request: str
    summary: str
    constraints: dict
    new_constraints: dict
    only_constraints: bool
    clarify: str | None
    fast: bool
    compare: dict | None
    cap_notes: list[str]
    direct: bool


def adjustable(pid: str) -> dict:
    """The proposal to adjust, or AdjustError (404 unknown, 409 not pending, 422 not a v2 plan)."""
    p = store.get_proposal(pid)
    if not p:
        raise AdjustError(404, "no such proposal")
    if p["status"] != "pending":
        raise AdjustError(409, f"proposal is {p['status']}, not pending; open the latest plan")
    if (p.get("plan") or {}).get("version") != 2:
        raise AdjustError(422, "proposal is a v1 plan; re-run the agent")
    return p


def _warning(cmp: dict) -> str | None:
    """The pushback line, built from the comparison in code (no LLM numbers)."""
    return ("Costs you: " + ", ".join(cmp["why"])) if cmp.get("worse") and cmp.get("why") else None


def _direct_propose(tools: list, parent_plan: dict, s: dict, w, st: dict) -> str | None:
    """A request the rules fully capture ("don't cut travel", "keep 4 months"): re-solve the previous plan under
    the new rules in code, no agent loop. propose_plan carries every untouched cut and the cushion forward."""
    prop = next((t for t in tools if t.name == "propose_plan"), None)
    if prop is None:
        return None
    agreed = [{"category": t["category"], "current": float(t.get("current") or t["target"]), "target": float(t["target"]),
               "proof_month": t.get("proof_month") or "", "how": list(t.get("how") or [])[:2], "source": "agreed"}
              for t in parent_plan.get("spending_targets") or [] if t.get("source") != "suggested"]
    args = {"monthly_debt_budget": float(parent_plan.get("monthly_debt_budget") or 0),
            "extra_order": list(parent_plan.get("extra_order") or []), "spending_targets": agreed,
            "keep_emergency_months": float(parent_plan.get("keep_emergency_months") or 3.0),
            "rationale": str(s.get("summary") or "Your change")[:80]}
    _phase(w, "propose", "Re-solving with your change")
    try:
        out = prop.invoke(args)
    except Exception as e:  # noqa: BLE001
        _phase(w, "propose", "", f"needs the agent: {str(e)[:60]}")
        return None
    if isinstance(out, dict) and out.get("saved"):
        _phase(w, "propose", "", "saved")
        return str(s.get("summary") or "")
    _phase(w, "propose", "", "; ".join((out or {}).get("reasons") or [])[:80] if isinstance(out, dict) else "not saved")
    return None


def build_adjust_graph(pid: str, request: str):
    from langgraph.graph import END, START, StateGraph

    from ..planning import wealth
    from . import constraints as C
    from .adjust import compare as compare_plans
    from .validator import PlanRejected, validate_plan_v2

    parent = adjustable(pid)
    cid = parent["customer_id"]
    llm = _llm()
    try:
        from .sql_tool import SCHEMA_DOC
    except ImportError:
        SCHEMA_DOC = "(sql tool not available)"
    old = parent.get("constraints") or {}

    def load(s: AdjustState) -> dict:
        w = _writer()
        _phase(w, "gather", "Reading your plan and accounts")
        snap = gather_snapshot(cid)
        _phase(w, "gather", "", f"{len(snap['debts'])} debts · current plan")
        return {"snapshot": snap}

    def interpret(s: AdjustState) -> dict:
        w = _writer()
        _phase(w, "interpret", "Understanding your request")
        spend = wealth.context(cid)["spend"]
        cats = ", ".join(f"{k} ({'essential' if v.get('essential') else f'${v['avg']:,.0f}/mo'})" for k, v in spend.items())
        data, e = _ask_json(_budgeted(llm, INTERPRET_REASONING_BUDGET), prompts.fill(prompts.INTERPRET, categories=cats, plan=parent["plan"],
                                              outcome=sim_brief(parent["simulation"]), constraints=old, request=request))
        if not isinstance(data, dict):
            data = {}
        new, dropped = C.normalize(data.get("constraints"))
        clarify = data.get("clarify") if isinstance(data.get("clarify"), str) and data["clarify"].strip() else None
        msgs = [*(s.get("messages") or []), {**e, "name": "interpret"}]
        if clarify and not new:
            _phase(w, "interpret", "", "needs a detail")
            w({"type": "clarify", "text": clarify})
            return {"clarify": clarify, "messages": msgs}
        new, capped = C.clamp_caps(new, spend)
        merged = C.merge(old, new)
        summary = str(data.get("summary") or request)[:80]
        only = bool(data.get("only_constraints")) and bool(new)
        fast = only and not C.check(parent["plan"], merged, spend)
        w({"type": "constraints", "constraints": merged, "labels": C.describe(merged), "added": C.describe(new), "dropped": dropped,
           "notes": capped})
        labels = [d["label"] for d in C.describe(new)]
        _phase(w, "interpret", "", (", ".join(labels) or summary)[:60])
        return {"constraints": merged, "new_constraints": new, "summary": summary, "only_constraints": only,
                "fast": fast, "messages": msgs, "cap_notes": capped}

    def keep(s: AdjustState) -> dict:
        """The plan already obeys the new rules: save it again under them, no re-plan."""
        w = _writer()
        _phase(w, "keep", "Checking your plan against it")
        try:
            sim = dict(validate_plan_v2(cid, parent["plan"], constraints=s["constraints"]))
        except (PlanRejected, ValueError, KeyError, TypeError):
            _phase(w, "keep", "", "needs changes")
            return {"fast": False}
        from ..data import balances

        sim["balances_snapshot"] = balances.snapshot(cid)
        try:
            p = store.create_proposal(cid, parent["plan"], sim, "Your plan already does this.", parent_id=pid,
                                      constraints=s["constraints"], request=request)
        except store.Conflict as ex:
            return {"fast": False, "error": str(ex)}
        _phase(w, "keep", "", "already fits")
        return {"proposal_id": p["id"]}

    def plan(s: AdjustState) -> dict:
        w = _writer()
        st: dict = {"parent_id": pid, "request": request}
        snap = s["snapshot"]
        system = prompts.fill(prompts.ADJUST, constraints=s.get("constraints") or "none", summary=s.get("summary") or request,
                              sql_schema=SCHEMA_DOC, plan=parent["plan"], outcome=sim_brief(parent["simulation"]),
                              snapshot=snap, assumptions=snap.get("assumptions") or {})
        messages = list(s.get("messages") or [])
        tools = make_tools(cid, st, s.get("constraints"))
        final = _direct_propose(tools, parent["plan"], s, w, st) if s.get("only_constraints") else None
        direct = bool(st.get("proposal_id"))
        if not st.get("proposal_id"):  # the request needs more than rules: the agent works it out with tools
            final = _react(llm, tools, system, request, w, st, messages, ADJUST_RECURSION_LIMIT)
        if st.get("cap_notes"):
            w({"type": "notes", "notes": st["cap_notes"]})
        out: dict = {"messages": messages, "final": final, "proposal_id": st.get("proposal_id"), "direct": direct}
        if not st.get("proposal_id"):
            reasons = "; ".join(st.get("last_reasons") or []) or "the agent did not propose a plan"
            out["error"] = f"No valid plan for that change: {reasons}"[:400]
        return out

    def compare(s: AdjustState) -> dict:
        w = _writer()
        child = store.get_proposal(s["proposal_id"])
        cmp = compare_plans(parent["simulation"], child["simulation"], parent["plan"], child["plan"])
        store.annotate(child["id"], compare=cmp, constraint_labels=C.describe(s.get("constraints")))
        w({"type": "compare", **cmp})
        w({"type": "saved", "proposal_id": child["id"], "compare": cmp})  # the new plan shows now; the headline follows
        return {"compare": cmp}

    def explain(s: AdjustState) -> dict:
        w = _writer()
        cmp = s.get("compare") or {}
        extra = {"warning": _warning(cmp), "request": request}
        if s.get("fast"):
            head = ("Already at the limit. " + "; ".join(s["cap_notes"])) if s.get("cap_notes") else \
                "No change needed: your plan already fits"
            data = {**(parent["simulation"].get("explain") or {}), "headline": head, **extra}
            store.annotate(s["proposal_id"], explain=data)
            w({"type": "final", "text": data["headline"], "why": ""})
            return {"explain": data, "final": data["headline"]}
        if s.get("direct"):  # rules covered it: the one-line result comes from the diff, no LLM
            child = store.get_proposal(s["proposal_id"])
            head = _gap_first(_diff_headline(s.get("new_constraints"), cmp, s.get("cap_notes")), child["simulation"])
            data = _carry_explain(parent["simulation"].get("explain"), child["plan"], head, extra)
            store.annotate(s["proposal_id"], explain=data)
            _phase(w, "explain", "", head[:60])
            w({"type": "final", "text": head, "why": ""})
            return {"explain": data, "final": head}
        return _explain(llm, w, s["proposal_id"], list(s.get("messages") or []), extra, request=request, cmp=cmp,
                        parent_explain=parent["simulation"].get("explain"))

    g = StateGraph(AdjustState)
    for name, fn in (("load", load), ("interpret", interpret), ("keep", keep), ("plan", plan), ("compare", compare), ("explain", explain)):
        g.add_node(name, fn)
    g.add_edge(START, "load")
    g.add_edge("load", "interpret")
    g.add_conditional_edges("interpret", lambda s: END if s.get("clarify") else "keep" if s.get("fast") else "plan",
                            {END: END, "keep": "keep", "plan": "plan"})
    g.add_conditional_edges("keep", lambda s: "compare" if s.get("proposal_id") else END if s.get("error") else "plan",
                            {"compare": "compare", "plan": "plan", END: END})
    g.add_conditional_edges("plan", lambda s: "compare" if s.get("proposal_id") else END, {"compare": "compare", END: END})
    g.add_edge("compare", "explain")
    g.add_edge("explain", END)
    return g.compile()


def stream_adjust(pid: str, request: str):
    """Like stream_run, for one adjustment. Extra events: `constraints` (the rules now in force), `clarify`
    (a question instead of a plan), `compare` (cost vs the previous plan). `done` carries proposal_id (the new
    pending proposal) or clarify."""
    request = (request or "").strip()
    if not request:
        yield {"type": "error", "error": "say what you'd like to change"}
        return
    try:
        graph = build_adjust_graph(pid, request[:500])
    except (AgentUnavailable, AdjustError) as e:
        yield {"type": "error", "error": getattr(e, "detail", str(e))}
        return
    state: dict = {"messages": []}
    try:
        for mode, chunk in graph.stream({"customer_id": "", "instruction": request, "messages": []},
                                        stream_mode=["custom", "values"]):
            if mode == "custom":
                yield chunk
            else:
                state = chunk
    except Exception as ex:  # noqa: BLE001
        yield {"type": "error", "error": f"{type(ex).__name__}: {ex}"[:400], "messages": state.get("messages") or []}
        return
    r = {**_result(state), "clarify": state.get("clarify"), "compare": state.get("compare"),
         "constraints": state.get("constraints")}
    if r["error"]:
        yield {"type": "error", "error": r["error"], "messages": r["messages"]}
        return
    yield {"type": "done", **r}
