"""R8 — Knowledge gaps on a real database (WS-61 KB-9a).

Spec: ``project-docs/specs/knowledge_base.md`` §8.4 door 3, §9 and the KB-9a
row of §17.

What this proves against a REAL Postgres, through the real routes, as the
NON-privileged role of the phase-4 rehearsal (no superuser, no BYPASSRLS),
on a database with the whole ladder, the four generated phases and two
organizations:

- a ``GET /search/company`` with no Knowledge hit writes one ``kb_gaps`` row
  after it answers. A second asker adds one to the count and to ``askers``,
  and the same asker twice counts once;
- a search with a Knowledge hit, a short question and an org off
  ``KNOWLEDGE_GAPS`` write nothing;
- an ask after the 30-day window starts a new window, and ``askers`` keeps
  20 addresses;
- org B never sees a gap of org A, through the routes and under row level
  security;
- a member sees only the gaps the member asked, and a manager sees all;
- dismiss needs ``knowledge:manage``, and "I wrote it" needs a visible, live
  page that the caller can write;
- the My Day source gives a ``kb_gap`` card only at 2 askers or more inside
  30 days, and only to a manager;
- the member purge takes the address out of ``askers``;
- the migration forces row level security and replays.

The upsert with its CASEs over the OLD row, ``array_remove``, the policy and
the LEFT JOIN under the one clause are each SQL that a hermetic fake would
accept unread. ``tests/unit/test_kb_gaps.py`` holds the rest with no database.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    _URL,
    app_engine,
    promoted,
)
from tests.unit.test_kb_pages_r8 import (  # noqa: F401
    CAPABILITY,
    _add_member,
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

QUESTION = "How do we raise a credit note for a customer?"
NORM = "how do we raise a credit note for a customer"


def _gaps_migration() -> Path:
    """The migration that creates ``kb_gaps``, found by content (R1)."""
    found = [
        p for p in sorted(_MIGRATIONS.glob("*.sql"))
        if p.name != "schema.generated.sql"
        and "CREATE TABLE IF NOT EXISTS kb_gaps" in p.read_text(encoding="utf-8")
    ]
    assert len(found) == 1, [p.name for p in found]
    return found[0]


@pytest.fixture
def kb(ns, monkeypatch):  # noqa: F811 — a fixture
    """The KB-1 fixture with gaps on for every org, and room for a cold database."""
    from acb_common import get_settings
    from gateway.retrieval import company

    settings = get_settings()
    monkeypatch.setattr(settings, "knowledge_embed", "", raising=False)
    monkeypatch.setattr(settings, "knowledge_gaps", "*", raising=False)
    monkeypatch.setattr(company, "SOURCE_TIMEOUT_S", 30.0)
    monkeypatch.setattr(company, "TOTAL_BUDGET_S", 60.0)
    yield ns
    with ns.admin_engine.begin() as c:
        c.execute(text("DELETE FROM kb_gaps"))
        c.execute(text("DELETE FROM kb_chunks"))


async def _bound(p: Any, user: Any, coro_fn: Any) -> Any:
    """Run *coro_fn* as a request runs it: the app engine, the user's tenant."""
    from acb_common.db import bind_tenant, clear_tenant, release_tenant

    from tests.unit._tenant_ladder import tenant_engine_scope

    scope = clear_tenant()
    try:
        async with tenant_engine_scope(_app_dsn(p)):
            if user is not None:
                bind_tenant(user.organization_id)
            return await coro_fn()
    finally:
        release_tenant(scope)


async def _ask(p: Any, user: Any, q: str = QUESTION) -> dict[str, Any]:
    """``GET /search/company`` through FastAPI, as *user*.

    httpx's ASGI transport returns only after the app finishes, and the app
    runs its ``BackgroundTasks`` after it sent the answer. So the gap write
    has run when this returns.
    """
    from acb_auth import get_current_user
    from fastapi import FastAPI
    from gateway.retrieval import company

    async def _caller() -> Any:
        from acb_common.db import bind_tenant

        bind_tenant(user.organization_id)
        return user

    app = FastAPI()
    app.include_router(company.router)
    app.dependency_overrides[get_current_user] = _caller

    async def go() -> Any:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://kb9a.test") as client:
            return await client.get("/search/company", params={"q": q})

    res = await _bound(p, None, go)
    assert res.status_code == 200, res.text
    return res.json()


def _rows(p: Any) -> list[Any]:
    with p.admin_engine.connect() as c:
        return c.execute(text(
            "SELECT id, organization_id::text AS org, norm_query, sample_query, askers, "
            "ask_count, status, answered_page_id, dismissed_by FROM kb_gaps "
            "ORDER BY created_at")).all()


async def _page(p: Any, writer: Any, space_id: str | None, title: str, body: str,
                *, publish: bool = True) -> str:
    made: dict[str, Any] = {"title": title, "body_md": body}
    if space_id is not None:
        made["space_id"] = space_id
    page = await _ok(p, writer, "POST", "/knowledge/pages", made, status=201)
    if publish:
        await _ok(p, writer, "POST", f"/knowledge/pages/{page['id']}/publish")
    return page["id"]


def _seed_gap(p: Any, org: str, norm: str, askers: list[str], *, count: int | None = None,
              days_ago: int = 0, status: str = "open") -> str:
    with p.admin_engine.begin() as c:
        return str(c.execute(text(
            "INSERT INTO kb_gaps (organization_id, norm_query, sample_query, askers, "
            "ask_count, first_asked_at, last_asked_at, status) VALUES (CAST(:o AS uuid), "
            ":n, :n, CAST(:a AS text[]), :c, now() - make_interval(days => :d), "
            "now() - make_interval(days => :d), :s) RETURNING id"),
            {"o": org, "n": norm, "a": askers, "c": count or max(1, len(askers)),
             "d": days_ago, "s": status}).scalar())


# ── The write ───────────────────────────────────────────────────────────────

async def test_a_zero_hit_search_writes_one_row_and_counts_each_asker_once(
    kb, app_engine,  # noqa: F811
) -> None:
    _assert_non_priv(app_engine)
    asha = _principal(_address("asha"), kb.org_a)
    bala = _principal(_address("bala"), kb.org_a)

    answer = await _ask(kb, asha)
    assert [h for h in answer["hits"] if h["app"] == "knowledge"] == []
    [row] = _rows(kb)
    assert (row.org, row.norm_query, row.sample_query) == (kb.org_a, NORM, QUESTION)
    assert (row.askers, row.ask_count, row.status) == ([asha.email.lower()], 1, "open")

    # The same asker, another wording of one question: one row, one count.
    await _ask(kb, asha, "how do we  RAISE a credit-note for a customer")
    [row] = _rows(kb)
    assert (row.ask_count, row.askers) == (1, [asha.email.lower()])
    assert row.sample_query == QUESTION, "the first wording stays"

    # A second asker.
    await _ask(kb, bala)
    [row] = _rows(kb)
    assert row.ask_count == 2
    assert row.askers == [asha.email.lower(), bala.email.lower()]


async def test_a_knowledge_hit_writes_nothing(kb, app_engine) -> None:  # noqa: F811
    admin = _principal(_address("admin"), kb.org_a, CAPABILITY)
    space = await _space(kb, admin, "Finance", [{"subject": "org", "level": "read"}])
    await _page(kb, admin, space, "Credit notes",
                "# Credit notes\n\nTo raise a credit note for a customer, open the invoice.")
    member = _principal(_address("m"), kb.org_a)
    answer = await _ask(kb, member)
    assert [h["app"] for h in answer["hits"]] == ["knowledge"], answer
    assert _rows(kb) == []


async def test_a_short_question_and_a_closed_flag_write_nothing(
    kb, app_engine, monkeypatch,  # noqa: F811
) -> None:
    from acb_common import get_settings

    member = _principal(_address("m"), kb.org_a)
    await _ask(kb, member, "credit note")
    await _ask(kb, member, "open https://example.test/credit please")
    assert _rows(kb) == []
    monkeypatch.setattr(get_settings(), "knowledge_gaps", kb.org_b, raising=False)
    await _ask(kb, member)
    assert _rows(kb) == []


async def test_an_old_window_starts_again_and_askers_keeps_twenty(
    kb, app_engine,  # noqa: F811
) -> None:
    carol = _principal(_address("carol"), kb.org_a)
    _seed_gap(kb, kb.org_a, NORM, ["old1@x.test", "old2@x.test"], days_ago=40)
    await _ask(kb, carol)
    [row] = _rows(kb)
    assert (row.ask_count, row.askers) == (1, [carol.email.lower()])

    with kb.admin_engine.begin() as c:
        c.execute(text("DELETE FROM kb_gaps"))
    twenty = [f"m{n}@x.test" for n in range(20)]
    _seed_gap(kb, kb.org_a, NORM, twenty)
    await _ask(kb, carol)
    [row] = _rows(kb)
    assert row.ask_count == 21
    assert row.askers == twenty, "a full list stores no new address"
    # The same full list, an asker it holds: no new count.
    await _ask(kb, _principal("m3@x.test", kb.org_a))
    assert _rows(kb)[0].ask_count == 21


# ── Tenancy ─────────────────────────────────────────────────────────────────

async def test_org_b_never_sees_a_gap_of_org_a(kb, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    asha = _principal(_address("asha"), kb.org_a)
    await _ask(kb, asha)
    [row] = _rows(kb)
    admin_b = _principal(_address("admin-b"), kb.org_b, CAPABILITY)

    listed = await _ok(kb, admin_b, "GET", "/knowledge/gaps")
    assert listed == {"gaps": [], "scope": "org"}
    for verb, body in (("dismiss", None), ("answered", {"page_id": str(row.id)})):
        res = await _call(kb, admin_b, "POST", f"/knowledge/gaps/{row.id}/{verb}", body)
        assert res.status_code == 404, (verb, res.text)
    assert _rows(kb)[0].status == "open"

    # Under row level security, as the app role.
    with app_engine.connect() as c:
        trans = c.begin()
        c.execute(text("SELECT set_config('app.tenant_id', :o, true)"), {"o": kb.org_b})
        assert c.execute(text("SELECT count(*) FROM kb_gaps")).scalar() == 0
        with pytest.raises(DBAPIError):
            c.execute(text(
                "INSERT INTO kb_gaps (organization_id, norm_query, sample_query) "
                "VALUES (CAST(:o AS uuid), 'a b c', 'a b c')"), {"o": kb.org_a})
        trans.rollback()
    with app_engine.connect() as c:
        trans = c.begin()
        c.execute(text("SELECT set_config('app.tenant_id', :o, true)"), {"o": kb.org_a})
        assert c.execute(text("SELECT count(*) FROM kb_gaps")).scalar() == 1
        trans.rollback()


# ── Who sees a gap ──────────────────────────────────────────────────────────

async def test_a_member_sees_their_own_gaps_and_a_manager_sees_all(
    kb, app_engine,  # noqa: F811
) -> None:
    asha = _principal(_address("asha"), kb.org_a)
    bala = _principal(_address("bala"), kb.org_a)
    manager = _principal(_address("lead"), kb.org_a, CAPABILITY)
    await _ask(kb, asha)
    await _ask(kb, bala, "where is the travel expense policy")

    mine = await _ok(kb, asha, "GET", "/knowledge/gaps")
    assert mine["scope"] == "mine"
    assert [g["question"] for g in mine["gaps"]] == [QUESTION]
    assert mine["gaps"][0]["asked_by_me"] is True
    assert all("askers" not in g and "dismissed_by" not in g for g in mine["gaps"])

    every = await _ok(kb, manager, "GET", "/knowledge/gaps")
    assert every["scope"] == "org"
    assert {g["question"] for g in every["gaps"]} == {
        QUESTION, "where is the travel expense policy"}
    assert all(g["asked_by_me"] is False and "askers" not in g for g in every["gaps"])


# ── Dismiss and "I wrote it" ────────────────────────────────────────────────

async def test_dismiss_needs_manage(kb, app_engine) -> None:  # noqa: F811
    asha = _principal(_address("asha"), kb.org_a)
    manager = _principal(_address("lead"), kb.org_a, CAPABILITY)
    await _ask(kb, asha)
    [row] = _rows(kb)

    refused = await _call(kb, asha, "POST", f"/knowledge/gaps/{row.id}/dismiss")
    assert refused.status_code == 403
    assert _rows(kb)[0].status == "open"

    done = await _ok(kb, manager, "POST", f"/knowledge/gaps/{row.id}/dismiss")
    assert done["status"] == "dismissed" and done["dismissed_at"]
    assert done["dismissed_by"] == manager.email.lower()
    again = await _call(kb, manager, "POST", f"/knowledge/gaps/{row.id}/dismiss")
    assert again.status_code == 409
    assert (await _ok(kb, manager, "GET", "/knowledge/gaps"))["gaps"] == []
    closed = await _ok(kb, manager, "GET", "/knowledge/gaps?status=dismissed")
    assert [g["id"] for g in closed["gaps"]] == [str(row.id)]


async def test_answered_needs_a_visible_live_page_the_caller_can_write(
    kb, app_engine,  # noqa: F811
) -> None:
    asha_email = _address("asha")
    _add_member(kb, kb.org_a, asha_email)
    asha = _principal(asha_email, kb.org_a)
    manager = _principal(_address("lead"), kb.org_a, CAPABILITY)
    bala = _principal(_address("bala"), kb.org_a)
    await _ask(kb, asha)
    [row] = _rows(kb)
    gap = str(row.id)

    readable = await _space(kb, manager, "Handbook", [{"subject": "org", "level": "read"}])
    writable = await _space(kb, manager, "Finance", [{"subject": asha_email, "level": "write"}])
    hidden = await _space(kb, manager, "Board", [])
    read_only = await _page(kb, manager, readable, "Read only", "# Read only")
    hidden_page = await _page(kb, manager, hidden, "Hidden", "# Hidden")
    archived = await _page(kb, manager, writable, "Old", "# Old")
    await _ok(kb, manager, "POST", f"/knowledge/pages/{archived}/archive")
    # A draft of the asker's own, in a space the asker writes.
    draft = await _page(kb, asha, writable, "Credit notes", "# Credit notes", publish=False)

    async def status(user: Any, page: str, gap_id: str = gap) -> int:
        res = await _call(kb, user, "POST", f"/knowledge/gaps/{gap_id}/answered",
                          {"page_id": page})
        return res.status_code

    assert await status(asha, read_only) == 403, "a reader cannot answer with it"
    assert await status(asha, hidden_page) == 404, "a hidden page is a 404"
    assert await status(asha, archived) == 404, "an archived page the asker cannot see"
    assert await status(manager, archived) == 409, "an archived page"
    assert await status(asha, "not-a-page") == 404
    assert await status(bala, draft) == 404, "a member who did not ask sees no gap"
    assert _rows(kb)[0].status == "open"

    done = await _ok(kb, asha, "POST", f"/knowledge/gaps/{gap}/answered", {"page_id": draft})
    assert done["status"] == "answered"
    assert done["answered_page"] == {"id": draft, "title": "Credit notes"}
    assert str(_rows(kb)[0].answered_page_id) == draft
    # The manager reads the gap, and does not see the asker's draft title.
    every = await _ok(kb, manager, "GET", "/knowledge/gaps?status=answered")
    assert [g["answered_page"] for g in every["gaps"]] == [None]
    assert await status(asha, draft) == 409, "an answered gap is closed"


# ── My Day ──────────────────────────────────────────────────────────────────

async def _cards(p: Any, user: Any) -> list[Any]:
    from gateway.routes.shell import feed

    found = await _bound(p, user, lambda: feed._knowledge(user))
    return found.signals()


async def test_the_card_needs_two_askers_inside_thirty_days_and_a_manager(
    kb, app_engine,  # noqa: F811
) -> None:
    _assert_non_priv(app_engine)
    from gateway.routes.knowledge import gaps

    manager = _principal(_address("lead"), kb.org_a, CAPABILITY)
    member = _principal(_address("m"), kb.org_a)
    one = _seed_gap(kb, kb.org_a, "one asker only here", ["a@x.test"])
    two = _seed_gap(kb, kb.org_a, "two askers ask this", ["a@x.test", "b@x.test"])
    old = _seed_gap(kb, kb.org_a, "two askers long ago", ["a@x.test", "b@x.test"],
                    days_ago=31)
    gone = _seed_gap(kb, kb.org_a, "two askers but closed", ["a@x.test", "b@x.test"],
                     status="dismissed")
    _seed_gap(kb, kb.org_b, "two askers in org b", ["a@x.test", "b@x.test"])

    cards = await _cards(kb, manager)
    assert [s.signal_id for s in cards] == [f"knowledge:gap:{two}"]
    assert cards[0].facts["askers"] == 2
    assert all(f"knowledge:gap:{g}" not in {s.signal_id for s in cards}
               for g in (one, old, gone))
    # A member without manage gets no row, even through the read itself.
    assert await _bound(kb, member, lambda: gaps.my_day_rows(member, 16)) == []


async def test_the_second_asker_makes_the_card(kb, app_engine) -> None:  # noqa: F811
    manager = _principal(_address("lead"), kb.org_a, CAPABILITY)
    await _ask(kb, _principal(_address("asha"), kb.org_a))
    assert await _cards(kb, manager) == []
    await _ask(kb, _principal(_address("bala"), kb.org_a))
    [card] = await _cards(kb, manager)
    assert card.kind == "kb_gap" and card.title == QUESTION


# ── The member purge ────────────────────────────────────────────────────────

async def test_the_purge_takes_the_address_out_of_askers(kb, app_engine) -> None:  # noqa: F811
    from gateway.routes.knowledge import core, gaps

    gone = "gone@x.test"
    kept = "kept@x.test"
    _seed_gap(kb, kb.org_a, "a question asked twice", [gone, kept])
    other_org = _seed_gap(kb, kb.org_b, "a question asked twice", [gone])
    with kb.admin_engine.begin() as c:
        c.execute(text("UPDATE kb_gaps SET dismissed_by = :g WHERE organization_id = "
                       "CAST(:o AS uuid)"), {"g": gone.upper(), "o": kb.org_a})
    user = _principal("lead@x.test", kb.org_a, CAPABILITY)

    async def scrub() -> None:
        async with core._tenant_session(kb.org_a) as db:
            await gaps.scrub_member(db, kb.org_a, gone)

    await _bound(kb, user, scrub)
    rows = {(r.org, str(r.id)): r for r in _rows(kb)}
    [mine] = [r for (org, _), r in rows.items() if org == kb.org_a]
    assert (mine.askers, mine.ask_count, mine.dismissed_by) == ([kept], 2, None)
    assert rows[(kb.org_b, other_org)].askers == [gone], "another org is untouched"


# ── The migration ───────────────────────────────────────────────────────────

def test_the_table_forces_row_level_security_and_the_migration_replays(kb) -> None:
    sql = _gaps_migration().read_text(encoding="utf-8")
    for _ in range(2):
        with kb.admin_engine.begin() as c, c.connection.dbapi_connection.cursor() as cur:
            cur.execute(sql)
    with kb.admin_engine.connect() as c:
        forced = c.execute(text(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
            "WHERE oid = 'kb_gaps'::regclass")).first()
        policies = c.execute(text(
            "SELECT count(*) FROM pg_policies WHERE tablename = 'kb_gaps'")).scalar()
        unique = c.execute(text(
            "SELECT count(*) FROM pg_constraint WHERE conname = 'kb_gaps_org_norm_key'")).scalar()
    assert tuple(forced) == (True, True)
    assert policies == 1
    assert unique == 1
