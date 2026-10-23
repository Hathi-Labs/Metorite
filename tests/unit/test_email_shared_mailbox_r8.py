"""R8 — the mailbox access seam on a real database (WS-65 SM-2).

Spec: ``project-docs/specs/shared_mailbox.md`` §8.1, §8.2 step 2 and §8.3,
fences 1, 4, 5 and 7. Every read runs as the NON-privileged role
``acb_app_h3rls`` under FORCE ROW LEVEL SECURITY, through the seam
(``mailbox_access.may_access``, ``mailbox_ids_sql``) and through real routes
of the email router, with the feature gate of the router in place.

* **Private stays private.** A second member of the same organization never
  reaches the private mailbox of the first, with the flag off or on.
* **Fence 1, no cross-org path.** A member of org B, and an address of org A
  under the tenant of org B, reach nothing of org A, a mailbox shared with
  ``org`` included.
* **Fence 7, the flag off.** A shared row and a grant give a non-owner NO
  access, at the seam and at the routes. The connector keeps the access of
  before, as the expand step says (§8.2 step 1).
* **The levels, with the flag on.** A ``read`` grant reads and does not
  send. A ``send`` grant sends and does not manage. A ``manage`` grant
  manages. The connector, with no grant, reaches nothing (D-SM-3).
* **Fence 4.** A ``group:`` grant follows the membership of the group.
* **Fence 5.** "Self" of a member never holds a shared mailbox (D-SM-5).
* **D-SM-4.** A member with a grant and no ``feature:email`` gets 403 from
  the router before any grant read.

R7, the mutations. Each one turns a test here red (run 2026-10-23):

* M1 return the shared arm with the flag off in ``mailbox_predicate`` ->
  ``test_the_flag_off_gives_a_grant_no_access`` and two more.
* M2 drop ``AND {row}.shared_since IS NULL`` from the private arm ->
  ``test_the_connector_has_no_access_without_a_grant``.
* M3 make ``levels_at_least`` return every level ->
  ``test_a_read_grant_reads_and_does_not_send`` and two more.

Run (real Postgres)::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_email_shared_mailbox_r8.py -v -rs
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
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


def _assert_non_priv(engine: Any) -> None:
    with engine.connect() as c:
        role = c.execute(text(
            "SELECT rolsuper, rolbypassrls FROM pg_roles "
            "WHERE rolname = current_user")).first()
    assert role is not None and not role[0] and not role[1], (
        "this suite connects as a SUPERUSER/BYPASSRLS role, so RLS is bypassed")


@pytest.fixture
def flag(monkeypatch):
    from acb_common import get_settings

    def _set(value: bool) -> None:
        monkeypatch.setattr(get_settings(), "email_shared_mailbox", value, raising=False)

    _set(False)
    return _set


@pytest.fixture
def world(promoted, app_engine):  # noqa: F811 — fixtures
    """Org A: a private box of ``own``, a shared box ``sales`` that ``own``
    connected, and a shared box ``team`` shared with ``org``. Org B: one
    member and one shared box shared with ``org``."""
    _assert_non_priv(app_engine)
    p = promoted
    tag = uuid.uuid4().hex[:8]

    def addr(name: str) -> str:
        return f"{name}-{tag}@sm2.test"

    m = SimpleNamespace(**{k: addr(k) for k in (
        "own", "bob", "rita", "sam", "mia", "carol", "zed")})
    users: list[str] = []
    boxes: list[str] = []
    group_ids: list[str] = []
    with p.admin_engine.begin() as c:
        uid: dict[str, str] = {}
        for name in ("own", "bob", "rita", "sam", "mia", "carol"):
            uid[name] = str(c.execute(text(
                "INSERT INTO app_user (email, display_name, role, status, organization_id) "
                "VALUES (:e, :e, 'employee', 'active', CAST(:o AS uuid)) RETURNING id"),
                {"e": getattr(m, name), "o": p.org_a}).scalar_one())
            users.append(getattr(m, name))
        c.execute(text(
            "INSERT INTO app_user (email, display_name, role, status, organization_id) "
            "VALUES (:e, :e, 'employee', 'active', CAST(:o AS uuid))"),
            {"e": m.zed, "o": p.org_b})
        users.append(m.zed)

        def box(org: str, owner: str, shared: bool) -> str:
            bid = str(c.execute(text(
                "INSERT INTO email_accounts (user_id, provider, email_address, "
                "credentials_encrypted, initial_sync_done, organization_id, shared_since) "
                "VALUES (:u, 'microsoft', :a, 'x', true, CAST(:o AS uuid), "
                "CASE WHEN :s THEN now() END) RETURNING id"),
                {"u": owner, "a": f"box-{uuid.uuid4().hex[:6]}@sm2.test", "o": org,
                 "s": shared}).scalar_one())
            boxes.append(bid)
            return bid

        private = box(p.org_a, m.own, False)
        sales = box(p.org_a, m.own, True)
        team = box(p.org_a, m.own, True)
        other = box(p.org_b, m.zed, True)

        def grant(org: str, account: str, subject: str, level: str) -> None:
            c.execute(text(
                "INSERT INTO email_mailbox_grants (organization_id, account_id, subject, "
                "level, created_by) VALUES (CAST(:o AS uuid), CAST(:a AS uuid), :s, :l, "
                "'admin@sm2.test')"), {"o": org, "a": account, "s": subject, "l": level})

        grant(p.org_a, sales, m.rita.lower(), "read")
        grant(p.org_a, sales, m.sam.lower(), "send")
        grant(p.org_a, sales, m.mia.lower(), "manage")
        slug = f"sales-{tag}"
        grant(p.org_a, sales, f"group:{slug}", "send")
        grant(p.org_a, team, "org", "read")
        grant(p.org_b, other, "org", "read")
        gid = str(c.execute(text(
            "INSERT INTO org_group (organization_id, slug, display_name) "
            "VALUES (CAST(:o AS uuid), :s, :s) RETURNING id"),
            {"o": p.org_a, "s": slug}).scalar_one())
        group_ids.append(gid)
        c.execute(text(
            "INSERT INTO org_group_member (group_id, user_id, organization_id) "
            "VALUES (CAST(:g AS uuid), CAST(:u AS uuid), CAST(:o AS uuid))"),
            {"g": gid, "u": uid["carol"], "o": p.org_a})

        def mail(account: str, org: str) -> str:
            return str(c.execute(text(
                "INSERT INTO email_messages (account_id, provider_message_id, thread_id, "
                "folder, from_address, to_addresses, subject, body_text, body_html, "
                "snippet, received_at, organization_id) VALUES (CAST(:a AS uuid), :pid, "
                ":pid, 'inbox', '{\"name\": \"Ravi\", \"email\": \"ravi@contoso.test\"}'"
                "::jsonb, '[]'::jsonb, 'Quote', 'The quote.', '<p>The quote.</p>', "
                "'The quote.', now(), CAST(:o AS uuid)) RETURNING id"),
                {"a": account, "pid": f"pm-{uuid.uuid4().hex[:8]}", "o": org}).scalar_one())

        mails = {"private": mail(private, p.org_a), "sales": mail(sales, p.org_a)}

    try:
        yield SimpleNamespace(p=p, m=m, private=private, sales=sales, team=team,
                              other=other, mails=mails, group=gid, carol_id=uid["carol"])
    finally:
        with p.admin_engine.begin() as c:
            c.execute(text("DELETE FROM email_messages WHERE account_id = ANY(CAST(:b AS uuid[]))"),
                      {"b": boxes})
            c.execute(text("DELETE FROM email_mailbox_grants WHERE account_id = "
                           "ANY(CAST(:b AS uuid[]))"), {"b": boxes})
            c.execute(text("DELETE FROM email_accounts WHERE id = ANY(CAST(:b AS uuid[]))"),
                      {"b": boxes})
            for g in group_ids:
                c.execute(text("DELETE FROM org_group_member WHERE group_id = CAST(:g AS uuid)"),
                          {"g": g})
                c.execute(text("DELETE FROM org_group WHERE id = CAST(:g AS uuid)"), {"g": g})
            c.execute(text("DELETE FROM app_user WHERE email = ANY(:u)"), {"u": users})


async def _may(w: Any, org: str, member: str, account: str, level: str) -> bool:
    """``may_access`` in one tenant session as the app role, *org* bound."""
    from acb_common.db import bind_tenant, release_tenant
    from gateway.db import tenant_session
    from gateway.routes.email.mailbox_access import may_access

    token = bind_tenant(org)
    try:
        async with tenant_engine_scope(w.p.app_url.render_as_string(hide_password=False)):
            async with tenant_session() as db:
                return await may_access(db, account, member, level)
    finally:
        release_tenant(token)


async def _ids(w: Any, org: str, member: str, level: str) -> set[str]:
    from acb_common.db import bind_tenant, release_tenant
    from gateway.db import tenant_session
    from gateway.routes.email.mailbox_access import mailbox_ids_sql

    token = bind_tenant(org)
    try:
        async with tenant_engine_scope(w.p.app_url.render_as_string(hide_password=False)):
            async with tenant_session() as db:
                rows = (await db.execute(text(mailbox_ids_sql(level=level)),
                                         {"uid": member})).fetchall()
                return {str(r[0]) for r in rows}
    finally:
        release_tenant(token)


async def _call(w: Any, org: str, member: str, method: str, path: str,
                body: dict | None = None, *, feature: bool = True) -> Any:
    """One request through the real email router, as *member* of *org*."""
    import httpx
    from acb_auth import UserContext, UserRole, build_access, get_current_user
    from acb_common.db import bind_tenant, clear_tenant, release_tenant
    from fastapi import FastAPI
    from gateway.routes import email as email_pkg

    user = UserContext(
        email=member, role=UserRole.EMPLOYEE,
        access=build_access(["feature:email"] if feature else ["feature:tasks"]),
        organization_id=org,
    )

    async def _caller() -> Any:
        bind_tenant(org)
        return user

    app = FastAPI()
    app.include_router(email_pkg.router)
    app.dependency_overrides[get_current_user] = _caller
    scope = clear_tenant()
    try:
        async with tenant_engine_scope(w.p.app_url.render_as_string(hide_password=False)):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://em.test") as c:
                return await c.request(method, path, json=body)
    finally:
        release_tenant(scope)


# ── Private stays private ───────────────────────────────────────────────────

@pytest.mark.parametrize("on", [False, True])
async def test_a_second_member_never_reaches_a_private_mailbox(world, flag, on) -> None:
    w = world
    flag(on)
    for level in ("read", "send", "manage"):
        assert await _may(w, w.p.org_a, w.m.own, w.private, level)
        assert not await _may(w, w.p.org_a, w.m.bob, w.private, level)
        assert not await _may(w, w.p.org_a, w.m.mia, w.private, level)
    response = await _call(w, w.p.org_a, w.m.bob, "GET", f"/email/messages/{w.mails['private']}")
    assert response.status_code == 404
    assert w.private not in await _ids(w, w.p.org_a, w.m.bob, "read")


# ── Fence 1: no cross-org path ──────────────────────────────────────────────

@pytest.mark.parametrize("on", [False, True])
async def test_no_cross_org_path(world, flag, on) -> None:
    w = world
    flag(on)
    for account in (w.private, w.sales, w.team):
        assert not await _may(w, w.p.org_b, w.m.zed, account, "read")
        # An address of org A under the tenant of org B.
        assert not await _may(w, w.p.org_b, w.m.own, account, "read")
        assert not await _may(w, w.p.org_b, w.m.rita, account, "read")
    # Nothing of org A. An `org` grant of org B means each member of the
    # bound tenant, and the tenant comes from the session, never the request.
    assert not await _ids(w, w.p.org_b, w.m.own, "read") & {w.private, w.sales, w.team}
    response = await _call(w, w.p.org_b, w.m.zed, "GET", f"/email/messages/{w.mails['sales']}")
    assert response.status_code == 404
    # Org B's own box opens to its member: as the connector with the flag
    # off, and through the `org` grant with it on. Never to a member of A.
    assert await _may(w, w.p.org_b, w.m.zed, w.other, "read") is True
    assert not await _may(w, w.p.org_a, w.m.rita, w.other, "read")


# ── Fence 7: the flag off ───────────────────────────────────────────────────

async def test_the_flag_off_gives_a_grant_no_access(world, flag) -> None:
    w = world
    for member in (w.m.rita, w.m.sam, w.m.mia, w.m.carol, w.m.bob):
        for level in ("read", "send", "manage"):
            assert not await _may(w, w.p.org_a, member, w.sales, level), (member, level)
        assert not await _may(w, w.p.org_a, member, w.team, "read")
        assert await _ids(w, w.p.org_a, member, "read") == set()
    response = await _call(w, w.p.org_a, w.m.mia, "GET", f"/email/messages/{w.mails['sales']}")
    assert response.status_code == 404
    response = await _call(w, w.p.org_a, w.m.rita, "GET", f"/email/messages?account_id={w.sales}")
    assert w.mails["sales"] not in response.text


async def test_the_flag_off_keeps_the_connector_as_before(world, flag) -> None:
    """§8.2 step 1: old code reads ``user_id = :uid``, so a shared row looks
    private to its connector and to nobody else."""
    w = world
    assert await _may(w, w.p.org_a, w.m.own, w.sales, "manage")


# ── The levels, with the flag on ────────────────────────────────────────────

async def test_a_read_grant_reads_and_does_not_send(world, flag) -> None:
    w = world
    flag(True)
    assert await _may(w, w.p.org_a, w.m.rita, w.sales, "read")
    assert not await _may(w, w.p.org_a, w.m.rita, w.sales, "send")
    assert not await _may(w, w.p.org_a, w.m.rita, w.sales, "manage")
    got = await _call(w, w.p.org_a, w.m.rita, "GET", f"/email/messages/{w.mails['sales']}")
    assert got.status_code == 200, got.text
    listed = await _call(w, w.p.org_a, w.m.rita, "GET", f"/email/messages?account_id={w.sales}")
    assert listed.status_code == 200 and w.mails["sales"] in listed.text
    snooze = await _call(w, w.p.org_a, w.m.rita, "POST",
                         f"/email/messages/{w.mails['sales']}/snooze", {"until": None})
    assert snooze.status_code == 404


async def test_a_send_grant_sends_and_does_not_manage(world, flag) -> None:
    w = world
    flag(True)
    assert await _may(w, w.p.org_a, w.m.sam, w.sales, "read")
    assert await _may(w, w.p.org_a, w.m.sam, w.sales, "send")
    assert not await _may(w, w.p.org_a, w.m.sam, w.sales, "manage")
    snooze = await _call(w, w.p.org_a, w.m.sam, "POST",
                         f"/email/messages/{w.mails['sales']}/snooze", {"until": None})
    assert snooze.status_code == 200, snooze.text
    rename = await _call(w, w.p.org_a, w.m.sam, "PATCH", f"/email/accounts/{w.sales}",
                         {"label": "Sales"})
    assert rename.status_code == 404


async def test_a_manage_grant_manages(world, flag) -> None:
    w = world
    flag(True)
    for level in ("read", "send", "manage"):
        assert await _may(w, w.p.org_a, w.m.mia, w.sales, level)
    rename = await _call(w, w.p.org_a, w.m.mia, "PATCH", f"/email/accounts/{w.sales}",
                         {"label": "Sales"})
    assert rename.status_code == 200, rename.text
    assert rename.json()["label"] == "Sales"


async def test_the_connector_has_no_access_without_a_grant(world, flag) -> None:
    """D-SM-3: the connect gives no access. ``own`` connected ``sales``."""
    w = world
    flag(True)
    assert not await _may(w, w.p.org_a, w.m.own, w.sales, "read")
    assert await _may(w, w.p.org_a, w.m.own, w.private, "manage")
    assert w.sales not in await _ids(w, w.p.org_a, w.m.own, "read")


async def test_an_org_grant_reaches_each_member_of_the_org(world, flag) -> None:
    w = world
    flag(True)
    assert await _may(w, w.p.org_a, w.m.bob, w.team, "read")
    assert not await _may(w, w.p.org_a, w.m.bob, w.team, "send")
    assert await _ids(w, w.p.org_a, w.m.bob, "read") == {w.team}


# ── Fence 4: a group grant follows membership ───────────────────────────────

async def test_a_group_grant_follows_membership(world, flag) -> None:
    w = world
    flag(True)
    assert await _may(w, w.p.org_a, w.m.carol, w.sales, "send")
    with w.p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM org_group_member WHERE group_id = CAST(:g AS uuid) "
                       "AND user_id = CAST(:u AS uuid)"), {"g": w.group, "u": w.carol_id})
    assert not await _may(w, w.p.org_a, w.m.carol, w.sales, "read")
    response = await _call(w, w.p.org_a, w.m.carol, "GET", f"/email/messages/{w.mails['sales']}")
    assert response.status_code == 404


# ── Fence 5: self stays private ─────────────────────────────────────────────

@pytest.mark.parametrize("on", [False, True])
async def test_a_shared_mailbox_is_never_self(world, flag, on) -> None:
    """D-SM-5. ``own`` connected ``sales``, so ``user_id`` is the same. Still,
    the self set of the private box holds only the private box, and the self
    set of ``sales`` holds only ``sales``."""
    from gateway.routes.email.automation.identity import SELF_MAILBOX_IDS_SQL

    w = world
    flag(on)
    from acb_common.db import bind_tenant, release_tenant
    from gateway.db import tenant_session

    token = bind_tenant(w.p.org_a)
    try:
        async with tenant_engine_scope(w.p.app_url.render_as_string(hide_password=False)):
            async with tenant_session() as db:
                mine = {str(r[0]) for r in (await db.execute(
                    text(SELF_MAILBOX_IDS_SQL), {"aid": w.private})).fetchall()}
                shared = {str(r[0]) for r in (await db.execute(
                    text(SELF_MAILBOX_IDS_SQL), {"aid": w.sales})).fetchall()}
    finally:
        release_tenant(token)
    assert mine == {w.private}
    assert shared == {w.sales}


# ── D-SM-4: the feature first ───────────────────────────────────────────────

async def test_a_grant_without_the_email_feature_gets_403(world, flag) -> None:
    w = world
    flag(True)
    response = await _call(w, w.p.org_a, w.m.mia, "GET",
                           f"/email/messages/{w.mails['sales']}", feature=False)
    assert response.status_code == 403
