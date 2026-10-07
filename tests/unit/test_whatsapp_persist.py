"""Unit tests for the WhatsApp persist path — the single idempotent write.

No real database: a ``FakeDB`` captures the executed statements + params and
returns a canned RETURNING row, so the Python-side derivations (the 24h window,
direction→send_regime, the chat/message fan-out) are pinned without Postgres.
"""

from __future__ import annotations

from datetime import UTC, datetime

from whatsapp_ingestion import persist
from whatsapp_ingestion.providers.base import (
    SyncResult,
    WhatsAppContact,
    WhatsAppMedia,
    WhatsAppMessage,
)

_SENT = datetime(2026, 7, 23, 9, 0, tzinfo=UTC)


class _Result:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _Row:
    id = "chat-uuid-1"


class FakeDB:
    """Captures (sql_text, params) and returns a fixed chat id on RETURNING."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    async def execute(self, statement, params=None):
        self.calls.append((str(statement), params or {}))
        return _Result(_Row())

    def statements(self) -> str:
        return "\n".join(s for s, _ in self.calls)


def test_service_window_is_24h_after_inbound() -> None:
    assert persist.service_window_expiry(_SENT) == datetime(
        2026, 7, 24, 9, 0, tzinfo=UTC)
    assert persist.service_window_expiry(None) is None


def test_inbound_message_params_have_no_send_regime() -> None:
    msg = WhatsAppMessage(
        wa_message_id="wamid.1", wa_chat_id="91999", kind="text",
        sender_wa_id="91999", sender_name="Rajesh", body_text="hi", sent_at=_SENT)
    p = persist._message_params("acc", "chat-uuid-1", msg, "in")
    assert p["direction"] == "in"
    assert p["send_regime"] is None          # inbound has no regime
    assert p["kind"] == "text"
    assert '"name": "Rajesh"' in p["sender"]  # sender JSON carries the name


def test_outbound_message_params_default_to_session_regime() -> None:
    msg = WhatsAppMessage(
        wa_message_id="wamid.2", wa_chat_id="91999", body_text="ok", sent_at=_SENT)
    p = persist._message_params("acc", "chat-uuid-1", msg, "out")
    assert p["direction"] == "out"
    assert p["send_regime"] == "session"     # free-form reply inside the window


async def test_upsert_chat_sets_window_only_for_inbound() -> None:
    db = FakeDB()
    msg = WhatsAppMessage(
        wa_message_id="wamid.1", wa_chat_id="91999", chat_kind="dm",
        sender_name="Rajesh", sent_at=_SENT)

    await persist.upsert_chat(db, "acc", msg, direction="in")
    assert db.calls[-1][1]["window_expires"] == datetime(2026, 7, 24, 9, 0, tzinfo=UTC)

    db2 = FakeDB()
    await persist.upsert_chat(db2, "acc", msg, direction="out")
    assert db2.calls[-1][1]["window_expires"] is None  # our send never opens it


async def test_persist_sync_result_fans_out_contacts_chats_messages() -> None:
    db = FakeDB()
    result = SyncResult(
        contacts=[WhatsAppContact(wa_id="91999", phone_number="91999", name="Rajesh")],
        messages=[
            WhatsAppMessage(wa_message_id="wamid.1", wa_chat_id="91999",
                            sender_wa_id="91999", body_text="PO attached",
                            sent_at=_SENT),
            WhatsAppMessage(wa_message_id="wamid.2", wa_chat_id="91999",
                            sender_wa_id="91999", kind="document",
                            media=WhatsAppMedia(wa_media_id="M1",
                                                mime_type="application/pdf"),
                            sent_at=_SENT),
        ],
    )
    counts = await persist.persist_sync_result(db, "acc", result)
    # Both messages, one shared chat, and no history (WA-C3 P8).
    assert counts == {"messages": 2, "chats": 1, "history_messages": 0}
    sql = db.statements()
    assert "INSERT INTO wa_contacts" in sql
    assert "INSERT INTO wa_chats" in sql
    assert "INSERT INTO wa_messages" in sql
    assert "INSERT INTO wa_media" in sql            # the document's media row


async def test_persist_skips_messages_without_an_id() -> None:
    db = FakeDB()
    result = SyncResult(messages=[
        WhatsAppMessage(wa_message_id="", wa_chat_id="91999"),  # dropped
        WhatsAppMessage(wa_message_id="wamid.ok", wa_chat_id="91999", sent_at=_SENT),
    ])
    counts = await persist.persist_sync_result(db, "acc", result)
    assert counts["messages"] == 1


def test_body_truncation_marks_oversized_text() -> None:
    big = "x" * (persist.MAX_BODY_TEXT_BYTES + 100)
    out = persist._truncate(big, persist.MAX_BODY_TEXT_BYTES)
    assert out.endswith("[truncated]")
    assert persist._truncate(None, 10) is None


async def test_voice_media_lands_pending_for_transcription() -> None:
    db = FakeDB()
    voice = WhatsAppMessage(
        wa_message_id="wamid.v", wa_chat_id="91999", sender_wa_id="91999",
        kind="voice", media=WhatsAppMedia(wa_media_id="V1", mime_type="audio/ogg"),
        sent_at=_SENT)
    await persist.upsert_message(db, "acc", "chat-uuid-1", voice, direction="in")
    media_call = next(c for c in db.calls if "INSERT INTO wa_media" in c[0])
    assert media_call[1]["transcription_status"] == "pending"


async def test_document_media_has_no_transcription_status() -> None:
    db = FakeDB()
    doc = WhatsAppMessage(
        wa_message_id="wamid.d", wa_chat_id="91999", sender_wa_id="91999",
        kind="document",
        media=WhatsAppMedia(wa_media_id="D1", mime_type="application/pdf"),
        sent_at=_SENT)
    await persist.upsert_message(db, "acc", "chat-uuid-1", doc, direction="in")
    media_call = next(c for c in db.calls if "INSERT INTO wa_media" in c[0])
    assert media_call[1]["transcription_status"] is None


def test_is_transcribable_predicate() -> None:
    assert persist.is_transcribable("voice", "audio/ogg") is True
    assert persist.is_transcribable("audio", None) is True
    assert persist.is_transcribable("document", "audio/mpeg") is True  # by mime
    assert persist.is_transcribable("image", "image/jpeg") is False
    assert persist.is_transcribable("text", None) is False


# ── WS-20 WA-C3: history, echoes and statuses (spec §12.4.1) ─────────────────

def _history(wamid: str, direction: str, **kw) -> WhatsAppMessage:
    return WhatsAppMessage(
        wa_message_id=wamid, wa_chat_id="16505551234", direction=direction,
        sender_wa_id="16505551234", body_text="hi", sent_at=_SENT,
        from_history=True, **kw)


async def test_a_history_message_opens_no_service_window() -> None:
    """P7: a history message is old by construction. Even an inbound one
    must not open the free-form window."""
    db = FakeDB()
    await persist.upsert_chat(db, "acc", _history("wamid.h", "in"), direction="in")
    assert db.calls[-1][1]["window_expires"] is None


def test_a_history_row_and_an_echo_store_no_send_regime() -> None:
    """P9: Metorite did not send these, so no regime is known."""
    out_history = _history("wamid.ho", "out")
    echo = WhatsAppMessage(wa_message_id="wamid.e", wa_chat_id="1650",
                           direction="out", is_echo=True, sent_at=_SENT)
    for msg in (out_history, echo):
        p = persist._message_params("acc", "chat-uuid-1", msg, "out")
        assert p["send_regime"] is None, msg.wa_message_id


def test_a_history_row_carries_its_flag_and_status() -> None:
    p = persist._message_params(
        "acc", "chat-uuid-1", _history("wamid.h", "in", delivery_status="read"),
        "in")
    assert p["from_history"] is True
    assert p["delivery_status"] == "read"
    sql = persist._MESSAGE_INSERT
    # P8: the watermarks are set at insert, and only for a history row.
    assert "rules_processed_at" in sql and "commitment_checked_at" in sql


async def test_history_media_lands_skipped_not_pending() -> None:
    db = FakeDB()
    voice = _history("wamid.hv", "in", kind="voice",
                     media=WhatsAppMedia(wa_media_id="V9", mime_type="audio/ogg"))
    await persist.upsert_message(db, "acc", "chat-uuid-1", voice, direction="in")
    media_call = next(c for c in db.calls if "INSERT INTO wa_media" in c[0])
    assert media_call[1]["transcription_status"] == "skipped"


async def test_history_counts_apart_and_an_echo_counts_as_live() -> None:
    """P8: a batch of history alone must not fire `on_new_messages`, so it
    counts in its own key. An echo is live, so its promise still counts."""
    db = FakeDB()
    echo = WhatsAppMessage(wa_message_id="wamid.e", wa_chat_id="1650",
                           direction="out", is_echo=True, sent_at=_SENT)
    result = SyncResult(messages=[_history("wamid.h1", "in"),
                                  _history("wamid.h2", "out"), echo])
    counts = await persist.persist_sync_result(db, "acc", result)
    assert counts["history_messages"] == 2
    assert counts["messages"] == 1


def test_a_status_only_moves_forward() -> None:
    fwd = persist.status_moves_forward
    assert fwd(None, "sent") and fwd("sent", "delivered") and fwd("delivered", "read")
    assert fwd("read", "played") and fwd("pending", "sent")
    assert not fwd("read", "delivered")
    assert not fwd("delivered", "sent")
    assert not fwd("read", "read")
    # `failed` applies unless the row is read or played.
    assert fwd("sent", "failed") and fwd("delivered", "failed") and fwd(None, "failed")
    assert not fwd("read", "failed") and not fwd("played", "failed")
    # After a failure only proof of delivery moves the row.
    assert fwd("failed", "delivered") and not fwd("failed", "sent")
    assert not fwd("sent", None) and not fwd("sent", "deleted")
