"""WS43-F15 — the no-Copilot ratchet fence (WS-43k).

D84 (2026-10-03) removes the GitHub Copilot SDK from the platform. The owning
spec is ``project-docs/specs/maf_coding_engine.md`` §15.6. This test stops a
new Copilot use while the slices of WS-43 remove the old ones.

What the test reads
-------------------
Every ``.py`` file under ``apps/`` and ``packages/``, and the root
``agents.py``. It reads the syntax tree, not the text. So a comment or a
docstring never trips it. The Notes copilot and the Workflows copilot are
product names. They call no Copilot SDK, and their names do not trip it.

What counts as a Copilot use
----------------------------
* an absolute import of ``copilot`` or a module under ``copilot.``,
* an import of ``agent_framework_github_copilot``, or of a module path with a
  ``github_copilot`` part,
* the identifier ``GitHubCopilotAgent`` or ``github_copilot`` in code: a
  name, an attribute, an imported name, or a class or function name,
* one of those module names as a string to ``importlib.import_module`` or
  ``__import__``.

A relative import such as ``from .copilot import router`` is the Notes
copilot module, so it does not count.

The ratchet
-----------
``_ALLOWLIST`` names each file that held a Copilot use at build time, and
the slice that removes it. A file off the list with a use fails the test. An
entry for a file with no use fails the test too. So the list only shrinks,
and a PR that removes the last use from a file deletes its entry in the same
PR. At WS-43r the list is empty.

A file that does not parse FAILS the test. It never skips. A skipped file is
a file the fence does not read.
"""

from __future__ import annotations

import ast
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]

#: The dirs that the fence reads, and the one root file.
_SCAN_DIRS = ("apps", "packages")
_ROOT_FILES = ("agents.py",)

#: Dirs that hold no source of ours. A local checkout can carry them.
_SKIP_PARTS = frozenset({"__pycache__", ".venv", "node_modules", ".git"})

#: Each file that held a Copilot use at build time, and the slice that
#: removes it (``maf_coding_engine.md`` §15.6). Delete an entry in the PR that
#: removes the last Copilot use from its file. Never add one.
_ALLOWLIST: dict[str, str] = {
    "agents.py": "WS-43q",
    "apps/agents/agent-app-builder/agents.py": "WS-43j",
    "apps/agents/agent-task-manager/agents.py": "WS-8i, after the soak of WS-43t2",
    "apps/services/gateway/gateway/main.py": "WS-43q",
    "apps/services/orchestrator/mutation_runner.py": "WS-43p",
    "apps/services/orchestrator/orchestrator/_copilot_session.py": "WS-43q",
    "apps/services/orchestrator/orchestrator/copilot_agent.py": "WS-43q",
    "apps/services/orchestrator/orchestrator/executor.py": "WS-43q",
    "packages/acb_skills/acb_skills/permission_policy.py": "WS-43q",
}

#: Module roots that are the Copilot SDK or its MAF adapter.
_COPILOT_MODULE_ROOTS = frozenset({"copilot", "agent_framework_github_copilot"})

#: Identifiers that name the Copilot adapter in code.
_COPILOT_NAMES = frozenset({"GitHubCopilotAgent", "github_copilot"})

#: The calls that load a module from a string.
_IMPORT_CALLS = frozenset({"import_module", "__import__"})


def _is_copilot_module(dotted: str) -> bool:
    """True if *dotted* is a Copilot module path.

    Only the FIRST part may be ``copilot``. So ``gateway.routes.notes.copilot``,
    the Notes copilot, is not a Copilot module.
    """
    parts = dotted.split(".")
    if parts[0] in _COPILOT_MODULE_ROOTS:
        return True
    return any(
        part in _COPILOT_NAMES or part == "agent_framework_github_copilot" for part in parts[1:]
    )


def _string_prefix(node: ast.expr) -> str | None:
    """The static text at the start of a string argument, or None."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr) and node.values:
        head = node.values[0]
        if isinstance(head, ast.Constant) and isinstance(head.value, str):
            return head.value
    return None


def _call_name(func: ast.expr) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


@dataclass
class _CopilotFinder(ast.NodeVisitor):
    """Collects each Copilot use in one syntax tree, as ``line N: what``."""

    hits: list[str] = field(default_factory=list)

    def _hit(self, node: ast.AST, what: str) -> None:
        self.hits.append(f"line {getattr(node, 'lineno', '?')}: {what}")

    def _check_aliases(self, node: ast.Import | ast.ImportFrom) -> None:
        for alias in node.names:
            for name in (alias.name, alias.asname):
                if name in _COPILOT_NAMES:
                    self._hit(node, f"imports the name {name}")

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if _is_copilot_module(alias.name):
                self._hit(node, f"import {alias.name}")
        self._check_aliases(node)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        # A relative import (level > 0) names a module of ours, such as the
        # Notes copilot. Only an absolute import can reach the SDK.
        if node.level == 0 and node.module and _is_copilot_module(node.module):
            self._hit(node, f"from {node.module} import ...")
        self._check_aliases(node)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if _call_name(node.func) in _IMPORT_CALLS:
            args = list(node.args[:1]) + [k.value for k in node.keywords if k.arg == "name"]
            for arg in args:
                prefix = _string_prefix(arg)
                if prefix and _is_copilot_module(prefix.rstrip(".")):
                    self._hit(node, f"loads {prefix!r} through {_call_name(node.func)}")
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id in _COPILOT_NAMES:
            self._hit(node, f"names {node.id}")
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr in _COPILOT_NAMES:
            self._hit(node, f"names .{node.attr}")
        self.generic_visit(node)

    def _check_def(self, node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        # A shim class with the adapter's name is a Copilot use too (§15.5).
        if node.name in _COPILOT_NAMES:
            self._hit(node, f"defines {node.name}")
        self.generic_visit(node)

    visit_ClassDef = _check_def
    visit_FunctionDef = _check_def
    visit_AsyncFunctionDef = _check_def


def _copilot_uses(source: str, filename: str = "<source>") -> list[str]:
    """Each Copilot use in *source*. A syntax error RAISES. It never skips."""
    finder = _CopilotFinder()
    finder.visit(ast.parse(source, filename=filename))
    return finder.hits


@dataclass(frozen=True)
class _Scan:
    uses: dict[str, tuple[str, ...]]
    parse_errors: dict[str, str]
    files: frozenset[str]


def _rel(path: Path) -> str:
    return path.relative_to(_REPO).as_posix()


def _python_files() -> list[Path]:
    out = [_REPO / name for name in _ROOT_FILES]
    for top in _SCAN_DIRS:
        out.extend(p for p in (_REPO / top).rglob("*.py") if not _SKIP_PARTS.intersection(p.parts))
    return sorted(out)


def _scan_files(paths: list[Path], rel: Callable[[Path], str] = _rel) -> _Scan:
    """Read and parse each file. Record each use and each parse error."""
    uses: dict[str, tuple[str, ...]] = {}
    errors: dict[str, str] = {}
    for path in paths:
        key = rel(path)
        try:
            # utf-8-sig: 8 files under apps/ and packages/ start with a byte
            # order mark, and ast.parse refuses a leading U+FEFF.
            source = path.read_text(encoding="utf-8-sig")
            hits = _copilot_uses(source, filename=str(path))
        # A UnicodeDecodeError is a ValueError, and so is a NUL byte.
        except (SyntaxError, ValueError) as exc:
            errors[key] = f"{type(exc).__name__}: {exc}"
            continue
        if hits:
            uses[key] = tuple(hits)
    return _Scan(uses=uses, parse_errors=errors, files=frozenset(rel(p) for p in paths))


@cache
def _tree_scan() -> _Scan:
    return _scan_files(_python_files())


def _uses_or_fail() -> dict[str, tuple[str, ...]]:
    """The uses in the tree. A parse error fails the caller. It never skips."""
    scan = _tree_scan()
    assert not scan.parse_errors, (
        "WS43-F15 could not parse these files, so it cannot say they hold no "
        "Copilot use:\n  " + "\n  ".join(f"{p}: {e}" for p, e in sorted(scan.parse_errors.items()))
    )
    return scan.uses


# ── The fence on the tree ───────────────────────────────────────────────────


def test_every_scanned_file_parses() -> None:
    _uses_or_fail()


def test_the_scan_reads_the_whole_tree() -> None:
    files = _tree_scan().files
    assert "agents.py" in files, "the root agents.py is not scanned"
    for top in _SCAN_DIRS:
        assert any(f.startswith(f"{top}/") for f in files), f"nothing under {top}/ is scanned"


def test_no_copilot_use_off_the_allowlist() -> None:
    uses = _uses_or_fail()
    new = {p: u for p, u in sorted(uses.items()) if p not in _ALLOWLIST}
    assert not new, (
        "D84 removes the GitHub Copilot SDK (maf_coding_engine.md §15). These "
        "files are off the WS43-F15 allowlist and hold a Copilot use:\n  "
        + "\n  ".join(f"{p}: {'; '.join(u)}" for p, u in new.items())
        + "\n\nBuild a MAF Agent. Do not add the file to _ALLOWLIST."
    )


def test_allowlist_has_no_stale_entries() -> None:
    uses = _uses_or_fail()
    stale = sorted(p for p in _ALLOWLIST if p not in uses)
    assert not stale, (
        "These _ALLOWLIST entries hold no Copilot use. Delete them in this PR, "
        "so the list only shrinks:\n  " + "\n  ".join(stale)
    )


# ── The finder itself ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "source",
    [
        "import copilot\n",
        "import copilot as c\n",
        "import copilot.types\n",
        "from copilot import CopilotClient\n",
        "from copilot.generated.rpc import PermissionDecisionReject\n",
        "def f():\n    from copilot import PermissionHandler\n",
        "import agent_framework_github_copilot\n",
        "from agent_framework_github_copilot import GitHubCopilotAgent\n",
        "from agent_framework.github_copilot import Thing\n",
        "from agent_framework.github import GitHubCopilotAgent\n",
        "import importlib\nimportlib.import_module('copilot')\n",
        "import importlib\nimportlib.import_module('copilot.types')\n",
        "from importlib import import_module\nimport_module(name='copilot')\n",
        "__import__('copilot')\n",
        "__import__('agent_framework_github_copilot')\n",
        "import importlib\nimportlib.import_module(f'copilot.{x}')\n",
        "def build() -> GitHubCopilotAgent: ...\n",
        "agent = mod.GitHubCopilotAgent()\n",
        "x = github_copilot\n",
        "class GitHubCopilotAgent: ...\n",
    ],
)
def test_the_finder_catches_a_copilot_use(source: str) -> None:
    assert _copilot_uses(source), f"WS43-F15 missed a Copilot use in: {source!r}"


@pytest.mark.parametrize(
    "source",
    [
        "# a GitHubCopilotAgent and `import copilot` in a comment\n",
        '"""Build a GitHubCopilotAgent. import copilot."""\n',
        "def f():\n    'from copilot import CopilotClient'\n",
        "from .copilot import router\n",
        "from . import copilot\n",
        "from gateway.routes.notes import copilot\n",
        "import gateway.routes.notes.copilot\n",
        "from gateway.routes.notes.copilot import router\n",
        "copilot = 1\ncopilot_router = copilot\n",
        "runtime = 'github-copilot'\nlabel = 'github_copilot'\n",
        "msg = f'build a `GitHubCopilotAgent` in build_agents()'\n",
        "import importlib\nimportlib.import_module('acb_skills.write_artifact')\n",
        "__import__('datetime')\n",
        "import copilotkit\n",
    ],
)
def test_the_finder_ignores_text_and_product_names(source: str) -> None:
    assert not _copilot_uses(source), f"WS43-F15 flagged a non-use: {source!r}"


def test_a_file_that_does_not_parse_fails(tmp_path: Path) -> None:
    bad = tmp_path / "broken.py"
    bad.write_text("def broken(:\n    pass\n", encoding="utf-8")
    scan = _scan_files([bad], rel=lambda p: p.name)
    assert "broken.py" in scan.parse_errors
    assert not scan.uses


def test_a_byte_order_mark_does_not_hide_a_use(tmp_path: Path) -> None:
    bom = tmp_path / "bom.py"
    bom.write_bytes(b"\xef\xbb\xbfimport copilot\n")
    scan = _scan_files([bom], rel=lambda p: p.name)
    assert not scan.parse_errors
    assert "bom.py" in scan.uses
