"""The customer specs (data/customers.yaml): one readable description per customer that
scripts/gen_data.py turns into every PNC CSV. zeroday.linked reads its linked accounts from here too.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

from .config import CUSTOMERS_SPEC


def read(path: Path | str) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


@lru_cache(maxsize=None)
def load() -> dict:
    """The spec at CUSTOMERS_SPEC, or {} if there is none (fresh checkout without the file)."""
    path = Path(CUSTOMERS_SPEC)
    return read(path) if path.exists() else {}


def customer(customer_id: str) -> dict | None:
    return (load().get("customers") or {}).get(customer_id)
