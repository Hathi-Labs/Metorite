"""The gate of every AI Studio route on a real database (STU-J1-1, R8).

Spec: ``project-docs/specs/ai_studio.md`` §9 and §14 J1, and the owner's
answers STU-Q4 and STU-Q4b (§12). Code: ``require_feature_router("studio")``
on the one router of ``gateway/routes/studio/actions.py``, the vocabulary of
``acb_auth/permissions.py`` and the catalog row of
``infra/postgres/346_studio_feature.sql``. The hermetic half is
``test_studio_route_gate.py``.

Each member here holds a REAL system role of org A, seeded by the real
``provision_org_roles`` (migrations 200 and 207). The real ``resolve_access``
reads the role grants and the overrides on a phase-4 promoted scratch
database, as the non-privileged app role, with the org bound, so FORCE RLS
binds. The resolved access goes into the REAL Studio router over an ASGI
transport. No access is built by hand.

Run (a PRIVATE ladder database, one R8 suite at a time, because the
``promoted`` fixture drops ``<db>_h3rls``)::

    docker exec metorite-scratch-tenant createdb -U acb <private db>
    export TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5434/<private db>
    uv run pytest tests/unit/test_studio_route_gate_r8.py -v -rs

Without the database every test here SKIPS, and the run reads green. A skip
is not a pass.

**Acceptance (J1-1).**

a. A member, a manager, an admin and the owner hold ``feature:studio`` with
   no new row: ``feature:*`` (130, 207) or ``*``. Each gets 200 on
   ``GET /studio/actions``, ``GET /studio/skills`` and
   ``GET /studio/packages`` with the org in ``STUDIO_ORGS``.
b. A guest gets 403 on one route of each file: the catalog, the skills, the
   packages and the action door.
c. A member with an override that denies ``feature:studio`` gets 403.
d. A member with an override that allows ``feature:workflows`` and an
   override that denies ``feature:studio`` gets 403. So the interim gate of
   B1 is gone.
e. Only the owner holds ``studio:publish`` by default. A member with an
   override that allows it resolves it true through ``has_permission``.
f. The ``studio`` row of ``feature_catalog`` exists, with ``is_default``
   true. No role of the org holds a ``feature:studio`` or a
   ``studio:publish`` row.

**The mutations (R7).** Each ran on 2026-11-05.

* M1: in ``actions.py``, restore ``require_feature_router("workflows")``.
  Red: ``test_a_member_who_holds_workflows_but_not_studio_gets_403`` and
  ``test_a_member_with_a_deny_override_gets_403``.
* M4: remove ``"studio"`` from ``FEATURES`` and ``"studio:publish"`` from
  ``CAPABILITIES``. ``build_access`` drops nothing, so the gate still
  holds. Red: ``test_the_catalog_row_is_a_default_and_no_role_holds_a_studio_row``
  and the vocabulary pins of the hermetic half.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

pytest.importorskip("sqlalchemy")

from acb_auth import UserContext, UserRole, get_current_user
from acb_common import get_settings
from acb_common.db import bind_tenant, release_tenant
from fastapi import FastAPI, Request
from gateway.routes.studio import router as studio_router
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

TAG = uuid.uuid4().hex[:8]

#: The people of org A: a key, their system role, and their overrides.
PEOPLE: dict[str, tuple[str, tuple[tuple[str, str], ...]]] = {
    "owner": ("owner", ()),
    "admin": ("admin", ()),
    "manager": ("manager", ()),
    "member": ("member", ()),
    "guest": ("guest", ()),
    "denied": ("member", (("feature:studio", "deny"),)),
    "workflows_only": ("member", (("feature:workflows", "allow"), ("feature:studio", "deny"))),
    "publisher": ("member", (("studio:publish", "allow"),)),
}

#: One route of each file of ``routes/studio``.
ONE_OF_EACH_FILE = (
    ("GET", "/studio/actions"),
    ("GET", "/studio/skills"),
    ("GET", "/studio/packages"),
    ("POST", f"/studio/apps/{uuid.uuid4()}/actions/peek"),
)


def _email(key: str) -> str:
    return f"{key}.{TAG}@stuj1.test"


@pytest.fixture(scope="module")
def world(promoted):  # noqa: F811 — a fixture
    a = promoted.org_a
    with promoted.admin_engine.begin() as c:
        c.execute(text("SELECT set_config('app.tenant_id', :o, true)"), {"o": a})
        c.execute(text("SELECT provision_org_roles(:o)"), {"o": a})
        roles = {str(slug): str(rid) for slug, rid in c.execute(text(
            "SELECT slug, id FROM org_role WHERE organization_id = CAST(:o AS uuid)"),
            {"o": a}).all()}
        for key, (role, overrides) in PEOPLE.items():
            uid = c.execute(text(
                "INSERT INTO app_user (email, display_name, role, status, organization_id) "
                "VALUES (:e, :e, 'employee', 'active', CAST(:o AS uuid)) RETURNING id"),
                {"e": _email(key), "o": a}).scalar()
            c.execute(text(
                "INSERT INTO user_role (user_id, role_id, organization_id) "
                "VALUES (:u, CAST(:r AS uuid), CAST(:o AS uuid))"),
                {"u": uid, "r": roles[role], "o": a})
            for permission, effect in overrides:
                c.execute(text(
                    "INSERT INTO user_permission_override (user_id, permission, effect, "
                    "reason, set_by, organization_id) VALUES (:u, :p, :x, 'J1 R8', "
                    "'seed@stuj1.test', CAST(:o AS uuid))"),
                    {"u": uid, "p": permission, "x": effect, "o": a})
    yield SimpleNamespace(p=promoted, a=a, roles=roles)
    with promoted.admin_engine.begin() as c:
        c.execute(text("DELETE FROM app_user WHERE email LIKE :t"), {"t": f"%.{TAG}@stuj1.test"})


@pytest.fixture(autouse=True)
def _resolver(monkeypatch: pytest.MonkeyPatch) -> None:
    """The role leg reads ``app_user`` on a bound session (the cutover path),
    and an earlier suite cannot leave the resolver degraded."""
    import acb_auth.access as access_mod

    monkeypatch.setenv("IDENTITY_CUTOVER", "1")
    monkeypatch.delenv("ACCESS_LEGACY_FALLBACK", raising=False)
    monkeypatch.setattr(access_mod, "_tables_missing", False)
    monkeypatch.setattr(get_settings(), "studio_orgs", "*")
    access_mod.invalidate()


def _app(users: dict[str, UserContext]) -> FastAPI:
    """The REAL Studio router. The caller is the resolved member that the
    test header names, and the dependency binds the org, as the auth layer
    does."""
    app = FastAPI()
    app.include_router(studio_router)

    async def _caller(request: Request) -> UserContext:
        user = users[request.headers["x-test-as"]]
        bind_tenant(user.organization_id)
        return user

    app.dependency_overrides[get_current_user] = _caller
    return app


def _run(world: Any, fn: Callable[[Any], Awaitable[Any]]) -> Any:
    """Run ``fn(call)`` on the app role's engine. ``call(key, method, path)``
    resolves the member of *key* through the real ``resolve_access``, then
    sends the request through the real router."""
    import acb_auth.access as access_mod

    async def go() -> Any:
        users: dict[str, UserContext] = {}
        dsn = world.p.app_url.render_as_string(hide_password=False)
        async with (
            tenant_engine_scope(dsn),
            httpx.AsyncClient(transport=httpx.ASGITransport(app=_app(users)),
                              base_url="http://studio.test") as client,
        ):
            async def resolve(key: str) -> UserContext:
                token = bind_tenant(world.a)
                try:
                    access = await access_mod.resolve_access(_email(key), use_cache=False)
                finally:
                    release_tenant(token)
                return UserContext(email=_email(key), role=UserRole.EMPLOYEE,
                                   access=access, organization_id=world.a)

            async def call(key: str, method: str, path: str) -> httpx.Response:
                users[key] = await resolve(key)
                body = {} if method in ("POST", "PUT", "PATCH") else None
                token = bind_tenant(world.a)
                try:
                    return await client.request(method, path, json=body,
                                                headers={"x-test-as": key})
                finally:
                    release_tenant(token)

            call.resolve = resolve  # type: ignore[attr-defined]
            return await fn(call)

    return asyncio.run(go())


# ── a. The four roles that hold the feature ───────────────────────────────


@pytest.mark.parametrize("key", ["member", "manager", "admin", "owner"])
def test_each_role_but_guest_holds_studio_with_no_new_row(world, key: str) -> None:
    async def fn(call: Any) -> dict[str, Any]:
        user = await call.resolve(key)
        out: dict[str, Any] = {"active": user.access.is_active,
                               "studio": user.has_permission("feature:studio")}
        for method, path in ONE_OF_EACH_FILE[:3]:
            out[path] = (await call(key, method, path)).status_code
        return out

    got = _run(world, fn)
    assert got["active"] is True, got
    assert got["studio"] is True, got
    assert got["/studio/actions"] == 200, got
    assert got["/studio/skills"] == 200, got
    assert got["/studio/packages"] == 200, got


# ── b, c, d. The members without the feature ─────────────────────────────


def _statuses(world: Any, key: str) -> tuple[bool, dict[str, int]]:
    async def fn(call: Any) -> tuple[bool, dict[str, int]]:
        user = await call.resolve(key)
        codes = {p: (await call(key, m, p)).status_code for m, p in ONE_OF_EACH_FILE}
        return user.access.is_active, codes

    return _run(world, fn)


def test_a_guest_gets_403_on_one_route_of_each_file(world) -> None:
    active, codes = _statuses(world, "guest")
    assert active is True
    assert set(codes.values()) == {403}, codes


def test_a_member_with_a_deny_override_gets_403(world) -> None:
    active, codes = _statuses(world, "denied")
    assert active is True
    assert set(codes.values()) == {403}, codes


def test_a_member_who_holds_workflows_but_not_studio_gets_403(world) -> None:
    """Red first: with the gate of B1 restored (``workflows``), this member
    passes it and gets 200 on the catalog."""
    async def fn(call: Any) -> Any:
        return (await call.resolve("workflows_only")).access

    access = _run(world, fn)
    assert access.has("feature:workflows") and not access.has("feature:studio")
    _, codes = _statuses(world, "workflows_only")
    assert set(codes.values()) == {403}, codes


# ── e. The share verb ─────────────────────────────────────────────────────


def test_only_the_owner_holds_the_share_verb_by_default(world) -> None:
    async def fn(call: Any) -> dict[str, bool]:
        return {k: (await call.resolve(k)).has_permission("studio:publish")
                for k in ("owner", "admin", "manager", "member", "guest", "publisher")}

    held = _run(world, fn)
    assert held == {"owner": True, "admin": False, "manager": False, "member": False,
                    "guest": False, "publisher": True}, held


# ── f. The catalog row, and no role row ───────────────────────────────────


def test_the_catalog_row_is_a_default_and_no_role_holds_a_studio_row(world) -> None:
    with world.p.admin_engine.connect() as c:
        row = c.execute(text(
            "SELECT nav_href, category, sort_order, is_default FROM feature_catalog "
            "WHERE slug = 'studio'")).first()
        studio_rows = c.execute(text(
            "SELECT count(*) FROM org_role_permission p JOIN org_role r ON r.id = p.role_id "
            "WHERE r.organization_id = CAST(:o AS uuid) "
            "AND p.permission IN ('feature:studio', 'studio:publish')"), {"o": world.a}).scalar()
    assert row is not None
    assert tuple(row) == ("/studio", "apps", 96, True)
    assert studio_rows == 0
