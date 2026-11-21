"""H-211 (task 560): no first-party factory sets ``approve_all``, or any handler.

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

Three fences:

* **The name rule.** An AST scan of the root ``agents.py``, ``apps/`` and
  ``packages/`` fails on each ``approve_all`` reference outside
  :data:`_NAME_ALLOWLIST`: the attribute ``X.approve_all``, an imported name
  ``approve_all``, ``getattr(X, "approve_all")``, and ``approve_all`` (a name
  or the string) as an ``on_permission_request`` value. The mode comparisons
  (``mode == "approve_all"``) are text, so they do not count.
* **The setter rule** (repair round of task 560). A handler that approves
  everything needs no such name: a ``lambda``, a local ``def``, a
  ``functools.partial`` or ``getattr(PH, "approve" + "_all")``. So the same
  scan fails on EVERY place that sets ``on_permission_request``, whatever its
  value, outside :data:`_SETTER_ALLOWLIST`: a keyword argument (``dict(...)``
  and ``.update(...)`` included), a dict-literal key, an assignment to a
  subscript with that key, and ``setdefault`` with that key. A factory leaves
  the slot empty, and the executor fills it.
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

_MUTATION_RUNNER_REASON = (
    "It runs only in the mutation sandbox container (Dockerfile.mutation "
    "ENTRYPOINT, started by mutation._run_mutation_sandbox through docker "
    "run), never on the host. Nothing on the host imports it"
)

#: The files that may name ``approve_all``, and why. Never add an entry
#: without a reason that a reviewer can check in the code.
_NAME_ALLOWLIST: dict[str, str] = {
    "apps/services/orchestrator/mutation_runner.py": _MUTATION_RUNNER_REASON,
    "apps/services/orchestrator/orchestrator/_copilot_session.py": (
        "It implements the AGENT_PERMISSION_MODE=approve_all escape hatch, "
        "and it wraps that handler in the D85 guard"
    ),
}

#: The files that may set ``on_permission_request``, and why. Never add an
#: entry without a reason that a reviewer can check in the code.
_SETTER_ALLOWLIST: dict[str, str] = {
    "apps/services/orchestrator/mutation_runner.py": _MUTATION_RUNNER_REASON,
    "apps/services/orchestrator/orchestrator/copilot_agent.py": (
        "The executor's own install path. _create_session and _resume_session "
        "copy the agent's _permission_handler, which "
        "_install_copilot_permission_handler filled inside the D85 guard, into "
        "the SDK session config. They choose no handler of their own"
    ),
}

#: The keyword of the adapter and the SDK that takes the handler.
_HANDLER_KEY = "on_permission_request"
_NAME = "approve_all"


def _is_text(node: ast.AST | None, text: str) -> bool:
    return isinstance(node, ast.Constant) and node.value == text


def _is_approve_all_value(node: ast.AST | None) -> bool:
    """True for ``X.approve_all``, a bare ``approve_all`` or the string."""
    if isinstance(node, ast.Attribute):
        return node.attr == _NAME
    if isinstance(node, ast.Name):
        return node.id == _NAME
    return _is_text(node, _NAME)


@dataclass
class _HandlerFinder(ast.NodeVisitor):
    """Collects each hit of the two rules in one tree, as ``line N: what``."""

    names: list[str] = field(default_factory=list)
    setters: list[str] = field(default_factory=list)

    @staticmethod
    def _at(node: ast.AST, what: str) -> str:
        return f"line {getattr(node, 'lineno', '?')}: {what}"

    def _name(self, node: ast.AST, what: str) -> None:
        self.names.append(self._at(node, what))

    def _setter(self, node: ast.AST, what: str) -> None:
        self.setters.append(self._at(node, what))

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr == _NAME:
            self._name(node, f"names .{_NAME}")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            if _NAME in (alias.name, alias.asname):
                self._name(node, f"imports the name {_NAME}")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if (
            isinstance(func, ast.Name) and func.id == "getattr"
            and len(node.args) >= 2 and _is_text(node.args[1], _NAME)
        ):
            self._name(node, f'getattr(..., "{_NAME}")')
        if (
            isinstance(func, ast.Attribute) and func.attr == "setdefault"
            and node.args and _is_text(node.args[0], _HANDLER_KEY)
        ):
            self._setter(node, f'setdefault("{_HANDLER_KEY}", ...)')
        for kw in node.keywords:
            if kw.arg != _HANDLER_KEY:
                continue
            if _is_approve_all_value(kw.value):
                self._name(node, f"{_HANDLER_KEY}={ast.unparse(kw.value)}")
            self._setter(node, f"{_HANDLER_KEY}= keyword")
        self.generic_visit(node)

    def visit_Dict(self, node: ast.Dict) -> None:
        for key, value in zip(node.keys, node.values, strict=True):
            if not _is_text(key, _HANDLER_KEY):
                continue
            if _is_approve_all_value(value):
                self._name(node, f'"{_HANDLER_KEY}": {ast.unparse(value)}')
            self._setter(node, f'"{_HANDLER_KEY}" dict key')
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        if isinstance(node.ctx, ast.Store) and _is_text(node.slice, _HANDLER_KEY):
            self._setter(node, f'[...]["{_HANDLER_KEY}"] = ...')
        self.generic_visit(node)


def _finder(source: str, filename: str = "<source>") -> _HandlerFinder:
    """The hits in *source*. A syntax error RAISES. It never skips."""
    finder = _HandlerFinder()
    finder.visit(ast.parse(source, filename=filename))
    return finder


def _approve_all_uses(source: str) -> list[str]:
    return _finder(source).names


def _handler_setters(source: str) -> list[str]:
    return _finder(source).setters


@dataclass(frozen=True)
class _Scan:
    names: dict[str, tuple[str, ...]]
    setters: dict[str, tuple[str, ...]]
    parse_errors: dict[str, str]
    files: frozenset[str]


def _python_files() -> list[Path]:
    out = [_REPO / name for name in _ROOT_FILES]
    for top in _SCAN_DIRS:
        out.extend(
            p for p in (_REPO / top).rglob("*.py") if not _SKIP_PARTS.intersection(p.parts)
        )
    return sorted(out)


def _scan_sources(sources: dict[str, str]) -> _Scan:
    """Scan ``{repo-relative path: source}``. A parse error is recorded."""
    names: dict[str, tuple[str, ...]] = {}
    setters: dict[str, tuple[str, ...]] = {}
    errors: dict[str, str] = {}
    for key, source in sources.items():
        try:
            found = _finder(source, key)
        except (SyntaxError, ValueError) as exc:  # a parse error never skips
            errors[key] = f"{type(exc).__name__}: {exc}"
            continue
        if found.names:
            names[key] = tuple(found.names)
        if found.setters:
            setters[key] = tuple(found.setters)
    return _Scan(names=names, setters=setters, parse_errors=errors, files=frozenset(sources))


@cache
def _tree_scan() -> _Scan:
    sources: dict[str, str] = {}
    errors: dict[str, str] = {}
    for path in _python_files():
        key = path.relative_to(_REPO).as_posix()
        try:
            # utf-8-sig: some files start with a byte order mark, and
            # ast.parse refuses a leading U+FEFF.
            sources[key] = path.read_text(encoding="utf-8-sig")
        except ValueError as exc:  # a UnicodeDecodeError never skips
            errors[key] = f"{type(exc).__name__}: {exc}"
    scan = _scan_sources(sources)
    return _Scan(
        names=scan.names, setters=scan.setters,
        parse_errors={**scan.parse_errors, **errors},
        files=scan.files | frozenset(errors),
    )


def _scan_or_fail() -> _Scan:
    scan = _tree_scan()
    assert not scan.parse_errors, (
        "H-211: the fence could not parse these files, so it cannot say they "
        "set no permission handler:\n  "
        + "\n  ".join(f"{p}: {e}" for p, e in sorted(scan.parse_errors.items()))
    )
    return scan


def _off(found: dict[str, tuple[str, ...]], allow: dict[str, str]) -> dict[str, tuple[str, ...]]:
    return {p: u for p, u in sorted(found.items()) if p not in allow}


def _fmt(found: dict[str, tuple[str, ...]]) -> str:
    return "\n  ".join(f"{p}: {'; '.join(u)}" for p, u in found.items())


# ── The fence on the tree ───────────────────────────────────────────────────


def test_the_scan_reads_the_whole_tree() -> None:
    files = _tree_scan().files
    assert "agents.py" in files, "the root agents.py is not scanned"
    for top in _SCAN_DIRS:
        assert any(f.startswith(f"{top}/") for f in files), f"nothing under {top}/ is scanned"


def test_no_approve_all_off_the_allowlist() -> None:
    new = _off(_scan_or_fail().names, _NAME_ALLOWLIST)
    assert not new, (
        "H-211 (task 560): a first-party factory must not set approve_all. It "
        "approves every shell command, file write and read in every "
        "AGENT_PERMISSION_MODE. Leave on_permission_request out, so the "
        "executor installs the B6 handler:\n  " + _fmt(new)
    )


def test_no_permission_handler_set_off_the_allowlist() -> None:
    new = _off(_scan_or_fail().setters, _SETTER_ALLOWLIST)
    assert not new, (
        "H-211 (task 560): a first-party file must not set "
        "on_permission_request. A handler of its own (a lambda, a def, a "
        "partial) stays the agent's handler, and the B6 policy never applies. "
        "Leave it out, so the executor installs the B6 handler:\n  " + _fmt(new)
    )


@pytest.mark.parametrize("rule", ["names", "setters"])
def test_allowlist_has_no_stale_entries(rule: str) -> None:
    allow = _NAME_ALLOWLIST if rule == "names" else _SETTER_ALLOWLIST
    found = getattr(_scan_or_fail(), rule)
    stale = sorted(p for p in allow if p not in found)
    assert not stale, (
        f"These {rule} allow-list entries have no hit. Delete them in this PR, "
        "so the list only shrinks:\n  " + "\n  ".join(stale)
    )


def test_every_allowlist_entry_says_why() -> None:
    for path, reason in {**_NAME_ALLOWLIST, **_SETTER_ALLOWLIST}.items():
        assert reason.strip(), f"{path} has no reason"


def test_the_root_factory_is_not_on_an_allowlist() -> None:
    assert "agents.py" not in _NAME_ALLOWLIST
    assert "agents.py" not in _SETTER_ALLOWLIST


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
        # A local alias of the SDK handler, passed by its bare name.
        "agent = Agent(on_permission_request=approve_all)\n",
        "opts = {'on_permission_request': approve_all}\n",
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


#: Factories that approve everything with no ``approve_all`` name. The name
#: rule misses each one. The setter rule must catch each one.
_NAMELESS_HANDLERS = [
    # A lambda that approves everything.
    "def build_agent():\n"
    "    return GitHubCopilotAgent(default_options={\n"
    "        'model': 'tier-balanced',\n"
    "        'on_permission_request': lambda req, inv: PermissionDecisionApproveOnce(),\n"
    "    })\n",
    # A local def.
    "def build_agent():\n"
    "    def allow(req, inv):\n"
    "        return PermissionDecisionApproveOnce()\n"
    "    return GitHubCopilotAgent(on_permission_request=allow)\n",
    # functools.partial.
    "import functools\n"
    "agent = Agent(on_permission_request=functools.partial(allow, True))\n",
    # vars() of the handler class.
    "opts = {'on_permission_request': vars(PH)['approve' + '_all']}\n",
    # getattr with a built name.
    "agent = Agent(on_permission_request=getattr(PH, 'approve' + '_all'))\n",
    # dict(...) and .update(...).
    "opts = dict(model='m', on_permission_request=allow)\n",
    "opts.update(on_permission_request=allow)\n",
    # An assignment to a subscript.
    "opts = {}\nopts['on_permission_request'] = allow\n",
    "agent._default_options['on_permission_request'] = lambda r, i: ok()\n",
    # setdefault.
    "opts.setdefault('on_permission_request', allow)\n",
]


@pytest.mark.parametrize("source", _NAMELESS_HANDLERS)
def test_the_setter_rule_catches_a_handler_with_no_name(source: str) -> None:
    assert _handler_setters(source), f"H-211 setter rule missed a handler in: {source!r}"


@pytest.mark.parametrize(
    "source",
    [
        # A read of the key sets nothing.
        "h = opts.get('on_permission_request')\n",
        "h = opts['on_permission_request']\n",
        "if 'on_permission_request' in opts:\n    pass\n",
        "agent = GitHubCopilotAgent(default_options={'model': 'tier-balanced'})\n",
        '"""A docstring: on_permission_request=lambda r, i: ok()."""\n',
    ],
)
def test_the_setter_rule_ignores_a_read(source: str) -> None:
    assert not _handler_setters(source), f"H-211 setter rule flagged a read: {source!r}"


def test_a_planted_lambda_handler_in_an_app_factory_fails_the_fence() -> None:
    """R7: the setter rule turns red on a new ``apps/agents/*/agents.py``
    whose factory passes a lambda that approves everything. The name rule
    alone let this pass (the verifier's and the reviewer's finding)."""
    rel = "apps/agents/agent-probe/agents.py"
    scan = _scan_sources({rel: _NAMELESS_HANDLERS[0]})
    assert not scan.parse_errors
    assert not _off(scan.names, _NAME_ALLOWLIST), "the name rule now sees it"
    assert rel in _off(scan.setters, _SETTER_ALLOWLIST)


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
