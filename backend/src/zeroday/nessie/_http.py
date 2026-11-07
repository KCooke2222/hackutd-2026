from __future__ import annotations

from typing import Any

import httpx

from ..config import NESSIE_BASE_URL, NESSIE_KEY


class NessieUnavailable(RuntimeError):
    pass


class NessieError(RuntimeError):
    """HTTP error from Nessie, with the API key scrubbed (httpx puts it in the URL)."""


def enabled() -> bool:
    return bool(NESSIE_KEY)


def request(method: str, path: str, json: Any | None = None, params: dict | None = None) -> Any:
    if not NESSIE_KEY:
        raise NessieUnavailable("NESSIE_KEY is not set; using local PNC data only")
    q = {"key": NESSIE_KEY, **(params or {})}
    try:
        r = httpx.request(method, f"{NESSIE_BASE_URL}{path}", params=q, json=json, timeout=20)
    except httpx.HTTPError as e:
        raise NessieError(f"{method} {path}: {type(e).__name__}") from None
    if r.is_error:
        raise NessieError(f"{method} {path} -> {r.status_code}: {r.text[:200]}")
    return r.json() if r.content else None
