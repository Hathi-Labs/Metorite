"""Task 3966: a source fence over the owner check of the ``/notes`` routes (R7).

Owner decision (2026-11-26): "Owner only, the same rule as the meetings list
and detail." The rule is ``core.OWNED_MEETING_PREDICATE`` and
``core.load_owned_meeting``. This fence reads the source of
``gateway/routes/notes/``. Every route whose path names a meeting, an action
or a recording must reach that rule in its own body, before any response
starts. A new route without the check fails here.

Fences:

* ``notes-route-has-owner-check``: each such route calls
  ``load_owned_meeting``, binds ``OWNED_MEETING_PREDICATE``, or calls a helper
  that does: one of its module, ``core.require_owned_meeting`` or
  ``actions._load_action``. A helper that is an async generator does not
  count, because a stream body runs after the response started.
* ``notes-machine-routes-named``: the only routes outside the rule are the
  machine callbacks of the meeting-bot worker. Each checks the bot token.
* ``notes-lists-owner-scoped``: ``/bots/active`` and ``/live/sessions`` bind
  the predicate in their SQL.
* ``notes-context-cache-per-tenant``: the context pack cache keys on the
  bound tenant as well as the meeting.
* ``notes-fence-finds-a-gap``: the reader flags a planted route with no check.

The full R8 proof is ``test_notes_meeting_owner_r8.py``.
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

#: A path parameter that names a meeting or a row of a meeting.
_ID_PARAM = re.compile(r"\{(meeting_id|action_id|recording_id)\}")

#: Routes with an id and no member. Each is a callback of the meeting-bot
#: worker, authed by ``MEETING_BOT_TOKEN``. The worker has no member identity,
#: so a member owner check has no owner to compare (``live.read_live_wanted``).
MACHINE_ROUTES: dict[str, str] = {
    "/meetings/{meeting_id}/live/segment":
        "the worker posts live segments. Bot token, no member",
    "/meetings/{meeting_id}/live/wanted":
        "the worker asks if it may stream. Bot token, no member, one boolean",
}

_METHODS = {"get", "post", "put", "patch", "delete"}


def _routes(tree: ast.Module) -> list[tuple[str, ast.AsyncFunctionDef | ast.FunctionDef]]:
    out = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if (
                isinstance(dec, ast.Call)
                and isinstance(dec.func, ast.Attribute)
                and isinstance(dec.func.value, ast.Name)
                and dec.func.value.id == "router"
                and dec.func.attr in _METHODS
                and dec.args
                and isinstance(dec.args[0], ast.Constant)
            ):
                out.append((str(dec.args[0].value), node))
    return out


def _is_async_generator(fn: ast.AST) -> bool:
    return isinstance(fn, ast.AsyncFunctionDef) and any(
        isinstance(n, (ast.Yield, ast.YieldFrom)) for n in ast.walk(fn)
    )


def _names(node: ast.AST) -> set[str]:
    """Each name and attribute that ``node`` reads."""
    out: set[str] = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Name):
            out.add(n.id)
        elif isinstance(n, ast.Attribute):
            out.add(n.attr)
    return out


def _checkers(tree: ast.Module, imported: frozenset[str] = frozenset()) -> set[str]:
    """The module-level functions and constants that reach the rule, closed
    over the calls between them. Async generators never count."""
    defs: dict[str, ast.AST] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not _is_async_generator(node):
                defs[node.name] = node
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    defs[t.id] = node.value
    known = set(_RULE) | set(imported)
    changed = True
    while changed:
        changed = False
        for name, node in defs.items():
            if name not in known and _names(node) & known:
                known.add(name)
                changed = True
    return known


#: The shared helpers that a module may import: ``core.require_owned_meeting``
#: and ``actions._load_action``. The fence proves that each reaches the rule.
_SHARED = {"core.py": "require_owned_meeting", "actions.py": "_load_action"}


def _shared_checkers() -> frozenset[str]:
    out: set[str] = set()
    for module, name in _SHARED.items():
        tree = ast.parse((_NOTES / module).read_text(encoding="utf-8"))
        out |= {name} & _checkers(tree)
    return frozenset(out)


def _gaps(source: str, imported: frozenset[str]) -> list[str]:
    tree = ast.parse(source)
    known = _checkers(tree, imported)
    gaps = []
    for path, fn in _routes(tree):
        if not _ID_PARAM.search(path) or path in MACHINE_ROUTES:
            continue
        if not (_names(fn) & known):
            gaps.append(f"{fn.name} ({path})")
    return gaps


def _all_routes() -> dict[str, tuple[str, ast.AST]]:
    out = {}
    for f in sorted(_NOTES.glob("*.py")):
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for path, fn in _routes(tree):
            out[path + " " + fn.name] = (f.name, fn)
    return out


def test_every_route_with_a_meeting_id_reaches_the_owner_rule() -> None:
    imported = _shared_checkers()
    assert imported == set(_SHARED.values()), f"a shared helper left the rule: {imported}"
    gaps = []
    for f in sorted(_NOTES.glob("*.py")):
        gaps += [f"{f.name}: {g}" for g in _gaps(f.read_text(encoding="utf-8"), imported)]
    assert gaps == [], (
        "These /notes routes name a meeting, an action or a recording and do "
        "not reach core.load_owned_meeting or core.OWNED_MEETING_PREDICATE "
        f"(task 3966, owner 2026-11-26): {gaps}"
    )


def test_the_reader_sees_the_routes() -> None:
    """A broken reader would find no route and pass. It must see them all."""
    with_id = [k for k in _all_routes() if _ID_PARAM.search(k)]
    assert len(with_id) >= 45, with_id


def test_the_machine_routes_are_the_bot_callbacks_only() -> None:
    found = {k.split(" ")[0]: v for k, v in _all_routes().items()}
    for path in MACHINE_ROUTES:
        assert path in found, f"stale machine route: {path}"
        _file, fn = found[path]
        assert "_check_bot_auth" in _names(fn), f"{path} must check the bot token"
        assert "get_current_user" not in _names(fn), f"{path} has a member"


@pytest.mark.parametrize(
    ("module", "function"),
    [("meeting_bot.py", "list_active_bots"), ("live_session.py", "list_live_sessions")],
)
def test_the_two_lists_bind_the_owner_predicate(module: str, function: str) -> None:
    tree = ast.parse((_NOTES / module).read_text(encoding="utf-8"))
    known = _checkers(tree)
    fn = next(
        n for n in tree.body
        if isinstance(n, ast.AsyncFunctionDef) and n.name == function
    )
    assert _names(fn) & known, f"{module}:{function} lists meetings of other members"


def test_the_reader_finds_a_planted_route_with_no_check() -> None:
    planted = (
        "@router.get('/meetings/{meeting_id}/leak')\n"
        "async def leak(meeting_id: str, user=Depends(get_current_user)):\n"
        "    async with _tenant_session() as db:\n"
        "        return await db.execute(text('SELECT 1'))\n"
        "\n"
        "async def _sse(meeting_id):\n"
        "    await load_owned_meeting(None, meeting_id, None)\n"
        "    yield b''\n"
        "\n"
        "@router.get('/meetings/{meeting_id}/stream')\n"
        "async def stream(meeting_id: str):\n"
        "    return StreamingResponse(_sse(meeting_id))\n"
        "\n"
        "@router.get('/meetings/{meeting_id}/fine')\n"
        "async def fine(meeting_id: str, user=Depends(get_current_user)):\n"
        "    return await _own(meeting_id, user)\n"
        "\n"
        "async def _own(meeting_id, user):\n"
        "    return await load_owned_meeting(None, meeting_id, user.email)\n"
    )
    assert _gaps(planted, frozenset()) == [
        "leak (/meetings/{meeting_id}/leak)",
        "stream (/meetings/{meeting_id}/stream)",
    ]


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
