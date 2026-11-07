"""R8 — the neighbours of company search on a real database (WS-KG KG-6).

Spec: ``project-docs/specs/knowledge_graph.md`` §5, §6 and the KG-6 row of
§9. Code: ``gateway/retrieval/company.py`` (the step),
``gateway/retrieval/sources.py`` (``Source.open``) and
``gateway/kb_entities/graph.py`` (``neighbour_edges``). The checks with no
database are in ``test_company_search_graph.py``.

Against a REAL Postgres with the whole ladder, as the NON-privileged role of
the phase-4 rehearsal (no superuser, no BYPASSRLS), through each app's own
read by id. A member of org A who holds every app, and whose hits have
edges to records that the member cannot open:

1. a private task of another member, a task in a project that only a
   ``group:<slug>`` grant opens, another member's email, another member's
   WhatsApp message, a CRM record without CRM access, a page in a closed
   space, a person who left, and a project node NEVER appear. The records
   the member can open DO appear, so an empty answer cannot pass;
2. an admin with CRM access sees the deal, so the CRM gate is the reason;
3. the read drops AMBIGUOUS, removed and INFERRED-below-0.5 edges, and keeps
   an INFERRED edge at 0.5;
4. org B reads no edge of org A, and opens no record of org A, with the ids
   of org A in hand;
5. the whole route, with ``GRAPH_SEARCH_ORGS`` on, answers ``neighbours``
   with the open records only, and ``apps=`` keeps the kinds of the named
   sources. Off, it has no ``neighbours`` key;
6. a mail in a separate mailbox (``in_all_inboxes`` false) and a trashed
   mail never appear, as Email search hides them, and an inbox mail does;
7. the artifact read and the WhatsApp read open the member's own records
   on the real database, and not another member's;
8. 40 edges to a kind that no app opens do not push out the one task link.

⚠️ It SKIPS without ``TENANT_LADDER_DATABASE_URL``, and a skip is not a pass.
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
    _add_group,
    _add_member,
    _address,
    _app_dsn,
    _assert_non_priv,
    _ok,
    _space,
    ns,
)

# Without the gate the suite ERRORS where no ladder URL is set (deploy.yml).
pytestmark = _DB_GATE

TAG = uuid.uuid4().hex[:8]
WORD = f"kingfisher{TAG}"
FEATURES = ("projects", "email", "people", "crm", "knowledge", "whatsapp", "artifacts")


def _id() -> str:
    return str(uuid.uuid4())


def _user(email: str, org: str, *perms: str) -> Any:
    from acb_auth import UserContext, UserRole, build_access

    return UserContext(
        email=email, role=UserRole.EMPLOYEE, organization_id=org,
        access=build_access([*(f"feature:{f}" for f in FEATURES), *perms]),
    )


def _project(c: Any, org: str, who: str, subject: str) -> tuple[str, str]:
    """One project with one grant and one lane."""
    project, lane = _id(), _id()
    c.execute(text(
        "INSERT INTO pm_projects (id, organization_id, name, source, created_by, "
        "owns_statuses) VALUES (CAST(:p AS uuid), CAST(:o AS uuid), :n, 'manual', :me, "
        "true)"), {"p": project, "o": org, "n": f"kg6-{TAG}-{subject[:20]}", "me": who})
    c.execute(text(
        "INSERT INTO pm_project_grants (project_id, subject, created_by, organization_id) "
        "VALUES (CAST(:p AS uuid), :s, :me, CAST(:o AS uuid))"),
        {"p": project, "s": subject, "me": who, "o": org})
    c.execute(text(
        "INSERT INTO pm_task_statuses (id, project_id, name, position, category, "
        "is_default) VALUES (CAST(:s AS uuid), CAST(:p AS uuid), 'To do', 1, 'todo', "
        "true)"), {"s": lane, "p": project})
    return project, lane


_NUMBER = iter(range(1, 10_000))


def _task(c: Any, org: str, who: str, project: tuple[str, str], title: str) -> str:
    task = _id()
    c.execute(text(
        "INSERT INTO pm_tasks (id, organization_id, project_id, root_project_id, "
        "status_id, title, source, created_by, task_number) VALUES (CAST(:t AS uuid), "
        "CAST(:o AS uuid), CAST(:p AS uuid), CAST(:p AS uuid), CAST(:s AS uuid), :ti, "
        "'manual', :me, :n)"),
        {"t": task, "o": org, "p": project[0], "s": project[1], "ti": title, "me": who,
         "n": next(_NUMBER)})
    return task


def _mail(c: Any, org: str, owner: str, subject: str, *, folder: str = "inbox",
          separate: bool = False) -> str:
    """One mail in a new mailbox of *owner*. *separate* is a mailbox that the
    member keeps out of All inboxes (``in_all_inboxes`` false, D-EM-28)."""
    account = str(c.execute(text(
        "INSERT INTO email_accounts (user_id, provider, email_address, "
        "credentials_encrypted, in_all_inboxes, organization_id) VALUES (:u, "
        "'microsoft', :m, 'x', :pooled, CAST(:o AS uuid)) RETURNING id"),
        {"u": owner, "m": f"box-{uuid.uuid4().hex[:8]}@kg6.test", "o": org,
         "pooled": not separate}).scalar_one())
    return str(c.execute(text(
        "INSERT INTO email_messages (account_id, provider_message_id, folder, "
        "from_address, to_addresses, subject, body_text, received_at, organization_id) "
        "VALUES (CAST(:a AS uuid), :p, :f, "
        "'{\"name\": \"S\", \"email\": \"s@x.test\"}'::jsonb, '[]'::jsonb, :s, 'body', now(), "
        "CAST(:o AS uuid)) RETURNING id"),
        {"a": account, "p": uuid.uuid4().hex, "s": subject, "o": org,
         "f": folder}).scalar_one())


def _whatsapp(c: Any, org: str, owner: str) -> str:
    account, chat, message = _id(), _id(), _id()
    p = {"a": account, "c": chat, "m": message, "o": org, "me": owner,
         "ph": f"+91{uuid.uuid4().int % 10**10}"}
    c.execute(text(
        "INSERT INTO wa_accounts (id, user_id, phone_number, phone_number_id, "
        "credentials_encrypted, organization_id) VALUES (CAST(:a AS uuid), :me, :ph, "
        "'pn-' || :a, 'x', CAST(:o AS uuid))"), p)
    c.execute(text(
        "INSERT INTO wa_chats (id, account_id, wa_chat_id, name, organization_id) VALUES "
        "(CAST(:c AS uuid), CAST(:a AS uuid), 'chat-' || :c, 'Buyers', CAST(:o AS uuid))"), p)
    c.execute(text(
        "INSERT INTO wa_messages (id, account_id, chat_id, wa_message_id, body_text, "
        "sent_at, organization_id) VALUES (CAST(:m AS uuid), CAST(:a AS uuid), "
        "CAST(:c AS uuid), 'wamid-' || :m, 'hello', now(), CAST(:o AS uuid))"), p)
    return message


def _artifact(c: Any, org: str, owner: str, title: str) -> str:
    """One active artifact of *owner*, with the personal scope."""
    return str(c.execute(text(
        "INSERT INTO artifacts (organization_id, owner_email, agent_name, path, sha256, "
        "kind, title, made_by, scope) VALUES (CAST(:o AS uuid), :me, 'mira', :p, 'x', "
        "'document', :t, 'mira', 'personal') RETURNING id"),
        {"o": org, "me": owner, "p": f"outputs/{uuid.uuid4().hex}.md", "t": title},
    ).scalar_one())


def _person(c: Any, org: str, status: str) -> str:
    return str(c.execute(text(
        "INSERT INTO people (name, department, source, status, organization_id) VALUES "
        "(:n, 'Ops', :s, :st, CAST(:o AS uuid)) RETURNING id"),
        {"n": f"Person {status}", "s": f"kg6-{TAG}", "st": status, "o": org}).scalar_one())


def _deal(c: Any, org: str) -> str:
    status, deal = _id(), _id()
    c.execute(text(
        "INSERT INTO crm_deal_statuses (id, name, position, type, organization_id) "
        "VALUES (CAST(:s AS uuid), 'Open ' || :tag, 1, 'open', CAST(:o AS uuid))"),
        {"s": status, "tag": TAG, "o": org})
    c.execute(text(
        "INSERT INTO crm_deals (id, name, status_id, organization_id) VALUES "
        "(CAST(:d AS uuid), 'Deal ' || :tag, CAST(:s AS uuid), CAST(:o AS uuid))"),
        {"d": deal, "tag": TAG, "s": status, "o": org})
    return deal


def _edge(c: Any, org: str, sk: str, sid: str, relation: str, tk: str, tid: str,
          provenance: str = "EXTRACTED", confidence: float = 1.0,
          removed: bool = False) -> None:
    c.execute(text(
        "INSERT INTO kb_links (organization_id, page_id, source_kind, source_id, "
        "relation, target_kind, target_id, provenance, confidence, created_by, "
        "removed_at, removed_by) VALUES (CAST(:o AS uuid), NULL, :sk, :sid, :rel, :tk, "
        ":tid, :prov, :conf, 'system', CASE WHEN :gone THEN now() END, "
        "CASE WHEN :gone THEN 'admin@kg6.test' END)"),
        {"o": org, "sk": sk, "sid": sid, "rel": relation, "tk": tk, "tid": tid,
         "prov": provenance, "conf": confidence, "gone": removed})


@pytest.fixture
async def world(ns, monkeypatch):  # noqa: F811 — a fixture
    """Org A: a member, another member, the records of each app, and the
    edges of §5. Org B: one member and one edge of its own."""
    from acb_common import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "crm_enabled", True, raising=False)
    monkeypatch.setattr(settings, "crm_orgs", "*", raising=False)
    monkeypatch.setattr(settings, "graph_search_orgs", "*", raising=False)
    monkeypatch.setattr(settings, "context_recency", False, raising=False)
    monkeypatch.setattr(settings, "artifact_registry_enabled", True, raising=False)

    org, org_b = ns.org_a, ns.org_b
    me, other = _address("me"), _address("other")
    admin, member_b = _address("admin"), _address("member-b")
    _add_member(ns, org, me)
    other_id = _add_member(ns, org, other)
    _add_member(ns, org, admin)
    _add_member(ns, org_b, member_b)
    slug = f"kg6-{TAG}"
    _add_group(ns, org, slug, [other_id])

    w: dict[str, Any] = {"org": org, "org_b": org_b, "me": me, "other": other,
                         "admin": admin, "member_b": member_b}
    with ns.admin_engine.begin() as c:
        # Each test seeds its own rows, so no row of an earlier test meets it.
        c.execute(text("DELETE FROM crm_settings"))
        open_p = _project(c, org, me, "org")
        group_p = _project(c, org, other, f"group:{slug}")
        private_p = _project(c, org, other, other)
        w["projects"] = [open_p[0], group_p[0], private_p[0]]
        w["hit"] = _task(c, org, me, open_p, f"Plan {WORD}")
        w["seen"] = _task(c, org, me, open_p, "Seen task")
        w["group_task"] = _task(c, org, other, group_p, "Group task")
        w["private_task"] = _task(c, org, other, private_p, "Private task")
        w["filter_hit"] = _task(c, org, me, open_p, "Filter hit")
        for name in ("ambiguous", "removed", "low", "floor"):
            w[name] = _task(c, org, me, open_p, f"Filter {name}")
        w["my_mail"] = _mail(c, org, me, "My mail")
        w["their_mail"] = _mail(c, org, other, "Their mail")
        w["their_wa"] = _whatsapp(c, org, other)
        # KG-6 fix round: the scope of Email search, and the other openers.
        w["hit2"] = _task(c, org, me, open_p, "Second hit")
        w["separate_mail"] = _mail(c, org, me, "NDA mail", separate=True)
        w["trashed_mail"] = _mail(c, org, me, "Thrown away", folder="trash")
        w["my_wa"] = _whatsapp(c, org, me)
        w["my_artifact"] = _artifact(c, org, me, "My deck")
        w["their_artifact"] = _artifact(c, org, other, "Their deck")
        w["crowded"] = _task(c, org, me, open_p, "Crowded hit")
        w["active"] = _person(c, org, "active")
        w["alumni"] = _person(c, org, "alumni")
        w["deal"] = _deal(c, org)
        b_open = _project(c, org_b, member_b, "org")
        w["projects_b"] = [b_open[0]]
        w["b_task"] = _task(c, org_b, member_b, b_open, f"Beta {WORD}")
        w["b_other"] = _task(c, org_b, member_b, b_open, "Beta other")

    boss = _user(admin, org, CAPABILITY)
    open_s = await _space(ns, boss, f"Open {TAG}", [{"subject": "org", "level": "read"}])
    closed_s = await _space(ns, boss, f"Closed {TAG}", [{"subject": other, "level": "read"}])
    closed = await _ok(ns, boss, "POST", "/knowledge/pages",
                       {"space_id": closed_s, "title": "Closed page", "body_md": "# Closed"},
                       status=201)
    await _ok(ns, boss, "POST", f"/knowledge/pages/{closed['id']}/publish")
    # The open page links to the closed page: a page row, read as `links_to`.
    page = await _ok(ns, boss, "POST", "/knowledge/pages",
                     {"space_id": open_s, "title": "Open page",
                      "body_md": f"[c](/knowledge/pages/{closed['id']})"}, status=201)
    await _ok(ns, boss, "POST", f"/knowledge/pages/{page['id']}/publish")
    w["page"], w["closed_page"] = page["id"], closed["id"]

    with ns.admin_engine.begin() as c:
        hit = w["hit"]
        _edge(c, org, "task", hit, "task_link", "task", w["seen"])
        _edge(c, org, "task", hit, "task_link", "task", w["group_task"])
        _edge(c, org, "task", hit, "task_link", "task", w["private_task"])
        _edge(c, org, "task", hit, "origin", "email", w["my_mail"])
        _edge(c, org, "task", hit, "origin", "email", w["their_mail"])
        _edge(c, org, "task", hit, "origin", "whatsapp", w["their_wa"])
        pg = w["page"]
        _edge(c, org, "page", pg, "mentions", "deal", w["deal"], "INFERRED", 0.9)
        _edge(c, org, "page", pg, "mentions", "person", w["active"])
        _edge(c, org, "page", pg, "mentions", "person", w["alumni"])
        _edge(c, org, "page", pg, "mentions", "project", w["projects"][1])
        f = w["filter_hit"]
        _edge(c, org, "task", f, "task_link", "task", w["ambiguous"], "AMBIGUOUS", 0.5)
        _edge(c, org, "task", f, "task_link", "task", w["removed"], removed=True)
        _edge(c, org, "task", f, "task_link", "task", w["low"], "INFERRED", 0.4)
        _edge(c, org, "task", f, "task_link", "task", w["floor"], "INFERRED", 0.5)
        _edge(c, org_b, "task", w["b_task"], "task_link", "task", w["b_other"])
        h2 = w["hit2"]
        _edge(c, org, "task", h2, "origin", "email", w["separate_mail"])
        _edge(c, org, "task", h2, "origin", "email", w["trashed_mail"])
        _edge(c, org, "task", h2, "origin", "whatsapp", w["my_wa"])
        _edge(c, org, "task", h2, "mentions", "artifact", w["my_artifact"])
        _edge(c, org, "task", h2, "mentions", "artifact", w["their_artifact"])
        # P2: more edges to a kind that no app opens than the cap of a hit,
        # written AFTER the one task link, so each ranks before it by age.
        cr = w["crowded"]
        _edge(c, org, "task", cr, "task_link", "task", w["seen"])
        for _ in range(40):
            _edge(c, org, "task", cr, "attendee", "meeting", _id())
    yield w
    with ns.admin_engine.begin() as c:
        c.execute(text("DELETE FROM kb_links WHERE page_id IS NULL"))
        c.execute(text("DELETE FROM artifacts WHERE owner_email IN (:a, :b)"),
                  {"a": me, "b": other})
        c.execute(text("DELETE FROM people WHERE source = :s"), {"s": f"kg6-{TAG}"})
        c.execute(text("DELETE FROM crm_deals WHERE name = 'Deal ' || :t"), {"t": TAG})
        c.execute(text("DELETE FROM crm_deal_statuses WHERE name = 'Open ' || :t"), {"t": TAG})
        for who in (me, other):
            c.execute(text("DELETE FROM email_messages WHERE account_id IN "
                           "(SELECT id FROM email_accounts WHERE user_id = :u)"), {"u": who})
            c.execute(text("DELETE FROM email_accounts WHERE user_id = :u"), {"u": who})
            c.execute(text("DELETE FROM wa_messages WHERE account_id IN "
                           "(SELECT id FROM wa_accounts WHERE user_id = :u)"), {"u": who})
            c.execute(text("DELETE FROM wa_chats WHERE account_id IN "
                           "(SELECT id FROM wa_accounts WHERE user_id = :u)"), {"u": who})
            c.execute(text("DELETE FROM wa_accounts WHERE user_id = :u"), {"u": who})
        for project in (*w["projects"], *w["projects_b"]):
            for sql in ("DELETE FROM pm_tasks WHERE project_id = CAST(:p AS uuid)",
                        "DELETE FROM pm_task_statuses WHERE project_id = CAST(:p AS uuid)",
                        "DELETE FROM pm_project_grants WHERE project_id = CAST(:p AS uuid)",
                        "DELETE FROM pm_projects WHERE id = CAST(:p AS uuid)"):
                c.execute(text(sql), {"p": project})


async def _as(p: Any, user: Any, fn: Any) -> Any:
    """Run ``fn()`` as a request of *user* runs: the tenant from the user."""
    from acb_common.db import bind_tenant, clear_tenant, release_tenant

    from tests.unit._tenant_ladder import tenant_engine_scope

    scope = clear_tenant()
    try:
        async with tenant_engine_scope(_app_dsn(p)):
            token = bind_tenant(user.organization_id)
            try:
                return await fn()
            finally:
                release_tenant(token)
    finally:
        release_tenant(scope)


def _hit(kind: str, ident: str) -> Any:
    from gateway.retrieval.sources import Hit

    return Hit(app="x", kind=kind, id=ident, title="", hint="", href="")


async def _neighbours(p: Any, user: Any, *hits: tuple[str, str],
                      apps: list[str] | None = None) -> list[dict]:
    from gateway.retrieval import company

    return await _as(p, user, lambda: company.neighbours(
        user, [_hit(k, i) for k, i in hits], stamp=False, apps=apps))


def _keys(items: list[dict]) -> set[tuple[str, str]]:
    return {(n["kind"], n["id"]) for n in items}


@_DB_GATE
async def test_a_hidden_record_never_appears_and_an_open_one_does(
        ns, world, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    w = world
    me = _user(w["me"], w["org"])
    got = await _neighbours(ns, me, ("task", w["hit"]), ("page", w["page"]),
                            ("task", w["hit2"]))
    assert _keys(got) == {
        ("task", w["seen"]), ("email", w["my_mail"]), ("person", w["active"]),
        ("whatsapp", w["my_wa"]), ("artifact", w["my_artifact"]),
    }
    shown = repr(got)
    for hidden in ("group_task", "private_task", "their_mail", "their_wa", "deal",
                   "closed_page", "alumni", "separate_mail", "trashed_mail",
                   "their_artifact"):
        assert w[hidden] not in shown, hidden
    for project in w["projects"]:
        assert project not in shown
    by_id = {n["id"]: n for n in got}
    assert by_id[w["seen"]]["edge"] == {"from": f"task:{w['hit']}", "relation": "task_link",
                                         "provenance": "EXTRACTED"}
    assert by_id[w["active"]]["edge"]["from"] == f"page:{w['page']}"


@_DB_GATE
async def test_a_separate_mailbox_and_the_trash_stay_out_as_in_search(ns, world) -> None:  # noqa: F811
    """The verifier's live case: a mail in a mailbox with ``in_all_inboxes``
    false (the NDA case, D-EM-28) and a mail with ``folder = 'trash'``. Email
    search hides both, so a neighbour never names them. The member's inbox
    mail does show, and the app's owner read by id DOES open both, so the
    scope of the search is the reason."""
    from gateway.routes.email.transport.messages import (
        MessageSummariesRequest,
        message_summaries,
    )

    w = world
    me = _user(w["me"], w["org"])
    got = await _neighbours(ns, me, ("task", w["hit"]), ("task", w["hit2"]))
    keys = _keys(got)
    assert ("email", w["my_mail"]) in keys
    assert ("email", w["separate_mail"]) not in keys
    assert ("email", w["trashed_mail"]) not in keys
    owner = await _as(ns, me, lambda: message_summaries(
        req=MessageSummariesRequest(ids=[w["separate_mail"], w["trashed_mail"]]), user=me))
    assert {s["id"] for s in owner["summaries"]} == {w["separate_mail"], w["trashed_mail"]}


@_DB_GATE
async def test_the_artifact_and_whatsapp_reads_open_on_the_real_db(ns, world) -> None:  # noqa: F811
    from acb_memory import artifacts as registry
    from gateway.routes.whatsapp.transport.messages import open_messages

    w = world
    me = _user(w["me"], w["org"])
    arts = await _as(ns, me, lambda: registry.open_artifacts(
        [w["my_artifact"], w["their_artifact"]], me.email))
    assert [a["id"] for a in arts] == [w["my_artifact"]]
    msgs = await _as(ns, me, lambda: open_messages(me, [w["my_wa"], w["their_wa"]]))
    assert [m.id for m in msgs] == [w["my_wa"]]
    other = _user(w["other"], w["org"])
    got = await _neighbours(ns, other, ("task", w["hit2"]))
    assert ("artifact", w["their_artifact"]) in _keys(got)
    assert ("artifact", w["my_artifact"]) not in _keys(got)


@_DB_GATE
async def test_edges_no_app_opens_do_not_use_the_cap_of_a_hit(ns, world) -> None:  # noqa: F811
    from gateway.kb_entities import graph

    w = world
    me = _user(w["me"], w["org"])
    edges = await _as(ns, me, lambda: graph.neighbour_edges(
        me, [("task", w["crowded"])], kinds=["task"], per_hit=30))
    assert [(e.kind, e.id) for e in edges] == [("task", w["seen"])]
    got = await _neighbours(ns, me, ("task", w["crowded"]))
    assert _keys(got) == {("task", w["seen"])}


@_DB_GATE
async def test_crm_access_is_the_reason_the_deal_is_hidden(ns, world) -> None:  # noqa: F811
    from gateway.routes.crm.access import ADMIN_PERMISSION

    w = world
    boss = _user(w["admin"], w["org"], ADMIN_PERMISSION)
    got = await _neighbours(ns, boss, ("page", w["page"]))
    assert ("deal", w["deal"]) in _keys(got)
    (deal,) = [n for n in got if n["kind"] == "deal"]
    assert deal["edge"] == {"from": f"page:{w['page']}", "relation": "mentions",
                            "provenance": "INFERRED"}


@_DB_GATE
async def test_the_other_member_opens_the_closed_page_and_the_group_task(
        ns, world) -> None:  # noqa: F811
    """Non-vacuity: the hidden records exist and open for their audience."""
    w = world
    other = _user(w["other"], w["org"])
    # Tasks and pages only, so the cap of 3 holds both hidden tasks.
    got = await _neighbours(ns, other, ("task", w["hit"]), ("page", w["page"]),
                            apps=["tasks", "knowledge"])
    keys = _keys(got)
    assert ("page", w["closed_page"]) in keys
    assert {("task", w["group_task"]), ("task", w["private_task"])} <= keys
    mail = await _neighbours(ns, other, ("task", w["hit"]), apps=["email"])
    assert _keys(mail) == {("email", w["their_mail"])}


@_DB_GATE
async def test_the_read_drops_ambiguous_removed_and_low_inferred(
        ns, world) -> None:  # noqa: F811
    from gateway.kb_entities import graph

    w = world
    me = _user(w["me"], w["org"])
    edges = await _as(ns, me, lambda: graph.neighbour_edges(
        me, [("task", w["filter_hit"])], kinds=graph.NODE_KINDS))
    assert {e.id for e in edges} == {w["floor"]}
    got = await _neighbours(ns, me, ("task", w["filter_hit"]))
    assert [n["id"] for n in got] == [w["floor"]]
    assert got[0]["edge"]["provenance"] == "INFERRED"


@_DB_GATE
async def test_org_b_reads_no_edge_and_opens_no_record_of_org_a(
        ns, world) -> None:  # noqa: F811
    from gateway.kb_entities import graph

    w = world
    b = _user(w["member_b"], w["org_b"])
    a_nodes = [("task", w["hit"]), ("page", w["page"]), ("task", w["filter_hit"])]
    assert await _as(ns, b, lambda: graph.neighbour_edges(
        b, a_nodes, kinds=graph.NODE_KINDS)) == []
    assert await _neighbours(ns, b, *a_nodes) == []
    # Non-vacuity: B reads its own edge.
    own = await _neighbours(ns, b, ("task", w["b_task"]))
    assert _keys(own) == {("task", w["b_other"])}
    # And A, with the ids of B in hand, reads nothing of B.
    me = _user(w["me"], w["org"])
    assert await _neighbours(ns, me, ("task", w["b_task"])) == []


@_DB_GATE
async def test_the_route_answers_neighbours_with_the_flag_and_none_without(
        ns, world, monkeypatch) -> None:  # noqa: F811
    import httpx
    from acb_auth import get_current_user
    from acb_common import get_settings
    from acb_common.db import bind_tenant
    from fastapi import FastAPI
    from gateway.retrieval import company

    # A cold scratch database is slower than the budgets of production.
    monkeypatch.setattr(company, "SOURCE_TIMEOUT_S", 30.0)
    monkeypatch.setattr(company, "TOTAL_BUDGET_S", 60.0)
    monkeypatch.setattr(company, "GRAPH_TIMEOUT_S", 30.0)
    w = world
    me = _user(w["me"], w["org"])

    async def ask(apps: str | None = None) -> dict:
        async def _caller() -> Any:
            bind_tenant(me.organization_id)
            return me

        app = FastAPI()
        app.include_router(company.router)
        app.dependency_overrides[get_current_user] = _caller
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://kg6.test") as client:
            params = {"q": WORD, **({"apps": apps} if apps else {})}
            res = await client.get("/search/company", params=params)
        assert res.status_code == 200, res.text
        return res.json()

    on = await _as(ns, me, ask)
    assert [h["id"] for h in on["hits"]] == [w["hit"]]
    assert "graph" not in on["skipped"]
    assert _keys(on["neighbours"]) == {("task", w["seen"]), ("email", w["my_mail"])}
    # `apps=` names the sources whose kinds a neighbour may have.
    only = await _as(ns, me, lambda: ask("tasks"))
    assert _keys(only["neighbours"]) == {("task", w["seen"])}
    monkeypatch.setattr(get_settings(), "graph_search_orgs", "", raising=False)
    off = await _as(ns, me, ask)
    assert "neighbours" not in off
    assert off["hits"] == on["hits"]
