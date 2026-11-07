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

**The rule.** The seeds are the SQL constants and helpers of
``automation/identity.py`` and ``transport/accounts._display_labels``
(:data:`SEEDS`). A **reader** is a top-level function or method that names a
seed or another reader. The fence resolves each name through the imports of
its module, so a common name in an unrelated module is never a reader. A
module-level constant built from a reader is a reader too (for example
``digest._NOT_FROM_SELF_EM``). Each reader must:

1. call no unbound opener (:data:`UNBOUND_OPENERS`: ``get_db``, ``_get_db``,
   ``get_session_factory``, ``get_engine``, ``open_session`` that is not a
   parameter of the function, and an engine or sessionmaker made in place).
2. either open the seam (a call of ``_tenant_session`` or ``tenant_session``
   in its body), or receive its session as a parameter. A reader that
   receives the session passes the rule up. Each function that names it is a
   reader in turn, until one opens the seam.

A top-level reader that opens no seam and takes no parameter has no session
that a tenant binds, and it fails.

Three choices keep other lanes green. A function reader counts only where
it is CALLED, so a hook that registers a job as a callback is no reader. A
reader that opens the seam, or that the fence already names for an unbound
opener, passes nothing up, so one defect gives one finding. A name resolves
through the imports of its own module, so a same name elsewhere is out of
scope. :data:`ALLOWED` is empty: on 2026-11-07 the tree scan judged 74
functions with zero hits.

⚠️ **Limit (stated, R7).** The fence reads names. It follows a function only
by its own name, an imported name or ``module.name``. A method call such as
``obj.run(db)`` is not followed, so the rule stops at a method that receives
the session. A session that a caller hands into a method keeps its seam by
review, and the hermetic tenancy fences
(``test_email_automation_tenancy.py``, ``test_email_request_jobs_tenancy.py``)
cover the jobs. ``tests/unit/test_email_mailbox_list_tenant_r8.py`` proves
the predicate on a real Postgres.

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
#: (``teardown.py``, ``memory_purge.py``).
UNBOUND_OPENERS = frozenset({
    "get_db", "_get_db", "get_session_factory", "get_engine",
    "create_async_engine", "create_engine", "async_sessionmaker",
    "sessionmaker", "AsyncSession", "open_session",
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
    #: The names it refers to, resolved to (module, name).
    refs: set[tuple[str, str]] = field(default_factory=set)
    #: The functions it calls, resolved the same way. A function that is
    #: only passed as a value (a callback that a hook registers) is no call.
    calls: set[tuple[str, str]] = field(default_factory=set)
    opens_seam: bool = False
    unbound: list[str] = field(default_factory=list)


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


def _params(node: ast.AST) -> frozenset[str]:
    a = node.args  # type: ignore[attr-defined]
    names = [x.arg for x in (*a.posonlyargs, *a.args, *a.kwonlyargs)]
    if a.vararg:
        names.append(a.vararg.arg)
    if a.kwarg:
        names.append(a.kwarg.arg)
    return frozenset(n for n in names if n not in {"self", "cls"})


def _call_name(call: ast.Call) -> str:
    f = call.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return ""


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


def _openers(node: ast.AST, params: frozenset[str]) -> tuple[bool, list[str]]:
    """Does ``node`` open the seam, and which unbound openers does it call?"""
    inner: set[str] = set(params)
    for n in ast.walk(node):
        if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
            inner |= _params(n)
    seam, unbound = False, []
    for n in ast.walk(node):
        if not isinstance(n, ast.Call):
            continue
        cn = _call_name(n)
        if cn in TENANT_SEAMS:
            seam = True
        elif cn in UNBOUND_OPENERS and not (cn == "open_session" and cn in inner):
            unbound.append(f"{cn}() at line {n.lineno}")
    return seam, unbound


def _func(node: ast.AST, qual: str, module: str, resolve: _Resolver) -> _Func:
    params = _params(node)
    seam, unbound = _openers(node, params)
    return _Func(module=module, qualname=qual, lineno=node.lineno,  # type: ignore[attr-defined]
                 params=params, refs=resolve.refs(node), calls=resolve.calls(node),
                 opens_seam=seam, unbound=unbound)


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


def _readers(modules: list[_Module], seeds: frozenset[tuple[str, str]]):
    """The reader set, and the test of one function against it.

    A constant reader counts at any reference. A function reader counts only
    where it is called. A reader that opens the seam, or that opens an
    unbound session, passes nothing to its callers: the first is safe, and
    the fence names the second at its own line."""
    readers: set[tuple[str, str]] = set(seeds)
    funcs = {(m.name, f.qualname) for m in modules for f in m.funcs}

    def hits(f: _Func) -> bool:
        key = (f.module, f.qualname)
        return (any(r in readers and r not in funcs for r in f.refs - {key})
                or any(c in readers for c in f.calls - {key}))

    changed = True
    while changed:
        changed = False
        for m in modules:
            for const, refs in m.consts.items():
                key = (m.name, const)
                # A module-level text names a reader constant, or calls a
                # function reader, as ``ALSO_IN_SQL`` calls
                # ``paired_mailbox_ids_sql``.
                if key not in readers and refs & readers:
                    readers.add(key)
                    changed = True
            for f in m.funcs:
                key = (m.name, f.qualname)
                if (key in readers or f.opens_seam or f.unbound
                        or not hits(f)):
                    continue
                readers.add(key)
                changed = True
    return readers, hits


def judged(modules: list[_Module], seeds: frozenset[tuple[str, str]] = SEEDS) -> set[str]:
    """Each function that the rule judges, as ``module:qualname``."""
    _, hits = _readers(modules, seeds)
    return {f"{m.name}:{f.qualname}" for m in modules for f in m.funcs
            if (m.name, f.qualname) not in seeds and hits(f)}


def scan(modules: list[_Module], seeds: frozenset[tuple[str, str]] = SEEDS) -> list[str]:
    """Each reader that breaks the rule, as ``module:qualname: why``."""
    _, hits = _readers(modules, seeds)
    out: list[str] = []
    for m in modules:
        for f in m.funcs:
            key = (m.name, f.qualname)
            if key in seeds or not hits(f):
                continue
            where = f"{m.name}:{f.qualname}"
            if where in ALLOWED:
                continue
            if f.unbound:
                out.append(f"{where}: reads the self or pair SQL and opens "
                           f"an unbound session: {', '.join(f.unbound)}")
            elif not f.opens_seam and not f.params:
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


def test_the_tree_holds_no_reader_off_the_seam():
    assert scan(_tree_modules()) == []


def test_the_scan_finds_the_readers_it_guards():
    """Non-vacuity: the scan reaches the readers of each kind in the tree. A
    rename that hides them from the scan fails here, not in silence."""
    mods = _tree_modules()
    names = {(m.name, f.qualname) for m in mods for f in m.funcs}
    for want in (
        (_IDENTITY, "resolve_self"),
        (_IDENTITY, "also_in_by_message"),
        (_IDENTITY, "draft_skip_in_pair"),
        (_ACCOUNTS, "_display_labels"),
    ):
        assert want in names, want
    got = judged(mods)
    for want in (
        # The pair set: "also in" of the list and the search, and the draft
        # dedupe of the rule runner.
        "gateway.routes.email.transport.messages:_also_in_by_message",
        "gateway.routes.email.transport.messages:list_messages",
        "gateway.routes.email.transport.search:search_messages",
        "gateway.routes.email.automation.actions:_skip_for_paired_mailbox",
        "gateway.routes.email.automation.runner:_run_rules_job",
        # The self set: a derived constant of the digest, and the labels.
        "gateway.routes.email.digest:_maybe_send_digest",
        "gateway.routes.email.transport.accounts:create_account",
    ):
        assert want in got, want
    seeds = set(SEEDS)
    found = {(m.name, n) for m in mods for n in (m.local_defs)}
    assert seeds <= found, sorted(seeds - found)


def test_each_allowed_entry_is_live_and_has_a_reason():
    live = judged(_tree_modules()) if ALLOWED else set()
    for where, why in ALLOWED.items():
        assert why.strip(), where
        assert where in live, f"stale ALLOWED entry: {where}"


# ── Positive controls: a synthetic module that breaks the rule ─────────────

_HEAD = (
    "from gateway.routes.email.automation.identity import (\n"
    "    SELF_ADDRESSES_SQL, PAIRED_MAILBOX_IDS_SQL, resolve_self)\n"
    "from gateway.routes.email.automation import identity\n"
    "from gateway.db import get_db, tenant_session\n"
    "from gateway.routes.email.core import _tenant_session\n"
    "from sqlalchemy import text\n"
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
    hits = _scan_src(
        "async def orphan():\n"
        "    return PAIRED_MAILBOX_IDS_SQL\n")
    assert hits and "no tenant_session" in hits[0]


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
