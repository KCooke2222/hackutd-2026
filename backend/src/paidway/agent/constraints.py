"""Customer constraints for "talk to the plan" (PR 4).

A request like "don't touch savings" becomes a structured rule the validator enforces, so the model can't
quietly ignore it. Constraints accumulate across adjustments and are stored on the proposal.

Shape (every key optional):
    forbid_sources:       ["cash" | "cd" | "brokerage"]  no lump sum from these
    min_emergency_months: float                           plan keeps at least this many months of cash
    protect_categories:   [category]                      no spending cut in these
    category_max:         {category: $/mo}                spending target at most this ("cut dining to $200")
    category_min:         {category: $/mo}                spending target at least this ("not below $300")
    max_redirect:         $/mo                            total redirects at most this
    min_debt_budget:      $/mo                            monthly_debt_budget at least this
    max_debt_budget:      $/mo                            monthly_debt_budget at most this
"""

from __future__ import annotations

SOURCES = ("cash", "cd", "brokerage")
SOURCE_NAME = {"cash": "savings", "cd": "CDs", "brokerage": "brokerage"}
LISTS = ("forbid_sources", "protect_categories")
MAPS = ("category_max", "category_min")
NUMBERS = ("min_emergency_months", "max_redirect", "min_debt_budget", "max_debt_budget")
KEYS = LISTS + MAPS + NUMBERS


def _num(v) -> float | None:
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    return n if n >= 0 else None


def normalize(raw) -> tuple[dict, list[str]]:
    """Clean a model- or user-supplied constraints dict. Returns (constraints, dropped) where dropped lists
    what was ignored and why, so the caller can tell the customer."""
    out: dict = {}
    dropped: list[str] = []
    if not isinstance(raw, dict):
        return out, ([] if raw in (None, {}) else ["constraints must be an object"])
    for k, v in raw.items():
        if k not in KEYS:
            dropped.append(f"unknown constraint {k!r}")
        elif k == "forbid_sources":
            vals = [str(x).lower() for x in (v if isinstance(v, list) else [v])]
            bad = [x for x in vals if x not in SOURCES]
            dropped += [f"forbid_sources: unknown source {x!r}" for x in bad]
            if good := sorted({x for x in vals if x in SOURCES}):
                out[k] = good
        elif k == "protect_categories":
            vals = sorted({str(x).strip().lower() for x in (v if isinstance(v, list) else [v]) if str(x).strip()})
            if vals:
                out[k] = vals
        elif k in MAPS:
            if not isinstance(v, dict):
                dropped.append(f"{k} must map category to $/mo")
                continue
            m = {}
            for cat, amt in v.items():
                n = _num(amt)
                if n is None:
                    dropped.append(f"{k}.{cat}: not a non-negative number")
                else:
                    m[str(cat).strip().lower()] = round(n, 2)
            if m:
                out[k] = m
        else:
            n = _num(v)
            if n is None:
                dropped.append(f"{k}: not a non-negative number")
            else:
                out[k] = round(n, 2)
    return out, dropped


def merge(old: dict | None, new: dict | None) -> dict:
    """Earlier constraints plus new ones. Lists union; maps and numbers: the newer value wins.
    Protecting a category drops an older category_max for it, and vice versa (latest request wins)."""
    a, b = dict(old or {}), dict(new or {})
    out = {k: (list(v) if isinstance(v, list) else dict(v) if isinstance(v, dict) else v) for k, v in a.items()}
    for k, v in b.items():
        if k in LISTS:
            out[k] = sorted(set(out.get(k, [])) | set(v))
        elif k in MAPS:
            out[k] = {**out.get(k, {}), **v}
        else:
            out[k] = v
    for cat in b.get("protect_categories", []):
        out.get("category_max", {}).pop(cat, None)
    for cat in b.get("category_max", {}):
        if cat in out.get("protect_categories", []):
            out["protect_categories"] = [c for c in out["protect_categories"] if c != cat]
    return {k: v for k, v in out.items() if v not in ([], {}, None)}


def check(plan: dict, constraints: dict | None, spend: dict | None = None) -> list[str]:
    """Reasons the plan breaks the customer's constraints (empty list = fine). `spend` is wealth.context()['spend']
    ({category: {"avg": ...}}), used when the plan has no target for a capped category."""
    c = constraints or {}
    if not c:
        return []
    out: list[str] = []
    said = "you said"

    for s in c.get("forbid_sources", []):
        if any(str(ls.get("source", "")).lower() == s for ls in plan.get("lump_sums") or []):
            out.append(f"uses {SOURCE_NAME[s]}, which {said} not to touch")

    if (m := c.get("min_emergency_months")) is not None:
        keep = float(plan.get("keep_emergency_months") or 0)
        if keep < m - 1e-9:
            out.append(f"keeps {keep:g} months of cash; {said} at least {m:g}")

    targets = {str(t.get("category", "")).lower(): t for t in plan.get("spending_targets") or []}
    spend = spend or {}

    def level(cat: str) -> float | None:
        t = targets.get(cat)
        if t is not None:
            try:
                return float(t.get("target"))
            except (TypeError, ValueError):
                return None
        avg = (spend.get(cat) or {}).get("avg")
        return float(avg) if avg is not None else None

    def current(cat: str) -> float | None:
        avg = (spend.get(cat) or {}).get("avg")
        if avg is not None:
            return float(avg)
        t = targets.get(cat)
        return float(t["current"]) if t and t.get("current") is not None else None

    for cat in c.get("protect_categories", []):
        lv, cur = level(cat), current(cat)
        if cat in targets and lv is not None and cur is not None and lv < cur - 0.5:
            out.append(f"cuts {cat}, which {said} not to cut")
    for cat, cap in c.get("category_max", {}).items():
        lv = level(cat)
        if lv is not None and lv > cap + 0.5:
            out.append(f"{cat} target ${lv:,.0f}/mo; {said} you can get it to ${cap:,.0f}")
    for cat, floor in c.get("category_min", {}).items():
        lv = level(cat)
        if lv is not None and lv < floor - 0.5:
            out.append(f"{cat} target ${lv:,.0f}/mo; {said} no lower than ${floor:,.0f}")

    if (cap := c.get("max_redirect")) is not None:
        red = sum(float(r.get("amount_per_month") or 0) for r in plan.get("redirects") or [])
        if red > cap + 0.5:
            out.append(f"redirects ${red:,.0f}/mo; {said} at most ${cap:,.0f}")

    budget = float(plan.get("monthly_debt_budget") or 0)
    if (lo := c.get("min_debt_budget")) is not None and budget < lo - 0.5:
        out.append(f"pays ${budget:,.0f}/mo to debt; {said} at least ${lo:,.0f}")
    if (hi := c.get("max_debt_budget")) is not None and budget > hi + 0.5:
        out.append(f"pays ${budget:,.0f}/mo to debt; {said} at most ${hi:,.0f}")
    return out


def describe(constraints: dict | None) -> list[dict]:
    """Short labels for the UI tags: [{"key", "item", "label"}]. `item` identifies list/map entries for removal."""
    c = constraints or {}
    out: list[dict] = []
    for s in c.get("forbid_sources", []):
        out.append({"key": "forbid_sources", "item": s, "label": f"Don't use {SOURCE_NAME[s]}"})
    if (m := c.get("min_emergency_months")) is not None:
        out.append({"key": "min_emergency_months", "item": None, "label": f"Keep {m:g}+ months cash"})
    for cat in c.get("protect_categories", []):
        out.append({"key": "protect_categories", "item": cat, "label": f"Don't cut {cat.replace('_', ' ')}"})
    for cat, v in c.get("category_max", {}).items():
        out.append({"key": "category_max", "item": cat, "label": f"{cat.replace('_', ' ').capitalize()} ≤ ${v:,.0f}/mo"})
    for cat, v in c.get("category_min", {}).items():
        out.append({"key": "category_min", "item": cat, "label": f"{cat.replace('_', ' ').capitalize()} ≥ ${v:,.0f}/mo"})
    if (v := c.get("max_redirect")) is not None:
        out.append({"key": "max_redirect", "item": None, "label": f"Redirect ≤ ${v:,.0f}/mo"})
    if (v := c.get("min_debt_budget")) is not None:
        out.append({"key": "min_debt_budget", "item": None, "label": f"Debt ≥ ${v:,.0f}/mo"})
    if (v := c.get("max_debt_budget")) is not None:
        out.append({"key": "max_debt_budget", "item": None, "label": f"Debt ≤ ${v:,.0f}/mo"})
    return out


def remove(constraints: dict | None, key: str, item: str | None = None) -> dict:
    """Drop one constraint (a whole key, or one entry of a list/map)."""
    c = merge(constraints, None)
    if key in LISTS and item is not None:
        c[key] = [x for x in c.get(key, []) if x != item]
    elif key in MAPS and item is not None:
        c.get(key, {}).pop(item, None)
    else:
        c.pop(key, None)
    return {k: v for k, v in c.items() if v not in ([], {}, None)}


SOLVER_KEYS = ("forbid_sources", "min_debt_budget", "max_debt_budget", "max_redirect")


def solver_inputs(constraints: dict | None, keep_months: float | None, targets: list[dict] | None,
                  spend: dict | None = None) -> dict:
    """The customer's rules as solver.solve() inputs, so the solved plan obeys them by construction
    (check() then runs on it as a safety net):
        min_emergency_months -> keep_emergency_months (at least)
        protect_categories   -> no spending target there, and declined_cuts (the solver suggests none there)
        category_max / _min  -> clamp the chosen targets; a cap below the current average adds that cut;
                                category_min also floors the solver's suggested cuts
        targets tagged source="suggested" are skipped: the solver remakes its own suggestions
        forbid_sources, min/max_debt_budget, max_redirect -> passed through to the solver
    `spend` is wealth.context()['spend'], used to skip caps the customer already meets."""
    c = constraints or {}
    keep = keep_months
    if (m := c.get("min_emergency_months")) is not None:
        keep = max(float(keep if keep is not None else 0.0), float(m))
    protect = set(c.get("protect_categories") or [])
    cmax, cmin = c.get("category_max") or {}, c.get("category_min") or {}
    out, seen = [], set()
    for t in targets or []:
        cat = str(t.get("category", "")).lower()
        if cat in protect or t.get("source") == "suggested":
            continue
        target = float(t["target"])
        if cat in cmax:
            target = min(target, cmax[cat])
        if cat in cmin:
            target = max(target, cmin[cat])
        avg = ((spend or {}).get(cat) or {}).get("avg")
        if avg is not None and target >= float(avg) - 0.5:
            continue  # clamped back to no cut
        seen.add(cat)
        out.append({**t, "category": cat, "target": round(target, 2)})
    for cat, cap in cmax.items():
        avg = ((spend or {}).get(cat) or {}).get("avg")
        if cat not in seen and cat not in protect and (avg is None or cap < float(avg) - 0.5):
            out.append({"category": cat, "target": max(cap, cmin.get(cat, 0.0))})
    res = {"keep_emergency_months": keep, "spending_targets": out, **{k: c[k] for k in SOLVER_KEYS if k in c}}
    if protect:
        res["declined_cuts"] = sorted(protect)
    if cmin:
        res["category_min"] = dict(cmin)
    return res


def clamp_caps(new: dict | None, spend: dict | None) -> tuple[dict, list[str]]:
    """Raise any category_max that would cut deeper than the category allows (its max cut %, or the customer's
    own lowest month) up to that floor, so a "cut X more" request becomes the deepest allowed cut instead of
    a rejected plan. Returns (constraints, notes); each note is one short line for the chat."""
    import math

    from ..data import categories
    from ..planning import assumptions as A

    c = dict(new or {})
    caps = dict(c.get("category_max") or {})
    notes: list[str] = []
    for cat, cap in list(caps.items()):
        s = (spend or {}).get(cat) or {}
        avg = s.get("avg")
        flag = categories.spending_flag(cat)
        if avg is None or flag["flag"] in (categories.CANCEL, categories.ASK, categories.FIXED):
            continue
        pct = float(flag.get("max_pct") or A.get("max_cut_pct"))
        floor = max(math.ceil(float(avg) * (1 - pct / 100)), math.ceil(float(s.get("min") or 0)))
        if cap < floor - 0.5:
            caps[cat] = float(floor)
            name = cat.replace('_', ' ').capitalize()
            notes.append(f"{name} capped at {pct:g}%, ${floor:,.0f}/mo: the most one category is cut"
                         if floor == math.ceil(float(avg) * (1 - pct / 100)) else
                         f"{name} capped at ${floor:,.0f}/mo: your lowest month")
    if caps:
        c["category_max"] = caps
    return c, notes
