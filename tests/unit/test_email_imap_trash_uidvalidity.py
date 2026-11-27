"""Task 4095 (rung 0, data loss): a stale IMAP UID never moves another mail.

When a server rebuilds INBOX, its UIDVALIDITY changes, and each mail gets a
new UID. ``sync_messages`` then reads only the newest 100 UIDs, and the upsert
keys on ``(account_id, provider_message_id)``. So an older row keeps its old
UID, and that UID can now name another mail. The trash of the old code moved
that other mail to Trash.

The trash now takes the stored Message-ID of the row. It reads the Message-ID
of the mail at the UID with ``BODY.PEEK`` and raises on a mismatch, so nothing
moves. ``FakeImap`` of ``test_email_imap_trash.py`` keeps real UIDs and a
UIDVALIDITY, and ``rebuild`` gives INBOX new UIDs.

Owning spec: ``project-docs/specs/email_app_master_plan.md`` §18.13.3.
Run: ``uv run pytest tests/unit/test_email_imap_trash_uidvalidity.py -q``
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from email_ingestion.providers.imap import IMAPProvider
from gateway.routes.email.automation import actions
from gateway.routes.email.transport import messages as route

from tests.unit.test_email_imap_trash import (
    _BARE_CAPS,
    _COPY_CAPS,
    _FULL_CAPS,
    FakeImap,
    _mail,
)

BOX = "00000000-0000-0000-0000-0000000000b0"
ROW = "00000000-0000-0000-0000-0000000000a1"
USER = SimpleNamespace(email="jane@acme.test")


def _server(caps: tuple[str, ...] = _FULL_CAPS) -> FakeImap:
    """INBOX holds A (uid 1), B (uid 2) and D (uid 3). No mail is read."""
    return FakeImap({
        "INBOX": [(_mail("A"), set()), (_mail("B"), set()), (_mail("D"), set())],
        "Trash": [(_mail("T1"), {"\\Seen"})],
    }, caps=caps)


def _provider(server: FakeImap) -> IMAPProvider:
    p = IMAPProvider({})
    p._imap = server  # type: ignore[assignment]
    p.authenticate = AsyncMock(return_value=True)  # type: ignore[method-assign]
    return p


async def _stored_b(server: FakeImap) -> Any:
    """The row of B, as the real sync stores it before the rebuild."""
    result = await _provider(server).sync_messages(None)
    [b] = [m for m in result.messages if m.subject == "B"]
    assert (b.provider_message_id, b.thread_id) == ("2", "<B@test>")
    assert result.new_history_id == "3:1"
    return b


def _rebuilt(caps: tuple[str, ...] = _FULL_CAPS) -> FakeImap:
    """After the rebuild, UID 2 names A, and B has UID 3."""
    server = _server(caps)
    server.rebuild([_mail("D"), _mail("A"), _mail("B")])
    assert server.boxes["INBOX"][2][0] == _mail("A")
    return server


# ── The route: the defect, end to end ─────────────────────────────────────────


def _route_db(stored: str | None) -> Any:
    """The session of the route. The UPDATE finds the row, and a read of
    the stored Message-ID answers ``stored``."""
    db = AsyncMock()
    result = MagicMock()
    result.rowcount = 1
    result.fetchone.return_value = SimpleNamespace(message_id=stored)
    db.execute.return_value = result
    return db


def _wire_route(monkeypatch: pytest.MonkeyPatch, provider: Any, pmid: str,
                stored: str | None) -> None:
    db = _route_db(stored)

    @asynccontextmanager
    async def _session():
        yield db

    monkeypatch.setattr(route, "_tenant_session", _session)
    monkeypatch.setattr(route, "_provider_for_message",
                        AsyncMock(return_value=(provider, pmid, BOX, object())))
    monkeypatch.setattr(route, "_persist_rotated_creds", AsyncMock())


@pytest.mark.parametrize("caps", [_FULL_CAPS, _COPY_CAPS, _BARE_CAPS],
                         ids=["move", "copy-uidplus", "copy"])
async def test_a_stale_uid_after_a_uidvalidity_change_moves_no_mail(monkeypatch, caps):
    """The member deletes B from the list. The row of B still holds UID 2,
    which now names A. The route answers ``not_confirmed``, and no mail
    on the server changes: A stays in INBOX and unread."""
    server = _server(caps)
    stored = await _stored_b(server)
    server.rebuild([_mail("D"), _mail("A"), _mail("B")])
    before = server.snapshot()
    _wire_route(monkeypatch, _provider(server), stored.provider_message_id,
                stored.thread_id)

    resp = await route.delete_message(ROW, user=USER, report=True)

    assert server.snapshot()["INBOX"] == before["INBOX"], "a stale UID moved another mail"
    assert server.snapshot() == before
    assert json.loads(resp.body)["provider"] == "not_confirmed"


async def test_the_route_trashes_a_row_whose_uid_is_still_right(monkeypatch):
    server = _server()
    stored = await _stored_b(server)
    _wire_route(monkeypatch, _provider(server), stored.provider_message_id,
                stored.thread_id)
    resp = await route.delete_message(ROW, user=USER, report=True)
    assert json.loads(resp.body)["provider"] == "confirmed"
    assert [b for b, _f in server.boxes["INBOX"].values()] == [_mail("A"), _mail("D")]
    assert _mail("B") in [b for b, _f in server.boxes["Trash"].values()]


# ── The provider: the check of the Message-ID ────────────────────────────────


@pytest.mark.parametrize("caps", [_FULL_CAPS, _COPY_CAPS, _BARE_CAPS],
                         ids=["move", "copy-uidplus", "copy"])
async def test_a_mismatch_raises_and_the_right_uid_moves(caps):
    server = _rebuilt(caps)
    before = server.snapshot()
    with pytest.raises(RuntimeError, match="another mail"):
        await _provider(server).trash_message("2", message_id="<B@test>")
    assert server.snapshot() == before
    assert not any(line.split()[1] in ("MOVE", "COPY", "STORE")
                   for line in server.log if line.startswith("UID "))
    # B at its new UID moves.
    await _provider(server).trash_message("3", message_id="<B@test>")
    assert _mail("B") in [b for b, _f in server.boxes["Trash"].values()]
    assert _mail("A") in [b for b, _f in server.boxes["INBOX"].values()]


async def test_the_read_is_a_peek_and_sets_no_flag():
    """The check never marks a mail read: it fetches with ``BODY.PEEK``."""
    server = _rebuilt()
    with pytest.raises(RuntimeError):
        await _provider(server).trash_message("2", message_id="<B@test>")
    assert server.boxes["INBOX"][2][1] == set()  # A is still unread
    fetches = [line for line in server.log if line.startswith("UID FETCH")]
    assert fetches and all("BODY.PEEK[" in line for line in fetches)


@pytest.mark.parametrize("stored", [None, "", "   ", "<>"])
async def test_a_row_with_no_message_id_moves_nothing(stored):
    """Fail closed: with no stored Message-ID, the trash cannot know which
    mail the UID names. It raises before any command."""
    server = _server()
    with pytest.raises(ValueError, match="Message-ID"):
        await _provider(server).trash_message("2", message_id=stored)
    assert server.log == []


async def test_a_call_with_no_message_id_argument_moves_nothing():
    """A caller that does not pass the Message-ID fails closed."""
    server = _server()
    with pytest.raises(ValueError, match="Message-ID"):
        await _provider(server).trash_message("2")
    assert server.log == []


async def test_a_server_mail_with_no_message_id_moves_nothing():
    server = _server()
    server.boxes["INBOX"][2] = (b"Subject: B\r\n\r\nB", set())
    before = server.snapshot()
    with pytest.raises(RuntimeError, match="no Message-ID"):
        await _provider(server).trash_message("2", message_id="<B@test>")
    assert server.snapshot() == before


@pytest.mark.parametrize("header,stored", [
    (b"Message-ID:\r\n <B@test>", "<B@test>"),          # a folded header
    (b"Message-Id: <B@test>", "<B@test>"),              # the case of the name
    (b"Message-ID: <B@test> (a comment)", "<B@test>"),  # a comment after it
    (b"Message-ID: <B@test>", "B@test"),                # no angle brackets
    (b"Message-ID:   <B@test>  ", " <B@test>\r\n"),     # white space
])
async def test_folding_brackets_and_white_space_still_match(header, stored):
    server = _server()
    server.boxes["INBOX"][2] = (b"Subject: B\r\n" + header + b"\r\n\r\nB", set())
    await _provider(server).trash_message("2", message_id=stored)
    assert len(server.boxes["INBOX"]) == 2


@pytest.mark.parametrize("uid_last", [False, True], ids=["uid-first", "uid-last"])
async def test_the_uid_may_come_before_or_after_the_header(uid_last):
    """A server can name the UID after the literal of the header. The check
    reads the answer for that UID in both orders."""
    server = _rebuilt()
    server.uid_last = uid_last
    with pytest.raises(RuntimeError, match="another mail"):
        await _provider(server).trash_message("2", message_id="<B@test>")
    await _provider(server).trash_message("3", message_id="<B@test>")
    assert _mail("B") in [b for b, _f in server.boxes["Trash"].values()]


async def test_another_case_of_the_id_does_not_match():
    """The two values come from the same header bytes, so a change of case
    is another id. The trash fails closed."""
    server = _server()
    before = server.snapshot()
    with pytest.raises(RuntimeError, match="another mail"):
        await _provider(server).trash_message("2", message_id="<b@test>")
    assert server.snapshot() == before


async def test_a_twin_with_the_same_message_id_is_a_copy_of_the_same_mail():
    """Two mails can hold one Message-ID (a list copy and a direct copy).
    The check cannot tell them apart, so the twin at the UID moves. It is
    a copy of the same mail, and it goes to Trash, so no mail is lost."""
    server = _server()
    twin = b"Subject: B (list)\r\nMessage-ID: <B@test>\r\n\r\nB"
    server.boxes["INBOX"][2] = (twin, set())
    await _provider(server).trash_message("2", message_id="<B@test>")
    assert twin in [b for b, _f in server.boxes["Trash"].values()]


# ── The bulk path and the helper ──────────────────────────────────────────────


async def test_the_bulk_trash_checks_each_message_id():
    server = _rebuilt(_COPY_CAPS)
    failed: list[str] = []
    await _provider(server).bulk_apply(
        ["2", "3"], "trash", failed,
        message_ids={"2": "<B@test>", "3": "<B@test>"})
    assert failed == ["2"]
    assert [b for b, _f in server.boxes["INBOX"].values()] == [_mail("D"), _mail("A")]


async def test_a_bulk_trash_with_no_message_ids_moves_nothing():
    server = _rebuilt()
    before = server.snapshot()
    failed: list[str] = []
    await _provider(server).bulk_apply(["1", "2", "3"], "trash", failed)
    assert failed == ["1", "2", "3"]
    assert server.snapshot() == before


async def test_the_helper_passes_the_message_id_to_imap_only():
    from email_ingestion.providers.base import trash_checked

    plain = SimpleNamespace(trash_message=AsyncMock(return_value="new-id"))
    assert await trash_checked(plain, "p1", "<x@y>") == "new-id"
    plain.trash_message.assert_awaited_once_with("p1")
    mocked = MagicMock()  # an attribute of a mock is not True
    mocked.trash_message = AsyncMock()
    await trash_checked(mocked, "p1", "<x@y>")
    mocked.trash_message.assert_awaited_once_with("p1")
    imap = _provider(_server())
    await trash_checked(imap, "2", "<B@test>")
    assert _mail("B") in [b for b, _f in imap._imap.boxes["Trash"].values()]  # type: ignore[union-attr]


# ── A rule action ─────────────────────────────────────────────────────────────


async def test_a_rule_trash_on_a_stale_uid_keeps_its_row():
    """The TRASH action of a rule reads the stored Message-ID of the row. A
    stale UID raises, the row stays as it was, and History names the issue."""
    server = _rebuilt()
    before = server.snapshot()
    db = _route_db("<B@test>")
    errors: list[dict[str, str]] = []
    done = await actions._apply_rule_actions(
        db, _provider(server), ROW, "2", [{"type": "TRASH"}], errors_out=errors)
    assert done == [] and errors and errors[0]["type"] == "TRASH"
    assert server.snapshot() == before
    writes = [str(c.args[0]) for c in db.execute.await_args_list
              if "UPDATE" in str(c.args[0])]
    assert writes == []
