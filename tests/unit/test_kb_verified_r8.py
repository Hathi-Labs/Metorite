"""R8 — verified pages on a real database (WS-61 KB-AI-4 PR A).

Spec: ``project-docs/specs/knowledge_base.md`` §22.3.

What this proves against a REAL Postgres, through the real ``/knowledge``
router, as the NON-privileged role of the phase-4 rehearsal:

- with ``reviewed`` on, an expired page ranks below a current owned page, and
  the hit carries ``review_at``;
- ``verified_only`` returns the current owned page and nothing else: no
  expired, no unowned, no draft, no page of a space the member cannot read,
  no page of another organization;
- ``GET /knowledge/maintenance`` names a page that is expired, unowned and
  stale ONCE, with three reasons, for a manager only, and never a ``me`` page;
- ``POST /knowledge/pages/{id}/verify`` moves the review date, claims an
  empty owner, and writes no version and no ``updated_at``;
- a publish through the route and through the proposals accept both set the
  owner to the author and the review date to now plus the interval;
- migration 302 replays.

The ``COALESCE`` in the publish SET, the ``make_interval`` call, the partial
index and the one SELECT of the list are each SQL that a hermetic fake would
accept unread.
"""

from __future__ import annotations

from pathlib import Path
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
    _call,
    _ok,
    _principal,
    _space,
    _versions_and_stamp,
    ns,
)
from tests.unit.test_kb_search_r8 import (  # noqa: F401
    _bound,
    _found,
    _page,
    _search,
    kb,
)

# ⚠️ Every test here needs the real database. Without this gate the suite
# ERRORS where no ladder URL is set (the deploy run), and the deploy stops.
pytestmark = _DB_GATE

_MIGRATIONS = Path(__file__).resolve().parents[2] / "infra" / "postgres"

BODY = "The warranty covers the nozzle for twelve months."


def _sql(world: Any, sql: str, **params: Any) -> None:
    with world.admin_engine.begin() as c:
        c.execute(text(sql), params)


def _scalar(world: Any, sql: str, **params: Any) -> Any:
    with world.admin_engine.connect() as c:
        return c.execute(text(sql), params).scalar()


def _days_ahead(p: Any, page_id: str) -> float:
    return float(_scalar(
        p, "SELECT extract(epoch FROM (review_at - now())) / 86400 "
           "FROM kb_pages WHERE id = CAST(:p AS uuid)", p=page_id))


async def _find(p: Any, user: Any, **kw: Any) -> list[str]:
    from gateway.routes.knowledge import search as kb_search

    answer = await _bound(p, user, lambda: kb_search.search_pages(
        q="warranty nozzle", space=None, k=20, hybrid=False, user=user, **kw))
    return _found(answer)


async def _hits(p: Any, user: Any, **kw: Any) -> list[dict[str, Any]]:
    from gateway.routes.knowledge import search as kb_search

    answer = await _bound(p, user, lambda: kb_search.search_pages(
        q="warranty nozzle", space=None, k=20, hybrid=False, user=user, **kw))
    return answer["hits"]


async def _three(kb: Any) -> dict[str, Any]:  # noqa: F811
    """Current+owned, expired+owned, current+unowned: the same text."""
    admin = _principal(_address("admin"), kb.org_a, CAPABILITY)
    space = await _space(kb, admin, "Policies", [{"subject": "org", "level": "read"}])
    current = await _page(kb, admin, space, "Current", BODY)
    expired = await _page(kb, admin, space, "Expired", BODY)
    unowned = await _page(kb, admin, space, "Unowned", BODY)
    _sql(kb, "UPDATE kb_pages SET review_at = now() - interval '2 days' "
             "WHERE id = CAST(:p AS uuid)", p=expired)
    _sql(kb, "UPDATE kb_pages SET owner_email = NULL WHERE id = CAST(:p AS uuid)", p=unowned)
    return {"admin": admin, "space": space, "current": current, "expired": expired,
            "unowned": unowned, "member": _principal(_address("m"), kb.org_a)}


# ── Acceptance 1: an expired page ranks lower and carries the date ──────────

async def test_an_expired_page_ranks_below_a_current_owned_page(
        kb, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    w = await _three(kb)
    hits = await _hits(kb, w["member"], reviewed=True)
    order = [h["page_id"] for h in hits]
    assert order.index(w["current"]) < order.index(w["expired"])
    by_id = {h["page_id"]: h for h in hits}
    assert by_id[w["expired"]]["review_at"] is not None
    assert by_id[w["current"]]["owner_email"] is not None
    assert by_id[w["unowned"]]["owner_email"] is None


# ── Acceptance 2: verified only returns no expired page ─────────────────────

async def test_verified_only_returns_the_current_owned_page_only(
        kb, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    w = await _three(kb)
    admin, member = w["admin"], w["member"]
    # A draft, a page in a closed space, and a page of org B, all with the text.
    await _page(kb, admin, w["space"], "Draft", BODY, publish=False)
    closed = await _space(kb, admin, "Closed", [{"subject": admin.email, "level": "write"}])
    hidden = await _page(kb, admin, closed, "Hidden", BODY)
    admin_b = _principal(_address("admin-b"), kb.org_b, CAPABILITY)
    space_b = await _space(kb, admin_b, "B space", [{"subject": "org", "level": "read"}])
    theirs = await _page(kb, admin_b, space_b, "Theirs", BODY)

    everything = set(await _find(kb, member, reviewed=True))
    assert {w["current"], w["expired"], w["unowned"]} <= everything
    only = await _find(kb, member, reviewed=True, verified_only=True)
    assert only == [w["current"]]
    # The admin reaches the closed space, and the member does not.
    assert set(await _find(kb, admin, reviewed=True, verified_only=True)) == {
        w["current"], hidden}
    assert theirs not in await _find(kb, admin, reviewed=True, verified_only=True)
    member_b = _principal(_address("mb"), kb.org_b)
    assert await _find(kb, member_b, reviewed=True, verified_only=True) == [theirs]
    # Off, nothing changes for the plain search.
    assert set(await _find(kb, member)) == everything


# ── Acceptance 3: the list names each page once ─────────────────────────────

async def test_the_maintenance_list_names_each_page_once(kb, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    admin = _principal(_address("admin"), kb.org_a, CAPABILITY)
    member = _principal(_address("m"), kb.org_a)
    space = await _space(kb, admin, "Policies", [{"subject": "org", "level": "read"}])
    allthree = await _page(kb, admin, space, "All three", BODY)
    plain_unowned = await _page(kb, admin, space, "Unowned only", BODY)
    clean = await _page(kb, admin, space, "Clean", BODY)
    _sql(kb, "UPDATE kb_pages SET review_at = now() - interval '2 days', "
             "owner_email = NULL, updated_at = now() - interval '400 days' "
             "WHERE id = CAST(:p AS uuid)", p=allthree)
    _sql(kb, "UPDATE kb_pages SET owner_email = NULL WHERE id = CAST(:p AS uuid)",
         p=plain_unowned)
    # A published page in the manager's own me space never appears.
    mine = await _ok(kb, admin, "POST", "/knowledge/pages",
                     {"title": "Mine", "body_md": "private"}, status=201)
    await _ok(kb, admin, "POST", f"/knowledge/pages/{mine['id']}/publish")
    _sql(kb, "UPDATE kb_pages SET owner_email = NULL WHERE id = CAST(:p AS uuid)", p=mine["id"])

    assert (await _call(kb, member, "GET", "/knowledge/maintenance")).status_code == 403
    listed = await _ok(kb, admin, "GET", "/knowledge/maintenance")
    ids = [r["id"] for r in listed["pages"]]
    assert ids == [allthree, plain_unowned]
    assert len(ids) == len(set(ids))
    assert listed["pages"][0]["reasons"] == ["expired", "unowned", "stale"]
    assert listed["pages"][1]["reasons"] == ["unowned"]
    assert listed["counts"] == {"expired": 1, "unowned": 2, "stale": 1}
    assert listed["scope"] == "org"
    assert clean not in ids and mine["id"] not in ids
    only = await _ok(kb, admin, "GET", "/knowledge/maintenance?reason=expired")
    assert [r["id"] for r in only["pages"]] == [allthree]
    cut = await _ok(kb, admin, "GET", "/knowledge/maintenance?limit=1")
    assert [r["id"] for r in cut["pages"]] == [allthree]
    assert cut["counts"]["unowned"] == 2


# ── The verify act ──────────────────────────────────────────────────────────

async def test_verify_moves_the_date_and_claims_the_owner_without_a_version(
        kb, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    admin = _principal(_address("admin"), kb.org_a, CAPABILITY)
    writer_email = _address("writer")
    writer = _principal(writer_email, kb.org_a)
    reader = _principal(_address("reader"), kb.org_a)
    space = await _space(kb, admin, "Policies", [
        {"subject": writer_email, "level": "write"}, {"subject": "org", "level": "read"}])
    page = await _page(kb, admin, space, "Warranty", BODY)
    care = await _ok(kb, writer, "PATCH", f"/knowledge/pages/{page}",
                     {"review_every_days": 30})
    assert care["review_every_days"] == 30
    _sql(kb, "UPDATE kb_pages SET owner_email = NULL, "
             "review_at = now() - interval '5 days' WHERE id = CAST(:p AS uuid)", p=page)
    before = _versions_and_stamp(kb, page)

    assert (await _call(kb, reader, "POST", f"/knowledge/pages/{page}/verify")
            ).status_code == 403
    done = await _ok(kb, writer, "POST", f"/knowledge/pages/{page}/verify")
    assert done["owner_email"] == writer_email
    assert 29.5 < _days_ahead(kb, page) < 30.5
    assert _versions_and_stamp(kb, page) == before
    # A second verify keeps the owner.
    again = await _ok(kb, admin, "POST", f"/knowledge/pages/{page}/verify")
    assert again["owner_email"] == writer_email
    assert _versions_and_stamp(kb, page) == before
    assert await _find(kb, reader, reviewed=True, verified_only=True) == [page]

    draft = await _page(kb, admin, space, "Draft", "x", publish=False)
    assert (await _call(kb, admin, "POST", f"/knowledge/pages/{draft}/verify")
            ).status_code == 409


async def test_an_owner_or_interval_patch_writes_no_version(kb, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    admin = _principal(_address("admin"), kb.org_a, CAPABILITY)
    space = await _space(kb, admin, "Policies", [{"subject": "org", "level": "read"}])
    page = await _page(kb, admin, space, "Warranty", BODY)
    before = _versions_and_stamp(kb, page)
    owner = _address("owner")
    out = await _ok(kb, admin, "PATCH", f"/knowledge/pages/{page}",
                    {"owner_email": owner.upper(), "review_every_days": 90})
    assert (out["owner_email"], out["review_every_days"]) == (owner, 90)
    assert _versions_and_stamp(kb, page) == before
    cleared = await _ok(kb, admin, "PATCH", f"/knowledge/pages/{page}",
                        {"owner_email": None, "review_every_days": None})
    assert cleared["owner_email"] is None and cleared["review_every_days"] is None
    assert (await _call(kb, admin, "PATCH", f"/knowledge/pages/{page}",
                        {"review_every_days": 0})).status_code == 422
    # Mixed with a title it joins the versioned write.
    await _ok(kb, admin, "PATCH", f"/knowledge/pages/{page}",
              {"title": "Renamed", "review_every_days": 60})
    assert _versions_and_stamp(kb, page)[0] == before[0] + 1


# ── Both publish paths ──────────────────────────────────────────────────────

async def test_a_publish_sets_the_owner_and_the_date_on_both_paths(
        kb, app_engine, monkeypatch) -> None:  # noqa: F811
    from acb_common import get_settings

    _assert_non_priv(app_engine)
    monkeypatch.setattr(get_settings(), "knowledge_proposals", True, raising=False)
    admin = _principal(_address("admin"), kb.org_a, CAPABILITY)
    author_email = _address("author")
    author = _principal(author_email, kb.org_a)
    space = await _space(kb, admin, "Wiki", [{"subject": author_email, "level": "write"}])

    # The route, default interval.
    page = await _page(kb, author, space, "Plain", BODY)
    assert _scalar(kb, "SELECT owner_email FROM kb_pages WHERE id = CAST(:p AS uuid)",
                   p=page) == author_email
    assert 179.5 < _days_ahead(kb, page) < 180.5

    # The route, a set interval.
    draft = await _page(kb, author, space, "Short", BODY, publish=False)
    await _ok(kb, author, "PATCH", f"/knowledge/pages/{draft}", {"review_every_days": 30})
    await _ok(kb, author, "POST", f"/knowledge/pages/{draft}/publish")
    assert 29.5 < _days_ahead(kb, draft) < 30.5

    # The proposals accept.
    made = await _ok(kb, author, "POST", "/knowledge/proposals",
                     {"kind": "page", "space_id": space, "title": "Leave", "body_md": "ten days",
                      "summary": "From the chat."}, status=201,
                     headers={"X-Actor-Via": "chat:knowledge"})
    await _ok(kb, author, "PATCH", f"/knowledge/pages/{made['id']}",
              {"review_every_days": 45})
    await _ok(kb, author, "POST", f"/knowledge/proposals/{made['id']}/accept")
    assert _scalar(kb, "SELECT owner_email FROM kb_pages WHERE id = CAST(:p AS uuid)",
                   p=made["id"]) == author_email
    assert 44.5 < _days_ahead(kb, made["id"]) < 45.5


# ── Migration 302 ───────────────────────────────────────────────────────────

def _migration() -> Path:
    """Migration 302, found by content (R1)."""
    found = [
        p for p in sorted(_MIGRATIONS.glob("*.sql"))
        if p.name != "schema.generated.sql"
        and "ADD COLUMN IF NOT EXISTS review_every_days" in p.read_text(encoding="utf-8")
    ]
    assert len(found) == 1, [p.name for p in found]
    return found[0]


def test_the_migration_replays_with_no_change(kb) -> None:  # noqa: F811
    sql = _migration().read_text(encoding="utf-8")
    for _ in range(2):
        with kb.admin_engine.begin() as c, c.connection.dbapi_connection.cursor() as cur:
            cur.execute(sql)
    cols = _scalar(kb, "SELECT count(*) FROM information_schema.columns "
                       "WHERE table_name = 'kb_pages' "
                       "AND column_name IN ('owner_email', 'review_every_days')")
    assert cols == 2
    indexes = _scalar(kb, "SELECT count(*) FROM pg_indexes WHERE tablename = 'kb_pages' "
                          "AND indexname IN ('idx_kb_pages_owner', 'idx_kb_pages_review')")
    assert indexes == 2
