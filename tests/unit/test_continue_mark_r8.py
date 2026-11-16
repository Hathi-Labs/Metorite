"""CO-13: the continue mark on a REAL Postgres (R8), as the app role.

``chat_organization.md`` §17.5. The session list gives ``continuedFrom`` for a
chat that continues another one. The suite reuses the world of
``test_continue_carry.py``: the whole ladder, two organizations, and the
non-privileged role under RLS. Each case reads the list through
``_get_sessions`` as the member.

- a chat made by the WT-1 route gives the source id and title to its owner;
- a member of the new chat who cannot read the source gets no title and no id;
- a deleted source gives no mark (``ON DELETE SET NULL``);
- a link to a chat of another organization never resolves;
- an archived source stays readable, with its title and link;
- a chat that continues nothing reads null, with the same keys as before.

Mutations, each red here: resolve the title with a second query that skips
``SESSION_VISIBLE_SQL`` (the cannot-read case); read the source without the
tenant (the other-org case); fill a mark for a NULL link (the deleted case).
"""

# ruff: noqa: F811 -- a fixture is imported by name, and each test takes it as a parameter
from __future__ import annotations

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import text

from tests.unit.test_continue_carry import (  # noqa: F401 — fixtures and helpers
    ALICE,
    BOB,
    CAROL,
    _chat,
    _continue,
    _held_chat,
    _row,
    _track,
    _user,
    db,
    world,
)
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)


def _mark(ns, member: str, org: str, sid: str):
    from gateway.routes.chat import _get_sessions

    rows = [r for r in _get_sessions(member, organization_id=org) if r["id"] == sid]
    return rows[0] if rows else None


def _continued(ns, *, title: str | None = "Pricing deck") -> tuple[str, str]:
    """A held chat of Alice, and the chat that the WT-1 route made from it."""
    src = _held_chat(ns)
    if title is not None:
        with ns.admin_engine.begin() as c:
            c.execute(text("UPDATE chat_session SET title = :t WHERE id = :s"),
                      {"t": title, "s": src})
    resp = _continue(ns, src, _user(ALICE, ns.org_a))
    assert resp.status_code == 200, resp.text
    return src, _track(ns, resp)["session_id"]


@_DB_GATE
def test_the_owner_sees_the_source_title_and_id(db) -> None:
    src, new = _continued(db)
    row = _mark(db, ALICE, db.org_a, new)
    assert row["continuedFrom"] == {"id": src, "title": "Pricing deck"}


@_DB_GATE
def test_a_member_who_cannot_read_the_source_gets_no_title_and_no_id(db) -> None:
    src, new = _continued(db)
    with db.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO chat_session_participant (session_id, subject, role, "
            "organization_id) VALUES (:s, :b, 'member', CAST(:o AS uuid))"),
            {"s": new, "b": BOB, "o": db.org_a})
    row = _mark(db, BOB, db.org_a, new)
    assert row is not None, "Bob reads the new chat"
    assert row["continuedFrom"] == {"id": None, "title": None}
    assert "Pricing deck" not in repr(row) and src not in repr(row)
    assert _mark(db, BOB, db.org_a, src) is None


@_DB_GATE
def test_a_deleted_source_leaves_no_mark(db) -> None:
    src, new = _continued(db)
    with db.admin_engine.begin() as c:
        c.execute(text("DELETE FROM chat_session WHERE id = :s"), {"s": src})
    assert _mark(db, ALICE, db.org_a, new)["continuedFrom"] is None


@_DB_GATE
def test_a_source_of_another_org_never_resolves(db) -> None:
    other = _chat(db, org=db.org_b, owner=CAROL)
    with db.admin_engine.begin() as c:
        c.execute(text("UPDATE chat_session SET title = 'Carol secret' WHERE id = :s"),
                  {"s": other})
    _, new = _continued(db)
    with db.admin_engine.begin() as c:
        c.execute(text("UPDATE chat_session SET continued_from = :o WHERE id = :n"),
                  {"o": other, "n": new})
    row = _mark(db, ALICE, db.org_a, new)
    assert row["continuedFrom"] == {"id": None, "title": None}
    assert "Carol secret" not in repr(row) and other not in repr(row)


@_DB_GATE
def test_an_archived_source_keeps_its_title_and_link(db) -> None:
    src, new = _continued(db)
    with db.admin_engine.begin() as c:
        c.execute(text("UPDATE chat_session SET archived_at = now() WHERE id = :s"),
                  {"s": src})
    assert _mark(db, ALICE, db.org_a, new)["continuedFrom"] == {
        "id": src, "title": "Pricing deck"}


@_DB_GATE
def test_a_chat_with_no_continuation_is_unchanged(db) -> None:
    sid = _chat(db, org=db.org_a, owner=ALICE)
    row = _mark(db, ALICE, db.org_a, sid)
    assert row["continuedFrom"] is None
    assert set(row) == {
        "id", "agentName", "title", "lastPreview", "messageCount", "createdAt",
        "updatedAt", "visibility", "isOwner", "participantCount",
        "hasUserMessage", "archivedAt", "continuedFrom",
    }
