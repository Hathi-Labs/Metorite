"""R8 — Knowledge spaces and pages on a real database (WS-61 KB-1).

Spec: ``project-docs/specs/knowledge_base.md`` §4.1, §4.2, §5.1, §5.2, §5.4,
§12 and the KB-1 row of §17.

What this proves against a REAL Postgres, through the real ``/knowledge``
router, as the NON-privileged role of the phase-4 rehearsal
(``acb_app_h3rls``: no superuser, no BYPASSRLS), on a database with the whole
ladder, the four generated phases and two organizations:

- two orgs, two spaces, one grant each: the other org and the other space
  see nothing (the KB-1 done-when);
- the grant subjects are ``org``, the member's email and the member's groups
  from ``_MY_GROUPS_SQL``, under row level security;
- ``knowledge:manage`` reads each published page of each space that is not a
  ``me`` space, and never a draft that is not its own, nor a ``me`` space.
  ``data:org:read`` gives nothing;
- the author sees a draft, a named reviewer sees it, and nobody else does;
- each write appends one version with its reason and its channel;
- a page in another org or not visible answers 404, never 403;
- two concurrent first writes make one ``me`` space;
- after the ladder, ``admin`` holds ``knowledge:manage`` and ``manager`` and
  ``member`` do not, in every organization and in a new one;
- the five tables force row level security, and the migration replays.

A hermetic fake agrees with any SQL it is handed, so none of these could be
proved without the database. ``tests/unit/test_kb_visibility_one_seam.py``
holds the one-seam rule without one.
"""

from __future__ import annotations

import asyncio
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from tests.unit._tenant_ladder import apply_ladder, tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    _URL,
    app_engine,
    promoted,
)

# ⚠️ Every test here needs the real database. Without this gate the suite
# ERRORS where no ladder URL is set (the deploy run), and the deploy stops.
pytestmark = _DB_GATE

_MIGRATIONS = Path(__file__).resolve().parents[2] / "infra" / "postgres"

_TABLES = ("kb_spaces", "kb_space_grants", "kb_pages", "kb_page_versions", "kb_links")

CAPABILITY = "knowledge:manage"


def _kb_migration() -> Path:
    """The migration that creates ``kb_spaces``, found by content (R1)."""
    found = [
        p for p in sorted(_MIGRATIONS.glob("*.sql"))
        if p.name != "schema.generated.sql"
        and "CREATE TABLE IF NOT EXISTS kb_spaces" in p.read_text(encoding="utf-8")
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


def _address(tag: str) -> str:
    return f"{tag}.{uuid.uuid4().hex[:8]}@kb1-r8.test"


def _principal(email: str, org: str, *perms: str) -> Any:
    from acb_auth import UserContext, UserRole, build_access

    return UserContext(
        email=email, role=UserRole.EMPLOYEE,
        access=build_access(["feature:knowledge", *perms]),
        organization_id=org,
    )


async def _calls(
    p: Any, user: Any, requests: list[tuple[str, str, dict | None]],
    headers: dict[str, str] | None = None,
) -> list[Any]:
    """Requests through the real ``/knowledge`` router, as *user*, at once.

    One engine scope holds them all, because the scope resets the process-wide
    engine. The override binds the tenant from the user, which is what
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
                transport=transport, base_url="http://kb.test",
            ) as client:
                return list(await asyncio.gather(*[
                    client.request(method, path, json=body, headers=headers)
                    for method, path, body in requests
                ]))
    finally:
        release_tenant(scope)


async def _call(
    p: Any, user: Any, method: str, path: str, body: dict | None = None,
    headers: dict[str, str] | None = None,
) -> Any:
    """One request through the real ``/knowledge`` router, as *user*."""
    return (await _calls(p, user, [(method, path, body)], headers))[0]


async def _ok(p: Any, user: Any, method: str, path: str, body: dict | None = None,
              status: int = 200, headers: dict[str, str] | None = None) -> Any:
    response = await _call(p, user, method, path, body, headers)
    assert response.status_code == status, (method, path, response.status_code, response.text)
    return response.json()


@pytest.fixture
def ns(promoted, monkeypatch):  # noqa: F811 — a fixture
    """The promoted catalog with the switch on, cleaned after each test."""
    from acb_common import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "knowledge_enabled", True, raising=False)
    monkeypatch.setattr(settings, "knowledge_orgs", "*", raising=False)
    made: dict[str, list[str]] = {"users": [], "groups": []}
    promoted.made = made
    yield promoted
    with promoted.admin_engine.begin() as c:
        # KB-3: a publish writes chunks, and no foreign key removes them.
        for table in ("kb_chunks", "kb_links", "kb_page_versions"):
            c.execute(text(f"DELETE FROM {table}"))
        c.execute(text("UPDATE kb_pages SET parent_id = NULL"))
        for table in ("kb_pages", "kb_space_grants", "kb_spaces"):
            c.execute(text(f"DELETE FROM {table}"))
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


async def _space(p: Any, admin: Any, name: str, grants: list[dict]) -> str:
    made = await _ok(p, admin, "POST", "/knowledge/spaces",
                     {"name": name, "grants": grants}, status=201)
    return made["id"]


async def _published(p: Any, writer: Any, space_id: str, title: str) -> str:
    page = await _ok(p, writer, "POST", "/knowledge/pages",
                     {"space_id": space_id, "title": title, "body_md": f"# {title}"},
                     status=201)
    await _ok(p, writer, "POST", f"/knowledge/pages/{page['id']}/publish")
    return page["id"]


async def _titles(p: Any, user: Any) -> set[str]:
    listed = await _ok(p, user, "GET", "/knowledge/pages")
    return {row["title"] for row in listed["pages"]}


async def _status(p: Any, user: Any, page_id: str) -> int:
    return (await _call(p, user, "GET", f"/knowledge/pages/{page_id}")).status_code


# ── The KB-1 done-when: two orgs, two spaces, one grant each ───────────────

async def test_the_other_org_and_the_other_space_see_nothing(ns, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    seller, outsider, chief_b = _address("seller"), _address("Outsider"), _address("chief-b")
    seller_id = _add_member(ns, ns.org_a, seller)
    _add_member(ns, ns.org_a, outsider)
    _add_member(ns, ns.org_b, chief_b)
    _add_group(ns, ns.org_a, "sales", [seller_id])

    admin_a = _principal(_address("admin-a"), ns.org_a, CAPABILITY)
    admin_b = _principal(_address("admin-b"), ns.org_b, CAPABILITY)

    # Org A: space X granted to a group, space Y granted to one email.
    sales = await _space(ns, admin_a, "Sales", [{"subject": "group:sales", "level": "read"}])
    ops = await _space(ns, admin_a, "Ops", [{"subject": outsider, "level": "read"}])
    on_sales = await _published(ns, admin_a, sales, "Sales playbook")
    on_ops = await _published(ns, admin_a, ops, "Ops runbook")
    # Org B: one space granted to the whole org.
    hb = await _space(ns, admin_b, "Handbook", [{"subject": "org", "level": "read"}])
    on_b = await _published(ns, admin_b, hb, "B handbook")

    seller_a = _principal(seller, ns.org_a)
    outsider_a = _principal(outsider, ns.org_a)
    member_b = _principal(chief_b, ns.org_b)

    assert await _titles(ns, seller_a) == {"Sales playbook"}
    assert await _titles(ns, outsider_a) == {"Ops runbook"}
    assert await _titles(ns, member_b) == {"B handbook"}

    # The other space and the other org answer 404, never 403.
    assert await _status(ns, seller_a, on_ops) == 404
    assert await _status(ns, outsider_a, on_sales) == 404
    assert await _status(ns, member_b, on_sales) == 404
    assert await _status(ns, member_b, on_ops) == 404
    assert await _status(ns, seller_a, on_b) == 404
    assert await _status(ns, seller_a, on_sales) == 200

    # The space list shows the same split, with the counts of visible pages.
    seller_spaces = {s["slug"]: s for s in (await _ok(
        ns, seller_a, "GET", "/knowledge/spaces"))["spaces"]}
    assert set(seller_spaces) == {"company", "team-sales"}
    assert seller_spaces["team-sales"]["page_count"] == 1
    assert seller_spaces["team-sales"]["level"] == "read"
    b_spaces = {s["slug"] for s in (await _ok(
        ns, member_b, "GET", "/knowledge/spaces"))["spaces"]}
    assert b_spaces == {"company", "team-handbook"}

    # A write by the other org on org A's page is a 404 too.
    for method, path, body in (
        ("PATCH", f"/knowledge/pages/{on_sales}", {"title": "x"}),
        ("POST", f"/knowledge/pages/{on_sales}/archive", None),
        ("PATCH", f"/knowledge/spaces/{sales}", {"name": "x"}),
        ("PUT", f"/knowledge/spaces/{sales}/grants", {"grants": []}),
    ):
        assert (await _call(ns, admin_b, method, path, body)).status_code == 404


async def test_a_group_of_another_org_does_not_count(ns, app_engine) -> None:  # noqa: F811
    """The group read runs under row level security in the caller's tenant.
    A ``sales`` group of org B names nobody in org A."""
    _assert_non_priv(app_engine)
    seller = _address("seller")
    seller_id = _add_member(ns, ns.org_b, seller)
    _add_group(ns, ns.org_b, "sales", [seller_id])
    admin_a = _principal(_address("admin-a"), ns.org_a, CAPABILITY)
    sales = await _space(ns, admin_a, "Sales", [{"subject": "group:sales", "level": "read"}])
    await _published(ns, admin_a, sales, "Sales playbook")
    # The same person, if bound to org A, finds no group there.
    assert await _titles(ns, _principal(seller, ns.org_a)) == set()


async def test_the_company_space_is_made_once_with_an_org_grant(ns, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    member = _principal(_address("m"), ns.org_a)
    first = await _calls(ns, member, [("GET", "/knowledge/spaces", None)] * 4)
    assert [r.status_code for r in first] == [200] * 4, [r.text for r in first]
    with ns.admin_engine.connect() as c:
        rows = c.execute(text(
            "SELECT s.organization_id::text, g.subject, g.level FROM kb_spaces s "
            "JOIN kb_space_grants g ON g.space_id = s.id WHERE s.slug = 'company'"),
        ).all()
    assert [tuple(r) for r in rows] == [(ns.org_a, "org", "read")]

    admin = _principal(_address("admin"), ns.org_a, CAPABILITY)
    company = next(s for s in (await _ok(ns, admin, "GET", "/knowledge/spaces"))["spaces"]
                   if s["slug"] == "company")
    assert company["owner_email"] is None, "the first visitor must not own the company space"
    await _published(ns, admin, company["id"], "Refund policy")
    # Every member of org A reads it, through the `org` grant. Org B does not.
    assert await _titles(ns, _principal(_address("x"), ns.org_a)) == {"Refund policy"}
    assert await _titles(ns, _principal(_address("y"), ns.org_b)) == set()
    # A member cannot write in it: `org` read is not write.
    refused = await _call(ns, member, "POST", "/knowledge/pages",
                          {"space_id": company["id"], "title": "Mine"})
    assert refused.status_code == 403


# ── knowledge:manage and data:org:read ──────────────────────────────────────

async def test_manage_reads_published_pages_only_and_no_me_space(ns, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    admin = _principal(_address("admin"), ns.org_a, CAPABILITY)
    writer_email = _address("writer")
    writer = _principal(writer_email, ns.org_a)
    space = await _space(ns, admin, "Eng", [{"subject": writer_email, "level": "write"}])
    published = await _published(ns, writer, space, "Eng published")
    draft = await _ok(ns, writer, "POST", "/knowledge/pages",
                      {"space_id": space, "title": "Eng draft"}, status=201)
    mine = await _ok(ns, writer, "POST", "/knowledge/pages", {"title": "My note"}, status=201)
    # Published in the writer's own me space: still the writer's alone.
    await _ok(ns, writer, "POST", f"/knowledge/pages/{mine['id']}/publish")

    # A second admin, who made nothing, reads what manage allows.
    other_admin = _principal(_address("admin2"), ns.org_a, CAPABILITY)
    assert await _titles(ns, other_admin) == {"Eng published"}
    assert await _status(ns, other_admin, published) == 200
    assert await _status(ns, other_admin, draft["id"]) == 404
    assert await _status(ns, other_admin, mine["id"]) == 404
    admin_spaces = {s["kind"] for s in (await _ok(
        ns, other_admin, "GET", "/knowledge/spaces"))["spaces"]}
    assert "me" not in admin_spaces

    # data:org:read gives nothing in Knowledge.
    reader = _principal(_address("reader"), ns.org_a, "data:org:read")
    assert await _titles(ns, reader) == set()
    assert await _status(ns, reader, published) == 404

    # The writer sees all three: the published page, its draft, its own note.
    assert await _titles(ns, writer) == {"Eng published", "Eng draft", "My note"}


async def test_the_published_count_feeds_the_index_block(ns, app_engine) -> None:  # noqa: F811
    """WS-61 KB-4a (§6.5). ``published_count`` counts the published pages the
    member can read, and a draft never. The assistant's index block reads it,
    so a member who reads only drafts gets no block. ``GET /knowledge/pages/{id}``
    names the space, for ``read_page``."""
    from acb_skills.knowledge_tools import INDEX_HEADER, format_index

    _assert_non_priv(app_engine)
    admin = _principal(_address("admin"), ns.org_a, CAPABILITY)
    writer_email = _address("writer")
    writer = _principal(writer_email, ns.org_a)
    reader = _principal(_address("reader"), ns.org_a)
    space = await _space(ns, admin, "Policies", [
        {"subject": "org", "level": "read"},
        {"subject": writer_email, "level": "write"},
    ])
    await _ok(ns, writer, "POST", "/knowledge/pages",
              {"space_id": space, "title": "Refund draft", "body_md": "SECRET DRAFT"},
              status=201)

    # One draft: the writer counts it as a page, and nobody counts it as published.
    by_writer = {s["name"]: s for s in (await _ok(
        ns, writer, "GET", "/knowledge/spaces"))["spaces"]}
    assert by_writer["Policies"]["page_count"] == 1
    assert by_writer["Policies"]["published_count"] == 0
    assert format_index(await _ok(ns, writer, "GET", "/knowledge/spaces")) == ""
    assert format_index(await _ok(ns, reader, "GET", "/knowledge/spaces")) == ""

    published = await _published(ns, writer, space, "Refund policy")
    by_reader = {s["name"]: s for s in (await _ok(
        ns, reader, "GET", "/knowledge/spaces"))["spaces"]}
    assert by_reader["Policies"]["page_count"] == 1
    assert by_reader["Policies"]["published_count"] == 1
    block = format_index(await _ok(ns, reader, "GET", "/knowledge/spaces"))
    assert block.splitlines() == [INDEX_HEADER, "- Policies (1 page)"]
    assert "SECRET" not in block and "Refund" not in block

    # Another org's member reads nothing here, so no block.
    stranger = _principal(_address("stranger"), ns.org_b)
    assert format_index(await _ok(ns, stranger, "GET", "/knowledge/spaces")) == ""

    page = await _ok(ns, reader, "GET", f"/knowledge/pages/{published}")
    assert page["space_name"] == "Policies"


async def test_a_member_cannot_make_a_space(ns, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    member = _principal(_address("m"), ns.org_a)
    response = await _call(ns, member, "POST", "/knowledge/spaces", {"name": "Mine"})
    assert response.status_code == 403
    with ns.admin_engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM kb_spaces WHERE kind = 'team'")).scalar() == 0


# ── Drafts, authors and reviewers ───────────────────────────────────────────

async def test_a_draft_is_its_author_and_named_reviewers_only(ns, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    admin = _principal(_address("admin"), ns.org_a, CAPABILITY)
    author_email, reviewer_email, bystander_email = (
        _address("author"), _address("Reviewer"), _address("bystander"))
    space = await _space(ns, admin, "Docs", [
        {"subject": "org", "level": "read"},
        {"subject": author_email, "level": "write"},
    ])
    author = _principal(author_email, ns.org_a)
    draft = await _ok(ns, author, "POST", "/knowledge/pages", {
        "space_id": space, "title": "Pricing draft", "reviewers": [reviewer_email],
    }, status=201)
    assert draft["reviewers"] == [reviewer_email.lower()]
    assert await _status(ns, author, draft["id"]) == 200
    assert await _status(ns, _principal(reviewer_email.lower(), ns.org_a), draft["id"]) == 200
    assert await _status(ns, _principal(bystander_email, ns.org_a), draft["id"]) == 404
    assert await _titles(ns, _principal(bystander_email, ns.org_a)) == set()
    # A reviewer may not write the draft: reading is not writing.
    refused = await _call(ns, _principal(reviewer_email, ns.org_a), "PATCH",
                          f"/knowledge/pages/{draft['id']}", {"title": "x"})
    assert refused.status_code == 403
    # Published, the org reads it.
    await _ok(ns, author, "POST", f"/knowledge/pages/{draft['id']}/publish")
    assert await _status(ns, _principal(bystander_email, ns.org_a), draft["id"]) == 200


async def test_a_reviewer_with_no_grant_gets_no_space_name(ns, app_engine) -> None:  # noqa: F811
    """Review of KB-4a, P2. A named reviewer reads a draft in a space that
    grants them nothing. The page answers, and the space's name does not."""
    _assert_non_priv(app_engine)
    admin = _principal(_address("admin"), ns.org_a, CAPABILITY)
    author_email, reviewer_email = _address("author"), _address("reviewer")
    space = await _space(ns, admin, "Board papers", [
        {"subject": author_email, "level": "write"},
    ])
    author = _principal(author_email, ns.org_a)
    draft = await _ok(ns, author, "POST", "/knowledge/pages", {
        "space_id": space, "title": "Q3 draft", "reviewers": [reviewer_email],
    }, status=201)
    seen = await _ok(ns, _principal(reviewer_email, ns.org_a), "GET",
                     f"/knowledge/pages/{draft['id']}")
    assert seen["level"] is None
    assert seen["space_name"] is None
    own = await _ok(ns, author, "GET", f"/knowledge/pages/{draft['id']}")
    assert own["space_name"] == "Board papers"


async def test_a_me_space_has_no_reviewers_and_no_grants(ns, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    me_email = _address("me")
    member = _principal(me_email, ns.org_a)
    refused = await _call(ns, member, "POST", "/knowledge/pages",
                          {"title": "Note", "reviewers": [_address("r")]})
    assert refused.status_code == 422
    page = await _ok(ns, member, "POST", "/knowledge/pages", {"title": "Note"}, status=201)
    space_id = page["space_id"]
    grants = await _call(ns, member, "PUT", f"/knowledge/spaces/{space_id}/grants",
                         {"grants": [{"subject": "org", "level": "read"}]})
    assert grants.status_code == 422
    # Another member, and an admin, find no such space.
    admin = _principal(_address("admin"), ns.org_a, CAPABILITY)
    for other in (_principal(_address("o"), ns.org_a), admin):
        assert (await _call(ns, other, "PUT", f"/knowledge/spaces/{space_id}/grants",
                            {"grants": []})).status_code == 404
        assert (await _call(ns, other, "PATCH", f"/knowledge/spaces/{space_id}",
                            {"name": "Mine"})).status_code == 404


# ── Writes append versions with the reason and the channel ──────────────────

async def test_each_write_appends_a_version_with_reason_and_via(ns, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    admin = _principal(_address("admin"), ns.org_a, CAPABILITY)
    writer_email = _address("writer")
    writer = _principal(writer_email, ns.org_a)
    space = await _space(ns, admin, "Wiki", [{"subject": writer_email, "level": "write"}])
    page = await _ok(ns, writer, "POST", "/knowledge/pages",
                     {"space_id": space, "title": "One", "body_md": "first"}, status=201)
    pid = page["id"]
    assert page["version"] == 1
    edited = await _ok(ns, writer, "PATCH", f"/knowledge/pages/{pid}",
                       {"body_md": "second"}, headers={"X-Actor-Via": "chat:knowledge"})
    assert edited["version"] == 2 and edited["body_md"] == "second"
    await _ok(ns, writer, "POST", f"/knowledge/pages/{pid}/publish")
    reverted = await _ok(ns, writer, "POST", f"/knowledge/pages/{pid}/revert", {"version": 1})
    assert reverted["body_md"] == "first"
    child = await _ok(ns, writer, "POST", "/knowledge/pages",
                      {"space_id": space, "title": "Two"}, status=201)
    moved = await _ok(ns, writer, "POST", f"/knowledge/pages/{child['id']}/move",
                      {"parent_id": pid})
    assert moved["parent_id"] == pid
    # A writer does not archive: that needs manage.
    assert (await _call(ns, writer, "POST", f"/knowledge/pages/{pid}/archive")).status_code == 403
    await _ok(ns, admin, "POST", f"/knowledge/pages/{pid}/archive")

    with ns.admin_engine.connect() as c:
        rows = c.execute(text(
            "SELECT version, reason, via, author_email FROM kb_page_versions "
            "WHERE page_id = CAST(:p AS uuid) ORDER BY version"), {"p": pid}).all()
    assert [tuple(r) for r in rows] == [
        (1, "create", "web", writer_email),
        (2, "edit", "chat", writer_email),
        (3, "publish", "web", writer_email),
        (4, "revert", "web", writer_email),
        (5, "archive", "web", admin.email),
    ]
    # The archive view: a manager sees the archived page and its history. The
    # author, a writer with no manage, does not (fix round 2, P1-e).
    assert await _status(ns, writer, pid) == 404
    detail = await _ok(ns, admin, "GET", f"/knowledge/pages/{pid}")
    assert detail["state"] == "archived"
    assert [v["reason"] for v in detail["versions"]] == [
        "archive", "revert", "publish", "edit", "create"]


async def test_a_move_refuses_a_cycle(ns, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    member = _principal(_address("m"), ns.org_a)
    top = await _ok(ns, member, "POST", "/knowledge/pages", {"title": "Top"}, status=201)
    mid = await _ok(ns, member, "POST", "/knowledge/pages",
                    {"title": "Mid", "parent_id": top["id"]}, status=201)
    low = await _ok(ns, member, "POST", "/knowledge/pages",
                    {"title": "Low", "parent_id": mid["id"]}, status=201)
    refused = await _call(ns, member, "POST", f"/knowledge/pages/{top['id']}/move",
                          {"parent_id": low["id"]})
    assert refused.status_code == 422
    assert (await _call(ns, member, "POST", f"/knowledge/pages/{top['id']}/move",
                        {"parent_id": top["id"]})).status_code == 422


# ── KB-2e: the place of a page in the tree ─────────────────────────────────

def _tree_rows(p: Any, space: str) -> list[tuple[str, int]]:
    """``(title, position)`` of each page in *space*, in the order of the tree."""
    with p.admin_engine.connect() as c:
        rows = c.execute(text(
            "SELECT title, position FROM kb_pages WHERE space_id = CAST(:s AS uuid) "
            "ORDER BY parent_id NULLS FIRST, position, lower(title)"), {"s": space}).all()
    return [(r.title, r.position) for r in rows]


def _versions_and_stamp(p: Any, pid: str) -> tuple[int, Any]:
    with p.admin_engine.connect() as c:
        count = c.execute(text(
            "SELECT count(*) FROM kb_page_versions WHERE page_id = CAST(:p AS uuid)"),
            {"p": pid}).scalar()
        stamp = c.execute(text(
            "SELECT updated_at FROM kb_pages WHERE id = CAST(:p AS uuid)"), {"p": pid}).scalar()
    return int(count), stamp


async def test_a_new_page_goes_last_and_a_reorder_writes_no_version(ns, app_engine) -> None:  # noqa: F811
    """The KB-2e done-when: a drop keeps its place after a reload, and a
    reorder adds no row to ``kb_page_versions`` and keeps ``updated_at``."""
    _assert_non_priv(app_engine)
    admin = _principal(_address("admin"), ns.org_a, CAPABILITY)
    writer_email = _address("writer")
    writer = _principal(writer_email, ns.org_a)
    space = await _space(ns, admin, "Tree", [{"subject": writer_email, "level": "write"}])
    ids = {}
    for title in ("Charlie", "Alpha", "Bravo"):
        ids[title] = await _published(ns, writer, space, title)
    # A create puts the page last, so the order is the order of creation.
    assert _tree_rows(ns, space) == [("Charlie", 0), ("Alpha", 1), ("Bravo", 2)]

    before = _versions_and_stamp(ns, ids["Bravo"])
    moved = await _ok(ns, writer, "POST", f"/knowledge/pages/{ids['Bravo']}/move",
                      {"position": 0})
    assert moved["position"] == 0 and "version" not in moved
    assert _tree_rows(ns, space) == [("Bravo", 0), ("Charlie", 1), ("Alpha", 2)]
    # No version and no new `updated_at`, on the page or on a sibling.
    assert _versions_and_stamp(ns, ids["Bravo"]) == before
    # A drop between two pages: Bravo goes between Charlie and Alpha.
    await _ok(ns, writer, "POST", f"/knowledge/pages/{ids['Bravo']}/move", {"position": 1})
    listed = await _ok(ns, writer, "GET", f"/knowledge/pages?space={space}")
    assert [r["title"] for r in listed["pages"]] == ["Charlie", "Bravo", "Alpha"]
    assert _versions_and_stamp(ns, ids["Bravo"]) == before
    # A place past the end is the end.
    await _ok(ns, writer, "POST", f"/knowledge/pages/{ids['Charlie']}/move", {"position": 99})
    assert _tree_rows(ns, space) == [("Bravo", 0), ("Alpha", 1), ("Charlie", 2)]
    # The same space and the same parent with no place is still nothing.
    same = await _call(ns, writer, "POST", f"/knowledge/pages/{ids['Alpha']}/move",
                       {"space_id": space})
    assert same.status_code == 422
    assert (await _call(ns, writer, "POST", f"/knowledge/pages/{ids['Alpha']}/move",
                        {"position": -1})).status_code == 422


async def test_a_nest_with_a_place_writes_the_move_version(ns, app_engine) -> None:  # noqa: F811
    """A move to another parent keeps today's version, and goes to its place."""
    _assert_non_priv(app_engine)
    member = _principal(_address("m"), ns.org_a)
    top = await _ok(ns, member, "POST", "/knowledge/pages", {"title": "Top"}, status=201)
    space = top["space_id"]
    first = await _ok(ns, member, "POST", "/knowledge/pages",
                      {"title": "First", "parent_id": top["id"]}, status=201)
    second = await _ok(ns, member, "POST", "/knowledge/pages",
                       {"title": "Second", "parent_id": top["id"]}, status=201)
    assert (first["position"], second["position"]) == (0, 1)
    loose = await _ok(ns, member, "POST", "/knowledge/pages", {"title": "Loose"}, status=201)
    moved = await _ok(ns, member, "POST", f"/knowledge/pages/{loose['id']}/move",
                      {"parent_id": top["id"], "position": 1})
    assert moved["version"] == 2 and moved["position"] == 1
    children = [r for r in (await _ok(ns, member, "GET", f"/knowledge/pages?space={space}"))["pages"]
                if r["parent_id"] == top["id"]]
    assert [(r["title"], r["position"]) for r in children] == [
        ("First", 0), ("Loose", 1), ("Second", 2)]
    # With no place, a nest puts the page last.
    other = await _ok(ns, member, "POST", "/knowledge/pages", {"title": "Other"}, status=201)
    moved = await _ok(ns, member, "POST", f"/knowledge/pages/{other['id']}/move",
                      {"parent_id": top["id"]})
    assert moved["position"] == 3
    # A page still cannot go into its own subtree, with a place or without one.
    refused = await _call(ns, member, "POST", f"/knowledge/pages/{top['id']}/move",
                          {"parent_id": first["id"], "position": 0})
    assert refused.status_code == 422


async def test_a_reader_cannot_reorder(ns, app_engine) -> None:  # noqa: F811
    """A reader sees no handle, and the gateway refuses the move as well."""
    _assert_non_priv(app_engine)
    admin = _principal(_address("admin"), ns.org_a, CAPABILITY)
    writer_email, reader_email = _address("writer"), _address("reader")
    writer = _principal(writer_email, ns.org_a)
    reader = _principal(reader_email, ns.org_a)
    space = await _space(ns, admin, "Read", [
        {"subject": writer_email, "level": "write"},
        {"subject": reader_email, "level": "read"},
    ])
    await _published(ns, writer, space, "One")
    two = await _published(ns, writer, space, "Two")
    refused = await _call(ns, reader, "POST", f"/knowledge/pages/{two}/move", {"position": 0})
    assert refused.status_code == 403
    assert _tree_rows(ns, space) == [("One", 0), ("Two", 1)]


async def test_a_hidden_sibling_keeps_its_place(ns, app_engine) -> None:  # noqa: F811
    """The place counts among the siblings the caller sees. A draft of another
    writer stays where it was, and every sibling gets 0 to n."""
    _assert_non_priv(app_engine)
    admin = _principal(_address("admin"), ns.org_a, CAPABILITY)
    one_email, two_email = _address("one"), _address("two")
    one = _principal(one_email, ns.org_a)
    two = _principal(two_email, ns.org_a)
    space = await _space(ns, admin, "Shared", [
        {"subject": one_email, "level": "write"},
        {"subject": two_email, "level": "write"},
    ])
    a = await _published(ns, one, space, "A")
    await _ok(ns, two, "POST", "/knowledge/pages",
              {"space_id": space, "title": "Hidden"}, status=201)
    await _published(ns, one, space, "B")
    # `one` sees A, B. A to place 1 is "after B" for `one`.
    await _ok(ns, one, "POST", f"/knowledge/pages/{a}/move", {"position": 1})
    assert _tree_rows(ns, space) == [("Hidden", 0), ("B", 1), ("A", 2)]
    seen = await _ok(ns, one, "GET", f"/knowledge/pages?space={space}")
    assert [r["title"] for r in seen["pages"]] == ["B", "A"]
    # A to place 0 is "before B": the page goes in just before B.
    await _ok(ns, one, "POST", f"/knowledge/pages/{a}/move", {"position": 0})
    assert _tree_rows(ns, space) == [("Hidden", 0), ("A", 1), ("B", 2)]


async def test_a_member_sees_no_gap_for_a_hidden_page(ns, app_engine) -> None:  # noqa: F811
    """Fix round, P2. The stored place counts hidden siblings. Each answer
    gives the caller only the rank among the siblings the caller sees, so no
    gap tells a member that a hidden page sits there."""
    _assert_non_priv(app_engine)
    admin = _principal(_address("admin"), ns.org_a, CAPABILITY)
    one_email, two_email = _address("one"), _address("two")
    one = _principal(one_email, ns.org_a)
    two = _principal(two_email, ns.org_a)
    space = await _space(ns, admin, "Gaps", [
        {"subject": one_email, "level": "write"},
        {"subject": two_email, "level": "write"},
    ])
    a = await _published(ns, one, space, "A")
    await _ok(ns, two, "POST", "/knowledge/pages",
              {"space_id": space, "title": "Hidden"}, status=201)
    b = await _published(ns, one, space, "B")
    # The stored places hold the hidden draft between A and B.
    assert _tree_rows(ns, space) == [("A", 0), ("Hidden", 1), ("B", 2)]

    def seen(listed: dict) -> list[tuple[str, int]]:
        return [(r["title"], r["position"]) for r in listed["pages"]]

    assert seen(await _ok(ns, one, "GET", f"/knowledge/pages?space={space}")) == [("A", 0), ("B", 1)]
    assert (await _ok(ns, one, "GET", f"/knowledge/pages/{b}"))["position"] == 1
    moved = await _ok(ns, one, "POST", f"/knowledge/pages/{a}/move", {"position": 1})
    assert moved["position"] == 1
    assert seen(await _ok(ns, one, "GET", f"/knowledge/pages?space={space}")) == [("B", 0), ("A", 1)]
    # The author of the draft sees all three, each at its rank.
    assert [p for _t, p in seen(await _ok(ns, two, "GET", f"/knowledge/pages?space={space}"))] == [0, 1, 2]


async def test_opposite_moves_between_two_spaces_do_not_deadlock(ns, app_engine) -> None:  # noqa: F811
    """Fix round, P2. X goes from S1 to S2 while Y goes from S2 to S1. Each
    move takes both tree locks in one order, so neither waits on the other
    in a circle, and both land."""
    _assert_non_priv(app_engine)
    admin = _principal(_address("admin"), ns.org_a, CAPABILITY)
    writer_email = _address("writer")
    writer = _principal(writer_email, ns.org_a)
    grant = [{"subject": writer_email, "level": "write"}]
    s1 = await _space(ns, admin, "One", grant)
    s2 = await _space(ns, admin, "Two", grant)
    x = await _published(ns, writer, s1, "X")
    y = await _published(ns, writer, s2, "Y")
    for title in ("P1", "P2", "P3"):
        await _published(ns, writer, s1, title)
        await _published(ns, writer, s2, title.replace("P", "Q"))
    here, there = s1, s2
    for _round in range(4):
        responses = await _calls(ns, writer, [
            ("POST", f"/knowledge/pages/{x}/move", {"space_id": there, "position": 0}),
            ("POST", f"/knowledge/pages/{y}/move", {"space_id": here, "position": 0}),
        ])
        assert [r.status_code for r in responses] == [200, 200], [r.text for r in responses]
        here, there = there, here
    # Four rounds: each page is back where it began, first in its tree. A
    # page that leaves a tree leaves a gap there, so the places are distinct
    # and need not be contiguous. The visible rank hides the gap.
    for space, first in ((s1, "X"), (s2, "Y")):
        rows = _tree_rows(ns, space)
        assert rows[0] == (first, 0) and len(rows) == 4
        assert len({pos for _t, pos in rows}) == len(rows)
        listed = await _ok(ns, writer, "GET", f"/knowledge/pages?space={space}")
        assert [r["position"] for r in listed["pages"]] == [0, 1, 2, 3]


async def test_concurrent_reorders_leave_one_order(ns, app_engine) -> None:  # noqa: F811
    """The tree lock: moves at once in one space end in 0 to n, each place once."""
    _assert_non_priv(app_engine)
    member = _principal(_address("m"), ns.org_a)
    made = []
    for title in ("P0", "P1", "P2", "P3", "P4", "P5"):
        made.append(await _ok(ns, member, "POST", "/knowledge/pages", {"title": title},
                              status=201))
    space = made[0]["space_id"]
    responses = await _calls(ns, member, [
        ("POST", f"/knowledge/pages/{made[5]['id']}/move", {"position": 0}),
        ("POST", f"/knowledge/pages/{made[0]['id']}/move", {"position": 5}),
        ("POST", f"/knowledge/pages/{made[3]['id']}/move", {"position": 1}),
        ("POST", f"/knowledge/pages/{made[2]['id']}/move", {"position": 4}),
    ])
    assert [r.status_code for r in responses] == [200, 200, 200, 200]
    places = sorted(position for _title, position in _tree_rows(ns, space))
    assert places == [0, 1, 2, 3, 4, 5]


# ── Fix round 1 ─────────────────────────────────────────────────────────────

async def _me_space(p: Any, user: Any) -> str:
    note = await _ok(p, user, "POST", "/knowledge/pages", {"title": "Home"}, status=201)
    return note["space_id"]


async def test_a_move_to_another_space_hides_the_private_history(ns, app_engine) -> None:  # noqa: F811
    """P1-a. A page written in a ``me`` space and then shared does not share
    its private versions: not in the version list, not through revert."""
    _assert_non_priv(app_engine)
    admin = _principal(_address("admin"), ns.org_a, CAPABILITY)
    author_email, writer_email = _address("author"), _address("writer")
    author = _principal(author_email, ns.org_a)
    writer = _principal(writer_email, ns.org_a)
    eng = await _space(ns, admin, "Eng", [
        {"subject": author_email, "level": "write"},
        {"subject": writer_email, "level": "write"},
    ])
    page = await _ok(ns, author, "POST", "/knowledge/pages",
                     {"title": "Plan", "body_md": "private salary notes"}, status=201)
    pid = page["id"]
    await _ok(ns, author, "PATCH", f"/knowledge/pages/{pid}", {"body_md": "the plan"})
    moved = await _ok(ns, author, "POST", f"/knowledge/pages/{pid}/move", {"space_id": eng})
    assert moved["version"] == 3 and moved["history_from"] == 3
    await _ok(ns, author, "POST", f"/knowledge/pages/{pid}/publish")

    for reader in (writer, author):
        detail = await _ok(ns, reader, "GET", f"/knowledge/pages/{pid}")
        assert [v["version"] for v in detail["versions"]] == [4, 3]
    refused = await _call(ns, writer, "POST", f"/knowledge/pages/{pid}/revert", {"version": 1})
    assert refused.status_code == 404
    page_now = await _ok(ns, writer, "GET", f"/knowledge/pages/{pid}")
    assert page_now["body_md"] == "the plan"
    assert (await _call(ns, writer, "POST", f"/knowledge/pages/{pid}/revert",
                        {"version": 3})).status_code == 200

    # Back in the author's own `me` space, the author has the whole history.
    home = await _me_space(ns, author)
    await _ok(ns, author, "POST", f"/knowledge/pages/{pid}/move", {"space_id": home})
    detail = await _ok(ns, author, "GET", f"/knowledge/pages/{pid}")
    assert [v["version"] for v in detail["versions"]] == [6, 5, 4, 3, 2, 1]


async def test_an_author_who_lost_write_cannot_edit_or_publish(ns, app_engine) -> None:  # noqa: F811
    """P1-b. Authorship is no bypass. The draft stays readable to its author,
    and the author can take it home to their own ``me`` space."""
    _assert_non_priv(app_engine)
    admin = _principal(_address("admin"), ns.org_a, CAPABILITY)
    author_email = _address("author")
    author = _principal(author_email, ns.org_a)
    space = await _space(ns, admin, "Ops", [{"subject": author_email, "level": "write"}])
    draft = await _ok(ns, author, "POST", "/knowledge/pages",
                      {"space_id": space, "title": "Runbook"}, status=201)
    pid = draft["id"]
    await _ok(ns, admin, "PUT", f"/knowledge/spaces/{space}/grants",
              {"grants": [{"subject": author_email, "level": "read"}]})

    assert await _status(ns, author, pid) == 200
    for method, path, body in (
        ("PATCH", f"/knowledge/pages/{pid}", {"body_md": "changed"}),
        ("POST", f"/knowledge/pages/{pid}/publish", None),
        ("POST", f"/knowledge/pages/{pid}/revert", {"version": 1}),
        ("POST", f"/knowledge/pages/{pid}/archive", None),
    ):
        response = await _call(ns, author, method, path, body)
        assert response.status_code == 403, (method, path, response.text)
    with ns.admin_engine.connect() as c:
        row = c.execute(text("SELECT state, body_md FROM kb_pages WHERE id = CAST(:p AS uuid)"),
                        {"p": pid}).first()
    assert tuple(row) == ("draft", "")

    home = await _me_space(ns, author)
    moved = await _ok(ns, author, "POST", f"/knowledge/pages/{pid}/move", {"space_id": home})
    assert moved["space_id"] == home
    await _ok(ns, author, "PATCH", f"/knowledge/pages/{pid}", {"body_md": "mine now"})


async def test_a_space_manager_cannot_grant_org(ns, app_engine) -> None:  # noqa: F811
    """P1-c. A ``manage`` grant changes groups and emails. Only
    ``knowledge:manage`` adds, changes or removes the ``org`` subject."""
    _assert_non_priv(app_engine)
    admin = _principal(_address("admin"), ns.org_a, CAPABILITY)
    lead_email, peer_email = _address("lead"), _address("peer")
    lead = _principal(lead_email, ns.org_a)
    space = await _space(ns, admin, "Team", [{"subject": lead_email, "level": "manage"}])
    path = f"/knowledge/spaces/{space}/grants"
    lead_grant = {"subject": lead_email, "level": "manage"}

    for level in ("read", "write", "manage"):
        refused = await _call(ns, lead, "PUT", path,
                              {"grants": [lead_grant, {"subject": "org", "level": level}]})
        assert refused.status_code == 403, refused.text
    await _ok(ns, lead, "PUT", path, {"grants": [
        lead_grant, {"subject": "group:eng", "level": "write"},
        {"subject": peer_email, "level": "read"}]})

    # A malformed email is a 422 at the boundary, never a 500 from the CHECK.
    for bad in ("a@b@c", "a b@c", "@x"):
        response = await _call(ns, admin, "PUT", path,
                               {"grants": [{"subject": bad, "level": "read"}]})
        assert response.status_code == 422, (bad, response.status_code)
    await _ok(ns, admin, "PUT", path, {"grants": [lead_grant, {"subject": "org", "level": "read"}]})
    # An unchanged `org` entry is fine. A changed or removed one is not.
    await _ok(ns, lead, "PUT", path, {"grants": [
        lead_grant, {"subject": "org", "level": "read"}, {"subject": peer_email, "level": "write"}]})
    assert (await _call(ns, lead, "PUT", path, {"grants": [
        lead_grant, {"subject": "org", "level": "write"}]})).status_code == 403
    assert (await _call(ns, lead, "PUT", path, {"grants": [lead_grant]})).status_code == 403
    with ns.admin_engine.connect() as c:
        org_level = c.execute(text(
            "SELECT level FROM kb_space_grants WHERE space_id = CAST(:s AS uuid) "
            "AND subject = 'org'"), {"s": space}).scalar()
    assert org_level == "read"


async def test_a_manager_sees_and_restores_an_archived_page(ns, app_engine) -> None:  # noqa: F811
    """P1-d. An archived page shows to a manager of its space in the archive
    view (§4.2), and a manager returns it to draft."""
    _assert_non_priv(app_engine)
    admin = _principal(_address("admin"), ns.org_a, CAPABILITY)
    writer_email, lead_email, reader_email = (
        _address("writer"), _address("lead"), _address("reader"))
    writer = _principal(writer_email, ns.org_a)
    lead = _principal(lead_email, ns.org_a)
    reader = _principal(reader_email, ns.org_a)
    space = await _space(ns, admin, "Docs", [
        {"subject": writer_email, "level": "write"},
        {"subject": lead_email, "level": "manage"},
        {"subject": reader_email, "level": "read"},
    ])
    pid = await _published(ns, writer, space, "Old policy")
    other_admin = _principal(_address("admin2"), ns.org_a, CAPABILITY)
    await _ok(ns, other_admin, "POST", f"/knowledge/pages/{pid}/archive")

    for manager in (other_admin, lead):
        detail = await _ok(ns, manager, "GET", f"/knowledge/pages/{pid}")
        assert detail["state"] == "archived"
        archive = await _ok(ns, manager, "GET", f"/knowledge/pages?space={space}&state=archived")
        assert [r["id"] for r in archive["pages"]] == [pid]
    assert await _status(ns, reader, pid) == 404
    assert await _status(ns, writer, pid) == 404  # authorship opens no archive

    # The writer cannot see the archived page, so restore is a 404 for them.
    assert (await _call(ns, writer, "POST", f"/knowledge/pages/{pid}/restore")).status_code == 404
    restored = await _ok(ns, lead, "POST", f"/knowledge/pages/{pid}/restore")
    # A published page comes back published, so every reader has it again.
    assert restored["state"] == "published" and restored["archived_at"] is None
    assert restored["archived_from_state"] is None
    for member in (lead, reader, writer):
        assert await _status(ns, member, pid) == 200
    assert (await _call(ns, lead, "POST", f"/knowledge/pages/{pid}/restore")).status_code == 409
    with ns.admin_engine.connect() as c:
        reason = c.execute(text(
            "SELECT reason FROM kb_page_versions WHERE page_id = CAST(:p AS uuid) "
            "ORDER BY version DESC LIMIT 1"), {"p": pid}).scalar()
    assert reason == "restore"


async def test_an_archived_me_page_stays_its_owners(ns, app_engine) -> None:  # noqa: F811
    """The archive arm never opens a ``me`` space, also to an admin."""
    _assert_non_priv(app_engine)
    owner = _principal(_address("owner"), ns.org_a)
    note = await _ok(ns, owner, "POST", "/knowledge/pages", {"title": "Mine"}, status=201)
    await _ok(ns, owner, "POST", f"/knowledge/pages/{note['id']}/archive")
    admin = _principal(_address("admin"), ns.org_a, CAPABILITY)
    assert await _status(ns, admin, note["id"]) == 404
    restored = await _ok(ns, owner, "POST", f"/knowledge/pages/{note['id']}/restore")
    assert restored["state"] == "draft"  # a draft that was archived is a draft again
    again = await _call(ns, owner, "POST", f"/knowledge/pages/{note['id']}/restore")
    assert again.status_code == 409


# ── Fix round 2 ─────────────────────────────────────────────────────────────

async def test_a_revoked_author_reads_no_published_page(ns, app_engine) -> None:  # noqa: F811
    """P1-e. Authorship gives no read of a published page. With the grant
    gone, the author sees neither the page nor a later edit by another."""
    _assert_non_priv(app_engine)
    admin = _principal(_address("admin"), ns.org_a, CAPABILITY)
    author_email, editor_email = _address("author"), _address("editor")
    author = _principal(author_email, ns.org_a)
    editor = _principal(editor_email, ns.org_a)
    eng = await _space(ns, admin, "Eng", [
        {"subject": author_email, "level": "write"},
        {"subject": editor_email, "level": "write"},
    ])
    pid = await _published(ns, author, eng, "Runbook")
    await _ok(ns, admin, "PUT", f"/knowledge/spaces/{eng}/grants",
              {"grants": [{"subject": editor_email, "level": "write"}]})
    await _ok(ns, editor, "PATCH", f"/knowledge/pages/{pid}", {"body_md": "secret after revoke"})

    assert await _status(ns, author, pid) == 404
    assert "Runbook" not in await _titles(ns, author)
    assert await _status(ns, editor, pid) == 200


async def test_an_author_cannot_yank_a_published_page(ns, app_engine) -> None:  # noqa: F811
    """P1-f. A published page belongs to its space. An author who still reads
    it, but lost ``write``, cannot move it into their own ``me`` space."""
    _assert_non_priv(app_engine)
    admin = _principal(_address("admin"), ns.org_a, CAPABILITY)
    author_email, editor_email = _address("author"), _address("editor")
    author = _principal(author_email, ns.org_a)
    editor = _principal(editor_email, ns.org_a)
    eng = await _space(ns, admin, "Eng", [
        {"subject": author_email, "level": "write"},
        {"subject": editor_email, "level": "write"},
    ])
    pid = await _published(ns, author, eng, "Runbook")
    await _ok(ns, admin, "PUT", f"/knowledge/spaces/{eng}/grants", {"grants": [
        {"subject": author_email, "level": "read"},
        {"subject": editor_email, "level": "write"},
    ]})
    home = await _me_space(ns, author)
    yank = await _call(ns, author, "POST", f"/knowledge/pages/{pid}/move", {"space_id": home})
    assert yank.status_code == 403, yank.text
    detail = await _ok(ns, editor, "GET", f"/knowledge/pages/{pid}")
    assert detail["space_id"] == eng


# ── One `me` space under concurrent first writes ────────────────────────────

async def test_an_archived_draft_stays_its_authors(ns, app_engine) -> None:  # noqa: F811
    """Review round 3. An archived draft opens to its author only. A space
    manager or a holder of ``knowledge:manage`` never reads a draft that is
    not their own (§5.2), also after an archive. A restore of a published
    page sets a fresh review date."""
    _assert_non_priv(app_engine)
    admin = _principal(_address("admin"), ns.org_a, CAPABILITY)
    author_email, lead_email = _address("author"), _address("lead")
    author = _principal(author_email, ns.org_a)
    lead = _principal(lead_email, ns.org_a)
    space = await _space(ns, admin, "Drafts", [
        {"subject": author_email, "level": "manage"},
        {"subject": lead_email, "level": "manage"},
    ])
    draft = await _ok(ns, author, "POST", "/knowledge/pages",
                      {"title": "Half-written", "space_id": space}, status=201)
    await _ok(ns, author, "POST", f"/knowledge/pages/{draft['id']}/archive")
    assert await _status(ns, author, draft["id"]) == 200
    assert await _status(ns, lead, draft["id"]) == 404
    assert await _status(ns, admin, draft["id"]) == 404

    pid = await _published(ns, author, space, "Old runbook")
    with ns.admin_engine.begin() as c:
        c.execute(text("UPDATE kb_pages SET review_at = now() - interval '30 days' "
                       "WHERE id = CAST(:p AS uuid)"), {"p": pid})
    await _ok(ns, lead, "POST", f"/knowledge/pages/{pid}/archive")
    await _ok(ns, lead, "POST", f"/knowledge/pages/{pid}/restore")
    with ns.admin_engine.connect() as c:
        fresh = c.execute(text(
            "SELECT review_at > now() + interval '170 days' FROM kb_pages "
            "WHERE id = CAST(:p AS uuid)"), {"p": pid}).scalar()
    assert fresh is True


async def test_concurrent_first_writes_make_one_me_space(ns, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    email = _address("racer")
    member = _principal(email, ns.org_a)
    results = await _calls(ns, member, [
        ("POST", "/knowledge/pages", {"title": f"Note {i}"}) for i in range(6)
    ])
    assert [r.status_code for r in results] == [201] * 6, [r.text for r in results]
    assert len({r.json()["space_id"] for r in results}) == 1
    with ns.admin_engine.connect() as c:
        count = c.execute(text(
            "SELECT count(*) FROM kb_spaces WHERE kind = 'me' AND owner_email = :e"),
            {"e": email}).scalar()
    assert count == 1


def test_the_me_insert_waits_and_then_does_nothing(ns, app_engine) -> None:  # noqa: F811
    """The race, made certain: the second insert BLOCKS on the first's
    uncommitted row, then does nothing and reads the first's row. These are
    the statements ``core.ensure_me_space`` runs."""
    from acb_auth import UserContext, UserRole, build_access
    from gateway.routes.knowledge import core as kb_core

    _assert_non_priv(app_engine)
    user = UserContext(email=_address("racer"), role=UserRole.EMPLOYEE,
                       access=build_access(["feature:knowledge"]),
                       organization_id=ns.org_a)
    params = kb_core.me_space_params(user)
    first = app_engine.connect()
    trans = first.begin()
    first.execute(text("SELECT set_config('app.tenant_id', :o, true)"), {"o": ns.org_a})
    first.execute(text(kb_core.INSERT_ME_SPACE_SQL), params)

    seen: dict[str, Any] = {}

    def second() -> None:
        try:
            with app_engine.connect() as c, c.begin():
                c.execute(text("SELECT set_config('app.tenant_id', :o, true)"),
                          {"o": ns.org_a})
                c.execute(text(kb_core.INSERT_ME_SPACE_SQL), params)
                seen["row"] = c.execute(text(kb_core.SELECT_ME_SPACE_SQL), params).first()
        except Exception as exc:  # the failure the fence exists to catch
            seen["error"] = exc

    worker = threading.Thread(target=second)
    worker.start()
    time.sleep(0.8)
    assert worker.is_alive(), "the second insert did not wait for the first"
    mine = first.execute(text(kb_core.SELECT_ME_SPACE_SQL), params).first()
    trans.commit()
    first.close()
    worker.join(timeout=10)
    assert "error" not in seen, seen.get("error")
    assert seen["row"] is not None and str(seen["row"].id) == str(mine.id)
    with ns.admin_engine.connect() as c:
        assert c.execute(text(
            "SELECT count(*) FROM kb_spaces WHERE owner_email = :e"),
            {"e": params["me"]}).scalar() == 1


# ── The tables themselves ───────────────────────────────────────────────────

@pytest.mark.parametrize("table", _TABLES)
def test_each_table_forces_row_level_security(ns, table: str) -> None:
    with ns.admin_engine.connect() as c:
        forced = c.execute(text(
            "SELECT relrowsecurity AND relforcerowsecurity FROM pg_class "
            "WHERE oid = to_regclass(:t)"), {"t": table}).scalar()
        policies = c.execute(text(
            "SELECT count(*) FROM pg_policies WHERE tablename = :t"), {"t": table},
        ).scalar()
    assert forced is True
    assert policies == 1


def test_a_space_for_another_tenant_is_refused(ns, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    with app_engine.connect() as c:
        trans = c.begin()
        c.execute(text("SELECT set_config('app.tenant_id', :o, true)"), {"o": ns.org_a})
        with pytest.raises(DBAPIError):
            c.execute(text(
                "INSERT INTO kb_spaces (organization_id, slug, name, kind) "
                "VALUES (CAST(:o AS uuid), 'team-x', 'X', 'team')"), {"o": ns.org_b})
        trans.rollback()


def test_an_unbound_session_reads_no_space(ns, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    with ns.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO kb_spaces (organization_id, slug, name, kind) "
            "VALUES (CAST(:o AS uuid), 'company', 'Company', 'company')"), {"o": ns.org_a})
    with app_engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM kb_spaces")).scalar() == 0


def test_the_migration_replays_with_no_change(ns) -> None:
    with ns.admin_engine.begin() as c:
        before = c.execute(text(
            "SELECT pg_get_functiondef('provision_org_roles(uuid)'::regprocedure)")).scalar()
    sql = _kb_migration().read_text(encoding="utf-8")
    with ns.admin_engine.begin() as c, c.connection.dbapi_connection.cursor() as cur:
        cur.execute(sql)
    with ns.admin_engine.connect() as c:
        after = c.execute(text(
            "SELECT pg_get_functiondef('provision_org_roles(uuid)'::regprocedure)")).scalar()
        held = c.execute(text(
            "SELECT count(*) FROM org_role_permission p JOIN org_role r ON r.id = p.role_id "
            "JOIN organization o ON o.id = r.organization_id "
            "WHERE r.slug = 'admin' AND p.permission = :c AND o.slug = 'default'"),
            {"c": CAPABILITY}).scalar()
    assert after == before
    assert after.count(f"'{CAPABILITY}'") == 1
    assert held == 1


# ── The seed of `knowledge:manage` ──────────────────────────────────────────

def _held(conn: Any, org: str) -> dict[str, set[str]]:
    return {
        row[0]: set(row[1] or [])
        for row in conn.execute(
            text("SELECT r.slug, array_agg(p.permission) FROM org_role r "
                 "LEFT JOIN org_role_permission p ON p.role_id = r.id "
                 "WHERE r.organization_id = CAST(:o AS uuid) GROUP BY r.slug"),
            {"o": org},
        )
    }


def _assert_admin_only(held: dict[str, set[str]]) -> None:
    assert CAPABILITY in held["admin"], "admin does not hold knowledge:manage"
    for role in ("manager", "member", "guest", "agent_service"):
        assert CAPABILITY not in held.get(role, set()), f"{role} holds {CAPABILITY}"
    assert "*" in held["owner"]


def test_the_seed_on_the_promoted_catalog(ns) -> None:
    """The tenancy arm: ``org_role_permission.organization_id`` exists, as in
    production. Each organization's admin holds the capability, and a new
    organization gets it from ``provision_org_roles``."""
    with ns.admin_engine.connect() as c:
        trans = c.begin()
        try:
            default = c.execute(text(
                "SELECT id::text FROM organization WHERE slug = 'default'")).scalar()
            _assert_admin_only(_held(c, default))
            org = c.execute(text(
                "SELECT provision_organization('kb1-second', 'Second Org', "
                "'chief@kb1-second.test')")).scalar_one()
            _assert_admin_only(_held(c, str(org)))
        finally:
            trans.rollback()


@pytest.fixture(scope="module")
def replayed():
    """The plain ladder, applied twice: no tenancy column, the other arm."""
    eng = create_engine(_URL, future=True)
    with eng.begin() as conn:
        apply_ladder(conn)
    with eng.begin() as conn:
        apply_ladder(conn)
    yield eng
    eng.dispose()


def test_the_seed_on_the_replayed_ladder(replayed) -> None:
    with replayed.connect() as c:
        trans = c.begin()
        try:
            default = c.execute(text(
                "SELECT id::text FROM organization WHERE slug = 'default'")).scalar()
            _assert_admin_only(_held(c, default))
            rows = c.execute(text(
                "SELECT count(*) FROM org_role_permission p "
                "JOIN org_role r ON r.id = p.role_id WHERE r.slug = 'admin' "
                "AND p.permission = :c AND r.organization_id = CAST(:o AS uuid)"),
                {"c": CAPABILITY, "o": default}).scalar()
            assert rows == 1, "a replay granted the capability twice"
            org = c.execute(text(
                "SELECT provision_organization('kb1-ladder', 'Ladder Org', "
                "'chief@kb1-ladder.test')")).scalar_one()
            _assert_admin_only(_held(c, str(org)))
        finally:
            trans.rollback()
