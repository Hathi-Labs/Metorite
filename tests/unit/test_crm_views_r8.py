"""R8 — the CRM list filters and the saved views (WS-53 CRM-Q8, CRM-S2).

Migration `crm_views` adds the saved-view table. ``routes/crm/core.py`` holds the
extra list filters and ``routes/crm/views.py`` holds the view routes. Spec:
``crm_platform.md`` §5.3, §6 and §18.5.

What this proves against a REAL Postgres, through the real ``/crm`` router, as
the NON-privileged role of the phase-4 rehearsal (no superuser, no BYPASSRLS),
with two organizations:

- the filters return the right rows: owner (and ``me``), source, the date
  ranges, the close dates, a custom field by equality, and a custom picklist
  with ``.in``. The export takes the same filters;
- an unknown custom key, a bad date, a bad UUID and a close date on a lead each
  answer 422 before any SQL;
- a custom value that looks like SQL is data, never SQL;
- a private view is invisible to another member of the same organization. A
  shared view shows to both. Only an admin shares. A member that is not the
  owner gets 404 on a private view and 403 on a shared one. An admin may change
  a shared view;
- another organization sees none of it;
- the view routes work in Mirror mode;
- the migration is found by content and it forces row level security.

A hermetic fake agrees with any SQL it is handed, so none of this could be
proved without the database.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

_MIGRATIONS = Path(__file__).resolve().parents[2] / "infra" / "postgres"


def _views_migration() -> Path:
    """The migration that creates ``crm_views``, found by content (R1)."""
    found = [
        p for p in sorted(_MIGRATIONS.glob("*.sql"))
        if p.name != "schema.generated.sql"
        and "CREATE TABLE IF NOT EXISTS crm_views" in p.read_text(encoding="utf-8")
    ]
    assert len(found) == 1, [p.name for p in found]
    return found[0]


def _app_dsn(ns: Any) -> str:
    return ns.app_url.render_as_string(hide_password=False)


def _principal(email: str, org: str, *perms: str) -> Any:
    from acb_auth import UserContext, UserRole, build_access

    return UserContext(
        email=email, role=UserRole.EMPLOYEE, access=build_access(list(perms)),
        organization_id=org,
    )


def _admin(org: str, email: str = "admin@crm-q8.test") -> Any:
    return _principal(email, org, "feature:crm", "admin:access:manage")


def _member(org: str, email: str) -> Any:
    return _principal(email, org, "feature:crm")


async def _call(
    p: Any, user: Any, method: str, path: str, body: dict | None = None,
) -> Any:
    """One request through the real ``/crm`` router, as *user*."""
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


_OWNED = (
    "crm_views", "crm_deals", "crm_leads", "crm_field_defs", "crm_settings",
    "crm_deal_statuses", "crm_lead_statuses",
)


@pytest.fixture
def ns(promoted, monkeypatch):  # noqa: F811 — a fixture
    """The promoted catalog with the CRM switch on, cleaned after each test."""
    from tests.unit._crm_fakes import crm_switch_on

    crm_switch_on(monkeypatch, "*")
    yield promoted
    with promoted.admin_engine.begin() as c:
        for org in (promoted.org_a, promoted.org_b):
            for table in _OWNED:
                c.execute(text(
                    f"DELETE FROM {table} WHERE organization_id = CAST(:o AS uuid)"),
                    {"o": org})


def _everyone(p: Any, org: str) -> None:
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO crm_settings (organization_id, access) "
            "VALUES (CAST(:o AS uuid), 'everyone') "
            "ON CONFLICT (organization_id) DO UPDATE SET access = 'everyone'"),
            {"o": org})


def _seed(p: Any, org: str) -> None:
    """Three deals and two leads, with owners, sources, dates and a picklist."""
    with p.admin_engine.begin() as c:
        deal_status = c.execute(text(
            "INSERT INTO crm_deal_statuses (organization_id, name, position, type) "
            "VALUES (CAST(:o AS uuid), 'New', 1, 'open') RETURNING id"),
            {"o": org}).scalar()
        lead_status = c.execute(text(
            "INSERT INTO crm_lead_statuses (organization_id, name, position, type) "
            "VALUES (CAST(:o AS uuid), 'New', 1, 'open') RETURNING id"),
            {"o": org}).scalar()
        for key, ent in (("Region", "deal"), ("Region", "lead")):
            c.execute(text(
                "INSERT INTO crm_field_defs (organization_id, entity, key, label, "
                "type) VALUES (CAST(:o AS uuid), :e, :k, :k, 'picklist')"),
                {"o": org, "e": ent, "k": key})
        deals = (
            ("D-north", "ann@crm-q8.test", "email", "2026-01-10", "2026-02-01",
             '{"Region": "North"}'),
            ("D-south", "bob@crm-q8.test", "agent", "2026-03-10", "2026-04-01",
             '{"Region": "South"}'),
            ("D-east", "ann@crm-q8.test", "email", "2026-05-10", "2026-06-01",
             '{"Region": "East"}'),
        )
        for name, owner, source, created, close, custom in deals:
            c.execute(text(
                "INSERT INTO crm_deals (organization_id, name, status_id, "
                "owner_email, source, created_at, updated_at, expected_close_date, "
                "custom) VALUES (CAST(:o AS uuid), :n, CAST(:s AS uuid), :ow, :so, "
                "CAST(:cr AS timestamptz), CAST(:cr AS timestamptz), "
                "CAST(:cl AS date), CAST(:cu AS jsonb))"),
                {"o": org, "n": name, "s": str(deal_status), "ow": owner,
                 "so": source, "cr": created, "cl": close, "cu": custom})
        for name, owner in (("L-ann", "ann@crm-q8.test"), ("L-bob", "bob@crm-q8.test")):
            c.execute(text(
                "INSERT INTO crm_leads (organization_id, lead_name, status_id, "
                "owner_email, custom) VALUES (CAST(:o AS uuid), :n, "
                "CAST(:s AS uuid), :ow, CAST(:cu AS jsonb))"),
                {"o": org, "n": name, "s": str(lead_status), "ow": owner,
                 "cu": '{"Region": "North"}'})


async def _names(p: Any, user: Any, path: str) -> list[str]:
    response = await _call(p, user, "GET", path)
    assert response.status_code == 200, response.text
    return sorted(r.get("name") or r.get("lead_name") for r in response.json()["rows"])


# ── The filters ─────────────────────────────────────────────────────────────

async def test_owner_and_me_filter_the_list(ns) -> None:
    _seed(ns, ns.org_a)
    ann = _admin(ns.org_a, "Ann@crm-q8.test")
    assert await _names(ns, ann, "/crm/deals?owner=bob@crm-q8.test") == ["D-south"]
    # `me` is the caller, case-insensitively, and never a request value.
    assert await _names(ns, ann, "/crm/deals?owner=me") == ["D-east", "D-north"]
    assert await _names(ns, ann, "/crm/leads?owner=me") == ["L-ann"]


async def test_source_and_date_filters(ns) -> None:
    _seed(ns, ns.org_a)
    user = _admin(ns.org_a)
    assert await _names(ns, user, "/crm/deals?source=agent") == ["D-south"]
    assert await _names(
        ns, user, "/crm/deals?created_from=2026-03-01&created_to=2026-05-10",
    ) == ["D-east", "D-south"]
    assert await _names(ns, user, "/crm/deals?updated_to=2026-01-10") == ["D-north"]
    assert await _names(
        ns, user, "/crm/deals?close_from=2026-03-01&close_to=2026-04-01",
    ) == ["D-south"]


async def test_custom_equality_and_picklist_in(ns) -> None:
    _seed(ns, ns.org_a)
    user = _admin(ns.org_a)
    assert await _names(ns, user, "/crm/deals?custom.Region=South") == ["D-south"]
    assert await _names(
        ns, user, "/crm/deals?custom.Region.in=North,East",
    ) == ["D-east", "D-north"]
    # It combines with another filter, and it is scoped to the entity.
    assert await _names(
        ns, user, "/crm/deals?custom.Region.in=North,East&owner=bob@crm-q8.test",
    ) == []
    assert await _names(ns, user, "/crm/leads?custom.Region=North") == ["L-ann", "L-bob"]


async def test_the_export_takes_the_same_filters(ns) -> None:
    _seed(ns, ns.org_a)
    user = _admin(ns.org_a)
    response = await _call(
        ns, user, "GET",
        "/crm/export/deals.csv?custom.Region.in=North,East&source=email&owner=me",
    )
    assert response.status_code == 200, response.text
    assert response.headers["X-Export-Rows"] == "0"  # admin@ owns none
    response = await _call(
        ns, user, "GET", "/crm/export/deals.csv?custom.Region.in=North,East",
    )
    assert response.headers["X-Export-Rows"] == "2"


@pytest.mark.parametrize("query", [
    "/crm/deals?custom.Nope=1",
    "/crm/deals?custom.Region.in.in=1",
    "/crm/deals?created_from=not-a-date",
    "/crm/deals?updated_to=2026-13-40",
    "/crm/deals?pipeline_id=not-a-uuid",
    "/crm/leads?close_from=2026-01-01",
    "/crm/contacts?custom.Region=North",
])
async def test_a_bad_filter_is_a_422(ns, query: str) -> None:
    _seed(ns, ns.org_a)
    response = await _call(ns, _admin(ns.org_a), "GET", query)
    assert response.status_code == 422, (query, response.text)


async def test_a_custom_value_is_data_never_sql(ns) -> None:
    _seed(ns, ns.org_a)
    user = _admin(ns.org_a)
    for value in ("x' OR '1'='1", "x'); DROP TABLE crm_deals; --"):
        assert await _names(ns, user, f"/crm/deals?custom.Region={value}") == []
    assert len(await _names(ns, user, "/crm/deals")) == 3


async def test_filters_do_not_cross_organizations(ns) -> None:
    _seed(ns, ns.org_a)
    _seed(ns, ns.org_b)
    with ns.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE crm_deals SET name = 'B-' || name "
            "WHERE organization_id = CAST(:o AS uuid)"), {"o": ns.org_b})
    assert await _names(
        ns, _admin(ns.org_b), "/crm/deals?custom.Region=North",
    ) == ["B-D-north"]


# ── The views ───────────────────────────────────────────────────────────────

_VIEW = {
    "entity": "deals", "name": "Mine", "layout": "board",
    "filters": {"owner": "me", "custom": {"Region": "North"}},
    "sort": {"key": "amount", "dir": "desc"}, "columns": ["Region"],
}


async def test_a_private_view_is_invisible_to_another_member(ns) -> None:
    _everyone(ns, ns.org_a)
    ann = _member(ns.org_a, "ann@crm-q8.test")
    bob = _member(ns.org_a, "bob@crm-q8.test")
    made = await _call(ns, ann, "POST", "/crm/views", _VIEW)
    assert made.status_code == 201, made.text
    view = made.json()
    assert view["shared"] is False and view["mine"] is True
    assert view["owner_email"] == "ann@crm-q8.test"
    assert view["filters"]["custom"] == {"Region": "North"}
    assert view["layout"] == "board" and view["columns"] == ["Region"]

    seen = await _call(ns, ann, "GET", "/crm/views?entity=deals")
    assert [v["id"] for v in seen.json()] == [view["id"]]
    other = await _call(ns, bob, "GET", "/crm/views?entity=deals")
    assert other.json() == []
    # Another member cannot learn that the view exists.
    assert (await _call(
        ns, bob, "PATCH", f"/crm/views/{view['id']}", {"name": "x"},
    )).status_code == 404
    assert (await _call(ns, bob, "DELETE", f"/crm/views/{view['id']}")).status_code == 404


async def test_only_an_admin_shares_and_a_shared_view_shows_to_both(ns) -> None:
    _everyone(ns, ns.org_a)
    ann = _member(ns.org_a, "ann@crm-q8.test")
    bob = _member(ns.org_a, "bob@crm-q8.test")
    boss = _admin(ns.org_a)
    refused = await _call(ns, ann, "POST", "/crm/views", {**_VIEW, "shared": True})
    assert refused.status_code == 403
    mine = (await _call(ns, ann, "POST", "/crm/views", _VIEW)).json()
    assert (await _call(
        ns, ann, "PATCH", f"/crm/views/{mine['id']}", {"shared": True},
    )).status_code == 403

    shared = await _call(
        ns, boss, "POST", "/crm/views", {**_VIEW, "name": "Team", "shared": True},
    )
    assert shared.status_code == 201, shared.text
    sid = shared.json()["id"]
    for who in (ann, bob):
        listed = await _call(ns, who, "GET", "/crm/views?entity=deals")
        assert sid in [v["id"] for v in listed.json()]
    # Not the owner: read, but not change.
    assert (await _call(
        ns, bob, "PATCH", f"/crm/views/{sid}", {"name": "x"},
    )).status_code == 403
    assert (await _call(ns, bob, "DELETE", f"/crm/views/{sid}")).status_code == 403
    # The admin may share a view of a member, once the member's view is visible.
    renamed = await _call(ns, boss, "PATCH", f"/crm/views/{sid}", {"name": "Team 2"})
    assert renamed.status_code == 200 and renamed.json()["name"] == "Team 2"
    assert (await _call(ns, boss, "DELETE", f"/crm/views/{sid}")).status_code == 200


async def test_the_owner_renames_and_deletes(ns) -> None:
    _everyone(ns, ns.org_a)
    ann = _member(ns.org_a, "ann@crm-q8.test")
    view = (await _call(ns, ann, "POST", "/crm/views", _VIEW)).json()
    patched = await _call(ns, ann, "PATCH", f"/crm/views/{view['id']}", {
        "name": "  New   name ", "filters": {"source": "email"}, "sort": None,
        "layout": "list",
    })
    assert patched.status_code == 200, patched.text
    body = patched.json()
    assert body["name"] == "New name" and body["filters"] == {"source": "email"}
    assert body["sort"] is None and body["layout"] == "list"
    assert (await _call(ns, ann, "DELETE", f"/crm/views/{view['id']}")).status_code == 200
    assert (await _call(ns, ann, "GET", "/crm/views?entity=deals")).json() == []


@pytest.mark.parametrize("body", [
    {**_VIEW, "name": "  "},
    {**_VIEW, "filters": {"nope": 1}},
    {**_VIEW, "entity": "contacts", "layout": "board", "filters": {}},
    {**_VIEW, "entity": "leads", "pipeline_id": "00000000-0000-0000-0000-000000000000"},
    {**_VIEW, "pipeline_id": "not-a-uuid"},
    {**_VIEW, "entity": "widgets"},
])
async def test_a_bad_view_is_a_422(ns, body: dict) -> None:
    _everyone(ns, ns.org_a)
    response = await _call(ns, _member(ns.org_a, "ann@crm-q8.test"), "POST", "/crm/views", body)
    assert response.status_code == 422, (body, response.text)


async def test_another_organization_sees_no_view(ns) -> None:
    _everyone(ns, ns.org_a)
    _everyone(ns, ns.org_b)
    boss = _admin(ns.org_a)
    made = (await _call(
        ns, boss, "POST", "/crm/views", {**_VIEW, "shared": True},
    )).json()
    outsider = _admin(ns.org_b, "admin@other.test")
    assert (await _call(ns, outsider, "GET", "/crm/views?entity=deals")).json() == []
    assert (await _call(ns, outsider, "DELETE", f"/crm/views/{made['id']}")).status_code == 404


async def test_views_work_in_mirror_mode(ns) -> None:
    with ns.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO crm_settings (organization_id, access, mode) "
            "VALUES (CAST(:o AS uuid), 'everyone', 'mirror') "
            "ON CONFLICT (organization_id) DO UPDATE SET mode = 'mirror', "
            "access = 'everyone'"), {"o": ns.org_a})
    ann = _member(ns.org_a, "ann@crm-q8.test")
    made = await _call(ns, ann, "POST", "/crm/views", _VIEW)
    assert made.status_code == 201, made.text
    assert (await _call(
        ns, ann, "PATCH", f"/crm/views/{made.json()['id']}", {"name": "Still works"},
    )).status_code == 200


def test_the_table_forces_row_level_security(ns) -> None:
    path = _views_migration()
    source = path.read_text(encoding="utf-8")
    assert "FORCE  ROW LEVEL SECURITY" in source and "WITH CHECK" in source
    with ns.admin_engine.connect() as c:
        flags = c.execute(text(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
            "WHERE oid = 'crm_views'::regclass")).first()
    assert tuple(flags) == (True, True)
