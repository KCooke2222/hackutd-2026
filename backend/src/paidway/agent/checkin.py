"""Agent v3 monthly check-in: code advances a (demo) month and computes the status and fixes; one agent call
names the drift and picks a fix; code saves it as a PENDING proposal. Never approves or moves money.

    checkin(cid, scenario=None, prop=None) -> {"level", "facts", "drift", "pick", "why", "proposal_id", "tracking"}
"""

from __future__ import annotations

from ..data import store
from ..planning import tracking
from . import prompts
from .graph import _ask_json, _llm, _words


def checkin(cid: str, scenario: str | None = None, prop: dict | None = None) -> dict:
    prop = prop or tracking.approved_proposal(cid)
    if not prop:
        raise ValueError("no approved plan to check in on")
    t = tracking.step(cid, prop, scenario) if scenario else tracking.dashboard(cid, prop)
    st, alert = t["status"], (t["alerts"] or [None])[0]
    sw = t.get("switch")
    fixes = [{k: o.get(k) for k in ("key", "title", "feasible", "why", "extra_per_month", "projected_month",
                                    "new_month", "months_later", "net_worth_24m", "card_cap")} for o in (sw or {}).get("options") or []]
    facts = [*st["breaks"], *st["reasons"], *st["nudges"]] or ([alert["text"]] if alert else [])
    out = {"level": st["level"], "facts": facts, "fixes": fixes, "drift": "", "pick": None, "why": "", "proposal_id": None,
           "headline": None, "tracking": t}
    if st["level"] == "on_track" and not st["breaks"]:
        out["drift"] = "On plan this month."
        return out
    data, e = _ask_json(_llm(), prompts.fill(prompts.CHECKIN, status={k: st[k] for k in (
        "level", "late_months", "goal_month", "projected_month", "breaks", "reasons", "nudges")}, alert=alert, fixes=fixes or None))
    data = data if isinstance(data, dict) else {}
    out.update(drift=_words(data.get("drift"), 240), why=_words(data.get("why"), 200), reasoning=(e.get("reasoning") or "")[:2000])
    ok = {f["key"] for f in fixes if f.get("feasible")}
    pick = data.get("pick") if data.get("pick") in ok else (next(iter(sorted(ok)), None) if ok else None)
    if pick:
        new = tracking.choose(cid, prop, pick)  # a PENDING proposal; the customer approves it
        ex = {**(new["simulation"].get("explain") or {}), "driver": out["drift"], "summary": "; ".join(facts)[:300],
              "checkin_why": out["why"], "checkin_pick": pick}
        store.annotate(new["id"], explain=ex)
        out.update(pick=pick, proposal_id=new["id"], headline=ex.get("headline"))
    return out
