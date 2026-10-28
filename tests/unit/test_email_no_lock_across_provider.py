"""No session is open across a provider call of the message routes (2026-10-28).

Production logged a lock queue on one email row. ``update_message`` wrote the
row, then called ``get_message`` inside its own open session. The second
session's mark-read waited on the first session's row lock, until the
statement timeout. ``get_message`` and ``delete_message`` also held the row
lock and one pooled connection across the provider fetch or push.

1. Hermetic: each provider call sees no open session, and the PATCH reads the
   mail after its write committed.
2. R8: a PATCH of an unread mail on a real database answers at once.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from gateway.routes.email.transport import messages as m
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope

# ``world``, ``promoted`` and ``app_engine`` are used by name for fixture
# injection, so the imports are load-bearing even though they read as unused.
from tests.unit.test_email_html_hot_only import (  # noqa: F401
    _bound,
    _seed_message,
    _user,
    world,
)
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

MID = "55555555-5555-4555-8555-555555555555"
USER = SimpleNamespace(email="u@example.com", organization_id=None)


class _Db:
    """One fake session for each block. Records each statement."""

    def __init__(self, row: Any) -> None:
        self.row = row
        self.sql: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> Any:
        self.sql.append((str(stmt), dict(params or {})))
        return SimpleNamespace(fetchone=lambda: self.row, fetchall=lambda: [], rowcount=1)

    def has(self, fragment: str) -> list[dict[str, Any]]:
        return [p for s, p in self.sql if fragment in s]


class _Provider:
    """Records the open sessions at each network call."""

    def __init__(self, open_now: list[int]) -> None:
        self.open_now = open_now
        self.calls: list[tuple[str, int]] = []

    def _at(self, name: str) -> None:
        self.calls.append((name, self.open_now[0]))

    async def authenticate(self) -> bool:
        self._at("authenticate")
        return True

    async def get_message(self, _pmid: str) -> Any:
        self._at("get_message")
        att = SimpleNamespace(filename="a.pdf", mime_type="application/pdf",
                              size_bytes=1, provider_attachment_id="att-1")
        return SimpleNamespace(body_text="the text", body_html=None,
                               has_attachments=True, attachments=[att])

    async def apply_flags(self, *_a: Any, **_k: Any) -> None:
        self._at("apply_flags")

    async def move_to_folder(self, *_a: Any) -> str:
        self._at("move_to_folder")
        return "NEW_PID"

    async def set_labels(self, *_a: Any, **_k: Any) -> None:
        self._at("set_labels")

    async def trash_message(self, *_a: Any) -> str:
        self._at("trash_message")
        return "NEW_PID"

    def credentials_dirty(self) -> bool:
        return False


def _wire(monkeypatch: pytest.MonkeyPatch, row: Any) -> tuple[_Db, _Provider, list[int]]:
    db = _Db(row)
    open_now = [0]

    @asynccontextmanager
    async def _session(*_a: Any, **_k: Any):
        open_now[0] += 1
        try:
            yield db
        finally:
            open_now[0] -= 1

    provider = _Provider(open_now)

    async def _for_message(*_a: Any, **_k: Any) -> Any:
        return provider, "pm-1", "acc-1", object()

    monkeypatch.setattr(m, "_tenant_session", _session)
    monkeypatch.setattr(m, "_provider_for_message", _for_message)
    return db, provider, open_now


def _open_row() -> SimpleNamespace:
    """A headers-only mail, so the open fetches its body."""
    return SimpleNamespace(
        id=MID, provider_message_id="pm-1", thread_id="t", account_id="acc-1",
        folder="inbox", labels=[], from_address=None, to_addresses=[],
        cc_addresses=[], bcc_addresses=[], subject="s", body_text="",
        body_html=None, snippet="", has_attachments=False, is_read=False,
        is_starred=False, is_flagged=False, importance="normal", categories=[],
        received_at=datetime.now(UTC), synced_at=None, snoozed_until=None,
        stored_bytes=0,
    )


async def test_the_open_fetches_with_no_session_open(monkeypatch) -> None:
    db, provider, _open = _wire(monkeypatch, _open_row())
    msg = await m.get_message(MID, user=USER)

    assert msg.body_text == "the text"
    names = [n for n, _ in provider.calls]
    # The body fetch, then the attachment fetch. The fake stores no file, so
    # the open also runs `_hydrate_attachments`. A real database fetches once.
    assert names.count("get_message") == 2, provider.calls
    assert {at for _, at in provider.calls} == {0}, provider.calls
    assert db.has("SET is_read = true"), "the open still marks the mail read"
    assert db.has("SET body_text"), "the open still stores the fetched body"
    assert db.has("INSERT INTO email_attachments")


async def test_the_patch_pushes_and_reads_with_no_session_open(monkeypatch) -> None:
    db, provider, open_now = _wire(monkeypatch, SimpleNamespace(id=MID, categories=[]))
    read_at: list[tuple[int, Any]] = []

    async def _get(mid: str, user: Any, **kw: Any) -> str:
        read_at.append((open_now[0], kw.get("mark_read")))
        return "the mail"

    monkeypatch.setattr(m, "get_message", _get)
    got = await m.update_message(
        MID, m.MessageUpdateModel(is_starred=True, folder="archive", add_labels=["X"]),
        user=USER)

    assert got == "the mail"
    # After the commit, and with no mark-read (review round 1).
    assert read_at == [(0, False)], "the PATCH read the mail in its own open session"
    assert [n for n, _ in provider.calls] == [
        "authenticate", "apply_flags", "move_to_folder", "set_labels"]
    assert {at for _, at in provider.calls} == {0}, provider.calls
    [rekey] = db.has("provider_message_id = :pid")
    assert (rekey["pid"], rekey["old"]) == ("NEW_PID", "pm-1")


async def test_the_trash_pushes_with_no_session_open(monkeypatch) -> None:
    db, provider, _open = _wire(monkeypatch, SimpleNamespace())
    await m.delete_message(MID, user=USER)

    assert [n for n, _ in provider.calls] == ["authenticate", "trash_message"]
    assert {at for _, at in provider.calls} == {0}, provider.calls
    assert db.has("SET folder = 'trash'")
    [rekey] = db.has("provider_message_id = :pid")
    assert (rekey["pid"], rekey["old"]) == ("NEW_PID", "pm-1")


async def test_a_patch_to_unread_never_marks_the_mail_read_again(monkeypatch) -> None:
    """Review round 1, P1. The PATCH answers with an open. After the commit,
    a mark-read there undid "Mark as unread" at once."""
    row = _open_row()
    row.body_text, row.is_read = "the body", True
    db, _provider, _open = _wire(monkeypatch, row)

    msg = await m.update_message(MID, m.MessageUpdateModel(is_read=False), user=USER)

    assert db.has("is_read = :is_read") == [{"id": MID, "is_read": False}]
    assert not db.has("SET is_read = true"), "the answer marked the mail read again"
    assert msg.id == MID


async def test_a_failed_push_keeps_the_local_write_and_skips_the_rekey(monkeypatch) -> None:
    db, provider, _open = _wire(monkeypatch, SimpleNamespace(id=MID, categories=[]))

    async def _boom(*_a: Any) -> str:
        raise RuntimeError("graph is down")

    provider.move_to_folder = _boom  # type: ignore[method-assign]

    async def _get(mid: str, user: Any, **_k: Any) -> str:
        return "the mail"

    monkeypatch.setattr(m, "get_message", _get)
    assert await m.update_message(
        MID, m.MessageUpdateModel(folder="archive"), user=USER) == "the mail"
    assert db.has("folder = :folder"), "the local move still lands"
    assert not db.has("provider_message_id = :pid")


# ── R8: the PATCH on a real database ────────────────────────────────────────


def _flags_of(admin, message_id: str) -> tuple[Any, ...]:
    with admin.connect() as c:
        return tuple(c.execute(text(
            "SELECT is_starred, is_read FROM email_messages "
            "WHERE id = CAST(:m AS uuid)"), {"m": message_id}).one())


def _seed(w, *, is_read: bool) -> str:
    """A mail with a stored body, so the open fetches nothing."""
    mid = _seed_message(w.admin, org=w.org, account=w.account,
                        received_at=datetime.now(UTC), body_text="b")
    with w.admin.begin() as c:
        c.execute(text("UPDATE email_messages SET is_read = :r "
                       "WHERE id = CAST(:m AS uuid)"), {"m": mid, "r": is_read})
    return mid


async def _patch(w, mid: str, update: Any) -> Any:
    async with tenant_engine_scope(w.dsn):
        with _bound(w.org):
            return await asyncio.wait_for(m.update_message(
                mid, update, user=_user(w.owner, w.org)), timeout=20)


@_DB_GATE
async def test_a_patch_of_an_unread_mail_answers_at_once(world) -> None:
    """The case that production logged. The old route waited on its own row
    lock here, so ``wait_for`` timed out."""
    mid = _seed(world, is_read=False)
    msg = await _patch(world, mid, m.MessageUpdateModel(is_starred=True))

    assert msg.is_starred is True
    # A star is no open: the mail stays unread (review round 1).
    assert _flags_of(world.admin, mid) == (True, False)


def _pmid_and_folder(admin, message_id: str) -> tuple[Any, ...]:
    with admin.connect() as c:
        return tuple(c.execute(text(
            "SELECT provider_message_id, folder FROM email_messages "
            "WHERE id = CAST(:m AS uuid)"), {"m": message_id}).one())


def _mover(monkeypatch: pytest.MonkeyPatch, new_pid: str, *, rotates: bool = False) -> None:
    """An Outlook-like provider whose /move re-keys the mail to ``new_pid``.
    With ``rotates``, its sign-in refreshed the token, so the block stores it."""
    from gateway.routes.email import core

    class _Moves:
        async def authenticate(self) -> bool:
            return True

        async def move_to_folder(self, _pmid: str, _name: str) -> str:
            return new_pid

        def credentials_dirty(self) -> bool:
            return rotates

        def export_credentials(self) -> dict[str, str]:
            return {"token": "rotated"}

    class _Store:
        def encrypt(self, value: str) -> str:
            return "enc:" + value

    monkeypatch.setattr(core, "_instantiate_provider", lambda _n, _c: _Moves())
    monkeypatch.setattr(core, "_decrypt_credentials", lambda _blob: ({}, _Store()))


def _creds(admin, account: str) -> str:
    with admin.connect() as c:
        return c.execute(text(
            "SELECT credentials_encrypted FROM email_accounts "
            "WHERE id = CAST(:a AS uuid)"), {"a": account}).scalar_one()


@_DB_GATE
async def test_a_move_stores_the_new_provider_id(world, monkeypatch) -> None:
    mid = _seed(world, is_read=True)
    _mover(monkeypatch, "pm-after-move")
    await _patch(world, mid, m.MessageUpdateModel(folder="archive"))
    assert _pmid_and_folder(world.admin, mid) == ("pm-after-move", "archive")


@_DB_GATE
async def test_a_refused_rekey_keeps_the_move_and_answers(world, monkeypatch) -> None:
    """Review round 1, P2. A sweep stored the new id first, so the unique key
    refuses the re-key. The savepoint keeps the rest of the block: here, the
    token that the sign-in rotated. Without it, the block rolled back."""
    mid = _seed(world, is_read=True)
    taken = _seed(world, is_read=True)
    taken_pid, _folder = _pmid_and_folder(world.admin, taken)
    old_pid, _folder = _pmid_and_folder(world.admin, mid)
    _mover(monkeypatch, taken_pid, rotates=True)

    msg = await _patch(world, mid, m.MessageUpdateModel(folder="archive"))

    assert msg.folder == "archive"
    assert _pmid_and_folder(world.admin, mid) == (old_pid, "archive")
    assert _creds(world.admin, world.account) == 'enc:{"token": "rotated"}'


@_DB_GATE
async def test_mark_as_unread_sticks(world) -> None:
    """Review round 1, P1, on a real database: the answer of the PATCH must not
    mark the mail read again after the write committed."""
    mid = _seed(world, is_read=True)
    msg = await _patch(world, mid, m.MessageUpdateModel(is_read=False))

    assert msg.is_read is False
    assert _flags_of(world.admin, mid) == (False, False)
