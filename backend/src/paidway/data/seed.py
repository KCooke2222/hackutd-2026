"""Seed Nessie from the PNC CSVs for a few demo customers.

    uv run python -m paidway.data.seed            # C001 C002 C003
    uv run python -m paidway.data.seed C005
    uv run python -m paidway.data.seed reseed     # DELETE all Nessie data for this key, then seed

Both also (re)load the linked external accounts into SQLite.

No-op without NESSIE_KEY. Nessie ids are saved to backend/data/nessie_ids.json (gitignored),
because Nessie data is per API key: the whole team should share one key and one seed.
"""

from __future__ import annotations

import json
import sys

from ..config import BACKEND_DIR, NESSIE_KEY
from . import pnc_data

IDS_PATH = BACKEND_DIR / "data" / "nessie_ids.json"
NESSIE_TYPES = {"checking": "Checking", "savings": "Savings", "credit_card": "Credit Card"}
DEFAULT_CUSTOMERS = ["C001", "C002", "C003"]
PURCHASE_MONTHS = 2


def _load() -> dict:
    return json.loads(IDS_PATH.read_text()) if IDS_PATH.exists() else {}


def nessie_ids(customer_id: str) -> dict:
    return _load().get(customer_id, {})


def load_linked() -> None:
    """Linked external accounts (student loans, brokerage, property, other cards) go into SQLite."""
    from . import linked

    print(f"loaded {linked.load()} linked accounts into SQLite")


def seed(customer_ids: list[str]) -> dict:
    load_linked()
    if not NESSIE_KEY:
        print("NESSIE_KEY not set; nothing to seed (app runs on local PNC CSVs).")
        return {}
    from ..nessie import read, write

    ids = _load()
    # Nessie can't delete customers, so reuse the "PNC <cid>" customer left by a previous seed.
    existing = {c.get("last_name"): c for c in read.customers() if c.get("first_name") == "PNC"}
    merchants = ids.setdefault("_merchants", {})
    for cid in customer_ids:
        if ids.get(cid, {}).get("complete"):
            print(f"{cid}: already seeded, skipping")
            continue
        cust = existing.get(cid) or write.create_customer("PNC", cid)["objectCreated"]
        entry = {"customer": cust["_id"]}
        for a in pnc_data.customer_accounts(cid):
            t = NESSIE_TYPES.get(a["account_type"])
            if not t:
                continue  # Nessie has no loan/mortgage account types
            acc = write.create_account(cust["_id"], t, f"{cid} {a['account_type']}", round(a["balance"]))["objectCreated"]
            entry[a["account_type"]] = acc["_id"]
            ids[cid] = entry
            IDS_PATH.write_text(json.dumps(ids, indent=2))  # save as we go: no orphans on a crash
        card = pnc_data.card_account(cid)
        if card and "credit_card" in entry:
            txs = pnc_data.customer_transactions(cid, account_id=card["account_id"], transaction_type="purchase")
            months = sorted({t["posted_date"][:7] for t in txs})[-PURCHASE_MONTHS:]
            for t in (t for t in txs if t["posted_date"][:7] in months):
                if t["category"] not in merchants:
                    merchants[t["category"]] = write.create_merchant(t["category"].title(), t["category"])["objectCreated"]["_id"]
                write.create_purchase(entry["credit_card"], merchants[t["category"]], round(abs(t["amount"]), 2), t["posted_date"], t["description"])
        entry["complete"] = True
        ids[cid] = entry
        IDS_PATH.write_text(json.dumps(ids, indent=2))
        print(f"{cid}: seeded {entry}")
    return ids


NESSIE_DATA_TYPES = ["Bills", "Deposits", "Loans", "Purchases", "Transfers", "Withdrawals", "Accounts", "Customers"]


def reseed(customer_ids: list[str]) -> dict:
    """Wipe everything this Nessie key owns, seed fresh, rewrite the PNC-id -> Nessie-id map.

    Nessie assigns new ids on every seed, so the map is always rebuilt from scratch.
    """
    if not NESSIE_KEY:
        print("NESSIE_KEY not set; nothing to reseed in Nessie (app runs on local PNC CSVs).")
        load_linked()
        return {}
    from ..nessie import read
    from ..nessie._http import NessieError, request

    try:
        for t in NESSIE_DATA_TYPES:
            request("DELETE", "/data", params={"type": t})
        print("deleted all Nessie data via DELETE /data")
    except NessieError as e:
        # api.nessieisreal.com has no DELETE /data and no customer delete: remove every account
        # (their purchases/transfers go with them) and keep the empty customers for reuse.
        print(f"DELETE /data unavailable ({e}); deleting accounts one by one")
        n = 0
        for c in read.customers():
            for a in read.customer_accounts(c["_id"]):
                request("DELETE", f"/accounts/{a['_id']}")
                n += 1
        print(f"deleted {n} accounts")
    if IDS_PATH.exists():
        IDS_PATH.unlink()
    return seed(customer_ids)


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "reseed":
        reseed(args[1:] or DEFAULT_CUSTOMERS)
    else:
        seed(args or DEFAULT_CUSTOMERS)
