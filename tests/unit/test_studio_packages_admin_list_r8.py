"""The admin view of the packages of an org on a real database (STU-J4, R8).

Spec: ``project-docs/specs/ai_studio.md`` §14 J4. Code:
``gateway/studio/packages_admin_list.py`` behind ``GET /studio/packages?scope=org``
in ``gateway/routes/studio/packages.py``. The hermetic half is
``test_studio_packages_admin_list.py``.

Each person here holds a REAL system role of an org, seeded by the real
``provision_org_roles``. The real ``resolve_access`` reads the grants as the
non-privileged app role with the org bound, so FORCE RLS binds. The resolved
access goes into the REAL Studio router over an ASGI transport. No access is
built by hand.

Run (a PRIVATE ladder database, one R8 suite at a time, because the
``promoted`` fixture drops ``<db>_h3rls``)::

    docker exec <your container> createdb -U acb <private db>
    export TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:<port>/<private db>
    uv run pytest tests/unit/test_studio_packages_admin_list_r8.py -v -rs

Without the database every test here SKIPS, and the run reads green. A skip
is not a pass.

**The five checks of J4.**

1. A holder of the admin right gets EVERY package of the bound org, with its
   owner, its state, its live version, its action calls and its last error.
2. A member gets only their own packages.
3. The package of another org never appears, for a holder or a member.
4. The answer holds no manifest, no reason text and no value.
5. A package with no audit row shows honest empty fields.

**The mutations (R7).** Each ran on 2026-11-11, and each test named went red.

* M1: ``holds_admin_right`` answers ``True``. Red:
  ``test_a_member_gets_only_their_own_packages``.
* M2: drop the ``LATERAL`` filter on the outcome in ``_SQL``. Red:
  ``test_the_last_error_is_the_newest_failed_or_refused_action_call``.
* M3: add ``x.detail->>'reason'`` to the answer. Red:
  ``test_the_answer_holds_no_manifest_reason_or_value``.
* M4: drop ``organization_id`` from the ``WHERE`` of ``_SQL`` (RLS still
  holds, so the test reads the statement too). Red: the hermetic
  ``test_the_statement_is_one_bounded_set_read_that_never_selects_a_secret``.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Awaitable, Callable
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

pytest.importorskip("sqlalchemy")

from acb_auth import UserContext, UserRole, get_current_user
from acb_common import get_settings
from acb_common.db import bind_tenant, release_tenant, tenant_session
from fastapi import FastAPI, Request
from gateway.routes.studio import router as studio_router
from gateway.studio import packages_admin_list as admin_list
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

TAG = uuid.uuid4().hex[:8]
SECRET = f"SECRET-{TAG}"
REASON = f"reason-text-{TAG}"

#: The people of org A and org B: a key, the org, and the system role.
PEOPLE: dict[str, tuple[str, str]] = {
    "admin_a": ("a", "admin"),
    "owner_a": ("a", "owner"),
    "ana": ("a", "member"),
    "ben": ("a", "member"),
    "manager_a": ("a", "manager"),
    "admin_b": ("b", "admin"),
}


def _email(key: str) -> str:
    return f"{key}.{TAG}@stuj4.test"


@pytest.fixture(scope="module")
def world(promoted):  # noqa: F811 — a fixture
    orgs = {"a": promoted.org_a, "b": promoted.org_b}
    with promoted.admin_engine.begin() as c:
        roles: dict[str, dict[str, str]] = {}
        for key, org in orgs.items():
            c.execute(text("SELECT set_config('app.tenant_id', :o, true)"), {"o": org})
            c.execute(text("SELECT provision_org_roles(:o)"), {"o": org})
            roles[key] = {str(s): str(i) for s, i in c.execute(text(
                "SELECT slug, id FROM org_role WHERE organization_id = CAST(:o AS uuid)"),
                {"o": org}).all()}
        for key, (o, role) in PEOPLE.items():
            uid = c.execute(text(
                "INSERT INTO app_user (email, display_name, role, status, organization_id) "
                "VALUES (:e, :e, 'employee', 'active', CAST(:o AS uuid)) RETURNING id"),
                {"e": _email(key), "o": orgs[o]}).scalar()
            c.execute(text(
                "INSERT INTO user_role (user_id, role_id, organization_id) "
                "VALUES (:u, CAST(:r AS uuid), CAST(:o AS uuid))"),
                {"u": uid, "r": roles[o][role], "o": orgs[o]})
    yield SimpleNamespace(p=promoted, orgs=orgs)
    with promoted.admin_engine.begin() as c:
        c.execute(text("DELETE FROM app_audit WHERE user_email LIKE :t"), {"t": f"%.{TAG}@stuj4.test"})
        c.execute(text("DELETE FROM apps WHERE slug LIKE :t"), {"t": f"j4{TAG}-%"})
        c.execute(text("DELETE FROM app_user WHERE email LIKE :t"), {"t": f"%.{TAG}@stuj4.test"})


@pytest.fixture(autouse=True)
def _resolver(monkeypatch: pytest.MonkeyPatch) -> None:
    import acb_auth.access as access_mod

    monkeypatch.setenv("IDENTITY_CUTOVER", "1")
    monkeypatch.delenv("ACCESS_LEGACY_FALLBACK", raising=False)
    monkeypatch.setattr(access_mod, "_tables_missing", False)
    monkeypatch.setattr(get_settings(), "studio_orgs", "*")
    access_mod.invalidate()


def _app(users: dict[str, UserContext]) -> FastAPI:
    app = FastAPI()
    app.include_router(studio_router)

    async def _caller(request: Request) -> UserContext:
        user = users[request.headers["x-test-as"]]
        bind_tenant(user.organization_id)
        return user

    app.dependency_overrides[get_current_user] = _caller
    return app


def _run(world: Any, fn: Callable[[Any], Awaitable[Any]]) -> Any:
    """Run ``fn(call)`` on the app role's engine. ``call(key, path)`` resolves
    the person through the real ``resolve_access``, then asks the real router."""
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
                org = world.orgs[PEOPLE[key][0]]
                token = bind_tenant(org)
                try:
                    access = await access_mod.resolve_access(_email(key), use_cache=False)
                finally:
                    release_tenant(token)
                return UserContext(email=_email(key), role=UserRole.EMPLOYEE,
                                   access=access, organization_id=org)

            async def call(key: str, path: str) -> httpx.Response:
                users[key] = await resolve(key)
                token = bind_tenant(world.orgs[PEOPLE[key][0]])
                try:
                    return await client.get(path, headers={"x-test-as": key})
                finally:
                    release_tenant(token)

            call.resolve = resolve  # type: ignore[attr-defined]
            return await fn(call)

    return asyncio.run(go())


# ── Seeding (the superuser, so RLS hides nothing) ──────────────────────────


def _package(world: Any, org: str, owner: str, name: str, *, live: int | None = None) -> str:
    """One manifest package. The manifest holds a SECRET, so a leak shows."""
    slug = f"j4{TAG}-{name}-{uuid.uuid4().hex[:4]}"
    manifest = {"studio": 1, "id": slug, "name": name, "purpose": SECRET, "objects": [], "actions": []}
    with world.p.admin_engine.begin() as c:
        return str(c.execute(text(
            "INSERT INTO apps (slug, name, description, owner_email, visibility, status, "
            "manifest, workspace_path, format, live_version, organization_id) "
            "VALUES (:s, :n, '', :o, 'private', :st, CAST(:m AS jsonb), '', 'manifest', :v, "
            "CAST(:org AS uuid)) RETURNING id"),
            {"s": slug, "n": f"{name}-{TAG}", "o": _email(owner),
             "st": "draft" if live is None else "live", "m": json.dumps(manifest),
             "v": live, "org": org}).scalar_one())


def _audit(world: Any, org: str, pid: str, kind: str, outcome: str | None,
           action: str = "close", at: str = "now()") -> None:
    detail: dict[str, Any] = {"action": action, "reason": REASON}
    if outcome is not None:
        detail["outcome"] = outcome
    with world.p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO app_audit (app_id, user_email, kind, detail, at, organization_id) "
            f"VALUES (CAST(:p AS uuid), 'x@stuj4.test', :k, CAST(:d AS jsonb), {at}, "
            "CAST(:o AS uuid))"),
            {"p": pid, "k": kind, "d": json.dumps(detail), "o": org})


@pytest.fixture(scope="module")
def pkgs(world) -> SimpleNamespace:
    a, b = world.orgs["a"], world.orgs["b"]
    p = SimpleNamespace(
        draft=_package(world, a, "ana", "draft"),
        live=_package(world, a, "ben", "live", live=3),
        ben_draft=_package(world, a, "ben", "bendraft"),
        other=_package(world, b, "admin_b", "otherorg", live=1),
    )
    # The live package: 2 ran, 1 failed (old), 1 refused (newest), and noise
    # that must not count as an error: a failed `storage` row and a `ran` row.
    for outcome, when in (("ran", "now() - interval '5 hours'"),
                          ("ran", "now() - interval '4 hours'"),
                          ("failed", "now() - interval '3 hours'"),
                          ("refused", "now() - interval '2 hours'")):
        _audit(world, a, p.live, "action", outcome, "close", when)
    _audit(world, a, p.live, "storage", "failed", "storage_noise", "now() - interval '1 hour'")
    _audit(world, a, p.live, "action", "ran", "ran_newest", "now()")
    # An action call of another org on its own package. It must not count anywhere.
    _audit(world, b, p.other, "action", "failed", "other_org_action")
    return p


def _ids(body: dict[str, Any]) -> list[str]:
    return [str(r["id"]) for r in body["packages"]]


# ── 1. The holder ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("key", ["admin_a", "owner_a"])
def test_a_holder_gets_every_package_of_the_org(world, pkgs, key: str) -> None:
    async def fn(call: Any) -> Any:
        user = await call.resolve(key)
        r = await call(key, "/studio/packages?scope=org")
        return user.has_permission("admin:members:manage"), r

    right, r = _run(world, fn)
    assert right is True
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["admin"] is True and body["truncated"] is False
    mine = {x for x in _ids(body)} & {pkgs.draft, pkgs.live, pkgs.ben_draft, pkgs.other}
    assert mine == {pkgs.draft, pkgs.live, pkgs.ben_draft}
    rows = {r_["id"]: r_ for r_ in body["packages"]}
    assert rows[pkgs.draft]["owner_email"] == _email("ana")
    assert rows[pkgs.live]["owner_email"] == _email("ben")
    assert rows[pkgs.live]["state"] == "live" and rows[pkgs.live]["live_version"] == 3
    assert rows[pkgs.draft]["state"] == "draft" and rows[pkgs.draft]["live_version"] is None
    # Newest change first.
    stamps = [r_["updated_at"] for r_ in body["packages"]]
    assert stamps == sorted(stamps, reverse=True)


def test_the_last_error_is_the_newest_failed_or_refused_action_call(world, pkgs) -> None:
    async def fn(call: Any) -> Any:
        return await call("admin_a", "/studio/packages?scope=org")

    row = {r["id"]: r for r in _run(world, fn).json()["packages"]}[pkgs.live]
    # 5 action rows: 3 ran, 1 failed, 1 refused. The `storage` row is not an action call.
    assert row["action_calls"] == 5, row
    assert row["action_calls_capped"] is False
    assert row["last_error"]["outcome"] == "refused"
    assert row["last_error"]["action"] == "close"
    assert set(row["last_error"]) == {"action", "outcome", "at"}
    assert row["use"] is None


# ── 2. The member ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("key,expect", [("ana", "draft"), ("ben", "live"), ("manager_a", None)])
def test_a_member_gets_only_their_own_packages(world, pkgs, key: str, expect: str | None) -> None:
    async def fn(call: Any) -> Any:
        user = await call.resolve(key)
        return user.has_permission("admin:members:manage"), await call(key, "/studio/packages?scope=org")

    right, r = _run(world, fn)
    assert right is False
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["admin"] is False
    assert "truncated" not in body
    ids = set(_ids(body))
    owned = {"draft": {pkgs.draft}, "live": {pkgs.live, pkgs.ben_draft}, None: set()}[expect]
    assert ids & {pkgs.draft, pkgs.live, pkgs.ben_draft, pkgs.other} == owned
    assert all("owner_email" not in x and "action_calls" not in x for x in body["packages"])


def test_the_default_list_is_the_member_list_even_for_a_holder(world, pkgs) -> None:
    async def fn(call: Any) -> Any:
        return await call("admin_a", "/studio/packages")

    r = _run(world, fn)
    assert r.status_code == 200
    assert set(r.json()) == {"packages"}
    assert not ({pkgs.draft, pkgs.live, pkgs.ben_draft} & set(_ids(r.json())))


# ── 3. Another org ─────────────────────────────────────────────────────────


def test_another_orgs_package_never_appears(world, pkgs) -> None:
    async def fn(call: Any) -> Any:
        return (await call("admin_a", "/studio/packages?scope=org"),
                await call("admin_b", "/studio/packages?scope=org"))

    a, b = _run(world, fn)
    assert pkgs.other not in _ids(a.json())
    assert _email("admin_b") not in a.text
    mine_b = {x for x in _ids(b.json())} & {pkgs.draft, pkgs.live, pkgs.ben_draft, pkgs.other}
    assert mine_b == {pkgs.other}
    row = {r["id"]: r for r in b.json()["packages"]}[pkgs.other]
    # The audit row of org B counts for org B, and the rows of org A never reach it.
    assert row["action_calls"] == 1
    assert "other_org_action" in b.text


# ── 4. No manifest, no reason, no value ────────────────────────────────────


def test_the_answer_holds_no_manifest_reason_or_value(world, pkgs) -> None:
    async def fn(call: Any) -> Any:
        return (await call("admin_a", "/studio/packages?scope=org"),
                await call("ana", "/studio/packages?scope=org"))

    for r in _run(world, fn):
        assert SECRET not in r.text
        assert REASON not in r.text
        assert "manifest\"" not in r.text.replace("manifest_id", "")
        assert "reason" not in r.text


# ── 5. Honest empty fields ─────────────────────────────────────────────────


def test_a_package_with_no_audit_row_shows_honest_empty_fields(world, pkgs) -> None:
    async def fn(call: Any) -> Any:
        return await call("admin_a", "/studio/packages?scope=org")

    row = {r["id"]: r for r in _run(world, fn).json()["packages"]}[pkgs.draft]
    assert row["action_calls"] == 0
    assert row["last_error"] is None
    assert row["use"] is None
    assert row["live_version"] is None


# ── The bound ──────────────────────────────────────────────────────────────


def test_the_list_stops_at_its_cap_and_says_so(world, pkgs, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(admin_list, "MAX_PACKAGES", 2)

    async def fn(call: Any) -> Any:
        return await call("admin_a", "/studio/packages?scope=org")

    body = _run(world, fn).json()
    assert len(body["packages"]) == 2 and body["truncated"] is True


def test_the_count_stops_at_its_cap(world, pkgs, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(admin_list, "MAX_COUNT", 2)

    async def fn(call: Any) -> Any:
        return await call("admin_a", "/studio/packages?scope=org")

    row = {r["id"]: r for r in _run(world, fn).json()["packages"]}[pkgs.live]
    assert row["action_calls"] == 2 and row["action_calls_capped"] is True


# ── The seam ───────────────────────────────────────────────────────────────


def test_the_tables_are_forced_and_the_role_has_no_bypass(world, pkgs) -> None:
    with world.p.admin_engine.connect() as c:
        forced = {str(r[0]): bool(r[1]) for r in c.execute(text(
            "SELECT relname, relforcerowsecurity FROM pg_class "
            "WHERE relname IN ('apps', 'app_audit')")).all()}
    assert forced == {"apps": True, "app_audit": True}

    async def fn(_call: Any) -> Any:
        token = bind_tenant(world.orgs["a"])
        try:
            async with tenant_session() as db:
                return (await db.execute(text(
                    "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"))).one()
        finally:
            release_tenant(token)

    sup, bypass = _run(world, fn)
    assert (sup, bypass) == (False, False)
