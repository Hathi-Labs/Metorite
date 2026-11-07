"""WS-29 H-334d: each reader of the self and pair SQL reads on a tenant session.

The self set, the rows of ``resolve_self`` and the pair set all read
``identity._member_mailboxes_from``. That clause names
``a.organization_id = app.tenant_id`` (H-334c, H-334d). The predicate only
holds when the session binds a tenant. A session from ``get_db()`` binds none.
For the RLS-bypassing roles it compares to NULL and reads an empty set. For
``acb_app``, RLS hides each row. So a reader on an unbound session gets a
silent empty identity, and a reader that someone moves onto a privileged
connection loses the defence. This fence keeps every reader on
``tenant_session``.

**The reader set.** The seeds are the SQL constants and helpers of
``automation/identity.py`` and ``transport/accounts._display_labels``
(:data:`SEEDS`). A top-level function or method that names a seed, or names
another member of the set, joins the set. The fence resolves each name
through the imports of its module, so a common name in an unrelated module
never joins. A module-level constant built from a member joins too (for
example ``digest._NOT_FROM_SELF_EM``). A constant counts at any reference. A
function counts only where it is CALLED, so a hook that registers a job as a
callback does not join.

**Builder or reader.** A function of the set that only BUILDS the SQL text is
a **builder**: it calls no execute method (:data:`EXEC_ATTRS`), it opens no
session, and each function of the set that it calls is a builder too.
``paired_mailbox_ids_sql`` and ``def _sql(): return f"... {SELF_ADDRESSES_SQL}"``
are builders. A builder passes the set to its callers, and the fence does not
judge it. Every other function of the set is a **reader**, and the fence
judges it.

**The rule.** Each reader must do one of these:

1. open the seam (a call of ``_tenant_session`` or ``tenant_session``). If it
   also opens an unbound session (:data:`UNBOUND_OPENERS`), the fence follows
   that session inside the function. It fails when that session, or a name
   bound from it, runs the SQL text of the set, or goes into a call of a
   member of the set.
2. open no unbound session and receive its session as a parameter. A
   parameter counts as a session when its name looks like one
   (:func:`_is_session_param`) or its annotation names ``AsyncSession``,
   ``Session``, ``AsyncConnection`` or ``Connection``. Such a reader passes the
   rule up: each function that calls it joins the set, until one opens the
   seam.

A reader that opens an unbound session and no seam fails. A reader with no
seam and no session parameter fails. A reader that opens the seam, or that
the fence names for an unbound opener, passes nothing up, so one defect gives
one finding. :data:`ALLOWED` is empty: on 2026-11-07 the tree scan judged the
readers of the tree with zero hits.

⚠️ **Limits (stated, R7).** The fence reads names inside one function at a
time. It does not catch these shapes. Each one has a known-gap control below,
which asserts that the fence stays silent, so a fix shows up as a change.

1. A method call such as ``obj.run(db)``. The fence follows a function only by
   its own name, an imported name or ``module.name``.
2. A reader passed as a value, such as ``functools.partial(resolve_self, db)``.
   Only a call counts.
3. A local helper that returns an unbound session, such as
   ``async with _open() as db`` where ``_open`` returns ``get_db()``. The fence
   knows only the openers of :data:`UNBOUND_OPENERS`. A reader with no session
   parameter still fails by rule 2. A reader with one passes.
4. A reader with a session parameter that reads on another session, such as a
   module-level one. The fence trusts the parameter.
5. A reader that opens the seam and moves an unbound session through an
   attribute, a container or a second function. The flow in rule 1 follows
   plain names and the local text variables only.
6. A builder that passes the SQL text, with a module-level session, to a helper
   outside the set. It runs no execute method itself, so the fence takes it for
   a builder. Its callers still join the set.
7. A reader with no session parameter fails, and the functions that call it
   join the set too. So such a defect can give more than one finding.

The hermetic tenancy fences (``test_email_automation_tenancy.py``,
``test_email_request_jobs_tenancy.py``) cover the jobs.
``tests/unit/test_email_mailbox_list_tenant_r8.py`` proves the predicate on a
real Postgres.

Run::

    uv run pytest tests/unit/test_email_self_reader_tenant_fence.py -q
"""
from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
#: Where a reader can live: the application code, never the tests.
_SCAN_ROOTS = (_ROOT / "apps", _ROOT / "packages")
_SKIP_PARTS = frozenset({
    "tests", "test", "node_modules", ".venv", "__pycache__", "migrations",
})

_IDENTITY = "gateway.routes.email.automation.identity"
_ACCOUNTS = "gateway.routes.email.transport.accounts"

#: The seeds: (module, name). Each one reads, or builds the SQL that reads,
#: the mailboxes of the member through ``_member_mailboxes_from``.
#: ``_display_labels`` reads the mailboxes of the member with its own SQL,
#: which names the bound tenant since H-334c.
SEEDS: frozenset[tuple[str, str]] = frozenset({
    (_IDENTITY, "_member_mailboxes_from"),
    (_IDENTITY, "_MEMBER_MAILBOXES_FROM"),
    (_IDENTITY, "SELF_ADDRESSES_SQL"),
    (_IDENTITY, "SELF_MAILBOX_IDS_SQL"),
    (_IDENTITY, "_MEMBER_MAILBOXES_SQL"),
    (_IDENTITY, "paired_mailbox_ids_sql"),
    (_IDENTITY, "PAIRED_MAILBOX_IDS_SQL"),
    (_IDENTITY, "ALSO_IN_SQL"),
    (_ACCOUNTS, "_display_labels"),
})

#: The seam that binds the tenant: ``acb_common.db.tenant_session`` and its
#: per-package alias ``_tenant_session``.
TENANT_SEAMS = frozenset({"tenant_session", "_tenant_session"})

#: The openers that bind no tenant. ``open_session`` counts only when it is
#: NOT a parameter. As a parameter it is the tenant session of the caller
#: (``teardown.py``, ``memory_purge.py``). A ``.connect()`` or ``.begin()``
#: on a name that ends in ``engine`` counts too (:data:`ENGINE_OPENERS`).
UNBOUND_OPENERS = frozenset({
    "get_db", "_get_db", "get_session_factory", "get_engine",
    "create_async_engine", "create_engine", "async_sessionmaker",
    "sessionmaker", "AsyncSession", "SessionLocal", "open_session",
})

#: The methods that open a connection on an engine.
ENGINE_OPENERS = frozenset({"connect", "begin"})

#: The methods that run SQL. A function of the set that calls none of them,
#: opens no session and calls only builders is a builder.
EXEC_ATTRS = frozenset({
    "execute", "executemany", "exec_driver_sql", "scalar", "scalars",
    "stream", "stream_scalars", "fetch", "fetchrow", "fetchval",
})

#: A parameter name that holds a session.
_SESSION_NAMES = frozenset({"db", "session", "conn", "connection", "s", "sess"})
_SESSION_SUFFIXES = ("_db", "_session", "_conn")
#: An annotation that names a session type.
_SESSION_TYPES = frozenset({
    "AsyncSession", "Session", "AsyncConnection", "Connection",
})

#: A reader that the rule may not judge, and why. Empty on purpose: each
#: reader in the tree today opens the seam or receives its session. A new
#: entry needs a reason that a reviewer can check against the code.
ALLOWED: dict[str, str] = {}


@dataclass
class _Func:
    """One top-level function or method, with its nested defs folded in."""

    module: str
    qualname: str
    lineno: int
    params: frozenset[str]
    #: The parameters that hold a session (:func:`_is_session_param`).
    session_params: frozenset[str] = frozenset()
    #: The names it refers to, resolved to (module, name).
    refs: set[tuple[str, str]] = field(default_factory=set)
    #: The functions it calls, resolved the same way. A function that is
    #: only passed as a value (a callback that a hook registers) is no call.
    calls: set[tuple[str, str]] = field(default_factory=set)
    opens_seam: bool = False
    unbound: list[str] = field(default_factory=list)
    #: It calls a method of :data:`EXEC_ATTRS`.
    executes: bool = False
    #: The names that an execute call on an unbound session reads, and the
    #: functions that receive an unbound session as an argument.
    unbound_uses: set[tuple[str, str]] = field(default_factory=set)
    #: Each local name, with the names its assigned value reads.
    local_refs: dict[str, set[tuple[str, str]]] = field(default_factory=dict)


@dataclass
class _Module:
    name: str
    path: str
    #: Each top-level def or class attribute name, for local resolution.
    local_defs: set[str] = field(default_factory=set)
    #: A local name that an import binds, to (module, name). A module
    #: alias maps to (module, "").
    imports: dict[str, tuple[str, str]] = field(default_factory=dict)
    funcs: list[_Func] = field(default_factory=list)
    #: Each module-level assignment target, with the names its value reads.
    consts: dict[str, set[tuple[str, str]]] = field(default_factory=dict)


def _module_name(path: Path) -> str:
    """The dotted name of ``path``. The root is the highest folder that holds
    an ``__init__.py``, because ``gateway/routes`` is a namespace package
    with no ``__init__.py`` of its own."""
    top = None
    for d in path.parents:
        if d == _ROOT or _ROOT not in d.parents:
            break
        if (d / "__init__.py").exists():
            top = d
    base = (top or path.parent).parent
    rel = path.relative_to(base).with_suffix("")
    parts = list(rel.parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _resolve_relative(module: str, is_pkg: bool, level: int, target: str | None) -> str:
    base = module.split(".")
    if not is_pkg:
        base = base[:-1]
    if level > 1:
        base = base[: len(base) - (level - 1)]
    return ".".join([*base, target] if target else base)


def _args(node: ast.AST) -> list[ast.arg]:
    a = node.args  # type: ignore[attr-defined]
    out = [*a.posonlyargs, *a.args, *a.kwonlyargs]
    if a.vararg:
        out.append(a.vararg)
    if a.kwarg:
        out.append(a.kwarg)
    return [x for x in out if x.arg not in {"self", "cls"}]


def _params(node: ast.AST) -> frozenset[str]:
    return frozenset(x.arg for x in _args(node))


def _is_session_param(arg: ast.arg) -> bool:
    """A parameter holds a session when its name looks like one, or when its
    annotation names a session type."""
    name = arg.arg
    if name in _SESSION_NAMES or name.endswith(_SESSION_SUFFIXES):
        return True
    if arg.annotation is None:
        return False
    for n in ast.walk(arg.annotation):
        if isinstance(n, ast.Name) and n.id in _SESSION_TYPES:
            return True
        if isinstance(n, ast.Attribute) and n.attr in _SESSION_TYPES:
            return True
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and any(
                t in n.value for t in _SESSION_TYPES):
            return True
    return False


def _call_name(call: ast.Call) -> str:
    f = call.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return ""


def _tail_name(n: ast.AST) -> str:
    if isinstance(n, ast.Name):
        return n.id
    if isinstance(n, ast.Attribute):
        return n.attr
    return ""


def _is_engine_open(call: ast.Call) -> bool:
    """``admin_engine.connect()``, ``self._engine.begin()``."""
    f = call.func
    return (isinstance(f, ast.Attribute) and f.attr in ENGINE_OPENERS
            and _tail_name(f.value).lower().endswith("engine"))


def _imports(tree: ast.AST, name: str, is_pkg: bool) -> dict[str, tuple[str, str]]:
    """Each local name that an import binds, to (module, name). A module
    alias maps to (module, "")."""
    out: dict[str, tuple[str, str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            src = (_resolve_relative(name, is_pkg, node.level, node.module)
                   if node.level else (node.module or ""))
            for alias in node.names:
                # ``from pkg import mod`` binds a module or a name. The pair
                # (src, name) serves both readings: see ``_Resolver``.
                out[alias.asname or alias.name] = (src, alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    out[alias.asname] = (alias.name, "")
    return out


@dataclass
class _Resolver:
    """Resolve a name of one module to (module, name)."""

    module: str
    imports: dict[str, tuple[str, str]]

    def __call__(self, n: ast.AST) -> tuple[str, str] | None:
        if isinstance(n, ast.Name):
            return self.imports.get(n.id, (self.module, n.id))
        if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name):
            head = self.imports.get(n.value.id)
            if head is None:
                return None
            src, member = head
            # ``identity.X`` where ``identity`` came from ``from pkg import
            # identity`` or ``import pkg.identity as identity``.
            return (f"{src}.{member}" if member else src, n.attr)
        return None

    def refs(self, body: ast.AST) -> set[tuple[str, str]]:
        return {r for n in ast.walk(body) if isinstance(n, ast.Name | ast.Attribute)
                and (r := self(n)) is not None}

    def calls(self, body: ast.AST) -> set[tuple[str, str]]:
        return {r for n in ast.walk(body) if isinstance(n, ast.Call)
                and (r := self(n.func)) is not None}


def _openers(node: ast.AST, inner: set[str]) -> tuple[bool, list[str]]:
    """Does ``node`` open the seam, and which unbound openers does it call?"""
    seam, unbound = False, []
    for n in ast.walk(node):
        if not isinstance(n, ast.Call):
            continue
        cn = _call_name(n)
        if cn in TENANT_SEAMS:
            seam = True
        elif cn in UNBOUND_OPENERS and not (cn == "open_session" and cn in inner):
            unbound.append(f"{cn}() at line {n.lineno}")
        elif _is_engine_open(n):
            unbound.append(f"{_tail_name(n.func.value)}.{cn}() at line {n.lineno}")  # type: ignore[attr-defined]
    return seam, unbound


def _bindings(node: ast.AST) -> list[tuple[str, ast.AST]]:
    """Each plain name that the body binds, with the value it binds."""
    out: list[tuple[str, ast.AST]] = []
    for n in ast.walk(node):
        if isinstance(n, ast.Assign):
            out += [(t.id, n.value) for t in n.targets if isinstance(t, ast.Name)]
        elif isinstance(n, ast.AnnAssign | ast.NamedExpr) and n.value is not None:
            if isinstance(n.target, ast.Name):
                out.append((n.target.id, n.value))
        elif isinstance(n, ast.With | ast.AsyncWith):
            out += [(i.optional_vars.id, i.context_expr) for i in n.items
                    if isinstance(i.optional_vars, ast.Name)]
    return out


def _is_unbound(e: ast.AST, names: set[str], inner: set[str]) -> bool:
    """Is ``e`` an unbound session: an unbound opener, a name bound from one,
    or a call on one?"""
    while isinstance(e, ast.Await):
        e = e.value
    if isinstance(e, ast.Name):
        return e.id in names
    if not isinstance(e, ast.Call):
        return False
    f, cn = e.func, _call_name(e)
    if cn in UNBOUND_OPENERS and not (cn == "open_session" and cn in inner):
        return True
    if _is_engine_open(e):
        return True
    if isinstance(f, ast.Call):  # ``get_session_factory()()``
        return _is_unbound(f, names, inner)
    if isinstance(f, ast.Name):  # ``factory()``
        return f.id in names
    # ``engine.connect()``, ``factory.begin()``
    return isinstance(f, ast.Attribute) and _is_unbound(f.value, names, inner)


def _unbound_flow(node: ast.AST, inner: set[str], resolve: _Resolver):
    """Follow each unbound session inside one function.

    Returns the names that an execute call on an unbound session reads, the
    functions that get an unbound session as an argument, and the names that
    each local assignment reads."""
    binds = _bindings(node)
    names: set[str] = set()

    def unbound(e: ast.AST) -> bool:
        return _is_unbound(e, names, inner)

    changed = True
    while changed:
        changed = False
        for name, value in binds:
            if name not in names and unbound(value):
                names.add(name)
                changed = True
    uses: set[tuple[str, str]] = set()
    for n in ast.walk(node):
        if not isinstance(n, ast.Call):
            continue
        f = n.func
        argv = [*n.args, *(k.value for k in n.keywords)]
        if (isinstance(f, ast.Attribute) and f.attr in EXEC_ATTRS
                and unbound(f.value)):
            for a in argv:
                uses |= resolve.refs(a) | resolve.calls(a)
        if any(unbound(a) for a in argv) and (r := resolve(f)) is not None:
            uses.add(r)
    local_refs: dict[str, set[tuple[str, str]]] = {}
    for name, value in binds:
        local_refs.setdefault(name, set()).update(resolve.refs(value))
    return uses, local_refs


def _func(node: ast.AST, qual: str, module: str, resolve: _Resolver) -> _Func:
    params = _params(node)
    inner: set[str] = set(params)
    for n in ast.walk(node):
        if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
            inner |= _params(n)
    seam, unbound = _openers(node, inner)
    executes = any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                   and n.func.attr in EXEC_ATTRS for n in ast.walk(node))
    uses, local_refs = (_unbound_flow(node, inner, resolve) if seam and unbound
                        else (set(), {}))
    return _Func(module=module, qualname=qual, lineno=node.lineno,  # type: ignore[attr-defined]
                 params=params,
                 session_params=frozenset(
                     a.arg for a in _args(node) if _is_session_param(a)),
                 refs=resolve.refs(node), calls=resolve.calls(node),
                 opens_seam=seam, unbound=unbound, executes=executes,
                 unbound_uses=uses, local_refs=local_refs)


def parse_module(source: str, name: str, path: str = "<src>", is_pkg: bool = False) -> _Module:
    """One module: its imports, its top-level functions and its constants."""
    tree = ast.parse(source)
    mod = _Module(name=name, path=path, imports=_imports(tree, name, is_pkg))
    resolve = _Resolver(name, mod.imports)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            mod.local_defs.add(node.name)
            mod.funcs.append(_func(node, node.name, name, resolve))
        elif isinstance(node, ast.ClassDef):
            mod.local_defs.add(node.name)
            mod.funcs.extend(
                _func(sub, f"{node.name}.{sub.name}", name, resolve)
                for sub in node.body
                if isinstance(sub, ast.FunctionDef | ast.AsyncFunctionDef))
        elif isinstance(node, ast.Assign | ast.AnnAssign | ast.AugAssign) and node.value:
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                if isinstance(t, ast.Name):
                    mod.local_defs.add(t.id)
                    mod.consts.setdefault(t.id, set()).update(resolve.refs(node.value))
    return mod


@dataclass
class _Set:
    """The reader set of a group of modules, and the tests against it."""

    members: set[tuple[str, str]]
    funcs: set[tuple[str, str]]
    #: The functions of the set that run SQL or open a session.
    executors: set[tuple[str, str]]

    def hits(self, f: _Func) -> bool:
        key = (f.module, f.qualname)
        return (any(r in self.members and r not in self.funcs for r in f.refs - {key})
                or any(c in self.members for c in f.calls - {key}))

    def reaches(self, f: _Func) -> bool:
        """Does an unbound session of ``f`` run the SQL text of the set, or go
        into a call of a member of the set?"""
        seen: set[tuple[str, str]] = set()
        todo = list(f.unbound_uses)
        while todo:
            r = todo.pop()
            if r in seen:
                continue
            seen.add(r)
            if r in self.members:
                return True
            if r[0] == f.module and r[1] in f.local_refs:
                todo.extend(f.local_refs[r[1]])
        return False


def _reader_set(modules: list[_Module], seeds: frozenset[tuple[str, str]]) -> _Set:
    """The reader set.

    A constant member counts at any reference. A function member counts only
    where it is called. A member that opens the seam, or that opens an
    unbound session, passes nothing to its callers: the first is safe, and
    the fence names the second at its own line."""
    s = _Set(members=set(seeds),
             funcs={(m.name, f.qualname) for m in modules for f in m.funcs},
             executors=set())
    changed = True
    while changed:
        changed = False
        for m in modules:
            for const, refs in m.consts.items():
                key = (m.name, const)
                # A module-level text names a member constant, or calls a
                # function member, as ``ALSO_IN_SQL`` calls
                # ``paired_mailbox_ids_sql``.
                if key not in s.members and refs & s.members:
                    s.members.add(key)
                    changed = True
            for f in m.funcs:
                key = (m.name, f.qualname)
                if (key in s.members or f.opens_seam or f.unbound
                        or not s.hits(f)):
                    continue
                s.members.add(key)
                changed = True
    # A function of the set runs SQL when it calls an execute method, opens
    # a session, or calls a function of the set that runs SQL.
    by_key = {(m.name, f.qualname): f for m in modules for f in m.funcs}
    changed = True
    while changed:
        changed = False
        for key, f in by_key.items():
            if key in s.executors:
                continue
            if (f.executes or f.opens_seam or f.unbound
                    or any(c in s.executors for c in f.calls - {key})):
                s.executors.add(key)
                changed = True
    return s


def _judged_funcs(modules: list[_Module], seeds: frozenset[tuple[str, str]]):
    s = _reader_set(modules, seeds)
    for m in modules:
        for f in m.funcs:
            key = (m.name, f.qualname)
            if key not in seeds and s.hits(f) and key in s.executors:
                yield s, m, f


def judged(modules: list[_Module], seeds: frozenset[tuple[str, str]] = SEEDS) -> set[str]:
    """Each reader that the rule judges, as ``module:qualname``. A builder is
    not judged."""
    return {f"{m.name}:{f.qualname}" for _, m, f in _judged_funcs(modules, seeds)}


def scan(modules: list[_Module], seeds: frozenset[tuple[str, str]] = SEEDS) -> list[str]:
    """Each reader that breaks the rule, as ``module:qualname: why``."""
    out: list[str] = []
    for s, m, f in _judged_funcs(modules, seeds):
        where = f"{m.name}:{f.qualname}"
        if where in ALLOWED:
            continue
        if f.opens_seam:
            if f.unbound and s.reaches(f):
                out.append(f"{where}: opens tenant_session, but reads the self "
                           "or pair SQL on an unbound session: "
                           f"{', '.join(f.unbound)}")
        elif f.unbound:
            out.append(f"{where}: reads the self or pair SQL and opens "
                       f"an unbound session: {', '.join(f.unbound)}")
        elif not f.session_params:
            out.append(f"{where}: reads the self or pair SQL with no "
                       "tenant_session and no session parameter")
    return sorted(out)


def _tree_modules() -> list[_Module]:
    mods: list[_Module] = []
    for root in _SCAN_ROOTS:
        for path in root.rglob("*.py"):
            rel = path.relative_to(_ROOT)
            if _SKIP_PARTS & set(rel.parts) or path.name.startswith("test_"):
                continue
            src = path.read_text(encoding="utf-8")
            if "gateway" not in src and "identity" not in src:
                continue
            try:
                mods.append(parse_module(src, _module_name(path), str(rel),
                                         path.name == "__init__.py"))
            except SyntaxError:
                continue
    return mods


# ── The tree ────────────────────────────────────────────────────────────────

#: The floor of the readers that the tree scan judges. The scan judged 74 on
#: 2026-11-07. The floor sits well below, so a lane that renames or removes a
#: reader stays green. A scan that loses most of the readers fails.
_MIN_JUDGED = 40


def test_the_tree_holds_no_reader_off_the_seam():
    assert scan(_tree_modules()) == []


def test_the_scan_finds_the_readers_it_guards():
    """Non-vacuity, by shape and not by name: each seed resolves to a real
    definition, each seed has a member of the set that uses it, and the scan
    judges at least :data:`_MIN_JUDGED` readers."""
    mods = _tree_modules()
    found = {(m.name, n) for m in mods for n in m.local_defs}
    assert set(SEEDS) <= found, sorted(set(SEEDS) - found)
    s = _reader_set(mods, SEEDS)
    used: set[tuple[str, str]] = set()
    for m in mods:
        for const, refs in m.consts.items():
            if (m.name, const) in s.members:
                used |= refs
        for f in m.funcs:
            if (m.name, f.qualname) in s.members or s.hits(f):
                used |= f.refs | f.calls
    assert set(SEEDS) <= used, sorted(set(SEEDS) - used)
    got = judged(mods)
    assert len(got) >= _MIN_JUDGED, len(got)


def test_each_allowed_entry_is_live_and_has_a_reason():
    live = judged(_tree_modules()) if ALLOWED else set()
    for where, why in ALLOWED.items():
        assert why.strip(), where
        assert where in live, f"stale ALLOWED entry: {where}"


# ── Positive controls: a synthetic module that breaks the rule ─────────────

_HEAD = (
    "import functools\n"
    "from gateway.routes.email.automation.identity import (\n"
    "    SELF_ADDRESSES_SQL, PAIRED_MAILBOX_IDS_SQL, resolve_self)\n"
    "from gateway.routes.email.automation import identity\n"
    "from gateway.db import get_db, tenant_session, admin_engine, SessionLocal\n"
    "from gateway.routes.email.core import _tenant_session\n"
    "from sqlalchemy import text\n"
    "from sqlalchemy.ext.asyncio import AsyncSession\n"
    "GLOBAL_DB = None\n"
)


def _real(module: str) -> _Module:
    """One real seed module, so a synthetic source meets the true readers."""
    rel = Path(*module.split(".")).with_suffix(".py")
    path = _ROOT / "apps/services/gateway" / rel
    return parse_module(path.read_text(encoding="utf-8"), module, str(path))


def _scan_src(body: str) -> list[str]:
    return scan([_real(_IDENTITY), _real(_ACCOUNTS),
                 parse_module(_HEAD + body, "gateway.sample")])


def test_a_reader_on_get_db_is_caught():
    hits = _scan_src(
        "async def leak():\n"
        "    db = await get_db()\n"
        "    return await db.execute(text(SELF_ADDRESSES_SQL), {'aid': 'x'})\n")
    assert hits and "gateway.sample:leak" in hits[0] and "get_db()" in hits[0]


def test_a_helper_reader_on_get_db_is_caught():
    hits = _scan_src(
        "async def leak(aid):\n"
        "    db = await get_db()\n"
        "    return await resolve_self(db, aid)\n")
    assert hits and "get_db()" in hits[0]


def test_a_module_alias_reader_is_caught():
    hits = _scan_src(
        "async def leak():\n"
        "    db = await get_db()\n"
        "    return await db.execute(text(identity.PAIRED_MAILBOX_IDS_SQL))\n")
    assert hits and "gateway.sample:leak" in hits[0]


def test_an_unbound_open_session_is_caught():
    hits = _scan_src(
        "from somewhere import open_session\n"
        "async def leak(aid):\n"
        "    async with open_session() as db:\n"
        "        return await resolve_self(db, aid)\n")
    assert hits and "open_session()" in hits[0]


def test_an_open_session_parameter_is_the_seam_of_the_caller():
    assert _scan_src(
        "async def ok(aid, open_session):\n"
        "    async with open_session() as db:\n"
        "        return await resolve_self(db, aid)\n") == []


def test_the_rule_climbs_from_a_receiver_to_its_caller():
    """A helper that receives the session passes the rule to its caller."""
    hits = _scan_src(
        "async def helper(db, aid):\n"
        "    return await resolve_self(db, aid)\n"
        "async def caller(aid):\n"
        "    db = await get_db()\n"
        "    return await helper(db, aid)\n")
    assert len(hits) == 1 and "gateway.sample:caller" in hits[0]


def test_a_derived_constant_is_a_reader():
    hits = _scan_src(
        "_MINE = f'x NOT IN ({SELF_ADDRESSES_SQL})'\n"
        "async def leak():\n"
        "    db = await get_db()\n"
        "    return await db.execute(text(_MINE))\n")
    assert hits and "gateway.sample:leak" in hits[0]


def test_a_reader_with_no_session_at_all_is_caught():
    """It runs the SQL on a module-level session, and it takes no session."""
    hits = _scan_src(
        "async def orphan():\n"
        "    return await GLOBAL_DB.execute(text(PAIRED_MAILBOX_IDS_SQL))\n")
    assert hits and "gateway.sample:orphan" in hits[0] and "no tenant_session" in hits[0]


def test_the_seam_passes():
    assert _scan_src(
        "async def ok(aid):\n"
        "    async with _tenant_session() as db:\n"
        "        return await resolve_self(db, aid)\n"
        "async def ok2(org):\n"
        "    async with tenant_session(org) as db:\n"
        "        return await db.execute(text(SELF_ADDRESSES_SQL))\n"
        "async def caller(aid):\n"
        "    return await ok(aid)\n") == []


def test_a_same_name_in_an_unrelated_module_is_not_a_reader():
    """No false positive: a local ``resolve_self`` of another module is not
    the identity helper, so a get_db() beside it is out of scope."""
    src = (
        "from gateway.db import get_db\n"
        "async def resolve_self(db, x):\n"
        "    return x\n"
        "async def other():\n"
        "    db = await get_db()\n"
        "    return await resolve_self(db, 1)\n")
    assert scan([parse_module(src, "gateway.unrelated")]) == []


def test_a_registered_callback_is_no_call():
    """No false positive: a hook that registers a reader as a value takes
    no session, and the reader opens its own seam when it runs."""
    assert _scan_src(
        "async def job(aid):\n"
        "    async with _tenant_session() as db:\n"
        "        return await resolve_self(db, aid)\n"
        "def register(hooks):\n"
        "    hooks.append(job)\n"
        "def boot():\n"
        "    register([])\n") == []


def test_an_unbound_reader_is_named_once_at_its_own_line():
    """The reader that opens an unbound session is the one finding. Its
    callers are not named again."""
    hits = _scan_src(
        "async def leak(aid):\n"
        "    db = await get_db()\n"
        "    return await resolve_self(db, aid)\n"
        "async def route():\n"
        "    return await leak('x')\n")
    assert len(hits) == 1 and "gateway.sample:leak" in hits[0]


# ── Builders (R2a): a function that only builds the SQL text ───────────────


def test_a_text_builder_is_not_judged():
    """No false positive: a function with no parameter that only builds the
    SQL text runs no SQL, so it needs no session."""
    assert _scan_src(
        "def _sql():\n"
        "    return f'x IN ({SELF_ADDRESSES_SQL})'\n"
        "def _sql2(alias):\n"
        "    return _sql() + alias\n") == []


def test_the_caller_of_a_text_builder_is_judged():
    """A builder passes the set to its callers. A caller that runs the text
    on get_db() is caught."""
    hits = _scan_src(
        "def _sql():\n"
        "    return f'x IN ({SELF_ADDRESSES_SQL})'\n"
        "async def leak():\n"
        "    db = await get_db()\n"
        "    return await db.execute(text(_sql()))\n")
    assert len(hits) == 1 and "gateway.sample:leak" in hits[0]


# ── The seam and an unbound opener in one function (R2b) ───────────────────


def test_the_seam_beside_an_unrelated_unbound_opener_passes():
    """No false positive: the read runs on the seam. The get_engine() serves
    something else."""
    assert _scan_src(
        "from gateway.db import get_engine\n"
        "async def ok(aid):\n"
        "    eng = get_engine()\n"
        "    async with eng.connect() as c:\n"
        "        await c.execute(text('SELECT 1'))\n"
        "    async with _tenant_session() as db:\n"
        "        return await resolve_self(db, aid)\n") == []


def test_the_seam_with_the_read_on_an_unbound_session_is_caught():
    """The function opens the seam, but the unbound session runs the read."""
    hits = _scan_src(
        "async def leak(aid):\n"
        "    async with _tenant_session() as t:\n"
        "        await t.execute(text('SELECT 1'))\n"
        "    db = await get_db()\n"
        "    return await resolve_self(db, aid)\n")
    assert len(hits) == 1 and "opens tenant_session, but" in hits[0]


def test_the_seam_with_the_text_run_on_an_unbound_engine_is_caught():
    """The flow follows an engine to its connection, and a local text."""
    hits = _scan_src(
        "from gateway.db import get_engine\n"
        "async def leak():\n"
        "    async with _tenant_session() as t:\n"
        "        await t.execute(text('SELECT 1'))\n"
        "    sql = f'x IN ({SELF_ADDRESSES_SQL})'\n"
        "    eng = get_engine()\n"
        "    async with eng.connect() as c:\n"
        "        return await c.execute(text(sql))\n")
    assert len(hits) == 1 and "gateway.sample:leak" in hits[0]


# ── The openers and the session parameter (R4) ─────────────────────────────


def test_an_engine_connect_is_caught():
    hits = _scan_src(
        "def leak():\n"
        "    with admin_engine.connect() as c:\n"
        "        return c.execute(text(SELF_ADDRESSES_SQL))\n")
    assert hits and "admin_engine.connect()" in hits[0]


def test_an_engine_begin_is_caught():
    hits = _scan_src(
        "class Repo:\n"
        "    def leak(self, aid):\n"
        "        with self._engine.begin() as c:\n"
        "            return c.execute(text(SELF_ADDRESSES_SQL), {'aid': aid})\n")
    assert hits and "gateway.sample:Repo.leak" in hits[0] and "_engine.begin()" in hits[0]


def test_a_session_local_is_caught():
    hits = _scan_src(
        "async def leak(aid):\n"
        "    async with SessionLocal() as db:\n"
        "        return await resolve_self(db, aid)\n")
    assert hits and "SessionLocal()" in hits[0]


def test_a_parameter_that_is_no_session_does_not_pass_the_climb():
    """A route that takes ``user`` and no session has no session that a
    tenant binds."""
    hits = _scan_src(
        "async def route(user, aid):\n"
        "    return await resolve_self(GLOBAL_DB, aid)\n")
    assert hits and "gateway.sample:route" in hits[0] and "no session parameter" in hits[0]


def test_a_session_annotation_names_a_receiver():
    """No false positive: a parameter typed ``AsyncSession`` holds a session,
    whatever its name. Its caller opens the seam."""
    assert _scan_src(
        "async def helper(x: AsyncSession, aid):\n"
        "    return await resolve_self(x, aid)\n"
        "async def caller(aid):\n"
        "    async with _tenant_session() as db:\n"
        "        return await helper(db, aid)\n") == []


# ── Known gaps (R7): the fence stays silent on these shapes ────────────────
# Each control asserts the CURRENT behaviour, so a fix changes it on purpose.
# The numbers match the limits in the module docstring.


def test_known_gap_1_a_method_call_is_not_followed():
    assert _scan_src(
        "class Svc:\n"
        "    async def run(self, db, aid):\n"
        "        return await resolve_self(db, aid)\n"
        "async def route(aid, svc):\n"
        "    db = await get_db()\n"
        "    return await svc.run(db, aid)\n") == []


def test_known_gap_2_a_reader_passed_as_a_value_is_not_a_call():
    assert _scan_src(
        "async def route(aid):\n"
        "    db = await get_db()\n"
        "    read = functools.partial(resolve_self, db)\n"
        "    return await read(aid)\n") == []


def test_known_gap_3_a_local_opener_helper_is_not_an_opener():
    assert _scan_src(
        "def _open():\n"
        "    return get_db()\n"
        "async def reader(session, aid):\n"
        "    async with _open() as db:\n"
        "        return await resolve_self(db, aid)\n") == []


def test_known_gap_4_a_session_parameter_is_trusted():
    assert _scan_src(
        "async def reader(db, aid):\n"
        "    return await resolve_self(GLOBAL_DB, aid)\n") == []


def test_known_gap_5_the_flow_does_not_follow_a_container():
    assert _scan_src(
        "async def leak(aid):\n"
        "    async with _tenant_session() as t:\n"
        "        await t.execute(text('SELECT 1'))\n"
        "    holder = {'db': await get_db()}\n"
        "    return await resolve_self(holder['db'], aid)\n") == []


def test_known_gap_6_a_builder_that_hands_the_text_out_is_not_judged():
    assert _scan_src(
        "from somewhere import run_sql\n"
        "async def leak():\n"
        "    return await run_sql(GLOBAL_DB, SELF_ADDRESSES_SQL)\n") == []


def test_known_gap_7_a_reader_with_no_session_is_named_with_its_caller():
    hits = _scan_src(
        "async def helper(user, aid):\n"
        "    return await resolve_self(GLOBAL_DB, aid)\n"
        "async def route(aid):\n"
        "    return await helper('u', aid)\n")
    assert len(hits) == 2
