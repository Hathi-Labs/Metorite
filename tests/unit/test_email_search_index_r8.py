"""WS-17 — mail search uses its GIN index under RLS (incident 2026-10-18).

Spec: ``project-docs/specs/email_app_master_plan.md``, "Incident 2026-10-18:
search speed". Migration ``*_email_search_index.sql`` (284 at build time).

**The incident.** On WhatsApp, 7 of 12 ``query_inbox`` calls of one member died
on the 30 s statement timeout. Each ran the COUNT(*) of ``GET /email/messages``
with an inline ``to_tsvector(...) @@ websearch_to_tsquery(...)``. The ``@@`` of
tsvector is not LEAKPROOF, so under FORCE RLS the planner may not use a GIN
index for it. The app role read every body of the organization on each call.

R7 fences named here:

* ``email-search-gin-under-rls`` (R8): the function
  ``email_message_search`` reads ``email_messages`` through
  ``idx_email_messages_fts_all``, never by a scan with the vector in a Filter.
  RED when the index is dropped or its expression drifts from the function.
* ``email-search-route-by-pkey`` (R8): as the app role, the route predicate
  ``core.text_match_sql`` reaches the rows by primary key and computes no
  ``to_tsvector``. RED when a route goes back to the inline match.
* ``email-search-semantics`` (R8): a name in From, a name in To, a name in Cc
  and a quoted phrase in the body each find their mail, through the real
  ``list_messages`` and ``search_messages``, as the app role.
* ``email-search-tenant`` (R8): the function gives the ids of the bound
  organization only, and nothing when no organization is bound. PUBLIC holds
  no EXECUTE.

Run (real Postgres)::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_email_search_index_r8.py -v -rs
"""
from __future__ import annotations

import uuid

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth.roles import UserContext, UserRole
from acb_common.db import bind_tenant, release_tenant
from gateway.routes.email.core import text_match_sql
from gateway.routes.email.transport import messages as m
from gateway.routes.email.transport import search as s
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope

# ``promoted`` and ``app_engine`` are used by name for fixture injection, so
# the import is load-bearing even though it reads as unused.
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _APP_ROLE,
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

FN = "public.email_message_search(text)"
INDEX = "idx_email_messages_fts_all"
OWNER = "owner@ms-search.test"

# FastAPI Query() defaults do not resolve on a direct call, so every filter
# gets its real "off" value (as in test_email_all_inboxes.py).
_LIST_OFF = {
    "account_id": None, "folder": "all", "label": None, "uncategorized": False,
    "query": None, "thread_id": None, "received_after": None,
    "received_before": None, "is_read": None, "is_starred": None,
    "has_attachments": None, "importance": None, "from_email": None,
    "sender_category": None, "sort": "newest", "collapse": False,
    "page": 1, "page_size": 50,
}
_SEARCH_OFF = {
    "q": None, "account_id": None, "folder": None, "label": None,
    "labels": None, "uncategorized": False, "from_addr": None, "to_addr": None,
    "received_after": None, "received_before": None, "is_read": None,
    "is_starred": None, "has_attachments": None, "sender_category": None,
    "importance": None, "hybrid": False, "light": True, "page": 1,
    "page_size": 50,
}

_FILLER = 1500


def _insert(c, *, org: str, account: str, pid: str, frm: str, to: str = "[]",
            cc: str = "[]", subject: str = "s", body: str = "b") -> None:
    c.execute(text(
        "INSERT INTO email_messages (account_id, provider_message_id, thread_id,"
        " folder, from_address, to_addresses, cc_addresses, subject, body_text,"
        " received_at, organization_id) VALUES (CAST(:a AS uuid), :p, :p,"
        " 'inbox', CAST(:f AS jsonb), CAST(:t AS jsonb), CAST(:cc AS jsonb),"
        " :s, :b, now(), CAST(:o AS uuid))"),
        {"a": account, "p": pid, "f": frm, "t": to, "cc": cc, "s": subject,
         "b": body, "o": org})


@pytest.fixture(scope="module")
def seeded(promoted):  # noqa: F811
    """Two organizations. Org B holds the needles and some filler. Org A holds
    a mail with the SAME words, so a leak across tenants would show."""
    p = promoted
    eng = p.admin_engine
    with eng.begin() as c:
        c.execute(text(f"GRANT EXECUTE ON FUNCTION {FN} TO {_APP_ROLE}"))
        ids = {}
        for org in (p.org_a, p.org_b):
            ids[org] = str(c.execute(text(
                "INSERT INTO email_accounts (user_id, provider, email_address, "
                "credentials_encrypted, organization_id) VALUES (:u, 'microsoft',"
                " :m, 'x', CAST(:o AS uuid)) RETURNING id"),
                {"u": OWNER, "m": f"box-{uuid.uuid4().hex[:8]}@ms-search.test",
                 "o": org}).scalar_one())
        box_b, box_a = ids[p.org_b], ids[p.org_a]
        # Filler, so the planner has a table to choose a scan over.
        c.execute(text(
            "INSERT INTO email_messages (account_id, provider_message_id, folder,"
            " from_address, to_addresses, subject, body_text, received_at,"
            " organization_id) SELECT CAST(:a AS uuid), 'fill-' || g, 'inbox',"
            " jsonb_build_object('name', 'Vendor ' || g, 'email',"
            " 'v' || g || '@vendor.test'), '[]'::jsonb, 'Invoice ' || g,"
            " repeat('quarterly shipment schedule update ', 20) || md5(g::text),"
            " now() - make_interval(mins => g), CAST(:o AS uuid)"
            " FROM generate_series(1, :n) g"),
            {"a": box_b, "o": p.org_b, "n": _FILLER})
        _insert(c, org=p.org_b, account=box_b, pid="needle-from-to",
                frm='{"name": "Sanjeev Goel", "email": "sanjeev.goel@fracktal.test"}',
                to='[{"name": "Payal Dave", "email": "payal.dave@fracktal.test"}]',
                subject="Pending purchase order",
                body="A reminder on the purchase order for the spares.")
        _insert(c, org=p.org_b, account=box_b, pid="needle-cc",
                frm='{"name": "Ravi Rao", "email": "ravi@vendor.test"}',
                cc='[{"name": "Meera Iyer", "email": "meera.iyer@fracktal.test"}]',
                subject="Status", body="Copying the team on this one.")
        _insert(c, org=p.org_b, account=box_b, pid="needle-phrase",
                frm='{"name": "Arjun Nair", "email": "arjun@vendor.test"}',
                subject="Shipping",
                body="The consignment of carbide nozzles left the warehouse.")
        # Org A: the same words. It must never surface for org B.
        _insert(c, org=p.org_a, account=box_a, pid="other-org",
                frm='{"name": "Sanjeev Goel", "email": "sanjeev.goel@other.test"}',
                to='[{"name": "Payal Dave", "email": "payal.dave@other.test"}]',
                subject="Pending purchase order",
                body="The consignment of carbide nozzles, again.")
        c.execute(text("ANALYZE email_messages"))
    yield p
    with eng.begin() as c:
        for box in (box_a, box_b):
            c.execute(text("DELETE FROM email_messages WHERE account_id = CAST(:a AS uuid)"),
                      {"a": box})
            c.execute(text("DELETE FROM email_accounts WHERE id = CAST(:a AS uuid)"),
                      {"a": box})


def _function_plan(admin_engine, org: str, query: str) -> str:
    """The plan of the SELECT inside the function, through auto_explain.

    The admin connection is the superuser that owns the function in this
    catalog, as BYPASSRLS ``postgres`` owns it on production. The plan inside a
    SECURITY DEFINER body is the owner's plan for every caller."""
    notes: list[str] = []
    with admin_engine.connect() as c:
        raw = c.connection.dbapi_connection
        raw.add_notice_handler(lambda d: notes.append(d.message_primary or ""))
        for stmt in (
            "LOAD 'auto_explain'",
            "SET auto_explain.log_min_duration = 0",
            "SET auto_explain.log_nested_statements = on",
            "SET auto_explain.log_level = notice",
            "SET client_min_messages = notice",
        ):
            c.execute(text(stmt))
        c.execute(text("SELECT set_config('app.tenant_id', :o, false)"), {"o": org})
        c.execute(text(f"SELECT count(*) FROM {FN.split('(')[0]}(:q)"), {"q": query})
        c.execute(text("RESET app.tenant_id"))
    inner = [n for n in notes if "FROM public.email_messages m" in n]
    assert inner, f"auto_explain gave no plan for the function body: {notes!r}"
    return "\n".join(inner)


class TestTheIndexServesTheSearch:

    def test_the_function_reads_through_the_gin_index(self, seeded):
        plan = _function_plan(seeded.admin_engine, seeded.org_b,
                              "Sanjeev Goel Payal Dave")
        assert INDEX in plan, plan
        assert "Seq Scan on email_messages" not in plan, plan
        # The vector must be an index condition, never a per-row Filter.
        for line in plan.splitlines():
            if line.strip().startswith("Filter:"):
                assert "to_tsvector" not in line, plan

    def test_the_route_predicate_reads_rows_by_primary_key(self, seeded, app_engine):  # noqa: F811
        with app_engine.connect() as c:
            role = c.execute(text(
                "SELECT rolsuper, rolbypassrls FROM pg_roles "
                "WHERE rolname = current_user")).first()
            assert role is not None and not role[0] and not role[1]
            c.execute(text("SELECT set_config('app.tenant_id', :o, false)"),
                      {"o": seeded.org_b})
            plan = "\n".join(r[0] for r in c.execute(text(
                "EXPLAIN SELECT count(*) FROM email_messages em WHERE "
                + text_match_sql("query")), {"query": "Sanjeev Goel"}))
        assert "to_tsvector" not in plan, plan
        assert "Seq Scan on email_messages" not in plan, plan
        assert "email_messages_pkey" in plan, plan


class TestTheSearchStillFindsWhatMembersAskFor:

    async def _list(self, seeded, query: str) -> dict:
        me = UserContext(email=OWNER, role=UserRole.EMPLOYEE,
                         organization_id=seeded.org_b)
        token = bind_tenant(seeded.org_b)
        try:
            async with tenant_engine_scope(
                    seeded.app_url.render_as_string(hide_password=False)):
                return await m.list_messages(user=me, **{**_LIST_OFF, "query": query})
        finally:
            release_tenant(token)

    @staticmethod
    def _pids(out: dict) -> set[str]:
        return {e["provider_message_id"] for e in out["emails"]}

    async def test_a_name_in_from_matches(self, seeded):
        out = await self._list(seeded, "Sanjeev Goel")
        assert self._pids(out) == {"needle-from-to"} and out["total"] == 1

    async def test_a_name_in_to_matches(self, seeded):
        out = await self._list(seeded, "Payal Dave")
        assert self._pids(out) == {"needle-from-to"} and out["total"] == 1

    async def test_the_incident_query_matches_from_and_to_together(self, seeded):
        out = await self._list(seeded, "Sanjeev Goel Payal Dave")
        assert self._pids(out) == {"needle-from-to"} and out["total"] == 1

    async def test_a_name_in_cc_matches(self, seeded):
        out = await self._list(seeded, "Meera Iyer")
        assert self._pids(out) == {"needle-cc"}

    async def test_a_phrase_in_the_body_matches_and_keeps_its_order(self, seeded):
        out = await self._list(seeded, '"carbide nozzles"')
        assert self._pids(out) == {"needle-phrase"}
        out = await self._list(seeded, '"nozzles carbide"')
        assert self._pids(out) == set() and out["total"] == 0

    async def test_or_still_unions(self, seeded):
        out = await self._list(seeded, "Meera OR carbide")
        assert self._pids(out) == {"needle-cc", "needle-phrase"}

    async def test_the_search_route_finds_the_recipient_too(self, seeded):
        me = UserContext(email=OWNER, role=UserRole.EMPLOYEE,
                         organization_id=seeded.org_b)
        token = bind_tenant(seeded.org_b)
        try:
            async with tenant_engine_scope(
                    seeded.app_url.render_as_string(hide_password=False)):
                out = await s.search_messages(
                    user=me, **{**_SEARCH_OFF, "q": "Sanjeev Goel Payal Dave"})
        finally:
            release_tenant(token)
        assert {e["provider_message_id"] for e in out["emails"]} == {"needle-from-to"}
        assert out["total"] == 1


class TestTheFunctionKeepsTheTenant:

    def test_it_gives_only_the_bound_organization(self, seeded, app_engine):  # noqa: F811
        with app_engine.connect() as c:
            for org, want in ((seeded.org_b, "needle-from-to"),
                              (seeded.org_a, "other-org")):
                c.execute(text("SELECT set_config('app.tenant_id', :o, false)"),
                          {"o": org})
                got = c.execute(text(
                    "SELECT provider_message_id FROM email_messages WHERE id IN "
                    "(SELECT public.email_message_search('Sanjeev Goel'))")).scalars().all()
                assert got == [want]
            # The ids alone, read with RLS out of the way: still one org only.
            c.execute(text("SELECT set_config('app.tenant_id', :o, false)"),
                      {"o": seeded.org_b})
            ids = c.execute(text(
                "SELECT public.email_message_search('Sanjeev Goel')")).scalars().all()
        with seeded.admin_engine.connect() as c:
            orgs = c.execute(text(
                "SELECT DISTINCT organization_id::text FROM email_messages "
                "WHERE id = ANY(CAST(:ids AS uuid[]))"),
                {"ids": [str(i) for i in ids]}).scalars().all()
        assert orgs == [seeded.org_b]

    def test_no_bound_organization_gives_nothing(self, seeded, app_engine):  # noqa: F811
        with app_engine.connect() as c:
            c.execute(text("SELECT set_config('app.tenant_id', '', false)"))
            assert c.execute(text(
                "SELECT count(*) FROM public.email_message_search('Sanjeev Goel')"
            )).scalar_one() == 0

    def test_public_holds_no_execute(self, seeded):
        with seeded.admin_engine.connect() as c:
            acl = c.execute(text(
                "SELECT coalesce(proacl::text[], '{}') FROM pg_proc "
                "WHERE oid = CAST(:f AS regprocedure)"), {"f": FN}).scalar_one()
            definer = c.execute(text(
                "SELECT prosecdef FROM pg_proc WHERE oid = CAST(:f AS regprocedure)"),
                {"f": FN}).scalar_one()
        assert acl, "a NULL acl means PUBLIC may EXECUTE"
        assert not any(a.startswith("=") for a in acl), acl
        assert definer is True
