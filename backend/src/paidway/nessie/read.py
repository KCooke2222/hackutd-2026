"""Read-only Nessie calls (GET only)."""

from __future__ import annotations

from ._http import enabled, request

__all__ = ["enabled", "customers", "customer_accounts", "account", "purchases", "deposits", "withdrawals", "transfers", "bills", "ledger_balance"]


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


def withdrawals(account_id: str) -> list[dict]:
    return request("GET", f"/accounts/{account_id}/withdrawals")


def _list(fn, account_id: str) -> list[dict]:
    try:
        return fn(account_id) or []
    except Exception as e:  # noqa: BLE001 - this Nessie answers 404 for an empty transfer list
        if "404" in str(e):
            return []
        raise


def ledger_balance(account_id: str, debt: bool, raw: float | None = None) -> float:
    """The account's balance after Paidway's own Nessie transactions (descriptions starting with calls.TAG).

    This Nessie stores transfers, deposits and withdrawals but never posts them to the balance, so the raw
    balance stays at what the account was created with. An asset loses its transfers and withdrawals and gains
    its deposits; a debt (Credit Card mirror, balance owed) is lowered by its deposits (payments)."""
    from concurrent.futures import ThreadPoolExecutor

    from .calls import TAG

    with ThreadPoolExecutor(4) as ex:
        fut_raw = ex.submit(account, account_id) if raw is None else None
        dep, wd, tr = (ex.submit(_list, f, account_id) for f in (deposits, withdrawals, transfers))
        base = float(fut_raw.result()["balance"]) if fut_raw else float(raw)
        mine = lambda xs: sum(float(x.get("amount") or 0) for x in xs if str(x.get("description") or "").startswith(TAG))  # noqa: E731
        d, w, t = mine(dep.result()), mine(wd.result()), mine(tr.result())
    return round(base - d + w if debt else base + d - w - t, 2)
