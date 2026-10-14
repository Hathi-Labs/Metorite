"""R8 for the agent-read paging change: the routes the chat now pages.

Review of #1014 (2026-10-14). Two faults a hermetic fake cannot see:

1. **The CRM timeline could not be paged.** Its unpaged read gives email half
   the page, merges and cuts, so a bigger LIMIT is a different mix. The route
   now takes ``offset`` and pages ONE total order. Here: 30 newer emails and
   30 older notes, 20 a page, and 3 pages hold all 60 once, in order.
2. **A tie of the sort key has no order.** OFFSET (or a bigger LIMIT) over a
   tie may repeat or skip a row. Each paged route now ends its ORDER BY on the
   row id. Here: rows that share one instant come back in id order, and the
   pages join up with no repeat and no gap.

Against a REAL Postgres, under FORCE RLS, as the non-privileged app role
(``promoted`` / ``app_engine`` of ``test_h3_rls_promotion_rehearsal.py``).
With no ``TENANT_LADDER_DATABASE_URL`` the suite skips, and a skip is not a
pass.

Mutations this file catches (R7):

* the timeline tool or route pages a budgeted read again ->
  ``test_the_crm_timeline_pages_with_no_repeat_and_no_gap``;
* a tie-break is dropped from one of the four routes -> the test of that route.
"""
from __future__ import annotations

import inspect
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth.roles import UserContext, UserRole
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_email_keep_separate import _account, _as_member, _assert_non_priv
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

TAG = uuid.uuid4().hex[:8]
ME = f"paging-{TAG}@paging-r8.test"
T0 = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)


def _direct(fn: Any, **given: Any) -> Any:
    """Call a route function directly, every unnamed argument at its default.

    A FastAPI default is a ``Query`` object, not its value, so a bare call
    would hand the route a ``Query``. This reads ``.default`` off each one."""
    kwargs: dict[str, Any] = {}
    for name, param in inspect.signature(fn).parameters.items():
        if name in given:
            kwargs[name] = given[name]
        elif param.default is not inspect.Parameter.empty:
            kwargs[name] = getattr(param.default, "default", param.default)
    return fn(**kwargs)


def _user(org: str, *features: str) -> UserContext:
    from acb_auth import build_access

    return UserContext(email=ME, role=UserRole.EMPLOYEE, organization_id=org,
                       access=build_access(list(features)))


def _sorted_desc(ids: list[str]) -> list[str]:
    return sorted(ids, key=lambda i: uuid.UUID(i).bytes, reverse=True)


@pytest.fixture
def db(promoted, app_engine):  # noqa: F811
    _assert_non_priv(app_engine)
    yield promoted
    with promoted.admin_engine.begin() as c:
        c.execute(text("DELETE FROM email_accounts WHERE user_id = :u"), {"u": ME})
        c.execute(text("DELETE FROM wa_accounts WHERE user_id = :u"), {"u": ME})
        c.execute(text("DELETE FROM pm_projects WHERE created_by = :u"), {"u": ME})
        c.execute(text("DELETE FROM app_user WHERE lower(email) = :u"), {"u": ME})
        for table in ("crm_activities", "crm_leads", "crm_settings"):
            c.execute(text(f"DELETE FROM {table} WHERE organization_id = CAST(:o AS uuid)"),
                      {"o": promoted.org_a})


# ── 1. The CRM timeline pages one total order ──────────────────────────────

async def _crm(p: Any, method: str, path: str, body: dict | None = None) -> Any:
    """One request through the real /crm router, as a CRM admin of org A."""
    import httpx
    from acb_auth import get_current_user
    from acb_common.db import bind_tenant, clear_tenant, release_tenant
    from fastapi import FastAPI
    from gateway.routes import crm as crm_package

    user = _user(p.org_a, "feature:crm", "admin:access:manage")

    async def _caller() -> Any:
        bind_tenant(user.organization_id)
        return user

    app = FastAPI()
    app.include_router(crm_package.router)
    app.dependency_overrides[get_current_user] = _caller
    scope = clear_tenant()
    try:
        async with (
            tenant_engine_scope(p.app_url.render_as_string(hide_password=False)),
            httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                              base_url="http://crm.test") as client,
        ):
            return await client.request(method, path, json=body)
    finally:
        release_tenant(scope)


async def test_the_crm_timeline_pages_with_no_repeat_and_no_gap(db, monkeypatch) -> None:
    from tests.unit._crm_fakes import crm_switch_on

    crm_switch_on(monkeypatch, "*")
    res = await _crm(db, "PATCH", "/crm/settings", {"mode": "native"})
    assert res.status_code == 200, res.text
    buyer = f"buyer-{TAG}@acme.example"
    res = await _crm(db, "POST", "/crm/leads", {"first_name": "Ann", "email": buyer})
    assert res.status_code == 201, res.text
    lead = res.json()["id"]
    box = _account(db.admin_engine, org=db.org_a, owner=ME)
    with db.admin_engine.begin() as c:
        for k in range(30):
            # Newest first: mail 0 is the newest row of the record.
            c.execute(text(
                "INSERT INTO email_messages (account_id, provider_message_id, thread_id, "
                "folder, from_address, to_addresses, subject, received_at, organization_id) "
                "VALUES (CAST(:a AS uuid), :p, :t, 'INBOX', CAST(:f AS jsonb), "
                "CAST('[]' AS jsonb), :s, :r, "
                "CAST(:o AS uuid))"),
                {"a": box, "p": f"pm-{TAG}-{k}", "t": f"t-{TAG}-{k}",
                 "f": json.dumps({"name": "Ann", "email": buyer}), "s": f"mail {k}",
                 "r": T0 + timedelta(days=60 - k), "o": db.org_a})
        for k in range(30):
            # Notes in PAIRS at one instant, so the id must break the tie.
            c.execute(text(
                "INSERT INTO crm_activities (type, subject, occurred_at, lead_id, "
                "created_by, organization_id) VALUES ('note', :s, :at, "
                "CAST(:l AS uuid), :by, CAST(:o AS uuid))"),
                {"s": f"note {k}", "at": T0 - timedelta(days=k // 2), "l": lead,
                 "by": ME, "o": db.org_a})

    pages = []
    for offset in (0, 20, 40):
        res = await _crm(db, "GET", f"/crm/leads/{lead}/timeline?limit=20&offset={offset}")
        assert res.status_code == 200, res.text
        pages.append(res.json())
    rows = [e for page in pages for e in page["entries"]]
    keys = [(e["email_thread"] or {}).get("message_id") or (e["activity"] or {}).get("id")
            for e in rows]
    assert len(keys) == 60 and len(set(keys)) == 60, "a page repeated or skipped a row"
    assert [e["email_thread"]["subject"] for e in rows[:30]] == [f"mail {k}" for k in range(30)]
    assert {e["kind"] for e in rows[30:]} == {"activity"}
    assert [p["has_more"] for p in pages] == [True, True, False]
    # Each pair of notes at one instant comes back in id order.
    notes = [e["activity"]["id"] for e in rows[30:]]
    for i in range(0, 30, 2):
        assert notes[i:i + 2] == _sorted_desc(notes[i:i + 2])
    # The unpaged read the panel draws keeps its budget and its shape.
    res = await _crm(db, "GET", f"/crm/leads/{lead}/timeline?limit=20")
    body = res.json()
    assert body.get("has_more") is None
    assert sum(1 for e in body["entries"] if e["kind"] == "email_thread") == 10


# ── 2. A tie of the sort key keeps one order ───────────────────────────────

async def test_email_messages_break_a_tie_by_id(db) -> None:
    from gateway.routes.email.transport import messages

    box = _account(db.admin_engine, org=db.org_b, owner=ME)
    ids: list[str] = []
    with db.admin_engine.begin() as c:
        for k in range(30):
            ids.append(str(c.execute(text(
                "INSERT INTO email_messages (account_id, provider_message_id, thread_id, "
                "folder, from_address, to_addresses, subject, received_at, organization_id) "
                "VALUES (CAST(:a AS uuid), :p, :t, 'INBOX', CAST(:f AS jsonb), "
                "CAST('[]' AS jsonb), :s, :r, "
                "CAST(:o AS uuid)) RETURNING id"),
                {"a": box, "p": f"pt-{TAG}-{k}", "t": f"thr-{TAG}",
                 "f": json.dumps({"email": "x@y.io"}), "s": f"s{k}", "r": T0,
                 "o": db.org_b}).scalar_one()))
    user = _user(db.org_b, "feature:email")
    async with _as_member(db, db.org_b):
        newest = []
        for page in (1, 2, 3):
            got = await _direct(messages.list_messages, account_id=box, folder="INBOX",
                                page=page, page_size=10, user=user)
            newest += [str(e["id"]) for e in got["emails"]]
        thread = []
        for page in (1, 2, 3):
            got = await _direct(messages.list_messages, thread_id=f"thr-{TAG}",
                                page=page, page_size=10, user=user)
            thread += [str(e["id"]) for e in got["emails"]]
    assert newest == _sorted_desc(ids)
    assert thread == list(reversed(_sorted_desc(ids)))


async def test_rule_history_breaks_a_tie_by_id(db) -> None:
    from gateway.routes.email.automation import runner

    box = _account(db.admin_engine, org=db.org_b, owner=ME)
    ids: list[str] = []
    with db.admin_engine.begin() as c:
        for k in range(30):
            ids.append(str(c.execute(text(
                "INSERT INTO email_executed_rules (account_id, rule_name, subject, status, "
                "created_at, organization_id) VALUES (CAST(:a AS uuid), 'r', :s, "
                "'APPLIED', :at, CAST(:o AS uuid)) RETURNING id"),
                {"a": box, "s": f"s{k}", "at": T0, "o": db.org_b}).scalar_one()))
    user = _user(db.org_b, "feature:email")
    async with _as_member(db, db.org_b):
        got = {n: await _direct(runner.rules_history, limit=n, user=user) for n in (11, 30)}
    rows = {n: [str(h["id"]) for h in got[n]["history"]] for n in got}
    assert rows[30] == _sorted_desc(ids)
    assert rows[11] == rows[30][:11], "a bigger LIMIT must begin with the smaller one"


async def test_notifications_break_a_tie_by_id(db) -> None:
    from gateway.routes.projects import notifications
    from gateway.routes.projects.core import Page

    project, lane = str(uuid.uuid4()), str(uuid.uuid4())
    task = str(uuid.uuid4())
    ids: list[str] = []
    with db.admin_engine.begin() as c:
        # An org grant reaches members of the org, so the reader is one.
        c.execute(text(
            "INSERT INTO app_user (email, display_name, role, status, organization_id) "
            "VALUES (:e, :e, 'employee', 'active', CAST(:o AS uuid))"),
            {"e": ME, "o": db.org_b})
        c.execute(text(
            "INSERT INTO pm_projects (id, organization_id, name, source, created_by, "
            "owns_statuses) VALUES (CAST(:p AS uuid), CAST(:o AS uuid), "
            "'Shared', 'manual', :me, true)"), {"p": project, "o": db.org_b, "me": ME})
        # An org grant, so the member sees the task (the shell suite's shape).
        c.execute(text(
            "INSERT INTO pm_project_grants (project_id, subject, created_by, "
            "organization_id) VALUES (CAST(:p AS uuid), 'org', :me, CAST(:o AS uuid))"),
            {"p": project, "me": ME, "o": db.org_b})
        c.execute(text(
            "INSERT INTO pm_task_statuses (id, project_id, name, position, category, "
            "is_default) VALUES (CAST(:s AS uuid), CAST(:p AS uuid), 'To do', 1, 'todo', "
            "true)"), {"s": lane, "p": project})
        c.execute(text(
            "INSERT INTO pm_tasks (id, organization_id, project_id, root_project_id, "
            "status_id, title, source, created_by, task_number) VALUES (CAST(:t AS uuid), "
            "CAST(:o AS uuid), CAST(:p AS uuid), CAST(:p AS uuid), CAST(:s AS uuid), "
            "'Call', 'manual', :me, 1)"),
            {"t": task, "o": db.org_b, "p": project, "s": lane, "me": ME})
        for _ in range(30):
            ids.append(str(c.execute(text(
                "INSERT INTO pm_notifications (recipient, kind, task_id, actor, created_at, "
                "organization_id) VALUES (:me, 'assigned', CAST(:t AS uuid), 'boss@x.io', "
                ":at, CAST(:o AS uuid)) RETURNING id"),
                {"me": ME, "t": task, "at": T0, "o": db.org_b}).scalar_one()))
    user = _user(db.org_b, "feature:projects")
    seen: list[str] = []
    async with _as_member(db, db.org_b):
        for page in (1, 2, 3):
            got = await notifications.list_notifications(
                unread_only=False, page=Page(page=page, page_size=10), user=user)
            seen += [str(r["id"]) for r in got["rows"]]
    assert seen == _sorted_desc(ids)


async def test_whatsapp_chats_break_a_tie_by_id(db) -> None:
    from gateway.routes.whatsapp.transport import chats

    ids: list[str] = []
    with db.admin_engine.begin() as c:
        acct = str(c.execute(text(
            "INSERT INTO wa_accounts (user_id, phone_number, phone_number_id, "
            "credentials_encrypted, organization_id) VALUES (:u, '+00 000 0000', :p, "
            "'enc', CAST(:o AS uuid)) RETURNING id"),
            {"u": ME, "p": f"pn-paging-{TAG}", "o": db.org_b}).scalar_one())
        for k in range(30):
            ids.append(str(c.execute(text(
                "INSERT INTO wa_chats (account_id, wa_chat_id, kind, name, last_message_at, "
                "organization_id) VALUES (CAST(:a AS uuid), :w, 'dm', :n, :at, "
                "CAST(:o AS uuid)) RETURNING id"),
                {"a": acct, "w": f"chat-{TAG}-{k}", "n": f"Chat {k}", "at": T0,
                 "o": db.org_b}).scalar_one()))
    user = _user(db.org_b, "feature:whatsapp")
    async with _as_member(db, db.org_b):
        got = {n: await _direct(chats.list_chats, stream="all", limit=n, user=user)
               for n in (21, 30)}
    rows = {n: [str(ch.id) for ch in got[n]] for n in got}
    assert rows[30] == _sorted_desc(ids)
    assert rows[21] == rows[30][:21], "a bigger LIMIT must begin with the smaller one"
