"""R8 — the 14-day expiry of Knowledge proposals on a real database (WS-61 KB-5c).

Spec: ``project-docs/specs/knowledge_base.md`` §8.4 and the KB-5c row of §17.

What this proves against a REAL Postgres, as the NON-privileged role of the
phase-4 rehearsal (no superuser, no BYPASSRLS), through the real loop:

1. an edit proposal 15 days old becomes ``expired`` with ``decided_at`` set
   and ``decided_by = 'system'``. A 13-day one stays ``pending``;
2. a proposed page 15 days old is archived from ``draft`` with ONE new
   version (reason ``archive``, author ``system``, via ``sync``). A 13-day
   one stays a draft and gets no version;
3. a draft with no ``agent_proposal`` origin, a PUBLISHED proposed page and
   a decided edit are untouched;
4. a draft that a person edited 2 days ago is not archived;
5. one tick expires the rows of two orgs, each in its own session, and an org
   that Knowledge does not serve is left alone;
6. a second tick changes nothing and adds no version;
7. the Review list reads ``expired`` for the expired page and edit, and the
   expired page does not read as ``rejected``.

The rows are aged by an owner-role UPDATE of ``created_at`` and
``updated_at``.
"""

from __future__ import annotations

import json
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
    _ok,
    _principal,
    _space,
    ns,
)
from tests.unit.test_kb_proposals_r8 import (  # noqa: F401
    CHAT,
    _published,
    _rows,
    _scalar,
)

# ⚠️ Every test here needs the real database. Without this gate the suite
# ERRORS where no ladder URL is set (the deploy run), and the deploy stops.
pytestmark = _DB_GATE


@pytest.fixture(autouse=True)
def _proposals_on(ns, monkeypatch):  # noqa: F811 — a fixture
    """``KNOWLEDGE_PROPOSALS`` on, to file the proposals. The expiry does not need it."""
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "knowledge_proposals", True, raising=False)


async def _tick(p: Any) -> Any:
    """One REAL tick, on the app engine. The loop binds each tenant itself."""
    from acb_common.db import clear_tenant, release_tenant
    from gateway.routes.knowledge import proposal_expiry

    from tests.unit._tenant_ladder import tenant_engine_scope

    proposal_expiry._stopping.clear()
    scope = clear_tenant()
    try:
        async with tenant_engine_scope(_app_dsn(p)):
            return await proposal_expiry.run_tick()
    finally:
        release_tenant(scope)


async def _file_page(p: Any, user: Any, space: str, title: str) -> str:
    made = await _ok(p, user, "POST", "/knowledge/proposals",
                     {"kind": "page", "space_id": space, "title": title,
                      "body_md": f"body of {title}", "summary": "From the chat."},
                     status=201, headers=CHAT)
    return made["id"]


async def _file_edit(p: Any, user: Any, page_id: str, body: str) -> str:
    page = await _ok(p, user, "GET", f"/knowledge/pages/{page_id}")
    made = await _ok(p, user, "POST", "/knowledge/proposals",
                     {"kind": "edit", "page_id": page_id, "body_md": body,
                      "base_version": page["versions"][0]["version"],
                      "summary": "A change."}, status=201, headers=CHAT)
    return made["id"]


def _age_page(p: Any, page_id: str, *, created: int, updated: int | None = None) -> None:
    """Age a page: created *created* days ago, last edited *updated* days ago."""
    with p.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE kb_pages SET created_at = now() - make_interval(days => :c), "
            "updated_at = now() - make_interval(days => :u) WHERE id = CAST(:p AS uuid)"),
            {"c": created, "u": created if updated is None else updated, "p": page_id})


def _age_edit(p: Any, proposal_id: str, days: int) -> None:
    with p.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE kb_page_proposals SET created_at = now() - make_interval(days => :d) "
            "WHERE id = CAST(:i AS uuid)"), {"d": days, "i": proposal_id})


def _page(p: Any, page_id: str) -> Any:
    return _rows(p, "SELECT state, archived_from_state, origin FROM kb_pages "
                    "WHERE id = CAST(:p AS uuid)", p=page_id)[0]


def _versions(p: Any, page_id: str) -> list[tuple[Any, ...]]:
    return [tuple(r) for r in _rows(
        p, "SELECT version, reason, author_email, via FROM kb_page_versions "
           "WHERE page_id = CAST(:p AS uuid) ORDER BY version", p=page_id)]


def _edit(p: Any, proposal_id: str) -> tuple[Any, ...]:
    return tuple(_rows(p, "SELECT status, decided_by, decided_at IS NOT NULL "
                          "FROM kb_page_proposals WHERE id = CAST(:i AS uuid)",
                       i=proposal_id)[0])


async def _world(p: Any, org: str, tag: str) -> tuple[Any, str]:
    """A writer, a space and a published page in *org*."""
    admin = _principal(_address("admin"), org, CAPABILITY)
    email = _address(tag)
    writer = _principal(email, org)
    space = await _space(p, admin, f"Space {tag}", [{"subject": email, "level": "write"}])
    return writer, space


async def test_an_edit_proposal_expires_at_14_days(ns, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    writer, space = await _world(ns, ns.org_a, "w1")
    pid = await _published(ns, writer, space, "Runbook", "old")
    old = await _file_edit(ns, writer, pid, "new one")
    young = await _file_edit(ns, writer, pid, "new two")
    _age_edit(ns, old, 15)
    _age_edit(ns, young, 13)
    before = len(_versions(ns, pid))

    await _tick(ns)

    assert _edit(ns, old) == ("expired", "system", True)
    assert _edit(ns, young) == ("pending", None, False)
    # The page of an edit gets no version from the expiry.
    assert len(_versions(ns, pid)) == before


async def test_a_page_proposal_archives_with_one_system_version(
    ns, app_engine,  # noqa: F811
) -> None:
    _assert_non_priv(app_engine)
    writer, space = await _world(ns, ns.org_a, "w2")
    old = await _file_page(ns, writer, space, "Old idea")
    young = await _file_page(ns, writer, space, "Young idea")
    _age_page(ns, old, created=15)
    _age_page(ns, young, created=13)
    before_old, before_young = len(_versions(ns, old)), len(_versions(ns, young))

    await _tick(ns)

    state, from_state, origin = _page(ns, old)
    assert (state, from_state) == ("archived", "draft")
    assert (origin if isinstance(origin, dict) else json.loads(origin))["expired"] is True
    versions = _versions(ns, old)
    assert len(versions) == before_old + 1
    assert versions[-1][1:] == ("archive", "system", "sync")
    assert versions[-1][0] == before_old + 1

    assert _page(ns, young)[:2] == ("draft", None)
    assert len(_versions(ns, young)) == before_young


async def test_other_rows_are_untouched(ns, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    writer, space = await _world(ns, ns.org_a, "w3")
    # A plain draft with no agent origin.
    plain = (await _ok(ns, writer, "POST", "/knowledge/pages",
                       {"space_id": space, "title": "Plain", "body_md": "x"}, status=201))["id"]
    # A published agent proposal (accepted).
    accepted = await _file_page(ns, writer, space, "Accepted idea")
    await _ok(ns, writer, "POST", f"/knowledge/proposals/{accepted}/accept")
    # A decided edit.
    base = await _published(ns, writer, space, "Base", "old")
    done = await _file_edit(ns, writer, base, "newer")
    await _ok(ns, writer, "POST", f"/knowledge/proposals/{done}/reject")
    for page in (plain, accepted, base):
        _age_page(ns, page, created=40)
    _age_edit(ns, done, 40)
    counts = {p: len(_versions(ns, p)) for p in (plain, accepted)}

    await _tick(ns)

    assert _page(ns, plain)[0] == "draft"
    assert _page(ns, accepted)[0] == "published"
    assert _edit(ns, done)[0] == "rejected"
    assert {p: len(_versions(ns, p)) for p in (plain, accepted)} == counts


async def test_an_edited_draft_is_not_unanswered(ns, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    writer, space = await _world(ns, ns.org_a, "w4")
    pid = await _file_page(ns, writer, space, "Edited idea")
    _age_page(ns, pid, created=20, updated=2)

    await _tick(ns)

    assert _page(ns, pid)[0] == "draft"


async def test_each_org_has_its_own_session_and_an_unserved_org_is_left(
    ns, app_engine, monkeypatch,  # noqa: F811
) -> None:
    from acb_common import get_settings

    _assert_non_priv(app_engine)
    writer_a, space_a = await _world(ns, ns.org_a, "wa")
    writer_b, space_b = await _world(ns, ns.org_b, "wb")
    page_a = await _file_page(ns, writer_a, space_a, "A idea")
    page_b = await _file_page(ns, writer_b, space_b, "B idea")
    _age_page(ns, page_a, created=15)
    _age_page(ns, page_b, created=15)

    # Only org A is served: org B keeps its row.
    monkeypatch.setattr(get_settings(), "knowledge_orgs", ns.org_a, raising=False)
    await _tick(ns)
    assert _page(ns, page_a)[0] == "archived" and _page(ns, page_b)[0] == "draft"

    # Both served, and each tick session binds one org (record them).
    from gateway.routes.knowledge import proposal_expiry

    bound: list[str] = []
    real = proposal_expiry.tenant_session

    def spy(org: str) -> Any:
        bound.append(org)
        return real(org)

    monkeypatch.setattr(get_settings(), "knowledge_orgs", "*", raising=False)
    monkeypatch.setattr(proposal_expiry, "tenant_session", spy)
    await _tick(ns)
    assert _page(ns, page_b)[0] == "archived"
    assert bound.count(ns.org_a) == 1 and bound.count(ns.org_b) == 1


async def test_a_second_tick_changes_nothing(ns, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    writer, space = await _world(ns, ns.org_a, "w6")
    pid = await _file_page(ns, writer, space, "Once")
    _age_page(ns, pid, created=15)
    await _tick(ns)
    first = _versions(ns, pid)
    second = await _tick(ns)
    assert _versions(ns, pid) == first
    assert second.pages == 0 and second.edits == 0


async def test_the_review_list_reads_expired_and_not_rejected(
    ns, app_engine,  # noqa: F811
) -> None:
    _assert_non_priv(app_engine)
    writer, space = await _world(ns, ns.org_a, "w7")
    page = await _file_page(ns, writer, space, "Lapsed idea")
    base = await _published(ns, writer, space, "Base page", "old")
    edit = await _file_edit(ns, writer, base, "new")
    _age_page(ns, page, created=15)
    _age_edit(ns, edit, 15)

    await _tick(ns)

    expired = await _ok(ns, writer, "GET", "/knowledge/proposals?status=expired")
    got = {(r["kind"], r["id"], r["status"]) for r in expired["proposals"]}
    assert got == {("page", page, "expired"), ("edit", edit, "expired")}
    rejected = await _ok(ns, writer, "GET", "/knowledge/proposals?status=rejected")
    assert page not in {r["id"] for r in rejected["proposals"]}
    pending = await _ok(ns, writer, "GET", "/knowledge/proposals?status=pending")
    assert page not in {r["id"] for r in pending["proposals"]}
