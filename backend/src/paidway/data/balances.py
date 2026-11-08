"""Snapshot of the balances a proposal depends on, so approval can refuse stale plans."""

from __future__ import annotations

from ..nessie import read
from . import pnc_data


def snapshot(customer_id: str) -> dict:
    accs = {a["account_type"]: a for a in pnc_data.customer_accounts(customer_id)}
    snap = {
        "pnc": {k: round(accs[k]["balance"], 2) for k in ("credit_card", "savings") if k in accs},
        "nessie": None,
    }
    if read.enabled():
        from .seed import nessie_ids  # mapping only; seed's write import is lazy

        ids = nessie_ids(customer_id)
        if ids.get("credit_card") and ids.get("savings"):
            snap["nessie"] = {k: round(float(read.account(ids[k])["balance"]), 2) for k in ("credit_card", "savings")}
    return snap


def changed(old: dict | None, new: dict) -> list[str]:
    """Names of balances that differ (only compares sources present in both)."""
    if not old:
        return []
    diffs = []
    for src in ("pnc", "nessie"):
        a, b = old.get(src), new.get(src)
        if a is None or b is None:
            continue
        for k in a:
            if abs(a[k] - b.get(k, a[k])) > 0.005:
                diffs.append(f"{src}.{k}: {a[k]} -> {b.get(k)}")
    return diffs
