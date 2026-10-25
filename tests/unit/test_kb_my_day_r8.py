"""R8 — the Knowledge cards of My Day on a real database (WS-61 KB-9b slice 1).

Spec: ``project-docs/specs/knowledge_base.md`` §9 and §17 (the KB-9b row).

What this proves against a REAL Postgres, as the NON-privileged role of the
phase-4 rehearsal (RLS forced), through ``my_day.read`` and the provider:

- A1: an expired published page gives its owner one card, or its author when
  the page has no owner. With the owner set to B, B gets it and author A does
  not. A verified page, a draft, a page in a ``me`` space and a page that the
  clause hides give none.
- A2: a page that another author published less than 24 hours ago gives a
  reader of its space one card. The viewer's own page, an old page, a page of
  a closed space and a page of org B give none. Seven new pages give five
  cards, the newest first.
- A4: the read opens ONE session.

The owner ``COALESCE``, the ``interval`` test, the author test and the one
visibility clause are SQL that a hermetic fake accepts unread.
"""

from __future__ import annotations

import contextlib
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
    _assert_non_priv,
    _ok,
    _principal,
    _space,
    ns,
)
from tests.unit.test_kb_search_r8 import _bound, _page, kb  # noqa: F401

# ⚠️ Every test here needs the real database. Without this gate the suite
# ERRORS where no ladder URL is set (the deploy run), and the deploy stops.
pytestmark = _DB_GATE

BODY = "The warranty covers the nozzle for twelve months."


def _sql(world: Any, sql: str, **params: Any) -> None:
    with world.admin_engine.begin() as c:
        c.execute(text(sql), params)


async def _read(kb: Any, user: Any, *, gaps_on: bool = False) -> Any:  # noqa: F811
    from gateway.routes.knowledge import my_day

    return await _bound(kb, user, lambda: my_day.read(user, gaps_on=gaps_on, limit=16))


def _ids(rows: list[dict[str, Any]]) -> list[str]:
    return [r["id"] for r in rows]


async def _org_space(kb: Any, admin: Any, name: str = "Policies") -> str:  # noqa: F811
    return await _space(kb, admin, name, [{"subject": "org", "level": "read"}])


# ── A1: the page that needs a review ────────────────────────────────────────

async def test_an_expired_page_gives_one_card_to_its_owner(kb, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    admin = _principal(_address("admin"), kb.org_a, CAPABILITY)
    a = _principal(_address("a"), kb.org_a)
    b = _principal(_address("b"), kb.org_a)
    space = await _org_space(kb, admin)
    no_owner = await _page(kb, admin, space, "No owner", BODY)
    owned_by_b = await _page(kb, admin, space, "Owned by B", BODY)
    verified = await _page(kb, admin, space, "Verified", BODY)
    draft = await _page(kb, admin, space, "Draft", BODY, publish=False)
    closed_space = await _space(kb, admin, "Closed", [{"subject": admin.email, "level": "write"}])
    closed = await _page(kb, admin, closed_space, "Closed", BODY)
    me_page = await _ok(kb, a, "POST", "/knowledge/pages",
                        {"title": "Mine", "body_md": "private"}, status=201)
    await _ok(kb, a, "POST", f"/knowledge/pages/{me_page['id']}/publish")

    past = "review_at = now() - interval '2 days'"
    _sql(kb, f"UPDATE kb_pages SET author_email = :a, owner_email = NULL, {past} "
             "WHERE id = CAST(:p AS uuid)", a=a.email, p=no_owner)
    _sql(kb, f"UPDATE kb_pages SET author_email = :a, owner_email = :b, {past} "
             "WHERE id = CAST(:p AS uuid)", a=a.email, b=b.email, p=owned_by_b)
    # Verified: the review date is ahead, and A owns it.
    _sql(kb, "UPDATE kb_pages SET author_email = :a, owner_email = :a "
             "WHERE id = CAST(:p AS uuid)", a=a.email, p=verified)
    for pid in (draft, closed):
        _sql(kb, f"UPDATE kb_pages SET author_email = :a, owner_email = :a, {past} "
                 "WHERE id = CAST(:p AS uuid)", a=a.email, p=pid)
    _sql(kb, f"UPDATE kb_pages SET owner_email = :a, {past} WHERE id = CAST(:p AS uuid)",
         a=a.email, p=me_page["id"])

    # A: no owner on one page, so the author gets it. Not the page B owns,
    # the draft, the verified page, the closed space or the me space.
    assert _ids((await _read(kb, a)).review) == [no_owner]
    # B owns one page that A wrote, and B did not write any.
    assert _ids((await _read(kb, b)).review) == [owned_by_b]
    # One page is one row, even for a manager who reads every space.
    assert _ids((await _read(kb, admin)).review) == []


# ── A2: the page that is new ────────────────────────────────────────────────

async def test_a_new_page_gives_one_card_to_a_reader(kb, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    admin = _principal(_address("admin"), kb.org_a, CAPABILITY)
    reader = _principal(_address("r"), kb.org_a)
    space = await _org_space(kb, admin)
    fresh = await _page(kb, admin, space, "Fresh", BODY)
    old = await _page(kb, admin, space, "Old", BODY)
    mine = await _page(kb, reader, await _space_for(kb, admin, reader), "Mine", BODY)
    closed_space = await _space(kb, admin, "Closed", [{"subject": admin.email, "level": "write"}])
    closed = await _page(kb, admin, closed_space, "Closed", BODY)
    draft = await _page(kb, admin, space, "Draft", BODY, publish=False)
    admin_b = _principal(_address("admin-b"), kb.org_b, CAPABILITY)
    theirs = await _page(kb, admin_b, await _org_space(kb, admin_b), "Theirs", BODY)
    _sql(kb, "UPDATE kb_pages SET published_at = now() - interval '25 hours' "
             "WHERE id = CAST(:p AS uuid)", p=old)

    ids = _ids((await _read(kb, reader)).shared)
    assert fresh in ids
    assert mine not in ids, "the reader wrote it"
    assert old not in ids and closed not in ids and draft not in ids and theirs not in ids
    assert len(ids) == len(set(ids))
    # The admin wrote the others, and gets the page that the reader wrote.
    admins = _ids((await _read(kb, admin)).shared)
    assert admins == [mine]
    # Org B reads its own page only.
    member_b = _principal(_address("mb"), kb.org_b)
    assert _ids((await _read(kb, member_b)).shared) == [theirs]


async def _space_for(kb: Any, admin: Any, writer: Any) -> str:  # noqa: F811
    return await _space(kb, admin, "Team", [
        {"subject": "org", "level": "read"}, {"subject": writer.email, "level": "write"}])


async def test_seven_new_pages_give_five_cards_newest_first(kb, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    admin = _principal(_address("admin"), kb.org_a, CAPABILITY)
    reader = _principal(_address("r"), kb.org_a)
    space = await _org_space(kb, admin)
    made = [await _page(kb, admin, space, f"Page {i}", BODY) for i in range(7)]
    for i, pid in enumerate(made):
        _sql(kb, "UPDATE kb_pages SET published_at = now() - make_interval(mins => :m) "
                 "WHERE id = CAST(:p AS uuid)", m=(i + 1) * 10, p=pid)
    assert _ids((await _read(kb, reader)).shared) == made[:5]


# ── A4: one session ─────────────────────────────────────────────────────────

async def test_the_read_opens_one_session(kb, app_engine, monkeypatch) -> None:  # noqa: F811
    from gateway.routes.knowledge import core
    from gateway.routes.shell import feed

    _assert_non_priv(app_engine)
    admin = _principal(_address("admin"), kb.org_a, CAPABILITY)
    other = _principal(_address("w"), kb.org_a)
    await _page(kb, other, await _space_for(kb, admin, other), "New", BODY)
    opened: list[str] = []
    real = core._tenant_session

    @contextlib.asynccontextmanager
    async def counting():
        opened.append("kb")
        async with real() as db:
            yield db

    monkeypatch.setattr(core, "_tenant_session", counting)
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "knowledge_gaps", "*", raising=False)
    found = await _bound(kb, admin, lambda: feed._knowledge(admin))
    assert opened == ["kb"]
    kinds = {s.kind for s in found.signals()}
    assert kinds == {"doc_shared_with_me"}


# ── F4: an archived page, an archived space and a me space give no card ─────

async def test_an_archived_page_an_archived_space_and_a_me_space_give_no_new_card(
        kb, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    admin = _principal(_address("admin"), kb.org_a, CAPABILITY)
    writer = _principal(_address("w"), kb.org_a)
    reader = _principal(_address("r"), kb.org_a)
    team = await _space_for(kb, admin, writer)
    live = await _page(kb, writer, team, "Live", BODY)
    gone = await _page(kb, writer, team, "Archived page", BODY)
    shelved_space = await _space(kb, admin, "Shelved", [
        {"subject": "org", "level": "read"}, {"subject": writer.email, "level": "write"}])
    shelved = await _page(kb, writer, shelved_space, "In a shelved space", BODY)
    # The writer's own me space holds a published page that someone else wrote.
    mine = await _ok(kb, reader, "POST", "/knowledge/pages",
                     {"title": "Reader me page", "body_md": "private"}, status=201)
    await _ok(kb, reader, "POST", f"/knowledge/pages/{mine['id']}/publish")
    _sql(kb, "UPDATE kb_pages SET author_email = :a WHERE id = CAST(:p AS uuid)",
         a=writer.email, p=mine["id"])
    _sql(kb, "UPDATE kb_pages SET state = 'archived', archived_from_state = 'published', "
             "archived_at = now() WHERE id = CAST(:p AS uuid)", p=gone)
    _sql(kb, "UPDATE kb_spaces SET archived_at = now() WHERE id = CAST(:s AS uuid)",
         s=shelved_space)

    # The reader reads `live` only. The me page is the reader's own space.
    assert _ids((await _read(kb, reader)).shared) == [live]
    # A manager reads archived pages through the clause, and gets no card.
    assert _ids((await _read(kb, admin)).shared) == [live]


# ── F2: twelve expired pages give ten review cards and still the new page ───

async def test_twelve_expired_pages_give_ten_review_rows_and_the_shared_card(
        kb, app_engine) -> None:  # noqa: F811
    from gateway.routes.shell import feed

    _assert_non_priv(app_engine)
    admin = _principal(_address("admin"), kb.org_a, CAPABILITY)
    owner = _principal(_address("o"), kb.org_a)
    space = await _org_space(kb, admin)
    for i in range(12):
        pid = await _page(kb, admin, space, f"Old {i}", BODY)
        _sql(kb, "UPDATE kb_pages SET owner_email = :o, author_email = :a, "
                 "review_at = now() - make_interval(days => :d) WHERE id = CAST(:p AS uuid)",
             o=owner.email, a=admin.email, d=i + 1, p=pid)
    # Published a moment ago by someone else, and not past review.
    fresh = await _page(kb, admin, space, "Fresh", BODY)
    found = await _bound(kb, owner, lambda: feed._knowledge(owner))
    kinds = [s.kind for s in found.signals()]
    assert kinds.count("doc_needs_review") == 10
    assert kinds.count("doc_shared_with_me") >= 1
    assert f"knowledge:page:{fresh}" in [s.signal_id for s in found.signals()]
