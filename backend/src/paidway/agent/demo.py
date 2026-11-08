"""Hidden demo mode: record every model call once, then replay it so a whole run takes seconds.

Only the LLM call is skipped. Everything around it runs for real: data reads, the solver, the validator, saved
PENDING proposals, and Nessie writes on approve. The cache sits under the chat model (`ReplayMixin._generate`),
so every call site (first run, chat Q&A, chat edits, check-in, Nessie 400 repair) goes through it unchanged.

Modes, per request:
    off     normal (default)
    record  call the model, save the response
    replay  serve the saved response after the time the live call took, scaled by DEMO_REPLAY_SPEED and capped at
            DEMO_REPLAY_CAP seconds (default 1.0 and 5.0), so the steps still show at a live pace; on a miss call the
            model for real and save it, and count the miss
Set by the `pw_demo` cookie (the frontend's hidden toggle; EventSource can't send headers, cookies it does) or an
`X-Paidway-Demo` header: 1/replay, record, 0/off. Server default: DEMO_REPLAY=1 (or =record) in .env. On the public
deploy (PAIDWAY_PUBLIC=1) visitors can't turn on record, and replay misses are kept in memory only.

Keys: sha256 of (call site, model, messages, bound kwargs such as tools and max_tokens), with dates, times and
ids normalized out so a rehearsal and the demo match. If that misses, a second key with digits and month names
also normalized out (but not in what the customer typed) finds the same call on slightly different balances or
another demo month; the closest recording by numbers wins. Any path recorded once replays: the key is the model
request itself, so a new screen or agent run (a replan) needs no code here, only a recording.
Recordings: data/demo_cache/<customer>.json, each entry with `ms` (how long the live call took).
scripts/demo_record.py records every demo path in parallel on isolated backends; scripts/demo_rehearse.py walks a
short script against a running one.
"""

from __future__ import annotations

import contextvars
import hashlib
import json
import os
import re
import sys
import threading
import time
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config import BACKEND_DIR

CACHE_DIR = Path(os.getenv("DEMO_CACHE_DIR") or BACKEND_DIR / "data" / "demo_cache")
PUBLIC = os.getenv("PAIDWAY_PUBLIC") == "1"
SPEED = float(os.getenv("DEMO_REPLAY_SPEED", "1.0"))  # replayed call = recorded time x SPEED (0: instant)...
CAP = float(os.getenv("DEMO_REPLAY_CAP", "5.0"))  # ...but never more than CAP seconds
UNTIMED_MS = 2500  # recordings from before `ms` was kept
_ENV = {"1": "replay", "replay": "replay", "record": "record"}.get(os.getenv("DEMO_REPLAY", "").strip().lower())

_mode: contextvars.ContextVar[str | None] = contextvars.ContextVar("demo_mode", default=None)
_customer: contextvars.ContextVar[str | None] = contextvars.ContextVar("demo_customer", default=None)

stats: Counter = Counter()  # hit, near, miss, recorded, live
_lock = threading.Lock()
_files: dict[str, dict] = {}


def mode() -> str | None:
    m = _mode.get()
    if m is None:
        m = _ENV
    return None if m == "off" else m


def parse(value: str | None) -> str | None:
    """Cookie/header value -> mode ("off" turns off a server default). Unknown values are ignored."""
    v = (value or "").strip().lower()
    m = {"1": "replay", "replay": "replay", "record": "record", "0": "off", "off": "off"}.get(v)
    return "replay" if (m == "record" and PUBLIC) else m


@contextmanager
def session(m: str | None, customer: str | None = None):
    """Run a block in a demo mode (scripts, tests). Threads started inside need contextvars.copy_context()."""
    t1, t2 = _mode.set(m or "off"), _customer.set(customer)
    try:
        yield
    finally:
        _mode.reset(t1)
        _customer.reset(t2)


# ---------- keys ----------

_NORM = [
    (re.compile(r"\b\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?\b"), "<date>"),
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"), "<uuid>"),
    (re.compile(r"\b(?=[0-9a-f]*[a-f])(?=[0-9a-f]*\d)[0-9a-f]{12,40}\b"), "<id>"),  # proposal/run ids, Nessie ids
    (re.compile(r"\b(?:call|chatcmpl|toolu|run)[-_][A-Za-z0-9_-]{6,}\b"), "<id>"),  # tool-call / completion ids
]
_DIGITS = re.compile(r"\d+(?:\.\d+)?")


def _norm(s: str) -> str:
    for pat, rep in _NORM:
        s = pat.sub(rep, s)
    return s


def _content(c: Any) -> str:
    return c if isinstance(c, str) else json.dumps(c, sort_keys=True, default=str)


_PKG = str(Path(__file__).resolve().parent.parent).replace("\\", "/") + "/"


def _site() -> str:
    """The first frame in our own code above the model: 'agent.graph:decide', 'api.plan_view:ask', ..."""
    f = sys._getframe(2)
    while f is not None:
        fn = f.f_code.co_filename.replace("\\", "/")
        # Our package dir only: on the VM the whole checkout sits under /opt/paidway/, venv included.
        if fn.startswith(_PKG) and not fn.endswith("/agent/demo.py"):
            mod = fn[len(_PKG):].removesuffix(".py").replace("/", ".")
            return f"{mod}:{f.f_code.co_name}"
        f = f.f_back
    return "?"


_MONTHS = re.compile(r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\b")
_REQUEST_LINE = re.compile(r"^.*\brequest:.*$", re.I | re.M)


def _loose(content: str) -> str:
    """For the near key: digits and month names out, except in what the customer asked (a short message, or a
    'Request:' line in a template), so "Keep 2 months" never stands in for "Keep 4 months"."""
    if len(content) < 400:
        return content
    keep = {f"\x00{chr(0xE000 + i)}\x00": m.group(0) for i, m in enumerate(_REQUEST_LINE.finditer(content))}
    for tok, line in keep.items():
        content = content.replace(line, tok, 1)
    content = _MONTHS.sub("<mon>", _DIGITS.sub("#", content))
    for tok, line in keep.items():
        content = content.replace(tok, line, 1)
    return content


def _material(model: str, messages: list, stop, kwargs: dict) -> tuple[str, str, str]:
    """(exact body, near body, bound kwargs), all normalized."""
    msgs = []
    for m in messages:
        e = {"type": getattr(m, "type", "?"), "content": _norm(_content(getattr(m, "content", "")))}
        tcs = getattr(m, "tool_calls", None)
        if tcs:
            e["tool_calls"] = [{"name": t.get("name"), "args": _norm(json.dumps(t.get("args"), sort_keys=True, default=str))} for t in tcs]
        msgs.append(e)
    kw = {k: v for k, v in kwargs.items() if k not in ("run_manager", "callbacks", "timeout")}
    kw_s = _norm(json.dumps(kw, sort_keys=True, default=str))
    loose = [{**e, "content": _loose(e["content"]),
              **({"tool_calls": [{**t, "args": _DIGITS.sub("#", t["args"])} for t in e["tool_calls"]]} if "tool_calls" in e else {})}
             for e in msgs]
    dump = lambda ms: json.dumps({"model": model, "stop": stop, "messages": ms}, sort_keys=True)  # noqa: E731
    return dump(msgs), dump(loose), kw_s


def _h(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()[:20]


# ---------- store ----------

def _path(customer: str) -> Path:
    return CACHE_DIR / f"{re.sub(r'[^A-Za-z0-9_-]', '_', customer)}.json"


def _load(customer: str) -> dict:
    if customer not in _files:
        p = _path(customer)
        try:
            _files[customer] = json.loads(p.read_text()) if p.exists() else {}
        except (OSError, ValueError):
            _files[customer] = {}
    return _files[customer]


def _save(customer: str) -> None:
    if PUBLIC:
        return  # a public deploy keeps its misses in memory: visitors never write files
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    p = _path(customer)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(_files[customer], indent=1, sort_keys=True, default=str))
    tmp.replace(p)


def _dump(result) -> dict:
    from langchain_core.messages import message_to_dict

    return {"generations": [{"message": message_to_dict(g.message), "info": g.generation_info} for g in result.generations],
            "llm_output": result.llm_output}


def _restore(d: dict):
    from langchain_core.messages import messages_from_dict
    from langchain_core.outputs import ChatGeneration, ChatResult

    gens = [ChatGeneration(message=messages_from_dict([g["message"]])[0], generation_info=g.get("info")) for g in d["generations"]]
    return ChatResult(generations=gens, llm_output=d.get("llm_output"))


def _nums(s: str) -> Counter:
    return Counter(_DIGITS.findall(s))


def _find(customer: str, key: str, near: str, text: str) -> tuple[dict | None, str]:
    with _lock:
        for c in dict.fromkeys([customer, "_misc"]):
            e = _load(c).get(key)
            if e:
                return e, "hit"
        best, score = None, -1
        want = _nums(text)
        for c in dict.fromkeys([customer, "_misc"]):
            for e in _load(c).values():
                if e.get("near") == near:
                    s = sum((want & _nums(e.get("text", ""))).values())
                    if s > score:
                        best, score = e, s
        return best, ("near" if best else "miss")


def _put(customer: str, key: str, entry: dict) -> None:
    with _lock:
        _load(customer)[key] = entry
        _save(customer)


def delay(entry: dict) -> float:
    """Seconds to wait before serving a recording: its live time, scaled and capped."""
    ms = entry.get("ms")
    return max(0.0, min(CAP, (UNTIMED_MS if ms is None else float(ms)) / 1000 * SPEED))


def merge(src: Path, dst: Path = CACHE_DIR) -> int:
    """Copy recordings from another cache dir (a recorder worker's) into `dst`; returns how many were new.
    An entry already in `dst` is kept unless the incoming one is newer."""
    added = 0
    dst.mkdir(parents=True, exist_ok=True)
    for f in sorted(src.glob("*.json")):
        try:
            inc = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        out = dst / f.name
        cur = json.loads(out.read_text()) if out.exists() else {}
        for k, e in inc.items():
            if k not in cur:
                added += 1
            if k not in cur or str(e.get("recorded_at", "")) > str(cur[k].get("recorded_at", "")):
                cur[k] = e
        tmp = out.with_suffix(".tmp")
        tmp.write_text(json.dumps(cur, indent=1, sort_keys=True, default=str))
        tmp.replace(out)
    if dst == CACHE_DIR:
        with _lock:
            _files.clear()
    return added


def _log(what: str, site: str, customer: str) -> None:
    stats[what] += 1
    print(f"[demo] {what:8} {customer:6} {site}", file=sys.stderr, flush=True)


# ---------- the hook ----------

class ReplayMixin:
    """Mix in before the chat model class: class NemotronChat(ReplayMixin, ChatOpenAI)."""

    def _should_stream(self, *args, **kwargs) -> bool:
        return False if mode() else super()._should_stream(*args, **kwargs)  # type: ignore[misc]

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        m = mode()
        if not m:
            stats["live"] += 1
            return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)  # type: ignore[misc]
        site, customer = _site(), _customer.get() or "_misc"
        body, loose, kw = _material(str(getattr(self, "model_name", "")), messages, stop, kwargs)
        key, near = _h(site, body, kw), _h(site, loose, kw)
        if m == "replay":
            e, how = _find(customer, key, near, body)
            if e:
                _log(how, site, customer)
                time.sleep(delay(e))
                return _restore(e["result"])
            _log("miss", site, customer)
        t0 = time.monotonic()
        result = super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)  # type: ignore[misc]
        _put(customer, key, {"site": site, "near": near, "text": body, "ms": round((time.monotonic() - t0) * 1000),
                             "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                             "result": _dump(result)})
        _log("recorded", site, customer)
        return result


# ---------- request scope ----------

_CID_PATH = re.compile(r"^/api/customers/([^/]+)/")
_PID_PATH = re.compile(r"^/api/proposals/([^/]+)/")


def _customer_for(path: str, query: str) -> str | None:
    m = _CID_PATH.match(path)
    if m:
        return m.group(1)
    from urllib.parse import parse_qs

    q = parse_qs(query)
    if q.get("customer_id"):
        return q["customer_id"][0]
    m = _PID_PATH.match(path)
    if m:
        try:
            from ..data import store

            p = store.get_proposal(m.group(1))
            return p and p.get("customer_id")
        except Exception:  # noqa: BLE001
            return None
    return None


class DemoMiddleware:
    """Pure ASGI (not BaseHTTPMiddleware) so the contextvars reach the endpoint, its threadpool and the stream."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        if path == "/api/_demo":  # counters for the rehearsal script and for checking a deploy; no UI uses it
            from starlette.responses import JSONResponse

            return await JSONResponse({"mode": mode(), "default": _ENV, "speed": SPEED, "cap_s": CAP,
                                       "stats": dict(stats), "recordings": {p.stem: len(_load(p.stem)) for p in CACHE_DIR.glob("*.json")}})(scope, receive, send)
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers") or []}
        raw = headers.get("x-paidway-demo")
        if raw is None:
            for part in headers.get("cookie", "").split(";"):
                k, _, v = part.strip().partition("=")
                if k == "pw_demo":
                    raw = v
        m = parse(raw)
        t1 = _mode.set(m) if m else None
        t2 = _customer.set(_customer_for(path, scope.get("query_string", b"").decode()))
        try:
            await self.app(scope, receive, send)
        finally:
            if t1 is not None:
                _mode.reset(t1)
            _customer.reset(t2)


def install(app) -> None:
    app.add_middleware(DemoMiddleware)
