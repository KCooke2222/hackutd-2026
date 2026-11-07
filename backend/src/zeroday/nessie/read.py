"""Read-only Nessie calls (GET only)."""

from __future__ import annotations

from ._http import enabled, request

__all__ = ["enabled", "customers", "customer_accounts", "account", "purchases", "deposits", "transfers", "bills"]


def customers() -> list[dict]:
    return request("GET", "/customers")


def customer_accounts(customer_id: str) -> list[dict]:
    return request("GET", f"/customers/{customer_id}/accounts")


def account(account_id: str) -> dict:
    return request("GET", f"/accounts/{account_id}")


def purchases(account_id: str) -> list[dict]:
    return request("GET", f"/accounts/{account_id}/purchases")


def deposits(account_id: str) -> list[dict]:
    return request("GET", f"/accounts/{account_id}/deposits")


def transfers(account_id: str) -> list[dict]:
    return request("GET", f"/accounts/{account_id}/transfers")


def bills(account_id: str) -> list[dict]:
    return request("GET", f"/accounts/{account_id}/bills")
