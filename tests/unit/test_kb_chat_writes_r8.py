"""R8 — the member's own Knowledge writes from the chat, on a real database (PAR-3).

Spec: ``project-docs/specs/chat_parity.md`` §5 (wave P-5), task 3105, and
``project-docs/specs/knowledge_base.md`` §8.1 (F2). The hermetic half is
``tests/unit/test_kb_chat_writes.py``.

What this proves against a REAL Postgres, through the real ``/knowledge``
router, as the NON-privileged role of the phase-4 rehearsal (no superuser, no
BYPASSRLS), on a database with the whole ladder and two organizations:

1. the REAL ``create_page`` tool, with the card approved, finds the space by
   its name and leaves ONE draft in it. The author is the member. One version
   says ``reason = create`` and ``via = chat``, and no chunk exists. The card
   held the full text. A declined card writes nothing;
2. a member with only ``read`` on the space gets the server's own sentence,
   and no row. The tool does not check the right itself;
3. the REAL ``edit_page`` changes the title and the body of a draft. The page
   stays a draft, and the version says ``via = chat``;
4. the REAL ``create_space`` is the server's 403 for a member who is no
   Knowledge manager. For a manager it makes the space with no grant;
5. a member of org B cannot find a space of org A, and cannot edit its page;
6. the REAL ``edit_page`` on a PUBLISHED page makes no card and changes no
   row. Only ``propose_edit`` changes published text (F2).

Nothing here publishes: each page that a chat tool touched stays a draft.
"""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    _URL,
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
from tests.unit.test_kb_proposals_r8 import _approve, _rows, _run_tool, _scalar

# ⚠️ Every test here needs the real database. Without this gate the suite
# ERRORS where no ladder URL is set (the deploy run), and the deploy stops.
pytestmark = _DB_GATE

BODY = "# Refunds\n\nThirty days.\n\n" + ("One line of the policy. " * 80) + "\nTHE END."


async def test_create_page_leaves_one_draft_one_chat_version_and_no_chunk(
    ns, app_engine, monkeypatch,  # noqa: F811
) -> None:
    _assert_non_priv(app_engine)
    from acb_skills import knowledge_tools as kt

    admin = _principal(_address("admin"), ns.org_a, CAPABILITY)
    member_email = _address("member")
    member = _principal(member_email, ns.org_a)
    space = await _space(ns, admin, "Support", [{"subject": member_email, "level": "write"}])
    cards = _approve(monkeypatch)

    out = await _run_tool(ns, member, monkeypatch, lambda: kt.create_page(
        title="Refund policy", body=BODY, space="Support"))
    assert len(cards) == 1
    assert BODY.strip() in cards[0]["context"] and "THE END." in cards[0]["context"]
    assert cards[0]["detail"] == kt.DRAFT_LINE
    assert out.startswith("Saved a new draft page. It is not published.")

    pages = _rows(ns, "SELECT id, state, author_email, space_id::text, organization_id::text, "
                      "body_md, published_at FROM kb_pages WHERE title = 'Refund policy'")
    assert len(pages) == 1
    pid, state, author, sid, org, body, published_at = pages[0]
    assert (state, author, sid, org) == ("draft", member_email.lower(), space, ns.org_a)
    assert body == BODY and published_at is None
    versions = _rows(ns, "SELECT version, reason, via, author_email FROM kb_page_versions "
                         "WHERE page_id = :p ORDER BY version", p=pid)
    assert [tuple(v) for v in versions] == [(1, "create", "chat", member_email.lower())]
    assert _scalar(ns, "SELECT count(*) FROM kb_chunks WHERE source_id = :p", p=pid) == 0
    assert f"Link: /knowledge/pages/{pid}" in out

    # A declined card, through the same real path, writes nothing.
    _approve(monkeypatch, False)
    out = await _run_tool(ns, member, monkeypatch, lambda: kt.create_page(
        title="Second", body="x", space="Support"))
    assert out in (kt.NOT_SAVED, kt.NO_CARD_SAVED)
    assert _scalar(ns, "SELECT count(*) FROM kb_pages WHERE title = 'Second'") == 0


async def test_a_reader_gets_the_servers_refusal_and_no_row(
    ns, app_engine, monkeypatch,  # noqa: F811
) -> None:
    _assert_non_priv(app_engine)
    from acb_skills import knowledge_tools as kt

    admin = _principal(_address("admin"), ns.org_a, CAPABILITY)
    reader_email = _address("reader")
    reader = _principal(reader_email, ns.org_a)
    await _space(ns, admin, "Finance", [{"subject": reader_email, "level": "read"}])
    cards = _approve(monkeypatch)

    out = await _run_tool(ns, reader, monkeypatch, lambda: kt.create_page(
        title="Budget", body="Numbers.", space="Finance"))
    assert out == "Nothing was saved. Writing in this space needs a write grant."
    assert len(cards) == 1, "the card shows, and the server refuses the write"
    assert _scalar(ns, "SELECT count(*) FROM kb_pages WHERE title = 'Budget'") == 0


async def test_edit_page_changes_a_draft_and_it_stays_a_draft(
    ns, app_engine, monkeypatch,  # noqa: F811
) -> None:
    _assert_non_priv(app_engine)
    from acb_skills import knowledge_tools as kt

    admin = _principal(_address("admin"), ns.org_a, CAPABILITY)
    writer_email = _address("writer")
    writer = _principal(writer_email, ns.org_a)
    space = await _space(ns, admin, "Ops", [{"subject": writer_email, "level": "write"}])
    made = await _ok(ns, writer, "POST", "/knowledge/pages",
                     {"space_id": space, "title": "Runbook", "body_md": "old"}, status=201)
    cards = _approve(monkeypatch)

    out = await _run_tool(ns, writer, monkeypatch, lambda: kt.edit_page(
        page_id=made["id"], title="Runbook v2", body="new steps"))
    assert out.startswith("Saved the change to the page.")
    assert "State: draft" in cards[0]["context"] and "new steps" in cards[0]["context"]
    row = _rows(ns, "SELECT title, body_md, state, published_at FROM kb_pages "
                    "WHERE id = CAST(:p AS uuid)", p=made["id"])
    assert [tuple(r) for r in row] == [("Runbook v2", "new steps", "draft", None)]
    versions = _rows(ns, "SELECT version, reason, via FROM kb_page_versions "
                         "WHERE page_id = :p ORDER BY version", p=made["id"])
    assert [tuple(v) for v in versions] == [(1, "create", "web"), (2, "edit", "chat")]


async def test_create_space_needs_a_manager_and_sends_no_grant(
    ns, app_engine, monkeypatch,  # noqa: F811
) -> None:
    _assert_non_priv(app_engine)
    from acb_skills import knowledge_tools as kt

    member = _principal(_address("member"), ns.org_a)
    manager = _principal(_address("manager"), ns.org_a, CAPABILITY)
    _approve(monkeypatch)

    out = await _run_tool(ns, member, monkeypatch, lambda: kt.create_space(
        name="Sales", purpose="Deals"))
    assert out == "Nothing was saved. Making a space needs knowledge:manage."
    assert _scalar(ns, "SELECT count(*) FROM kb_spaces WHERE name = 'Sales'") == 0

    out = await _run_tool(ns, manager, monkeypatch, lambda: kt.create_space(
        name="Sales", purpose="Deals"))
    assert out.startswith("Made the space.")
    rows = _rows(ns, "SELECT id, kind, purpose, organization_id::text FROM kb_spaces "
                     "WHERE name = 'Sales'")
    assert len(rows) == 1
    sid, kind, purpose, org = rows[0]
    assert (kind, purpose, org) == ("team", "Deals", ns.org_a)
    assert _scalar(ns, "SELECT count(*) FROM kb_space_grants WHERE space_id = :s", s=sid) == 0


async def test_org_b_cannot_find_a_space_or_edit_a_page_of_org_a(
    ns, app_engine, monkeypatch,  # noqa: F811
) -> None:
    _assert_non_priv(app_engine)
    from acb_skills import knowledge_tools as kt

    admin = _principal(_address("admin"), ns.org_a, CAPABILITY)
    writer_email = _address("writer")
    writer = _principal(writer_email, ns.org_a)
    space = await _space(ns, admin, "Legal", [{"subject": writer_email, "level": "write"}])
    made = await _ok(ns, writer, "POST", "/knowledge/pages",
                     {"space_id": space, "title": "Contracts", "body_md": "A"}, status=201)
    stranger = _principal(_address("stranger"), ns.org_b, CAPABILITY)
    cards = _approve(monkeypatch)

    out = await _run_tool(ns, stranger, monkeypatch, lambda: kt.create_page(
        title="Hijack", body="x", space=space))
    assert out == kt.NO_SPACE
    out = await _run_tool(ns, stranger, monkeypatch, lambda: kt.edit_page(
        page_id=made["id"], body="changed"))
    assert out == kt.PAGE_404
    assert cards == []
    assert _scalar(ns, "SELECT count(*) FROM kb_pages WHERE title = 'Hijack'") == 0
    assert _scalar(ns, "SELECT body_md FROM kb_pages WHERE id = CAST(:p AS uuid)",
                   p=made["id"]) == "A"


async def test_edit_page_never_changes_a_published_page(
    ns, app_engine, monkeypatch,  # noqa: F811
) -> None:
    """F2: only propose_edit changes published text. The real tool makes no card
    and no write, and the row and its versions stay as they were."""
    _assert_non_priv(app_engine)
    from acb_skills import knowledge_tools as kt

    admin = _principal(_address("admin"), ns.org_a, CAPABILITY)
    writer_email = _address("writer")
    writer = _principal(writer_email, ns.org_a)
    space = await _space(ns, admin, "Policies", [{"subject": writer_email, "level": "write"}])
    made = await _ok(ns, writer, "POST", "/knowledge/pages",
                     {"space_id": space, "title": "Leave", "body_md": "old"}, status=201)
    await _ok(ns, writer, "POST", f"/knowledge/pages/{made['id']}/publish")
    before = _rows(ns, "SELECT title, body_md, state, updated_at FROM kb_pages "
                       "WHERE id = CAST(:p AS uuid)", p=made["id"])
    versions = _scalar(ns, "SELECT count(*) FROM kb_page_versions WHERE page_id = :p",
                       p=made["id"])
    cards = _approve(monkeypatch)

    out = await _run_tool(ns, writer, monkeypatch, lambda: kt.edit_page(
        page_id=made["id"], title="Leave v2", body="new"))
    assert out == kt.PUBLISHED_PAGE
    assert cards == []
    after = _rows(ns, "SELECT title, body_md, state, updated_at FROM kb_pages "
                      "WHERE id = CAST(:p AS uuid)", p=made["id"])
    assert [tuple(r) for r in after] == [tuple(r) for r in before]
    assert before[0][2] == "published"
    assert _scalar(ns, "SELECT count(*) FROM kb_page_versions WHERE page_id = :p",
                   p=made["id"]) == versions
