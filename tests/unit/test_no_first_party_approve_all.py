"""H-211 (task 560): no first-party factory sets ``approve_all``.

The root ``metorite`` factory (repo-root ``agents.py``) passed
``on_permission_request: PermissionHandler.approve_all``. The adapter stores
that handler in ``_permission_handler``. ``_copilot_permission_handler`` then
kept it (inside the D85 guard), so the root agent approved every shell
command, file write and read, in every ``AGENT_PERMISSION_MODE``.

Task 560 took it out. The slot stays empty, so the one install function
(``orchestrator._copilot_session._install_copilot_permission_handler``) gives
the agent the B6 handler of the run's mode. ``enforce`` (the default) uses
``risk_aware_permission_handler``. Only ``AGENT_PERMISSION_MODE=approve_all``
uses the SDK's blanket handler, and that is the operator's escape hatch.

Two fences:

* **The tree.** An AST scan of the root ``agents.py``, ``apps/`` and
  ``packages/`` fails on each ``approve_all`` reference outside
  :data:`_ALLOWLIST`: the attribute ``X.approve_all``, an imported name
  ``approve_all``, ``getattr(X, "approve_all")``, and the string
  ``"approve_all"`` as an ``on_permission_request`` value. The mode
  comparisons (``mode == "approve_all"``) are text, so they do not count.
* **The root agent.** The agent that ``build_agent()`` returns has no
  handler, and once installed, ``enforce`` refuses a denylisted shell
  command while it keeps ``ls``. A factory ``approve_all`` turns both red.

Authority: ``permissions_sandbox_b6.md`` "The gap", ``agent_architecture.md``
§3.2, ``maf_coding_engine.md`` §14.
"""
from __future__ import annotations

import ast
import importlib.util
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any

import pytest

_REPO = Path(__file__).resolve().parents[2]

#: The dirs that the fence reads, and the one root file.
_SCAN_DIRS = ("apps", "packages")
_ROOT_FILES = ("agents.py",)

#: Dirs that hold no source of ours. A local checkout can carry them.
_SKIP_PARTS = frozenset({"__pycache__", ".venv", "node_modules", ".git"})

#: The files that may name ``approve_all``, and why. Never add an entry
#: without a reason that a reviewer can check in the code.
_ALLOWLIST: dict[str, str] = {
    "apps/services/orchestrator/mutation_runner.py": (
        "It runs only in the mutation sandbox container "
        "(Dockerfile.mutation ENTRYPOINT, started by "
        "mutation._run_mutation_sandbox through docker run), never on the "
        "host. Nothing on the host imports it"
    ),
    "apps/services/orchestrator/orchestrator/_copilot_session.py": (
        "It implements the AGENT_PERMISSION_MODE=approve_all escape hatch, "
        "and it wraps that handler in the D85 guard"
    ),
}

#: The keyword of the adapter and the SDK that takes the handler.
_HANDLER_KEY = "on_permission_request"
_NAME = "approve_all"


def _is_approve_all_text(node: ast.AST | None) -> bool:
    return isinstance(node, ast.Constant) and node.value == _NAME


def _is_approve_all_value(node: ast.AST | None) -> bool:
    """True for ``X.approve_all``, a bare ``approve_all`` or the string."""
    if isinstance(node, ast.Attribute):
        return node.attr == _NAME
    if isinstance(node, ast.Name):
        return node.id == _NAME
    return _is_approve_all_text(node)


@dataclass
class _ApproveAllFinder(ast.NodeVisitor):
    """Collects each ``approve_all`` reference in one tree, as ``line N: what``."""

    hits: list[str] = field(default_factory=list)

    def _hit(self, node: ast.AST, what: str) -> None:
        self.hits.append(f"line {getattr(node, 'lineno', '?')}: {what}")

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr == _NAME:
            self._hit(node, f"names .{_NAME}")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            if _NAME in (alias.name, alias.asname):
                self._hit(node, f"imports the name {_NAME}")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if (
            isinstance(func, ast.Name) and func.id == "getattr"
            and len(node.args) >= 2 and _is_approve_all_text(node.args[1])
        ):
            self._hit(node, f'getattr(..., "{_NAME}")')
        for kw in node.keywords:
            if kw.arg == _HANDLER_KEY and _is_approve_all_text(kw.value):
                self._hit(node, f'{_HANDLER_KEY}="{_NAME}"')
        self.generic_visit(node)

    def visit_Dict(self, node: ast.Dict) -> None:
        for key, value in zip(node.keys, node.values, strict=True):
            if (
                isinstance(key, ast.Constant) and key.value == _HANDLER_KEY
                and _is_approve_all_text(value)
            ):
                self._hit(node, f'"{_HANDLER_KEY}": "{_NAME}"')
        self.generic_visit(node)


def _approve_all_uses(source: str, filename: str = "<source>") -> list[str]:
    """Each ``approve_all`` reference in *source*. A syntax error RAISES."""
    finder = _ApproveAllFinder()
    finder.visit(ast.parse(source, filename=filename))
    return finder.hits


@dataclass(frozen=True)
class _Scan:
    uses: dict[str, tuple[str, ...]]
    parse_errors: dict[str, str]
    files: frozenset[str]


def _python_files() -> list[Path]:
    out = [_REPO / name for name in _ROOT_FILES]
    for top in _SCAN_DIRS:
        out.extend(
            p for p in (_REPO / top).rglob("*.py") if not _SKIP_PARTS.intersection(p.parts)
        )
    return sorted(out)


@cache
def _tree_scan() -> _Scan:
    uses: dict[str, tuple[str, ...]] = {}
    errors: dict[str, str] = {}
    paths = _python_files()
    for path in paths:
        key = path.relative_to(_REPO).as_posix()
        try:
            # utf-8-sig: some files start with a byte order mark, and
            # ast.parse refuses a leading U+FEFF.
            hits = _approve_all_uses(path.read_text(encoding="utf-8-sig"), str(path))
        except (SyntaxError, ValueError) as exc:  # a parse error never skips
            errors[key] = f"{type(exc).__name__}: {exc}"
            continue
        if hits:
            uses[key] = tuple(hits)
    files = frozenset(p.relative_to(_REPO).as_posix() for p in paths)
    return _Scan(uses=uses, parse_errors=errors, files=files)


def _uses_or_fail() -> dict[str, tuple[str, ...]]:
    scan = _tree_scan()
    assert not scan.parse_errors, (
        "H-211: the fence could not parse these files, so it cannot say they "
        "set no approve_all:\n  "
        + "\n  ".join(f"{p}: {e}" for p, e in sorted(scan.parse_errors.items()))
    )
    return scan.uses


# ── The fence on the tree ───────────────────────────────────────────────────


def test_the_scan_reads_the_whole_tree() -> None:
    files = _tree_scan().files
    assert "agents.py" in files, "the root agents.py is not scanned"
    for top in _SCAN_DIRS:
        assert any(f.startswith(f"{top}/") for f in files), f"nothing under {top}/ is scanned"


def test_no_approve_all_off_the_allowlist() -> None:
    uses = _uses_or_fail()
    new = {p: u for p, u in sorted(uses.items()) if p not in _ALLOWLIST}
    assert not new, (
        "H-211 (task 560): a first-party factory must not set approve_all. It "
        "approves every shell command, file write and read in every "
        "AGENT_PERMISSION_MODE. Leave on_permission_request out, so the "
        "executor installs the B6 handler:\n  "
        + "\n  ".join(f"{p}: {'; '.join(u)}" for p, u in new.items())
    )


def test_allowlist_has_no_stale_entries() -> None:
    uses = _uses_or_fail()
    stale = sorted(p for p in _ALLOWLIST if p not in uses)
    assert not stale, (
        "These _ALLOWLIST entries hold no approve_all. Delete them in this PR, "
        "so the list only shrinks:\n  " + "\n  ".join(stale)
    )


def test_every_allowlist_entry_says_why() -> None:
    for path, reason in _ALLOWLIST.items():
        assert reason.strip(), f"{path} has no reason"


def test_the_root_factory_is_not_on_the_allowlist() -> None:
    assert "agents.py" not in _ALLOWLIST


# ── The finder itself (R7: it must see a hit) ───────────────────────────────


@pytest.mark.parametrize(
    "source",
    [
        # The exact shape that task 560 removed from the root factory.
        "def build_agent():\n"
        "    from copilot import PermissionHandler\n"
        "    return GitHubCopilotAgent(default_options={\n"
        "        'model': 'tier-balanced',\n"
        "        'on_permission_request': PermissionHandler.approve_all,\n"
        "    })\n",
        "kw = {'on_permission_request': PermissionHandler.approve_all}\n",
        "agent = GitHubCopilotAgent(on_permission_request=PermissionHandler.approve_all)\n",
        "import copilot\nh = copilot.PermissionHandler.approve_all\n",
        "from copilot.permissions import approve_all\n",
        "from copilot import approve_all as allow\n",
        "h = getattr(PermissionHandler, 'approve_all')\n",
        "agent = Agent(on_permission_request='approve_all')\n",
        "opts = {'on_permission_request': 'approve_all'}\n",
    ],
)
def test_the_finder_catches_an_approve_all(source: str) -> None:
    assert _approve_all_uses(source), f"H-211 fence missed approve_all in: {source!r}"


@pytest.mark.parametrize(
    "source",
    [
        # The mode comparison is text. The escape hatch reads it.
        "if os.environ.get('AGENT_PERMISSION_MODE') == 'approve_all':\n    pass\n",
        "MODES = ('enforce', 'audit', 'approve_all')\n",
        '"""A docstring that names PermissionHandler.approve_all."""\n',
        "# PermissionHandler.approve_all in a comment\nx = 1\n",
        "agent = GitHubCopilotAgent(default_options={'model': 'tier-balanced'})\n",
        # The Notes route has a function of that name. A def is not a handler.
        "async def approve_all(request):\n    return None\n",
    ],
)
def test_the_finder_ignores_text_and_other_names(source: str) -> None:
    assert not _approve_all_uses(source), f"H-211 fence flagged a non-use: {source!r}"


# ── The root agent: the executor installs the B6 handler ────────────────────


def _load_root_agents() -> Any:
    """The repo-root ``agents.py``, by path. ``import agents`` can find another."""
    spec = importlib.util.spec_from_file_location(
        "_root_agents_h211", _REPO / "agents.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _shell_request(command: str) -> Any:
    from copilot.generated.session_events import (
        PermissionRequestShell,
        PermissionRequestShellCommand,
    )
    return PermissionRequestShell(
        can_offer_session_approval=False,
        commands=[PermissionRequestShellCommand(identifier="sh", read_only=False)],
        full_command_text=command,
        has_write_file_redirection=False,
        intention="run a command",
        possible_paths=[],
        possible_urls=[],
    )


def _write_request(file_name: str) -> Any:
    from copilot.generated.session_events import PermissionRequestWrite
    return PermissionRequestWrite(
        can_offer_session_approval=False, diff="+ a line", file_name=file_name,
        intention="write a file", new_file_contents="a line\n",
    )


_INVOCATION = {"session_id": "s-h211", "managed_settings_enabled": False}


def _approved(result: Any) -> bool:
    from copilot.generated.rpc import PermissionDecisionApproveOnce
    return isinstance(result, PermissionDecisionApproveOnce)


def _refused(result: Any) -> bool:
    from copilot.generated.rpc import PermissionDecisionReject
    return isinstance(result, PermissionDecisionReject)


def test_the_root_factory_sets_no_permission_handler() -> None:
    agent = _load_root_agents().build_agent()
    assert agent.name == "metorite"
    assert getattr(agent, "_permission_handler", "absent") is None, (
        "H-211: the root factory set a permission handler, so the executor "
        "keeps it and the B6 policy never applies"
    )
    assert _HANDLER_KEY not in (getattr(agent, "_default_options", None) or {})


def test_the_root_agent_gets_the_b6_handler_and_keeps_its_terminal(
    monkeypatch, tmp_path,
) -> None:
    """In ``enforce`` the root agent keeps its shell for a normal command, and
    the B6 policy refuses a denylisted command and a write out of its
    workspace. Under a factory ``approve_all`` both refusals were approvals."""
    from acb_skills.permission_policy import risk_aware_permission_handler
    from acb_skills.write_artifact import artifact_context_scope, bind_artifact_context
    from orchestrator._copilot_session import _install_copilot_permission_handler

    monkeypatch.setenv("AGENT_PERMISSION_MODE", "enforce")
    agent = _load_root_agents().build_agent()
    _install_copilot_permission_handler(agent)
    handler = agent._permission_handler
    assert getattr(handler, "__cc_d85_guard__", False), "the D85 guard is missing"
    assert getattr(handler, "__wrapped__", None) is risk_aware_permission_handler

    workspace = tmp_path / "ws"
    workspace.mkdir()
    with artifact_context_scope():
        # What the executor binds for a run of ``metorite`` (D85 leaves it
        # alone, so its host shell is not refused).
        bind_artifact_context(
            agent_name="metorite", workspace_root=str(workspace),
            host_shell_refused=False, agent_push_allowed=False,
        )
        assert _approved(handler(_shell_request("ls -la"), _INVOCATION))
        assert _approved(handler(_shell_request("uv run pytest tests -q"), _INVOCATION))
        assert _approved(handler(_write_request(str(workspace / "a.txt")), _INVOCATION))
        assert _refused(handler(_shell_request("rm -rf /"), _INVOCATION))
        assert _refused(handler(_write_request(str(tmp_path / "out.txt")), _INVOCATION))
