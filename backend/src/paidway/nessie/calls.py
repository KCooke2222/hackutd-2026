"""The exact Nessie write requests, as data (pure: builds {method, path, body}, sends nothing).

`write` sends these; the approve preview shows them, so what the customer sees before approving is
byte-for-byte what approve sends.
"""

from __future__ import annotations


def transfer(from_account_id: str, to_account_id: str, amount: float, date: str, description: str) -> dict:
    return {"method": "POST", "path": f"/accounts/{from_account_id}/transfers",
            "body": {"medium": "balance", "payee_id": to_account_id, "transaction_date": date, "amount": amount,
                     "description": description}}


def withdrawal(account_id: str, amount: float, date: str, description: str) -> dict:
    return {"method": "POST", "path": f"/accounts/{account_id}/withdrawals",
            "body": {"medium": "balance", "transaction_date": date, "amount": amount, "description": description}}


def bill(account_id: str, payee: str, nickname: str, payment_date: str, recurring_date: int, amount: float) -> dict:
    return {"method": "POST", "path": f"/accounts/{account_id}/bills",
            "body": {"status": "recurring", "payee": payee, "nickname": nickname, "payment_date": payment_date,
                     "recurring_date": recurring_date, "payment_amount": amount}}
