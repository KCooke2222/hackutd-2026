"""Nessie calls that change data. Import only from the approve endpoint and seed.py.

The agent must never import this module (enforced by tests/test_safety.py).
"""

from __future__ import annotations

from . import calls
from ._http import request


def create_customer(first_name: str, last_name: str) -> dict:
    body = {
        "first_name": first_name,
        "last_name": last_name,
        "address": {"street_number": "1", "street_name": "Main St", "city": "Richardson", "state": "TX", "zip": "75080"},
    }
    return request("POST", "/customers", json=body)


def create_account(customer_id: str, type_: str, nickname: str, balance: float) -> dict:
    body = {"type": type_, "nickname": nickname, "rewards": 0, "balance": balance}
    return request("POST", f"/customers/{customer_id}/accounts", json=body)


def create_purchase(account_id: str, merchant_id: str, amount: float, date: str, description: str) -> dict:
    body = {"merchant_id": merchant_id, "medium": "balance", "purchase_date": date, "amount": amount, "description": description}
    return request("POST", f"/accounts/{account_id}/purchases", json=body)


def create_deposit(account_id: str, amount: float, date: str, description: str) -> dict:
    return send(calls.deposit(account_id, amount, date, description))


def send(call: dict) -> dict:
    """Send one request built by nessie.calls."""
    return request(call["method"], call["path"], json=call.get("body"))


def create_transfer(from_account_id: str, amount: float, date: str, description: str) -> dict:
    """A transfer out of `from_account_id` (this Nessie's transfers have no destination; see calls.py)."""
    return send(calls.transfer(from_account_id, amount, date, description))


def create_merchant(name: str, category: str) -> dict:
    body = {
        "name": name,
        "category": category,
        "address": {"street_number": "1", "street_name": "Main St", "city": "Richardson", "state": "TX", "zip": "75080"},
        "geocode": {"lat": 32.99, "lng": -96.75},
    }
    return request("POST", "/merchants", json=body)


def create_withdrawal(account_id: str, amount: float, date: str, description: str) -> dict:
    return send(calls.withdrawal(account_id, amount, date, description))


def create_bill(account_id: str, payee: str, nickname: str, payment_date: str, recurring_date: int, amount: float) -> dict:
    return send(calls.bill(account_id, payee, nickname, payment_date, recurring_date, amount))


def delete_account(account_id: str) -> None:
    request("DELETE", f"/accounts/{account_id}")


def create_loan(account_id: str, type_: str, amount: float, monthly_payment: float, description: str) -> dict:
    """A Nessie loan record (types home / auto / small business) on a loan's Credit Card-type mirror."""
    body = {"type": type_, "status": "approved", "credit_score": 700, "monthly_payment": round(monthly_payment, 2),
            "amount": round(amount, 2), "description": description}
    return request("POST", f"/accounts/{account_id}/loans", json=body)


def update_bill(bill_id: str, amount: float) -> dict:
    return send(calls.update_bill(bill_id, amount))


def delete_bill(bill_id: str) -> dict:
    return send(calls.delete_bill(bill_id))
