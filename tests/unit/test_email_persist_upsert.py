"""Unit tests for the shared message-upsert helper (C1 consolidation).

These are DB-free: a fake session records the ``execute(text, params)`` calls so
we can assert the emitted SQL + binds without a live Postgres (the DB-touching
suites are slow/flaky on Windows). They pin the behaviour every ingest path now
shares — conflict mode, unsubscribe-link preservation, body truncation, and
attachment persistence.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from email_ingestion.persist import (
    MAX_BODY_TEXT_BYTES,
    truncate_body,
    upsert_message,
)


@dataclass
class _Addr:
    name: str = "Jane"
    email: str = "jane@example.com"


@dataclass
class _Att:
    filename: str = "f.pdf"
    mime_type: str = "application/pdf"
    size_bytes: int = 10
    provider_attachment_id: str = "att-1"


@dataclass
class _Msg:
    provider_message_id: str = "pm-1"
    internet_message_id: str | None = None
    thread_id: str | None = "t-1"
    folder: str = "INBOX"
    labels: list = field(default_factory=list)
    from_address: Any = field(default_factory=_Addr)
    to_addresses: list = field(default_factory=list)
    cc_addresses: list = field(default_factory=list)
    bcc_addresses: list = field(default_factory=list)
    subject: str = "hi"
    body_text: str = "body"
    body_html: str | None = None
    snippet: str = "snip"
    has_attachments: bool = False
    attachments: list = field(default_factory=list)
    is_read: bool = False
    is_starred: bool = False
    is_flagged: bool = False
    importance: str = "normal"
    categories: list = field(default_factory=list)
    categories_authoritative: bool = False
    unsubscribe_link: str | None = "https://unsub.example/x"
    received_at: Any = None


class _FakeDB:
    """Records every execute() as (rendered_sql, params) — no real DB."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def execute(self, sql: Any, params: dict | None = None):
        self.calls.append((str(sql), params or {}))
        return None


async def test_update_mode_emits_do_update_and_preserves_unsubscribe():
    db = _FakeDB()
    await upsert_message(db, "acct-1", _Msg())
    sql, params = db.calls[0]
    assert "INSERT INTO email_messages" in sql
    assert "DO UPDATE SET" in sql
    # The link is bound AND preserved (COALESCE) rather than clobbered on re-sync.
    assert "unsubscribe_link = COALESCE(EXCLUDED.unsubscribe_link" in sql
    assert params["unsubscribe_link"] == "https://unsub.example/x"
    assert params["account_id"] == "acct-1"
    assert params["provider_id"] == "pm-1"


async def test_resync_never_clobbers_labels_a_provider_cannot_report():
    """The regression that made the Inbox Cleaner look empty.

    ``email_messages.categories`` is where the RULE ENGINE writes its labels
    (Newsletter / Marketing / Notification / …) and what every category chip,
    quick filter and the cleaner read. A provider that simply doesn't report
    categories — generic IMAP, and Gmail before it resolved user-label IDs to
    names — sends ``{}`` on every tick. The old unconditional
    ``categories = EXCLUDED.categories`` therefore erased the lot on the next
    re-sync, permanently: ``rules_processed_at`` is already stamped, so the
    rules never re-applied them. The replace is now opt-in per provider.
    """
    db = _FakeDB()
    await upsert_message(db, "acct-1", _Msg())
    sql, params = db.calls[0]
    assert "categories = CASE WHEN :categories_authoritative" in sql
    assert "ELSE email_messages.categories END" in sql
    assert params["categories_authoritative"] is False


async def test_a_round_tripping_provider_stays_authoritative():
    """Gmail (with its label map loaded) and Outlook do report the full label
    set, so an empty list genuinely means "the user cleared them" and must
    propagate — otherwise a label removed upstream could never be removed."""
    db = _FakeDB()
    await upsert_message(
        db, "acct-1", _Msg(categories=["Newsletter"], categories_authoritative=True)
    )
    _, params = db.calls[0]
    assert params["categories_authoritative"] is True
    assert params["categories"] == ["Newsletter"]


async def test_nothing_mode_emits_do_nothing_only():
    db = _FakeDB()
    await upsert_message(db, "acct-1", _Msg(), on_conflict="nothing")
    sql, _ = db.calls[0]
    assert "DO NOTHING" in sql
    assert "DO UPDATE" not in sql


async def test_attachments_are_persisted_after_the_message():
    db = _FakeDB()
    await upsert_message(
        db, "acct-1", _Msg(has_attachments=True, attachments=[_Att()])
    )
    assert len(db.calls) == 2  # message, then attachment
    att_sql, att_params = db.calls[1]
    assert "INSERT INTO email_attachments" in att_sql
    assert att_params["provider_attachment_id"] == "att-1"
    assert att_params["provider_id"] == "pm-1"


async def test_body_is_truncated_for_every_ingest_path():
    big = "x" * (MAX_BODY_TEXT_BYTES + 5000)
    db = _FakeDB()
    await upsert_message(db, "acct-1", _Msg(body_text=big))
    _, params = db.calls[0]
    assert params["body_text"].endswith(" ... [truncated]")
    assert len(params["body_text"].encode("utf-8")) <= MAX_BODY_TEXT_BYTES


async def test_insert_carries_internet_message_id_and_refreshes_it():
    db = _FakeDB()
    await upsert_message(db, "acct-1", _Msg(internet_message_id="<abc@x>"))
    # The INSERT is the last call, with a reclaim UPDATE before it or not.
    sql, params = db.calls[-1]
    assert "internet_message_id" in sql  # column is inserted
    assert params["internet_message_id"] == "<abc@x>"
    # On conflict the id is refreshed but never wiped by a NULL from a provider
    # that omits it.
    assert ("internet_message_id = COALESCE(EXCLUDED.internet_message_id"
            in sql)


async def test_a_rekeyed_message_reclaims_its_row_instead_of_ghosting():
    """Outlook re-keying a message (new provider id, same Message-ID) must update
    its existing row, not insert a duplicate ghost. The caller passes
    ``reclaim=True``, the attribute of a provider that re-keys (D-EM-34)."""
    db = _FakeDB()
    await upsert_message(
        db, "acct-1", _Msg(provider_message_id="pm-NEW",
                           internet_message_id="<abc@x>"),
        reclaim=True)
    reclaim_sql, reclaim_params = db.calls[0]
    assert reclaim_sql.startswith("UPDATE email_messages SET provider_message_id")
    assert reclaim_params == {
        "new_pmid": "pm-NEW", "aid": "acct-1", "imid": "<abc@x>"}
    # The reclaim is collision-free: it only renames when nothing already holds
    # the new id and exactly one row carries this Message-ID.
    assert "NOT EXISTS" in reclaim_sql
    assert "COUNT(*)" in reclaim_sql and ") = 1" in reclaim_sql
    # ...and the INSERT still runs after it.
    assert "INSERT INTO email_messages" in db.calls[1][0]


@pytest.mark.parametrize("kwargs", [{"reclaim": False}, {}],
                         ids=["reclaim_false", "no_keyword"])
async def test_a_rekeyed_message_without_reclaim_runs_no_update(kwargs):
    """The twin (WS-17 EM-G1, D-EM-34). A provider that never re-keys, as
    Gmail does, passes ``reclaim=False``, and the default is false too. The
    upsert then runs no UPDATE, so two messages with one Message-ID keep two
    rows."""
    db = _FakeDB()
    await upsert_message(
        db, "acct-1", _Msg(provider_message_id="pm-NEW",
                           internet_message_id="<abc@x>"),
        **kwargs)
    assert "INSERT INTO email_messages" in db.calls[0][0]
    assert all(not sql.startswith("UPDATE email_messages")
               for sql, _ in db.calls)


async def test_no_reclaim_without_a_message_id():
    db = _FakeDB()
    await upsert_message(db, "acct-1", _Msg(internet_message_id=None),
                         reclaim=True)
    # First (and only pre-attachment) call is the INSERT — no reclaim UPDATE.
    assert "INSERT INTO email_messages" in db.calls[0][0]
    assert not db.calls[0][0].startswith("UPDATE email_messages")


async def test_no_reclaim_on_the_insert_only_path():
    # Inbound (on_conflict='nothing') is authoritative-on-arrival — it must not
    # reach back and reclaim/rename an existing row, even with reclaim=True.
    db = _FakeDB()
    await upsert_message(
        db, "acct-1", _Msg(internet_message_id="<abc@x>"),
        on_conflict="nothing", reclaim=True)
    assert "INSERT INTO email_messages" in db.calls[0][0]
    assert all(not sql.startswith("UPDATE email_messages")
               for sql, _ in db.calls)


def test_truncate_body_passthrough_and_cap():
    assert truncate_body(None, 100) is None
    assert truncate_body("", 100) == ""
    assert truncate_body("short", 100) == "short"
    out = truncate_body("y" * 500, 100)
    assert out.endswith(" ... [truncated]")
    assert len(out.encode("utf-8")) <= 100
