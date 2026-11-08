"""Public-demo guard: per-IP rate limits on the calls that cost model tokens or write to Nessie, and an
activity stamp the idle reset reads. Off unless PAIDWAY_PUBLIC=1, so local dev is unchanged."""

from __future__ import annotations

import os
import re
import threading
import time
from collections import defaultdict, deque
from pathlib import Path

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

PUBLIC = os.getenv("PAIDWAY_PUBLIC") == "1"
ACTIVITY_FILE = Path(os.getenv("PAIDWAY_ACTIVITY_FILE", "/tmp/paidway-activity"))
WINDOW = 600  # seconds

# (method, path pattern) -> (per-IP limit, all-visitors limit) per 10 minutes
LIMITS = {
    "run": (re.compile(r"^(GET /api/agent/stream|POST /api/agent/run)$"), 6, 40),
    "chat": (re.compile(r"^(GET /api/proposals/[^/]+/adjust/stream|POST /api/proposals/[^/]+/adjust)$"), 20, 120),
    "approve": (re.compile(r"^POST /api/proposals/[^/]+/(approve|retry)$"), 6, 30),
}

_hits: dict[tuple[str, str], deque] = defaultdict(deque)
_lock = threading.Lock()


def _ok(key: tuple[str, str], limit: int, now: float) -> bool:
    q = _hits[key]
    while q and q[0] < now - WINDOW:
        q.popleft()
    if len(q) >= limit:
        return False
    q.append(now)
    return True


def _client_ip(request) -> str:
    # Caddy replaces X-Forwarded-For from untrusted clients with their real address, so the first entry is real.
    fwd = request.headers.get("x-forwarded-for", "")
    return fwd.split(",")[0].strip() or (request.client.host if request.client else "?")


class Guard(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        sig = f"{request.method} {request.url.path}"
        writes = request.method != "GET" or sig.endswith("/stream") and ("/agent/stream" in sig or "/adjust/" in sig)
        if writes:
            try:
                ACTIVITY_FILE.touch()
            except OSError:
                pass
        for name, (pat, per_ip, total) in LIMITS.items():
            if pat.match(sig):
                now = time.time()
                with _lock:
                    if not _ok((name, _client_ip(request)), per_ip, now):
                        return JSONResponse({"detail": "Demo limit reached, try again in a few minutes."}, 429)
                    if not _ok((name, "*"), total, now):
                        return JSONResponse({"detail": "The demo is busy, try again in a few minutes."}, 429)
                break
        return await call_next(request)


def install(app) -> None:
    if PUBLIC:
        app.add_middleware(Guard)
