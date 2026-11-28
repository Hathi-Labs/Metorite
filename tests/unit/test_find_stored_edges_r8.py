"""R8 — CI-R1b (task 4171): the stored edges of the related step, on a real database.

Spec: ``project-docs/specs/knowledge_graph.md`` §6 and §9.1. Code:
``gateway/retrieval/company.py`` (``_stored_edges`` and ``_related_step``),
``gateway/retrieval/sources.py`` (each ``open``) and
``gateway/kb_entities/graph.py`` (``neighbour_edges``). The checks with no
database are in ``test_find_stored_edges.py``.

Against a REAL Postgres with the whole ladder, as the NON-privileged role of
the phase-4 rehearsal (no superuser, no BYPASSRLS), through each app's own
read by id. A member of org A who holds every app, and whose hit has edges to
records that the member cannot open:

1. a private task of another member, a task of a project that only a
   ``group:<slug>`` grant opens, another member's email and WhatsApp message,
   a mail in a separate mailbox, a trashed mail, another member's artifact, a
   page in a closed space, a person who left and a CRM record without CRM
   access NEVER appear. The records the member can open DO appear, so an
   empty answer cannot pass;
2. an admin with CRM access sees the deal, and the other member sees the
   hidden tasks, mail, artifact and page, so the gates are the reason;
3. no AMBIGUOUS edge, no removed edge and no INFERRED edge below 0.5 is used,
   and an INFERRED edge at 0.5 is. With ``verified``, no page neighbour;
4. org B reads no edge of org A and opens no record of org A, with the ids of
   org A in hand;
5. edges to a kind that no app opens do not push out a task link;
6. ``GET /search/company`` adds the neighbour with ``GRAPH_SEARCH_ORGS`` on,
   and reads no edge with it off.

⚠️ It SKIPS without ``TENANT_LADDER_DATABASE_URL``, and a skip is not a pass.
"""

from __future__ import annotations

import asyncio
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
        "true)"), {"p": project, "o": org, "n": f"cir1-{TAG}-{subject[:20]}", "me": who})
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
        {"u": owner, "m": f"box-{uuid.uuid4().hex[:8]}@cir1.test", "o": org,
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
        {"n": f"Person {status}", "s": f"cir1-{TAG}", "st": status, "o": org}).scalar_one())


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
        "CASE WHEN :gone THEN 'admin@cir1.test' END)"),
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
    monkeypatch.setattr(settings, "context_recency", True, raising=False)
    monkeypatch.setattr(settings, "find_orgs", "*", raising=False)
    monkeypatch.setattr(settings, "artifact_registry_enabled", True, raising=False)

    org, org_b = ns.org_a, ns.org_b
    me, other = _address("me"), _address("other")
    admin, member_b = _address("admin"), _address("member-b")
    _add_member(ns, org, me)
    other_id = _add_member(ns, org, other)
    _add_member(ns, org, admin)
    _add_member(ns, org_b, member_b)
    slug = f"cir1-{TAG}"
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
        w["company"] = str(c.execute(text(
            "INSERT INTO crm_organizations (name, organization_id) VALUES (:n, "
            "CAST(:o AS uuid)) RETURNING id"), {"n": f"Acme {WORD}", "o": org}).scalar_one())
        w["anchor"] = _person(c, org, "active")
        w["anchor_b"] = _person(c, org_b, "active")
        co, mine = w["company"], w["anchor"]
        for rel, kind, key in (
            ("task_link", "task", "seen"), ("task_link", "task", "group_task"),
            ("task_link", "task", "private_task"), ("origin", "email", "my_mail"),
            ("origin", "email", "their_mail"), ("origin", "email", "separate_mail"),
            ("origin", "email", "trashed_mail"), ("origin", "whatsapp", "their_wa"),
            ("origin", "whatsapp", "my_wa"), ("mentions", "artifact", "my_artifact"),
            ("mentions", "artifact", "their_artifact"), ("mentions", "page", "page"),
            ("mentions", "page", "closed_page"), ("mentions", "person", "active"),
            ("mentions", "person", "alumni"),
        ):
            _edge(c, org, "person", mine, rel, kind, w[key])
        _edge(c, org, "deal", w["deal"], "deal_company", "company", co, "INFERRED", 0.9)
        _edge(c, org, "person", mine, "mentions", "deal", w["deal"])
        _edge(c, org, "person", mine, "mentions", "meeting", _id())
        f = w["anchor_filter"] = _person(c, org, "active")
        _edge(c, org, "person", f, "mentions", "task", w["ambiguous"], "AMBIGUOUS", 0.5)
        _edge(c, org, "person", f, "mentions", "task", w["removed"], removed=True)
        _edge(c, org, "person", f, "mentions", "task", w["low"], "INFERRED", 0.4)
        _edge(c, org, "person", f, "mentions", "task", w["floor"], "INFERRED", 0.5)
        _edge(c, org_b, "person", w["anchor_b"], "mentions", "task", w["b_task"])
        cr = w["crowded"] = _person(c, org, "active")
        _edge(c, org, "person", cr, "mentions", "task", w["seen"])
        for _ in range(40):
            _edge(c, org, "person", cr, "attendee", "meeting", _id())
    yield w
    with ns.admin_engine.begin() as c:
        c.execute(text("DELETE FROM kb_links WHERE page_id IS NULL"))
        c.execute(text("DELETE FROM artifacts WHERE owner_email IN (:a, :b)"),
                  {"a": me, "b": other})
        c.execute(text("DELETE FROM people WHERE source = :s"), {"s": f"cir1-{TAG}"})
        c.execute(text("DELETE FROM crm_organizations WHERE name = :n"),
                  {"n": f"Acme {WORD}"})
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


@pytest.fixture(autouse=True)
def roomy(monkeypatch):
    """A cold scratch database is slower than the budgets of production. The
    caps are lifted too, so a hidden record can never hide behind a cap."""
    from gateway.retrieval import company

    for name in ("SOURCE_TIMEOUT_S", "TOTAL_BUDGET_S", "NEAR_LOOKUP_S",
                 "BROADEN_BUDGET_S", "RELATED_BUDGET_S", "ANSWER_CAP_S"):
        monkeypatch.setattr(company, name, 120.0)
    monkeypatch.setattr(company, "RELATED_PER_HIT", 60)
    monkeypatch.setattr(company, "RELATED_MAX", 200)
    monkeypatch.setattr(company, "RELATED_TOKENS", 100_000)


def _anchor(kind: str, ident: str) -> Any:
    from gateway.retrieval.sources import Hit

    return Hit(app="people" if kind == "person" else "crm", kind=kind, id=ident,
               title="", hint="", href="")


async def _related(p: Any, user: Any, *hits: tuple[str, str],
                   verified: bool = False) -> list[Any]:
    """Rung 4 itself, as *user*, on the anchors *hits*."""
    from gateway.retrieval import company

    async def step() -> list[Any]:
        return await company._related_step(
            user, [_anchor(k, i) for k, i in hits], ["acme"], since=None,
            until=asyncio.get_running_loop().time() + 120.0, verified=verified)

    return await _as(p, user, step)


def _keys(hits: list[Any]) -> set[tuple[str, str]]:
    return {(h.kind, h.id) for h in hits}


HIDDEN = ("group_task", "private_task", "their_mail", "their_wa", "deal",
          "closed_page", "alumni", "separate_mail", "trashed_mail", "their_artifact")


@_DB_GATE
async def test_a_hidden_record_never_appears_and_an_open_one_does(
        ns, world, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    w = world
    me = _user(w["me"], w["org"])
    got = await _related(ns, me, ("person", w["anchor"]))
    keys = _keys(got)
    assert {
        ("task", w["seen"]), ("email", w["my_mail"]), ("whatsapp", w["my_wa"]),
        ("artifact", w["my_artifact"]), ("page", w["page"]), ("person", w["active"]),
    } <= keys
    shown = repr([h.as_dict(stamp=True, find=True) for h in got])
    for hidden in HIDDEN:
        assert w[hidden] not in shown, hidden
    for project in w["projects"]:
        assert project not in shown
    by_id = {h.id: h for h in got}
    assert by_id[w["seen"]].edge == "task_of"
    assert by_id[w["seen"]].from_ == f"person:{w['anchor']}"
    assert by_id[w["seen"]].matched_by == "related"


@_DB_GATE
async def test_the_gates_are_the_reason_the_records_are_hidden(ns, world) -> None:  # noqa: F811
    """Non-vacuity: each hidden record exists, and opens for its audience."""
    from gateway.routes.crm.access import ADMIN_PERMISSION

    w = world
    boss = _user(w["admin"], w["org"], ADMIN_PERMISSION)
    got = await _related(ns, boss, ("person", w["anchor"]), ("company", w["company"]))
    assert ("deal", w["deal"]) in _keys(got)
    other = _user(w["other"], w["org"])
    got = await _related(ns, other, ("person", w["anchor"]))
    assert {("task", w["group_task"]), ("task", w["private_task"]),
            ("email", w["their_mail"]), ("whatsapp", w["their_wa"]),
            ("artifact", w["their_artifact"]), ("page", w["closed_page"])} <= _keys(got)
    assert ("email", w["my_mail"]) not in _keys(got)
    assert ("artifact", w["my_artifact"]) not in _keys(got)


@_DB_GATE
async def test_verified_adds_no_page_neighbour(ns, world) -> None:  # noqa: F811
    w = world
    me = _user(w["me"], w["org"])
    got = await _related(ns, me, ("person", w["anchor"]), verified=True)
    assert ("task", w["seen"]) in _keys(got)
    assert not [h for h in got if h.kind == "page"]


@_DB_GATE
async def test_ambiguous_removed_and_low_inferred_are_never_used(ns, world) -> None:  # noqa: F811
    w = world
    me = _user(w["me"], w["org"])
    got = await _related(ns, me, ("person", w["anchor_filter"]))
    assert _keys(got) == {("task", w["floor"])}


@_DB_GATE
async def test_org_b_reads_no_edge_and_opens_no_record_of_org_a(ns, world) -> None:  # noqa: F811
    from gateway.kb_entities import graph

    w = world
    b = _user(w["member_b"], w["org_b"])
    a_nodes = [("person", w["anchor"]), ("person", w["anchor_filter"]),
               ("company", w["company"])]
    assert await _as(ns, b, lambda: graph.neighbour_edges(
        b, a_nodes, kinds=graph.NODE_KINDS)) == []
    assert await _related(ns, b, *a_nodes) == []
    # Non-vacuity: B reads its own edge.
    own = await _related(ns, b, ("person", w["anchor_b"]))
    assert _keys(own) == {("task", w["b_task"])}
    # And A, with the ids of B in hand, reads nothing of B.
    me = _user(w["me"], w["org"])
    assert await _related(ns, me, ("person", w["anchor_b"])) == []


@_DB_GATE
async def test_edges_no_app_opens_do_not_use_the_cap_of_a_node(ns, world) -> None:  # noqa: F811
    from gateway.kb_entities import graph

    w = world
    me = _user(w["me"], w["org"])
    edges = await _as(ns, me, lambda: graph.neighbour_edges(
        me, [("person", w["crowded"])], kinds=["task"], per_node=30))
    assert [(e.kind, e.id) for e in edges] == [("task", w["seen"])]
    assert _keys(await _related(ns, me, ("person", w["crowded"]))) == {("task", w["seen"])}


@_DB_GATE
async def test_the_route_adds_the_neighbour_only_with_the_flag(
        ns, world, monkeypatch) -> None:  # noqa: F811
    from acb_common import get_settings

    from tests.unit.test_company_search_r8 import _ask

    w = world
    me = _user(w["me"], w["org"])
    with ns.admin_engine.begin() as c:
        c.execute(text("UPDATE people SET name = :n WHERE id = CAST(:i AS uuid)"),
                  {"n": f"Zed {WORD}", "i": w["anchor"]})

    async def ask() -> dict:
        return await _ask(ns, me, {"q": WORD, "apps": "people"})

    on = await ask()
    assert [h["id"] for h in on["hits"]] == [w["anchor"]]
    related = {h["id"]: h for h in on.get("related", [])}
    assert related[w["seen"]]["edge"] == "task_of"
    monkeypatch.setattr(get_settings(), "graph_search_orgs", "", raising=False)

    async def refuse(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("an org off the flag must read no edge")

    from gateway.kb_entities import graph

    monkeypatch.setattr(graph, "neighbour_edges", refuse)
    off = await ask()
    assert off["hits"] == on["hits"]
    assert w["seen"] not in repr(off)
