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
- KB-9b slice 2, A1: the connector gets one ``source_sync_failed`` row for a
  ``needs_reconnect`` source and for three failed changes or reconcile runs.
  No row when one of the three is ok or stopped, fewer than three finished,
  an error is stale or superseded, the source is disconnected, the viewer is
  another member (even a manager), or the source is in org B. The org
  predicate also holds with row level security off.

The owner ``COALESCE``, the ``interval`` test, the author test and the one
visibility clause are SQL that a hermetic fake accepts unread.
"""

from __future__ import annotations

import contextlib
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
    managers = _ids((await _read(kb, admin)).shared)
    assert managers == [live]
    assert gone not in managers and shelved not in managers


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


# ── KB-9b slice 2: the card of a source that stopped syncing ────────────────

CONNECTOR = "ana@kb9b2-r8.test"


@pytest.fixture
def conns(kb):  # noqa: F811 — a fixture
    """The connections and sources that a test makes, deleted after it."""
    made: list[str] = []
    kb.src_made = made
    yield kb
    with kb.admin_engine.begin() as c:
        # kb_sources and kb_sync_runs go with the connection (ON DELETE CASCADE).
        c.execute(text("DELETE FROM integration_connections "
                       "WHERE member_email LIKE '%@kb9b2-r8.test'"))


def _source(p: Any, org: str, owner: str, state: str = "ready",
            runs: tuple[tuple[str, str, str | None, int], ...] = ()) -> str:
    """One Drive source of *owner*. *runs* are ``(kind, status, error, minutes ago)``."""
    tag = uuid.uuid4().hex[:8]
    with p.admin_engine.begin() as c:
        conn = c.execute(text(
            "INSERT INTO integration_connections (organization_id, provider, owning_app, "
            "scope_kind, member_email) VALUES (CAST(:o AS uuid), 'google_drive', "
            "'knowledge', 'member', :m) RETURNING id"),
            {"o": org, "m": f"{tag}@kb9b2-r8.test"}).scalar()
        sid = str(c.execute(text(
            "INSERT INTO kb_sources (organization_id, connection_id, provider, owner_email, "
            "state, account_email, last_error) VALUES (CAST(:o AS uuid), :c, 'google_drive', "
            ":w, :s, 'drive.owner@gmail.example', 'invalid_grant') RETURNING id"),
            {"o": org, "c": conn, "w": owner, "s": state}).scalar())
        for kind, status, error, ago in runs:
            c.execute(text(
                "INSERT INTO kb_sync_runs (organization_id, source_id, kind, status, error, "
                "started_at, finished_at) VALUES (CAST(:o AS uuid), CAST(:s AS uuid), :k, "
                ":st, :e, now() - make_interval(mins => :a), "
                "CASE WHEN :st = 'running' THEN NULL "
                "ELSE now() - make_interval(mins => :a) END)"),
                {"o": org, "s": sid, "k": kind, "st": status, "e": error, "a": ago})
    return sid


def _failed(*errors: str | None, kind: str = "changes",
            start: int = 10) -> tuple[tuple[str, str, str | None, int], ...]:
    """Failed runs, the newest first, one every ten minutes."""
    return tuple((kind, "failed", e, start + 10 * i) for i, e in enumerate(errors))


async def _cards(kb: Any, user: Any) -> list[dict[str, Any]]:  # noqa: F811
    from gateway.routes.knowledge import my_day

    return (await _bound(kb, user, lambda: my_day.read(
        user, gaps_on=False, limit=16, sources_on=True))).sources


async def test_the_connector_gets_a_card_for_each_failing_source(conns, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    ana = _principal(CONNECTOR, conns.org_a)
    reconnect = _source(conns, conns.org_a, CONNECTOR, "needs_reconnect")
    three = _source(conns, conns.org_a, CONNECTOR, runs=_failed("http_500", "http_500", "x"))
    mixed = _source(conns, conns.org_a, CONNECTOR, runs=(
        ("changes", "failed", "http_500", 10), ("reconcile", "failed", "x", 20),
        ("changes", "failed", "y", 30), ("changes", "ok", None, 40)))
    # A running run, a discovery and a newer good run of ANOTHER source do not
    # count: the three newest FINISHED runs of changes or reconcile decide.
    quiet_tail = _source(conns, conns.org_a, CONNECTOR, runs=(
        ("changes", "running", None, 1), ("discover", "ok", None, 2),
        *_failed("a", "b", "c", start=10)))
    rows = await _cards(conns, ana)
    assert {r["id"] for r in rows} == {reconnect, three, mixed, quiet_tail}
    assert {r["state"] for r in rows} == {"needs_reconnect", "ready"}


async def test_no_card_for_a_source_that_has_not_failed_three_times(conns, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    ana = _principal(CONNECTOR, conns.org_a)
    _source(conns, conns.org_a, CONNECTOR, runs=(
        ("changes", "failed", "x", 10), ("changes", "ok", None, 20), ("changes", "failed", "x", 30)))
    _source(conns, conns.org_a, CONNECTOR, runs=(
        ("changes", "failed", "x", 10), ("changes", "stopped", "rate_limited", 20),
        ("changes", "failed", "x", 30)))
    _source(conns, conns.org_a, CONNECTOR, runs=_failed("x", "y"))
    _source(conns, conns.org_a, CONNECTOR, runs=(
        *_failed("x", "y"), ("changes", "running", None, 1)))
    _source(conns, conns.org_a, CONNECTOR, runs=_failed("stale", "x", "y"))
    _source(conns, conns.org_a, CONNECTOR, runs=_failed("x", "superseded", "y"))
    _source(conns, conns.org_a, CONNECTOR, runs=(
        ("discover", "failed", "x", 10), ("discover", "failed", "x", 20),
        ("discover", "failed", "x", 30)))
    _source(conns, conns.org_a, CONNECTOR)
    assert await _cards(conns, ana) == []


async def test_no_card_for_a_disconnected_source(conns, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    ana = _principal(CONNECTOR, conns.org_a)
    _source(conns, conns.org_a, CONNECTOR, "disconnected", runs=_failed("x", "y", "z"))
    _source(conns, conns.org_a, CONNECTOR, "disconnected")
    assert await _cards(conns, ana) == []


async def test_no_card_for_another_member_even_a_manager(conns, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    admin = _principal(_address("admin"), conns.org_a, CAPABILITY)
    other = _principal(_address("other"), conns.org_a)
    sid = _source(conns, conns.org_a, CONNECTOR, "needs_reconnect")
    three = _source(conns, conns.org_a, CONNECTOR, runs=_failed("x", "y", "z"))
    assert await _cards(conns, admin) == []
    assert await _cards(conns, other) == []
    # The connector's address folds: the case of the stored owner never matters.
    with conns.admin_engine.begin() as c:
        c.execute(text("UPDATE kb_sources SET owner_email = upper(owner_email) "
                       "WHERE id = ANY(CAST(:ids AS uuid[]))"), {"ids": [sid, three]})
    assert {r["id"] for r in await _cards(conns, _principal(CONNECTOR, conns.org_a))} == {
        sid, three}


async def test_no_card_for_a_source_of_org_b(conns, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    # The same address is a member of both orgs. Only the org of the session reads.
    theirs = _source(conns, conns.org_b, CONNECTOR, "needs_reconnect")
    mine = _source(conns, conns.org_a, CONNECTOR, "needs_reconnect")
    assert [r["id"] for r in await _cards(conns, _principal(CONNECTOR, conns.org_a))] == [mine]
    assert [r["id"] for r in await _cards(conns, _principal(CONNECTOR, conns.org_b))] == [theirs]


async def test_the_org_predicate_holds_with_row_level_security_off(conns, app_engine) -> None:  # noqa: F811
    """The query keeps its own org predicate. A session that bypasses RLS (the
    owner role of the box) must still read one org only."""
    from gateway.routes.knowledge import my_day

    from tests.unit._tenant_ladder import tenant_engine_scope

    with conns.admin_engine.connect() as c:
        bypass = c.execute(text(
            "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user")).scalar()
    assert bypass, "the admin role does not bypass RLS, so this test would prove nothing"
    theirs = _source(conns, conns.org_b, CONNECTOR, "needs_reconnect")
    mine = _source(conns, conns.org_a, CONNECTOR, "needs_reconnect")
    ana = _principal(CONNECTOR, conns.org_a)
    from acb_common.db import bind_tenant, clear_tenant, release_tenant

    scope = clear_tenant()
    try:
        async with tenant_engine_scope(conns.admin_engine.url.render_as_string(
                hide_password=False)):
            bind_tenant(ana.organization_id)
            rows = (await my_day.read(
                ana, gaps_on=False, limit=16, sources_on=True)).sources
    finally:
        release_tenant(scope)
    assert [r["id"] for r in rows] == [mine] and theirs not in [r["id"] for r in rows]


async def test_the_card_holds_no_code_no_address_and_no_token(conns, app_engine) -> None:  # noqa: F811
    from gateway.routes.shell import feed

    _assert_non_priv(app_engine)
    ana = _principal(CONNECTOR, conns.org_a)
    _source(conns, conns.org_a, CONNECTOR, "needs_reconnect", runs=_failed("http_500"))
    rows = await _cards(conns, ana)
    assert rows and set(rows[0]) == {"id", "provider", "state", "failed_since"}
    sig = feed._source_signal(rows[0])
    from datetime import UTC, datetime
    from zoneinfo import ZoneInfo

    now = datetime(2026, 10, 25, 9, 0, tzinfo=UTC)
    [card] = feed.build_cards([sig], now, ZoneInfo("UTC"), now.date())
    text_of_card = repr(card)
    for leak in ("invalid_grant", "drive.owner@gmail.example", "gmail", "http_500", "@"):
        assert leak not in text_of_card, leak


async def test_the_provider_reads_in_one_session_with_sources_on(
        conns, app_engine, monkeypatch) -> None:  # noqa: F811
    from acb_common import get_settings
    from gateway.routes.knowledge import core
    from gateway.routes.shell import feed

    _assert_non_priv(app_engine)
    ana = _principal(CONNECTOR, conns.org_a)
    sid = _source(conns, conns.org_a, CONNECTOR, "needs_reconnect")
    opened: list[str] = []
    real = core._tenant_session

    @contextlib.asynccontextmanager
    async def counting():
        opened.append("kb")
        async with real() as db:
            yield db

    monkeypatch.setattr(core, "_tenant_session", counting)
    settings = get_settings()
    monkeypatch.setattr(settings, "knowledge_sources", "*", raising=False)
    found = await _bound(conns, ana, lambda: feed._knowledge(ana))
    assert opened == ["kb"]
    assert [s.signal_id for s in found.signals()] == [f"knowledge:source:{sid}"]
    # A2: with the switch off no source statement runs, and the call still answers.
    monkeypatch.setattr(settings, "knowledge_sources", "", raising=False)
    opened.clear()
    found = await _bound(conns, ana, lambda: feed._knowledge(ana))
    assert found.signals() == [] and opened == ["kb"]


def _add_runs(p: Any, org: str, sid: str,
              runs: tuple[tuple[str, str, str | None, int], ...]) -> None:
    with p.admin_engine.begin() as c:
        for kind, status, error, ago in runs:
            c.execute(text(
                "INSERT INTO kb_sync_runs (organization_id, source_id, kind, status, error, "
                "started_at, finished_at) VALUES (CAST(:o AS uuid), CAST(:s AS uuid), :k, "
                ":st, :e, now() - make_interval(mins => :a), now() - make_interval(mins => :a))"),
                {"o": org, "s": sid, "k": kind, "st": status, "e": error, "a": ago})


async def test_failed_since_holds_inside_an_episode_and_moves_in_the_next(
        conns, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    ana = _principal(CONNECTOR, conns.org_a)
    # The last good run is 100 minutes ago. Three failures follow it.
    sid = _source(conns, conns.org_a, CONNECTOR, runs=(
        ("changes", "ok", None, 100), *_failed("x", "y", "z", start=10)[::-1]))
    [first] = await _cards(conns, ana)
    assert first["id"] == sid and first["failed_since"] is not None
    # A 4th and a 5th failed run land: the same episode, the same value.
    _add_runs(conns, conns.org_a, sid, (("reconcile", "failed", "x", 5),))
    [four] = await _cards(conns, ana)
    _add_runs(conns, conns.org_a, sid, (("changes", "failed", "x", 2),))
    [five] = await _cards(conns, ana)
    assert first["failed_since"] == four["failed_since"] == five["failed_since"]
    # The source recovers on one ok run and fails three times again: a new value.
    _add_runs(conns, conns.org_a, sid, (("changes", "ok", None, 1),))
    assert await _cards(conns, ana) == []
    with conns.admin_engine.begin() as c:
        c.execute(text("INSERT INTO kb_sync_runs (organization_id, source_id, kind, status, "
                       "error, started_at, finished_at) SELECT CAST(:o AS uuid), "
                       "CAST(:s AS uuid), 'changes', 'failed', 'x', now() + make_interval(secs "
                       "=> i), now() + make_interval(secs => i) FROM generate_series(1, 3) i"),
                  {"o": conns.org_a, "s": sid})
    [again] = await _cards(conns, ana)
    assert again["failed_since"] > five["failed_since"]


async def test_failed_since_of_a_reconnect_is_the_state_change(conns, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    ana = _principal(CONNECTOR, conns.org_a)
    sid = _source(conns, conns.org_a, CONNECTOR, "needs_reconnect")
    with conns.admin_engine.begin() as c:
        c.execute(text("UPDATE kb_sources SET updated_at = now() - interval '3 days' "
                       "WHERE id = CAST(:s AS uuid)"), {"s": sid})
    [row] = await _cards(conns, ana)
    with conns.admin_engine.connect() as c:
        stored = c.execute(text("SELECT updated_at FROM kb_sources WHERE id = CAST(:s AS uuid)"),
                           {"s": sid}).scalar()
    assert row["failed_since"] == stored
