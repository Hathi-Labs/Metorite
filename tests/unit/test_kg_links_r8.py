"""R8 — the structural edges of the graph on a real database (WS-KG KG-4).

Spec: ``project-docs/specs/knowledge_graph.md`` §3.2, §4.1, §4.4 and the KG-4
row of §9. Code: ``gateway/kb_entities/graph.py``. The checks with no
database are in ``tests/unit/test_kg_links_writer.py``.

Against a REAL Postgres with the migration of KG-4, as the NON-privileged
role of the phase-4 rehearsal (no superuser, no BYPASSRLS):

1. each KG-4 relation appears on a seeded org, as EXTRACTED with page_id
   NULL. An attendee id that is no row of ``people`` in the org gives none;
2. a second sweep writes 0 rows;
3. a removed edge stays removed after a sweep, and only a holder of
   ``knowledge:manage`` removes one;
4. org B reads, removes and writes no edge of org A;
5. the page-link reads (the backlinks and the links of a page) give the same
   answer after a sweep and beside a graph row that names the page;
6. the job writes nothing for an org off ``GRAPH_SWEEP_ORGS``.
"""

from __future__ import annotations

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
    _ok,
    _principal,
    _space,
    ns,
)

# ⚠️ Every test here needs the real database. Without this gate the suite
# ERRORS where no ladder URL is set (the deploy run), and the deploy stops.
pytestmark = _DB_GATE

TAG = uuid.uuid4().hex[:8]


def _id() -> str:
    return str(uuid.uuid4())


def _seed_org(c: Any, org: str, who: str) -> dict[str, str]:
    """One record of each source table of §4.1, in *org*, as the owner role."""
    ids = {k: _id() for k in (
        "company", "contact", "status", "deal", "activity", "project", "lane",
        "task1", "task2", "person", "meeting", "account", "chat", "message",
        "email")}
    p = {**ids, "o": org, "me": who, "tag": TAG}
    stmts = (
        "INSERT INTO crm_companies (id, name, organization_id) "
        "VALUES (CAST(:company AS uuid), 'Acme ' || :tag, CAST(:o AS uuid))",
        "INSERT INTO crm_contacts (id, first_name, company_id, organization_id) "
        "VALUES (CAST(:contact AS uuid), 'Meera', CAST(:company AS uuid), CAST(:o AS uuid))",
        "INSERT INTO crm_deal_statuses (id, name, position, type, organization_id) "
        "VALUES (CAST(:status AS uuid), 'Open ' || :tag, 1, 'open', CAST(:o AS uuid))",
        "INSERT INTO crm_deals (id, name, status_id, company_id, organization_id) "
        "VALUES (CAST(:deal AS uuid), 'Deal ' || :tag, CAST(:status AS uuid), "
        "CAST(:company AS uuid), CAST(:o AS uuid))",
        "INSERT INTO crm_deal_contacts (deal_id, contact_id, organization_id) "
        "VALUES (CAST(:deal AS uuid), CAST(:contact AS uuid), CAST(:o AS uuid))",
        "INSERT INTO crm_activities (id, type, created_by, deal_id, contact_id, "
        "company_id, organization_id) VALUES (CAST(:activity AS uuid), 'call', :me, "
        "CAST(:deal AS uuid), CAST(:contact AS uuid), CAST(:company AS uuid), "
        "CAST(:o AS uuid))",
        "INSERT INTO pm_projects (id, organization_id, name, source, created_by, "
        "owns_statuses) VALUES (CAST(:project AS uuid), CAST(:o AS uuid), "
        "'kg4-' || :tag, 'manual', :me, true)",
        "INSERT INTO pm_task_statuses (id, project_id, name, position, category, "
        "is_default) VALUES (CAST(:lane AS uuid), CAST(:project AS uuid), 'To do', 1, "
        "'todo', true)",
        "INSERT INTO wa_accounts (id, user_id, phone_number, phone_number_id, "
        "credentials_encrypted, organization_id) VALUES (CAST(:account AS uuid), :me, "
        "'+91' || :tag, 'pn-' || :account, 'x', CAST(:o AS uuid))",
        "INSERT INTO wa_chats (id, account_id, wa_chat_id, organization_id) VALUES "
        "(CAST(:chat AS uuid), CAST(:account AS uuid), 'chat-' || :chat, CAST(:o AS uuid))",
        "INSERT INTO wa_messages (id, account_id, chat_id, wa_message_id, "
        "organization_id) VALUES (CAST(:message AS uuid), CAST(:account AS uuid), "
        "CAST(:chat AS uuid), 'wamid-' || :message, CAST(:o AS uuid))",
        "INSERT INTO pm_tasks (id, organization_id, project_id, root_project_id, "
        "status_id, title, source, created_by, task_number, origin) VALUES "
        "(CAST(:task1 AS uuid), CAST(:o AS uuid), CAST(:project AS uuid), "
        "CAST(:project AS uuid), CAST(:lane AS uuid), 'From mail', 'email', :me, 1, "
        "jsonb_build_object('kind', 'email', 'email_id', CAST(:email AS text), 'subject', 'Hi'))",
        "INSERT INTO pm_tasks (id, organization_id, project_id, root_project_id, "
        "status_id, title, source, created_by, task_number, origin) VALUES "
        "(CAST(:task2 AS uuid), CAST(:o AS uuid), CAST(:project AS uuid), "
        "CAST(:project AS uuid), CAST(:lane AS uuid), 'From chat', 'manual', :me, 2, "
        "jsonb_build_object('kind', 'whatsapp', 'chat_id', CAST(:chat AS text), "
        "'wa_message_id', 'wamid-' || CAST(:message AS text)))",
        "INSERT INTO pm_task_links (source_task_id, target_task_id, link_type, "
        "created_by, organization_id) VALUES (CAST(:task1 AS uuid), "
        "CAST(:task2 AS uuid), 'blocks', :me, CAST(:o AS uuid))",
        "INSERT INTO people (id, name, source, organization_id) VALUES "
        "(CAST(:person AS uuid), 'Ravi', 'kg4-' || :tag, CAST(:o AS uuid))",
    )
    for sql in stmts:
        c.execute(text(sql), p)
    return ids


def _add_meeting(c: Any, org: str, meeting: str, attendees: list[str]) -> None:
    c.execute(text(
        "INSERT INTO meeting (id, platform, start_at, attendee_ids, organization_id) "
        "VALUES (CAST(:m AS uuid), 'meet', now(), CAST(:a AS uuid[]), CAST(:o AS uuid))"),
        {"m": meeting, "a": attendees, "o": org})


@pytest.fixture(scope="module")
def seeded(promoted):  # noqa: F811 — a fixture
    """Two orgs with one record of each source. The meeting of A names its
    own person, a person of B and an id that is no person at all."""
    with promoted.admin_engine.begin() as c:
        a = _seed_org(c, promoted.org_a, f"kg4-a-{TAG}@kg4-r8.test")
        b = _seed_org(c, promoted.org_b, f"kg4-b-{TAG}@kg4-r8.test")
        _add_meeting(c, promoted.org_a, a["meeting"], [a["person"], b["person"], _id()])
        _add_meeting(c, promoted.org_b, b["meeting"], [b["person"]])
    return {"a": a, "b": b}


@pytest.fixture
def kg(ns, seeded, monkeypatch):  # noqa: F811 — a fixture
    """The switch on for every org. Each test starts with no graph row."""
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "graph_sweep_orgs", "*", raising=False)
    ns.seeded = seeded
    yield ns
    with ns.admin_engine.begin() as c:
        c.execute(text("DELETE FROM kb_links WHERE page_id IS NULL"))


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


def _graph_rows(p: Any, org: str) -> set[tuple[str, ...]]:
    with p.admin_engine.connect() as c:
        rows = c.execute(text(
            "SELECT source_kind, source_id, relation, target_kind, target_id, "
            "provenance, confidence, page_id IS NULL AS no_page "
            "FROM kb_links WHERE organization_id = CAST(:o AS uuid) "
            "AND source_kind IS NOT NULL"), {"o": org}).fetchall()
    for r in rows:
        assert r.no_page and r.provenance == "EXTRACTED" and r.confidence == 1.0, r
    return {(r.source_kind, r.source_id, r.relation, r.target_kind, r.target_id)
            for r in rows}


def _expected(s: dict[str, str]) -> set[tuple[str, ...]]:
    return {
        ("deal", s["deal"], "deal_contact", "contact", s["contact"]),
        ("deal", s["deal"], "deal_company", "company", s["company"]),
        ("contact", s["contact"], "contact_company", "company", s["company"]),
        ("deal", s["deal"], "activity_on", "contact", s["contact"]),
        ("deal", s["deal"], "activity_on", "company", s["company"]),
        ("contact", s["contact"], "activity_on", "company", s["company"]),
        ("task", s["task1"], "origin", "email", s["email"]),
        ("task", s["task2"], "origin", "whatsapp", s["message"]),
        ("task", s["task1"], "task_link", "task", s["task2"]),
        ("meeting", s["meeting"], "attendee", "person", s["person"]),
    }


def _manager(org: str) -> Any:
    return _principal(_address("admin"), org, CAPABILITY)


@_DB_GATE
async def test_each_kg4_relation_appears_on_a_seeded_org(kg, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    a, b = kg.seeded["a"], kg.seeded["b"]
    result = await _sweep(kg, kg.org_a)
    assert result.total == 10
    assert set(result.written) == {"deal_contact", "deal_company", "contact_company",
                                   "activity_on", "origin", "task_link", "attendee"}
    assert _graph_rows(kg, kg.org_a) == _expected(a)
    # The person of org B and the id that is no person give no edge.
    assert _graph_rows(kg, kg.org_b) == set()
    assert await _sweep(kg, kg.org_b) is not None
    assert _graph_rows(kg, kg.org_b) == _expected(b)


@_DB_GATE
async def test_a_second_sweep_writes_nothing(kg) -> None:
    first = await _sweep(kg, kg.org_a)
    second = await _sweep(kg, kg.org_a)
    assert first.total == 10
    assert second.total == 0
    assert len(_graph_rows(kg, kg.org_a)) == 10


@_DB_GATE
async def test_a_removed_edge_stays_removed_after_a_sweep(kg) -> None:
    from fastapi import HTTPException
    from gateway.kb_entities import graph

    a = kg.seeded["a"]
    await _sweep(kg, kg.org_a)
    admin = _manager(kg.org_a)
    edges = await _in_org(kg, kg.org_a,
                          lambda db: graph.read_edges(db, admin, "deal", a["deal"]))
    assert {e.relation for e in edges} == {"deal_contact", "deal_company", "activity_on"}
    (target,) = [e for e in edges if e.relation == "deal_company"]

    member = _principal(_address("member"), kg.org_a)
    with pytest.raises(HTTPException) as refused:
        await _in_org(kg, kg.org_a, lambda db: graph.remove_edge(db, member, target.id))
    assert refused.value.status_code == 403

    assert await _in_org(kg, kg.org_a, lambda db: graph.remove_edge(db, admin, target.id))
    assert not await _in_org(kg, kg.org_a, lambda db: graph.remove_edge(db, admin, target.id))

    again = await _sweep(kg, kg.org_a)
    assert again.total == 0
    with kg.admin_engine.connect() as c:
        row = c.execute(text(
            "SELECT removed_at IS NOT NULL AS gone, removed_by, "
            "(SELECT count(*) FROM kb_links k WHERE k.organization_id = l.organization_id "
            " AND k.source_id = l.source_id AND k.relation = l.relation "
            " AND k.target_id = l.target_id) AS n "
            "FROM kb_links l WHERE l.id = CAST(:i AS uuid)"), {"i": target.id}).one()
    assert row.gone and row.removed_by == admin.email.lower() and row.n == 1

    shown = await _in_org(kg, kg.org_a,
                          lambda db: graph.read_edges(db, admin, "company", a["company"]))
    assert target.id not in {e.id for e in shown}
    every = await _in_org(kg, kg.org_a, lambda db: graph.read_edges(
        db, admin, "company", a["company"], with_removed=True))
    assert [e.removed for e in every if e.id == target.id] == [True]


@_DB_GATE
async def test_org_b_reads_removes_and_writes_no_edge_of_org_a(kg) -> None:
    from gateway.kb_entities import graph

    a = kg.seeded["a"]
    await _sweep(kg, kg.org_a)
    before = _graph_rows(kg, kg.org_a)
    admin_a, admin_b = _manager(kg.org_a), _manager(kg.org_b)
    edge_a = (await _in_org(kg, kg.org_a,
                            lambda db: graph.read_edges(db, admin_a, "deal", a["deal"])))[0]

    # A member of B names a record and an edge of A: nothing comes back.
    assert await _in_org(kg, kg.org_b,
                         lambda db: graph.read_edges(db, admin_b, "deal", a["deal"])) == []
    assert not await _in_org(kg, kg.org_b,
                             lambda db: graph.remove_edge(db, admin_b, edge_a.id))

    # A sweep in the session of B that names org A reads and writes nothing.
    async def cross(db: Any) -> Any:
        seen = (await db.execute(text(
            "SELECT count(*) FROM kb_links WHERE organization_id = CAST(:o AS uuid)"),
            {"o": kg.org_a})).scalar_one()
        return seen, await graph.sweep_org(db, kg.org_a)

    seen, wrote = await _in_org(kg, kg.org_b, cross)
    assert seen == 0 and wrote.total == 0
    assert _graph_rows(kg, kg.org_a) == before
    assert _graph_rows(kg, kg.org_b) == set()
    with kg.admin_engine.connect() as c:
        assert c.execute(text(
            "SELECT removed_at FROM kb_links WHERE id = CAST(:i AS uuid)"),
            {"i": edge_a.id}).scalar_one() is None


@_DB_GATE
async def test_the_page_link_reads_give_the_same_answer(kg) -> None:
    from gateway.kb_entities import graph

    admin = _manager(kg.org_a)
    space = await _space(kg, admin, f"Graph {TAG}", [{"subject": "org", "level": "read"}])
    target = await _ok(kg, admin, "POST", "/knowledge/pages",
                       {"space_id": space, "title": "Target", "body_md": "# Target"},
                       status=201)
    await _ok(kg, admin, "POST", f"/knowledge/pages/{target['id']}/publish")
    source = await _ok(kg, admin, "POST", "/knowledge/pages",
                       {"space_id": space, "title": "Source",
                        "body_md": f"[t](/knowledge/pages/{target['id']})"}, status=201)
    await _ok(kg, admin, "POST", f"/knowledge/pages/{source['id']}/publish")

    async def answers() -> tuple[Any, Any]:
        back = await _ok(kg, admin, "GET", f"/knowledge/pages/{target['id']}/backlinks")
        page = await _ok(kg, admin, "GET", f"/knowledge/pages/{source['id']}")
        return back, page["links"]

    before = await answers()
    assert [r["id"] for r in before[0]["backlinks"]] == [source["id"]]
    assert [r["target_id"] for r in before[1]] == [target["id"]]

    await _sweep(kg, kg.org_a)
    # A graph row that names the target page, as KG-10 will write one.
    with kg.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO kb_links (organization_id, page_id, source_kind, source_id, "
            "relation, target_kind, target_id, provenance, confidence, created_by) "
            "VALUES (CAST(:o AS uuid), NULL, 'task', :t, 'origin', 'page', :p, "
            "'EXTRACTED', 1.0, 'system')"),
            {"o": kg.org_a, "t": kg.seeded["a"]["task1"], "p": target["id"]})
    assert await answers() == before

    # The graph read takes the page row as `links_to`, from its page.
    edges = await _in_org(kg, kg.org_a,
                          lambda db: graph.read_edges(db, admin, "page", target["id"]))
    links = {(e.source_kind, e.source_id, e.relation) for e in edges}
    assert ("page", source["id"], "links_to") in links
    assert ("task", kg.seeded["a"]["task1"], "origin") in links
    # A page row is never removed here: the page writes it again on save.
    (page_row,) = [e for e in edges if e.relation == "links_to"]
    assert not await _in_org(kg, kg.org_a,
                             lambda db: graph.remove_edge(db, admin, page_row.id))


@_DB_GATE
async def test_the_job_writes_only_for_an_org_on_the_switch(kg, monkeypatch) -> None:
    from acb_common import get_settings
    from acb_common.db import bind_tenant, release_tenant
    from gateway.kb_entities import graph

    async def job(db: Any) -> None:
        token = bind_tenant(kg.org_a)
        try:
            await graph.sweep_job(db, {})
        finally:
            release_tenant(token)

    monkeypatch.setattr(get_settings(), "graph_sweep_orgs", kg.org_b, raising=False)
    await _in_org(kg, kg.org_a, job)
    assert _graph_rows(kg, kg.org_a) == set()
    monkeypatch.setattr(get_settings(), "graph_sweep_orgs", kg.org_a, raising=False)
    await _in_org(kg, kg.org_a, job)
    assert _graph_rows(kg, kg.org_a) == _expected(kg.seeded["a"])
