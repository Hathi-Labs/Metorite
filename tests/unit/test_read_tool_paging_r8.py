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

Part 2 (2026-10-15). Every other route that a paged tool reads ends its
ORDER BY on a unique key too: Reply Zero, the priority inbox, the WhatsApp
search, the cold senders, the two pattern lists, the deal contacts, the
company projects, the member's areas, the people list and the published
workflows. Each test below seeds 30 rows with one tie and reads them in the
order of the unique key. The sender list already broke its tie on the
address, and a test pins it. The rule history joins at most one draft for
each execution, because ``email_ai_drafts`` is keyed on the thread, and a
test pins that too.
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


def _sorted_asc(ids: list[str]) -> list[str]:
    return sorted(ids, key=lambda i: uuid.UUID(i).bytes)


@pytest.fixture
def db(promoted, app_engine):  # noqa: F811
    _assert_non_priv(app_engine)
    yield promoted
    with promoted.admin_engine.begin() as c:
        c.execute(text("DELETE FROM email_accounts WHERE user_id = :u"), {"u": ME})
        c.execute(text("DELETE FROM wa_accounts WHERE user_id = :u"), {"u": ME})
        c.execute(text("DELETE FROM pm_projects WHERE created_by = :u"), {"u": ME})
        c.execute(text("DELETE FROM workflows WHERE owner_email = :u"), {"u": ME})
        c.execute(text("DELETE FROM people WHERE source = :t"), {"t": f"paging-{TAG}"})
        c.execute(text("DELETE FROM crm_deals WHERE name = :t"), {"t": f"deal-{TAG}"})
        c.execute(text("DELETE FROM crm_contacts WHERE last_name = :t"), {"t": TAG})
        c.execute(text("DELETE FROM crm_deal_statuses WHERE name = :t"), {"t": f"st-{TAG}"})
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


# ── 3. Part 2: every other route a paged tool reads ─────────────────────────


def _messages(c: Any, box: str, org: str, rows: list[tuple[str, str]], *,
              at: str = ":r") -> list[str]:
    """Inbox mail of ``box``, one row for each ``(thread, sender)``, at one instant.

    ``at`` is an SQL expression, so a test that needs a recent instant can
    use the database clock and no clock of its own."""
    ids = []
    for k, (thread, sender) in enumerate(rows):
        ids.append(str(c.execute(text(
            "INSERT INTO email_messages (account_id, provider_message_id, thread_id, "
            "folder, from_address, to_addresses, subject, received_at, is_read, "
            "organization_id) VALUES (CAST(:a AS uuid), :p, :t, 'inbox', "
            "CAST(:f AS jsonb), CAST('[]' AS jsonb), :s, "
            f"{at}, false, CAST(:o AS uuid)) RETURNING id"),
            {"a": box, "p": f"p2-{TAG}-{uuid.uuid4().hex[:8]}-{k}", "t": thread,
             "f": json.dumps({"email": sender}), "s": f"s{k}", "r": T0,
             "o": org}).scalar_one()))
    return ids


async def test_reply_zero_breaks_a_tie_by_thread(db) -> None:
    from fastapi import BackgroundTasks
    from gateway.routes.email.automation import replyzero

    box = _account(db.admin_engine, org=db.org_b, owner=ME)
    threads = [uuid.uuid4().hex for _ in range(30)]
    with db.admin_engine.begin() as c:
        ids = _messages(c, box, db.org_b, [(t, "x@y.io") for t in threads])
        for thread, mid in zip(threads, ids, strict=True):
            c.execute(text(
                "INSERT INTO email_thread_status (account_id, thread_id, status, "
                "last_message_id, last_message_at, organization_id) VALUES "
                "(CAST(:a AS uuid), :t, 'NEEDS_REPLY', CAST(:m AS uuid), :at, "
                "CAST(:o AS uuid))"),
                {"a": box, "t": thread, "m": mid, "at": T0, "o": db.org_b})
    user = _user(db.org_b, "feature:email")
    async with _as_member(db, db.org_b):
        got = {n: await _direct(replyzero.reply_zero, background=BackgroundTasks(),
                                account_id=box, limit=n, user=user) for n in (11, 30)}
    rows = {n: [t["thread_id"] for t in got[n]["threads"]] for n in got}
    assert rows[30] == sorted(threads, reverse=True)
    assert rows[11] == rows[30][:11], "a bigger LIMIT must begin with the smaller one"


async def test_the_priority_inbox_breaks_a_tie_by_id(db) -> None:
    from gateway.routes.email.transport import messages

    box = _account(db.admin_engine, org=db.org_b, owner=ME)
    threads = [uuid.uuid4().hex for _ in range(30)]
    # One instant from the database clock, inside the 30-day window. A
    # transaction reads one now(), so every row shares it.
    recent = "now() - interval '1 day'"
    with db.admin_engine.begin() as c:
        ids = _messages(c, box, db.org_b, [(t, "x@y.io") for t in threads], at=recent)
        # A second message of each thread at the same instant: the newest of
        # a thread is a tie too, and the id must choose it.
        twins = _messages(c, box, db.org_b, [(t, "x@y.io") for t in threads], at=recent)
    user = _user(db.org_b, "feature:email")
    async with _as_member(db, db.org_b):
        got = {n: await _direct(messages.priority_inbox, account_id=box, limit=n,
                                user=user) for n in (11, 30)}
    rows = {n: [e["message_id"] for e in got[n]["emails"]] for n in got}
    heads = [_sorted_desc([one, two])[0] for one, two in zip(ids, twins, strict=True)]
    assert rows[30] == _sorted_desc(heads)
    assert rows[11] == rows[30][:11], "a bigger LIMIT must begin with the smaller one"


async def test_the_whatsapp_search_breaks_a_tie_by_id(db) -> None:
    from gateway.routes.whatsapp.transport import messages

    word = f"needle{TAG}"
    ids: list[str] = []
    with db.admin_engine.begin() as c:
        acct = str(c.execute(text(
            "INSERT INTO wa_accounts (user_id, phone_number, phone_number_id, "
            "credentials_encrypted, organization_id) VALUES (:u, '+00 000 0001', :p, "
            "'enc', CAST(:o AS uuid)) RETURNING id"),
            {"u": ME, "p": f"pn-search-{TAG}", "o": db.org_b}).scalar_one())
        chat = str(c.execute(text(
            "INSERT INTO wa_chats (account_id, wa_chat_id, kind, name, organization_id) "
            "VALUES (CAST(:a AS uuid), :w, 'dm', 'Ann', CAST(:o AS uuid)) RETURNING id"),
            {"a": acct, "w": f"search-{TAG}", "o": db.org_b}).scalar_one())
        for k in range(30):
            ids.append(str(c.execute(text(
                "INSERT INTO wa_messages (account_id, chat_id, wa_message_id, body_text, "
                "sent_at, organization_id) VALUES (CAST(:a AS uuid), CAST(:c AS uuid), "
                ":w, :b, :at, CAST(:o AS uuid)) RETURNING id"),
                {"a": acct, "c": chat, "w": f"wm-{TAG}-{k}", "b": f"price {word}",
                 "at": T0, "o": db.org_b}).scalar_one()))
    user = _user(db.org_b, "feature:whatsapp")
    async with _as_member(db, db.org_b):
        got = {n: await _direct(messages.search_messages, q=word, limit=n, user=user)
               for n in (11, 30)}
    rows = {n: [str(m.id) for m in got[n]] for n in got}
    assert rows[30] == _sorted_desc(ids)
    assert rows[11] == rows[30][:11], "a bigger LIMIT must begin with the smaller one"


async def test_the_email_lists_the_tools_page_break_a_tie_by_id(db) -> None:
    """The cold senders, the learned patterns and the rule patterns."""
    from gateway.routes.email.automation import assistant, rules, senders

    box = _account(db.admin_engine, org=db.org_b, owner=ME)
    cold: dict[str, str] = {}
    learned: list[str] = []
    patterns: list[str] = []
    with db.admin_engine.begin() as c:
        rule = str(c.execute(text(
            "INSERT INTO email_rules (account_id, name, organization_id) VALUES "
            "(CAST(:a AS uuid), 'r', CAST(:o AS uuid)) RETURNING id"),
            {"a": box, "o": db.org_b}).scalar_one())
        for k in range(30):
            cold_id = str(c.execute(text(
                "INSERT INTO email_cold_senders (account_id, from_email, updated_at, "
                "organization_id) VALUES (CAST(:a AS uuid), :f, :at, CAST(:o AS uuid)) "
                "RETURNING id"),
                {"a": box, "f": f"c{k}@cold.io", "at": T0, "o": db.org_b}).scalar_one())
            cold[cold_id] = f"c{k}@cold.io"
            learned.append(str(c.execute(text(
                "INSERT INTO email_learned_patterns (account_id, pattern, updated_at, "
                "organization_id) VALUES (CAST(:a AS uuid), :p, :at, CAST(:o AS uuid)) "
                "RETURNING id"),
                {"a": box, "p": f"say {k}", "at": T0, "o": db.org_b}).scalar_one()))
            patterns.append(str(c.execute(text(
                "INSERT INTO email_rule_patterns (account_id, rule_id, value, created_at, "
                "organization_id) VALUES (CAST(:a AS uuid), CAST(:r AS uuid), :v, :at, "
                "CAST(:o AS uuid)) RETURNING id"),
                {"a": box, "r": rule, "v": f"v{k}@p.io", "at": T0,
                 "o": db.org_b}).scalar_one()))
    user = _user(db.org_b, "feature:email")
    async with _as_member(db, db.org_b):
        got_cold = await senders.list_cold_senders(account_id=box, user=user)
        got_learned = await assistant.list_learned_patterns(account_id=box, user=user)
        got_rules = await rules.list_rule_patterns(account_id=box, user=user)
    assert [r["from_email"] for r in got_cold["cold_senders"]] == [
        cold[i] for i in _sorted_desc(list(cold))]
    assert [r["id"] for r in got_learned["patterns"]] == _sorted_desc(learned)
    assert [r["id"] for r in got_rules["patterns"]] == _sorted_desc(patterns)


async def test_the_sender_list_breaks_a_tie_by_address(db) -> None:
    """A pin: the route already ended its ORDER BY on the address."""
    from gateway.routes.email.automation import senders

    box = _account(db.admin_engine, org=db.org_b, owner=ME)
    names = [f"{uuid.uuid4().hex[:10]}@tie.io" for _ in range(30)]
    with db.admin_engine.begin() as c:
        _messages(c, box, db.org_b, [(f"st-{k}", n) for k, n in enumerate(names)])
    user = _user(db.org_b, "feature:email")
    async with _as_member(db, db.org_b):
        got = await _direct(senders.list_senders, account_id=box, limit=30, user=user)
    assert [s["email"] for s in got["senders"]] == sorted(names)


async def test_the_deal_contacts_break_a_tie_by_id(db) -> None:
    from gateway.routes.crm import deal_contacts

    ids: list[str] = []
    with db.admin_engine.begin() as c:
        status = str(c.execute(text(
            "INSERT INTO crm_deal_statuses (name, position, type, organization_id) "
            "VALUES (:n, 1, 'open', CAST(:o AS uuid)) RETURNING id"),
            {"n": f"st-{TAG}", "o": db.org_b}).scalar_one())
        deal = str(c.execute(text(
            "INSERT INTO crm_deals (name, status_id, organization_id) VALUES "
            "(:n, CAST(:s AS uuid), CAST(:o AS uuid)) RETURNING id"),
            {"n": f"deal-{TAG}", "s": status, "o": db.org_b}).scalar_one())
        for _ in range(30):
            contact = str(c.execute(text(
                "INSERT INTO crm_contacts (first_name, last_name, organization_id) "
                "VALUES ('Ann', :t, CAST(:o AS uuid)) RETURNING id"),
                {"t": TAG, "o": db.org_b}).scalar_one())
            c.execute(text(
                "INSERT INTO crm_deal_contacts (deal_id, contact_id, organization_id) "
                "VALUES (CAST(:d AS uuid), CAST(:c AS uuid), CAST(:o AS uuid))"),
                {"d": deal, "c": contact, "o": db.org_b})
            ids.append(contact)
    user = _user(db.org_b, "feature:crm")
    async with _as_member(db, db.org_b):
        got = await deal_contacts.list_deal_contacts(deal, user=user)
    assert [str(r.contact["id"]) for r in got.rows] == _sorted_asc(ids)


async def test_the_project_lists_break_a_tie_by_id(db) -> None:
    """The company projects (`/projects/nodes`) and the member's areas."""
    from gateway.routes.projects import personal, tree

    nodes: list[str] = []
    areas: list[str] = []
    with db.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO app_user (email, display_name, role, status, organization_id) "
            "VALUES (:e, :e, 'employee', 'active', CAST(:o AS uuid))"),
            {"e": ME, "o": db.org_b})
        for _ in range(30):
            node = str(c.execute(text(
                "INSERT INTO pm_projects (organization_id, name, source, created_by, "
                "owns_statuses) VALUES (CAST(:o AS uuid), 'Same', 'manual', :me, true) "
                "RETURNING id"),
                {"o": db.org_b, "me": ME}).scalar_one())
            c.execute(text(
                "INSERT INTO pm_project_grants (project_id, subject, created_by, "
                "organization_id) VALUES (CAST(:p AS uuid), 'org', :me, CAST(:o AS uuid))"),
                {"p": node, "me": ME, "o": db.org_b})
            nodes.append(node)
        root = str(c.execute(text(
            "INSERT INTO pm_projects (organization_id, name, source, created_by, "
            "personal_owner, owns_statuses) VALUES (CAST(:o AS uuid), 'Mine', 'manual', "
            ":me, :me, true) "
            "RETURNING id"), {"o": db.org_b, "me": ME}).scalar_one())
        for _ in range(30):
            areas.append(str(c.execute(text(
                "INSERT INTO pm_projects (organization_id, name, source, created_by, "
                "personal_owner, parent_project_id) VALUES (CAST(:o AS uuid), 'Area', "
                "'manual', :me, :me, CAST(:r AS uuid)) RETURNING id"),
                {"o": db.org_b, "me": ME, "r": root}).scalar_one()))
    user = _user(db.org_b, "feature:projects")
    async with _as_member(db, db.org_b):
        got_nodes = await tree.list_nodes(user=user)
        got_areas = await personal.list_my_areas(include_archived=False, user=user)
    mine = set(nodes)
    assert [str(r["id"]) for r in got_nodes["rows"] if str(r["id"]) in mine] == (
        _sorted_asc(nodes))
    assert [str(r["id"]) for r in got_areas["rows"]] == _sorted_asc(areas)


async def test_the_people_list_breaks_a_tie_by_id(db) -> None:
    from gateway.routes.tasks import people

    ids: list[str] = []
    with db.admin_engine.begin() as c:
        for _ in range(30):
            ids.append(str(c.execute(text(
                "INSERT INTO people (name, department, source, organization_id) VALUES "
                "(:n, 'Ops', :s, CAST(:o AS uuid)) RETURNING id"),
                {"n": f"Pat {TAG}", "s": f"paging-{TAG}", "o": db.org_b}).scalar_one()))
    user = _user(db.org_b, "feature:projects")
    async with _as_member(db, db.org_b):
        got = await people.list_people(q=TAG, include_inactive=False, user=user)
    # The member row of an earlier test can match the tag too, so only the
    # seeded rows count.
    mine = set(ids)
    assert [str(p.id) for p in got if str(p.id) in mine] == _sorted_asc(ids)


async def test_the_published_workflows_break_a_tie_by_id(db, monkeypatch) -> None:
    """The read opens the unbound ``get_db()`` on purpose (the module's H4
    rule), and under FORCE RLS that reads no row. So the test hands it a
    tenant-bound session, and checks the ORDER BY only."""
    from acb_common.db import tenant_session
    from gateway.routes.workflows import service

    class _Bound:
        """The bound session, with a ``close`` that leaves it to its scope."""

        def __init__(self, session: Any) -> None:
            self._session = session

        async def execute(self, *args: Any, **kwargs: Any) -> Any:
            return await self._session.execute(*args, **kwargs)

        async def close(self) -> None:
            return None

    ids: list[str] = []
    with db.admin_engine.begin() as c:
        for _ in range(30):
            ids.append(str(c.execute(text(
                "INSERT INTO workflows (name, owner_email, status, latest_version, "
                "organization_id) VALUES (:n, :me, 'published', 1, CAST(:o AS uuid)) "
                "RETURNING id"),
                {"n": f"Flow {TAG}", "me": ME, "o": db.org_b}).scalar_one()))
    mine = set(ids)
    # ⚠️ This test pins the ORDER only. The real path is broken under RLS:
    # `list_published_workflows` reads through the unbound `get_db()`, and
    # under FORCE RLS the app role (acb_app) sees 0 rows there. Production
    # counts 0 published workflows on that read. The patch below hands the
    # read a tenant-bound session, so this test passes where production
    # fails. A separate PR binds the tenant in the read. That fix will
    # replace this patch, and this test will then call the real path.
    async with _as_member(db, db.org_b), tenant_session() as session:
        async def _get_db() -> Any:
            return _Bound(session)

        monkeypatch.setattr(service, "_get_db", _get_db)
        got = await service.list_published_workflows()
    assert [w["id"] for w in got if w["id"] in mine] == _sorted_asc(ids)


# ── 4. The rule history joins one draft for each execution ─────────────────

async def test_the_rule_history_holds_each_execution_once(db) -> None:
    """A pin. ``email_ai_drafts`` is keyed on ``(account_id, thread_id)``, so
    the history's LEFT JOIN finds at most one draft, and a second draft for
    the thread cannot exist."""
    import sqlalchemy.exc
    from gateway.routes.email.automation import runner

    box = _account(db.admin_engine, org=db.org_b, owner=ME)
    thread = f"hist-{TAG}"
    with db.admin_engine.begin() as c:
        mid = _messages(c, box, db.org_b, [(thread, "x@y.io")])[0]
        executed = str(c.execute(text(
            "INSERT INTO email_executed_rules (account_id, rule_name, subject, status, "
            "message_id, organization_id) VALUES (CAST(:a AS uuid), 'r', 's', "
            "'APPLIED', CAST(:m AS uuid), CAST(:o AS uuid)) RETURNING id"),
            {"a": box, "m": mid, "o": db.org_b}).scalar_one())
        c.execute(text(
            "INSERT INTO email_ai_drafts (account_id, thread_id, draft_text, "
            "organization_id) VALUES (CAST(:a AS uuid), :t, 'first', CAST(:o AS uuid))"),
            {"a": box, "t": thread, "o": db.org_b})
    with pytest.raises(sqlalchemy.exc.IntegrityError), db.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO email_ai_drafts (account_id, thread_id, draft_text, "
            "organization_id) VALUES (CAST(:a AS uuid), :t, 'second', CAST(:o AS uuid))"),
            {"a": box, "t": thread, "o": db.org_b})
    user = _user(db.org_b, "feature:email")
    async with _as_member(db, db.org_b):
        got = await _direct(runner.rules_history, account_id=box, user=user)
    rows = [h for h in got["history"] if h["id"] == executed]
    assert len(rows) == 1 and rows[0]["draft_preview"] == "first"
