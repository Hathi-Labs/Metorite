"""R8 — exact identifiers to records on a real database (WS-KG KG-5).

Spec: ``project-docs/specs/knowledge_graph.md`` §3.3, §4.1 (``participant``,
``domain_company`` and the address half of ``attendee``), §4.2, §4.3, §4.4
and the KG-5 row of §9. Code: ``gateway/kb_entities/graph.py`` and
``registry.add_aliases`` / ``registry.remove_aliases``. The checks with no
database are in ``tests/unit/test_kg_links_writer.py``.

Against a REAL Postgres with the phase-4 promotion, as the NON-privileged
role (no superuser, no BYPASSRLS):

1. the exact case: one address, one contact phone or one website host with
   one candidate gives an EXTRACTED row at 1.0, and the host becomes an
   alias of the customer entity. ``WWW.Client`` matches ``client``;
2. the ambiguous case: two candidates give one AMBIGUOUS row for each, at
   0.5, and never a guess. A host that two companies share gives no row;
3. the free-mail and site cases: a free-mail sender, a linkedin.com or a
   github.com website and a company address (not a website) map to no
   company;
4. privacy: a phone on ``people`` (a private field) gives no edge, and it
   does not make a contact match AMBIGUOUS;
5. the rows follow the data: EXTRACTED turns AMBIGUOUS and back, a stale row
   goes, a removed row stays removed, and a sweep with no change changes 0
   rows. A host alias goes when two companies share the host;
6. the alias batch moves on each night, and a host that can never be an
   alias takes no place in it. A company that moves its website loses the
   old host alias, and the company that takes the host gets it;
7. tenant isolation: org B holds the same addresses, and no sweep reads or
   writes across the orgs.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import text

from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)
from tests.unit.test_kb_pages_r8 import (  # noqa: F401
    CAPABILITY,
    _address,
    _app_dsn,
    _assert_non_priv,
    _principal,
    ns,
)

# ⚠️ Every test here needs the real database. Without this gate the suite
# ERRORS where no ladder URL is set (the deploy run), and the deploy stops.
pytestmark = _DB_GATE

TAG = uuid.uuid4().hex[:8]
ACME = f"acme-{TAG}.test"          # the domain of the people of the org
CLIENT = f"client-{TAG}.test"      # one CRM company
SHARED = f"shared-{TAG}.test"      # two CRM companies
MAILONLY = f"mailonly-{TAG}.test"  # a company address, and no website
PHONE = "+91 98765 " + str(10000 + int(TAG[:4], 16) % 89999)
WA_ID = "".join(ch for ch in PHONE if ch.isdigit())


def _id() -> str:
    return str(uuid.uuid4())


def _mail(c: Any, org: str, account: str, mid: str, sender: str, to: list[str]) -> None:
    c.execute(text(
        "INSERT INTO email_messages (id, account_id, provider_message_id, "
        "from_address, to_addresses, organization_id) VALUES (CAST(:m AS uuid), "
        "CAST(:a AS uuid), :pm, CAST(:f AS jsonb), CAST(:t AS jsonb), CAST(:o AS uuid))"),
        {"m": mid, "a": account, "pm": f"pm-{mid}", "o": org,
         "f": json.dumps({"email": sender, "name": "x"}),
         "t": json.dumps([{"email": a, "name": "y"} for a in to])})


def _seed_org(c: Any, org: str, who: str) -> dict[str, str]:
    """The same addresses, phone and hosts in each org, as the owner role."""
    ids = {k: _id() for k in (
        "ravi", "dup_person", "free_person", "meera", "dup_contact",
        "client", "shared1", "shared2", "freemail_co", "linked_co", "mailonly_co",
        "github_co", "account", "e1", "e2", "e3", "e4", "e5", "e6", "wa_account", "chat", "wa1",
        "meeting")}
    p = {**ids, "o": org, "me": who, "tag": TAG, "acme": ACME, "client_d": CLIENT,
         "shared": SHARED, "shared2_site": f"http://www.{SHARED}:8080/", "mailonly": MAILONLY, "phone": PHONE, "wa_id": WA_ID}
    stmts = (
        # Ravi's phone is a private field. It equals Meera's mobile.
        "INSERT INTO people (id, name, email, phone, source, organization_id) VALUES "
        "(CAST(:ravi AS uuid), 'Ravi', 'ravi@' || :acme, :phone, 'kg5', CAST(:o AS uuid))",
        "INSERT INTO people (id, name, email, source, organization_id) VALUES "
        "(CAST(:dup_person AS uuid), 'Dup', 'dup@' || :shared, 'kg5', CAST(:o AS uuid))",
        "INSERT INTO people (id, name, email, source, organization_id) VALUES "
        "(CAST(:free_person AS uuid), 'Free', 'free-' || :tag || '@gmail.com', 'kg5', "
        "CAST(:o AS uuid))",
        "INSERT INTO crm_companies (id, name, website, organization_id) VALUES "
        "(CAST(:client AS uuid), 'Client', 'HTTPS://WWW.' || upper(:client_d) || '/about', "
        "CAST(:o AS uuid))",
        "INSERT INTO crm_companies (id, name, website, organization_id) VALUES "
        "(CAST(:shared1 AS uuid), 'Shared one', :shared, CAST(:o AS uuid))",
        "INSERT INTO crm_companies (id, name, website, organization_id) VALUES "
        "(CAST(:shared2 AS uuid), 'Shared two', :shared2_site, CAST(:o AS uuid))",
        "INSERT INTO crm_companies (id, name, website, organization_id) VALUES "
        "(CAST(:freemail_co AS uuid), 'Free mail Co', 'http://gmail.com', CAST(:o AS uuid))",
        "INSERT INTO crm_companies (id, name, website, organization_id) VALUES "
        "(CAST(:linked_co AS uuid), 'Linked Co', 'https://www.linkedin.com/company/x', "
        "CAST(:o AS uuid))",
        "INSERT INTO crm_companies (id, name, website, organization_id) VALUES "
        "(CAST(:github_co AS uuid), 'Git Co', 'https://github.com/acme', CAST(:o AS uuid))",
        "INSERT INTO crm_companies (id, name, email, organization_id) VALUES "
        "(CAST(:mailonly_co AS uuid), 'Mail only', 'info@' || :mailonly, CAST(:o AS uuid))",
        "INSERT INTO crm_contacts (id, first_name, email, mobile, company_id, "
        "organization_id) VALUES (CAST(:meera AS uuid), 'Meera', 'Meera@' || :client_d, "
        ":phone, CAST(:client AS uuid), CAST(:o AS uuid))",
        "INSERT INTO crm_contacts (id, first_name, email, organization_id) VALUES "
        "(CAST(:dup_contact AS uuid), 'Dup', 'DUP@' || :shared, CAST(:o AS uuid))",
        "INSERT INTO email_accounts (id, user_id, provider, email_address, "
        "credentials_encrypted, organization_id) VALUES (CAST(:account AS uuid), :me, "
        "'imap', :me, 'x', CAST(:o AS uuid))",
        "INSERT INTO wa_accounts (id, user_id, phone_number, phone_number_id, "
        "credentials_encrypted, organization_id) VALUES (CAST(:wa_account AS uuid), :me, "
        "'+10000' || :tag, 'pn-' || :wa_account, 'x', CAST(:o AS uuid))",
        "INSERT INTO wa_chats (id, account_id, wa_chat_id, organization_id) VALUES "
        "(CAST(:chat AS uuid), CAST(:wa_account AS uuid), :wa_id, CAST(:o AS uuid))",
        "INSERT INTO wa_messages (id, account_id, chat_id, wa_message_id, sender, "
        "organization_id) VALUES (CAST(:wa1 AS uuid), CAST(:wa_account AS uuid), "
        "CAST(:chat AS uuid), 'wamid-' || :wa1, jsonb_build_object('wa_id', "
        "CAST(:wa_id AS text), 'name', 'Meera'), CAST(:o AS uuid))",
        "INSERT INTO meeting (id, platform, start_at, attendees, organization_id) VALUES "
        "(CAST(:meeting AS uuid), 'meet', now(), jsonb_build_array(jsonb_build_object("
        "'email', ' RAVI@' || :acme || ' ', 'name', 'Ravi')), CAST(:o AS uuid))",
    )
    for sql in stmts:
        c.execute(text(sql), p)
    acct = ids["account"]
    # E1: Meera of the client writes to Ravi. One candidate for each.
    _mail(c, org, acct, ids["e1"], f"meera@{CLIENT}", [f"Ravi@{ACME}"])
    # E2: an address that a person and a contact both hold, at a host that
    # two companies share.
    _mail(c, org, acct, ids["e2"], f"Dup@{SHARED}", [])
    # E3: a free-mail sender. A person holds the address, and a company names
    # the domain, which never counts.
    _mail(c, org, acct, ids["e3"], f"free-{TAG}@gmail.com", [])
    # E4: a sender at linkedin.com, which a company names as its website.
    _mail(c, org, acct, ids["e4"], f"li-{TAG}@linkedin.com", [])
    # E5: a sender at a domain that only a company address holds.
    _mail(c, org, acct, ids["e5"], f"x@{MAILONLY}", [])
    # E6: a sender at github.com, which a company names as its website.
    _mail(c, org, acct, ids["e6"], f"notify-{TAG}@github.com", [])
    return ids


@pytest.fixture(scope="module")
def seeded(promoted):  # noqa: F811 — a fixture
    with promoted.admin_engine.begin() as c:
        a = _seed_org(c, promoted.org_a, f"kg5-a-{TAG}@kg5-r8.test")
        b = _seed_org(c, promoted.org_b, f"kg5-b-{TAG}@kg5-r8.test")
    return {"a": a, "b": b}


@pytest.fixture
def kg(ns, seeded, monkeypatch):  # noqa: F811 — a fixture
    """The switch on for every org. Each test starts with no graph row and no
    entity."""
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "graph_sweep_orgs", "*", raising=False)
    ns.seeded = seeded
    yield ns
    with ns.admin_engine.begin() as c:
        c.execute(text("DELETE FROM kb_links WHERE page_id IS NULL"))
        c.execute(text("DELETE FROM kb_entities WHERE ref_table = 'crm_companies'"))


def _admin(p: Any, sql: str, params: dict[str, Any]) -> None:
    with p.admin_engine.begin() as c:
        c.execute(text(sql), params)


async def _in_org(p: Any, org: str, fn: Any) -> Any:
    """Run ``fn(db)`` in ``tenant_session(org)``, as the app role."""
    from acb_common.db import clear_tenant, release_tenant, tenant_session

    from tests.unit._tenant_ladder import tenant_engine_scope

    scope = clear_tenant()
    try:
        async with tenant_engine_scope(_app_dsn(p)), tenant_session(org) as db:
            return await fn(db)
    finally:
        release_tenant(scope)


async def _sweep(p: Any, org: str) -> Any:
    from gateway.kb_entities import graph

    return await _in_org(p, org, lambda db: graph.sweep_org(db, org))


_KG5 = ("participant", "domain_company", "attendee")


def _rows(p: Any, org: str, *, with_removed: bool = False
          ) -> dict[tuple[str, ...], tuple[str, float]]:
    """The KG-5 rows of *org*: the key to (provenance, confidence)."""
    with p.admin_engine.connect() as c:
        rows = c.execute(text(
            "SELECT source_kind, source_id, relation, target_kind, target_id, "
            "provenance, confidence, page_id IS NULL AS no_page, created_by "
            "FROM kb_links WHERE organization_id = CAST(:o AS uuid) "
            "AND relation = ANY(:r) AND (:w OR removed_at IS NULL)"),
            {"o": org, "r": list(_KG5), "w": with_removed}).fetchall()
    assert all(r.no_page and r.created_by == "system:identifier" for r in rows)
    return {(r.source_kind, r.source_id, r.relation, r.target_kind, r.target_id):
            (r.provenance, round(float(r.confidence), 4)) for r in rows}


ONE, HALF = ("EXTRACTED", 1.0), ("AMBIGUOUS", 0.5)


def _expected(s: dict[str, str]) -> dict[tuple[str, ...], tuple[str, float]]:
    return {
        # The exact case. `WWW.CLIENT-…/about` is the host `client-…`.
        ("email", s["e1"], "participant", "contact", s["meera"]): ONE,
        ("email", s["e1"], "participant", "person", s["ravi"]): ONE,
        ("email", s["e1"], "domain_company", "company", s["client"]): ONE,
        ("meeting", s["meeting"], "attendee", "person", s["ravi"]): ONE,
        # Meera's mobile, and never Ravi's private phone: one candidate.
        ("whatsapp", s["wa1"], "participant", "contact", s["meera"]): ONE,
        # The ambiguous case: a person and a contact. The shared host gives
        # no `domain_company` row at all.
        ("email", s["e2"], "participant", "person", s["dup_person"]): HALF,
        ("email", s["e2"], "participant", "contact", s["dup_contact"]): HALF,
        # The free-mail case: the address maps to its person, the domain to
        # no company. E4 (linkedin.com) and E5 (a company address) give none.
        ("email", s["e3"], "participant", "person", s["free_person"]): ONE,
    }


def _aliases(p: Any, org: str) -> dict[str, list[str]]:
    with p.admin_engine.connect() as c:
        rows = c.execute(text(
            "SELECT ref_id::text AS ref, aliases FROM kb_entities "
            "WHERE organization_id = CAST(:o AS uuid) AND ref_table = 'crm_companies'"),
            {"o": org}).fetchall()
    return {r.ref: sorted(r.aliases) for r in rows}


@_DB_GATE
async def test_the_exact_ambiguous_free_mail_and_site_cases(kg, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    a = kg.seeded["a"]
    result = await _sweep(kg, kg.org_a)
    got = _rows(kg, kg.org_a)
    assert got == _expected(a)
    assert result.written["participant"] == 6
    assert result.written["domain_company"] == 1
    assert not any(k[4] in {a["shared1"], a["shared2"], a["freemail_co"], a["linked_co"],
                            a["mailonly_co"], a["github_co"]} for k in got)
    assert not any(k[1] in {a["e4"], a["e5"], a["e6"]} for k in got)
    # The private phone of Ravi gives no WhatsApp edge to a person.
    assert not any(k[0] == "whatsapp" and k[3] == "person" for k in got)
    # Only the one-company host is an alias. The shared, free-mail and
    # linkedin.com and github.com hosts are aliases of nothing.
    assert result.aliases == 1
    assert _aliases(kg, kg.org_a) == {a["client"]: [CLIENT]}


@_DB_GATE
async def test_the_rows_follow_the_data_and_a_removed_row_stays(kg) -> None:
    from gateway.kb_entities import graph

    a, o = kg.seeded["a"], kg.org_a
    meera_key = ("email", a["e1"], "participant", "contact", a["meera"])
    first = await _sweep(kg, o)
    assert first.changes > 0
    quiet = await _sweep(kg, o)
    assert quiet.changes == 0, quiet  # no insert, no update, no delete, no alias

    twin, other = _id(), _id()
    try:
        # A second contact with Meera's address: EXTRACTED turns AMBIGUOUS.
        _admin(kg, "INSERT INTO crm_contacts (id, first_name, email, organization_id) "
               "VALUES (CAST(:i AS uuid), 'Twin', :e, CAST(:o AS uuid))",
               {"i": twin, "e": f"meera@{CLIENT}", "o": o})
        r = await _sweep(kg, o)
        rows = _rows(kg, o)
        assert rows[meera_key] == HALF
        assert rows[("email", a["e1"], "participant", "contact", twin)] == HALF
        assert (r.updated["participant"], r.written["participant"]) == (1, 1)

        # It goes away: AMBIGUOUS turns EXTRACTED, and its stale row goes.
        _admin(kg, "DELETE FROM crm_contacts WHERE id = CAST(:i AS uuid)", {"i": twin})
        r = await _sweep(kg, o)
        assert _rows(kg, o) == _expected(a)
        assert (r.updated["participant"], r.deleted["participant"]) == (1, 1)

        # Ravi's address changes: his two address rows are stale and go.
        _admin(kg, "UPDATE people SET email = :e WHERE id = CAST(:i AS uuid)",
               {"e": f"ravi-new@{ACME}", "i": a["ravi"]})
        r = await _sweep(kg, o)
        rows = _rows(kg, o)
        assert ("email", a["e1"], "participant", "person", a["ravi"]) not in rows
        assert ("meeting", a["meeting"], "attendee", "person", a["ravi"]) not in rows
        assert (r.deleted["participant"], r.deleted["attendee"]) == (1, 1)
        _admin(kg, "UPDATE people SET email = :e WHERE id = CAST(:i AS uuid)",
               {"e": f"ravi@{ACME}", "i": a["ravi"]})
        await _sweep(kg, o)

        # An admin removes the free-mail edge. A second candidate appears and
        # goes again: the removed row keeps its removal and its values.
        admin = _principal(_address("admin"), o, CAPABILITY)
        edges = await _in_org(kg, o, lambda db: graph.read_edges(db, admin, "email", a["e3"]))
        (target,) = [e for e in edges if e.target_id == a["free_person"]]
        assert await _in_org(kg, o, lambda db: graph.remove_edge(db, admin, target.id))
        free_key = ("email", a["e3"], "participant", "person", a["free_person"])
        _admin(kg, "INSERT INTO crm_contacts (id, first_name, email, organization_id) "
               "VALUES (CAST(:i AS uuid), 'Other', :e, CAST(:o AS uuid))",
               {"i": other, "e": f"free-{TAG}@gmail.com", "o": o})
        await _sweep(kg, o)
        assert _rows(kg, o, with_removed=True)[free_key] == ONE
        assert free_key not in _rows(kg, o)
        _admin(kg, "DELETE FROM crm_contacts WHERE id = CAST(:i AS uuid)", {"i": other})
        await _sweep(kg, o)
        with kg.admin_engine.connect() as c:
            row = c.execute(text(
                "SELECT removed_at IS NOT NULL AS gone, provenance, (SELECT count(*) "
                "FROM kb_links k WHERE k.organization_id = l.organization_id "
                "AND k.source_id = l.source_id AND k.relation = l.relation "
                "AND k.target_id = l.target_id) AS n "
                "FROM kb_links l WHERE l.id = CAST(:i AS uuid)"), {"i": target.id}).one()
        assert row.gone and row.provenance == "EXTRACTED" and row.n == 1
        assert (await _sweep(kg, o)).changes == 0
    finally:
        _admin(kg, "DELETE FROM crm_contacts WHERE id = ANY(CAST(:i AS uuid[]))",
               {"i": [twin, other]})
        _admin(kg, "UPDATE people SET email = :e WHERE id = CAST(:i AS uuid)",
               {"e": f"ravi@{ACME}", "i": a["ravi"]})


@_DB_GATE
async def test_a_host_that_turns_shared_loses_its_edge_and_its_alias(kg) -> None:
    a, o = kg.seeded["a"], kg.org_a
    domain_key = ("email", a["e1"], "domain_company", "company", a["client"])
    await _sweep(kg, o)
    assert _aliases(kg, o) == {a["client"]: [CLIENT]}
    try:
        _admin(kg, "UPDATE crm_companies SET website = :w WHERE id = CAST(:i AS uuid)",
               {"w": f"https://{CLIENT}", "i": a["shared1"]})
        # The client host is shared now. The shared host has one company
        # left, so E2 names it.
        r = await _sweep(kg, o)
        rows = _rows(kg, o)
        assert domain_key not in rows
        assert rows[("email", a["e2"], "domain_company", "company", a["shared2"])] == ONE
        assert r.deleted["domain_company"] == 1 and r.aliases_removed == 1
        assert _aliases(kg, o) == {a["client"]: [], a["shared2"]: [SHARED]}
    finally:
        _admin(kg, "UPDATE crm_companies SET website = :w WHERE id = CAST(:i AS uuid)",
               {"w": SHARED, "i": a["shared1"]})
    r = await _sweep(kg, o)
    assert _rows(kg, o) == _expected(a)
    assert (r.aliases, r.aliases_removed) == (1, 1)
    assert _aliases(kg, o) == {a["client"]: [CLIENT], a["shared2"]: []}


@_DB_GATE
async def test_the_alias_batch_moves_on_each_night(kg, monkeypatch) -> None:
    from gateway.kb_entities import graph

    a, o = kg.seeded["a"], kg.org_a
    extra = {f"b{i}-{TAG}.test": (_id(), _id()) for i in (1, 2)}
    monkeypatch.setattr(graph, "ALIAS_BATCH", 2)
    try:
        with kg.admin_engine.begin() as c:
            for host, (company, mail) in extra.items():
                c.execute(text(
                    "INSERT INTO crm_companies (id, name, website, organization_id) "
                    "VALUES (CAST(:i AS uuid), :h, :h, CAST(:o AS uuid))"),
                    {"i": company, "h": host, "o": o})
                _mail(c, o, a["account"], mail, f"x@{host}", [])
        # Three one-company hosts send mail, and the batch is 2.
        first = await _sweep(kg, o)
        second = await _sweep(kg, o)
        third = await _sweep(kg, o)
        assert (first.aliases, second.aliases, third.aliases) == (2, 1, 0)
        want = {a["client"]: [CLIENT]} | {c: [h] for h, (c, _m) in extra.items()}
        assert _aliases(kg, o) == want
    finally:
        with kg.admin_engine.begin() as c:
            for _host, (company, mail) in extra.items():
                c.execute(text("DELETE FROM email_messages WHERE id = CAST(:i AS uuid)"),
                          {"i": mail})
                c.execute(text("DELETE FROM crm_companies WHERE id = CAST(:i AS uuid)"),
                          {"i": company})


@_DB_GATE
async def test_org_b_reads_and_writes_nothing_of_org_a(kg) -> None:
    from gateway.kb_entities import graph

    a, b = kg.seeded["a"], kg.seeded["b"]
    # Both orgs hold the same addresses. Each sweep finds its own records,
    # with one candidate each, so no candidate of the other org counts.
    await _sweep(kg, kg.org_a)
    await _sweep(kg, kg.org_b)
    rows_a, rows_b = _rows(kg, kg.org_a), _rows(kg, kg.org_b)
    assert rows_a == _expected(a)
    assert rows_b == _expected(b)
    ids_a, ids_b = set(a.values()), set(b.values())
    assert all({k[1], k[4]} <= ids_a for k in rows_a)
    assert all({k[1], k[4]} <= ids_b for k in rows_b)
    assert _aliases(kg, kg.org_b) == {b["client"]: [CLIENT]}

    # A sweep in the session of B that names org A changes nothing of A.
    before_a = _rows(kg, kg.org_a)
    wrote = await _in_org(kg, kg.org_b, lambda db: graph.sweep_org(db, kg.org_a))
    assert wrote.changes == 0
    assert _rows(kg, kg.org_a) == before_a and _rows(kg, kg.org_b) == rows_b

    with kg.admin_engine.begin() as c:
        c.execute(text("DELETE FROM kb_links WHERE page_id IS NULL"))
        c.execute(text("DELETE FROM kb_entities WHERE ref_table = 'crm_companies'"))
    wrote = await _in_org(kg, kg.org_b, lambda db: graph.sweep_org(db, kg.org_a))
    assert wrote.changes == 0
    assert _rows(kg, kg.org_a) == {} and _rows(kg, kg.org_b) == {}
    assert _aliases(kg, kg.org_a) == {} and _aliases(kg, kg.org_b) == {}


@_DB_GATE
async def test_the_alias_writers_change_once_and_never_across_orgs(kg) -> None:
    from gateway.kb_entities import registry

    a = kg.seeded["a"]

    async def make(db: Any) -> Any:
        eid = await registry.ensure_entity(db, "crm_companies", a["client"])
        first = await registry.add_aliases(db, eid, ["Client Co", " client  co ", ""])
        again = await registry.add_aliases(db, eid, ["CLIENT CO"])
        return eid, first, again

    eid, first, again = await _in_org(kg, kg.org_a, make)
    assert (first, again) == (1, 0)
    assert _aliases(kg, kg.org_a) == {a["client"]: ["Client Co"]}
    # Org B names the entity of org A: nothing is read or written.
    assert await _in_org(kg, kg.org_b,
                         lambda db: registry.add_aliases(db, eid, ["Stolen"])) == 0
    assert await _in_org(kg, kg.org_b,
                         lambda db: registry.remove_aliases(db, eid, ["client co"])) == 0
    assert _aliases(kg, kg.org_a) == {a["client"]: ["Client Co"]}
    assert await _in_org(kg, kg.org_a,
                         lambda db: registry.remove_aliases(db, eid, ["CLIENT  co"])) == 1
    assert _aliases(kg, kg.org_a) == {a["client"]: []}


LONG = f"{'a' * 60}.{'b' * 55}-{TAG}.test"  # 130 characters: never an alias


@_DB_GATE
async def test_a_host_that_can_never_be_an_alias_takes_no_place_in_the_batch(
        kg, monkeypatch) -> None:
    from gateway.kb_entities import graph

    assert len(LONG) == 130
    a, o = kg.seeded["a"], kg.org_a
    # The long host sorts first in the batch, by its company id.
    long_co = "00000000-0000-4000-8000-" + uuid.uuid4().hex[:12]
    extra_host, extra_co = f"b9-{TAG}.test", _id()
    mails = [_id(), _id()]
    monkeypatch.setattr(graph, "ALIAS_BATCH", 2)
    try:
        with kg.admin_engine.begin() as c:
            for company, host, mail in ((long_co, LONG, mails[0]),
                                        (extra_co, extra_host, mails[1])):
                c.execute(text(
                    "INSERT INTO crm_companies (id, name, website, organization_id) "
                    "VALUES (CAST(:i AS uuid), 'Batch', :h, CAST(:o AS uuid))"),
                    {"i": company, "h": host, "o": o})
                _mail(c, o, a["account"], mail, f"x@{host}", [])
        first = await _sweep(kg, o)
        assert first.aliases == 2
        assert _aliases(kg, o) == {a["client"]: [CLIENT], extra_co: [extra_host]}
    finally:
        with kg.admin_engine.begin() as c:
            c.execute(text("DELETE FROM email_messages WHERE id = ANY(CAST(:i AS uuid[]))"),
                      {"i": mails})
            c.execute(text("DELETE FROM crm_companies WHERE id = ANY(CAST(:i AS uuid[]))"),
                      {"i": [long_co, extra_co]})


@_DB_GATE
async def test_a_company_that_moves_its_website_loses_the_old_alias(
        kg, monkeypatch) -> None:
    from acb_common import get_settings
    from gateway.kb_entities import resolver

    a, o = kg.seeded["a"], kg.org_a
    moved = f"client-{TAG}.io"
    taker, mail = _id(), _id()
    settings = get_settings()
    monkeypatch.setattr(settings, "crm_enabled", True, raising=False)
    monkeypatch.setattr(settings, "crm_orgs", "*", raising=False)
    await _sweep(kg, o)
    assert _aliases(kg, o) == {a["client"]: [CLIENT]}
    try:
        # The client moves to a new host, and another company takes the old.
        with kg.admin_engine.begin() as c:
            c.execute(text("UPDATE crm_companies SET website = :w "
                           "WHERE id = CAST(:i AS uuid)"),
                      {"w": f"https://{moved}", "i": a["client"]})
            c.execute(text(
                "INSERT INTO crm_companies (id, name, website, organization_id) "
                "VALUES (CAST(:i AS uuid), 'Taker', :w, CAST(:o AS uuid))"),
                {"i": taker, "w": CLIENT, "o": o})
            _mail(c, o, a["account"], mail, f"hello@{moved}", [])
        r = await _sweep(kg, o)
        assert (r.aliases, r.aliases_removed) == (2, 1)
        assert _aliases(kg, o) == {a["client"]: [moved], taker: [CLIENT]}
        member = _principal(_address("crm"), o, "feature:crm", "admin:access:manage")
        hits = await _in_org(kg, o, lambda db: resolver.resolve_alias(
            db, member, CLIENT, "customer"))
        assert [h["ref_id"] for h in hits] == [taker]
        assert (await _sweep(kg, o)).changes == 0
    finally:
        with kg.admin_engine.begin() as c:
            c.execute(text("DELETE FROM email_messages WHERE id = CAST(:i AS uuid)"),
                      {"i": mail})
            c.execute(text("DELETE FROM crm_companies WHERE id = CAST(:i AS uuid)"),
                      {"i": taker})
            c.execute(text("UPDATE crm_companies SET website = :w "
                           "WHERE id = CAST(:i AS uuid)"),
                      {"w": f"HTTPS://WWW.{CLIENT.upper()}/about", "i": a["client"]})
