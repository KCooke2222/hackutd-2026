"""LangGraph ReAct agent on Nemotron (OpenAI-compatible endpoint)."""

from __future__ import annotations

from ..config import NEMOTRON_MODEL, NVIDIA_API_KEY, NVIDIA_BASE_URL
from .tools import make_tools

SYSTEM = """You are Zeroday, a proactive credit-card payoff agent for a PNC customer.
Goal: find out why the card balance isn't going down and propose ONE plan to reach $0.

Rules:
- Use tools for every number. Never do arithmetic yourself.
- Start with refill_breakdown, get_balance_sheet and debt_priority.
- Raise a lump sum the cheapest way, using liquidity_ladder: cash above the emergency buffer first, then CDs,
  then brokerage. Only draw from a tier when its one-time cost is well below the card interest it saves.
  Never propose retirement (IRA) or property; you may mention them.
- Respect debt_priority: never suggest prepaying secured or low-rate debt ahead of the card. If another debt has
  a higher APR than the PNC card, say so in the rationale.
- Spending cap: essentials (refill_breakdown categories marked essential, e.g. groceries, transport) stay whole;
  trim discretionary categories (dining, shopping) by 30-50%. Never cap below essentials, never $0.
- Payment: keep the current monthly payment (or more) until $0, never below the card minimum, never $0.
  Set autopay_full=true: after $0 the full statement is paid each payday, so the card stays at $0 and
  the old payment minus the new spending is freed for savings.
- Compare 2-3 options with simulate_payoff.
- Keep at least 3 months of expenses in cash (more if the customer asks).
- Finish by calling propose_plan exactly once. You cannot move money; the customer approves.
- Then reply with ONE sentence of at most 10 words: why the card is stuck and the fix.
- Keep the propose_plan rationale to one sentence of at most 15 words.
This is not financial advice; describe tradeoffs, not orders."""


class AgentUnavailable(RuntimeError):
    pass


def build_agent(customer_id: str):
    if not NVIDIA_API_KEY:
        raise AgentUnavailable("NVIDIA_API_KEY is not set. Get one at build.nvidia.com and add it to .env")
    from langchain_openai import ChatOpenAI
    from langgraph.prebuilt import create_react_agent

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

    llm = NemotronChat(model=NEMOTRON_MODEL, base_url=NVIDIA_BASE_URL, api_key=NVIDIA_API_KEY, temperature=0.2)
    return create_react_agent(llm, make_tools(customer_id), prompt=SYSTEM)


DEFAULT_INSTRUCTION = "Run the weekly check on my credit card and propose a plan."


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


def run(customer_id: str, instruction: str | None = None) -> dict:
    agent = build_agent(customer_id)
    result = agent.invoke({"messages": [("user", instruction or DEFAULT_INSTRUCTION)]}, {"recursion_limit": 25})
    messages = [_entry(m) for m in result["messages"]]
    return {"messages": messages, "final": messages[-1]["content"] if messages else ""}


def _parse(content: str):
    import ast
    import json

    for load in (json.loads, ast.literal_eval):
        try:
            return load(content)
        except (ValueError, SyntaxError, TypeError):
            pass
    return None


def _usd(n) -> str:
    return f"${float(n):,.0f}"


def _months(n: int) -> str:
    return "now" if n == 0 else f"{n} mo"


def summarize(name: str, args: dict, content: str) -> str:
    """One number per tool result, for the thinking chips."""
    d = _parse(content)
    if not isinstance(d, (dict, list)):
        return "done"
    try:
        if isinstance(d, dict) and d.get("error"):
            return "refused"
        if name == "refill_breakdown":
            return f"{d['share_of_payment_refilled'] * 100:.0f}% refilled"
        if name == "get_balance_sheet":
            return f"net worth {_usd(d['totals']['net_worth'])}"
        if name == "get_accounts":
            return f"{len(d['accounts'])} accounts"
        if name == "get_card_activity":
            return f"{len(d)} entries"
        if name == "debt_priority":
            top = next((x for x in d["debts"] if x.get("extra_rank") == 1), None)
            return f"#1 {top['name']} {top['apr_pct']:g}%" if top else f"{len(d['debts'])} debts"
        if name == "liquidity_ladder":
            avail = sum(t["available"] for t in d["tiers"] if t["proposable"])
            return f"{_usd(avail)} tappable"
        if name == "simulate_payoff":
            out = _months(d["months_to_zero"]) if d["pays_off"] else "never"
            out = "$0 " + out if d["pays_off"] else out
            return out + (f" · cost {_usd(d['tap_cost_total'])}" if d.get("tap_cost_total") else "")
        if name == "propose_plan":
            return "saved" if d.get("saved") else "refused"
    except (KeyError, TypeError, StopIteration):
        pass
    return "done"


def _label(name: str, args: dict) -> str:
    if name == "simulate_payoff":
        lump = sum(float(args.get(k) or 0) for k in ("lump_sum_from_savings", "lump_sum_from_cd", "lump_sum_from_brokerage"))
        bits = [f"pay {_usd(args.get('monthly_payment', 0))}", f"spend {_usd(args.get('monthly_card_spending', 0))}"]
        if lump:
            bits.insert(0, f"lump {_usd(lump)}")
        return "Simulate " + " · ".join(bits)
    return {
        "refill_breakdown": "Refill breakdown", "get_balance_sheet": "Balance sheet", "get_accounts": "Accounts",
        "get_card_activity": "Card activity", "debt_priority": "Debt ranking", "liquidity_ladder": "Cash ladder",
        "propose_plan": "Propose plan",
    }.get(name, name)


def stream_run(customer_id: str, instruction: str | None = None):
    """Yield UI events as the agent works: `call` (tool starts), `result` (one-number summary), `done`."""
    try:
        agent = build_agent(customer_id)
    except AgentUnavailable as e:
        yield {"type": "error", "error": str(e)}
        return
    messages = [{"role": "human", "content": instruction or DEFAULT_INSTRUCTION}]
    pending: dict[str, dict] = {}
    try:
        for update in agent.stream({"messages": [("user", messages[0]["content"])]}, {"recursion_limit": 25}, stream_mode="updates"):
            for node in update.values():
                for m in (node or {}).get("messages", []):
                    e = _entry(m)
                    messages.append(e)
                    if m.type == "ai":
                        why = e.get("reasoning") or e["content"]
                        for c in getattr(m, "tool_calls", None) or []:
                            pending[c["id"]] = c
                            yield {"type": "call", "id": c["id"], "name": c["name"], "label": _label(c["name"], c["args"]), "args": c["args"], "why": why}
                            why = ""
                        if not m.tool_calls:
                            yield {"type": "final", "text": e["content"], "why": e.get("reasoning", "")}
                    elif m.type == "tool":
                        c = pending.get(m.tool_call_id, {})
                        yield {"type": "result", "id": m.tool_call_id, "name": e["name"], "result": summarize(e["name"], c.get("args", {}), e["content"])}
    except Exception as ex:  # noqa: BLE001
        yield {"type": "error", "error": f"{type(ex).__name__}: {ex}"[:400], "messages": messages}
        return
    yield {"type": "done", "final": messages[-1]["content"] if messages else "", "messages": messages}
