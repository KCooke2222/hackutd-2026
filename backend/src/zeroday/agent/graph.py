"""LangGraph ReAct agent on Nemotron (OpenAI-compatible endpoint)."""

from __future__ import annotations

from ..config import NEMOTRON_MODEL, NVIDIA_API_KEY, NVIDIA_BASE_URL
from .tools import make_tools

SYSTEM = """You are Zeroday, a proactive credit-card payoff agent for a PNC customer.
Goal: find out why the card balance isn't going down and propose ONE plan to reach $0.

Rules:
- Use tools for every number. Never do arithmetic yourself.
- Start with refill_breakdown and get_accounts. Compare 2-3 options with simulate_payoff.
- Keep at least 3 months of expenses in savings (more if the customer asks).
- Finish by calling propose_plan exactly once. You cannot move money; the customer approves.
- Then reply in 3-5 short sentences: why the balance is stuck, the plan, the $0 date change.
This is not financial advice; describe tradeoffs, not orders."""


class AgentUnavailable(RuntimeError):
    pass


def build_agent(customer_id: str):
    if not NVIDIA_API_KEY:
        raise AgentUnavailable("NVIDIA_API_KEY is not set. Get one at build.nvidia.com and add it to .env")
    from langchain_openai import ChatOpenAI
    from langgraph.prebuilt import create_react_agent

    llm = ChatOpenAI(model=NEMOTRON_MODEL, base_url=NVIDIA_BASE_URL, api_key=NVIDIA_API_KEY, temperature=0.2)
    return create_react_agent(llm, make_tools(customer_id), prompt=SYSTEM)


def run(customer_id: str, instruction: str | None = None) -> dict:
    agent = build_agent(customer_id)
    msg = instruction or "Run the weekly check on my credit card and propose a plan."
    result = agent.invoke({"messages": [("user", msg)]}, {"recursion_limit": 25})
    messages = []
    for m in result["messages"]:
        entry = {"role": m.type, "content": m.content if isinstance(m.content, str) else str(m.content)}
        if getattr(m, "tool_calls", None):
            entry["tool_calls"] = [{"name": c["name"], "args": c["args"]} for c in m.tool_calls]
        if m.type == "tool":
            entry["name"] = getattr(m, "name", None)
        messages.append(entry)
    return {"messages": messages, "final": messages[-1]["content"] if messages else ""}
