"""R8 — the CRM access rule and the change to Native mode (WS-53 CRM-T5).

Migration 242 adds ``crm_settings``. ``routes/crm/access.py`` reads it on
each ``/crm`` request, and ``PATCH /crm/settings`` writes it and seeds the
pipeline (``routes/crm/seed.py``). Spec: ``crm_platform.md`` §3.1, §13.3 (the
CRM-T5 row) and §16 Q3.

What this proves against a REAL Postgres, through the real ``/crm`` router,
as the NON-privileged role of the phase-4 rehearsal (``acb_app_h3rls``: no
superuser, no BYPASSRLS), on a database with the whole ladder and two
organizations:

- done-when 2: with no row, or ``access = 'admins'``, a member gets 403 on
  each of the four entity lists, and an admin gets 200;
- done-when 3: with ``access = 'groups'`` and ``['group:sales']``, a member of
  ``sales`` gets 200 and a member outside it gets 403. The group read is
  ``routes/projects/core._MY_GROUPS_SQL`` under row level security;
- done-when 5: ``PATCH /crm/settings {"mode": "native"}`` by an admin gives
  the organization exactly 6 deal stages, 5 lead stages and 6 lost reasons.
  A second PATCH adds none, and the other organization's counts do not move.
  The ``ON CONFLICT`` arms infer the per-tenant keys of migration 241 only
  on a real catalog, so no fake can prove this;
- ``crm_settings`` forces row level security: a write for another tenant is
  refused, and an unbound session reads no row;
- migration 242 replays with no change (idempotent).

A hermetic fake agrees with any SQL it is handed, so none of these could be
proved without the database. ``tests/unit/test_crm_access.py`` holds the same
rules without one.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

# ⚠️ Every test here needs the real database. Without this gate the suite
# ERRORS where no ladder URL is set (the deploy run), and the deploy stops.
pytestmark = _DB_GATE

_MIGRATIONS = Path(__file__).resolve().parents[2] / "infra" / "postgres"

LISTS = ("/crm/leads", "/crm/deals", "/crm/contacts", "/crm/organizations")

_SEEDED = ("crm_deal_statuses", "crm_lead_statuses", "crm_lost_reasons")


def _settings_migration() -> Path:
    """The migration that creates ``crm_settings``, found by content (R1)."""
    found = [
        p for p in sorted(_MIGRATIONS.glob("*.sql"))
        if p.name != "schema.generated.sql"
        and "CREATE TABLE IF NOT EXISTS crm_settings" in p.read_text(encoding="utf-8")
    ]
    assert len(found) == 1, [p.name for p in found]
    return found[0]


def _app_dsn(ns: Any) -> str:
    return ns.app_url.render_as_string(hide_password=False)


def _assert_non_priv(app_eng: Any) -> None:
    with app_eng.connect() as c:
        role = c.execute(text(
            "SELECT rolsuper, rolbypassrls FROM pg_roles "
            "WHERE rolname = current_user")).first()
    assert role is not None and not role[0] and not role[1], (
        "this suite connects as a SUPERUSER/BYPASSRLS role, so RLS is bypassed"
    )


def _principal(email: str, org: str, *perms: str) -> Any:
    from acb_auth import UserContext, UserRole, build_access

    return UserContext(
        email=email, role=UserRole.EMPLOYEE, access=build_access(list(perms)),
        organization_id=org,
    )


def _admin(org: str) -> Any:
    return _principal("admin@crm-t5-r8.test", org, "feature:crm", "admin:access:manage")


def _member(email: str, org: str) -> Any:
    return _principal(email, org, "feature:crm")


async def _call(
    p: Any, user: Any, method: str, path: str, body: dict | None = None,
) -> Any:
    """One request through the real ``/crm`` router, as *user*.

    The override binds the tenant from the user, which is what
    ``_with_resolved_access`` does for a real request. The tenant never comes
    from the request.
    """
    import httpx
    from acb_auth import get_current_user
    from acb_common.db import bind_tenant, clear_tenant, release_tenant
    from fastapi import FastAPI
    from gateway.routes import crm as crm_package

    async def _caller() -> Any:
        bind_tenant(user.organization_id)
        return user

    app = FastAPI()
    app.include_router(crm_package.router)
    app.dependency_overrides[get_current_user] = _caller
    # The transport runs the app in this task, so the binding would outlive
    # the request. This scope ends it with the call.
    scope = clear_tenant()
    try:
        async with tenant_engine_scope(_app_dsn(p)):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://crm.test",
            ) as client:
                return await client.request(method, path, json=body)
    finally:
        release_tenant(scope)


async def _codes(p: Any, user: Any) -> dict[str, int]:
    return {path: (await _call(p, user, "GET", path)).status_code for path in LISTS}


def _counts(p: Any, org: str) -> tuple[int, int, int]:
    with p.admin_engine.connect() as c:
        return tuple(
            int(c.execute(text(
                f"SELECT count(*) FROM {table} "
                "WHERE organization_id = CAST(:o AS uuid)"), {"o": org}).scalar())
            for table in _SEEDED
        )


def _set_access(p: Any, org: str, access: str, groups: list[str]) -> None:
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO crm_settings (organization_id, access, access_groups) "
            "VALUES (CAST(:o AS uuid), :a, :g) "
            "ON CONFLICT (organization_id) DO UPDATE SET "
            "access = EXCLUDED.access, access_groups = EXCLUDED.access_groups"),
            {"o": org, "a": access, "g": groups})


@pytest.fixture
def ns(promoted, monkeypatch):  # noqa: F811 — a fixture
    """The promoted catalog with the CRM switch on, cleaned after each test."""
    from tests.unit._crm_fakes import crm_switch_on

    crm_switch_on(monkeypatch, "*")
    made: dict[str, list[str]] = {"users": [], "groups": []}
    promoted.made = made
    yield promoted
    with promoted.admin_engine.begin() as c:
        for org in (promoted.org_a, promoted.org_b):
            c.execute(text(
                "DELETE FROM crm_settings WHERE organization_id = CAST(:o AS uuid)"),
                {"o": org})
            for table in _SEEDED:
                c.execute(text(
                    f"DELETE FROM {table} WHERE organization_id = CAST(:o AS uuid)"),
                    {"o": org})
        for gid in made["groups"]:
            c.execute(text("DELETE FROM org_group WHERE id = CAST(:g AS uuid)"),
                      {"g": gid})
        for email in made["users"]:
            c.execute(text("DELETE FROM app_user WHERE lower(email) = :e"),
                      {"e": email.lower()})


def _add_member(p: Any, org: str, email: str) -> str:
    with p.admin_engine.begin() as c:
        uid = c.execute(text(
            "INSERT INTO app_user (email, display_name, role, status, "
            "organization_id) VALUES (:e, :e, 'employee', 'active', "
            "CAST(:o AS uuid)) RETURNING id"), {"e": email, "o": org}).scalar()
    p.made["users"].append(email)
    return str(uid)


def _add_group(p: Any, org: str, slug: str, user_ids: list[str]) -> None:
    with p.admin_engine.begin() as c:
        gid = str(c.execute(text(
            "INSERT INTO org_group (organization_id, slug, display_name) "
            "VALUES (CAST(:o AS uuid), :s, :s) RETURNING id"),
            {"o": org, "s": slug}).scalar())
        for uid in user_ids:
            c.execute(text(
                "INSERT INTO org_group_member (group_id, user_id, organization_id) "
                "VALUES (CAST(:g AS uuid), CAST(:u AS uuid), CAST(:o AS uuid))"),
                {"g": gid, "u": uid, "o": org})
    p.made["groups"].append(gid)


def _address(tag: str) -> str:
    return f"{tag}.{uuid.uuid4().hex[:8]}@crm-t5-r8.test"


# ── Done-when 2 ─────────────────────────────────────────────────────────────

async def test_with_no_row_a_member_gets_403_and_an_admin_200(ns, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    member = _member(_address("m"), ns.org_a)
    assert await _codes(ns, member) == {path: 403 for path in LISTS}
    assert await _codes(ns, _admin(ns.org_a)) == {path: 200 for path in LISTS}


async def test_with_access_admins_a_member_gets_403(ns, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    _set_access(ns, ns.org_a, "admins", [])
    member = _member(_address("m"), ns.org_a)
    assert await _codes(ns, member) == {path: 403 for path in LISTS}
    assert await _codes(ns, _admin(ns.org_a)) == {path: 200 for path in LISTS}


async def test_another_orgs_everyone_does_not_open_this_org(ns, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    _set_access(ns, ns.org_b, "everyone", [])
    member = _member(_address("m"), ns.org_a)
    assert await _codes(ns, member) == {path: 403 for path in LISTS}
    other = _member(_address("b"), ns.org_b)
    assert await _codes(ns, other) == {path: 200 for path in LISTS}


# ── Done-when 3 ─────────────────────────────────────────────────────────────

async def test_with_groups_sales_admits_its_member_only(ns, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    seller, outsider = _address("Seller"), _address("outsider")
    seller_id = _add_member(ns, ns.org_a, seller)
    outsider_id = _add_member(ns, ns.org_a, outsider)
    _add_group(ns, ns.org_a, "sales", [seller_id])
    _add_group(ns, ns.org_a, "support", [outsider_id])
    _set_access(ns, ns.org_a, "groups", ["group:sales"])

    assert await _codes(ns, _member(seller, ns.org_a)) == {p: 200 for p in LISTS}
    assert await _codes(ns, _member(outsider, ns.org_a)) == {p: 403 for p in LISTS}


async def test_a_sales_group_in_another_org_does_not_count(ns, app_engine) -> None:  # noqa: F811
    """The group read runs under row level security in the caller's tenant.
    A ``sales`` group of org B names nobody in org A."""
    _assert_non_priv(app_engine)
    seller = _address("seller")
    seller_id = _add_member(ns, ns.org_b, seller)
    _add_group(ns, ns.org_b, "sales", [seller_id])
    _set_access(ns, ns.org_a, "groups", ["group:sales"])
    # The same person, if they were bound to org A, finds no group there.
    assert await _codes(ns, _member(seller, ns.org_a)) == {p: 403 for p in LISTS}


# ── Done-when 4 ─────────────────────────────────────────────────────────────

async def test_with_everyone_a_feature_holder_gets_200(ns, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    _set_access(ns, ns.org_a, "everyone", [])
    member = _member(_address("m"), ns.org_a)
    assert await _codes(ns, member) == {path: 200 for path in LISTS}


# ── Done-when 5 and 6 ───────────────────────────────────────────────────────

async def test_native_seeds_the_org_once_and_no_other(ns, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    assert _counts(ns, ns.org_a) == (0, 0, 0), "org A is not a new org"
    before_b = _counts(ns, ns.org_b)

    first = await _call(ns, _admin(ns.org_a), "PATCH", "/crm/settings",
                        {"mode": "native"})
    assert first.status_code == 200, first.text
    assert first.json()["mode"] == "native"
    assert _counts(ns, ns.org_a) == (6, 5, 6)

    second = await _call(ns, _admin(ns.org_a), "PATCH", "/crm/settings",
                         {"mode": "native"})
    assert second.status_code == 200, second.text
    assert _counts(ns, ns.org_a) == (6, 5, 6)
    assert _counts(ns, ns.org_b) == before_b

    with ns.admin_engine.connect() as c:
        rows = c.execute(text(
            "SELECT organization_id::text, mode, updated_by FROM crm_settings "
            "WHERE organization_id IN (CAST(:a AS uuid), CAST(:b AS uuid))"),
            {"a": ns.org_a, "b": ns.org_b}).all()
    assert [tuple(r) for r in rows] == [(ns.org_a, "native", "admin@crm-t5-r8.test")]


async def test_the_seed_puts_no_deleted_stage_back(ns, app_engine) -> None:  # noqa: F811
    """The seed runs on the change of mode only. A later PATCH, also one
    that names ``native`` again, does not put a deleted stage back."""
    _assert_non_priv(app_engine)
    admin = _admin(ns.org_a)
    assert (await _call(ns, admin, "PATCH", "/crm/settings",
                        {"mode": "native"})).status_code == 200
    with ns.admin_engine.begin() as c:
        c.execute(text(
            "DELETE FROM crm_lost_reasons WHERE organization_id = CAST(:o AS uuid) "
            "AND label = 'Other'"), {"o": ns.org_a})
    assert (await _call(ns, admin, "PATCH", "/crm/settings",
                        {"mode": "native", "access": "everyone"})).status_code == 200
    assert _counts(ns, ns.org_a) == (6, 5, 5)


async def test_mirror_is_422_and_writes_nothing(ns, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    response = await _call(ns, _admin(ns.org_a), "PATCH", "/crm/settings",
                           {"mode": "mirror"})
    assert response.status_code == 422, response.text
    assert _counts(ns, ns.org_a) == (0, 0, 0)
    with ns.admin_engine.connect() as c:
        assert c.execute(text(
            "SELECT count(*) FROM crm_settings WHERE organization_id = CAST(:o AS uuid)"),
            {"o": ns.org_a}).scalar() == 0


async def test_a_member_gets_403_on_post_statuses_deal(ns, app_engine) -> None:  # noqa: F811
    """Done-when 1, on the real catalog: the floor holds also when the access
    rule admits the member."""
    _assert_non_priv(app_engine)
    _set_access(ns, ns.org_a, "everyone", [])
    member = _member(_address("m"), ns.org_a)
    refused = await _call(ns, member, "POST", "/crm/statuses/deal", {"name": "Pilot"})
    assert refused.status_code == 403
    made = await _call(ns, _admin(ns.org_a), "POST", "/crm/statuses/deal",
                       {"name": "Pilot"})
    assert made.status_code == 201, made.text
    assert _counts(ns, ns.org_a)[0] == 1


# ── The table itself ────────────────────────────────────────────────────────

def test_crm_settings_refuses_another_tenants_row(ns, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    with app_engine.connect() as c:
        trans = c.begin()
        c.execute(text("SELECT set_config('app.tenant_id', :o, true)"), {"o": ns.org_a})
        with pytest.raises(DBAPIError):
            c.execute(text(
                "INSERT INTO crm_settings (organization_id, access) "
                "VALUES (CAST(:o AS uuid), 'everyone')"), {"o": ns.org_b})
        trans.rollback()


def test_an_unbound_session_reads_no_settings_row(ns, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    _set_access(ns, ns.org_a, "everyone", [])
    with app_engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM crm_settings")).scalar() == 0


def test_the_column_default_is_the_bound_tenant(ns, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    with app_engine.connect() as c:
        trans = c.begin()
        c.execute(text("SELECT set_config('app.tenant_id', :o, true)"), {"o": ns.org_a})
        c.execute(text("INSERT INTO crm_settings (access) VALUES ('everyone')"))
        stored = c.execute(text(
            "SELECT organization_id::text FROM crm_settings")).scalars().all()
        trans.rollback()
    assert stored == [ns.org_a]


def test_the_migration_replays_with_no_change(ns) -> None:
    _set_access(ns, ns.org_a, "groups", ["group:sales"])
    sql = _settings_migration().read_text(encoding="utf-8")
    with ns.admin_engine.begin() as c, c.connection.dbapi_connection.cursor() as cur:
        cur.execute(sql)
    with ns.admin_engine.connect() as c:
        row = c.execute(text(
            "SELECT access, access_groups FROM crm_settings "
            "WHERE organization_id = CAST(:o AS uuid)"), {"o": ns.org_a}).first()
        policies = c.execute(text(
            "SELECT count(*) FROM pg_policies WHERE tablename = 'crm_settings'"),
        ).scalar()
        forced = c.execute(text(
            "SELECT relrowsecurity AND relforcerowsecurity FROM pg_class "
            "WHERE oid = 'crm_settings'::regclass")).scalar()
    assert (row[0], list(row[1])) == ("groups", ["group:sales"])
    assert policies == 1
    assert forced is True
