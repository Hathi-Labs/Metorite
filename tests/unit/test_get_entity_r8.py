"""R8 — the entity card on a real database (WS-KG KG-7).

Spec: ``project-docs/specs/knowledge_graph.md`` §3.1, §6, §7.1 and the KG-7
row of §9. Code: ``gateway/retrieval/entity.py`` (the card),
``gateway/kb_entities/resolver.py`` (``find_entity``) and the KG-6 step of
``gateway/retrieval/company.py``. The checks with no database are in
``test_get_entity_tool.py``.

Against a REAL Postgres with the whole ladder, as the NON-privileged role of
the phase-4 rehearsal (no superuser, no BYPASSRLS). The world of
``test_kg_neighbours_r8.py``, plus one CRM company with an entity row, two
aliases and a page, and edges to the company from records that the member
can and cannot open:

1. without CRM access, the name, an alias and the citation each give the
   empty answer of an unknown name, with no name or alias of the company;
2. with CRM access (``crm_settings.access = 'everyone'``), the card shows the
   company, its aliases and its page. Its neighbours hold the member's task,
   the member's mail, the deal and the active person of the page. They never
   hold a private task, a task that only a ``group:<slug>`` grant opens,
   another member's mail, a page in a closed space or a person who left;
3. the other member, who may open those records, sees them (non-vacuity);
4. org B, with the ids of org A in hand, gets the empty answer;
5. the tool, through the real route on the real database, prints the card.

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
    _assert_non_priv,
    ns,
)
from tests.unit.test_kg_neighbours_r8 import (  # noqa: F401
    _as,
    _edge,
    _user,
    world,
)

pytestmark = _DB_GATE

TAG = uuid.uuid4().hex[:8]
NAME = f"Heronworks {TAG}"
ALIAS = f"HW-{TAG}"


@pytest.fixture
async def card_world(ns, world):  # noqa: F811 — a fixture
    """One CRM company of org A with an entity row, and its edges."""
    w = world
    org = w["org"]
    company, entity_id = str(uuid.uuid4()), str(uuid.uuid4())
    with ns.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO crm_companies (id, name, industry, organization_id) VALUES "
            "(CAST(:c AS uuid), :n, 'Machines', CAST(:o AS uuid))"),
            {"c": company, "n": NAME, "o": org})
        c.execute(text(
            "INSERT INTO kb_entities (id, organization_id, type, ref_table, ref_id, "
            "aliases, page_id) VALUES (CAST(:e AS uuid), CAST(:o AS uuid), 'customer', "
            "'crm_companies', CAST(:c AS uuid), CAST(:a AS text[]), CAST(:p AS uuid))"),
            {"e": entity_id, "o": org, "c": company, "a": [ALIAS, f"Heron {TAG}"],
             "p": w["page"]})
        node = ("company", company)
        _edge(c, org, "deal", w["deal"], "deal_company", *node)
        for kind, key in (("task", "seen"), ("task", "private_task"),
                          ("task", "group_task"), ("email", "my_mail"),
                          ("email", "their_mail"), ("page", "closed_page")):
            _edge(c, org, kind, w[key], "mentions", *node, "INFERRED", 0.9)
    w["company"], w["entity"] = company, entity_id
    yield w
    with ns.admin_engine.begin() as c:
        c.execute(text("DELETE FROM kb_entities WHERE id = CAST(:e AS uuid)"), {"e": entity_id})
        c.execute(text("DELETE FROM crm_companies WHERE id = CAST(:c AS uuid)"),
                  {"c": company})
        c.execute(text("DELETE FROM crm_settings"))


def _open_crm(ns: Any, org: str) -> None:  # noqa: F811 — `ns` is the fixture's value
    with ns.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO crm_settings (organization_id, access) VALUES "
            "(CAST(:o AS uuid), 'everyone') ON CONFLICT (organization_id) DO UPDATE "
            "SET access = 'everyone'"), {"o": org})


async def _card(p: Any, user: Any, words: str) -> dict:
    from gateway.retrieval import entity

    return await _as(p, user, lambda: entity.card(user, words))


def _keys(items: list[dict]) -> set[tuple[str, str]]:
    return {(n["kind"], n["id"]) for n in items}


@pytest.fixture(autouse=True)
def _slow_db(monkeypatch):
    """A cold scratch database is slower than the budget of production."""
    from gateway.retrieval import company

    monkeypatch.setattr(company, "GRAPH_TIMEOUT_S", 30.0)


@_DB_GATE
async def test_without_crm_access_the_company_is_an_unknown_name(
        ns, card_world, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    w = card_world
    me = _user(w["me"], w["org"])
    unknown = await _card(ns, me, f"Nothing {TAG}")
    assert unknown == {"query": f"Nothing {TAG}", "candidates": []}
    for words in (NAME, ALIAS, f"company:{w['company']}", f"entity:{w['entity']}"):
        got = await _card(ns, me, words)
        assert got == {"query": words, "candidates": []}, words


@_DB_GATE
async def test_with_crm_access_the_card_shows_only_what_the_member_opens(
        ns, card_world) -> None:  # noqa: F811
    w = card_world
    _open_crm(ns, w["org"])
    me = _user(w["me"], w["org"])
    for words in (NAME, ALIAS.lower(), f"company:{w['company']}", f"entity:{w['entity']}"):
        got = await _card(ns, me, words)
        assert got["entity"]["name"] == NAME, words
        assert got["entity"]["full_id"] == f"company:{w['company']}"
        assert sorted(got["entity"]["aliases"]) == sorted([ALIAS, f"Heron {TAG}"])
        assert [r["id"] for r in got["records"]] == [w["page"]]
    keys = _keys(got["neighbours"])
    assert {("deal", w["deal"]), ("task", w["seen"]), ("email", w["my_mail"]),
            ("person", w["active"])} <= keys
    shown = repr(got)
    for hidden in ("private_task", "group_task", "their_mail", "closed_page", "alumni"):
        assert w[hidden] not in shown, hidden
    for item in got["neighbours"]:
        assert item["href"].startswith("/"), item


@_DB_GATE
async def test_the_other_member_opens_the_records_that_were_hidden(
        ns, card_world) -> None:  # noqa: F811
    w = card_world
    _open_crm(ns, w["org"])
    other = _user(w["other"], w["org"])
    got = await _card(ns, other, NAME)
    keys = _keys(got["neighbours"])
    assert {("task", w["private_task"]), ("task", w["group_task"]),
            ("email", w["their_mail"]), ("page", w["closed_page"])} <= keys
    assert ("email", w["my_mail"]) not in keys


@_DB_GATE
async def test_org_b_gets_the_unknown_answer_with_the_ids_of_org_a(
        ns, card_world) -> None:  # noqa: F811
    w = card_world
    _open_crm(ns, w["org"])
    _open_crm(ns, w["org_b"])
    b = _user(w["member_b"], w["org_b"])
    for words in (NAME, ALIAS, f"company:{w['company']}", f"entity:{w['entity']}"):
        assert await _card(ns, b, words) == {"query": words, "candidates": []}, words
    # Non-vacuity: org A reads its own entity in the same run.
    me = _user(w["me"], w["org"])
    assert (await _card(ns, me, NAME))["entity"]["name"] == NAME


@_DB_GATE
async def test_the_tool_prints_the_card_through_the_real_route(
        ns, card_world, monkeypatch) -> None:  # noqa: F811
    import httpx
    from acb_auth import get_current_user
    from acb_common.db import bind_tenant
    from acb_skills import company_search_tools as cst
    from fastapi import FastAPI
    from gateway.retrieval import company

    w = card_world
    _open_crm(ns, w["org"])
    me = _user(w["me"], w["org"])

    async def _caller() -> Any:
        bind_tenant(me.organization_id)
        return me

    app = FastAPI()
    app.include_router(company.router)
    app.dependency_overrides[get_current_user] = _caller
    real = httpx.AsyncClient
    monkeypatch.setattr(cst.httpx, "AsyncClient", lambda *a, **k: real(
        *a, transport=httpx.ASGITransport(app=app), **k))
    monkeypatch.setenv("GATEWAY_URL", "http://kg7.test")
    monkeypatch.setattr(cst, "_current_user_email", lambda: me.email)
    out = await _as(ns, me, lambda: cst.get_entity(NAME))
    lines = out.splitlines()
    assert lines[0] == cst.ENTITY_HEADER
    assert lines[1].startswith(f"- «{NAME}» (Customer, also: ")
    assert f"full_id: company:{w['company']}" in lines[1]
    assert cst.NEIGHBOURS_HEADER in lines
    assert f"full_id: deal:{w['deal']}" in out
    assert w["private_task"] not in out and w["their_mail"] not in out
    assert len(out) <= company.NEIGHBOUR_TOKENS * company.CHARS_PER_TOKEN
