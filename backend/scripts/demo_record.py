"""Record every demo path for demo mode (agent/demo.py), in parallel, on isolated backends.

    cd backend && uv run python scripts/demo_record.py                       # full: C001 + C027, 4 at a time
    cd backend && uv run python scripts/demo_record.py --parallel 6
    cd backend && uv run python scripts/demo_record.py --smoke               # 7 C001 paths, proves the setup

A path is a list of UI actions from a fresh start (first run, a chat chip, approve, a check-in, a replan...).
Paths form a tree: a node's children are what the screen it ends on offers (the chips of the plan it made, the
check-in scenarios after approve, ...), so they are found as the recording goes. Each path runs on its own
backend (scripts/demo_worker.py): a copy of the DB with the customer reset to the demo start, a copy of the
Nessie id map, its own port, Nessie writes faked (approve sends nothing), Nessie reads live. The backend replays
what is already recorded instantly and calls the model only for what isn't, so a child pays only for its last
step. Each finished path's new recordings are merged into data/demo_cache/.

At the end it prints the path count, model calls and time per kind of step, the wall-clock estimate for the
whole tree at --parallel, and every HTTP 429 the model API returned (its rate limit).

To add a screen (e.g. Replan as its own agent run): add a step kind in `do()` and offer it in `children()`.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path

import httpx

from paidway.agent import demo
from paidway.config import BACKEND_DIR, DB_PATH, NESSIE_IDS_PATH, REPO_DIR

# What the chat shows (frontend components/flow/Chat.tsx): QUESTIONS with no plan, the first with one, and
# the chips suggest() picks for a plan. ASK is the money concept the demo asks about (its followup: try IDR).
QUESTIONS = ["What's driving my debt?", "How much do I owe in total?", "Which debt should go first?"]
ASK = "what is income driven repayment"
FALLBACK = ["Finish sooner", "Lower my monthly payment", "Keep 6 months of cash"]
REPLAN_AFTER = {"income_drop"}  # check-ins whose story continues with a replan
SMOKE = 7


def title(s: str) -> str:
    s = s.replace("_", " ")
    return s[:1].upper() + s[1:]


def suggest(p: dict) -> list[str]:
    """Chat.tsx suggest(): 3-4 changes that fit this plan."""
    pl, sim = p.get("plan") or {}, p.get("simulation") or {}
    out: list[str] = []
    src = {x.get("source") for x in pl.get("lump_sums") or []}
    if "cash" in src:
        out.append("Don't touch my savings")
    if "brokerage" in src:
        out.append("Don't sell my investments")
    if "cd" in src:
        out.append("Leave my CD alone")
    if pl.get("redirects"):
        out.append("Keep investing every month")
    cuts = sorted(pl.get("spending_targets") or [], key=lambda t: -((t.get("current") or 0) - (t.get("target") or 0)))
    if cuts:
        out.append(f"Don't cut {title(cuts[0]['category']).lower()}")
    if len(cuts) > 1:
        out.append(f"I can cut {title(cuts[1]['category']).lower()} more")
    debts = [d for d in sim.get("debts") or [] if (d.get("balance") or 0) > 0]
    top = max(debts, key=lambda d: d.get("apr_pct") or 0, default=None)
    if top and (pl.get("extra_order") or [None])[0] != top.get("account_id"):
        out.append(f"Pay {top['name']} off first")
    late = max((d for d in debts if d.get("payoff_month") is None), key=lambda d: d.get("apr_pct") or 0, default=None)
    if late and late is not top:
        out.append(f"Pay off {late['name']} within 2 years")
    out += FALLBACK
    rules = {r.get("label", "").lower() for r in sim.get("constraint_labels") or []}
    return [x for x in dict.fromkeys(out) if x.lower() not in rules][:4]


# ---------- one backend ----------

class Worker:
    """A backend on its own port and its own copies; started fresh for every path."""

    def __init__(self, slot: int, port: int, root: Path, pristine: Path, ids: Path, env: dict):
        self.slot, self.port, self.root, self.pristine, self.ids, self.env = slot, port, root, pristine, ids, env
        self.proc: subprocess.Popen | None = None

    def start(self, cid: str, tag: str) -> tuple[httpx.Client, Path, Path]:
        d = self.root / f"w{self.slot}" / tag
        if d.exists():
            shutil.rmtree(d)
        (d / "cache").mkdir(parents=True)
        shutil.copy(self.pristine, d / "db.sqlite3")
        shutil.copy(self.ids, d / "nessie_ids.json")
        for f in demo.CACHE_DIR.glob("*.json"):
            shutil.copy(f, d / "cache" / f.name)
        env = {**self.env, "PAIDWAY_DB": str(d / "db.sqlite3"), "PAIDWAY_NESSIE_IDS": str(d / "nessie_ids.json"),
               "DEMO_CACHE_DIR": str(d / "cache"), "DEMO_REPLAY": "1", "DEMO_REPLAY_SPEED": "0", "PAIDWAY_PUBLIC": "0"}
        log = d / "backend.log"
        self.proc = subprocess.Popen([sys.executable, str(BACKEND_DIR / "scripts" / "demo_worker.py"), "--port", str(self.port), "--fresh", cid],
                                     cwd=BACKEND_DIR, env=env, stdout=log.open("w"), stderr=subprocess.STDOUT)
        c = httpx.Client(base_url=f"http://127.0.0.1:{self.port}", timeout=900)
        for _ in range(240):
            if self.proc.poll() is not None:
                raise RuntimeError(f"backend exited, see {log}")
            try:
                if c.get("/api/health").status_code == 200:
                    return c, d, log
            except httpx.HTTPError:
                pass
            time.sleep(0.25)
        raise RuntimeError(f"backend didn't start, see {log}")

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None


def sse(c: httpx.Client, url: str, **params) -> dict:
    """Read an SSE stream to `done` / `error`; returns that event."""
    with c.stream("GET", url, params=params) as r:
        r.raise_for_status()
        event = None
        for line in r.iter_lines():
            if line.startswith("event: "):
                event = line[7:]
            elif line.startswith("data: ") and event in ("done", "error"):
                return {"type": event, **json.loads(line[6:])}
    return {"type": "error", "error": "stream ended"}


def _pid(ev: dict) -> str | None:
    return ev.get("proposal_id") or (ev.get("proposal") or {}).get("id")


# ---------- steps ----------

def label(step: dict) -> str:
    k = step["do"]
    arg = step.get("q") or step.get("msg") or step.get("scenario") or ",".join(step.get("keys") or []) or step.get("key") or ""
    return f"{k}({arg})" if arg else k


def do(c: httpx.Client, cid: str, step: dict, st: dict) -> None:
    """Run one UI action. `st` carries the plan being looked at (pid, proposal) and the last answers."""
    k = step["do"]
    if k in ("run", "replan"):  # Find a plan; the dashboard's Replan is the same full agent run today
        ev = sse(c, "/api/agent/stream", customer_id=cid, **({"instruction": step["instruction"]} if step.get("instruction") else {}))
        if ev["type"] != "done":
            raise RuntimeError(f"{k}: {ev.get('error')}")
        st["pid"] = _pid(ev)
        st["proposal"] = ev.get("proposal") or (c.get(f"/api/proposals?customer_id={cid}").json() or [None])[0]
        st["plan_from"] = k
    elif k == "ask":
        r = c.post(f"/api/customers/{cid}/ask", json={"message": step["q"], "proposal_id": st.get("pid") if step.get("with_plan") else None})
        r.raise_for_status()
        st["answer"] = r.json()
    elif k == "edit":
        ev = sse(c, f"/api/proposals/{st['pid']}/adjust/stream", message=step["msg"])
        if ev["type"] == "done" and _pid(ev):
            st["pid"], st["proposal"], st["plan_from"] = _pid(ev), ev.get("proposal"), "edit"
        st["edit"] = ev
    elif k == "option_add":
        c.post(f"/api/customers/{cid}/options/add", json={"key": step["key"]}).raise_for_status()
    elif k == "options_apply":
        c.post(f"/api/customers/{cid}/options/apply", json={"keys": step["keys"]}).raise_for_status()
    elif k == "approve":
        r = c.post(f"/api/proposals/{st['pid']}/approve", json={})
        r.raise_for_status()
        st["approved"] = r.json()
    elif k == "checkin":
        r = c.post(f"/api/customers/{cid}/tracking/checkin", json={"scenario": step["scenario"]})
        r.raise_for_status()
        st["checkin"] = r.json()
    else:
        raise ValueError(k)


def plan_children(c: httpx.Client, cid: str, st: dict, path: list) -> list[list]:
    """What a plan screen offers: questions, the followup, chips, cushions, underwater options, approve."""
    p, out = st.get("proposal") or {}, []
    if not st.get("pid") or p.get("status") != "pending":
        return out
    if any(s["do"] == "options_apply" and not s.get("main") for s in path):
        return out  # a single option's plan is recorded; only the default picks' plan gets the whole tree
    out.append(path + [{"do": "ask", "q": QUESTIONS[0], "with_plan": True}])
    out.append(path + [{"do": "ask", "q": ASK, "with_plan": True}])
    out += [path + [{"do": "edit", "msg": m}] for m in suggest(p)]
    try:
        cur = (p.get("plan") or {}).get("keep_emergency_months")
        opts = c.post(f"/api/customers/{cid}/scenarios/cushion", json={"plan": p["plan"]}).json().get("options") or []
        out += [path + [{"do": "edit", "msg": f"Keep {o['months']:g} months of expenses in savings"}] for o in opts if o.get("months") != cur]
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        pass
    if st.get("plan_from") == "run" and not any(s["do"] == "options_apply" for s in path):
        rc = c.get(f"/api/customers/{cid}/reality-check").json()
        if not rc.get("realistic", True):
            cards = ((p.get("simulation") or {}).get("explain") or {}).get("underwater_options") \
                or c.get(f"/api/customers/{cid}/options").json().get("options") or []
            helps = [x for x in cards if x.get("modelled") and (x.get("closes_per_month") or 0) > 0.5]
            rec = [x for x in helps if x.get("recommended") and not x.get("never_recommend")] or [x for x in helps if not x.get("never_recommend")]
            order = lambda xs: [x["key"] for x in sorted(xs, key=lambda x: x.get("closes_per_month") or 0)]  # noqa: E731
            picks = [order(rec)] + [[x["key"]] for x in helps if [x["key"]] != order(rec)]
            out += [path + [{"do": "options_apply", "keys": ks, **({"main": True} if i == 0 else {})}, {"do": "run"}]
                    for i, ks in enumerate(picks) if ks]
    if st.get("plan_from") == "run":
        out.append(path + [{"do": "approve"}])  # approve the agent's plan (underwater or not), then check in
    return out


def children(c: httpx.Client, cid: str, st: dict, path: list) -> list[list]:
    last = path[-1]["do"]
    if last == "run":
        return plan_children(c, cid, st, path)
    if last == "ask" and path[-1]["q"] == ASK and st.get("answer", {}).get("followup"):
        f = st["answer"]["followup"]
        return [path[:-1] + [{"do": "option_add", "key": f["option"]}, {"do": "edit", "msg": f["message"]}]]
    if last == "approve":
        tr = c.get(f"/api/customers/{cid}/tracking").json()
        return [path + [{"do": "checkin", "scenario": s["key"]}] for s in tr.get("scenarios") or []]
    if last == "checkin" and path[-1]["scenario"] in REPLAN_AFTER:
        return [path + [{"do": "replan"}]]
    return []


# ---------- the recorder ----------

class Recorder:
    def __init__(self, a, env: dict, root: Path, pristine: Path):
        self.a, self.root = a, root
        self.workers = [Worker(i, a.port_base + i, root, pristine, Path(a.ids), env) for i in range(a.parallel)]
        self.lock = threading.Lock()
        self.rows: list[dict] = []
        self.r429 = 0
        self.llm = Counter()

    def node(self, w: Worker, cid: str, path: list, n: int) -> tuple[list, list[list]]:
        t0 = time.monotonic()
        c, d, log = w.start(cid, f"n{n}")
        st: dict = {}
        err = None
        try:
            s0 = c.get("/api/_demo").json()["stats"]
            for step in path:
                do(c, cid, step, st)
            kids = [] if len(path) >= self.a.depth else children(c, cid, st, path)
            s1 = c.get("/api/_demo").json()["stats"]
        except Exception as e:  # noqa: BLE001
            err, kids, s0, s1 = f"{type(e).__name__}: {e}"[:300], [], {}, {}
        finally:
            w.stop()
        new_ms = []
        with self.lock:
            before = {f.name: set(json.loads(f.read_text())) for f in demo.CACHE_DIR.glob("*.json")} if demo.CACHE_DIR.exists() else {}
            demo.merge(d / "cache", demo.CACHE_DIR)
            for f in (d / "cache").glob("*.json"):
                for k, e in json.loads(f.read_text()).items():
                    if k not in before.get(f.name, set()) and e.get("ms") is not None:
                        new_ms.append(e["ms"])
            text = log.read_text(errors="replace")
            st429 = len(re.findall(r"^\[llm\] 429", text, re.M))
            self.r429 += st429
            self.llm.update(re.findall(r"^\[llm\] (\d+)", text, re.M))
            writes = len(re.findall(r"^\[nessie dry-run\]", text, re.M))
            row = {"customer": cid, "path": " > ".join(label(s) for s in path), "kind": path[-1]["do"], "secs": round(time.monotonic() - t0, 1),
                   "live_calls": (s1.get("recorded", 0) - s0.get("recorded", 0)) if s1 else None, "live_ms": new_ms,
                   "hits": (s1.get("hit", 0) + s1.get("near", 0) - s0.get("hit", 0) - s0.get("near", 0)) if s1 else None,
                   "nessie_dry_writes": writes, "r429": st429, "error": err}
            self.rows.append(row)
            print(f"[{len(self.rows):3}] {row['secs']:6.1f}s  {cid} {row['path']}  live {row['live_calls']} hit {row['hits']}"
                  + (f"  dry-writes {writes}" if writes else "") + (f"  429x{st429}" if st429 else "") + (f"  ERROR {err}" if err else ""), flush=True)
        if not self.a.keep:
            shutil.rmtree(d, ignore_errors=True)
        return path, kids

    def run(self, roots: list[tuple[str, list]]) -> None:
        free = list(self.workers)
        queue = list(roots)
        done = n = 0
        with ThreadPoolExecutor(len(self.workers)) as ex:
            running: dict = {}
            while queue or running:
                while queue and free and (self.a.limit is None or done + len(running) < self.a.limit):
                    cid, path = queue.pop(0)
                    w = free.pop(0)
                    n += 1
                    running[ex.submit(self.node, w, cid, path, n)] = (w, cid)
                if not running:
                    break
                fin, _ = wait(running, return_when=FIRST_COMPLETED)
                for f in fin:
                    w, cid = running.pop(f)
                    free.append(w)
                    done += 1
                    _, kids = f.result()
                    if self.a.smoke:
                        kids = smoke_pick(kids)
                    queue += [(cid, k) for k in kids]
        for w in self.workers:
            w.stop()


def smoke_pick(kids: list[list]) -> list[list]:
    """Smoke: from the first plan, one question, one chip, and approve > income drop check-in."""
    want = []
    for k in kids:
        s = k[-1]
        if (s["do"] == "ask" and s.get("with_plan") and s["q"] == QUESTIONS[0]) or (s["do"] == "edit" and not any(x[-1]["do"] == "edit" for x in want)) \
                or s["do"] in ("approve", "replan") or (s["do"] == "checkin" and s["scenario"] == "income_drop"):
            want.append(k)
    return want


def roots_for(customers: list[str], smoke: bool) -> list[tuple[str, list]]:
    out = []
    for cid in customers:
        out.append((cid, [{"do": "run"}]))
        out += [(cid, [{"do": "ask", "q": q}]) for q in (QUESTIONS[:1] if smoke else QUESTIONS)]
    return out


def report(rec: Recorder, parallel: int) -> None:
    rows = rec.rows
    by = defaultdict(list)
    for r in rows:
        by[r["kind"]].append(r)
    print("\n== per step kind (the step a path ends on)")
    for k, rs in sorted(by.items()):
        ms = [m for r in rs for m in r["live_ms"]]
        calls = [r["live_calls"] for r in rs if r["live_calls"] is not None]
        print(f"  {k:14} paths {len(rs):3}  model calls/path {sum(calls) / max(1, len(calls)):4.1f}  "
              f"call {sum(ms) / max(1, len(ms)) / 1000:5.1f}s avg, {max(ms, default=0) / 1000:5.1f}s max  path {sum(r['secs'] for r in rs) / len(rs):6.1f}s avg")
    work = sum(r["secs"] for r in rows)
    print(f"\n== {len(rows)} paths, {sum(len(r['live_ms']) for r in rows)} model calls recorded, {sum(r['nessie_dry_writes'] for r in rows)} Nessie writes faked (none sent)")
    print(f"   work {work / 60:.1f} min; at --parallel {parallel}: about {work / parallel / 60:.1f} min wall clock (+ the longest chain)")
    print(f"   model HTTP statuses: {dict(rec.llm)}; 429 (rate limited): {rec.r429}")
    errs = [r for r in rows if r["error"]]
    if errs:
        print(f"   {len(errs)} paths failed:")
        for r in errs:
            print(f"     {r['customer']} {r['path']}: {r['error']}")


def load_env(path: Path | None) -> dict:
    env = dict(os.environ)
    if path and path.exists():
        for line in path.read_text().splitlines():
            m = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$", line)
            if m and m.group(1) not in env:
                env[m.group(1)] = m.group(2).strip().strip("'\"")
    return env


def snapshot(src: Path, dst: Path) -> None:
    """A consistent copy of a live SQLite DB (the dev server may be writing to it)."""
    with sqlite3.connect(src) as s, sqlite3.connect(dst) as d:
        s.backup(d)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--customers", nargs="+", default=["C001", "C027"])
    ap.add_argument("--parallel", type=int, default=4, help="backends (and model calls) at once")
    ap.add_argument("--port-base", type=int, default=8761, help="worker i listens on port-base + i (127.0.0.1)")
    ap.add_argument("--depth", type=int, default=6, help="longest path, in steps")
    ap.add_argument("--limit", type=int, default=None, help="stop after this many paths")
    ap.add_argument("--smoke", action="store_true", help=f"C001 only, {SMOKE} paths")
    ap.add_argument("--db", default=str(DB_PATH), help="DB to copy (default: the app's)")
    ap.add_argument("--ids", default=str(NESSIE_IDS_PATH), help="Nessie id map to copy")
    ap.add_argument("--env-file", default=str(REPO_DIR / ".env"), help="keys for the workers (NVIDIA_API_KEY, NESSIE_KEY)")
    ap.add_argument("--keep", action="store_true", help="keep each path's worker dir (DB, cache, log)")
    a = ap.parse_args()
    if a.smoke:
        a.customers, a.limit = a.customers[:1], a.limit or SMOKE
    for f in (a.db, a.ids):
        if not Path(f).exists():  # sqlite would quietly make an empty DB
            print(f"{f} not found (from a worktree, pass the main checkout's with --db / --ids)", file=sys.stderr)
            return 2
    env = load_env(Path(a.env_file))
    if not env.get("NVIDIA_API_KEY"):
        print("NVIDIA_API_KEY is not set (looked in the environment and --env-file)", file=sys.stderr)
        return 2
    root = Path(tempfile.mkdtemp(prefix="paidway-rec-"))
    pristine = root / "pristine.sqlite3"
    snapshot(Path(a.db), pristine)
    print(f"recording into {demo.CACHE_DIR} | workers {a.parallel} on 127.0.0.1:{a.port_base}-{a.port_base + a.parallel - 1} | scratch {root}")
    rec = Recorder(a, env, root, pristine)
    t0 = time.monotonic()
    try:
        rec.run(roots_for(a.customers, a.smoke))
    finally:
        for w in rec.workers:
            w.stop()
    print(f"\nwall clock {(time.monotonic() - t0) / 60:.1f} min")
    report(rec, a.parallel)
    out = BACKEND_DIR / "data" / "demo_record_last.json"  # not in demo_cache/: every *.json there is a recording file
    out.write_text(json.dumps(rec.rows, indent=1))
    print(f"   per path: {out}")
    if not a.keep:
        shutil.rmtree(root, ignore_errors=True)
    return 1 if any(r["error"] for r in rec.rows) else 0


if __name__ == "__main__":
    sys.exit(main())
