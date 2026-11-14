"""WS-STU I8a — Connect in place, the read of the connections of a draft, on
a real database (R8).

Spec: ``project-docs/specs/ai_studio.md`` §8.1 and §14 I8a. Code: the route
``GET /studio/packages/{id}/connections`` (``routes/studio/connections.py``),
which reads ``package_host/resolve.met_connections`` and through it
``connections/store.met_providers``. The hermetic twin is
``tests/unit/test_studio_connect_in_place.py``.

The real handlers run on a phase-4 promoted scratch database, as the
non-privileged app role, so FORCE RLS binds each statement. Each call binds
its org as the auth layer does. Org A has Owen, who owns the packages, and
Ben. Org B has Zed. Each provider name of this suite holds the run's tag, so
a row of another suite never meets a need here.

Run (a PRIVATE ladder database, one R8 suite at a time, because the
``promoted`` fixture drops ``<db>_h3rls``)::

    eval "$(bash scripts/dev_db.sh --export)"
    export TENANT_LADDER_DATABASE_URL="${TENANT_LADDER_DATABASE_URL%/*}/<private db>"
    uv run pytest tests/unit/test_studio_connect_in_place_r8.py -v -rs

Without the database every test here SKIPS, and the run reads green. A skip
is not a pass.

**Acceptance (I8a).**

a. An org row of org A meets the need of Owen's draft in org A. A name with
   no row reads not met. The answer holds the five keys of each row, in the
   order of the draft.
b. The org row of org A never meets the need of a draft in org B, and the
   org row of org B never meets a need in org A.
c. The member row of Ben meets Ben's own need, and never Owen's.
d. A row that is ``needs_reconnect`` or ``disconnected`` meets nothing.
e. Ben, who does not own the package, gets 404. Zed of org B gets 404.
f. With ``STUDIO_CONNECTION_GATE_ORGS`` off for org A, the read is 404.
g. ``can_connect`` follows ``integrations:manage`` for an org connection.
h. The read changes no row.
i. A draft that names ``mailbox``, ``whatsapp`` or a key of the console
   (``apollo``) gets ``met`` False, ``checkable`` False and ``can_connect``
   False, for an admin too, because the registry cannot meet them
   (``OUTSIDE_REGISTRY``: I9 and I8e).
j. A draft that names the bare id ``mcp`` or an id off the list (``slack``)
   gets the same, because no writer writes them. A real ``mcp:<server>``
   stays checkable.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Awaitable, Callable
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth import UserContext, UserRole, build_access
from acb_common import get_settings
from acb_common.db import bind_tenant, release_tenant
from fastapi import HTTPException
from gateway.routes.studio import connections, packages
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

TAG = uuid.uuid4().hex[:8]
OWEN = f"owen.{TAG}@i8a.test"
BEN = f"ben.{TAG}@i8a.test"
ZED = f"zed.{TAG}@i8a.test"
_EMAIL_LIKE = f"%.{TAG}@i8a.test"
_SLUG_LIKE = f"i8a{TAG}-%"
_COUNTER = iter(range(10_000))
STUDIO = ("feature:studio",)
ADMIN = ("feature:studio", "integrations:manage")
#: The names of this run. An MCP server, and two provider ids that no other
#: suite writes.
TALLY = f"mcp:tally-{TAG}"
LEDGER = f"ledger_{TAG}"
DIARY = f"diary_{TAG}"
ROW_KEYS = {"name", "label", "met", "checkable", "setup_href", "can_connect"}


def _manifest(names: list[str]) -> dict[str, Any]:
    return {
        "studio": 1, "id": f"i8a{TAG}-app{next(_COUNTER)}", "name": "Stock",
        "purpose": "Track the stock of a site.", "connections": names,
        "objects": [{"name": "item", "fields": [{"key": "sku", "type": "text"}]}],
        "actions": [{"id": "find", "uses": "object:item.list"}],
    }


def _user(org: str, email: str, grants: tuple[str, ...] = STUDIO) -> UserContext:
    return UserContext(email=email, role=UserRole.EMPLOYEE,
                       access=build_access(list(grants)), organization_id=org)


class _Request:
    """The body stream that a package handler reads."""

    def __init__(self, payload: Any = None) -> None:
        self._raw = b"" if payload is None else json.dumps(payload).encode("utf-8")

    async def stream(self) -> Any:
        yield self._raw


async def _bound(org: str, fn: Callable[[], Awaitable[Any]]) -> tuple[int, Any]:
    token = bind_tenant(org)
    try:
        return 200, await fn()
    except HTTPException as exc:
        return exc.status_code, exc.detail
    finally:
        release_tenant(token)


async def _new(org: str, email: str, names: list[str]) -> str:
    status, got = await _bound(org, lambda: packages.create_package(
        request=_Request(_manifest(names)), user=_user(org, email)))
    assert status == 200, got
    return str(got["package"]["id"])


async def _read(org: str, email: str, pid: str,
                grants: tuple[str, ...] = STUDIO) -> tuple[int, Any]:
    return await _bound(org, lambda: connections.package_connections(
        package_id=pid, user=_user(org, email, grants)))


def _run(world: Any, fn: Callable[[], Awaitable[Any]]) -> Any:
    """Run ``fn()`` in a new event loop, on the app role's engine."""
    async def go() -> Any:
        async with tenant_engine_scope(world.p.app_url.render_as_string(hide_password=False)):
            return await fn()
    return asyncio.run(go())


@pytest.fixture
def world(promoted, monkeypatch):  # noqa: F811
    """Org A and org B, with Studio and the connection gate on for both. The
    teardown removes each package of this run and each registry row whose
    provider holds the tag."""
    p = promoted
    monkeypatch.setattr(get_settings(), "studio_orgs", "*")
    monkeypatch.setattr(get_settings(), "studio_connection_gate_orgs", f"{p.org_a},{p.org_b}")
    with p.admin_engine.begin() as c:
        for email, org in ((OWEN, p.org_a), (BEN, p.org_a), (ZED, p.org_b)):
            c.execute(text(
                "INSERT INTO app_user (email, display_name, role, status, organization_id) "
                "VALUES (:e, :e, 'employee', 'active', :org)"), {"e": email, "org": org})
    yield SimpleNamespace(p=p, a=p.org_a, b=p.org_b)
    with p.admin_engine.begin() as c:
        ids = [str(r[0]) for r in c.execute(text(
            "SELECT id FROM apps WHERE slug LIKE :t"), {"t": _SLUG_LIKE}).all()]
        for table, col in (("app_audit", "app_id"), ("studio_objects", "package_id"),
                           ("app_versions", "app_id")):
            c.execute(text(f"DELETE FROM {table} WHERE {col} = ANY(CAST(:ids AS uuid[]))"),
                      {"ids": ids})
        c.execute(text("DELETE FROM apps WHERE slug LIKE :t"), {"t": _SLUG_LIKE})
        c.execute(text("DELETE FROM integration_connections WHERE provider LIKE :p"),
                  {"p": f"%{TAG}%"})
        c.execute(text("DELETE FROM app_user WHERE email LIKE :e"), {"e": _EMAIL_LIKE})


def _row(world: Any, provider: str, org: str, *, member: str | None = None,
         status: str = "connected") -> str:
    """One registry row, as the superuser. No blob, so no credential."""
    with world.p.admin_engine.begin() as c:
        return str(c.execute(text(
            "INSERT INTO integration_connections (organization_id, provider, owning_app, "
            "scope_kind, member_email, status) VALUES (CAST(:o AS uuid), :p, 'integrations', "
            ":k, :m, :s) RETURNING id"),
            {"o": org, "p": provider, "k": "member" if member else "org",
             "m": member.lower() if member else None, "s": status}).scalar_one())


def _met(answer: dict[str, Any]) -> dict[str, bool]:
    return {r["name"]: r["met"] for r in answer["connections"]}


def _snapshot(world: Any) -> list[Any]:
    """Each registry row of this run and each package row, with ``xmin``."""
    with world.p.admin_engine.connect() as c:
        regs = c.execute(text(
            "SELECT id::text, status, xmin::text FROM integration_connections "
            "WHERE provider LIKE :p ORDER BY id"), {"p": f"%{TAG}%"}).all()
        apps = c.execute(text(
            "SELECT id::text, updated_at, xmin::text FROM apps WHERE slug LIKE :t ORDER BY id"),
            {"t": _SLUG_LIKE}).all()
    return [tuple(r) for r in regs] + [tuple(r) for r in apps]


# ── a. An org row meets the need of its own org ───────────────────────────


def test_r8_a_an_org_row_meets_the_need_and_the_rows_keep_the_draft_order(world) -> None:
    _row(world, TALLY, world.a)
    pid = _run(world, lambda: _new(world.a, OWEN, [LEDGER, TALLY]))
    status, got = _run(world, lambda: _read(world.a, OWEN, pid))
    assert status == 200, got
    assert [r["name"] for r in got["connections"]] == [LEDGER, TALLY]
    assert all(set(r) == ROW_KEYS for r in got["connections"])
    assert _met(got) == {LEDGER: False, TALLY: True}
    tally = got["connections"][1]
    assert tally["label"] == f"tally-{TAG}"
    assert tally["setup_href"] == f"/integrations?add=mcp&name=tally-{TAG}"


# ── b. Org A never meets a need of org B ──────────────────────────────────


def test_r8_b_a_row_of_one_org_never_meets_a_need_of_the_other(world) -> None:
    _row(world, TALLY, world.a)
    _row(world, LEDGER, world.b)
    zed = _run(world, lambda: _new(world.b, ZED, [TALLY, LEDGER]))
    owen = _run(world, lambda: _new(world.a, OWEN, [TALLY, LEDGER]))
    status, got = _run(world, lambda: _read(world.b, ZED, zed))
    assert status == 200, got
    assert _met(got) == {TALLY: False, LEDGER: True}
    status, got = _run(world, lambda: _read(world.a, OWEN, owen))
    assert status == 200, got
    assert _met(got) == {TALLY: True, LEDGER: False}


# ── c. The member row of X never counts for Y ─────────────────────────────


def test_r8_c_a_member_row_meets_the_need_of_its_own_member_only(world) -> None:
    _row(world, DIARY, world.a, member=BEN)
    owen = _run(world, lambda: _new(world.a, OWEN, [DIARY]))
    ben = _run(world, lambda: _new(world.a, BEN, [DIARY]))
    assert _met(_run(world, lambda: _read(world.a, OWEN, owen))[1]) == {DIARY: False}
    assert _met(_run(world, lambda: _read(world.a, BEN, ben))[1]) == {DIARY: True}
    # An admin of the org does not borrow Ben's row either.
    status, got = _run(world, lambda: _read(world.a, OWEN, owen, ADMIN))
    assert (status, _met(got)) == (200, {DIARY: False})


# ── d. A broken row meets nothing ─────────────────────────────────────────


@pytest.mark.parametrize("state", ["needs_reconnect", "disconnected"])
def test_r8_d_a_row_that_is_not_connected_meets_nothing(world, state: str) -> None:
    _row(world, TALLY, world.a, status=state)
    pid = _run(world, lambda: _new(world.a, OWEN, [TALLY]))
    assert _met(_run(world, lambda: _read(world.a, OWEN, pid))[1]) == {TALLY: False}


# ── e. The owner only ─────────────────────────────────────────────────────


def test_r8_e_another_member_and_another_org_get_404(world) -> None:
    _row(world, TALLY, world.a)
    pid = _run(world, lambda: _new(world.a, OWEN, [TALLY]))
    assert _run(world, lambda: _read(world.a, BEN, pid)) == (404, "Not found")
    assert _run(world, lambda: _read(world.b, ZED, pid)) == (404, "Not found")
    assert _run(world, lambda: _read(world.a, BEN, str(uuid.uuid4()))) == (404, "Not found")


# ── f. The gate off ───────────────────────────────────────────────────────


def test_r8_f_the_gate_off_for_the_org_answers_404(world, monkeypatch) -> None:
    _row(world, TALLY, world.a)
    pid = _run(world, lambda: _new(world.a, OWEN, [TALLY]))
    monkeypatch.setattr(get_settings(), "studio_connection_gate_orgs", world.b)
    assert _run(world, lambda: _read(world.a, OWEN, pid)) == (404, "Not found")
    monkeypatch.setattr(get_settings(), "studio_connection_gate_orgs", world.a)
    assert _run(world, lambda: _read(world.a, OWEN, pid))[0] == 200


# ── g. can_connect ────────────────────────────────────────────────────────


def test_r8_g_can_connect_follows_the_right_for_an_org_connection(world) -> None:
    pid = _run(world, lambda: _new(world.a, OWEN, [TALLY, "google_calendar"]))
    status, member = _run(world, lambda: _read(world.a, OWEN, pid))
    assert status == 200, member
    assert [r["can_connect"] for r in member["connections"]] == [False, True]
    status, admin = _run(world, lambda: _read(world.a, OWEN, pid, ADMIN))
    assert status == 200, admin
    assert [r["can_connect"] for r in admin["connections"]] == [True, True]
    assert admin["connections"][1]["setup_href"] == "/calendar?settings=1"
    assert admin["connections"][1]["label"] == "Google Calendar"


# ── h. The read writes nothing ────────────────────────────────────────────


def test_r8_h_the_read_changes_no_row(world) -> None:
    _row(world, TALLY, world.a)
    _row(world, DIARY, world.a, member=BEN)
    pid = _run(world, lambda: _new(world.a, OWEN, [TALLY, DIARY]))
    before = _snapshot(world)
    for _ in range(2):
        assert _run(world, lambda: _read(world.a, OWEN, pid))[0] == 200
    assert _snapshot(world) == before


# ── i. A provider outside the registry ────────────────────────────────────


def test_r8_i_a_name_outside_the_registry_is_not_checkable_and_offers_no_connect(world) -> None:
    _row(world, TALLY, world.a)
    pid = _run(world, lambda: _new(world.a, OWEN, ["mailbox", "whatsapp", "apollo", TALLY]))
    for grants in (STUDIO, ADMIN):
        status, got = _run(world, lambda g=grants: _read(world.a, OWEN, pid, g))
        assert status == 200, got
        assert [(r["name"], r["met"], r["checkable"], r["can_connect"])
                for r in got["connections"]] == [
            ("mailbox", False, False, False),
            ("whatsapp", False, False, False),
            ("apollo", False, False, False),
            (TALLY, True, True, grants == ADMIN),
        ], grants


# ── j. The bare id mcp, and an id off the list ─────────────────────────────


def test_r8_j_a_bare_mcp_and_an_id_off_the_list_are_blocked(world) -> None:
    _row(world, TALLY, world.a)
    pid = _run(world, lambda: _new(world.a, OWEN, ["mcp", "slack", TALLY]))
    for grants in (STUDIO, ADMIN):
        status, got = _run(world, lambda g=grants: _read(world.a, OWEN, pid, g))
        assert status == 200, got
        assert [(r["name"], r["met"], r["checkable"], r["can_connect"])
                for r in got["connections"]] == [
            ("mcp", False, False, False),
            ("slack", False, False, False),
            (TALLY, True, True, grants == ADMIN),
        ], grants
