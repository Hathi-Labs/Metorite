"""R8 — the assistant's Knowledge proposals on a real database (WS-61 KB-5a).

Spec: ``project-docs/specs/knowledge_base.md`` §5.3, §8.1 (F1, F2), §12 and
the KB-5a row of §17. This file holds acceptance items 1, 4, 5, 6 and 7.
``tests/unit/test_kb_proposals.py`` holds 2, 3 and 8 with no database.

What this proves against a REAL Postgres, through the real ``/knowledge``
router, as the NON-privileged role of the phase-4 rehearsal (no superuser,
no BYPASSRLS), on a database with the whole ladder, the four generated
phases and two organizations:

1. the REAL ``propose_page`` tool, with the card approved, leaves one draft
   with ``origin.kind = agent_proposal``, one version with ``reason =
   agent_proposal`` and ``via = chat``, and no chunk;
4. the author accepts from the web: one ``accept`` version with ``via =
   web``, and the history names the proposer and the accepter. An edit that a
   writer accepts names the proposer and the decider on the proposal;
5. no ``write`` on the space is 403, and a page the member cannot see is the
   404 of a missing proposal. Only the author decides a proposed page (F1);
6. a second accept is 409, and an edit whose base moved is 409 and changes
   nothing;
7. a proposal of org A is 404 for org B, through the routes and under row
   level security;

and the table forces row level security, and the migration replays.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import text

from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    _URL,
    app_engine,
    promoted,
)
from tests.unit.test_kb_pages_r8 import (  # noqa: F401
    CAPABILITY,
    _address,
    _app_dsn,
    _assert_non_priv,
    _call,
    _ok,
    _principal,
    _space,
    ns,
)

# ⚠️ Every test here needs the real database. Without this gate the suite
# ERRORS where no ladder URL is set (the deploy run), and the deploy stops.
pytestmark = _DB_GATE

_MIGRATIONS = Path(__file__).resolve().parents[2] / "infra" / "postgres"

CHAT = {"X-Actor-Via": "chat:knowledge"}


def _proposals_migration() -> Path:
    """The migration that creates ``kb_page_proposals``, found by content (R1)."""
    found = [
        p for p in sorted(_MIGRATIONS.glob("*.sql"))
        if p.name != "schema.generated.sql"
        and "CREATE TABLE IF NOT EXISTS kb_page_proposals" in p.read_text(encoding="utf-8")
    ]
    assert len(found) == 1, [p.name for p in found]
    return found[0]


async def _run_tool(p: Any, user: Any, monkeypatch: Any, run: Any) -> str:
    """Run a REAL Knowledge tool against the real router, as *user*.

    The tool's ``httpx`` client reaches the router through an ASGI transport.
    The override binds the tenant from the user, as ``_with_resolved_access``
    does for a real request. The tool still sends its own headers.
    """
    import httpx
    from acb_auth import get_current_user
    from acb_common.db import bind_tenant, clear_tenant, release_tenant
    from acb_skills import knowledge_tools as kt
    from fastapi import FastAPI
    from gateway.routes import knowledge as kb_package

    from tests.unit._tenant_ladder import tenant_engine_scope

    async def _caller() -> Any:
        bind_tenant(user.organization_id)
        return user

    app = FastAPI()
    app.include_router(kb_package.router)
    app.dependency_overrides[get_current_user] = _caller
    real = httpx.AsyncClient
    scope = clear_tenant()
    try:
        async with tenant_engine_scope(_app_dsn(p)):
            transport = httpx.ASGITransport(app=app)
            with monkeypatch.context() as mp:
                mp.setattr(kt.httpx, "AsyncClient",
                           lambda *a, **k: real(*a, transport=transport, **k))
                mp.setenv("GATEWAY_URL", "http://kb.test")
                mp.setattr(kt, "_current_user_email", lambda: user.email)
                return await run()
    finally:
        release_tenant(scope)


def _approve(monkeypatch: Any, answer: Any = True) -> list[dict[str, Any]]:
    from acb_skills import ask_tools

    calls: list[dict[str, Any]] = []

    async def _card(*args: Any, **kwargs: Any) -> Any:
        calls.append(kwargs)
        return answer

    monkeypatch.setattr(ask_tools, "request_confirmation", _card)
    return calls


def _scalar(_ns: Any, sql: str, **params: Any) -> Any:
    with _ns.admin_engine.connect() as c:
        return c.execute(text(sql), params).scalar()


def _rows(_ns: Any, sql: str, **params: Any) -> list[Any]:
    with _ns.admin_engine.connect() as c:
        return list(c.execute(text(sql), params).fetchall())


async def _propose_edit(p: Any, user: Any, page_id: str, body: str, **extra: Any) -> dict:
    return await _ok(p, user, "POST", "/knowledge/proposals",
                     {"kind": "edit", "page_id": page_id, "body_md": body,
                      "summary": "A change.", **extra},
                     status=201, headers=CHAT)


async def _status(p: Any, user: Any, method: str, path: str, body: dict | None = None) -> int:
    return (await _call(p, user, method, path, body)).status_code


async def _published(p: Any, writer: Any, space: str, title: str, body: str) -> str:
    page = await _ok(p, writer, "POST", "/knowledge/pages",
                     {"space_id": space, "title": title, "body_md": body}, status=201)
    await _ok(p, writer, "POST", f"/knowledge/pages/{page['id']}/publish")
    return page["id"]


# ── Item 1: the real tool, with the card approved ──────────────────────────

async def test_propose_page_leaves_one_draft_one_version_and_no_chunk(
    ns, app_engine, monkeypatch,  # noqa: F811
) -> None:
    _assert_non_priv(app_engine)
    from acb_skills import knowledge_tools as kt

    admin = _principal(_address("admin"), ns.org_a, CAPABILITY)
    member_email = _address("member")
    member = _principal(member_email, ns.org_a)
    space = await _space(ns, admin, "Support", [{"subject": member_email, "level": "write"}])
    cards = _approve(monkeypatch)

    out = await _run_tool(ns, member, monkeypatch, lambda: kt.propose_page(
        title="Refund policy", body="# Refunds\n\nThirty days.",
        summary="What the member said.", space_id=space))
    assert len(cards) == 1
    assert out.startswith("Filed a proposed new page.")

    pages = _rows(ns, "SELECT id, state, origin, author_email, organization_id::text "
                      "FROM kb_pages WHERE title = 'Refund policy'")
    assert len(pages) == 1
    pid, state, origin, author, org = pages[0]
    origin = origin if isinstance(origin, dict) else json.loads(origin)
    assert (state, author, org) == ("draft", member_email.lower(), ns.org_a)
    assert origin["kind"] == "agent_proposal"
    assert origin["via"] == "chat" and origin["proposer"] == member_email.lower()
    versions = _rows(ns, "SELECT version, reason, via, author_email FROM kb_page_versions "
                         "WHERE page_id = :p ORDER BY version", p=pid)
    assert [tuple(v) for v in versions] == [(1, "agent_proposal", "chat", member_email.lower())]
    assert _scalar(ns, "SELECT count(*) FROM kb_chunks WHERE source_id = :p", p=pid) == 0
    assert f"/knowledge/pages/{pid}" in out

    # A declined card, through the same real path, writes nothing.
    _approve(monkeypatch, False)
    out = await _run_tool(ns, member, monkeypatch, lambda: kt.propose_page(
        title="Second", body="x", space_id=space))
    assert out == kt.DECLINED or out == kt.NO_CARD
    assert _scalar(ns, "SELECT count(*) FROM kb_pages WHERE title = 'Second'") == 0


async def test_propose_edit_by_the_tool_files_one_row_at_the_page_version(
    ns, app_engine, monkeypatch,  # noqa: F811
) -> None:
    _assert_non_priv(app_engine)
    from acb_skills import knowledge_tools as kt

    admin = _principal(_address("admin"), ns.org_a, CAPABILITY)
    writer_email = _address("writer")
    writer = _principal(writer_email, ns.org_a)
    space = await _space(ns, admin, "Ops", [{"subject": writer_email, "level": "write"}])
    pid = await _published(ns, writer, space, "Runbook", "old steps")
    _approve(monkeypatch)
    out = await _run_tool(ns, writer, monkeypatch, lambda: kt.propose_edit(
        page_id=pid, body="new steps", summary="Two steps change."))
    assert out.startswith("Filed a proposed edit.")
    rows = _rows(ns, "SELECT base_version, status, via, proposer_email, body_md "
                     "FROM kb_page_proposals WHERE page_id = :p", p=pid)
    assert [tuple(r) for r in rows] == [(2, "pending", "chat", writer_email.lower(), "new steps")]
    # The page is unchanged and stays published.
    page = await _ok(ns, writer, "GET", f"/knowledge/pages/{pid}")
    assert (page["state"], page["body_md"], page["versions"][0]["version"]) == (
        "published", "old steps", 2)


# ── Item 4: accept, and the history ─────────────────────────────────────────

async def test_the_author_accepts_from_the_web_and_the_history_names_both(
    ns, app_engine,  # noqa: F811
) -> None:
    _assert_non_priv(app_engine)
    admin = _principal(_address("admin"), ns.org_a, CAPABILITY)
    author_email = _address("author")
    author = _principal(author_email, ns.org_a)
    space = await _space(ns, admin, "Wiki", [{"subject": author_email, "level": "write"}])
    made = await _ok(ns, author, "POST", "/knowledge/proposals",
                     {"kind": "page", "space_id": space, "title": "Leave", "body_md": "ten days",
                      "summary": "From the chat."}, status=201, headers=CHAT)
    pid = made["id"]
    assert (made["kind"], made["status"], made["proposer_email"]) == (
        "page", "pending", author_email.lower())

    listed = await _ok(ns, author, "GET", "/knowledge/proposals?status=pending")
    assert [r["id"] for r in listed["proposals"]] == [pid]

    # The chat cannot accept, even as the author (F2).
    assert (await _call(ns, author, "POST", f"/knowledge/proposals/{pid}/accept",
                        headers=CHAT)).status_code == 403
    accepted = await _ok(ns, author, "POST", f"/knowledge/proposals/{pid}/accept")
    assert accepted["status"] == "accepted" and accepted["page"]["state"] == "published"

    page = await _ok(ns, author, "GET", f"/knowledge/pages/{pid}")
    history = [(v["version"], v["reason"], v["via"], v["author_email"]) for v in page["versions"]]
    assert history == [
        (2, "accept", "web", author_email.lower()),
        (1, "agent_proposal", "chat", author_email.lower()),
    ]
    assert page["origin"]
    assert _scalar(ns, "SELECT count(*) FROM kb_chunks WHERE source_id = :p", p=pid) >= 1
    assert (await _ok(ns, author, "GET", "/knowledge/proposals?status=pending"))[
        "proposals"] == []


async def test_a_writer_accepts_an_edit_and_the_record_names_proposer_and_decider(
    ns, app_engine,  # noqa: F811
) -> None:
    _assert_non_priv(app_engine)
    admin = _principal(_address("admin"), ns.org_a, CAPABILITY)
    proposer_email, editor_email, reader_email = (
        _address("proposer"), _address("editor"), _address("reader"))
    proposer = _principal(proposer_email, ns.org_a)
    editor = _principal(editor_email, ns.org_a)
    reader = _principal(reader_email, ns.org_a)
    space = await _space(ns, admin, "Eng", [
        {"subject": proposer_email, "level": "write"},
        {"subject": editor_email, "level": "write"},
        {"subject": reader_email, "level": "read"},
    ])
    pid = await _published(ns, editor, space, "Deploys", "old")
    made = await _propose_edit(ns, proposer, pid, "new", title="Deploy guide")
    prop = made["id"]
    assert (made["base_version"], made["status"]) == (2, "pending")

    # F1: the writer sees it, the reader does not.
    seen = await _ok(ns, editor, "GET", "/knowledge/proposals")
    assert [r["id"] for r in seen["proposals"]] == [prop]
    assert (await _ok(ns, reader, "GET", "/knowledge/proposals"))["proposals"] == []
    assert await _status(ns, reader, "POST", f"/knowledge/proposals/{prop}/accept") == 404

    accepted = await _ok(ns, editor, "POST", f"/knowledge/proposals/{prop}/accept")
    assert (accepted["status"], accepted["proposer_email"], accepted["decided_by"]) == (
        "accepted", proposer_email.lower(), editor_email.lower())
    assert accepted["decided_at"]
    page = await _ok(ns, reader, "GET", f"/knowledge/pages/{pid}")
    assert (page["title"], page["body_md"], page["state"]) == ("Deploy guide", "new", "published")
    top = page["versions"][0]
    assert (top["version"], top["reason"], top["via"], top["author_email"]) == (
        3, "accept", "web", editor_email.lower())
    done = await _ok(ns, proposer, "GET", "/knowledge/proposals?status=accepted")
    assert [(r["id"], r["proposer_email"], r["decided_by"]) for r in done["proposals"]] == [
        (prop, proposer_email.lower(), editor_email.lower())]


# ── Item 5: 403 and 404 ─────────────────────────────────────────────────────

async def test_no_write_is_403_and_a_hidden_page_is_the_404_of_a_missing_one(
    ns, app_engine,  # noqa: F811
) -> None:
    _assert_non_priv(app_engine)
    admin = _principal(_address("admin"), ns.org_a, CAPABILITY)
    writer_email, reader_email = _address("writer"), _address("reader")
    writer = _principal(writer_email, ns.org_a)
    reader = _principal(reader_email, ns.org_a)
    outsider = _principal(_address("outsider"), ns.org_a)
    space = await _space(ns, admin, "Sales", [
        {"subject": writer_email, "level": "write"},
        {"subject": reader_email, "level": "read"},
    ])
    pid = await _published(ns, writer, space, "Pricing", "list price")

    # A reader may not propose a page in the space, nor an edit of its page.
    assert await _status(ns, reader, "POST", "/knowledge/proposals",
                         {"kind": "page", "space_id": space, "title": "X"}) == 403
    assert await _status(ns, reader, "POST", "/knowledge/proposals",
                         {"kind": "edit", "page_id": pid, "body_md": "y"}) == 403

    # A member who cannot see the page gets the answer of a page that is not there.
    hidden = await _call(ns, outsider, "POST", "/knowledge/proposals",
                         {"kind": "edit", "page_id": pid, "body_md": "y"})
    missing = await _call(ns, outsider, "POST", "/knowledge/proposals",
                          {"kind": "edit", "page_id": str(uuid.uuid4()), "body_md": "y"})
    assert hidden.status_code == missing.status_code == 404
    assert hidden.json() == missing.json()

    # The same for a decision on a proposal of that page.
    prop = (await _propose_edit(ns, writer, pid, "new price"))["id"]
    hidden = await _call(ns, outsider, "POST", f"/knowledge/proposals/{prop}/accept")
    missing = await _call(ns, outsider, "POST", f"/knowledge/proposals/{uuid.uuid4()}/accept")
    assert hidden.status_code == missing.status_code == 404
    assert hidden.json() == missing.json()

    # The proposer who lost `write` sees the proposal, and may not decide it.
    await _ok(ns, admin, "PUT", f"/knowledge/spaces/{space}/grants",
              {"grants": [{"subject": writer_email, "level": "read"}]})
    assert await _status(ns, writer, "POST", f"/knowledge/proposals/{prop}/accept") == 403
    assert _scalar(ns, "SELECT status FROM kb_page_proposals WHERE id = :i", i=prop) == "pending"


async def test_only_the_author_decides_a_proposed_page(ns, app_engine) -> None:  # noqa: F811
    """F1: a named reviewer with ``write`` sees the draft and still cannot
    decide it. The answer is the 404 of a missing proposal."""
    _assert_non_priv(app_engine)
    admin = _principal(_address("admin"), ns.org_a, CAPABILITY)
    author_email, other_email = _address("author"), _address("other")
    author = _principal(author_email, ns.org_a)
    other = _principal(other_email, ns.org_a)
    space = await _space(ns, admin, "Team", [
        {"subject": author_email, "level": "write"},
        {"subject": other_email, "level": "write"},
    ])
    pid = (await _ok(ns, author, "POST", "/knowledge/proposals",
                     {"kind": "page", "space_id": space, "title": "Onboarding"},
                     status=201, headers=CHAT))["id"]
    await _ok(ns, author, "PATCH", f"/knowledge/pages/{pid}", {"reviewers": [other_email]})
    assert (await _ok(ns, other, "GET", f"/knowledge/pages/{pid}"))["state"] == "draft"
    for act in ("accept", "reject"):
        assert await _status(ns, other, "POST", f"/knowledge/proposals/{pid}/{act}") == 404
    assert (await _ok(ns, other, "GET", "/knowledge/proposals"))["proposals"] == []
    # A page that no agent proposed is no proposal, even for its author.
    plain = (await _ok(ns, author, "POST", "/knowledge/pages",
                       {"space_id": space, "title": "Plain"}, status=201))["id"]
    assert await _status(ns, author, "POST", f"/knowledge/proposals/{plain}/accept") == 404
    assert (await _ok(ns, author, "GET", f"/knowledge/pages/{plain}"))["state"] == "draft"


# ── Item 6: 409 ─────────────────────────────────────────────────────────────

async def test_a_second_accept_and_a_moved_base_are_409(ns, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    admin = _principal(_address("admin"), ns.org_a, CAPABILITY)
    writer_email = _address("writer")
    writer = _principal(writer_email, ns.org_a)
    space = await _space(ns, admin, "Docs", [{"subject": writer_email, "level": "write"}])

    # A proposed page: accept, then accept again, then reject.
    page = (await _ok(ns, writer, "POST", "/knowledge/proposals",
                      {"kind": "page", "space_id": space, "title": "FAQ"},
                      status=201, headers=CHAT))["id"]
    await _ok(ns, writer, "POST", f"/knowledge/proposals/{page}/accept")
    assert await _status(ns, writer, "POST", f"/knowledge/proposals/{page}/accept") == 409
    assert await _status(ns, writer, "POST", f"/knowledge/proposals/{page}/reject") == 409
    assert _scalar(ns, "SELECT count(*) FROM kb_page_versions WHERE page_id = :p "
                       "AND reason = 'accept'", p=page) == 1

    # An edit: a second accept is 409 and writes no second version.
    pid = await _published(ns, writer, space, "Guide", "v1")
    prop = (await _propose_edit(ns, writer, pid, "v2"))["id"]
    await _ok(ns, writer, "POST", f"/knowledge/proposals/{prop}/accept")
    assert await _status(ns, writer, "POST", f"/knowledge/proposals/{prop}/accept") == 409
    assert _scalar(ns, "SELECT count(*) FROM kb_page_versions WHERE page_id = :p "
                       "AND reason = 'accept'", p=pid) == 1

    # An edit whose base moved: 409, and the page keeps the newer text.
    stale = (await _propose_edit(ns, writer, pid, "v3 from an old read"))["id"]
    await _ok(ns, writer, "PATCH", f"/knowledge/pages/{pid}", {"body_md": "v3 by a person"})
    assert await _status(ns, writer, "POST", f"/knowledge/proposals/{stale}/accept") == 409
    assert (await _ok(ns, writer, "GET", f"/knowledge/pages/{pid}"))["body_md"] == (
        "v3 by a person")
    assert _scalar(ns, "SELECT status FROM kb_page_proposals WHERE id = :i", i=stale) == "pending"
    # It can still be rejected, and then it is decided.
    rejected = await _ok(ns, writer, "POST", f"/knowledge/proposals/{stale}/reject")
    assert rejected["status"] == "rejected"
    assert await _status(ns, writer, "POST", f"/knowledge/proposals/{stale}/accept") == 409


async def test_reject_archives_a_proposed_page_as_a_draft(ns, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    admin = _principal(_address("admin"), ns.org_a, CAPABILITY)
    writer_email = _address("writer")
    writer = _principal(writer_email, ns.org_a)
    space = await _space(ns, admin, "Notes", [{"subject": writer_email, "level": "write"}])
    pid = (await _ok(ns, writer, "POST", "/knowledge/proposals",
                     {"kind": "page", "space_id": space, "title": "Idea"},
                     status=201, headers=CHAT))["id"]
    out = await _ok(ns, writer, "POST", f"/knowledge/proposals/{pid}/reject")
    assert out["status"] == "rejected"
    row = _rows(ns, "SELECT state, archived_from_state FROM kb_pages WHERE id = :p", p=pid)
    assert tuple(row[0]) == ("archived", "draft")
    listed = await _ok(ns, writer, "GET", "/knowledge/proposals?status=rejected")
    assert [r["id"] for r in listed["proposals"]] == [pid]


# ── Item 7: another tenant ──────────────────────────────────────────────────

async def test_a_proposal_of_org_a_is_404_for_org_b(ns, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    admin_a = _principal(_address("admin-a"), ns.org_a, CAPABILITY)
    admin_b = _principal(_address("admin-b"), ns.org_b, CAPABILITY)
    writer_email = _address("writer")
    writer = _principal(writer_email, ns.org_a)
    space = await _space(ns, admin_a, "Finance", [{"subject": writer_email, "level": "write"}])
    pid = await _published(ns, writer, space, "Budget", "v1")
    edit = (await _propose_edit(ns, writer, pid, "v2"))["id"]
    page = (await _ok(ns, writer, "POST", "/knowledge/proposals",
                      {"kind": "page", "space_id": space, "title": "Plan"},
                      status=201, headers=CHAT))["id"]

    for prop in (edit, page):
        for act in ("accept", "reject"):
            assert await _status(ns, admin_b, "POST", f"/knowledge/proposals/{prop}/{act}") == 404
    assert await _status(ns, admin_b, "POST", "/knowledge/proposals",
                         {"kind": "edit", "page_id": pid, "body_md": "x"}) == 404
    for status in ("pending", "accepted", "rejected"):
        listed = await _ok(ns, admin_b, "GET", f"/knowledge/proposals?status={status}")
        assert listed["proposals"] == []
    # Nothing moved in org A.
    assert _scalar(ns, "SELECT status FROM kb_page_proposals WHERE id = :i", i=edit) == "pending"
    assert _scalar(ns, "SELECT state FROM kb_pages WHERE id = :i", i=page) == "draft"


def test_row_level_security_hides_the_row_from_the_other_tenant(ns, app_engine) -> None:  # noqa: F811
    """Under the non-privileged role, org B's tenant reads no row of org A,
    and cannot write a row for org A."""
    _assert_non_priv(app_engine)
    with ns.admin_engine.begin() as c:
        space = c.execute(text(
            "INSERT INTO kb_spaces (organization_id, slug, name, kind) "
            "VALUES (CAST(:o AS uuid), 'rls', 'RLS', 'team') RETURNING id"),
            {"o": ns.org_a}).scalar()
        page = c.execute(text(
            "INSERT INTO kb_pages (organization_id, space_id, title, author_email) "
            "VALUES (CAST(:o AS uuid), :s, 'P', 'a@x.test') RETURNING id"),
            {"o": ns.org_a, "s": space}).scalar()
        c.execute(text(
            "INSERT INTO kb_page_proposals (organization_id, page_id, base_version, "
            "title, proposer_email) VALUES (CAST(:o AS uuid), :p, 1, 'P', 'a@x.test')"),
            {"o": ns.org_a, "p": page})
    with app_engine.begin() as c:
        c.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": ns.org_b})
        assert c.execute(text("SELECT count(*) FROM kb_page_proposals")).scalar() == 0
    from sqlalchemy.exc import DBAPIError

    with pytest.raises(DBAPIError), app_engine.begin() as c:
        c.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": ns.org_b})
        c.execute(text(
            "INSERT INTO kb_page_proposals (organization_id, page_id, base_version, "
            "title, proposer_email) VALUES (CAST(:o AS uuid), :p, 1, 'P', 'b@x.test')"),
            {"o": ns.org_a, "p": page})
    with app_engine.begin() as c:
        c.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": ns.org_a})
        assert c.execute(text("SELECT count(*) FROM kb_page_proposals")).scalar() == 1


# ── The migration ───────────────────────────────────────────────────────────

def test_the_table_forces_row_level_security_and_the_migration_replays(ns) -> None:  # noqa: F811
    sql = _proposals_migration().read_text(encoding="utf-8")
    for _ in range(2):
        with ns.admin_engine.begin() as c, c.connection.dbapi_connection.cursor() as cur:
            cur.execute(sql)
    with ns.admin_engine.connect() as c:
        forced = c.execute(text(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
            "WHERE oid = 'kb_page_proposals'::regclass")).first()
        policies = c.execute(text(
            "SELECT count(*) FROM pg_policies WHERE tablename = 'kb_page_proposals'")).scalar()
        indexes = c.execute(text(
            "SELECT count(*) FROM pg_indexes WHERE tablename = 'kb_page_proposals' "
            "AND indexname = 'idx_kb_page_proposals_pending'")).scalar()
    assert tuple(forced) == (True, True)
    assert policies == 1
    assert indexes == 1


def test_a_concurrent_double_accept_writes_one_version(ns, app_engine) -> None:  # noqa: F811
    """Two accepts at once: the page row lock orders them, and the second
    is 409. One ``accept`` version, never two."""
    from tests.unit.test_kb_pages_r8 import _calls

    async def _go() -> list[int]:
        admin = _principal(_address("admin"), ns.org_a, CAPABILITY)
        writer_email = _address("writer")
        writer = _principal(writer_email, ns.org_a)
        space = await _space(ns, admin, "Race", [{"subject": writer_email, "level": "write"}])
        pid = await _published(ns, writer, space, "Race", "v1")
        prop = (await _propose_edit(ns, writer, pid, "v2"))["id"]
        path = f"/knowledge/proposals/{prop}/accept"
        answers = await _calls(ns, writer, [("POST", path, None), ("POST", path, None)])
        assert _scalar(ns, "SELECT count(*) FROM kb_page_versions WHERE page_id = :p "
                           "AND reason = 'accept'", p=pid) == 1
        return sorted(a.status_code for a in answers)

    assert asyncio.run(_go()) == [200, 409]
