"""Seed Nessie from the PNC CSVs for a few demo customers.

    uv run python -m paidway.data.seed            # C001 C027 (the seeded demo customers)
    uv run python -m paidway.data.seed C005
    uv run python -m paidway.data.seed reseed     # DELETE all Nessie data for this key, then seed

Both also (re)load the PNC CSVs (profiles, accounts, transactions, monthly_statements) and the linked
external accounts into SQLite.

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
DEFAULT_CUSTOMERS = ["C001", "C027"]  # the seeded demo customers
PURCHASE_MONTHS = 2


def _load() -> dict:
    return json.loads(IDS_PATH.read_text()) if IDS_PATH.exists() else {}


def nessie_ids(customer_id: str) -> dict:
    return _load().get(customer_id, {})


def load_linked() -> None:
    """Linked external accounts (student loans, brokerage, property, other cards) go into SQLite."""
    from . import linked

    print(f"loaded {linked.load()} linked accounts into SQLite")


def load_pnc() -> None:
    """The PNC CSVs go into SQLite tables, dropped and reloaded (see pnc_data.TABLES)."""
    counts = pnc_data.load_into_db(force=True)
    print("loaded PNC data into SQLite: " + ", ".join(f"{t} {n}" for t, n in counts.items()))


# Every account has a Nessie mirror. Nessie has only Checking / Savings / Credit Card accounts, so assets it
# lacks (CD, IRA, brokerage) are Savings-type and every debt (cards, loans) is Credit Card-type, nicknamed after
# the account. APRs, minimums and cost basis stay in our own data. Property is not mirrored: no plan moves it.
PRIMARY = {"A-{cid}-CHECKING": "checking", "A-{cid}-SAVINGS": "savings", "A-{cid}-CREDIT_CARD": "credit_card"}
NOT_MIRRORED = {"property"}
LOAN_RECORD = {"mortgage": "home", "auto_loan": "auto"}  # Nessie's loan types: also attach a loan record


def mirror_type(account_type: str, side: str) -> str:
    return NESSIE_TYPES.get(account_type) or ("Credit Card" if side == "debt" else "Savings")


def mirror_nickname(cid: str, row: dict) -> str:
    """The primary PNC accounts keep the seed's old nickname ("C001 savings"); the rest are named after the account."""
    key = PRIMARY.get(row["account_id"].replace(cid, "{cid}"))
    return f"{cid} {key}" if key else ("Brokerage" if row["account_type"] == "brokerage" else row["name"])


def mirror_plan(cid: str) -> list[dict]:
    """The Nessie accounts a seed creates for a customer, one per account in our data (pure)."""
    from ..planning import balance_sheet

    balance_sheet.clear_cache()
    return [{"account_id": r["account_id"], "type": mirror_type(r["account_type"], r["side"]),
             "nickname": mirror_nickname(cid, r), "balance": round(float(r["balance"]), 2),
             "loan": LOAN_RECORD.get(r["account_type"]), "min_payment": r.get("min_payment")}
            for r in balance_sheet.accounts(cid, live=False) if r["account_type"] not in NOT_MIRRORED]


def _create_mirror(write, nessie_customer: str, m: dict) -> dict:
    """Create one mirror with its exact balance in cents; fall back to whole dollars if Nessie refuses cents."""
    from ..nessie._http import NessieError

    try:
        acc = write.create_account(nessie_customer, m["type"], m["nickname"], m["balance"])["objectCreated"]
    except NessieError:
        acc = write.create_account(nessie_customer, m["type"], m["nickname"], round(m["balance"]))["objectCreated"]
    out = {"id": acc["_id"], "type": m["type"], "nickname": m["nickname"], "balance_sent": m["balance"],
           "balance_nessie": acc.get("balance")}
    if m["loan"]:  # the loan record is extra detail on the mirror; the mirror's balance is what the app reads
        try:
            res = write.create_loan(acc["_id"], m["loan"], m["balance"], m["min_payment"] or 0, m["nickname"])
            out["loan_id"] = ((res or {}).get("objectCreated") or {}).get("_id")
        except NessieError as e:
            out["loan_error"] = str(e)[:200]
    return out


def seed(customer_ids: list[str], reload_local: bool = True) -> dict:
    """Mirror every account of each customer into Nessie (adds missing mirrors to customers seeded before)."""
    if reload_local:
        load_pnc()
        load_linked()
    if not NESSIE_KEY:
        print("NESSIE_KEY not set; nothing to seed (app runs on local PNC CSVs).")
        return {}
    from ..nessie import read, write

    ids = _load()
    existing = None
    merchants = ids.setdefault("_merchants", {})
    for cid in customer_ids:
        entry = ids.get(cid) or {}
        if entry.get("complete") and entry.get("mirrors_complete"):
            print(f"{cid}: already seeded, skipping")
            continue
        if not entry.get("customer"):  # Nessie can't delete customers: reuse "PNC <cid>" from an earlier seed
            if existing is None:
                existing = {c.get("last_name"): c for c in read.customers() if c.get("first_name") == "PNC"}
            entry["customer"] = (existing.get(cid) or write.create_customer("PNC", cid)["objectCreated"])["_id"]
        accounts = entry.setdefault("accounts", {})
        for k, aid in PRIMARY.items():  # accounts an older seed made: adopt them
            if entry.get(aid) and k.format(cid=cid) not in accounts:
                accounts[k.format(cid=cid)] = {"id": entry[aid], "type": NESSIE_TYPES[aid], "nickname": f"{cid} {aid}"}
        new_card = False
        for m in mirror_plan(cid):
            if m["account_id"] in accounts:
                continue
            accounts[m["account_id"]] = _create_mirror(write, entry["customer"], m)
            key = PRIMARY.get(m["account_id"].replace(cid, "{cid}"))
            if key:
                entry[key] = accounts[m["account_id"]]["id"]
                new_card |= key == "credit_card"
            ids[cid] = entry
            IDS_PATH.write_text(json.dumps(ids, indent=2))  # save as we go: no orphans on a crash
        card = pnc_data.card_account(cid)
        if card and new_card:
            txs = pnc_data.customer_transactions(cid, account_id=card["account_id"], transaction_type="purchase")
            months = sorted({t["posted_date"][:7] for t in txs})[-PURCHASE_MONTHS:]
            for t in (t for t in txs if t["posted_date"][:7] in months):
                if t["category"] not in merchants:
                    merchants[t["category"]] = write.create_merchant(t["category"].title(), t["category"])["objectCreated"]["_id"]
                write.create_purchase(entry["credit_card"], merchants[t["category"]], round(abs(t["amount"]), 2), t["posted_date"], t["description"])
        entry["complete"] = entry["mirrors_complete"] = True
        ids[cid] = entry
        IDS_PATH.write_text(json.dumps(ids, indent=2))
        print(f"{cid}: {len(accounts)} accounts mirrored in Nessie")
    return ids


NESSIE_DATA_TYPES = ["Bills", "Deposits", "Loans", "Purchases", "Transfers", "Withdrawals", "Accounts", "Customers"]


def reseed(customer_ids: list[str]) -> dict:
    """Wipe everything this Nessie key owns, seed fresh, rewrite the PNC-id -> Nessie-id map.

    Nessie assigns new ids on every seed, so the map is always rebuilt from scratch.
    """
    if not NESSIE_KEY:
        print("NESSIE_KEY not set; nothing to reseed in Nessie (app runs on local PNC CSVs).")
        load_pnc()
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


def reset_customer(customer_id: str) -> dict:
    """Demo reset for one customer: undo what approve did so the demo can run again.

    Deletes every Nessie mirror of the customer (their transfers, withdrawals and bills go with them), mirrors
    every account again from our data (same Nessie customer, new account ids, balances as in the data) and
    restores the customer's linked balances locally. Local proposals are left alone (`make reset-local`)."""
    from . import linked, store

    store.replace_linked_for(customer_id, linked.generate_customer(customer_id))
    if not NESSIE_KEY:
        return {"customer": customer_id, "nessie": "NESSIE_KEY not set; linked balances restored only"}
    from ..nessie import write

    ids = _load()
    entry = ids.get(customer_id) or {}
    nids = {m["id"] for m in (entry.get("accounts") or {}).values()} | {entry[k] for k in NESSIE_TYPES if entry.get(k)}
    for nid in sorted(nids):
        write.delete_account(nid)
    ids[customer_id] = {"customer": entry["customer"]} if entry.get("customer") else {}
    IDS_PATH.write_text(json.dumps(ids, indent=2))
    fresh = seed([customer_id], reload_local=False).get(customer_id, {})
    return {"customer": customer_id, "deleted_accounts": len(nids),
            "mirrors": {k: {"id": v["id"], "nickname": v["nickname"], "balance": v.get("balance_nessie")}
                        for k, v in (fresh.get("accounts") or {}).items()}}


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "reset":
        for c in args[1:] or ["C001"]:
            print(reset_customer(c))
    elif args and args[0] == "reseed":
        reseed(args[1:] or DEFAULT_CUSTOMERS)
    else:
        seed(args or DEFAULT_CUSTOMERS)
