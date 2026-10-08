"""BH-F1 — every child process of the gateway gets the one allowlisted env.

Spec ``project-docs/specs/box_hardening.md`` §5 BH-1, decisions BH-D1 to
BH-D3, and HANDOFF H-270. The gateway env holds every secret of the box. A
child that inherits it can read all of them, and the Copilot CLI child has a
shell tool that a prompt injection can reach.

The fence is an AST scan of ``apps/`` and ``packages/``. Each spawn call must
pass ``env=`` from ``acb_common.child_env`` (``child_env``, ``copilot_env`` or
``docker_env``). Each ``CopilotClient(`` must pass ``env=`` too. The scan
reads import aliases, so ``from subprocess import run as r`` is still a spawn.

What breaks this fence:

- a spawn or a ``CopilotClient(`` with no ``env=``, or ``env=None``;
- ``env=`` from anything but the helper, or from a name or a local function
  that is not bound to the helper;
- ``os.environ`` anywhere inside ``extra=``, or a later
  ``.update(os.environ)`` on the name the helper's result is bound to;
- ``os.system``, ``os.popen``, ``pty.spawn`` and every other call that cannot
  take an env;
- an exemption that no longer matches a call.

The exempt lists below name each file or call and its reason.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

import pytest
from acb_common import child_env as seam

REPO = Path(__file__).resolve().parents[2]
ROOTS = ("apps", "packages")
HELPER_MODULE = "acb_common.child_env"
HELPERS = frozenset({"child_env", "copilot_env", "docker_env"})
HELPER_DOTTED = frozenset(f"{HELPER_MODULE}.{h}" for h in HELPERS)
ENV_VALUES = f"{HELPER_MODULE}.env_values"

#: Whole files the scan skips. Each one runs in a container, not in the
#: gateway, so the gateway env never reaches it.
EXEMPT_FILES: dict[str, str] = {
    "apps/services/meeting_bot/":
        "runs in the meeting-bot container. Compose gives it its env.",
    "apps/services/orchestrator/sandbox/":
        "runs in the coding sandbox container (data_engine.py). The broker "
        "builds its env with --env.",
    "apps/services/orchestrator/mutation_runner.py":
        "runs in the mutation container (Dockerfile.mutation ENTRYPOINT). "
        "mutation.py hands it its env with -e.",
}

#: Single calls the scan skips: (file, function, a text the call holds).
EXEMPT_CALLS: dict[tuple[str, str, str], str] = {
    (
        "apps/services/orchestrator/orchestrator/copilot_agent.py",
        "MetoriteCopilotAgent.start",
        "RuntimeConnection.for_uri",
    ): "connects to a sandbox CLI over a URI and spawns nothing",
    (
        "apps/services/gateway/gateway/routes/workflows/engine/modules.py",
        "run_module_code",
        "env={}",
    ): "passes an empty env on purpose, which is stricter than the helper",
}

#: Spawns that take ``env=`` as a keyword.
SPAWN_KW = frozenset({
    "subprocess.run", "subprocess.Popen", "subprocess.call",
    "subprocess.check_call", "subprocess.check_output",
    "asyncio.create_subprocess_exec", "asyncio.create_subprocess_shell",
    "asyncio.subprocess.create_subprocess_exec",
    "asyncio.subprocess.create_subprocess_shell",
    "anyio.run_process", "anyio.open_process",
})
#: Loop methods that take ``env=`` as a keyword, matched on the attribute.
LOOP_ATTRS = frozenset({"subprocess_exec", "subprocess_shell"})
#: Spawns that take the env as a positional argument: name -> index (-1 = last).
SPAWN_POS = {
    "os.execle": -1, "os.execlpe": -1, "os.execve": 2, "os.execvpe": 2,
    "os.spawnle": -1, "os.spawnlpe": -1, "os.spawnve": 3, "os.spawnvpe": 3,
    "os.posix_spawn": 2, "os.posix_spawnp": 2,
}
#: Spawns that cannot take an env at all. Use subprocess with env= instead.
SPAWN_NO_ENV = frozenset({
    "os.system", "os.popen", "pty.spawn",
    "subprocess.getoutput", "subprocess.getstatusoutput",
    "os.execl", "os.execlp", "os.execv", "os.execvp",
    "os.spawnl", "os.spawnlp", "os.spawnv", "os.spawnvp",
})

_SCOPES = ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda


@dataclass
class Report:
    spawns: int = 0
    copilot: int = 0
    violations: list[str] = field(default_factory=list)
    exempt_used: set[tuple[str, str, str]] = field(default_factory=set)


_MUTATORS = frozenset({"update", "setdefault", "__setitem__", "__ior__", "__or__"})


def _names_in(target: ast.AST, name: str) -> bool:
    return any(isinstance(n, ast.Name) and n.id == name for n in ast.walk(target))


def _collect(node: ast.AST, name: str, values: list[ast.AST], mutations: list[ast.AST]) -> bool:
    """Add what *node* does to *name*. True when *node* binds *name*."""
    if isinstance(node, ast.Assign):
        bound = False
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == name:
                values.append(node.value)
                bound = True
            elif isinstance(target, ast.Subscript) and _names_in(target.value, name):
                mutations.append(node.value)
        return bound
    if isinstance(node, ast.AnnAssign | ast.NamedExpr):
        if isinstance(node.target, ast.Name) and node.target.id == name:
            if node.value is not None:
                values.append(node.value)
            return True
        return False
    if isinstance(node, ast.AugAssign):
        if isinstance(node.target, ast.Name) and node.target.id == name:
            mutations.append(node.value)
            return True
        return False
    if isinstance(node, ast.Call):
        f = node.func
        if (
            isinstance(f, ast.Attribute)
            and isinstance(f.value, ast.Name)
            and f.value.id == name
            and f.attr in _MUTATORS
        ):
            mutations.extend([*node.args, *(k.value for k in node.keywords)])
        return False
    if isinstance(node, ast.For | ast.AsyncFor):
        targets = [node.target]
    elif isinstance(node, ast.With | ast.AsyncWith):
        targets = [i.optional_vars for i in node.items if i.optional_vars]
    else:
        return False
    hits = [t for t in targets if _names_in(t, name)]
    values.extend(hits)
    return bool(hits)


class _Module:
    """One parsed file, with the lookups the rules need."""

    def __init__(self, source: str, rel: str) -> None:
        self.rel = rel
        self.tree = ast.parse(source)
        self.parent: dict[int, ast.AST] = {}
        for node in ast.walk(self.tree):
            for child in ast.iter_child_nodes(node):
                self.parent[id(child)] = node
        self.alias: dict[str, str] = {}
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    if a.asname:
                        self.alias[a.asname] = a.name
                    else:
                        top = a.name.split(".")[0]
                        self.alias[top] = top
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                for a in node.names:
                    self.alias[a.asname or a.name] = f"{node.module}.{a.name}"
        self.functions: dict[str, ast.AST] = {
            n.name: n for n in self.tree.body
            if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)
        }

    # ── names ──────────────────────────────────────────────────────────────

    def dotted(self, node: ast.AST) -> str | None:
        if isinstance(node, ast.Name):
            return self.alias.get(node.id, node.id)
        if isinstance(node, ast.Attribute):
            base = self.dotted(node.value)
            return f"{base}.{node.attr}" if base else None
        return None

    def mentions_environ(self, node: ast.AST) -> bool:
        for sub in ast.walk(node):
            if isinstance(sub, ast.Attribute) and sub.attr in {"environ", "environb"}:
                return True
            if isinstance(sub, ast.Name) and self.dotted(sub) in {"os.environ", "os.environb"}:
                return True
        return False

    def scopes(self, node: ast.AST) -> list[ast.AST]:
        """The enclosing function scopes of *node*, innermost first, then the module."""
        out: list[ast.AST] = []
        cur = self.parent.get(id(node))
        while cur is not None:
            if isinstance(cur, _SCOPES):
                out.append(cur)
            cur = self.parent.get(id(cur))
        out.append(self.tree)
        return out

    def qualname(self, node: ast.AST) -> str:
        parts: list[str] = []
        cur = self.parent.get(id(node))
        while cur is not None:
            if isinstance(cur, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                parts.append(cur.name)
            cur = self.parent.get(id(cur))
        return ".".join(reversed(parts)) or "<module>"

    def _own(self, scope: ast.AST) -> list[ast.AST]:
        """The nodes of *scope* that do not sit in a nested function."""
        out: list[ast.AST] = []
        stack = list(ast.iter_child_nodes(scope))
        while stack:
            node = stack.pop()
            out.append(node)
            if not isinstance(node, _SCOPES | ast.ClassDef):
                stack.extend(ast.iter_child_nodes(node))
        return out

    def bindings(self, name: str, scopes: list[ast.AST]) -> tuple[list[ast.AST], list[ast.AST]]:
        """(values assigned to *name*, its mutating nodes), in the scope that binds it."""
        for scope in scopes:
            values: list[ast.AST] = []
            mutations: list[ast.AST] = []
            bound = False
            for node in self._own(scope):
                bound = _collect(node, name, values, mutations) or bound
            if bound:
                return values, mutations
        return [], []

    # ── the rules ──────────────────────────────────────────────────────────

    def env_problem(self, expr: ast.AST, scopes: list[ast.AST], depth: int = 0) -> str | None:
        """None when *expr* is the helper's env. Else the reason it is not."""
        if depth > 4:
            return "env= is bound too indirectly to read"
        if isinstance(expr, ast.Call):
            return self._call_env_problem(expr, scopes, depth)
        if isinstance(expr, ast.Name):
            values, mutations = self.bindings(expr.id, scopes)
            if not values:
                return f"env={expr.id} is not bound to the helper"
            for value in values:
                problem = self.env_problem(value, scopes, depth + 1)
                if problem:
                    return f"env={expr.id}: {problem}"
            if any(self.mentions_environ(mut) for mut in mutations):
                return f"{expr.id} takes os.environ after the helper built it"
            return None
        return f"env={ast.unparse(expr)[:60]} is not the helper's env"

    def _call_env_problem(self, expr: ast.Call, scopes: list[ast.AST], depth: int) -> str | None:
        if self.dotted(expr.func) in HELPER_DOTTED:
            if expr.args:
                return "the helper takes keywords only"
            for kw in expr.keywords:
                if kw.arg != "extra":
                    return f"the helper takes no {kw.arg or '**'} argument"
                problem = self.extra_problem(kw.value, scopes)
                if problem:
                    return problem
            return None
        if isinstance(expr.func, ast.Name) and expr.func.id in self.functions:
            name = expr.func.id
            returns = [n for n in self._own(self.functions[name]) if isinstance(n, ast.Return)]
            if not returns:
                return f"{name}() returns no env"
            inner = self.scopes(returns[0])
            for ret in returns:
                if ret.value is None:
                    return f"{name}() can return None"
                problem = self.env_problem(ret.value, inner, depth + 1)
                if problem:
                    return f"{name}(): {problem}"
            return None
        return f"env= comes from {ast.unparse(expr.func)}(), not from {HELPER_MODULE}"

    def extra_problem(self, expr: ast.AST, scopes: list[ast.AST], depth: int = 0) -> str | None:
        if depth > 4:
            return "extra= is bound too indirectly to read"
        if self.mentions_environ(expr):
            return "os.environ inside extra= gives the child every secret"
        for sub in ast.walk(expr):
            if isinstance(sub, ast.Call):
                if self.dotted(sub.func) == ENV_VALUES:
                    problem = self.env_values_problem(sub)
                    if problem:
                        return problem
                elif isinstance(sub.func, ast.Name) and sub.func.id in self.functions:
                    if self.mentions_environ(self.functions[sub.func.id]):
                        return f"extra= calls {sub.func.id}(), which reads os.environ"
        if isinstance(expr, ast.Name):
            values, mutations = self.bindings(expr.id, scopes)
            for node in [*values, *mutations]:
                problem = self.extra_problem(node, scopes, depth + 1)
                if problem:
                    return problem
        return None

    def env_values_problem(self, call: ast.Call) -> str | None:
        if call.keywords:
            return "env_values takes names only"
        for arg in call.args:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                continue
            if isinstance(arg, ast.Starred) and isinstance(arg.value, ast.Name):
                if self.dotted(arg.value).startswith(f"{HELPER_MODULE}."):
                    continue
                values, mutations = self.bindings(arg.value.id, [self.tree])
                if (
                    len(values) == 1
                    and not mutations
                    and isinstance(values[0], ast.Tuple | ast.List)
                    and all(
                        isinstance(e, ast.Constant) and isinstance(e.value, str)
                        for e in values[0].elts
                    )
                ):
                    continue
            return f"env_values({ast.unparse(arg)}) is not a literal name"
        return None


def _spawn_kind(mod: _Module, call: ast.Call) -> str | None:
    target = mod.dotted(call.func)
    if target in SPAWN_KW or target in SPAWN_POS or target in SPAWN_NO_ENV:
        return target
    if isinstance(call.func, ast.Attribute) and call.func.attr in LOOP_ATTRS:
        return f"loop.{call.func.attr}"
    return None


def _is_copilot_client(mod: _Module, call: ast.Call) -> bool:
    target = mod.dotted(call.func) or ""
    return target.split(".")[-1] == "CopilotClient"


def _env_expr(kind: str, call: ast.Call) -> tuple[ast.AST | None, str | None]:
    """(the env expression, or None with the reason)."""
    if kind in SPAWN_NO_ENV:
        return None, f"{kind} cannot take an env. Use subprocess with env=child_env()"
    if kind in SPAWN_POS:
        for kw in call.keywords:
            if kw.arg == "env":
                return kw.value, None
        index = SPAWN_POS[kind]
        if not call.args or (index >= 0 and len(call.args) <= index):
            return None, f"{kind} has no env argument"
        return call.args[index], None
    for kw in call.keywords:
        if kw.arg == "env":
            return kw.value, None
    if any(kw.arg is None for kw in call.keywords):
        return None, "env= is hidden in **kwargs. Pass env= by name"
    return None, "no env=, so the child inherits every secret of the gateway"


def scan_source(source: str, rel: str, report: Report | None = None) -> Report:
    """Scan one file. The self-tests call this with a snippet."""
    report = report or Report()
    mod = _Module(source, rel)
    for node in ast.walk(mod.tree):
        if not isinstance(node, ast.Call):
            continue
        kind = _spawn_kind(mod, node)
        if kind is None and _is_copilot_client(mod, node):
            kind = "CopilotClient"
        if kind is None:
            continue
        if kind == "CopilotClient":
            report.copilot += 1
        else:
            report.spawns += 1
        qual = mod.qualname(node)
        text = ast.unparse(node)
        exempt = next(
            (key for key in EXEMPT_CALLS if key[0] == rel and key[1] == qual and key[2] in text),
            None,
        )
        if exempt:
            report.exempt_used.add(exempt)
            continue
        expr, problem = _env_expr(kind if kind != "CopilotClient" else "subprocess.run", node)
        if expr is not None:
            problem = mod.env_problem(expr, mod.scopes(node))
        if problem:
            report.violations.append(f"{rel}:{node.lineno} {qual} {kind}: {problem}")
    return report


def _exempt_file(rel: str) -> bool:
    return any(
        rel == entry or (entry.endswith("/") and rel.startswith(entry))
        for entry in EXEMPT_FILES
    )


def _python_files() -> list[Path]:
    out: list[Path] = []
    for root in ROOTS:
        out.extend(
            p for p in (REPO / root).rglob("*.py")
            if not {"__pycache__", ".venv", "node_modules"} & set(p.parts)
        )
    return sorted(out)


@cache
def _tree_report() -> Report:
    report = Report()
    for path in _python_files():
        rel = path.relative_to(REPO).as_posix()
        if _exempt_file(rel):
            continue
        scan_source(path.read_text(encoding="utf-8-sig"), rel, report)
    return report


# ── the fence ────────────────────────────────────────────────────────────────


def test_every_spawn_passes_the_one_env() -> None:
    violations = _tree_report().violations
    assert not violations, (
        "These spawns can hand the gateway env to a child (WS-49 BH-1, H-270). "
        "Pass env=child_env(), copilot_env() or docker_env() from "
        "acb_common.child_env, and add a name only by value with extra=:\n"
        + "\n".join(violations)
    )


def test_the_scan_sees_the_tree() -> None:
    """A scan that finds nothing passes the fence by finding nothing."""
    report = _tree_report()
    assert report.spawns >= 50, report.spawns
    # copilot_agent.py (two), main.py and executor.py.
    assert report.copilot >= 4, report.copilot


def test_every_exemption_still_matches() -> None:
    for entry in EXEMPT_FILES:
        assert (REPO / entry.rstrip("/")).exists(), f"{entry} is gone. Drop its exemption."
        files = [
            p for p in _python_files()
            if _exempt_file(p.relative_to(REPO).as_posix())
            and p.relative_to(REPO).as_posix().startswith(entry.rstrip("/"))
        ]
        found = Report()
        for path in files:
            scan_source(path.read_text(encoding="utf-8-sig"), "x", found)
        assert found.spawns + found.copilot, f"{entry} spawns nothing. Drop its exemption."
    unused = set(EXEMPT_CALLS) - _tree_report().exempt_used
    assert not unused, f"These exemptions match no call. Drop them: {sorted(unused)}"


# ── the self-test: the fence can go red ─────────────────────────────────────

_RED = {
    "no env": "import subprocess\nsubprocess.run(['git'])",
    "env None": "import subprocess\nsubprocess.run(['git'], env=None)",
    "full env copy": "import os, subprocess\nsubprocess.run(['git'], env=dict(os.environ))",
    "alias module": "import subprocess as sp\nsp.Popen(['git'])",
    "alias function": "from subprocess import check_output as co\nco(['git'])",
    "asyncio": "import asyncio\nasync def f():\n    await asyncio.create_subprocess_exec('git')",
    "asyncio alias": "from asyncio import create_subprocess_shell as s\nasync def f():\n    await s('ls')",
    "loop": "async def f(loop):\n    await loop.subprocess_exec(object, 'git')",
    "anyio": "import anyio\nasync def f():\n    await anyio.run_process(['git'])",
    "os.system": "import os\nos.system('ls')",
    "os.popen": "import os\nos.popen('ls')",
    "os.execv": "import os\nos.execv('/bin/ls', ['ls'])",
    "os.execve full env": "import os\nos.execve('/bin/ls', ['ls'], os.environ)",
    "posix_spawn": "import os\nos.posix_spawn('/bin/ls', ['ls'], os.environ)",
    "pty": "import pty\npty.spawn('sh')",
    "getoutput": "import subprocess\nsubprocess.getoutput('ls')",
    "copilot no env": "from copilot import CopilotClient as C\nC(github_token='t')",
    "copilot kwargs": "from copilot import CopilotClient\nCopilotClient(**opts)",
    "copilot module": "import copilot\ncopilot.CopilotClient()",
    "extra os.environ": (
        "import os, subprocess\nfrom acb_common.child_env import child_env\n"
        "subprocess.run(['git'], env=child_env(extra=dict(os.environ)))"
    ),
    "extra environ get": (
        "import os, subprocess\nfrom acb_common.child_env import child_env\n"
        "subprocess.run(['git'], env=child_env(extra={'K': os.environ.get('K')}))"
    ),
    "extra from environ import": (
        "import subprocess\nfrom os import environ\nfrom acb_common.child_env import child_env\n"
        "subprocess.run(['git'], env=child_env(extra={**environ}))"
    ),
    "extra bound name": (
        "import os, subprocess\nfrom acb_common.child_env import child_env\n"
        "def f():\n    x = {}\n    x.update(os.environ)\n"
        "    subprocess.run(['git'], env=child_env(extra=x))"
    ),
    "update after bind": (
        "import os, subprocess\nfrom acb_common.child_env import child_env\n"
        "def f():\n    e = child_env()\n    e.update(os.environ)\n"
        "    subprocess.run(['git'], env=e)"
    ),
    "ior after bind": (
        "import os, subprocess\nfrom acb_common.child_env import child_env\n"
        "def f():\n    e = child_env()\n    e |= os.environ\n"
        "    subprocess.run(['git'], env=e)"
    ),
    "rebound name": (
        "import os, subprocess\nfrom acb_common.child_env import child_env\n"
        "def f():\n    e = child_env()\n    e = dict(os.environ)\n"
        "    subprocess.run(['git'], env=e)"
    ),
    "wrong helper": "import subprocess\nsubprocess.run(['git'], env=make_env())",
    "local func full env": (
        "import os, subprocess\ndef _env():\n    return dict(os.environ)\n"
        "subprocess.run(['git'], env=_env())"
    ),
    "env_values variable": (
        "import subprocess\nfrom acb_common.child_env import child_env, env_values\n"
        "def f(n):\n    subprocess.run(['git'], env=child_env(extra=env_values(n)))"
    ),
}

_GREEN = {
    "direct": (
        "import subprocess\nfrom acb_common.child_env import child_env\n"
        "subprocess.run(['git'], env=child_env())"
    ),
    "alias helper": (
        "import subprocess\nfrom acb_common.child_env import child_env as ce\n"
        "subprocess.run(['git'], env=ce())"
    ),
    "module helper": (
        "import subprocess\nfrom acb_common import child_env as m\n"
        "subprocess.run(['git'], env=m.child_env())"
    ),
    "extra literal": (
        "import subprocess\nfrom acb_common.child_env import child_env\n"
        "subprocess.run(['git'], env=child_env(extra={'GIT_SEQUENCE_EDITOR': 'true'}))"
    ),
    "extra env_values": (
        "import subprocess\nfrom acb_common.child_env import child_env, env_values\n"
        "NAMES = ('A', 'B')\n"
        "subprocess.run(['gh'], env=child_env(extra={**env_values('GH_TOKEN'), **env_values(*NAMES)}))"
    ),
    "bound name": (
        "import asyncio\nfrom acb_common.child_env import child_env\n"
        "async def f(v):\n    env = child_env(extra={'X': v})\n    env['Y'] = 'z'\n"
        "    await asyncio.create_subprocess_exec('node', env=env)"
    ),
    "local factory": (
        "import subprocess\nfrom acb_common.child_env import child_env\n"
        "EXTRA = {'A': '1'}\n"
        "def _env():\n    extra = dict(EXTRA)\n    extra['B'] = '2'\n    return child_env(extra=extra)\n"
        "subprocess.run(['git'], env=_env())"
    ),
    "copilot": (
        "from copilot import CopilotClient\nfrom acb_common.child_env import copilot_env\n"
        "CopilotClient(**opts, env=copilot_env())"
    ),
    "docker": (
        "import asyncio\nfrom acb_common.child_env import docker_env\n"
        "async def f():\n    await asyncio.create_subprocess_exec('docker', 'ps', env=docker_env())"
    ),
    "not a spawn": "import subprocess\nx = subprocess.PIPE\nrun(['git'])\nobj.run('x')",
}


@pytest.mark.parametrize("name", sorted(_RED))
def test_the_fence_refuses(name: str) -> None:
    report = scan_source(_RED[name], "selftest.py")
    assert report.violations, f"the fence missed: {name}"


@pytest.mark.parametrize("name", sorted(_GREEN))
def test_the_fence_accepts(name: str) -> None:
    report = scan_source(_GREEN[name], "selftest.py")
    assert not report.violations, report.violations


_REAL = "apps/services/gateway/gateway/routes/apps/durability.py"


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("env=child_env(),", "env=None,"),
        ("env=child_env(),", "env=child_env(extra=dict(os.environ)),"),
        ("env=child_env(),", ""),
    ],
    ids=["env-None", "extra-os-environ", "env-removed"],
)
def test_a_mutated_real_file_goes_red(old: str, new: str) -> None:
    """The same mutations, on a file of the tree, not a snippet."""
    source = (REPO / _REAL).read_text(encoding="utf-8")
    assert old in source
    assert not scan_source(source, _REAL).violations
    assert scan_source(source.replace(old, new, 1), _REAL).violations


# ── the helper ───────────────────────────────────────────────────────────────

_CANARIES = {
    "DATABASE_URL": "postgresql://u:canary@h/db",
    "GATEWAY_INTERNAL_TOKEN": "canary-internal",
    "OPENAI_API_KEY": "sk-canary",
    "BH1_CANARY_SECRET": "canary",
    "GITHUB_TOKEN": "ghp_canary",
    "GH_TOKEN": "gho_canary",
    "XDG_RUNTIME_DIR": "/run/user/1000",
    "LC_SECRET_KEY": "canary",
    "DOCKER_HOST": "unix:///canary.sock",
}


@pytest.fixture
def canaries(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in _CANARIES.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setenv("HOME", "/home/acb")
    monkeypatch.setenv("LC_ALL", "C.UTF-8")
    monkeypatch.setenv("XDG_CACHE_HOME", "/home/acb/.cache")
    monkeypatch.setenv("UV_CACHE_DIR", "/var/cache/acb-gateway/uv")
    monkeypatch.setenv("SHELL", "/bin/bash")
    monkeypatch.setenv("PYTHONPATH", "/srv/py")
    monkeypatch.setenv("VIRTUAL_ENV", "/srv/venv")


def _no_canary(env: dict[str, str], *allowed: str) -> None:
    for name in _CANARIES:
        if name not in allowed:
            assert name not in env, name
    leaked = [v for k, v in env.items() if "canary" in v and k not in allowed]
    assert not leaked, leaked


def test_child_env_is_the_allowlist(canaries: None) -> None:
    env = seam.child_env()
    _no_canary(env)
    assert env["PATH"] == "/usr/bin"
    assert env["HOME"] == "/home/acb"
    assert env["LC_ALL"] == "C.UTF-8"
    assert env["XDG_CACHE_HOME"] == "/home/acb/.cache"
    assert env["UV_CACHE_DIR"] == "/var/cache/acb-gateway/uv"
    assert env["PYTHONPATH"] == "/srv/py"
    assert env["VIRTUAL_ENV"] == "/srv/venv"
    assert "SHELL" not in env


def test_extra_adds_by_value_and_drops_none(canaries: None) -> None:
    env = seam.child_env(extra={"GIT_SEQUENCE_EDITOR": "true", "GONE": None})
    assert env["GIT_SEQUENCE_EDITOR"] == "true"
    assert "GONE" not in env
    assert seam.child_env(extra=seam.env_values("GH_TOKEN"))["GH_TOKEN"] == "gho_canary"
    assert seam.env_values("NOT_SET_BH1") == {}


def test_extra_refuses_the_live_environ() -> None:
    import os

    with pytest.raises(TypeError):
        seam.child_env(extra=os.environ)
    with pytest.raises(TypeError):
        seam.child_env(extra={"N": 1})  # type: ignore[dict-item]


def test_copilot_env_holds_no_token(canaries: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COPILOT_CLI_PATH", "/opt/copilot")
    monkeypatch.setenv("AGENTS_CLONE_DIR", "/home/acb/.acb/agents")
    env = seam.copilot_env()
    _no_canary(env)
    for name in ("GITHUB_TOKEN", "GH_TOKEN", "COPILOT_GITHUB_TOKEN", "COPILOT_SDK_AUTH_TOKEN"):
        assert name not in env
    assert env["SHELL"] == "/bin/bash"
    assert env["COPILOT_CLI_PATH"] == "/opt/copilot"
    assert env["AGENTS_CLONE_DIR"] == "/home/acb/.acb/agents"


def test_docker_env_adds_the_daemon_names_only(canaries: None) -> None:
    env = seam.docker_env()
    _no_canary(env, "DOCKER_HOST")
    assert env["DOCKER_HOST"] == "unix:///canary.sock"
