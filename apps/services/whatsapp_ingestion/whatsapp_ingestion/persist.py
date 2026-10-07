"""Shared persistence for normalized WhatsApp messages — the idempotent inbound
write path, mirroring ``email_ingestion.persist``.

Every inbound event (webhook batch, bridge batch, history-import page) lands here,
so a schema change touches one place and the ingest paths never drift.
``whatsapp_ingestion`` is the LOWER layer: this module imports nothing from the
gateway. (Outbound sends write their own row in the gateway send route, which
records the send regime the transport chose.)

Dedupe key is the message id (``wa_messages.wa_message_id``), which is stable, so
re-delivery of the same event (providers retry at-least-once) is a no-op UPDATE
rather than a duplicate row. As in the email upsert, an existing row's
``categories`` and ``rules_processed_at`` are PRESERVED on conflict — those are the
rule engine's, not the transport's, and a re-delivered message must not reset them
(which would re-run automation on an already-processed message).

WS-20 WA-C3 (``whatsapp_message_manager.md`` §12.4.1) adds four things here, all
in the caller's one bound session:

* a **history** row opens no service window (P7), stores no send regime (P9),
  and lands with both automation watermarks set (P8). Its media lands
  ``skipped``, so the transcription pass never reads it.
* a **status** only moves forward (P6). ``status_moves_forward`` is the rule,
  and ``_forward_sql`` writes the same rule as SQL from the same table.
* an **address-book** change upserts or flags a ``wa_contacts`` row and never
  deletes one (P10).
* the **progress** of the import lands on the account (P11).
"""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import text

from whatsapp_ingestion.providers.base import (
    HistoryProgress,
    SyncResult,
    WhatsAppContact,
    WhatsAppContactChange,
    WhatsAppMessage,
    WhatsAppStatus,
)
from whatsapp_ingestion.providers.webhook import normalize_delivery_status

# The Cloud API customer-service window: free-form replies are allowed for 24h
# after the customer's last inbound message.
SERVICE_WINDOW = timedelta(hours=24)

MAX_BODY_TEXT_BYTES = 64 * 1024  # WhatsApp bodies are short; cap defensively.

# Media we can turn into text — voice notes (the dominant medium for Indian
# dealers) and any audio attachment. The canonical predicate lives here (the
# lower layer) so the ingest path and the gateway transcription pass never drift.
_VOICE_KINDS = frozenset({"voice", "audio"})
_AUDIO_MIME_PREFIX = "audio/"

#: The fixed text of a declined history import (Meta code 2593109, P11).
HISTORY_DECLINED_ERROR = (
    "History sharing is off in the WhatsApp Business app, so Metorite cannot "
    "import older chats. Turn it on in the app, then disconnect this number "
    "and connect it again.")

# ── delivery status: the one order (P6) ──────────────────────────────────────
#
# A status only moves forward: pending < sent < delivered < read < played.
# `failed` applies unless the row is read or played. A row that failed moves
# again only on proof of delivery (delivered, read or played), so a late `sent`
# cannot hide a failure. `failed` takes rank 3 for that reason.
_STATUS_RANK = {
    "pending": 1, "sent": 2, "failed": 3, "delivered": 4, "read": 5, "played": 6,
}
_FAILED = "failed"
_FINAL_OVER_FAILED = ("read", "played", "failed")


def status_moves_forward(current: str | None, new: str | None) -> bool:
    """True when ``new`` may replace ``current`` as ``delivery_status``. Pure,
    and the same rule as ``_forward_sql``."""
    if new not in _STATUS_RANK:
        return False
    if new == _FAILED:
        return (current or "") not in _FINAL_OVER_FAILED
    return _STATUS_RANK[new] > _STATUS_RANK.get(current or "", 0)


def _rank_sql(expr: str) -> str:
    whens = " ".join(f"WHEN '{k}' THEN {v}" for k, v in _STATUS_RANK.items())
    return f"(CASE {expr} {whens} ELSE 0 END)"


def _forward_sql(new: str, current: str) -> str:
    """``status_moves_forward(current, new)`` as a SQL predicate, built from
    the same table so the two cannot drift."""
    final = ", ".join(f"'{s}'" for s in _FINAL_OVER_FAILED)
    return (
        f"(({new} = '{_FAILED}' AND COALESCE({current}, '') NOT IN ({final})) "
        f"OR ({new} <> '{_FAILED}' AND {_rank_sql(new)} > {_rank_sql(current)}))"
    )


def is_transcribable(kind: str | None, mime: str | None) -> bool:
    """True when a media attachment is a voice note / audio we can transcribe.
    Pure — used to mark media 'pending' at ingest and to gate the STT pass."""
    return (kind or "") in _VOICE_KINDS or (mime or "").startswith(_AUDIO_MIME_PREFIX)


def service_window_expiry(sent_at: Any) -> Any:
    """The instant the 24h window closes for an inbound message, or None.

    Pure so the window rule is unit-testable without a database. Only inbound
    (customer) messages open the window; the caller passes ``sent_at`` only for
    inbound.
    """
    if sent_at is None:
        return None
    return sent_at + SERVICE_WINDOW


def _truncate(value: str | None, max_bytes: int) -> str | None:
    if not value:
        return value
    encoded = value.encode("utf-8", errors="replace")
    if len(encoded) <= max_bytes:
        return value
    marker = b" ... [truncated]"
    cut = max_bytes - len(marker)
    while cut > 0 and (encoded[cut] & 0xC0) == 0x80:
        cut -= 1
    return encoded[:cut].decode("utf-8", errors="replace") + marker.decode()


# ── chats ────────────────────────────────────────────────────────────────────

def _chat_name(msg: WhatsAppMessage) -> str:
    """Best display name for the chat this message belongs to."""
    if msg.chat_kind == "group":
        return msg.group_subject or ""
    return msg.sender_name or ""


_CHAT_UPSERT = """INSERT INTO wa_chats
    (id, account_id, wa_chat_id, kind, name, last_message_at,
     service_window_expires_at)
  VALUES
    (:id, :account_id, :wa_chat_id, :kind, :name, :last_message_at,
     :window_expires)
  ON CONFLICT (account_id, wa_chat_id) DO UPDATE SET
    kind = EXCLUDED.kind,
    name = COALESCE(NULLIF(EXCLUDED.name, ''), wa_chats.name),
    last_message_at = GREATEST(
        wa_chats.last_message_at, EXCLUDED.last_message_at),
    -- Only an inbound message carries a window; keep the latest expiry, never
    -- shrink it (an out-of-order older inbound must not close a live window).
    service_window_expires_at = GREATEST(
        wa_chats.service_window_expires_at, EXCLUDED.service_window_expires_at),
    updated_at = now()
  RETURNING id
"""


async def upsert_chat(
    db: Any, account_id: str, msg: WhatsAppMessage, *, direction: str
) -> str:
    """Ensure the chat row exists and is current; return its UUID (as str).

    A history message opens no window at any age (WA-C3 P7). The customer
    wrote it before the connect, so it proves nothing about the window now.
    """
    opens_window = direction == "in" and not msg.from_history
    window = service_window_expiry(msg.sent_at) if opens_window else None
    result = await db.execute(text(_CHAT_UPSERT), {
        "id": str(uuid4()),
        "account_id": account_id,
        "wa_chat_id": msg.wa_chat_id,
        "kind": msg.chat_kind or "dm",
        "name": _chat_name(msg),
        "last_message_at": msg.sent_at,
        "window_expires": window,
    })
    row = result.fetchone()
    return str(row.id) if row else ""


# ── contacts ──────────────────────────────────────────────────────────────────

# A live profile name never replaces the name of an address-book contact
# (WA-C3 P10). The member typed that name in the app, and it wins.
_CONTACT_UPSERT = """INSERT INTO wa_contacts
    (id, account_id, phone_number, wa_id, display_name)
  VALUES (:id, :account_id, :phone_number, :wa_id, :display_name)
  ON CONFLICT (account_id, phone_number) DO UPDATE SET
    display_name = CASE
        WHEN wa_contacts.in_address_book IS TRUE
             AND COALESCE(wa_contacts.display_name, '') <> ''
        THEN wa_contacts.display_name
        ELSE COALESCE(NULLIF(EXCLUDED.display_name, ''), wa_contacts.display_name)
    END,
    wa_id = COALESCE(EXCLUDED.wa_id, wa_contacts.wa_id),
    updated_at = now()
"""

_CONTACT_ADD = """INSERT INTO wa_contacts
    (id, account_id, phone_number, wa_id, display_name, in_address_book)
  VALUES (:id, :account_id, :phone_number, :phone_number, :display_name, true)
  ON CONFLICT (account_id, phone_number) DO UPDATE SET
    display_name = COALESCE(
        NULLIF(EXCLUDED.display_name, ''), wa_contacts.display_name),
    wa_id = COALESCE(wa_contacts.wa_id, EXCLUDED.wa_id),
    in_address_book = true,
    updated_at = now()
"""

# A remove keeps the row, its name, its category and its entity_ref (P10).
_CONTACT_REMOVE = """UPDATE wa_contacts
  SET in_address_book = false, updated_at = now()
  WHERE account_id = :account_id AND phone_number = :phone_number
"""


async def upsert_contact(
    db: Any, account_id: str, contact: WhatsAppContact
) -> None:
    """Insert/update a contact identity (phone → name), never clobbering a known
    name with an empty one."""
    phone = contact.phone_number or contact.wa_id
    if not phone:
        return
    await db.execute(text(_CONTACT_UPSERT), {
        "id": str(uuid4()),
        "account_id": account_id,
        "phone_number": phone,
        "wa_id": contact.wa_id or None,
        "display_name": contact.name or "",
    })


async def apply_contact_change(
    db: Any, account_id: str, change: WhatsAppContactChange
) -> None:
    """Apply one ``smb_app_state_sync`` add or remove (WA-C3 P10)."""
    if not change.phone_number:
        return
    if change.action == "add":
        await db.execute(text(_CONTACT_ADD), {
            "id": str(uuid4()),
            "account_id": account_id,
            "phone_number": change.phone_number,
            "display_name": change.full_name or change.first_name or "",
        })
    elif change.action == "remove":
        await db.execute(text(_CONTACT_REMOVE), {
            "account_id": account_id, "phone_number": change.phone_number,
        })


# ── messages ──────────────────────────────────────────────────────────────────

_MESSAGE_INSERT = f"""INSERT INTO wa_messages
    (id, account_id, chat_id, wa_message_id, direction, sender, kind,
     body_text, quoted_wa_message_id, mentions, send_regime, template_name,
     sent_at, synced_at, delivery_status, from_history, rules_processed_at,
     commitment_checked_at)
  VALUES
    (:id, :account_id, :chat_id, :wa_message_id, :direction, :sender, :kind,
     :body_text, :quoted, :mentions, :send_regime, :template_name,
     :sent_at, now(), :delivery_status, :from_history,
     -- WA-C3 P8: a history row is not new mail for the rules or the
     -- commitment pass, so both watermarks are set when it lands.
     CASE WHEN CAST(:from_history AS boolean) THEN now() END,
     CASE WHEN CAST(:from_history AS boolean) THEN now() END)
  ON CONFLICT (account_id, wa_message_id) DO UPDATE SET
    -- Refresh transport fields on re-delivery, but NEVER touch the rule engine's
    -- columns (categories / rules_processed_at / commitment_checked_at) or
    -- `from_history`, or a re-delivered event would re-run automation on
    -- already-processed messages. Same discipline as the email upsert's
    -- categories guard.
    body_text = COALESCE(NULLIF(EXCLUDED.body_text, ''), wa_messages.body_text),
    kind = EXCLUDED.kind,
    sender = EXCLUDED.sender,
    -- A status only moves forward (WA-C3 P6).
    delivery_status = CASE
        WHEN EXCLUDED.delivery_status IS NOT NULL
             AND {_forward_sql("EXCLUDED.delivery_status", "wa_messages.delivery_status")}
        THEN EXCLUDED.delivery_status
        ELSE wa_messages.delivery_status
    END,
    updated_at = now()
"""

# One media row for each (message, Meta media id). Before WA-C3 a redelivered
# batch added a second row, because the row id is a fresh uuid4 and so
# `ON CONFLICT` never fired. A history chunk is redelivered as freely as any.
_MEDIA_INSERT = """INSERT INTO wa_media
    (id, message_id, wa_media_id, mime_type, filename, size_bytes, sha256,
     transcription_status)
  SELECT :id, m.id, :wa_media_id, :mime_type, :filename, :size_bytes, :sha256,
         :transcription_status
  FROM wa_messages m
  WHERE m.account_id = :account_id AND m.wa_message_id = :wa_message_id
    AND NOT EXISTS (
      SELECT 1 FROM wa_media x
      WHERE x.message_id = m.id AND x.wa_media_id = :wa_media_id)
  ON CONFLICT DO NOTHING
"""

# A live status updates a row that exists. It never creates one (P6).
_STATUS_UPDATE = f"""UPDATE wa_messages
  SET delivery_status = CAST(:status AS text), updated_at = now()
  WHERE account_id = :account_id AND wa_message_id = :wa_message_id
    AND {_forward_sql("CAST(:status AS text)", "delivery_status")}
"""


def _has_known_regime(msg: WhatsAppMessage, direction: str) -> bool:
    """Only a message that Metorite's own transport sent has a known send
    regime. An inbound row, a history row and an echo have none (P9)."""
    return direction != "in" and not msg.from_history and not msg.is_echo


def _message_params(
    account_id: str, chat_id: str, msg: WhatsAppMessage, direction: str
) -> dict[str, Any]:
    """Bind params for one message row (generates the row id via uuid4)."""
    return {
        "id": str(uuid4()),
        "account_id": account_id,
        "chat_id": chat_id,
        "wa_message_id": msg.wa_message_id,
        "direction": direction,
        "sender": json.dumps({
            "wa_id": msg.sender_wa_id, "name": msg.sender_name,
        }),
        "kind": msg.kind or "text",
        "body_text": _truncate(msg.body_text, MAX_BODY_TEXT_BYTES),
        "quoted": msg.quoted_wa_message_id,
        "mentions": list(msg.mentions or []),
        "send_regime": "session" if _has_known_regime(msg, direction) else None,
        "template_name": None,
        "sent_at": msg.sent_at,
        "delivery_status": msg.delivery_status,
        "from_history": bool(msg.from_history),
    }


def _transcription_status(msg: WhatsAppMessage) -> str | None:
    """Voice/audio lands 'pending' so the STT pass can find it. History
    media lands 'skipped' (WA-C3 P8), and other media has no lifecycle."""
    if msg.media is None or not is_transcribable(msg.kind, msg.media.mime_type):
        return None
    return "skipped" if msg.from_history else "pending"


async def upsert_message(
    db: Any, account_id: str, chat_id: str, msg: WhatsAppMessage, *,
    direction: str = "in",
) -> None:
    """Insert or update one normalized message + its media metadata.

    Idempotent on ``(account_id, wa_message_id)``. The caller owns the
    transaction (``db.commit()``).
    """
    await db.execute(
        text(_MESSAGE_INSERT), _message_params(account_id, chat_id, msg, direction)
    )
    if msg.media and msg.media.wa_media_id:
        await db.execute(text(_MEDIA_INSERT), {
            "id": str(uuid4()),
            "account_id": account_id,
            "wa_message_id": msg.wa_message_id,
            "wa_media_id": msg.media.wa_media_id,
            "mime_type": msg.media.mime_type,
            "filename": msg.media.filename,
            "size_bytes": msg.media.size_bytes,
            "sha256": msg.media.sha256,
            "transcription_status": _transcription_status(msg),
        })


async def apply_status(db: Any, account_id: str, status: WhatsAppStatus) -> None:
    """Move one message's ``delivery_status`` forward (WA-C3 P6). A status for
    a message id with no row changes nothing."""
    value = normalize_delivery_status(status.status)
    if value is None or not status.wa_message_id:
        return
    await db.execute(text(_STATUS_UPDATE), {
        "account_id": account_id,
        "wa_message_id": status.wa_message_id,
        "status": value,
    })


# ── the history import on the account (P11) ─────────────────────────────────

# `history_import_phase` is the highest Meta phase seen, plus 1. A higher phase
# replaces the progress, the same phase keeps the GREATEST, and a lower phase
# changes nothing. Progress 100 of a phase that counts means done. Every
# right-hand side reads the row as it was before this UPDATE.
_PROGRESS_UPDATE = """UPDATE wa_accounts SET
    history_import_progress = CASE
        WHEN CAST(:phase AS integer) > COALESCE(history_import_phase, 0)
        THEN CAST(:progress AS smallint)
        WHEN CAST(:phase AS integer) = COALESCE(history_import_phase, 0)
        THEN GREATEST(COALESCE(history_import_progress, 0),
                      CAST(:progress AS smallint))
        ELSE history_import_progress
    END,
    history_import_phase = GREATEST(
        COALESCE(history_import_phase, 0), CAST(:phase AS integer)),
    history_sync_state = CASE
        WHEN CAST(:progress AS integer) >= 100
             AND CAST(:phase AS integer) >= COALESCE(history_import_phase, 0)
        THEN 'complete'
        ELSE history_sync_state
    END,
    updated_at = now()
  WHERE id = :account_id
"""

_DECLINE_UPDATE = """UPDATE wa_accounts
  SET history_sync_state = 'declined', history_sync_error = :error,
      updated_at = now()
  WHERE id = :account_id
"""


async def apply_history_progress(
    db: Any, account_id: str, progress: HistoryProgress
) -> None:
    """Write one history item's phase and progress onto the account."""
    await db.execute(text(_PROGRESS_UPDATE), {
        "account_id": account_id,
        "phase": progress.phase + 1,
        "progress": max(0, min(100, progress.progress)),
    })


async def persist_sync_result(
    db: Any, account_id: str, result: SyncResult
) -> dict[str, int]:
    """Persist a parsed webhook/import batch: contacts, then each message's chat
    then the message itself, then statuses and the history progress.

    Returns ``{"messages": n, "chats": m, "history_messages": h}``. A history
    row counts in ``history_messages`` and never in ``messages``, so a batch of
    history alone does not fire ``on_new_messages`` (WA-C3 P8). An echo is
    live, so it counts in ``messages``.

    The caller owns the transaction, so every write here lands in the one
    bound session. ``direction`` comes from each message (the parser sets
    ``out`` for an echo and for a history message of the business).
    """
    for contact in result.contacts:
        await upsert_contact(db, account_id, contact)
    for change in result.contact_changes:
        await apply_contact_change(db, account_id, change)

    chats_seen: set[str] = set()
    messages = 0
    history_messages = 0
    for msg in result.messages:
        if not msg.wa_message_id:
            continue
        direction = msg.direction or "in"
        chat_id = await upsert_chat(db, account_id, msg, direction=direction)
        if not chat_id:
            continue
        chats_seen.add(chat_id)
        await upsert_message(db, account_id, chat_id, msg, direction=direction)
        if msg.from_history:
            history_messages += 1
        else:
            messages += 1

    for status in result.statuses:
        await apply_status(db, account_id, status)
    for progress in result.history_progress:
        await apply_history_progress(db, account_id, progress)
    if result.history_declined:
        await db.execute(text(_DECLINE_UPDATE), {
            "account_id": account_id, "error": HISTORY_DECLINED_ERROR,
        })

    return {"messages": messages, "chats": len(chats_seen),
            "history_messages": history_messages}
