"""R8 — backlinks, "Linked from" (WS-61 KB-2h).

Spec: ``project-docs/specs/knowledge_base.md`` §21.5, the KB-2h row.

Against a REAL Postgres, through the real ``/knowledge`` router, as the
non-privileged role: a saved link shows in the target's list, an edit that
drops the link removes it, one link twice is one row, a link in a code fence
is no row, a source the member cannot see is absent, a second org sees
nothing, a deleted page leaves no row, and a stale target never fails a save.
The reader itself needs no database and has its own plain tests.
"""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from gateway.routes.knowledge.backlinks import page_ids_in
from sqlalchemy import text

from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
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

A = "11111111-1111-4111-8111-111111111111"
B = "22222222-2222-4222-8222-222222222222"


# ── The reader (no database) ────────────────────────────────────────────────

def test_the_reader_finds_ids_once_and_strips_query_and_fragment() -> None:
    body = (f"[x](/knowledge/pages/{A}) and [y](/knowledge/pages/{A}?edit=1) "
            f"and [z](https://app.test/knowledge/pages/{B.upper()}#h)")
    assert page_ids_in(body) == [A, B]


def test_the_reader_skips_code_and_self() -> None:
    body = (f"```\n/knowledge/pages/{A}\n```\n~~~md\n/knowledge/pages/{A}\n~~~\n"
            f"`/knowledge/pages/{A}` [s](/knowledge/pages/{B})")
    assert page_ids_in(body) == [B]
    assert page_ids_in(body, own_id=B) == []
    assert page_ids_in(f"/knowledge/pages/{A}0") == []
    assert page_ids_in(None) == []


def test_the_reader_skips_indented_code_but_not_list_continuations() -> None:
    code = f"Intro\n\n    /knowledge/pages/{A}\n\n[s](/knowledge/pages/{B})"
    assert page_ids_in(code) == [B]
    assert page_ids_in(f"    /knowledge/pages/{A}") == []
    # A line right after paragraph text continues the paragraph.
    assert page_ids_in(f"Intro\n    /knowledge/pages/{A}") == [A]
    # Inside a list item the indent is the item's content.
    assert page_ids_in(f"- item\n\n    /knowledge/pages/{A}") == [A]


# ── The routes ──────────────────────────────────────────────────────────────


async def _page(p: Any, who: Any, space: str | None, title: str, body: str = "",
                *, publish: bool = True) -> str:
    made: dict[str, Any] = {"title": title, "body_md": body or f"# {title}"}
    if space is not None:
        made["space_id"] = space
    page = await _ok(p, who, "POST", "/knowledge/pages", made, status=201)
    if publish:
        await _ok(p, who, "POST", f"/knowledge/pages/{page['id']}/publish")
    return page["id"]


async def _from(p: Any, who: Any, page: str) -> list[str]:
    got = await _ok(p, who, "GET", f"/knowledge/pages/{page}/backlinks")
    return [r["id"] for r in got["backlinks"]]


def _rows(p: Any, source: str) -> int:
    with p.admin_engine.connect() as c:
        return c.execute(text(
            "SELECT count(*) FROM kb_links WHERE page_id = CAST(:p AS uuid) "
            "AND target_kind = 'page'"), {"p": source}).scalar()


@pytest.fixture
def kb(ns):  # noqa: F811 — a fixture
    yield ns
    with ns.admin_engine.begin() as c:
        c.execute(text("DELETE FROM kb_chunks"))


@_DB_GATE
async def test_a_link_lists_edit_removes_and_duplicates_collapse(kb, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    admin = _principal(_address("admin"), kb.org_a, CAPABILITY)
    space = await _space(kb, admin, "Handbook", [{"subject": "org", "level": "read"}])
    target = await _page(kb, admin, space, "Target")
    link = f"[t](/knowledge/pages/{target})"
    source = await _page(kb, admin, space, "Source", f"{link} and again {link}?x=1")
    assert await _from(kb, admin, target) == [source]
    assert _rows(kb, source) == 1

    # A save with no change adds no second row.
    await _ok(kb, admin, "PATCH", f"/knowledge/pages/{source}",
              {"body_md": f"{link} and again {link}?x=1"})
    assert _rows(kb, source) == 1

    await _ok(kb, admin, "PATCH", f"/knowledge/pages/{source}", {"body_md": "No link now."})
    assert await _from(kb, admin, target) == []
    assert _rows(kb, source) == 0


@_DB_GATE
async def test_a_fence_self_and_stale_target_give_no_row_and_no_error(kb, app_engine) -> None:  # noqa: F811
    admin = _principal(_address("admin"), kb.org_a, CAPABILITY)
    space = await _space(kb, admin, "Ops", [{"subject": "org", "level": "read"}])
    target = await _page(kb, admin, space, "Target")
    fenced = await _page(kb, admin, space, "Fenced",
                         f"```\n[t](/knowledge/pages/{target})\n```")
    assert await _from(kb, admin, target) == []
    own = await _page(kb, admin, space, "Own")
    await _ok(kb, admin, "PATCH", f"/knowledge/pages/{own}",
              {"body_md": f"[me](/knowledge/pages/{own}) [gone](/knowledge/pages/{A})"})
    assert _rows(kb, own) == 0 and _rows(kb, fenced) == 0


@_DB_GATE
async def test_a_source_the_member_cannot_see_is_absent(kb, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    admin = _principal(_address("admin"), kb.org_a, CAPABILITY)
    member_email = _address("member")
    _add_member(kb, kb.org_a, member_email)
    member = _principal(member_email, kb.org_a)
    open_space = await _space(kb, admin, "Handbook", [{"subject": "org", "level": "read"}])
    board = await _space(kb, admin, "Board", [])
    target = await _page(kb, admin, open_space, "Target")
    link = f"[t](/knowledge/pages/{target})"
    visible = await _page(kb, admin, open_space, "Visible", link)
    hidden = await _page(kb, admin, board, "Hidden", link)
    draft = await _page(kb, admin, open_space, "Draft", link, publish=False)
    assert await _from(kb, member, target) == [visible]
    assert set(await _from(kb, admin, target)) == {visible, hidden}
    assert draft not in await _from(kb, admin, target)

    # Archived sources are not listed, and a restore brings the row back.
    await _ok(kb, admin, "POST", f"/knowledge/pages/{visible}/archive")
    assert await _from(kb, member, target) == []
    await _ok(kb, admin, "POST", f"/knowledge/pages/{visible}/restore")
    assert await _from(kb, member, target) == [visible]


@_DB_GATE
async def test_a_second_org_sees_nothing_and_a_delete_leaves_no_row(kb, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    admin_a = _principal(_address("admin-a"), kb.org_a, CAPABILITY)
    admin_b = _principal(_address("admin-b"), kb.org_b, CAPABILITY)
    space = await _space(kb, admin_a, "Handbook", [{"subject": "org", "level": "read"}])
    target = await _page(kb, admin_a, space, "Target")
    source = await _page(kb, admin_a, space, "Source", f"[t](/knowledge/pages/{target})")
    assert (await _call(kb, admin_b, "GET", f"/knowledge/pages/{target}/backlinks")).status_code == 404
    with kb.admin_engine.connect() as c:
        assert c.execute(text(
            "SELECT count(*) FROM kb_links WHERE organization_id = CAST(:o AS uuid)"),
            {"o": kb.org_b}).scalar() == 0
    with kb.admin_engine.begin() as c:
        c.execute(text("DELETE FROM kb_pages WHERE id = CAST(:p AS uuid)"), {"p": source})
    assert _rows(kb, source) == 0
    assert await _from(kb, admin_a, target) == []


async def _member(kb: Any, org: str, tag: str) -> tuple[str, Any]:
    email = _address(tag)
    _add_member(kb, org, email)
    return email, _principal(email, org)


def _target_rows(p: Any, source: str, kind: str = "page") -> list[str]:
    with p.admin_engine.connect() as c:
        return [r[0] for r in c.execute(text(
            "SELECT target_id FROM kb_links WHERE page_id = CAST(:p AS uuid) "
            "AND target_kind = :k"), {"p": source, "k": kind})]


async def _links(p: Any, who: Any, page: str) -> list[str]:
    got = await _ok(p, who, "GET", f"/knowledge/pages/{page}")
    return [x["target_id"] for x in got["links"] if x["target_kind"] == "page"]


@_DB_GATE
async def test_a_hidden_target_lands_no_row_and_shows_no_link(kb, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    admin = _principal(_address("admin"), kb.org_a, CAPABILITY)
    m_email, member = await _member(kb, kb.org_a, "m")
    mine = await _space(kb, admin, "Mine", [{"subject": m_email, "level": "write"}])
    board = await _space(kb, admin, "Board", [])
    secret = await _page(kb, admin, board, "Secret")
    seen = await _page(kb, admin, mine, "Seen")
    source = await _page(kb, member, mine, "Source",
                         f"[s](/knowledge/pages/{secret}) [v](/knowledge/pages/{seen})")
    assert _target_rows(kb, source) == [seen]
    assert await _links(kb, member, source) == [seen]
    assert await _links(kb, admin, source) == [seen]


@_DB_GATE
async def test_a_reader_with_less_access_than_the_writer_sees_no_target(kb, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    admin = _principal(_address("admin"), kb.org_a, CAPABILITY)
    n_email, reader = await _member(kb, kb.org_a, "n")
    open_space = await _space(kb, admin, "Open", [{"subject": "org", "level": "read"}])
    board = await _space(kb, admin, "Board", [])
    secret = await _page(kb, admin, board, "Secret")
    seen = await _page(kb, admin, open_space, "Seen")
    # The admin sees both targets, so both rows land.
    source = await _page(kb, admin, open_space, "Source",
                         f"[s](/knowledge/pages/{secret}) [v](/knowledge/pages/{seen})")
    assert set(_target_rows(kb, source)) == {secret, seen}
    assert set(await _links(kb, admin, source)) == {secret, seen}
    # The reader reads the source and learns nothing of the secret target.
    assert await _links(kb, reader, source) == [seen]


@_DB_GATE
async def test_a_target_in_another_org_lands_no_row(kb, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    admin_a = _principal(_address("admin-a"), kb.org_a, CAPABILITY)
    admin_b = _principal(_address("admin-b"), kb.org_b, CAPABILITY)
    space_a = await _space(kb, admin_a, "Handbook", [{"subject": "org", "level": "read"}])
    space_b = await _space(kb, admin_b, "Handbook", [{"subject": "org", "level": "read"}])
    foreign = await _page(kb, admin_b, space_b, "Foreign")
    source = await _page(kb, admin_a, space_a, "Source", f"[f](/knowledge/pages/{foreign})")
    assert _target_rows(kb, source) == []
    with kb.admin_engine.connect() as c:
        assert c.execute(text(
            "SELECT count(*) FROM kb_links WHERE target_id = :t"), {"t": foreign}).scalar() == 0


@_DB_GATE
async def test_a_save_leaves_the_links_of_other_kinds(kb, app_engine) -> None:  # noqa: F811
    admin = _principal(_address("admin"), kb.org_a, CAPABILITY)
    space = await _space(kb, admin, "Ops", [{"subject": "org", "level": "read"}])
    target = await _page(kb, admin, space, "Target")
    source = await _page(kb, admin, space, "Source", f"[t](/knowledge/pages/{target})")
    with kb.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO kb_links (organization_id, page_id, target_kind, target_id, "
            "created_by) VALUES (CAST(:o AS uuid), CAST(:p AS uuid), 'url', "
            "'https://example.test/x', 'seed')"), {"o": kb.org_a, "p": source})
    await _ok(kb, admin, "PATCH", f"/knowledge/pages/{source}", {"body_md": "No link."})
    assert _target_rows(kb, source) == []
    assert _target_rows(kb, source, "url") == ["https://example.test/x"]
    got = await _ok(kb, admin, "GET", f"/knowledge/pages/{source}")
    assert [x["target_kind"] for x in got["links"]] == ["url"]
