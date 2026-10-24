"""R8 — the page icon, the favourites and the home lists (WS-61 KB-2f).

Spec: ``project-docs/specs/knowledge_base.md`` §21.7, the KB-2f row.

What this proves against a REAL Postgres, through the real ``/knowledge``
router, as the NON-privileged role of the phase-4 rehearsal (no superuser,
no BYPASSRLS), on a database with the whole ladder, the four generated
phases and two organizations:

- a change of only the icon writes no version, keeps ``updated_at`` and
  leaves the index as it was. The icon then shows in the tree, the page,
  the search hits, the recent list and the favourites;
- an icon with a title writes one version, a bad icon is a 422, a reader
  gets 403, and the CHECK of migration 291 refuses 17 characters;
- "Recently updated" shows the live pages the member sees, newest first,
  with a keyset cursor and no repeat, and ``mine`` keeps the member's own;
- a page that becomes hidden drops out of the favourites and of the recent
  list, and a star of a page the member cannot see is a 404;
- a second org sees none of the favourites, through the routes and under
  row level security;
- the member of a favourite is the session's, and the migration replays.

``tests/unit/test_kb_visibility_one_seam.py`` holds, with no database, that
each read of ``kb_favorites`` joins ``kb_pages`` under the one clause.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlencode

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

ICON = "\U0001f4d8"  # a blue book


def _home_migration() -> Path:
    """The migration that creates ``kb_favorites``, found by content (R1)."""
    found = [
        p for p in sorted(_MIGRATIONS.glob("*.sql"))
        if p.name != "schema.generated.sql"
        and "CREATE TABLE IF NOT EXISTS kb_favorites" in p.read_text(encoding="utf-8")
    ]
    assert len(found) == 1, [p.name for p in found]
    return found[0]


@pytest.fixture
def kb(ns, monkeypatch):  # noqa: F811 — a fixture
    """The KB-1 fixture with the embed door off. The chunks go first."""
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "knowledge_embed", "", raising=False)
    yield ns
    with ns.admin_engine.begin() as c:
        c.execute(text("DELETE FROM kb_favorites"))
        c.execute(text("DELETE FROM kb_chunks"))


async def _page(p: Any, writer: Any, space_id: str | None, title: str, body: str = "",
                *, publish: bool = True) -> str:
    made: dict[str, Any] = {"title": title, "body_md": body or f"# {title}\n\nText of {title}."}
    if space_id is not None:
        made["space_id"] = space_id
    page = await _ok(p, writer, "POST", "/knowledge/pages", made, status=201)
    if publish:
        await _ok(p, writer, "POST", f"/knowledge/pages/{page['id']}/publish")
    return page["id"]


def _row(p: Any, page_id: str) -> Any:
    with p.admin_engine.connect() as c:
        return c.execute(text(
            "SELECT updated_at, icon, (SELECT count(*) FROM kb_page_versions v "
            "WHERE v.page_id = kb_pages.id) AS versions, "
            "(SELECT array_agg(content_sha ORDER BY content_sha) FROM kb_chunks k "
            "WHERE k.source_id = kb_pages.id) AS shas "
            "FROM kb_pages WHERE id = CAST(:p AS uuid)"), {"p": page_id}).first()


async def _recent(p: Any, user: Any, **params: Any) -> dict[str, Any]:
    query = f"?{urlencode(params)}" if params else ""
    return await _ok(p, user, "GET", f"/knowledge/recent{query}")


async def _favorites(p: Any, user: Any) -> list[str]:
    return [row["id"] for row in (await _ok(p, user, "GET", "/knowledge/favorites"))["pages"]]


def _ids(answer: dict[str, Any]) -> list[str]:
    return [row["id"] for row in answer["pages"]]


# ── The icon ────────────────────────────────────────────────────────────────

async def test_an_icon_alone_writes_no_version_and_shows_in_five_places(
    kb, app_engine,  # noqa: F811
) -> None:
    _assert_non_priv(app_engine)
    admin = _principal(_address("admin"), kb.org_a, CAPABILITY)
    reader = _principal(_address("reader"), kb.org_a)
    space = await _space(kb, admin, "Handbook", [{"subject": "org", "level": "read"}])
    page = await _page(kb, admin, space, "Refund policy", "# Refund policy\n\nThe refund window is 30 days.")
    before = _row(kb, page)

    answer = await _ok(kb, admin, "PATCH", f"/knowledge/pages/{page}", {"icon": f" {ICON} "})
    assert answer["icon"] == ICON
    assert "version" not in answer
    after = _row(kb, page)
    assert after.icon == ICON
    assert after.versions == before.versions
    assert after.updated_at == before.updated_at
    assert after.shas == before.shas and after.shas

    # 1. The tree.
    tree = await _ok(kb, reader, "GET", f"/knowledge/pages?space={space}")
    assert [row["icon"] for row in tree["pages"] if row["id"] == page] == [ICON]
    # 2. The page, its title and its breadcrumb.
    assert (await _ok(kb, reader, "GET", f"/knowledge/pages/{page}"))["icon"] == ICON
    # 3. The search hits.
    hits = (await _ok(kb, reader, "GET",
                      "/knowledge/search?q=refund+window&hybrid=false"))["hits"]
    assert [h["icon"] for h in hits if h["page_id"] == page] == [ICON]
    # 4. The recent list.
    assert [r["icon"] for r in (await _recent(kb, reader))["pages"] if r["id"] == page] == [ICON]
    # 5. The favourites.
    await _ok(kb, reader, "POST", f"/knowledge/pages/{page}/favorite")
    favorites = (await _ok(kb, reader, "GET", "/knowledge/favorites"))["pages"]
    assert [(r["id"], r["icon"], r["space_name"]) for r in favorites] == [(page, ICON, "Handbook")]

    # An empty icon clears it, again with no version.
    cleared = await _ok(kb, admin, "PATCH", f"/knowledge/pages/{page}", {"icon": ""})
    assert cleared["icon"] is None
    assert _row(kb, page).versions == before.versions


async def test_an_icon_with_a_title_writes_one_version(kb, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    admin = _principal(_address("admin"), kb.org_a, CAPABILITY)
    space = await _space(kb, admin, "Ops", [{"subject": "org", "level": "read"}])
    page = await _page(kb, admin, space, "Runbook")
    before = _row(kb, page)
    answer = await _ok(kb, admin, "PATCH", f"/knowledge/pages/{page}",
                       {"icon": ICON, "title": "Runbook, 2026"})
    after = _row(kb, page)
    assert answer["icon"] == ICON and answer["title"] == "Runbook, 2026"
    assert after.versions == before.versions + 1
    assert after.updated_at > before.updated_at


@pytest.mark.parametrize("bad", ["Hello", "a", "\U0001f600" * 17, "\U0001f600 \U0001f600", "<b>"])
async def test_a_bad_icon_is_refused(kb, app_engine, bad: str) -> None:  # noqa: F811
    admin = _principal(_address("admin"), kb.org_a, CAPABILITY)
    space = await _space(kb, admin, "Ops", [{"subject": "org", "level": "read"}])
    page = await _page(kb, admin, space, "Runbook", publish=False)
    response = await _call(kb, admin, "PATCH", f"/knowledge/pages/{page}", {"icon": bad})
    assert response.status_code == 422, response.text
    assert _row(kb, page).icon is None


async def test_a_keycap_and_a_flag_are_icons(kb, app_engine) -> None:  # noqa: F811
    admin = _principal(_address("admin"), kb.org_a, CAPABILITY)
    space = await _space(kb, admin, "Ops", [{"subject": "org", "level": "read"}])
    page = await _page(kb, admin, space, "Runbook", publish=False)
    for good in ("1️⃣", "\U0001f1ee\U0001f1f3", "\U0001f468‍\U0001f4bb"):
        answer = await _ok(kb, admin, "PATCH", f"/knowledge/pages/{page}", {"icon": good})
        assert answer["icon"] == good


async def test_a_reader_cannot_set_an_icon(kb, app_engine) -> None:  # noqa: F811
    admin = _principal(_address("admin"), kb.org_a, CAPABILITY)
    reader = _principal(_address("reader"), kb.org_a)
    space = await _space(kb, admin, "Handbook", [{"subject": "org", "level": "read"}])
    page = await _page(kb, admin, space, "Policy")
    response = await _call(kb, reader, "PATCH", f"/knowledge/pages/{page}", {"icon": ICON})
    assert response.status_code == 403
    assert _row(kb, page).icon is None


async def test_the_check_refuses_a_long_icon(kb, app_engine) -> None:  # noqa: F811
    admin = _principal(_address("admin"), kb.org_a, CAPABILITY)
    page = await _page(kb, admin, None, "Mine", publish=False)
    with kb.admin_engine.connect() as c:
        trans = c.begin()
        c.execute(text("UPDATE kb_pages SET icon = :i WHERE id = CAST(:p AS uuid)"),
                  {"i": "x" * 16, "p": page})
        with pytest.raises(DBAPIError):
            c.execute(text("UPDATE kb_pages SET icon = :i WHERE id = CAST(:p AS uuid)"),
                      {"i": "x" * 17, "p": page})
        trans.rollback()


# ── Recently updated ────────────────────────────────────────────────────────

async def test_recent_lists_the_visible_live_pages_with_a_cursor(kb, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    admin = _principal(_address("admin"), kb.org_a, CAPABILITY)
    member_email = _address("member")
    _add_member(kb, kb.org_a, member_email)
    member = _principal(member_email, kb.org_a)
    space = await _space(kb, admin, "Handbook", [{"subject": "org", "level": "read"}])
    hidden = await _space(kb, admin, "Board", [])

    first = await _page(kb, admin, space, "First")
    second = await _page(kb, admin, space, "Second")
    third = await _page(kb, admin, space, "Third")
    await _page(kb, admin, space, "Admin draft", publish=False)
    await _page(kb, admin, hidden, "Board minutes")
    archived = await _page(kb, admin, space, "Old")
    await _ok(kb, admin, "POST", f"/knowledge/pages/{archived}/archive")
    own = await _page(kb, member, None, "My draft", publish=False)
    # A change brings the first page to the top.
    await _ok(kb, admin, "PATCH", f"/knowledge/pages/{first}", {"body_md": "# First\n\nNew."})

    page_one = await _recent(kb, member, limit=2)
    assert _ids(page_one) == [first, own]
    assert page_one["next_cursor"]
    page_two = await _recent(kb, member, limit=2, cursor=page_one["next_cursor"])
    # The last full page knows that no row follows it.
    assert _ids(page_two) == [third, second] and page_two["next_cursor"] is None
    whole = await _recent(kb, member, limit=50)
    assert _ids(whole) == [first, own, third, second] and whole["next_cursor"] is None
    assert {r["space_name"] for r in whole["pages"]} == {"Handbook", "My notes"}
    assert all("body_md" not in r for r in whole["pages"])

    assert _ids(await _recent(kb, member, mine="true")) == [own]
    # An icon alone does not move a page up the list.
    await _ok(kb, admin, "PATCH", f"/knowledge/pages/{second}", {"icon": ICON})
    assert _ids(await _recent(kb, member, limit=50)) == [first, own, third, second]

    bad = await _call(kb, member, "GET", "/knowledge/recent?cursor=not-a-cursor")
    assert bad.status_code == 422


# ── Favourites ──────────────────────────────────────────────────────────────

async def test_a_hidden_page_drops_out_of_the_favorites_and_the_home(kb, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    admin = _principal(_address("admin"), kb.org_a, CAPABILITY)
    member_email = _address("member")
    _add_member(kb, kb.org_a, member_email)
    member = _principal(member_email, kb.org_a)
    space = await _space(kb, admin, "Sales", [{"subject": member_email, "level": "read"}])
    page = await _page(kb, admin, space, "Price list")
    other = await _page(kb, admin, space, "Discounts")

    await _ok(kb, member, "POST", f"/knowledge/pages/{page}/favorite")
    await _ok(kb, member, "POST", f"/knowledge/pages/{page}/favorite")  # a second star
    await _ok(kb, member, "POST", f"/knowledge/pages/{other}/favorite")
    assert await _favorites(kb, member) == [other, page]

    # Archive: the page leaves the list, and comes back with a restore.
    await _ok(kb, admin, "POST", f"/knowledge/pages/{other}/archive")
    assert await _favorites(kb, member) == [page]
    assert other not in _ids(await _recent(kb, member, limit=50))
    await _ok(kb, admin, "POST", f"/knowledge/pages/{other}/restore")
    assert await _favorites(kb, member) == [other, page]

    # The grant goes: every favourite and every recent row of the space goes.
    await _ok(kb, admin, "PUT", f"/knowledge/spaces/{space}/grants", {"grants": []})
    assert await _favorites(kb, member) == []
    assert _ids(await _recent(kb, member, limit=50)) == []
    refused = await _call(kb, member, "POST", f"/knowledge/pages/{page}/favorite")
    assert refused.status_code == 404
    # The rows wait. A new grant brings the favourites back.
    await _ok(kb, admin, "PUT", f"/knowledge/spaces/{space}/grants",
              {"grants": [{"subject": member_email, "level": "read"}]})
    assert await _favorites(kb, member) == [other, page]

    # Unstar, twice: the second answers 204 too.
    for _ in range(2):
        gone = await _call(kb, member, "DELETE", f"/knowledge/pages/{page}/favorite")
        assert gone.status_code == 204
    assert await _favorites(kb, member) == [other]


async def test_the_favorites_page_with_a_cursor(kb, app_engine) -> None:  # noqa: F811
    admin = _principal(_address("admin"), kb.org_a, CAPABILITY)
    space = await _space(kb, admin, "Ops", [{"subject": "org", "level": "read"}])
    pages = [await _page(kb, admin, space, f"Page {n}") for n in range(3)]
    for page in pages:
        await _ok(kb, admin, "POST", f"/knowledge/pages/{page}/favorite")
    first = await _ok(kb, admin, "GET", "/knowledge/favorites?limit=2")
    assert _ids(first) == [pages[2], pages[1]] and first["next_cursor"]
    rest = await _ok(kb, admin, "GET",
                     f"/knowledge/favorites?limit=2&cursor={first['next_cursor']}")
    assert _ids(rest) == [pages[0]] and rest["next_cursor"] is None


async def test_a_second_org_sees_none_of_the_favorites(kb, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    admin_a = _principal(_address("admin-a"), kb.org_a, CAPABILITY)
    admin_b = _principal(_address("admin-b"), kb.org_b, CAPABILITY)
    space = await _space(kb, admin_a, "Handbook", [{"subject": "org", "level": "read"}])
    page = await _page(kb, admin_a, space, "Policy")
    await _ok(kb, admin_a, "POST", f"/knowledge/pages/{page}/favorite")

    assert await _favorites(kb, admin_b) == []
    assert page not in _ids(await _recent(kb, admin_b, limit=50))
    refused = await _call(kb, admin_b, "POST", f"/knowledge/pages/{page}/favorite")
    assert refused.status_code == 404
    # A DELETE from org B does not reach org A's row.
    await _call(kb, admin_b, "DELETE", f"/knowledge/pages/{page}/favorite")
    assert await _favorites(kb, admin_a) == [page]

    # Under row level security, as the app role.
    with app_engine.connect() as c:
        trans = c.begin()
        c.execute(text("SELECT set_config('app.tenant_id', :o, true)"), {"o": kb.org_b})
        assert c.execute(text("SELECT count(*) FROM kb_favorites")).scalar() == 0
        with pytest.raises(DBAPIError):
            c.execute(text(
                "INSERT INTO kb_favorites (organization_id, member_email, page_id) "
                "VALUES (CAST(:o AS uuid), 'x@b.test', CAST(:p AS uuid))"),
                {"o": kb.org_a, "p": page})
        trans.rollback()
    with app_engine.connect() as c:
        trans = c.begin()
        c.execute(text("SELECT set_config('app.tenant_id', :o, true)"), {"o": kb.org_a})
        assert c.execute(text("SELECT count(*) FROM kb_favorites")).scalar() == 1
        trans.rollback()


async def test_the_member_of_a_favorite_is_the_session(kb, app_engine) -> None:  # noqa: F811
    email = _address("Mixed.Case")
    admin = _principal(email, kb.org_a, CAPABILITY)
    space = await _space(kb, admin, "Ops", [{"subject": "org", "level": "read"}])
    page = await _page(kb, admin, space, "Policy")
    await _ok(kb, admin, "POST", f"/knowledge/pages/{page}/favorite")
    with kb.admin_engine.connect() as c:
        rows = c.execute(text(
            "SELECT member_email, organization_id::text FROM kb_favorites")).all()
    assert rows == [(email.lower(), kb.org_a)]


# ── The migration ───────────────────────────────────────────────────────────

def test_the_table_forces_row_level_security_and_the_migration_replays(kb) -> None:
    sql = _home_migration().read_text(encoding="utf-8")
    for _ in range(2):
        with kb.admin_engine.begin() as c, c.connection.dbapi_connection.cursor() as cur:
            cur.execute(sql)
    with kb.admin_engine.connect() as c:
        forced = c.execute(text(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
            "WHERE oid = 'kb_favorites'::regclass")).first()
        policies = c.execute(text(
            "SELECT count(*) FROM pg_policies WHERE tablename = 'kb_favorites'")).scalar()
        checks = c.execute(text(
            "SELECT count(*) FROM pg_constraint "
            "WHERE conname = 'kb_pages_icon_length'")).scalar()
    assert tuple(forced) == (True, True)
    assert policies == 1
    assert checks == 1
