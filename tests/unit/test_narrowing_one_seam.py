"""One seam for the narrowing pipeline. WS-48 N1, fences WS48-F4 and WS48-F5.

Spec: ``project-docs/specs/data_narrowing_pipeline.md`` §0 ("One seam"),
§4 and §8.

WS48-F4: only ``acb_skills/narrowing.py`` defines ``narrow_and_read``. An
agent gets the tool through ``narrow_tool_for``, which reads the flag and
builds it with ``make_narrow_tool``. A direct call of ``make_narrow_tool``
outside ``narrowing.py`` would skip the flag, so the scan refuses it. Each
agent that holds the tool carries ``narrowing.INSTRUCTION_LINE`` in its
``instructions.md``.

WS48-F5: an adapter (``narrow_source.py``) opens no database session and
imports no ``sqlalchemy``. ``narrowing.py`` holds to the same rule.

Each scan is an AST scan of ``apps/`` and ``packages/``. Each one takes its
root as an argument, and a companion test proves that it can fail on a
planted file (R7).

Mutations this file catches, each one run red before the change:

* a second ``def narrow_and_read``, or a function renamed to it ->
  ``test_only_narrowing_defines_the_tool`` and its companion;
* an agent calls ``make_narrow_tool`` directly -> ``test_no_agent_skips_the_flag``;
* an agent holds the tool and its instructions lack the count line rule ->
  ``test_each_agent_with_the_tool_states_the_count_line``;
* an adapter imports ``sqlalchemy`` or opens a session ->
  ``test_no_adapter_opens_a_session``;
* ``narrowing.py`` imports a vendor client -> ``test_narrowing_imports_no_vendor_client``.
"""
from __future__ import annotations

import ast
from pathlib import Path

from acb_skills import narrowing

REPO = Path(__file__).resolve().parents[2]
ROOTS = ("apps", "packages")
SEAM = Path("packages/acb_skills/acb_skills/narrowing.py")
TOOL = narrowing.TOOL_NAME

#: Imports that open a database path. An adapter calls a gateway route.
DB_MODULES = ("sqlalchemy", "asyncpg", "psycopg", "psycopg2", "gateway.db")
#: Calls and imported names that open a session.
DB_NAMES = frozenset({
    "tenant_session", "_tenant_session", "get_db", "get_session",
    "AsyncSession", "create_async_engine", "create_engine", "engine",
})
#: The vendor SDKs that ``test_no_direct_ai_vendor_calls.py`` refuses.
VENDOR_MODULES = ("openai", "anthropic", "litellm", "google.generativeai", "mistralai")


def _py_files(repo: Path) -> list[Path]:
    out: list[Path] = []
    for root in ROOTS:
        base = repo / root
        if not base.is_dir():
            continue
        for path in base.rglob("*.py"):
            parts = set(path.parts)
            if parts & {"node_modules", ".venv", "__pycache__", "site-packages"}:
                continue
            out.append(path)
    return out


def _tree(path: Path) -> ast.AST | None:
    try:
        return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError):
        return None


def _rel(repo: Path, path: Path) -> str:
    return path.relative_to(repo).as_posix()


def _called(node: ast.Call) -> str:
    fn = node.func
    if isinstance(fn, ast.Name):
        return fn.id
    if isinstance(fn, ast.Attribute):
        return fn.attr
    return ""


# ── The scans ───────────────────────────────────────────────────────────────


def tool_definitions(repo: Path) -> list[str]:
    """Each place outside the seam that defines the tool, or names a function
    after it."""
    bad: list[str] = []
    for path in _py_files(repo):
        if _rel(repo, path) == SEAM.as_posix():
            continue
        tree = _tree(path)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == TOOL:
                bad.append(f"{_rel(repo, path)}:{node.lineno} def {TOOL}")
            if isinstance(node, ast.Assign):
                named = any(
                    isinstance(t, ast.Attribute) and t.attr in {"__name__", "__qualname__"}
                    for t in node.targets
                )
                if named and isinstance(node.value, ast.Constant) and node.value.value == TOOL:
                    bad.append(f"{_rel(repo, path)}:{node.lineno} __name__ = {TOOL!r}")
    return bad


def direct_builds(repo: Path) -> list[str]:
    """Each call of ``make_narrow_tool`` outside the seam: it skips the flag."""
    bad: list[str] = []
    for path in _py_files(repo):
        if _rel(repo, path) == SEAM.as_posix():
            continue
        tree = _tree(path)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _called(node) == "make_narrow_tool":
                bad.append(f"{_rel(repo, path)}:{node.lineno}")
    return bad


def _agent_dir(path: Path, repo: Path) -> Path | None:
    """The nearest directory above *path* that holds a ``config.json``."""
    for parent in path.parents:
        if parent == repo:
            return None
        if (parent / "config.json").is_file():
            return parent
    return None


def holders_without_the_line(repo: Path) -> list[str]:
    """Each file that gives an agent the tool, where the agent's
    ``instructions.md`` lacks :data:`narrowing.INSTRUCTION_LINE`."""
    bad: list[str] = []
    for path in _py_files(repo):
        if _rel(repo, path) == SEAM.as_posix():
            continue
        tree = _tree(path)
        if tree is None:
            continue
        holds = any(
            isinstance(node, ast.Call) and _called(node) in {"narrow_tool_for", "make_narrow_tool"}
            for node in ast.walk(tree)
        )
        if not holds:
            continue
        agent = _agent_dir(path, repo)
        md = agent / "instructions.md" if agent else None
        text = md.read_text(encoding="utf-8") if md and md.is_file() else ""
        if narrowing.INSTRUCTION_LINE not in text:
            bad.append(_rel(repo, path))
    return bad


def _is_db_module(name: str) -> bool:
    return any(name == m or name.startswith(m + ".") for m in DB_MODULES)


def _db_uses(tree: ast.AST) -> list[str]:
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [f"import {a.name}" for a in node.names if _is_db_module(a.name)]
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if _is_db_module(module):
                found.append(f"from {module}")
            for alias in node.names:
                if alias.name in DB_NAMES:
                    found.append(f"from {module} import {alias.name}")
        elif isinstance(node, ast.Call) and _called(node) in DB_NAMES:
            found.append(f"call {_called(node)}()")
    return found


def adapters_with_a_session(repo: Path) -> list[str]:
    """Each adapter file, and the seam, that reaches a database."""
    bad: list[str] = []
    for path in _py_files(repo):
        rel = _rel(repo, path)
        if path.name != "narrow_source.py" and rel != SEAM.as_posix():
            continue
        tree = _tree(path)
        if tree is None:
            continue
        bad += [f"{rel}: {use}" for use in _db_uses(tree)]
    return bad


# ── WS48-F4 ─────────────────────────────────────────────────────────────────


def test_only_narrowing_defines_the_tool() -> None:
    assert (REPO / SEAM).is_file()
    assert tool_definitions(REPO) == []


def test_no_agent_skips_the_flag() -> None:
    assert direct_builds(REPO) == []


def test_each_agent_with_the_tool_states_the_count_line() -> None:
    assert holders_without_the_line(REPO) == []


def test_the_instruction_line_is_the_spec_line() -> None:
    """§6.1: the sentence that each data agent's instructions gain."""
    assert narrowing.INSTRUCTION_LINE == (
        "When you answer from `narrow_and_read`, say how many items you "
        "checked and how many you kept."
    )


# ── WS48-F5 ─────────────────────────────────────────────────────────────────


def test_no_adapter_opens_a_session() -> None:
    assert adapters_with_a_session(REPO) == []


def test_narrowing_imports_no_vendor_client() -> None:
    tree = _tree(REPO / SEAM)
    assert tree is not None
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    vendors = {m for m in imported if any(m == v or m.startswith(v + ".") for v in VENDOR_MODULES)}
    assert vendors == set(), vendors


# ── The companions: each scan can fail (R7) ─────────────────────────────────


def _plant(tmp: Path, rel: str, text: str) -> Path:
    path = tmp / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_a_second_definition_is_found(tmp_path: Path) -> None:
    _plant(tmp_path, SEAM.as_posix(), f"async def {TOOL}(query): ...\n")
    _plant(tmp_path, "apps/agents/agent-x/agents.py", f"async def {TOOL}(query): ...\n")
    _plant(tmp_path, "apps/agents/agent-y/agents.py", f"def f(): ...\nf.__name__ = {TOOL!r}\n")
    found = tool_definitions(tmp_path)
    assert len(found) == 2, found
    assert all(SEAM.as_posix() not in f for f in found)


def test_a_direct_build_is_found(tmp_path: Path) -> None:
    _plant(
        tmp_path, "apps/agents/agent-x/agents.py",
        "from acb_skills.narrowing import make_narrow_tool\n"
        "TOOLS = [make_narrow_tool(object())]\n",
    )
    assert direct_builds(tmp_path) == ["apps/agents/agent-x/agents.py:2"]


def test_a_holder_with_no_count_line_is_found(tmp_path: Path) -> None:
    agent = "apps/agents/agent-x"
    _plant(tmp_path, f"{agent}/config.json", "{}")
    _plant(tmp_path, f"{agent}/instructions.md", "You read mail.\n")
    _plant(
        tmp_path, f"{agent}/agents.py",
        "from acb_skills import narrowing\n"
        "tool = narrowing.narrow_tool_for('agent-x', None)\n",
    )
    assert holders_without_the_line(tmp_path) == [f"{agent}/agents.py"]
    _plant(tmp_path, f"{agent}/instructions.md", f"You read mail.\n{narrowing.INSTRUCTION_LINE}\n")
    assert holders_without_the_line(tmp_path) == []


def test_a_holder_with_no_agent_dir_is_found(tmp_path: Path) -> None:
    _plant(tmp_path, "packages/x/x.py", "narrow_tool_for('a', None)\n")
    assert holders_without_the_line(tmp_path) == ["packages/x/x.py"]


def test_an_adapter_with_a_session_is_found(tmp_path: Path) -> None:
    _plant(tmp_path, "apps/agents/agent-a/narrow_source.py", "import sqlalchemy\n")
    _plant(tmp_path, "apps/agents/agent-b/narrow_source.py",
           "from sqlalchemy.ext.asyncio import AsyncSession\n")
    _plant(tmp_path, "apps/agents/agent-c/narrow_source.py",
           "from acb_common.db import tenant_session\n")
    _plant(tmp_path, "apps/agents/agent-d/narrow_source.py",
           "async def f():\n    async with _tenant_session() as s: ...\n")
    _plant(tmp_path, "apps/agents/agent-e/narrow_source.py", "from gateway.db import x\n")
    _plant(tmp_path, "apps/agents/agent-f/narrow_source.py", "import httpx\n")
    found = adapters_with_a_session(tmp_path)
    agents = sorted({f.split("/")[2] for f in found})
    assert agents == ["agent-a", "agent-b", "agent-c", "agent-d", "agent-e"], found
