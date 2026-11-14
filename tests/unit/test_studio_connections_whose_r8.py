"""WS-STU I9a — whose account a connection need uses, on a real database (R8).

Spec: ``project-docs/specs/ai_studio.md`` §8.1 rules 3, 4 and 10, and the I9
row of §14. Code: ``acb_common/studio_manifest.py`` (``connection_as``),
``gateway/connections/store.py::met_providers`` (the split read),
``gateway/package_host/resolve.py::first_unmet_connection`` (the one rule) and
``gateway/routes/studio/invoke.py::_needs_connection`` (the door).

The real handlers run on a phase-4 promoted scratch database, as the
non-privileged app role, so FORCE RLS binds each statement. Org A has Owen
(the publisher), Ada (an admin who approves) and Mel (a member who runs the
app). Org B has Zed. The gate ``STUDIO_CONNECTION_GATE_ORGS`` is on for both.

Run (a PRIVATE ladder database, one R8 suite at a time)::

    export TENANT_LADDER_DATABASE_URL=<the DSN of your private database>
    uv run pytest tests/unit/test_studio_connections_whose_r8.py -v -rs -p no:randomly

Without the database every test here SKIPS, and the run reads green. A skip
is not a pass.

**Acceptance (I9a).**

a. A ``member`` need is met only by the row of the viewer, in one org and
   across two orgs. An org row and the row of another member never meet it.
b. A ``member`` need with no row of the viewer is 409 with the provider and
   ``as``, and the action does not run, even when an org row is connected.
c. An ``org`` need with no approved org install runs no action. The approval
   of org A does not apply to org B.
d. A version that widens ``scopes`` is a new version. It is pending, and the
   door refuses an install that pins it until a holder approves it.
e. The publisher cannot approve their own version (J2b-1, one assertion).

**The mutations of I9a.** Each ran on 2026-11-14.

* W1. ``connections/store._MET_SQL``: drop ``AND member_email = :member``.
  Cases a and b go red: the row of Ada meets the need of Mel.
* W2. ``resolve.first_unmet_connection``: test ``name in met.org`` for a
  ``member`` need. Case b goes red: an org row meets a member need.
* W3. ``resolve.first_unmet_connection``: drop ``org_approved``. Case c goes
  red: a pending version meets an org need.
* W4. ``resolve.org_install_approved``: drop the ``scope_kind == "org"`` test.
  The hermetic truth table goes red.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth import UserContext, UserRole, build_access
from acb_common import get_settings
from acb_common.db import bind_tenant, release_tenant, tenant_session
from fastapi import HTTPException
from gateway.routes.studio import invoke
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    promoted,
)
from tests.unit.test_studio_package_approval_routes_r8 import (
    ADA,
    MEL,
    OWEN,
    TAG,
    ZED,
    _ask,
    _decide,
    _install_org,
    _states,
)
from tests.unit.test_studio_package_approval_routes_r8 import (
    _call as _pkg_call,
)
from tests.unit.test_studio_package_approval_routes_r8 import (  # noqa: F401
    world as _base_world,
)

pytestmark = _DB_GATE

TALLY = f"mcp:tally-{TAG}"
_COUNTER = iter(range(10_000))
_MIDS: dict[str, str] = {}
_FIELDS = [{"key": "site", "type": "text"}]
_DENIED = {"code": invoke.NEEDS_CONNECTION, "message": invoke.CONNECTION_TEXT}


def _manifest(mid: str, connections: list[Any], purpose: str = "Track the visits to a site.",
              ) -> dict[str, Any]:
    return {
        "studio": 1, "id": mid, "name": "Site visits", "purpose": purpose,
        "connections": connections,
        "objects": [{"name": "visit", "fields": _FIELDS}],
        "actions": [{"id": "find", "uses": "object:visit.list"}],
    }


def _door_user(org: str, email: str) -> UserContext:
    return UserContext(email=email, role=UserRole.EMPLOYEE,
                       access=build_access(["feature:studio"]), organization_id=org)


class _Req:
    """The one part of a request that the door reads."""

    def __init__(self) -> None:
        self.method, self.headers = "POST", {}
        self.url = SimpleNamespace(path="/studio/apps/x/actions/find")
        self.query_params: dict[str, str] = {}

    async def stream(self) -> Any:
        yield b""


async def _door(org: str, email: str, pid: str) -> tuple[int, Any]:
    token = bind_tenant(org)
    try:
        return 200, await invoke.invoke_action(pid, "find", request=_Req(),  # type: ignore[arg-type]
                                              user=_door_user(org, email))
    except HTTPException as exc:
        return exc.status_code, exc.detail
    finally:
        release_tenant(token)


async def _live(org: str, who: str, connections: list[Any]) -> str:
    """A new package of *who* with one published version."""
    mid = f"i9a{TAG}-app{next(_COUNTER)}"
    made = await _pkg_call("create_package", org, who, payload=_manifest(mid, connections))
    assert made[0] == 200, made
    pid = str(made[1]["package"]["id"])
    _MIDS[pid] = mid
    assert (await _pkg_call("publish", org, who, package_id=pid, payload={}))[0] == 200
    return pid


async def _installed(org: str, who: str, connections: list[Any]) -> str:
    """A live package with an approved version and an ORG install."""
    pid = await _live(org, who, connections)
    assert (await _ask(org, pid, 1, who))[0] == 202
    assert (await _decide("approve", org, pid, 1))[0] == 200
    assert (await _install_org(org, pid))[0] == 201
    return pid


def _connect(world: Any, org: str, *, member: str | None = None, provider: str = TALLY,
             status: str = "connected") -> None:
    with world.p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO integration_connections (organization_id, provider, owning_app, "
            "scope_kind, member_email, status) VALUES (CAST(:o AS uuid), :p, 'integrations', "
            ":k, :m, :s)"),
            {"o": org, "p": provider, "k": "member" if member else "org",
             "m": member.lower() if member else None, "s": status})


def _run(world: Any, fn: Any) -> Any:
    async def go() -> Any:
        async with tenant_engine_scope(world.p.app_url.render_as_string(hide_password=False)):
            return await fn()
    return asyncio.run(go())


@pytest.fixture
def world(_base_world, monkeypatch):  # noqa: F811
    """The base world of J2b-1, with the connection gate on for every org and
    a spy on the executor of ``object:visit.list``. ``ran`` counts each run
    of the action."""
    monkeypatch.setattr(get_settings(), "studio_connection_gate_orgs", "*")
    w = _base_world
    w.ran = []
    real = invoke._READS["list"]

    async def spy(*args: Any, **kwargs: Any) -> Any:
        w.ran.append(1)
        return await real(*args, **kwargs)

    monkeypatch.setitem(invoke._READS, "list", spy)
    yield w
    with w.p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM integration_connections WHERE provider LIKE :p"),
                  {"p": f"%{TAG}%"})
        c.execute(text("DELETE FROM package_install WHERE app_id IN (SELECT id FROM apps "
                       "WHERE owner_email LIKE :t)"), {"t": f"%.{TAG}@j2b1.test"})


# ── a. A member need is met only by the row of the viewer ──────────────────


def test_r8_a_member_need_is_met_only_by_the_viewers_own_row(world) -> None:
    async def go() -> list[tuple[int, Any]]:
        pid = await _installed(world.a, OWEN, [{"name": TALLY, "as": "member"}])
        out = [await _door(world.a, MEL, pid)]                   # no row at all
        _connect(world, world.a)                                 # an org row
        out.append(await _door(world.a, MEL, pid))
        _connect(world, world.a, member=ADA)                     # another member's row
        out.append(await _door(world.a, MEL, pid))
        _connect(world, world.b, member=MEL)                     # Mel's address, in org B
        out.append(await _door(world.a, MEL, pid))
        _connect(world, world.a, member=MEL, status="needs_reconnect")
        out.append(await _door(world.a, MEL, pid))
        assert world.ran == []
        with world.p.admin_engine.begin() as c:
            c.execute(text("UPDATE integration_connections SET status = 'connected' "
                           "WHERE member_email = :m AND provider = :p"),
                      {"m": MEL.lower(), "p": TALLY})
        out.append(await _door(world.a, MEL, pid))               # Mel's own row
        out.append(await _door(world.a, ADA, pid))               # Ada has her own row too
        return out

    *refused, mine, ada = _run(world, go)
    want = {**_DENIED, "provider": TALLY, "as": "member"}
    assert refused == [(409, want)] * 5
    assert mine[0] == 200 and ada[0] == 200, (mine, ada)
    assert world.ran == [1, 1]


def test_r8_a_member_need_in_org_b_never_borrows_a_row_of_org_a(world) -> None:
    async def go() -> tuple[tuple[int, Any], tuple[int, Any]]:
        pid = await _live(world.b, ZED, [{"name": TALLY, "as": "member"}])
        _connect(world, world.a, member=ZED)                     # Zed's address, in org A
        _connect(world, world.a)
        first = await _door(world.b, ZED, pid)
        _connect(world, world.b, member=ZED)
        return first, await _door(world.b, ZED, pid)

    first, second = _run(world, go)
    assert first == (409, {**_DENIED, "provider": TALLY, "as": "member"})
    assert second[0] == 200 and world.ran == [1]


def test_r8_the_refusal_names_no_other_member(world) -> None:
    async def go() -> tuple[int, Any]:
        pid = await _installed(world.a, OWEN, [{"name": TALLY, "as": "member"}])
        _connect(world, world.a, member=ADA)
        _connect(world, world.a, member=OWEN)
        return await _door(world.a, MEL, pid)

    status, detail = _run(world, go)
    assert status == 409
    assert set(detail) == {"code", "message", "provider", "as"}
    blob = repr(detail).lower()
    assert ADA.lower() not in blob and OWEN.lower() not in blob and "@" not in blob


# ── c. An org need waits for an approved org install ──────────────────────


def test_r8_an_org_need_runs_only_on_an_approved_org_install(world) -> None:
    async def go() -> list[tuple[int, Any]]:
        pid = await _installed(world.a, OWEN, [TALLY])           # a plain name is `org`
        out = [await _door(world.a, MEL, pid)]                   # no org row yet
        _connect(world, world.a)
        out.append(await _door(world.a, MEL, pid))               # row and approval: runs
        # The version goes back to pending (a widened scope asks again).
        with world.p.admin_engine.begin() as c:
            c.execute(text("UPDATE app_versions SET review_status = 'pending' "
                           "WHERE app_id = CAST(:p AS uuid)"), {"p": pid})
        out.append(await _door(world.a, MEL, pid))
        assert (await _decide("approve", world.a, pid, 1))[0] == 200
        out.append(await _door(world.a, MEL, pid))
        return out

    none, ok, pending, again = _run(world, go)
    want = {**_DENIED, "provider": TALLY, "as": "org"}
    assert none == (409, want) and pending == (409, want)
    assert ok[0] == 200 and again[0] == 200
    assert world.ran == [1, 1]


def test_r8_the_owners_draft_and_a_member_row_never_meet_an_org_need(world) -> None:
    """The owner runs the draft, so an org need fails closed even with an
    org row (the known limit of I9a, until I9b). A member row never meets it."""
    async def go() -> list[tuple[int, Any]]:
        pid = await _live(world.a, OWEN, [TALLY])
        _connect(world, world.a)
        out = [await _door(world.a, OWEN, pid)]
        _connect(world, world.a, member=OWEN)
        out.append(await _door(world.a, OWEN, pid))
        return out

    got = _run(world, go)
    assert got == [(409, {**_DENIED, "provider": TALLY, "as": "org"})] * 2
    assert world.ran == []


async def _sidebar(org: str, who: str) -> bool:
    """`needs_setup` of the one install that *who* sees, from the real SQL and
    the real read of the connections, through the one resolve."""
    from gateway.package_host import resolve

    token = bind_tenant(org)
    try:
        async with tenant_session(org) as db:
            kept = await resolve.visible_installs(db, viewer=who)
            met = await resolve.met_connections(db, viewer=who)
        claims = [resolve.ClaimRow(install_id=i.id, point="jobs", key="k", payload={})
                  for i in kept]
        out = resolve.resolve({}, kept, claims, lambda _f: True, met, who)
        (entry,) = out["jobs"]
        return bool(entry["needs_setup"])
    finally:
        release_tenant(token)


def test_r8_the_owner_of_an_installed_app_gets_one_answer_in_door_and_sidebar(world) -> None:
    """P2-1. The door runs the owner's DRAFT, so an `org` need is 409 for the
    owner even with an approved org install and an org row. The sidebar says
    `needs_setup` for the owner too. Another member: 200 and ready."""
    async def go() -> dict[str, Any]:
        pid = await _installed(world.a, OWEN, [TALLY])
        _connect(world, world.a)
        return {"owner_door": await _door(world.a, OWEN, pid),
                "owner_side": await _sidebar(world.a, OWEN),
                "mel_door": await _door(world.a, MEL, pid),
                "mel_side": await _sidebar(world.a, MEL)}

    got = _run(world, go)
    assert got["owner_door"] == (409, {**_DENIED, "provider": TALLY, "as": "org"})
    assert got["owner_side"] is True
    assert got["mel_door"][0] == 200 and got["mel_side"] is False
    assert world.ran == [1]


def test_r8_a_member_need_is_the_same_for_the_owner_in_door_and_sidebar(world) -> None:
    async def go() -> tuple[tuple[int, Any], bool]:
        pid = await _installed(world.a, OWEN, [{"name": TALLY, "as": "member"}])
        _connect(world, world.a, member=OWEN)
        return await _door(world.a, OWEN, pid), await _sidebar(world.a, OWEN)

    door, side = _run(world, go)
    assert door[0] == 200 and side is False


def test_r8_the_approval_of_org_a_does_not_apply_to_org_b(world) -> None:
    async def go() -> tuple[tuple[int, Any], tuple[int, Any], tuple[int, Any]]:
        await _installed(world.a, OWEN, [TALLY])                 # approved in org A
        pid_b = await _live(world.b, ZED, [TALLY])
        _connect(world, world.b)
        install = await _pkg_call("install_package", world.b, ZED, package_id=pid_b,
                                  payload={"scope": "org"})
        return install, await _door(world.b, ZED, pid_b), (0, _states(world, pid_b))

    install, door, (_, states) = _run(world, go)
    assert install[0] == 202 and install[1]["review_status"] == "pending", install
    assert door == (409, {**_DENIED, "provider": TALLY, "as": "org"})
    assert states == {1: "pending"}
    assert world.ran == []


# ── d. A widened scope is a new version that asks again ───────────────────


def test_r8_a_version_that_widens_scopes_waits_for_an_approval(world) -> None:
    async def go() -> dict[str, Any]:
        small = [{"name": TALLY, "as": "org", "scopes": ["read"]}]
        wide = [{"name": TALLY, "as": "org", "scopes": ["read", "write"]}]
        pid = await _installed(world.a, OWEN, small)
        _connect(world, world.a)
        v1 = await _door(world.a, MEL, pid)
        saved = await _pkg_call("save_draft", world.a, OWEN, package_id=pid,
                                payload=_manifest(_MIDS[pid], wide, "Wider."))
        published = await _pkg_call("publish", world.a, OWEN, package_id=pid, payload={})
        ask = await _install_org(world.a, pid)
        # The install of v2 cannot exist before the approval. A forced pin
        # of the pending version shows the door refuses it.
        with world.p.admin_engine.begin() as c:
            c.execute(text("UPDATE package_install SET version = 2 "
                           "WHERE app_id = CAST(:p AS uuid) AND scope_kind = 'org'"), {"p": pid})
        pinned = await _door(world.a, MEL, pid)
        own = await _decide("approve", world.a, pid, 2, who=OWEN)
        approved = await _decide("approve", world.a, pid, 2)
        after = await _door(world.a, MEL, pid)
        return {"v1": v1, "saved": saved[0], "published": published[0], "ask": ask,
                "pinned": pinned, "own": own[0], "approved": approved[0], "after": after,
                "states": _states(world, pid)}

    got = _run(world, go)
    assert got["v1"][0] == 200 and got["saved"] == 200 and got["published"] == 200
    assert got["ask"][0] == 202 and got["ask"][1]["version"] == 2, got["ask"]
    assert got["pinned"] == (409, {**_DENIED, "provider": TALLY, "as": "org"})
    assert got["own"] == 403, "the publisher cannot approve their own version (J2b-1)"
    assert got["approved"] == 200 and got["after"][0] == 200
    assert got["states"] == {1: "approved", 2: "approved"}
    assert world.ran == [1, 1]


def test_r8_the_gate_off_changes_nothing(world, monkeypatch) -> None:
    """With the org off the list, ``connections`` makes no need, and the door
    answers as before I1a, whatever ``as`` says."""
    async def go() -> list[tuple[int, Any]]:
        pid = await _live(world.a, OWEN, [{"name": TALLY, "as": "member"}, "zoho"])
        monkeypatch.setattr(get_settings(), "studio_connection_gate_orgs", "")
        return [await _door(world.a, OWEN, pid)]

    (status, _), = _run(world, go)
    assert status == 200 and world.ran == [1]
