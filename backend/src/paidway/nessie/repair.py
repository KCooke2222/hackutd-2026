"""Fix a Nessie request body from its 400/422 validation message (pure: no I/O).

This Nessie answers a bad body with pydantic's message, e.g.
    "3 validation errors for TransferCreate\\nstatus\\n  field required (type=value_error.missing)\\n
     medium\\n  extra fields not permitted (type=value_error.extra)"
`fix` reads it and returns a corrected body plus one note per change: drop extra fields, add a missing status,
date, description or medium with a default, make amounts whole dollars, pick an allowed enum value. It never
changes how much money moves (beyond rounding to whole dollars) and never invents an amount.
"""

from __future__ import annotations

import json
import re

MONEY = ("amount", "payment_amount")
PREFERRED = ("completed", "recurring", "balance", "pending")  # enum values to pick, best first


def errors(text: str) -> list[tuple[str, str]]:
    """[(field, message)] from a pydantic v1 validation message (the response body, maybe JSON-quoted)."""
    t = text.strip()
    try:
        t = json.loads(t) if t.startswith('"') else t
        if isinstance(t, dict):
            t = str(t.get("message") or t.get("detail") or t)
    except ValueError:
        t = t.strip('"').replace("\\n", "\n")
    out, field = [], None
    for line in str(t).replace("\\n", "\n").splitlines():
        if not line.strip() or re.match(r"^\d+ validation errors? for ", line):
            continue
        if line.startswith(" "):
            if field:
                out.append((field, line.strip()))
        else:
            field = line.strip().split(" -> ")[-1]
    return out


def _default(field: str, body: dict, today: str):
    if field == "status":
        return "recurring" if "recurring_date" in body else "completed"
    if field in ("transaction_date", "payment_date", "purchase_date"):
        return today
    if field == "description":
        return "Paidway"
    if field == "nickname":
        return "Paidway"
    if field == "medium":
        return "balance"
    return None


def fix(body: dict, text: str, today: str) -> tuple[dict | None, list[str]]:
    """(new body, notes), or (None, notes) when the message names nothing this can fix safely."""
    new, notes, unfixable = dict(body), [], False
    for field, msg in errors(text):
        m = msg.lower()
        if "extra fields not permitted" in m:
            if field in MONEY:
                unfixable = True
                continue
            if field in new:
                new.pop(field)
                notes.append(f"dropped {field}")
        elif "field required" in m:
            v = _default(field, new, today)
            if v is None:
                unfixable = True
                continue
            new[field] = v
            notes.append(f"added {field}={v}")
        elif "integer" in m and field in new:
            try:
                v = float(new[field])
            except (TypeError, ValueError):
                unfixable = True
                continue
            new[field] = int(v + 0.5) if v >= 0 else -int(-v + 0.5)
            notes.append(f"{field} {new[field]} as whole dollars")
        elif "permitted:" in m:
            allowed = re.findall(r"'([^']+)'", msg.split("permitted:", 1)[1].split("(")[0])
            pick = next((a for a in PREFERRED if a in allowed), allowed[0] if allowed else None)
            if pick is None or field in MONEY:
                unfixable = True
                continue
            new[field] = pick
            notes.append(f"{field}={pick}")
        elif "date" in m and field in new:
            new[field] = today
            notes.append(f"{field}={today}")
        else:
            unfixable = True
    if new == body:
        return None, notes
    return (None if unfixable and not notes else new), notes


def safe(original: dict, candidate: dict) -> bool:
    """A corrected body (e.g. from the model) may not change how much money moves, beyond whole dollars."""
    if not isinstance(candidate, dict):
        return False
    for k in MONEY:
        if k in original:
            try:
                if abs(float(candidate.get(k, original[k])) - float(original[k])) > 0.5:
                    return False
            except (TypeError, ValueError):
                return False
    return True
