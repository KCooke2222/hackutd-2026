"""Agent v2: a LangGraph StateGraph on Nemotron (OpenAI-compatible endpoint).

gather (code: full snapshot) -> diagnose (LLM, JSON) -> plan (ReAct with read-only tools + propose_plan)
-> explain (LLM, JSON). Validation lives in propose_plan (validate_plan_v2); after MAX_REJECTIONS rejections
the run stops with an error. Nothing here can move money.
"""

from __future__ import annotations

import json
import re
from typing import Any, TypedDict

from .. import balance_sheet, finance, store
from ..config import NEMOTRON_MODEL, NVIDIA_API_KEY, NVIDIA_BASE_URL
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
        from .. import insights

        cf = insights.cash_flow(customer_id)
        cash = {k: cf.get(k) for k in ("monthly_avg", "unknown_outflow_per_month", "essentials_by_category", "card_spending_by_category")}
        cash["last_3_months"] = (cf.get("months") or [])[-3:]
    except ImportError:  # insights not built yet
        r = finance.refill_breakdown(customer_id)
        cash = {"card_monthly_avg": r.get("monthly_avg"), "card_spending_by_category": r.get("new_spending_by_category")}
    try:
        from ..assumptions import ASSUMPTIONS
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
        "assumptions": ASSUMPTIONS,
    }


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
            return f"{_usd(d['unknown_outflow_per_month'])}/mo unknown out"
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
        "liquidity_ladder": "Cash ladder", "propose_plan": "Propose plan",
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


def _explain(llm, w, proposal_id: str, messages: list, extra: dict | None = None) -> dict:
    """The explain phase: short UI labels for a saved proposal, stored on it. Falls back to a code headline."""
    _phase(w, "explain", "Writing it up")
    p = store.get_proposal(proposal_id)
    data, e = _ask_json(llm, prompts.fill(prompts.EXPLAIN, plan=p["plan"], simulation=sim_brief(p["simulation"])))
    if not isinstance(data, dict) or not isinstance(data.get("headline"), str) or not data["headline"]:
        sim = p["simulation"]
        data = {"headline": f"High-cost debt $0 by {sim.get('high_cost_debt_free') or 'never'}", "levers": [], "cuts": []}
    data = {**data, **(extra or {})}
    try:
        store.annotate(p["id"], explain=data)
    except AttributeError:
        pass
    _phase(w, "explain", "", data["headline"][:60])
    w({"type": "final", "text": data["headline"], "why": ""})
    return {"explain": data, "final": data["headline"], "messages": [*messages, {**e, "name": "explain"}]}


def build_graph(customer_id: str):
    llm = _llm()
    from langgraph.graph import END, START, StateGraph

    try:
        from .sql_tool import SCHEMA_DOC
    except ImportError:
        SCHEMA_DOC = "(sql tool not available)"

    def gather(s: AgentState) -> dict:
        w = _writer()
        _phase(w, "gather", "Reading all accounts")
        snap = gather_snapshot(customer_id)
        _phase(w, "gather", "", f"{len(snap['debts'])} debts · {len(snap['assets'])} assets")
        return {"snapshot": snap}

    def diagnose(s: AgentState) -> dict:
        w = _writer()
        _phase(w, "diagnose", "Diagnosing")
        data, e = _ask_json(llm, prompts.fill(prompts.DIAGNOSE, snapshot=s["snapshot"]))
        if isinstance(data, list):
            data = {"causes": data}
        if not isinstance(data, dict):
            data = {"causes": [], "unparsed": e["content"][:400]}
        causes = [c for c in data.get("causes") or [] if isinstance(c, dict)]
        top = causes[0] if causes else {}
        try:
            res = f"{top.get('label', 'no causes')}" + (f" {_usd(top['amount_per_month'])}/mo" if top.get("amount_per_month") else "")
        except (TypeError, ValueError):
            res = str(top.get("label", "done"))
        _phase(w, "diagnose", "", res[:60])
        return {"diagnosis": data, "messages": [*(s.get("messages") or []), {**e, "name": "diagnose"}]}

    def plan(s: AgentState) -> dict:
        w = _writer()
        st: dict = {}
        keep = (s["snapshot"].get("assumptions") or {}).get("default_emergency_months", 3.0)
        system = prompts.fill(prompts.PLAN, keep_emergency_months=f"{keep:g}", sql_schema=SCHEMA_DOC,
                              snapshot=s["snapshot"], diagnosis=s.get("diagnosis") or {}, assumptions=s["snapshot"].get("assumptions") or {})
        messages = list(s.get("messages") or [])
        final = _react(llm, make_tools(customer_id, st), system, s.get("instruction") or DEFAULT_INSTRUCTION, w, st, messages)
        out: dict = {"messages": messages, "final": final, "proposal_id": st.get("proposal_id")}
        if not st.get("proposal_id"):
            reasons = "; ".join(st.get("last_reasons") or []) or "the agent did not propose a plan"
            out["error"] = f"No valid plan: {reasons}"[:400]
        return out

    def explain(s: AgentState) -> dict:
        return _explain(llm, _writer(), s["proposal_id"], list(s.get("messages") or []))

    g = StateGraph(AgentState)
    g.add_node("gather", gather)
    g.add_node("diagnose", diagnose)
    g.add_node("plan", plan)
    g.add_node("explain", explain)
    g.add_edge(START, "gather")
    g.add_edge("gather", "diagnose")
    g.add_edge("diagnose", "plan")
    g.add_conditional_edges("plan", lambda s: "explain" if s.get("proposal_id") else END, {"explain": "explain", END: END})
    g.add_edge("explain", END)
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


def build_adjust_graph(pid: str, request: str):
    from langgraph.graph import END, START, StateGraph

    from .. import wealth
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
        data, e = _ask_json(llm, prompts.fill(prompts.INTERPRET, categories=cats, plan=parent["plan"],
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
        merged = C.merge(old, new)
        summary = str(data.get("summary") or request)[:80]
        only = bool(data.get("only_constraints")) and bool(new)
        fast = only and not C.check(parent["plan"], merged, spend)
        w({"type": "constraints", "constraints": merged, "labels": C.describe(merged), "added": C.describe(new), "dropped": dropped})
        labels = [d["label"] for d in C.describe(new)]
        _phase(w, "interpret", "", (", ".join(labels) or summary)[:60])
        return {"constraints": merged, "new_constraints": new, "summary": summary, "only_constraints": only,
                "fast": fast, "messages": msgs}

    def keep(s: AdjustState) -> dict:
        """The plan already obeys the new rules: save it again under them, no re-plan."""
        w = _writer()
        _phase(w, "keep", "Checking your plan against it")
        try:
            sim = dict(validate_plan_v2(cid, parent["plan"], constraints=s["constraints"]))
        except (PlanRejected, ValueError, KeyError, TypeError):
            _phase(w, "keep", "", "needs changes")
            return {"fast": False}
        from .. import balances

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
        final = _react(llm, make_tools(cid, st, s.get("constraints")), system, request, w, st, messages, ADJUST_RECURSION_LIMIT)
        out: dict = {"messages": messages, "final": final, "proposal_id": st.get("proposal_id")}
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
        return {"compare": cmp}

    def explain(s: AdjustState) -> dict:
        w = _writer()
        cmp = s.get("compare") or {}
        extra = {"warning": _warning(cmp), "request": request}
        if s.get("fast"):
            data = {**(parent["simulation"].get("explain") or {}), "headline": "No change needed: your plan already fits", **extra}
            store.annotate(s["proposal_id"], explain=data)
            w({"type": "final", "text": data["headline"], "why": ""})
            return {"explain": data, "final": data["headline"]}
        return _explain(llm, w, s["proposal_id"], list(s.get("messages") or []), extra)

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
