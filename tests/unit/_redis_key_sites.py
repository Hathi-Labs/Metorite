"""The AST scanner of raw Redis key sites (task 570, H5, R5).

A raw site is a place that reaches Redis through a client that is NOT the
tenant wrapper ``acb_common.tenant_redis``. The scanner finds three kinds:

* ``connect`` builds a raw client (``redis.from_url``, ``Redis(...)``,
  ``ConnectionPool``) or hands ``redis_url`` to a third party.
* ``key`` calls a command on a raw client, a raw pipeline or a raw pubsub.
  Each command names a key or a channel, so each call is one site.
* ``handoff`` passes a raw client to a callee that the scanner cannot
  follow (a third-party function). A callee in the tree is followed instead:
  its parameter becomes raw, and its own commands count as ``key`` sites.

The scan is an AST taint pass, not a regex. It follows import aliases
(``import redis.asyncio as aioredis``, ``from redis import Redis as R``),
module globals, ``self`` attributes, raw factory functions across modules
(``stream_relay._get_client``), a raw annotation on a parameter, and a raw
client passed into a function of the tree. It runs to a fixed point.

``tests/unit/test_redis_key_sites.py`` holds the allow-list and the ratchet.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

#: The directories of the scan.
SCAN_ROOTS = ("apps", "packages", "scripts")

#: The wrapper itself. It is the one place that may reach redis-py.
WRAPPER = "packages/acb_common/acb_common/tenant_redis.py"

#: A call of one of these names under the ``redis`` package builds a client.
RAW_CTORS = frozenset({
    "from_url", "Redis", "StrictRedis", "ConnectionPool",
    "BlockingConnectionPool", "RedisCluster", "Sentinel", "from_pool",
})

#: A name under the ``redis`` package with one of these last segments is a
#: client type. An annotation that names one makes its target raw.
RAW_TYPES = frozenset({
    "Redis", "StrictRedis", "Pipeline", "PubSub", "RedisCluster",
})

#: A method on a raw object that gives another raw object, and names no key.
PROPAGATE = frozenset({"pipeline", "pubsub", "client", "monitor"})

#: A method on a raw object that names no key.
NON_KEY = frozenset({
    "ping", "close", "aclose", "execute", "info", "reset", "dbsize",
    "get_message", "listen", "initialize", "connection_pool",
    "get_connection", "release", "disconnect", "echo", "time",
    "multi", "watch_none", "__aenter__", "__aexit__", "__enter__", "__exit__",
})

#: The text test of a file that may start a taint: it imports the ``redis``
#: package, or it reads ``redis_url``. Each other file enters the pass only
#: when it names a raw factory, a raw global or a raw-parameter function.
_SEED = re.compile(r"^[ \t]*(?:import[ \t]+redis\b|from[ \t]+redis\b)|\bredis_url\b", re.M)

#: The wrapper class. A raw client passed to it is wrapped, not handed off.
WRAPPER_CLASSES = frozenset({"TenantRedis"})


@dataclass(frozen=True)
class Site:
    """One raw Redis site."""

    path: str
    line: int
    function: str
    kind: str
    method: str
    text: str

    @property
    def where(self) -> str:
        return f"{self.path}::{self.function}"


@dataclass
class _Module:
    path: str
    name: str
    tree: ast.Module
    source: str
    aliases: dict[str, str] = field(default_factory=dict)
    defs: dict[str, ast.AST] = field(default_factory=dict)
    raw_globals: set[str] = field(default_factory=set)
    raw_attrs: set[str] = field(default_factory=set)
    classes: dict[str, ast.ClassDef] = field(default_factory=dict)


def module_name_in(rel: str, files: dict[str, str]) -> str:
    """The dotted import name of *rel*, from the package chain in *files*.

    A file outside any package (``scripts/check_infra.py``) takes its path.
    """
    folders = rel.split("/")[:-1]
    stem = rel.rsplit("/", 1)[-1].removesuffix(".py")
    parts = [] if stem == "__init__" else [stem]
    depth = len(folders)
    while depth and "/".join([*folders[:depth], "__init__.py"]) in files:
        parts.insert(0, folders[depth - 1])
        depth -= 1
    if depth == len(folders):
        return rel.removesuffix(".py").replace("/", ".")
    return ".".join(parts)


def _collect_aliases(mod: _Module) -> None:
    package = mod.name if mod.path.endswith("__init__.py") else mod.name.rpartition(".")[0]
    for node in ast.walk(mod.tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.asname:
                    mod.aliases[a.asname] = a.name
                else:
                    top = a.name.split(".")[0]
                    mod.aliases[top] = top
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                anchor = package.split(".")
                if node.level > 1:
                    anchor = anchor[: len(anchor) - (node.level - 1)]
                base = ".".join([*anchor, base] if base else anchor)
            for a in node.names:
                if a.name == "*":
                    continue
                mod.aliases[a.asname or a.name] = f"{base}.{a.name}"


def _collect_defs(mod: _Module) -> None:
    def visit(parent: ast.AST, prefix: str) -> None:
        for node in ast.iter_child_nodes(parent):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qual = f"{prefix}{node.name}"
                mod.defs[qual] = node
                visit(node, f"{qual}.")
            elif isinstance(node, ast.ClassDef):
                mod.classes[f"{prefix}{node.name}"] = node
                visit(node, f"{prefix}{node.name}.")
            elif isinstance(node, (ast.stmt, ast.excepthandler, ast.match_case)):
                visit(node, prefix)

    visit(mod.tree, "")


class Scanner:
    """The whole-tree taint pass. Build once, then read :attr:`sites`."""

    def __init__(self, files: dict[str, str]) -> None:
        self.modules: dict[str, _Module] = {}
        self.by_name: dict[str, _Module] = {}
        #: The text of each file. A file enters the AST pass only when its
        #: text names ``redis`` or the short name of a raw factory, a raw
        #: global or a function with a raw parameter. The filter is a speed
        #: step only: a file that names none of them can hold no raw site.
        self._pending = dict(files)
        self._rel_of_files = files
        self._lazy = False
        self._words: dict[str, re.Pattern[str]] = {}
        #: The dotted name of each file, from the ``__init__.py`` files of
        #: *files*. The tree and a self-test source take the same path.
        self._rel_of = {module_name_in(rel, files): rel for rel in files}
        self._nodes: dict[int, list[ast.AST]] = {}
        self._sums: dict[int, dict[str, list]] = {}
        self._admit(lambda rel, text: bool(_SEED.search(text)))
        self.factories: set[str] = set()
        self.raw_params: dict[str, set[str]] = {}
        self.sites: list[Site] = []
        self._run()

    def _admit(self, wanted) -> bool:
        """Parse each pending file whose text *wanted* accepts."""
        took = [rel for rel, text in self._pending.items() if wanted(rel, text)]
        for rel in took:
            source = self._pending.pop(rel)
            tree = ast.parse(source, filename=rel)
            name = module_name_in(rel, self._rel_of_files)
            mod = _Module(rel, name, tree, source)
            _collect_aliases(mod)
            _collect_defs(mod)
            self.modules[rel] = mod
            self.by_name[name] = mod
            self._seed_globals(mod)
        return bool(took)

    def _module(self, name: str) -> _Module | None:
        """The module of dotted *name*.

        A pending package ``__init__`` is parsed on demand, so a re-export
        resolves. Another pending file is parsed only while :attr:`_lazy` is
        set: a raw client goes into a call, and the callee must be found.
        """
        mod = self.by_name.get(name)
        wanted = self._rel_of.get(name)
        if mod is None and wanted in self._pending and (
            self._lazy or wanted.endswith("__init__.py")
        ):
            self._admit(lambda rel, _text: rel == wanted)
            mod = self.by_name.get(name)
        return mod

    def _reach(self) -> list[tuple[str, str, tuple[str, ...]]]:
        """For each raw factory, raw global and raw-parameter function: its
        short name, its form as an attribute of its module (``relay._get``),
        and the import texts of its module chain (``from app.relay import``,
        ``from app import``, ``import app.relay``). A file reaches it when its
        text holds the attribute form, or an import text and the name."""
        dotted = set(self.factories) | set(self.raw_params)
        for mod in list(self.modules.values()):
            dotted |= {f"{mod.name}.{g}" for g in mod.raw_globals}
        out = []
        for fq in sorted(dotted):
            *segs, last = fq.split(".")
            imports = tuple(f"from {'.'.join(segs[:n])} import" for n in range(1, len(segs) + 1))
            imports += (f"import {'.'.join(segs)}",)
            out.append((last, f"{segs[-1]}.{last}" if segs else last, imports))
        return out

    def _reaches(self, rel: str, text: str, reach: list[tuple[str, str, tuple[str, ...]]]) -> bool:
        """True when *text* can reach one entry of *reach*. A plain substring
        test runs first: it is the cheap one."""
        for last, attr, imports in reach:
            if last not in text:
                continue
            if attr in text:
                return True
            if any(i in text for i in imports) and self._word(last).search(text):
                return True
        return False

    def _word(self, name: str) -> re.Pattern[str]:
        pattern = self._words.get(name)
        if pattern is None:
            pattern = self._words[name] = re.compile(rf"(?<!\w){re.escape(name)}(?!\w)")
        return pattern

    # -- name resolution ---------------------------------------------------

    def _resolve(self, mod: _Module, node: ast.AST, cls: str | None) -> str | None:
        if isinstance(node, ast.Name):
            if node.id in mod.aliases:
                return self._follow(mod.aliases[node.id])
            if node.id in mod.defs or node.id in mod.raw_globals:
                return f"{mod.name}.{node.id}"
            return None
        if isinstance(node, ast.Attribute):
            if isinstance(node.value, ast.Name) and node.value.id in ("self", "cls") and cls:
                return f"{mod.name}.{cls}.{node.attr}"
            base = self._resolve(mod, node.value, cls)
            return self._follow(f"{base}.{node.attr}") if base else None
        return None

    def _follow(self, dotted: str) -> str:
        """Follow a re-export (``acb_memory.get_session_memory``) to its def."""
        for _ in range(5):
            head, _, last = dotted.rpartition(".")
            target = self._module(head)
            if target is None or last in target.defs or last not in target.aliases:
                return dotted
            dotted = target.aliases[last]
        return dotted

    def _def_of(self, dotted: str | None) -> tuple[_Module, str, ast.AST] | None:
        if not dotted:
            return None
        for split in range(dotted.count("."), 0, -1):
            head = ".".join(dotted.split(".")[:split])
            tail = ".".join(dotted.split(".")[split:])
            target = self._module(head)
            if target is not None and tail in target.defs:
                return target, tail, target.defs[tail]
        return None

    @staticmethod
    def _is_redis_name(dotted: str | None) -> bool:
        return bool(dotted) and (dotted == "redis" or dotted.startswith("redis."))

    def _is_raw_type(self, mod: _Module, ann: ast.AST | None, cls: str | None) -> bool:
        if ann is None:
            return False
        for node in ast.walk(ann):
            if isinstance(node, (ast.Name, ast.Attribute)):
                dotted = self._resolve(mod, node, cls)
                if self._is_redis_name(dotted) and dotted.rsplit(".", 1)[-1] in RAW_TYPES:
                    return True
        return False

    def _is_ctor(self, mod: _Module, call: ast.Call, cls: str | None) -> bool:
        dotted = self._resolve(mod, call.func, cls)
        return self._is_redis_name(dotted) and dotted.rsplit(".", 1)[-1] in RAW_CTORS

    # -- the raw test of one expression ------------------------------------

    def _raw(self, mod: _Module, node: ast.AST | None, local: set[str], cls: str | None) -> bool:
        if node is None:
            return False
        if isinstance(node, ast.Await):
            return self._raw(mod, node.value, local, cls)
        if isinstance(node, ast.IfExp):
            return self._raw(mod, node.body, local, cls) or self._raw(mod, node.orelse, local, cls)
        if isinstance(node, ast.BoolOp):
            return any(self._raw(mod, v, local, cls) for v in node.values)
        if isinstance(node, ast.NamedExpr):
            return self._raw(mod, node.value, local, cls)
        if isinstance(node, ast.Name):
            if node.id in local:
                return True
            if node.id in mod.raw_globals:
                return True
            dotted = self._resolve(mod, node, cls)
            return self._is_global_raw(dotted)
        if isinstance(node, ast.Attribute):
            if isinstance(node.value, ast.Name) and node.value.id in ("self", "cls"):
                return node.attr in mod.raw_attrs
            return self._is_global_raw(self._resolve(mod, node, cls))
        if isinstance(node, ast.Call):
            if self._is_ctor(mod, node, cls):
                return True
            dotted = self._resolve(mod, node.func, cls)
            found = self._def_of(dotted)
            if found and f"{found[0].name}.{found[1]}" in self.factories:
                return True
            func = node.func
            if (
                isinstance(func, ast.Attribute)
                and func.attr in PROPAGATE
                and self._raw(mod, func.value, local, cls)
            ):
                return True
        return False

    def _is_global_raw(self, dotted: str | None) -> bool:
        if not dotted:
            return False
        head, _, last = dotted.rpartition(".")
        target = self._module(head)
        return target is not None and last in target.raw_globals

    # -- the passes --------------------------------------------------------

    def _run(self) -> None:
        changed = True
        rounds = 0
        while changed and rounds < 30:
            rounds += 1
            changed = False
            for mod in list(self.modules.values()):
                if mod.path == WRAPPER:
                    continue
                changed |= self._spread(mod)
            reach = self._reach()
            if self._pending and reach:
                changed |= self._admit(lambda rel, text: self._reaches(rel, text, reach))
        for mod in list(self.modules.values()):
            if mod.path == WRAPPER:
                continue
            self._emit(mod)
        self.sites.sort(key=lambda s: (s.path, s.line, s.method))

    def _seed_globals(self, mod: _Module) -> None:
        for node in mod.tree.body:
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                if self._is_raw_type(mod, node.annotation, None):
                    mod.raw_globals.add(node.target.id)

    def _functions(self, mod: _Module) -> Iterable[tuple[str, ast.AST, str | None]]:
        """Each function with its qualname and its class, plus module code."""
        yield "<module>", mod.tree, None
        for qual, node in mod.classes.items():
            yield qual, node, qual
        for qual, node in mod.defs.items():
            owner = qual.rsplit(".", 1)[0] if "." in qual else ""
            yield qual, node, owner if owner in mod.classes else None

    def _own_nodes(self, fn: ast.AST) -> list[ast.AST]:
        """The nodes of *fn*, without the bodies of nested defs and classes."""
        cached = self._nodes.get(id(fn))
        if cached is not None:
            return cached
        out: list[ast.AST] = []
        stack = list(ast.iter_child_nodes(fn))
        while stack:
            node = stack.pop()
            out.append(node)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                continue
            stack.extend(ast.iter_child_nodes(node))
        self._nodes[id(fn)] = out
        return out

    def _summary(self, fn: ast.AST) -> dict[str, list]:
        """The nodes of *fn* that the passes read, sorted by role. Cached."""
        cached = self._sums.get(id(fn))
        if cached is not None:
            return cached
        out: dict[str, list] = {"assign": [], "ann": [], "ret": [], "call": [], "glob": [], "url": []}
        for node in self._own_nodes(fn):
            pairs = _assignments(node)
            if pairs:
                out["assign"].extend(pairs)
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                out["ann"].append(node)
            elif isinstance(node, ast.Return):
                out["ret"].append(node)
            elif isinstance(node, ast.Call):
                out["call"].append(node)
            elif isinstance(node, ast.Global):
                out["glob"].extend(node.names)
            elif (
                isinstance(node, ast.Attribute)
                and node.attr == "redis_url"
                and isinstance(node.ctx, ast.Load)
            ):
                out["url"].append(node)
        out["call"].sort(key=lambda n: (n.lineno, n.col_offset))
        self._sums[id(fn)] = out
        return out

    def _locals(self, mod: _Module, qual: str, fn: ast.AST, cls: str | None) -> set[str]:
        local: set[str] = set(self.raw_params.get(f"{mod.name}.{qual}", set()))
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = [*fn.args.posonlyargs, *fn.args.args, *fn.args.kwonlyargs]
            for a in args:
                if self._is_raw_type(mod, a.annotation, cls):
                    local.add(a.arg)
        summary = self._summary(fn)
        for node in summary["ann"]:
            if self._is_raw_type(mod, node.annotation, cls):
                local.add(node.target.id)
        pairs = [(t, v) for t, v in summary["assign"] if isinstance(t, ast.Name)]
        for _ in range(6):
            before = len(local)
            for target, value in pairs:
                if target.id not in local and self._raw(mod, value, local, cls):
                    local.add(target.id)
            if len(local) == before:
                break
        return local

    def _spread(self, mod: _Module) -> bool:
        changed = False
        for qual, fn, cls in self._functions(mod):
            local = self._locals(mod, qual, fn, cls)
            fq = f"{mod.name}.{qual}"
            summary = self._summary(fn)
            globals_here = set(summary["glob"])
            for target, value in summary["assign"]:
                if isinstance(target, ast.Name):
                    if qual != "<module>" and target.id not in globals_here:
                        continue
                    if target.id in mod.raw_globals:
                        continue
                elif isinstance(target, ast.Attribute):
                    if target.attr in mod.raw_attrs:
                        continue
                else:
                    continue
                if not self._raw(mod, value, local, cls):
                    continue
                if isinstance(target, ast.Name):
                    mod.raw_globals.add(target.id)
                else:
                    mod.raw_attrs.add(target.attr)
                changed = True
            if fq not in self.factories:
                for node in summary["ret"]:
                    if self._raw(mod, node.value, local, cls):
                        self.factories.add(fq)
                        changed = True
                        break
            for node in summary["call"]:
                if node.args or node.keywords:
                    changed |= self._pass_into(mod, node, local, cls)
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and fq not in self.factories:
                if self._is_raw_type(mod, fn.returns, cls):
                    self.factories.add(fq)
                    changed = True
        return changed

    def _callee(self, mod: _Module, call: ast.Call, cls: str | None, *, lazy: bool = False):
        self._lazy = lazy
        try:
            return self._def_of(self._resolve(mod, call.func, cls))
        finally:
            self._lazy = False

    def _takes_raw(self, mod: _Module, call: ast.Call, local: set[str], cls: str | None) -> bool:
        return any(self._raw(mod, a, local, cls) for a in call.args) or any(
            self._raw(mod, k.value, local, cls) for k in call.keywords
        )

    def _pass_into(self, mod: _Module, call: ast.Call, local: set[str], cls: str | None) -> bool:
        """Mark the parameter of an in-tree callee that receives a raw client."""
        if not self._takes_raw(mod, call, local, cls):
            return False
        found = self._callee(mod, call, cls, lazy=True)
        if not found:
            return False
        target, tail, fn = found
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return False
        params = [a.arg for a in [*fn.args.posonlyargs, *fn.args.args]]
        if params and params[0] in ("self", "cls"):
            params = params[1:]
        names: set[str] = set()
        for i, arg in enumerate(call.args):
            if i < len(params) and self._raw(mod, arg, local, cls):
                names.add(params[i])
        for kw in call.keywords:
            if kw.arg and self._raw(mod, kw.value, local, cls):
                names.add(kw.arg)
        fq = f"{target.name}.{tail}"
        have = self.raw_params.get(fq, set())
        if names - have:
            self.raw_params[fq] = have | names
            return True
        return False

    def _emit(self, mod: _Module) -> None:
        lines = mod.source.splitlines()

        def text(node: ast.AST) -> str:
            row = lines[node.lineno - 1].strip() if node.lineno - 1 < len(lines) else ""
            return row[:120]

        for qual, fn, cls in self._functions(mod):
            local = self._locals(mod, qual, fn, cls)
            ctor_args: set[int] = set()
            summary = self._summary(fn)
            for node in summary["call"]:
                if self._is_ctor(mod, node, cls):
                    method = self._resolve(mod, node.func, cls) or "?"
                    self.sites.append(Site(mod.path, node.lineno, qual, "connect",
                                           method.rsplit(".", 1)[-1], text(node)))
                    for sub in ast.walk(node):
                        ctor_args.add(id(sub))
                    continue
                func = node.func
                if isinstance(func, ast.Attribute) and self._raw(mod, func.value, local, cls):
                    if func.attr in PROPAGATE or func.attr in NON_KEY:
                        continue
                    self.sites.append(Site(mod.path, node.lineno, qual, "key", func.attr, text(node)))
                    continue
                if not self._takes_raw(mod, node, local, cls):
                    continue
                callee = self._resolve(mod, func, cls)
                if callee and callee.rsplit(".", 1)[-1] in WRAPPER_CLASSES:
                    continue
                if self._callee(mod, node, cls, lazy=True):
                    continue
                if True:
                    name = callee.rsplit(".", 1)[-1] if callee else ast.unparse(func)[:40]
                    self.sites.append(Site(mod.path, node.lineno, qual, "handoff", name, text(node)))
            for node in summary["url"]:
                if id(node) not in ctor_args:
                    self.sites.append(Site(mod.path, node.lineno, qual, "connect", "redis_url", text(node)))


def _assignments(node: ast.AST) -> list[tuple[ast.AST, ast.AST | None]]:
    """Each (target, value) pair that *node* binds."""
    if isinstance(node, ast.Assign):
        return [(t, node.value) for t in node.targets]
    if isinstance(node, ast.AnnAssign):
        return [(node.target, node.value)]
    if isinstance(node, ast.NamedExpr):
        return [(node.target, node.value)]
    if isinstance(node, (ast.With, ast.AsyncWith)):
        return [(i.optional_vars, i.context_expr) for i in node.items if i.optional_vars is not None]
    return []


def tree_files(repo: Path = REPO) -> dict[str, str]:
    """Every Python file of the scan roots, keyed by its path from the repo."""
    out: dict[str, str] = {}
    for root in SCAN_ROOTS:
        for path in sorted((repo / root).rglob("*.py")):
            if "__pycache__" in path.parts or ".venv" in path.parts or "node_modules" in path.parts:
                continue
            rel = str(path.relative_to(repo)).replace("\\", "/")
            out[rel] = path.read_text(encoding="utf-8-sig")
    return out


def scan(files: dict[str, str] | None = None) -> list[Site]:
    """The raw Redis sites of *files*, or of the whole tree."""
    return Scanner(files if files is not None else tree_files()).sites
