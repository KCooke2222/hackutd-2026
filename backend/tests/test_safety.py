"""The agent must never be able to reach the Nessie write client."""

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "zeroday"


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
        for imp in _imports(f):
            assert "write" not in imp, f"{f.name} imports {imp}"


def test_agent_tools_are_read_only_plus_propose():
    from zeroday.agent.tools import make_tools

    names = {t.name for t in make_tools("C001")}
    assert names == {"get_accounts", "get_card_activity", "refill_breakdown", "simulate_payoff", "propose_plan"}
