"""Nessie calls that change data. Import only from the approve endpoint and seed.py.

The agent must never import this module (enforced by tests/test_safety.py).
"""

from __future__ import annotations

from ._http import request


def create_customer(first_name: str, last_name: str) -> dict:
    body = {
        "first_name": first_name,
        "last_name": last_name,
        "address": {"street_number": "1", "street_name": "Main St", "city": "Richardson", "state": "TX", "zip": "75080"},
    }
    return request("POST", "/customers", json=body)


def create_account(customer_id: str, type_: str, nickname: str, balance: int) -> dict:
    body = {"type": type_, "nickname": nickname, "rewards": 0, "balance": balance}
    return request("POST", f"/customers/{customer_id}/accounts", json=body)


def create_purchase(account_id: str, merchant_id: str, amount: float, date: str, description: str) -> dict:
    body = {"merchant_id": merchant_id, "medium": "balance", "purchase_date": date, "amount": amount, "description": description}
    return request("POST", f"/accounts/{account_id}/purchases", json=body)


def create_deposit(account_id: str, amount: float, date: str, description: str) -> dict:
    body = {"medium": "balance", "transaction_date": date, "amount": amount, "description": description}
    return request("POST", f"/accounts/{account_id}/deposits", json=body)


def create_transfer(from_account_id: str, to_account_id: str, amount: float, date: str, description: str) -> dict:
    body = {"medium": "balance", "payee_id": to_account_id, "transaction_date": date, "amount": amount, "description": description}
    return request("POST", f"/accounts/{from_account_id}/transfers", json=body)


def create_merchant(name: str, category: str) -> dict:
    body = {
        "name": name,
        "category": category,
        "address": {"street_number": "1", "street_name": "Main St", "city": "Richardson", "state": "TX", "zip": "75080"},
        "geocode": {"lat": 32.99, "lng": -96.75},
    }
    return request("POST", "/merchants", json=body)
