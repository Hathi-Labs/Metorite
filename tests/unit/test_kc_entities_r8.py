"""R8 — the entity registry on a real database (WS-61 KC-8a).

Spec: ``project-docs/specs/knowledge_curation.md`` §6.2, §6.4, §16 and the
KC-8a row of §22.

What this proves against a REAL Postgres, through the real ``/knowledge``
router and the registry functions, as the NON-privileged role of the
phase-4 rehearsal (``acb_app_h3rls``: no superuser, no BYPASSRLS), with the
whole ladder, the four generated phases and two organizations:

- two ``ensure_entity`` calls on one ``people.id`` give one row, and a raw
  second INSERT raises a unique violation (the done-when "a second insert
  for one record fails"). Each person resolves to one entity;
- a member without ``feature:people`` lists no person, and a GET of a person
  entity is 404. An inactive person never lists;
- a project with no grant, another member's personal project and a Center
  project outside the member's group never list, never GET and never hit an
  alias. Removing the grant drops the project at once;
- the CRM rule: ``crm_settings.access`` of ``admins``, ``everyone`` and
  ``groups``, and the kill switch. A member outside the group gets no
  customer;
- a workflow needs ``feature:workflows``;
- an entity whose page the member cannot see shows with ``page_id`` null;
- a Knowledge-owned product in a space the member cannot read is hidden,
  and a grant shows it;
- org B sees nothing of org A, and ``ensure_entity`` in org B cannot point
  at a record of org A;
- the person resolver: an alias or an email gives the ``people.id``. One
  match acts, and two ask.

A hermetic fake agrees with any SQL it is handed, so none of these could be
proved without the database. ``tests/unit/test_kc_entities.py`` holds the
shape of the rule without one.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

# ⚠️ Every test here needs the real database. Without this gate the suite
# ERRORS where no ladder URL is set (the deploy run), and the deploy stops.
pytestmark = _DB_GATE

CAPABILITY = "knowledge:manage"
PEOPLE = "feature:people"
PROJECTS = "feature:projects"
WORKFLOWS = "feature:workflows"
CRM = "feature:crm"
CRM_ADMIN = "admin:access:manage"


# ── Helpers ─────────────────────────────────────────────────────────────────

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


def _address(tag: str) -> str:
    return f"{tag}.{uuid.uuid4().hex[:8]}@kc8a-r8.test"


def _principal(email: str, org: str, *perms: str) -> Any:
    from acb_auth import UserContext, UserRole, build_access

    return UserContext(
        email=email, role=UserRole.EMPLOYEE,
        access=build_access(["feature:knowledge", *perms]),
        organization_id=org,
    )


async def _calls(
    p: Any, user: Any, requests: list[tuple[str, str, dict | None]],
) -> list[Any]:
    """Requests through the real ``/knowledge`` router, as *user*.

    The override binds the tenant from the user, which is what
    ``_with_resolved_access`` does for a real request. The tenant never comes
    from the request.
    """
    import httpx
    from acb_auth import get_current_user
    from acb_common.db import bind_tenant, clear_tenant, release_tenant
    from fastapi import FastAPI
    from gateway.routes import knowledge as kb_package

    async def _caller() -> Any:
        bind_tenant(user.organization_id)
        return user

    app = FastAPI()
    app.include_router(kb_package.router)
    app.dependency_overrides[get_current_user] = _caller
    scope = clear_tenant()
    try:
        async with tenant_engine_scope(_app_dsn(p)):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://kc.test",
            ) as client:
                return [await client.request(method, path, json=body)
                        for method, path, body in requests]
    finally:
        release_tenant(scope)


async def _call(p: Any, user: Any, method: str, path: str,
                body: dict | None = None) -> Any:
    return (await _calls(p, user, [(method, path, body)]))[0]


async def _ok(p: Any, user: Any, method: str, path: str, body: dict | None = None,
              status: int = 200) -> Any:
    response = await _call(p, user, method, path, body)
    assert response.status_code == status, (method, path, response.status_code, response.text)
    return response.json()


async def _in_tenant(p: Any, org: str, fn: Callable[[Any], Awaitable[Any]]) -> Any:
    """Run *fn(db)* in one tenant session of *org*, as the app role."""
    from acb_common.db import bind_tenant, clear_tenant, release_tenant, tenant_session

    scope = clear_tenant()
    try:
        async with tenant_engine_scope(_app_dsn(p)):
            bind_tenant(org)
            async with tenant_session() as db:
                return await fn(db)
    finally:
        release_tenant(scope)


async def _ensure(p: Any, org: str, ref_table: str, ref_id: str) -> str | None:
    from gateway.kb_entities import registry

    return await _in_tenant(p, org, lambda db: registry.ensure_entity(db, ref_table, ref_id))


async def _ensure_at_once(p: Any, org: str, ref_table: str, ref_id: str,
                          n: int) -> list[str | None]:
    """*n* first calls at once, each in its own session, in one engine scope."""
    from acb_common.db import bind_tenant, clear_tenant, release_tenant, tenant_session
    from gateway.kb_entities import registry

    async def one() -> str | None:
        async with tenant_session() as db:
            return await registry.ensure_entity(db, ref_table, ref_id)

    scope = clear_tenant()
    try:
        async with tenant_engine_scope(_app_dsn(p)):
            bind_tenant(org)
            return list(await asyncio.gather(*[one() for _ in range(n)]))
    finally:
        release_tenant(scope)


async def _listed(p: Any, user: Any, kind: str, q: str | None = None) -> list[dict]:
    path = f"/knowledge/entities?type={kind}" + (f"&q={q}" if q else "")
    return (await _ok(p, user, "GET", path))["entities"]


async def _names(p: Any, user: Any, kind: str, q: str | None = None) -> set[str]:
    return {r["name"] for r in await _listed(p, user, kind, q)}


async def _get_status(p: Any, user: Any, entity_id: str) -> int:
    return (await _call(p, user, "GET", f"/knowledge/entities/{entity_id}")).status_code


def _set_alias(p: Any, entity_id: str, *aliases: str) -> None:
    with p.admin_engine.begin() as c:
        c.execute(text("UPDATE kb_entities SET aliases = CAST(:a AS text[]) "
                       "WHERE id = CAST(:i AS uuid)"), {"a": list(aliases), "i": entity_id})


# ── Seeds, as the superuser (it escapes the policies under test) ────────────

@pytest.fixture
def ns(promoted, monkeypatch):  # noqa: F811 — a fixture
    """The promoted catalog with Knowledge and the CRM on, cleaned after each test."""
    from acb_common import get_settings

    from tests.unit._crm_fakes import crm_switch_on

    settings = get_settings()
    monkeypatch.setattr(settings, "knowledge_enabled", True, raising=False)
    monkeypatch.setattr(settings, "knowledge_orgs", "*", raising=False)
    crm_switch_on(monkeypatch, "*")
    made: dict[str, list[str]] = {"users": [], "groups": []}
    promoted.made = made
    yield promoted
    with promoted.admin_engine.begin() as c:
        for table in ("kb_entities", "kb_chunks", "kb_links", "kb_page_versions"):
            c.execute(text(f"DELETE FROM {table}"))
        c.execute(text("UPDATE kb_pages SET parent_id = NULL"))
        for table in ("kb_pages", "kb_space_grants", "kb_spaces", "pm_project_grants"):
            c.execute(text(f"DELETE FROM {table}"))
        c.execute(text("UPDATE pm_projects SET parent_project_id = NULL"))
        for table in ("pm_projects", "crm_companies", "crm_settings", "workflows"):
            c.execute(text(f"DELETE FROM {table}"))
        c.execute(text("DELETE FROM people WHERE email LIKE '%@kc8a-r8.test'"))
        for gid in made["groups"]:
            c.execute(text("DELETE FROM org_group WHERE id = CAST(:g AS uuid)"), {"g": gid})
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


def _add_group(p: Any, org: str, slug: str, user_ids: list[str]) -> str:
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
    return gid


def _add_person(p: Any, org: str, name: str, email: str | None = None,
                status: str = "active") -> str:
    with p.admin_engine.begin() as c:
        return str(c.execute(text(
            "INSERT INTO people (name, email, status, organization_id) "
            "VALUES (:n, :e, :s, CAST(:o AS uuid)) RETURNING id"),
            {"n": name, "e": email or _address("p"), "s": status, "o": org}).scalar())


def _add_project(p: Any, org: str, name: str, *subjects: str,
                 personal_owner: str | None = None) -> str:
    with p.admin_engine.begin() as c:
        pid = str(c.execute(text(
            "INSERT INTO pm_projects (name, created_by, organization_id, personal_owner, "
            "owns_statuses) VALUES (:n, 'seed', CAST(:o AS uuid), :po, true) RETURNING id"),
            {"n": name, "o": org, "po": personal_owner}).scalar())
        for subject in subjects:
            c.execute(text(
                "INSERT INTO pm_project_grants (project_id, subject, created_by, "
                "organization_id) VALUES (CAST(:p AS uuid), :s, 'seed', CAST(:o AS uuid))"),
                {"p": pid, "s": subject, "o": org})
    return pid


def _add_company(p: Any, org: str, name: str) -> str:
    with p.admin_engine.begin() as c:
        return str(c.execute(text(
            "INSERT INTO crm_companies (name, organization_id) "
            "VALUES (:n, CAST(:o AS uuid)) RETURNING id"), {"n": name, "o": org}).scalar())


def _set_crm_access(p: Any, org: str, access: str, groups: list[str]) -> None:
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO crm_settings (organization_id, access, access_groups) "
            "VALUES (CAST(:o AS uuid), :a, :g) "
            "ON CONFLICT (organization_id) DO UPDATE SET "
            "access = EXCLUDED.access, access_groups = EXCLUDED.access_groups"),
            {"o": org, "a": access, "g": groups})


def _add_workflow(p: Any, org: str, name: str) -> str:
    with p.admin_engine.begin() as c:
        return str(c.execute(text(
            "INSERT INTO workflows (name, owner_email, hook_token, organization_id) "
            "VALUES (:n, 'seed@kc8a-r8.test', :h, CAST(:o AS uuid)) RETURNING id"),
            {"n": name, "h": uuid.uuid4().hex, "o": org}).scalar())


def _entity_rows(p: Any, ref_id: str) -> list[Any]:
    with p.admin_engine.connect() as c:
        return c.execute(text(
            "SELECT id, organization_id::text AS org FROM kb_entities "
            "WHERE ref_id = CAST(:r AS uuid)"), {"r": ref_id}).all()


# ── One entity for each record ──────────────────────────────────────────────

async def test_two_ensures_give_one_row_and_a_raw_second_insert_fails(ns, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    person = _add_person(ns, ns.org_a, f"Asha {uuid.uuid4().hex[:6]}")
    first = await _ensure(ns, ns.org_a, "people", person)
    second = await _ensure(ns, ns.org_a, "people", person)
    assert first is not None and first == second
    assert len(_entity_rows(ns, person)) == 1

    # Two first calls at the same time make one row too.
    other = _add_person(ns, ns.org_a, f"Bala {uuid.uuid4().hex[:6]}")
    both = await _ensure_at_once(ns, ns.org_a, "people", other, 3)
    assert len(set(both)) == 1 and both[0] is not None
    assert len(_entity_rows(ns, other)) == 1

    # A raw second INSERT for one record fails on kb_entities_org_ref_key.
    with pytest.raises(IntegrityError) as raised, ns.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO kb_entities (organization_id, type, ref_table, ref_id) "
            "VALUES (CAST(:o AS uuid), 'person', 'people', CAST(:r AS uuid))"),
            {"o": ns.org_a, "r": person})
    assert "kb_entities_org_ref_key" in str(raised.value)


async def test_each_person_resolves_to_one_entity(ns, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    tag = uuid.uuid4().hex[:6]
    ids = [_add_person(ns, ns.org_a, f"P{i} {tag}") for i in range(3)]
    for pid in ids:
        assert await _ensure(ns, ns.org_a, "people", pid) is not None
        await _ensure(ns, ns.org_a, "people", pid)
    member = _principal(_address("m"), ns.org_a, PEOPLE)
    rows = [r for r in await _listed(ns, member, "person") if tag in r["name"]]
    assert sorted(r["ref_id"] for r in rows) == sorted(ids)
    assert all(r["id"] is not None for r in rows)
    assert len({r["id"] for r in rows}) == 3
    # The name only: no email, skills or HR field reaches the caller.
    assert set(rows[0]) == {"id", "type", "ref_table", "ref_id", "name", "aliases",
                            "space_id", "page_id"}


# ── The owner rule of each type ─────────────────────────────────────────────

async def test_a_member_without_people_gets_no_person(ns, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    tag = uuid.uuid4().hex[:6]
    person = _add_person(ns, ns.org_a, f"Chitra {tag}")
    _add_person(ns, ns.org_a, f"Gone {tag}", status="alumni")
    eid = await _ensure(ns, ns.org_a, "people", person)
    assert eid is not None

    without = _principal(_address("no-people"), ns.org_a)
    assert await _listed(ns, without, "person") == []
    assert await _get_status(ns, without, eid) == 404

    holder = _principal(_address("people"), ns.org_a, PEOPLE)
    assert {n for n in await _names(ns, holder, "person") if tag in n} == {f"Chitra {tag}"}
    got = await _ok(ns, holder, "GET", f"/knowledge/entities/{eid}")
    assert got["ref_id"] == person and got["name"] == f"Chitra {tag}"


async def test_hidden_projects_never_show(ns, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    me = _address("eng")
    other = _address("other")
    my_id = _add_member(ns, ns.org_a, me)
    _add_member(ns, ns.org_a, other)
    eng_group = _add_group(ns, ns.org_a, f"eng-{uuid.uuid4().hex[:6]}", [my_id])
    with ns.admin_engine.connect() as c:
        eng_slug = c.execute(text("SELECT slug FROM org_group WHERE id = CAST(:g AS uuid)"),
                             {"g": eng_group}).scalar()
    design = f"design-{uuid.uuid4().hex[:6]}"
    _add_group(ns, ns.org_a, design, [])

    granted = _add_project(ns, ns.org_a, "Penrose", f"group:{eng_slug}")
    to_me = _add_project(ns, ns.org_a, "Mine by email", me)
    no_grant = _add_project(ns, ns.org_a, "Secret")
    center = _add_project(ns, ns.org_a, "Design centre", f"group:{design}")
    theirs = _add_project(ns, ns.org_a, "Their personal", other, personal_owner=other)
    mine_personal = _add_project(ns, ns.org_a, "My personal", me, personal_owner=me)
    ids = {pid: await _ensure(ns, ns.org_a, "pm_projects", pid)
           for pid in (granted, to_me, no_grant, center, theirs, mine_personal)}
    assert all(ids.values())

    member = _principal(me, ns.org_a, PROJECTS)
    assert await _names(ns, member, "workstream") == {"Penrose", "Mine by email"}
    for hidden in (no_grant, center, theirs, mine_personal):
        assert await _get_status(ns, member, ids[hidden]) == 404, hidden
    assert await _get_status(ns, member, ids[granted]) == 200

    # An alias on a hidden project gives no hit, and the same alias on a
    # project the member sees does.
    from gateway.kb_entities import resolver

    _set_alias(ns, ids[no_grant], "PX-1")
    _set_alias(ns, ids[theirs], "PX-2")
    _set_alias(ns, ids[granted], "the Penrose")
    for words in ("PX-1", "px-1", "PX-2"):
        hits = await _in_tenant(ns, ns.org_a,
                                lambda db, w=words: resolver.resolve_alias(db, member, w))
        assert hits == [], words
        assert await _listed(ns, member, "workstream", words) == []
    hits = await _in_tenant(ns, ns.org_a,
                            lambda db: resolver.resolve_alias(db, member, "THE  penrose"))
    assert [h["ref_id"] for h in hits] == [granted]

    # A holder of data:org:read sees each org project, and still never the
    # personal project of another member, nor its own.
    reader = _principal(_address("reader"), ns.org_a, PROJECTS, "data:org:read")
    _add_member(ns, ns.org_a, reader.email)
    assert "Their personal" not in await _names(ns, reader, "workstream")
    assert {"Penrose", "Secret", "Design centre"} <= await _names(ns, reader, "workstream")

    # Removing the grant drops the project at once.
    with ns.admin_engine.begin() as c:
        c.execute(text("DELETE FROM pm_project_grants WHERE project_id = CAST(:p AS uuid)"),
                  {"p": granted})
    assert await _names(ns, member, "workstream") == {"Mine by email"}
    assert await _get_status(ns, member, ids[granted]) == 404

    # Without feature:projects, no workstream at all.
    assert await _listed(ns, _principal(me, ns.org_a), "workstream") == []


async def test_the_crm_rule_decides_the_customers(ns, app_engine, monkeypatch) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    seller, outsider = _address("seller"), _address("outsider")
    seller_id = _add_member(ns, ns.org_a, seller)
    _add_member(ns, ns.org_a, outsider)
    _add_group(ns, ns.org_a, "sales", [seller_id])
    acme = _add_company(ns, ns.org_a, "Acme")
    eid = await _ensure(ns, ns.org_a, "crm_companies", acme)
    assert eid is not None

    admin = _principal(_address("admin"), ns.org_a, CRM, CRM_ADMIN)
    seller_a = _principal(seller, ns.org_a, CRM)
    outsider_a = _principal(outsider, ns.org_a, CRM)

    # No row reads as `admins`.
    assert await _names(ns, admin, "customer") == {"Acme"}
    assert await _listed(ns, seller_a, "customer") == []
    assert await _get_status(ns, seller_a, eid) == 404

    _set_crm_access(ns, ns.org_a, "groups", ["group:sales"])
    assert await _names(ns, seller_a, "customer") == {"Acme"}
    assert await _listed(ns, outsider_a, "customer") == []
    assert await _get_status(ns, outsider_a, eid) == 404
    _set_alias(ns, eid, "ACME-1")
    assert await _listed(ns, outsider_a, "customer", "ACME-1") == []
    assert len(await _listed(ns, seller_a, "customer", "acme-1")) == 1

    _set_crm_access(ns, ns.org_a, "everyone", [])
    assert await _names(ns, outsider_a, "customer") == {"Acme"}
    # `everyone` still needs feature:crm.
    assert await _listed(ns, _principal(outsider, ns.org_a), "customer") == []

    # The kill switch hides every customer, from an admin too.
    from tests.unit._crm_fakes import crm_switch_on

    crm_switch_on(monkeypatch, "")
    assert await _listed(ns, admin, "customer") == []
    assert await _get_status(ns, admin, eid) == 404


async def test_a_workflow_needs_the_workflows_feature(ns, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    wf = _add_workflow(ns, ns.org_a, "Invoice approval")
    eid = await _ensure(ns, ns.org_a, "workflows", wf)
    assert eid is not None
    assert await _listed(ns, _principal(_address("m"), ns.org_a), "workflow") == []
    assert await _get_status(ns, _principal(_address("m"), ns.org_a), eid) == 404
    holder = _principal(_address("w"), ns.org_a, WORKFLOWS)
    assert await _names(ns, holder, "workflow") == {"Invoice approval"}
    got = await _ok(ns, holder, "GET", f"/knowledge/entities/{eid}")
    assert set(got) == {"id", "type", "ref_table", "ref_id", "name", "aliases",
                        "space_id", "page_id"}


async def test_the_company_is_the_callers_org_only(ns, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    a = await _listed(ns, _principal(_address("a"), ns.org_a), "company")
    b = await _listed(ns, _principal(_address("b"), ns.org_b), "company")
    assert [r["ref_id"] for r in a] == [ns.org_a]
    assert [r["ref_id"] for r in b] == [ns.org_b]


# ── The page, and the Knowledge-owned types ─────────────────────────────────

async def test_a_hidden_page_shows_as_null(ns, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    person = _add_person(ns, ns.org_a, f"Dev {uuid.uuid4().hex[:6]}")
    eid = await _ensure(ns, ns.org_a, "people", person)
    author = _principal(_address("author"), ns.org_a, PEOPLE)
    # The author makes the page in their own `me` space, through the route.
    made = await _ok(ns, author, "POST", f"/knowledge/entities/{eid}/page", {}, status=201)
    page_id = made["page"]["id"]
    assert made["entity"]["page_id"] == page_id
    assert made["page"]["state"] == "draft" and made["page"]["title"] == made["entity"]["name"]

    other = _principal(_address("other"), ns.org_a, PEOPLE)
    seen = await _ok(ns, other, "GET", f"/knowledge/entities/{eid}")
    assert seen["page_id"] is None, "a page the member cannot see must not show"
    assert (await _ok(ns, author, "GET", f"/knowledge/entities/{eid}"))["page_id"] == page_id
    # A second page is refused, and the hidden page is never replaced.
    again = await _call(ns, other, "POST", f"/knowledge/entities/{eid}/page", {})
    assert again.status_code == 409
    with ns.admin_engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM kb_pages")).scalar() == 1


async def test_a_product_follows_its_space(ns, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    admin = _principal(_address("admin"), ns.org_a, CAPABILITY)
    space = (await _ok(ns, admin, "POST", "/knowledge/spaces",
                       {"name": "Products", "grants": []}, status=201))["id"]
    product = await _ok(ns, admin, "POST", "/knowledge/entities",
                        {"type": "product", "name": "Penrose Pellet Extruder",
                         "space_id": space, "aliases": ["PX-1", "px-1", "the Penrose"]},
                        status=201)
    assert product["aliases"] == ["PX-1", "the Penrose"]
    assert product["ref_table"] == "kb_entities" and product["ref_id"] == product["id"]

    member_email = _address("member")
    member = _principal(member_email, ns.org_a)
    assert await _listed(ns, member, "product") == []
    assert await _get_status(ns, member, product["id"]) == 404
    from gateway.kb_entities import resolver

    assert await _in_tenant(ns, ns.org_a,
                            lambda db: resolver.resolve_alias(db, member, "px-1")) == []
    # A member with no write on the space cannot make one there.
    refused = await _call(ns, member, "POST", "/knowledge/entities",
                          {"type": "topic", "name": "Pellets", "space_id": space})
    assert refused.status_code == 404
    # A person or a customer is never made here.
    wrong = await _call(ns, admin, "POST", "/knowledge/entities",
                        {"type": "person", "name": "X", "space_id": space})
    assert wrong.status_code == 422

    await _ok(ns, admin, "PUT", f"/knowledge/spaces/{space}/grants",
              {"grants": [{"subject": member_email, "level": "read"}]})
    assert await _names(ns, member, "product") == {"Penrose Pellet Extruder"}
    assert await _get_status(ns, member, product["id"]) == 200
    hits = await _in_tenant(ns, ns.org_a, lambda db: resolver.resolve_alias(db, member, "PX-1"))
    assert [h["id"] for h in hits] == [product["id"]]
    # Read is not write: the member still cannot make a topic.
    refused = await _call(ns, member, "POST", "/knowledge/entities",
                          {"type": "topic", "name": "Pellets", "space_id": space})
    assert refused.status_code == 403


# ── Two organizations ───────────────────────────────────────────────────────

async def test_org_b_sees_nothing_of_org_a(ns, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    tag = uuid.uuid4().hex[:6]
    person = _add_person(ns, ns.org_a, f"Esha {tag}")
    project = _add_project(ns, ns.org_a, f"Org A work {tag}", "org")
    company = _add_company(ns, ns.org_a, f"A customer {tag}")
    _set_crm_access(ns, ns.org_a, "everyone", [])
    _set_crm_access(ns, ns.org_b, "everyone", [])
    ids = [await _ensure(ns, ns.org_a, t, r) for t, r in
           (("people", person), ("pm_projects", project), ("crm_companies", company))]
    assert all(ids)

    # Org B cannot point at a record of org A. No row is made.
    for table, ref in (("people", person), ("pm_projects", project),
                       ("crm_companies", company), ("organization", ns.org_a)):
        assert await _ensure(ns, ns.org_b, table, ref) is None, table
        assert [r.org for r in _entity_rows(ns, ref)] in ([], [ns.org_a]), table

    b = _principal(_address("b"), ns.org_b, PEOPLE, PROJECTS, CRM, WORKFLOWS, CAPABILITY)
    _add_member(ns, ns.org_b, b.email)
    listed = (await _ok(ns, b, "GET", "/knowledge/entities"))["entities"]
    assert not [r for r in listed if tag in (r["name"] or "")]
    assert {r["ref_id"] for r in listed if r["type"] == "company"} == {ns.org_b}
    for eid in ids:
        assert await _get_status(ns, b, eid) == 404

    # The same address bound to org A sees them.
    a = _principal(_address("a"), ns.org_a, PEOPLE, PROJECTS, CRM, WORKFLOWS)
    _add_member(ns, ns.org_a, a.email)
    names = {r["name"] for r in (await _ok(ns, a, "GET", "/knowledge/entities"))["entities"]}
    assert {f"Esha {tag}", f"Org A work {tag}", f"A customer {tag}"} <= names


# ── The person resolver ─────────────────────────────────────────────────────

async def test_the_person_resolver(ns, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    from gateway.kb_entities import resolver

    tag = uuid.uuid4().hex[:6]
    priya_s = _add_person(ns, ns.org_a, f"Priya{tag} Sharma", _address("priya.s"))
    _add_person(ns, ns.org_a, f"Priya{tag} Nair", _address("priya.n"))
    arjun_mail = _address("arjun")
    arjun = _add_person(ns, ns.org_a, f"Arjun{tag} Rao", arjun_mail)
    eid = await _ensure(ns, ns.org_a, "people", arjun)
    _set_alias(ns, eid, f"the boss {tag}")
    # A person of org B with the same first name never counts.
    _add_person(ns, ns.org_b, f"Arjun{tag} Other", _address("arjun.b"))

    member = _principal(_address("m"), ns.org_a, PEOPLE)

    async def resolve(words: str, user: Any = member) -> dict:
        return await _in_tenant(ns, ns.org_a,
                                lambda db: resolver.resolve_person(db, user, words))

    by_alias = await resolve(f"The Boss {tag}")
    assert by_alias["status"] == "one" and by_alias["via"] == "alias"
    assert by_alias["matches"] == [{"people_id": arjun, "entity_id": eid,
                                    "name": f"Arjun{tag} Rao"}]

    by_name = await resolve(f"arjun{tag}")
    assert by_name["status"] == "one" and by_name["matches"][0]["people_id"] == arjun

    by_mail = await resolve(arjun_mail.upper())
    assert by_mail["status"] == "one" and by_mail["matches"][0]["people_id"] == arjun

    two = await resolve(f"Priya{tag}")
    assert two["status"] == "ask"
    assert len(two["matches"]) == 2 and priya_s in {m["people_id"] for m in two["matches"]}

    assert (await resolve(f"nobody{tag}"))["status"] == "none"
    refused = await resolve(f"arjun{tag}", _principal(_address("x"), ns.org_a))
    assert refused == {"status": "none", "via": None, "matches": [], "directory": False}
