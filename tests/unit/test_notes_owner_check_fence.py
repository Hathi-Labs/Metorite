"""Task 3966: a source fence over the owner check of the ``/notes`` routes (R7).

Owner decision (2026-11-26): "Owner only, the same rule as the meetings list
and detail." The rule is ``core.OWNED_MEETING_PREDICATE`` and
``core.load_owned_meeting``. This fence reads the source of
``gateway/routes/notes/``. It finds each route: a ``router.get``, ``post``,
``put``, ``patch``, ``delete`` or ``api_route`` decorator, and each
``router.add_api_route`` call.

**The id of a route** is a path parameter, a query parameter or a body field
named ``meeting_id``, ``action_id`` or ``recording_id``.

**The rule for a route with an id.** The first statement of the handler that
touches an id must be the owner check. It is a top-level statement with no
condition, and nothing that reads, writes or calls with the id comes before
it. The owner check is one of these:

* ``await <checker>(..., <id>, ..., user...)`` as a statement or an
  assignment. A checker is ``load_owned_meeting``, ``require_owned_meeting``,
  ``actions._load_action``, ``meetings._load_meeting`` or
  ``recordings._recording_path``. The call must pass an id and the
  signed-in member (the parameter that is ``Depends(get_current_user)``).
* ``async with _tenant_session() as db:`` whose first statement that touches
  an id is such a call.
* A ``try`` whose first handler is ``except HTTPException: raise``, and whose
  first statement that touches an id is a check. Only a database error is
  caught then, and the refusal still goes out.

A route outside that rule is in one named list, with its reason:

* ``MACHINE_ROUTES``: the bot-token callbacks. The first statement is
  ``_check_bot_auth(authorization)``.
* ``PREDICATE_IN_SQL``: the predicate is bound INTO the write, and a write of
  0 rows answers 404.
* ``GUARDED_ROUTES``: an optional id from the query or the body. Each has its
  own test of its shape.

Fences:

* ``notes-owner-check-first``: every route with an id meets the rule or is in
  a named list.
* ``notes-checkers-reach-the-rule``: each checker is in its home module and
  reaches the rule. A module may use only the checkers it defines or imports.
* ``notes-lists-owner-scoped``: ``/bots/active`` and ``/live/sessions`` bind
  the predicate in their SQL.
* ``notes-fence-finds-a-gap``: the reader flags each planted gap below.
* ``notes-context-cache-per-tenant``: the context pack cache keys on the
  bound tenant as well as the meeting.

**What this fence still cannot catch.** A checker that is wrong inside its
own SQL (the R8 suite proves the five). A route that reaches a meeting through
an id with another name, for example a bot id or a run id. A route on a router
outside ``routes/notes``. Code after the check that reads a meeting other
than the checked one. A route whose path is not a string constant is not
read: the reader reports it as unreadable, so it fails. The full R8 proof is
``test_notes_meeting_owner_r8.py``.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

_NOTES = (
    Path(__file__).resolve().parents[2]
    / "apps" / "services" / "gateway" / "gateway" / "routes" / "notes"
)

#: The names that ARE the one owner rule.
_RULE = frozenset({"load_owned_meeting", "OWNED_MEETING_PREDICATE"})

#: The names of an id that names a meeting or a row of a meeting.
ID_NAMES = frozenset({"meeting_id", "action_id", "recording_id"})
_PATH_ID = re.compile(r"\{(meeting_id|action_id|recording_id)\}")

#: Each checker, with its home module. A module may call a checker that it
#: defines there or imports from there.
CHECKERS: dict[str, str] = {
    "load_owned_meeting": "core.py",
    "require_owned_meeting": "core.py",
    "_load_action": "actions.py",
    "_load_meeting": "meetings.py",
    "_recording_path": "recordings.py",
}

#: Routes with an id and no member. Each is a callback of the meeting-bot
#: worker, authed by ``MEETING_BOT_TOKEN``. The worker has no member identity,
#: so a member owner check has no owner to compare (``live.read_live_wanted``).
MACHINE_ROUTES: dict[str, str] = {
    "/meetings/{meeting_id}/live/segment":
        "the worker posts live segments. Bot token, no member",
    "/meetings/{meeting_id}/live/wanted":
        "the worker asks if it may stream. Bot token, no member, one boolean",
}

#: Routes that bind the predicate INTO their one write. One statement
#: decides, and a write of 0 rows is the 404 of the detail route.
PREDICATE_IN_SQL: dict[tuple[str, str], str] = {
    ("copilot_context.py", "set_brief"):
        "UPDATE meeting ... AND OWNED_MEETING_PREDICATE, 404 on 0 rows",
    ("copilot_agenda.py", "write_agenda"):
        "_WRITE_AGENDA_SQL binds the predicate, 404 on 0 rows",
}

#: Routes whose id is optional and comes from the query or the body.
GUARDED_ROUTES: dict[tuple[str, str], str] = {
    ("live.py", "live_token"):
        "the query meeting_id is optional. With it, the check runs first",
    ("meeting_bot.py", "bot_join"):
        "the body meeting_id is optional. With it, the attach UPDATE binds "
        "the predicate. Without it, the route makes a new meeting of the caller",
}

_METHODS = {"get", "post", "put", "patch", "delete", "api_route"}


# ── The reader ─────────────────────────────────────────────────────────────


def _const_path(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _routes(tree: ast.Module) -> list[tuple[str | None, ast.AST, int]]:
    """``(path, handler, line)`` for each route. ``path`` is None when it is
    not a string constant."""
    defs = {
        n.name: n for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    out: list[tuple[str | None, ast.AST, int]] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                if (
                    isinstance(dec, ast.Call)
                    and isinstance(dec.func, ast.Attribute)
                    and isinstance(dec.func.value, ast.Name)
                    and dec.func.value.id == "router"
                    and dec.func.attr in _METHODS
                ):
                    path = _const_path(dec.args[0]) if dec.args else None
                    out.append((path, node, node.lineno))
        elif (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Attribute)
            and node.value.func.attr == "add_api_route"
        ):
            call = node.value
            path = _const_path(call.args[0]) if call.args else None
            target = call.args[1] if len(call.args) > 1 else next(
                (k.value for k in call.keywords if k.arg == "endpoint"), None)
            fn = defs.get(target.id) if isinstance(target, ast.Name) else None
            out.append((path, fn if fn is not None else node, node.lineno))
    return out


def _names(node: ast.AST) -> set[str]:
    out: set[str] = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Name):
            out.add(n.id)
        elif isinstance(n, ast.Attribute):
            out.add(n.attr)
    return out


def _touches(node: ast.AST, ids: set[str]) -> bool:
    for n in ast.walk(node):
        if isinstance(n, ast.Name) and n.id in ids:
            return True
        if isinstance(n, ast.Attribute) and n.attr in ids:
            return True
    return False


def _params(fn: ast.AST) -> list[ast.arg]:
    a = fn.args
    return [*a.posonlyargs, *a.args, *a.kwonlyargs]


def _member_param(fn: ast.AST) -> str | None:
    """The parameter that is ``Depends(get_current_user)``."""
    a = fn.args
    pos = [*a.posonlyargs, *a.args]
    pairs = list(zip(pos[len(pos) - len(a.defaults):], a.defaults, strict=True))
    pairs += [
        (p, d) for p, d in zip(a.kwonlyargs, a.kw_defaults, strict=True) if d is not None
    ]
    for p, d in pairs:
        if isinstance(d, ast.Call) and "get_current_user" in _names(d):
            return p.arg
    return None


def _ids(path: str, fn: ast.AST) -> tuple[set[str], bool]:
    """The id names of a route, and whether one comes from a body field."""
    ids = set(_PATH_ID.findall(path))
    params = {p.arg for p in _params(fn)}
    ids |= params & ID_NAMES
    body = any(
        isinstance(n, ast.Attribute) and n.attr in ID_NAMES
        and isinstance(n.value, ast.Name) and n.value.id in params
        for n in ast.walk(fn)
    )
    return ids, body


def _module_checkers(tree: ast.Module, filename: str) -> set[str]:
    """The checkers this module may call: defined here, or imported from home."""
    out = {
        n.name for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and CHECKERS.get(n.name) == filename
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            home = node.module.rsplit(".", 1)[-1] + ".py"
            out |= {a.name for a in node.names if CHECKERS.get(a.name) == home}
    return out


def _predicate_constants(tree: ast.Module) -> set[str]:
    """Module constants whose value binds the predicate, closed over each other."""
    assigns = {
        t.id: n.value for n in tree.body if isinstance(n, ast.Assign)
        for t in n.targets if isinstance(t, ast.Name)
    }
    known = {"OWNED_MEETING_PREDICATE"}
    changed = True
    while changed:
        changed = False
        for name, value in assigns.items():
            if name not in known and _names(value) & known:
                known.add(name)
                changed = True
    return known


class _Ctx:
    def __init__(self, ids: set[str], checkers: set[str], member: str | None,
                 predicates: set[str] | None) -> None:
        self.ids, self.checkers, self.member = ids, checkers, member
        #: None: a statement that only names the predicate does not count.
        self.predicates = predicates


def _checker_call(stmt: ast.AST, ctx: _Ctx) -> bool:
    if not isinstance(stmt, (ast.Expr, ast.Assign, ast.AnnAssign)):
        return False
    value = stmt.value
    if not (isinstance(value, ast.Await) and isinstance(value.value, ast.Call)):
        return False
    call = value.value
    if not (isinstance(call.func, ast.Name) and call.func.id in ctx.checkers):
        return False
    args = [*call.args, *(k.value for k in call.keywords)]
    passes_id = any(_touches(a, ctx.ids) for a in args)
    passes_member = ctx.member is not None and any(
        isinstance(n, ast.Name) and n.id == ctx.member
        for a in args for n in ast.walk(a)
    )
    return passes_id and passes_member


def _is_bound_with(stmt: ast.AST) -> bool:
    if not (isinstance(stmt, ast.AsyncWith) and len(stmt.items) == 1):
        return False
    ctx_expr = stmt.items[0].context_expr
    return (
        isinstance(ctx_expr, ast.Call)
        and isinstance(ctx_expr.func, ast.Name)
        and ctx_expr.func.id == "_tenant_session"
        and not ctx_expr.args
    )


def _reraises_refusal(stmt: ast.AST) -> bool:
    """A ``try`` whose FIRST handler is ``except HTTPException: raise``.

    It lets the 404 of the check through, and only turns a database error into
    a safe answer (``agenda_progress.read_progress``). Any other ``try``
    around a check can swallow the refusal, so it does not count."""
    if not isinstance(stmt, ast.Try) or not stmt.handlers:
        return False
    first = stmt.handlers[0]
    return (
        isinstance(first.type, ast.Name) and first.type.id == "HTTPException"
        and len(first.body) == 1 and isinstance(first.body[0], ast.Raise)
        and first.body[0].exc is None
    )


def _first_touch_ok(body: list[ast.stmt], ctx: _Ctx) -> bool:
    for stmt in body:
        if isinstance(stmt, (ast.Import, ast.ImportFrom)):
            continue
        if not _touches(stmt, ctx.ids):
            continue
        if _checker_call(stmt, ctx):
            return True
        if _is_bound_with(stmt):
            return _first_touch_ok(stmt.body, ctx)
        if _reraises_refusal(stmt):
            return _first_touch_ok(stmt.body, ctx)
        if ctx.predicates is not None and isinstance(stmt, (ast.Expr, ast.Assign)):
            return bool(_names(stmt) & ctx.predicates)
        return False
    return False


def _body(fn: ast.AST) -> list[ast.stmt]:
    body = list(fn.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        body = body[1:]
    return body


def _gaps(source: str, filename: str) -> list[str]:
    """Each route of ``source`` that breaks the rule, as ``name (path)``."""
    tree = ast.parse(source)
    checkers = _module_checkers(tree, filename)
    predicates = _predicate_constants(tree)
    gaps: list[str] = []
    for path, fn, line in _routes(tree):
        if path is None or not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            gaps.append(f"unreadable route at line {line}")
            continue
        ids, from_body = _ids(path, fn)
        if not ids and not from_body:
            continue
        key = (filename, fn.name)
        if path in MACHINE_ROUTES or key in GUARDED_ROUTES:
            continue
        label = f"{fn.name} ({path})"
        if from_body:
            gaps.append(f"{label}: an id from the body needs a GUARDED_ROUTES entry")
            continue
        ctx = _Ctx(ids, checkers, _member_param(fn),
                   predicates if key in PREDICATE_IN_SQL else None)
        if not _first_touch_ok(_body(fn), ctx):
            gaps.append(label)
    return gaps


def _sources() -> list[tuple[str, str]]:
    return [(f.name, f.read_text(encoding="utf-8")) for f in sorted(_NOTES.glob("*.py"))]


def _function(filename: str, name: str) -> ast.AST:
    tree = ast.parse((_NOTES / filename).read_text(encoding="utf-8"))
    return next(
        n for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name
    )


# ── The fences over the real source ────────────────────────────────────────


def test_every_route_with_an_id_checks_the_owner_first() -> None:
    gaps = [f"{name}: {g}" for name, src in _sources() for g in _gaps(src, name)]
    assert gaps == [], (
        "These /notes routes touch a meeting, an action or a recording id "
        "before the owner check, or never check it (task 3966, owner "
        f"2026-11-26): {gaps}"
    )


def test_the_reader_sees_the_routes() -> None:
    """A broken reader would find no route and pass. It must see them all."""
    with_id = [
        (name, fn.name) for name, src in _sources()
        for path, fn, _line in _routes(ast.parse(src))
        if path and (_PATH_ID.search(path) or ({p.arg for p in _params(fn)} & ID_NAMES))
    ]
    assert len(with_id) >= 46, with_id


def test_each_checker_lives_at_home_and_reaches_the_rule() -> None:
    for name, home in CHECKERS.items():
        assert _names(_function(home, name)) & _RULE, f"{home}:{name} left the rule"
    # `dispatch._load_meeting` has no owner check, and it is not a checker.
    tree = ast.parse((_NOTES / "dispatch.py").read_text(encoding="utf-8"))
    assert "_load_meeting" not in _module_checkers(tree, "dispatch.py")


def test_the_named_lists_are_current() -> None:
    found: dict[tuple[str, str], tuple[str | None, ast.AST]] = {}
    for name, src in _sources():
        for path, fn, _line in _routes(ast.parse(src)):
            found[(name, fn.name)] = (path, fn)
    paths = {path: fn for path, fn in found.values()}
    for path in MACHINE_ROUTES:
        assert path in paths, f"stale machine route: {path}"
        fn = paths[path]
        first = next(s for s in _body(fn) if not isinstance(s, (ast.Import, ast.ImportFrom)))
        assert isinstance(first, ast.Expr) and "_check_bot_auth" in _names(first), (
            f"{path} must check the bot token first")
        assert _member_param(fn) is None, f"{path} has a member"
    for key in (*PREDICATE_IN_SQL, *GUARDED_ROUTES):
        assert key in found, f"stale entry: {key}"
    for key in PREDICATE_IN_SQL:
        names = _names(found[key][1])
        assert "rowcount" in names and "HTTPException" in names, key


def test_the_query_id_of_live_token_is_checked_first() -> None:
    fn = _function("live.py", "live_token")
    guard = next(s for s in _body(fn) if _touches(s, {"meeting_id"}))
    assert isinstance(guard, ast.If) and _names(guard.test) == {"meeting_id"}
    ctx = _Ctx({"meeting_id"}, {"load_owned_meeting"}, _member_param(fn), None)
    assert _first_touch_ok(guard.body, ctx), "live-token reads before the check"


def test_the_body_id_of_bot_join_binds_the_predicate() -> None:
    fn = _function("meeting_bot.py", "bot_join")
    branch = next(
        n for n in ast.walk(fn)
        if isinstance(n, ast.If) and _touches(n.test, {"meeting_id"})
    )
    first = next(s for s in branch.body if _touches(s, {"meeting_id"}))
    assert "OWNED_MEETING_PREDICATE" in _names(first), "the attach UPDATE lost the rule"
    assert "UPDATE meeting" in ast.unparse(first)
    assert "INSERT INTO meeting" in ast.unparse(branch.orelse[0]), "create branch moved"


@pytest.mark.parametrize(
    ("module", "function"),
    [("meeting_bot.py", "list_active_bots"), ("live_session.py", "list_live_sessions")],
)
def test_the_two_lists_bind_the_owner_predicate(module: str, function: str) -> None:
    tree = ast.parse((_NOTES / module).read_text(encoding="utf-8"))
    fn = _function(module, function)
    assert _names(fn) & _predicate_constants(tree), (
        f"{module}:{function} lists meetings of other members")


# ── The planted gaps (each must be found) ──────────────────────────────────

_HEAD = (
    "from gateway.routes.notes.core import OWNED_MEETING_PREDICATE, "
    "load_owned_meeting, require_owned_meeting\n\n"
)

PLANTED = {
    "check after the read": (
        "@router.get('/meetings/{meeting_id}/a')\n"
        "async def a(meeting_id: str, user=Depends(get_current_user)):\n"
        "    async with _tenant_session() as db:\n"
        "        row = await db.execute(text('SELECT 1'), {'id': meeting_id})\n"
        "        await load_owned_meeting(db, meeting_id, user.email)\n"
        "        return row\n"
    ),
    "check after a side effect": (
        "@router.get('/meetings/{meeting_id}/b')\n"
        "async def b(meeting_id: str, user=Depends(get_current_user)):\n"
        "    await _refresh_bot(meeting_id)\n"
        "    await require_owned_meeting(meeting_id, user.email)\n"
    ),
    "check in a dead branch": (
        "@router.get('/meetings/{meeting_id}/c')\n"
        "async def c(meeting_id: str, user=Depends(get_current_user)):\n"
        "    if False:\n"
        "        await require_owned_meeting(meeting_id, user.email)\n"
        "    return await read(meeting_id)\n"
    ),
    "check swallowed by a try that catches the 404 first": (
        "@router.get('/meetings/{meeting_id}/d2')\n"
        "async def d2(meeting_id: str, user=Depends(get_current_user)):\n"
        "    try:\n"
        "        await require_owned_meeting(meeting_id, user.email)\n"
        "    except Exception:\n"
        "        return None\n"
        "    except HTTPException:\n"
        "        raise\n"
        "    return await read(meeting_id)\n"
    ),
    "check swallowed by a try": (
        "@router.get('/meetings/{meeting_id}/d')\n"
        "async def d(meeting_id: str, user=Depends(get_current_user)):\n"
        "    try:\n"
        "        await require_owned_meeting(meeting_id, user.email)\n"
        "    except Exception:\n"
        "        pass\n"
        "    return await read(meeting_id)\n"
    ),
    "id from the query": (
        "@router.get('/e')\n"
        "async def e(meeting_id: str, user=Depends(get_current_user)):\n"
        "    return await read(meeting_id)\n"
    ),
    "id from the body": (
        "@router.post('/f')\n"
        "async def f(body: Req, user=Depends(get_current_user)):\n"
        "    return await read(body.meeting_id)\n"
    ),
    "api_route with no check": (
        "@router.api_route('/meetings/{meeting_id}/g', methods=['GET'])\n"
        "async def g(meeting_id: str, user=Depends(get_current_user)):\n"
        "    return await read(meeting_id)\n"
    ),
    "add_api_route with no check": (
        "async def h(meeting_id: str, user=Depends(get_current_user)):\n"
        "    return await read(meeting_id)\n"
        "\n"
        "router.add_api_route('/meetings/{meeting_id}/h', h, methods=['GET'])\n"
    ),
    "only names the predicate": (
        "@router.get('/meetings/{meeting_id}/i')\n"
        "async def i(meeting_id: str, user=Depends(get_current_user)):\n"
        "    async with _tenant_session() as db:\n"
        "        return await db.execute(text(f'SELECT 1 WHERE {OWNED_MEETING_PREDICATE}'),\n"
        "                                {'id': meeting_id})\n"
    ),
    "owner from the body, not the member": (
        "@router.post('/meetings/{meeting_id}/j')\n"
        "async def j(meeting_id: str, body: Req, user=Depends(get_current_user)):\n"
        "    await require_owned_meeting(meeting_id, body.email)\n"
        "    return await read(meeting_id)\n"
    ),
    "a checker this module does not have": (
        "@router.get('/meetings/{meeting_id}/k')\n"
        "async def k(meeting_id: str, user=Depends(get_current_user)):\n"
        "    async with _tenant_session() as db:\n"
        "        await _load_meeting(db, meeting_id, user)\n"
    ),
    "a stream that checks inside the body": (
        "async def _sse(meeting_id):\n"
        "    await require_owned_meeting(meeting_id, None)\n"
        "    yield b''\n"
        "\n"
        "@router.get('/meetings/{meeting_id}/l')\n"
        "async def l(meeting_id: str, user=Depends(get_current_user)):\n"
        "    return StreamingResponse(_sse(meeting_id))\n"
    ),
    "a path that is not a constant": (
        "@router.get(PATH)\n"
        "async def m(meeting_id: str, user=Depends(get_current_user)):\n"
        "    return await read(meeting_id)\n"
    ),
}


@pytest.mark.parametrize("case", sorted(PLANTED))
def test_the_reader_finds_each_planted_gap(case: str) -> None:
    assert len(_gaps(_HEAD + PLANTED[case], "planted.py")) == 1, case


def test_the_reader_passes_the_good_shapes() -> None:
    good = _HEAD + (
        "@router.get('/meetings/{meeting_id}/ok1')\n"
        "async def ok1(meeting_id: str, user=Depends(get_current_user)):\n"
        "    if not user.email:\n"
        "        raise HTTPException(400)\n"
        "    await require_owned_meeting(meeting_id, user.email)\n"
        "    return await read(meeting_id)\n"
        "\n"
        "@router.get('/meetings/{meeting_id}/ok2')\n"
        "async def ok2(meeting_id: str, user=Depends(get_current_user)):\n"
        "    async with _tenant_session() as db:\n"
        "        await load_owned_meeting(db, meeting_id, user.email)\n"
        "        return await db.execute(text('SELECT 1'), {'id': meeting_id})\n"
    )
    assert _gaps(good, "planted.py") == []


def test_the_reader_flags_the_old_shape_of_get_note() -> None:
    """The shape of the old `GET /note`: a read by the id with no check."""
    old = _HEAD + (
        "@router.get('/meetings/{meeting_id}/note')\n"
        "async def get_note(meeting_id: str, _user=Depends(get_current_user)):\n"
        "    async with _tenant_session() as db:\n"
        "        return await db.execute(text('SELECT * FROM meeting_note'),\n"
        "                                {'id': meeting_id})\n"
    )
    assert _gaps(old, "summaries.py") == ["get_note (/meetings/{meeting_id}/note)"]


# ── The context pack cache ─────────────────────────────────────────────────


async def test_the_context_cache_keys_on_the_tenant(monkeypatch) -> None:
    from acb_common.db import bind_tenant, release_tenant
    from gateway.routes.notes import copilot_context

    built: list[str] = []

    async def build(meeting_id: str, *, deep: bool = False, db=None):
        built.append(meeting_id)
        return copilot_context.ContextPack(brief=f"pack {len(built)}")

    monkeypatch.setattr(copilot_context, "build", build)
    monkeypatch.setattr(copilot_context, "_CACHE", {})
    mid = "00000000-0000-0000-0000-000000003966"
    token = bind_tenant("11111111-1111-1111-1111-111111111111")
    try:
        first = await copilot_context.get(mid)
        again = await copilot_context.get(mid)
    finally:
        release_tenant(token)
    token = bind_tenant("22222222-2222-2222-2222-222222222222")
    try:
        other = await copilot_context.get(mid)
        copilot_context.forget(mid)
    finally:
        release_tenant(token)
    assert first is again
    assert other.brief == "pack 2", "a pack of one tenant served another tenant"
    assert built == [mid, mid]
    # `forget` under the second tenant drops only that tenant's pack.
    assert len(copilot_context._CACHE) == 1
