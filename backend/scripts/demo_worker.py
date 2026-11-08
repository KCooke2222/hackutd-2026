"""One isolated backend for the demo recorder (scripts/demo_record.py). Not for the live demo.

    PAIDWAY_DB=<copy> PAIDWAY_NESSIE_IDS=<copy> DEMO_CACHE_DIR=<dir> uv run python scripts/demo_worker.py --port 8761 --fresh C001

- Nessie writes never leave this process: every POST/PUT/DELETE gets a fake "created" answer (logged as
  `[nessie dry-run]`), and a later GET of the same path lists what was "created", so approve, its bills and the
  check-ins behave as after a real approve. Nessie reads (GET) go to the live API, so the prompts the model sees
  match the live demo's.
- --fresh <customer>: before serving, clear that customer's proposals, runs, demo months and applied options in
  the DB copy and restore their linked balances, so every recorded path starts where the demo starts.
- Model calls: logs each NVIDIA HTTP status (`[llm] 200`, `[llm] 429`) to stderr for the recorder's rate-limit
  count. Nessie URLs (they carry the key) are never logged.
"""

from __future__ import annotations

import argparse
import itertools
import logging
import sys
import threading

from paidway.config import NESSIE_KEY
from paidway.nessie import _http

_real = _http.request
_made: dict[str, list] = {}
_ids = itertools.count(1)
_lock = threading.Lock()


def _dry(method: str, path: str, json=None, params=None):
    if method.upper() == "GET":
        got = _real(method, path, json=json, params=params)
        with _lock:
            extra = list(_made.get(path, ()))
        return (got + extra) if isinstance(got, list) and extra else got
    if not NESSIE_KEY:
        return _real(method, path, json=json, params=params)  # same "not set" error as without the recorder
    oid = f"dryrun{next(_ids):018x}"
    obj = {"_id": oid, **(json or {})}
    if method.upper() == "POST":
        with _lock:
            _made.setdefault(path, []).append(obj)
    print(f"[nessie dry-run] {method} {path}", file=sys.stderr, flush=True)
    return {"code": 201, "message": "Created (dry run, not sent)", "objectCreated": obj}


_http.request = _dry
from paidway.nessie import read, write  # noqa: E402

write.request = _dry  # bound by name at import
read.request = _dry


class _LLMStatus(logging.Filter):
    """httpx logs one line per request; keep only the model's, as `[llm] <status>`."""

    def filter(self, rec: logging.LogRecord) -> bool:
        args = rec.args if isinstance(rec.args, tuple) else ()
        url = str(args[1]) if len(args) > 1 else ""
        if "nvidia" not in url and "/chat/completions" not in url:
            return False
        status = args[3] if len(args) > 3 else "?"
        rec.msg, rec.args = "[llm] %s", (status,)
        return True


def _log_llm() -> None:
    h = logging.StreamHandler(sys.stderr)
    h.addFilter(_LLMStatus())
    for name in ("httpx", "httpx2"):  # openai 3 sends through httpx2
        lg = logging.getLogger(name)
        lg.setLevel(logging.INFO)
        lg.addHandler(h)
        lg.propagate = False


def fresh(cid: str) -> None:
    from paidway.data import linked, store

    with store._conn() as c:
        for table in ("proposals", "agent_runs", "demo_months", "applied_options"):
            try:
                c.execute(f"delete from {table} where customer_id=?", (cid,))
            except Exception:  # noqa: BLE001 - a table this DB doesn't have yet
                pass
    store.replace_linked_for(cid, linked.generate_customer(cid))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--fresh", nargs="*", default=[])
    a = ap.parse_args()
    for c in a.fresh:
        fresh(c)
    import uvicorn

    from paidway.api.main import app

    _log_llm()  # after the app's imports, and uvicorn's own logging config is skipped, so nothing resets it
    uvicorn.run(app, host="127.0.0.1", port=a.port, log_level="warning", log_config=None)


if __name__ == "__main__":
    main()
