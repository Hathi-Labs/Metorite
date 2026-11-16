"""CO-13: the session list gives the continue mark (``chat_organization.md`` §17.5).

No database here. A fake ``tenant_session`` returns the rows that the one
list statement would return, and the tests check how ``_get_sessions`` turns
``continued_from`` into the ``continuedFrom`` key. The R8 twin
(``test_continue_mark_r8.py``) proves the same cases on a real Postgres.

The read rule is the rule of the list itself. A source is readable exactly
when it is a row of the list, because the statement carries
``SESSION_VISIBLE_SQL`` and no limit. So there is no second rule and no second
statement. Mutations, each red here: add a statement (the one-statement case),
fill the title of a source that is not in the list (the unreadable case), give
a mark for a NULL link (the none case), take the member from anywhere but the
caller (the SQL case).
"""

from __future__ import annotations

import contextlib
import datetime as dt
from types import SimpleNamespace

import pytest

pytest.importorskip("sqlalchemy")

NOW = dt.datetime(2026, 11, 16, 9, 0, tzinfo=dt.UTC)


def _row(sid: str, *, title: str | None = "t", link: str | None = None, archived=None):
    return SimpleNamespace(
        id=sid, agent_name="orchestrator", title=title, last_preview=None,
        message_count=1, created_at=NOW, updated_at=NOW, user_id="a@x.test",
        visibility="private", participant_count=1, has_user_message=True,
        my_role="owner", archived_at=archived, continued_from=link,
    )


def _list(monkeypatch, rows, *, pins=None):
    """Run the list. The pin switch is set here, so a developer ``.env`` with
    ``CHAT_PIN_ORGS`` cannot change a case. ``pins`` maps a chat id to its
    ``pinned_at``. With ``pins`` None the switch is OFF."""
    import acb_graph
    from acb_common import get_settings
    from gateway.routes.chat import _get_sessions

    monkeypatch.setattr(
        get_settings(), "chat_pin_orgs", "*" if pins is not None else "", raising=False)
    statements: list[str] = []

    class _Session:
        def execute(self, stmt, params=None):
            statements.append(str(stmt))
            self.params = params
            if "chat_session_member" in str(stmt):
                return SimpleNamespace(fetchall=lambda: [
                    SimpleNamespace(session_id=k, pinned_at=v)
                    for k, v in (pins or {}).items()
                ])
            return SimpleNamespace(fetchall=lambda: rows)

    @contextlib.contextmanager
    def _fake(_org):
        yield _Session()

    monkeypatch.setattr(acb_graph, "tenant_session", _fake)
    out = _get_sessions("a@x.test", organization_id="org")
    return out, statements


def test_a_chat_that_continues_nothing_reads_null(monkeypatch) -> None:
    out, _ = _list(monkeypatch, [_row("c1")])
    assert out[0]["continuedFrom"] is None


def test_a_readable_source_gives_its_id_and_title(monkeypatch) -> None:
    out, _ = _list(monkeypatch, [_row("new", link="old"), _row("old", title="Q3 deck")])
    assert out[0]["continuedFrom"] == {"id": "old", "title": "Q3 deck"}


def test_an_archived_source_keeps_its_title_and_link(monkeypatch) -> None:
    """An archived chat stays in the list, so the member can still read it."""
    out, _ = _list(monkeypatch, [
        _row("new", link="old"), _row("old", title="Old", archived=NOW),
    ])
    assert out[0]["continuedFrom"] == {"id": "old", "title": "Old"}


def test_a_source_the_member_cannot_read_gives_no_title_and_no_id(monkeypatch) -> None:
    """The source is not a row of this member's list, so it is not readable."""
    out, _ = _list(monkeypatch, [_row("new", link="hidden")])
    assert out[0]["continuedFrom"] == {"id": None, "title": None}
    assert "hidden" not in repr(out)


def test_an_untitled_readable_source_keeps_its_id(monkeypatch) -> None:
    out, _ = _list(monkeypatch, [_row("new", link="old"), _row("old", title=None)])
    assert out[0]["continuedFrom"] == {"id": "old", "title": None}


def test_a_deleted_source_leaves_a_null_link_and_no_mark(monkeypatch) -> None:
    """``ON DELETE SET NULL``: the column is NULL, so the key is null."""
    out, _ = _list(monkeypatch, [_row("new", link=None)])
    assert out[0]["continuedFrom"] is None


def test_the_list_is_still_one_statement_under_the_visible_rule(monkeypatch) -> None:
    from gateway.rooms import SESSION_VISIBLE_SQL

    _, statements = _list(monkeypatch, [_row("new", link="old"), _row("old")])
    assert len(statements) == 1
    assert " ".join(SESSION_VISIBLE_SQL.split()) in " ".join(statements[0].split())
    assert "s.continued_from" in statements[0]


def test_the_existing_keys_are_unchanged(monkeypatch) -> None:
    out, _ = _list(monkeypatch, [_row("c1")])
    assert set(out[0]) == {
        "id", "agentName", "title", "lastPreview", "messageCount", "createdAt",
        "updatedAt", "visibility", "isOwner", "participantCount",
        "hasUserMessage", "archivedAt", "continuedFrom",
    }


def test_the_pin_branch_keeps_the_mark_next_to_the_pin(monkeypatch) -> None:
    """CO-11 adds pin keys to each row. It must not rebuild the row (CO-13)."""
    out, statements = _list(
        monkeypatch,
        [_row("new", link="old"), _row("old", title="Old", archived=NOW)],
        pins={"new": NOW, "old": NOW},
    )
    new, old = out[0], out[1]
    assert len(statements) == 2, "the list statement and the pin statement"
    assert new["pinned"] is True and new["pinnedAt"] == NOW.isoformat()
    # An archived and pinned source reads as not pinned, and still resolves.
    assert old["pinned"] is False
    assert new["continuedFrom"] == {"id": "old", "title": "Old"}
