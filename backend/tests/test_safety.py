"""The agent can only read and propose. Money moves only through agent/execute.py on human approve."""

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "paidway"
APPROVE_ONLY = {"execute.py"}  # called from the approve route, never from the graph or tools


def _imports(path: Path) -> set[str]:
    names = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.ImportFrom):
            names.add((node.module or "") + ":" + ",".join(a.name for a in node.names))
        elif isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
    return names


def test_agent_does_not_import_write():
    for f in (SRC / "agent").glob("*.py"):
        if f.name in APPROVE_ONLY:
            continue
        for imp in _imports(f):
            assert "write" not in imp, f"{f.name} imports {imp}"


def test_agent_never_imports_execute():
    for f in (SRC / "agent").glob("*.py"):
        if f.name in APPROVE_ONLY:
            continue
        for imp in _imports(f):
            assert "execute" not in imp, f"{f.name} imports {imp}"


def test_agent_tools_are_read_only_plus_propose():
    from paidway.agent.tools import make_tools

    names = {t.name for t in make_tools("C001")}
    assert names == {
        "sql", "cash_flow", "spending_detail", "find_recurring", "compare_sources",
        "simulate_plan", "optimize_plan", "debt_priority", "liquidity_ladder", "propose_plan",
    }


PLAN = {"monthly_debt_budget": 3000, "extra_order": ["A-C001-CREDIT_CARD"], "keep_emergency_months": 3}
READ_CALLS = {
    "sql": {"query": "select count(*) from v_accounts"},
    "cash_flow": {"months": 6},
    "spending_detail": {"category": "Dining", "months": 6},
    "find_recurring": {},
    "compare_sources": {"amount": 1000, "target_debt": "A-C001-CREDIT_CARD"},
    "debt_priority": {},
    "liquidity_ladder": {"amount_needed": 1000},
    "simulate_plan": PLAN,
    "optimize_plan": {"keep_emergency_months": 3},
}


@pytest.fixture()
def no_writes(tmp_path, monkeypatch):
    """Every store write and the Nessie write client raise; reads still work."""
    from paidway.data import balances, store
    from paidway.nessie import write

    monkeypatch.setattr(store, "DB_PATH", tmp_path / "t.sqlite3")
    monkeypatch.setattr(balances.read, "enabled", lambda: False)
    from paidway.data import linked

    linked.load()  # seed the linked-accounts cache table before writes are blocked

    attempts: list[str] = []

    def blocker(name):
        def boom(*a, **k):
            attempts.append(name)
            raise RuntimeError(f"write attempted: {name}")
        return boom

    for name in ("create_proposal", "set_status", "claim", "release", "log_run", "annotate", "replace_linked_accounts"):
        if hasattr(store, name):
            monkeypatch.setattr(store, name, blocker(f"store.{name}"))
    for name in dir(write):
        if name.startswith(("create_", "delete_", "update_", "request")):
            monkeypatch.setattr(write, name, blocker(f"nessie.write.{name}"))
    return attempts


def test_read_tools_never_write(no_writes):
    from paidway.agent.tools import make_tools

    tools = {t.name: t for t in make_tools("C001")}
    assert set(READ_CALLS) == set(tools) - {"propose_plan"}
    for name, args in READ_CALLS.items():
        out = tools[name].invoke(args)
        assert not (isinstance(out, dict) and "write attempted" in str(out.get("error"))), name
    assert no_writes == []


def test_sql_tool_refuses_writes():
    from paidway.agent.tools import make_tools

    sql = next(t for t in make_tools("C001") if t.name == "sql")
    for q in ("delete from v_accounts", "drop view v_accounts", "update v_accounts set balance = 0",
              "insert into v_accounts values (1)", "select 1; delete from v_accounts", "attach database 'x' as y"):
        assert "error" in sql.invoke({"query": q}), q


def test_only_execute_and_seed_cli_import_nessie_write():
    """Money moves via agent/execute.py (approve route); data/seed.py is the offline `make seed` CLI."""
    allowed = {SRC / "agent" / "execute.py", SRC / "data" / "seed.py", SRC / "nessie" / "write.py"}
    for f in SRC.rglob("*.py"):
        if f in allowed:
            continue
        for imp in _imports(f):
            mod, _, names = imp.partition(":")
            assert not (mod.endswith("nessie") and "write" in names.split(",")) and not mod.endswith("nessie.write"), (
                f"{f.relative_to(SRC)} imports {imp}"
            )
