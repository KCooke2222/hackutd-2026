"""The exact Nessie write requests, as data (pure: builds {method, path, body}, sends nothing).

`write` sends these; the approve preview shows them, so what the customer sees before approving is
byte-for-byte what approve sends.

This Nessie (api.nessieisreal.com) validates strictly (extra fields are rejected) and takes whole-dollar
integer amounts. A transfer has no destination field: it is recorded on the account it leaves. So a payoff is
two calls, a transfer out of the source account and a deposit (a payment) on the debt's mirror. Nessie stores
both but does not post them to the account balance; `read.ledger_balance` adds Paidway's own transactions.
"""

from __future__ import annotations

TAG = "Paidway"  # every description starts with it: how read.ledger_balance finds our transactions


def dollars(x: float) -> int:
    """Whole dollars, half up (Nessie amounts are integers)."""
    return int(float(x) + 0.5) if x >= 0 else -int(-float(x) + 0.5)


def transfer(from_account_id: str, amount: float, date: str, description: str) -> dict:
    return {"method": "POST", "path": f"/accounts/{from_account_id}/transfers",
            "body": {"transaction_date": date, "status": "completed", "amount": dollars(amount), "description": description}}


def deposit(account_id: str, amount: float, date: str, description: str) -> dict:
    return {"method": "POST", "path": f"/accounts/{account_id}/deposits",
            "body": {"medium": "balance", "transaction_date": date, "status": "completed", "amount": dollars(amount),
                     "description": description}}


def withdrawal(account_id: str, amount: float, date: str, description: str) -> dict:
    return {"method": "POST", "path": f"/accounts/{account_id}/withdrawals",
            "body": {"medium": "balance", "transaction_date": date, "status": "completed", "amount": dollars(amount),
                     "description": description}}


def bill(account_id: str, payee: str, nickname: str, payment_date: str, recurring_date: int, amount: float) -> dict:
    return {"method": "POST", "path": f"/accounts/{account_id}/bills",
            "body": {"status": "recurring", "payee": payee, "nickname": nickname, "payment_date": payment_date,
                     "recurring_date": recurring_date, "payment_amount": dollars(amount)}}
