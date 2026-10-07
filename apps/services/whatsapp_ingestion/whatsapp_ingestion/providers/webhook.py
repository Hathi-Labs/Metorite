"""Parse a Meta WhatsApp Business Cloud API webhook payload into normalized
:class:`SyncResult` data — the pure, transport-boundary function the gateway's
webhook receiver calls before handing off to persist.

Deliberately total: a malformed entry never raises, it lands in
``SyncResult.errors`` so one bad change in a batch can't drop the good ones.
Signature verification (``X-Hub-Signature-256``) is the receiver's job, not
this parser's — this only decodes an already-trusted body.

Cloud API payload shape (``object: whatsapp_business_account``)::

    entry[].changes[].value = {
        messaging_product, metadata{display_phone_number, phone_number_id},
        contacts[]{profile{name}, wa_id},
        messages[]{from, id, timestamp, type, <type-object>, context{id}},
        statuses[]{id, status, timestamp, recipient_id, errors[]},
    }

WS-20 WA-C3 (``whatsapp_message_manager.md`` §12.4.1 P5). ``parse_webhook``
dispatches on ``change.field``. A change with no field is ``messages``, which
is the shape of every body before WA-C3. A field that this module does not
know is ignored, with no error. The three coexistence fields:

* ``history`` — ``value.history[]`` holds ``{metadata{phase, chunk_order,
  progress}, threads[]{id, messages[]}}``. Each message gets
  ``from_history``, its chat is ``thread.id``, and it carries
  ``history_context.status``.
* ``smb_message_echoes`` — ``value.message_echoes[]``, a message that the
  business sent from the phone app. It is ``out``, and its chat is ``to``.
* ``smb_app_state_sync`` — ``value.state_sync[]``, a contact ``add`` or
  ``remove``.

Every phone number of a chat or a contact is reduced to its digits, to match
``wa_id``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from whatsapp_ingestion.providers.base import (
    HistoryProgress,
    SyncResult,
    WhatsAppContact,
    WhatsAppContactChange,
    WhatsAppMedia,
    WhatsAppMessage,
    WhatsAppStatus,
)

# Meta ``type`` value → our normalized ``kind``. Audio splits into voice vs audio
# on the ``voice`` flag inside the object; unknown types fall through to system.
_MEDIA_TYPES = {"image", "video", "audio", "document", "sticker"}

#: Meta's error code when the member turned history sharing off in the
#: WhatsApp Business app (Meta, "Onboard WhatsApp Business app users").
HISTORY_DECLINED_CODE = 2593109

#: Meta's status words, in the history context (upper case) and in a live
#: ``statuses[]`` callback (lower case), mapped to ``delivery_status``.
_DELIVERY_STATUSES = {
    "pending": "pending",
    "sent": "sent",
    "delivered": "delivered",
    "read": "read",
    "played": "played",
    "error": "failed",
    "failed": "failed",
}


def normalize_delivery_status(value: Any) -> str | None:
    """Meta's status word as a ``delivery_status`` value, or None for a word
    this module does not know. Pure."""
    if not isinstance(value, str):
        return None
    return _DELIVERY_STATUSES.get(value.strip().lower())


def _digits(value: Any) -> str:
    """The digits of a phone number, so ``+1 (650) 555-1234`` matches the
    ``wa_id`` ``16505551234``. A value with no digits stays as it is."""
    raw = str(value or "")
    digits = "".join(ch for ch in raw if ch.isdigit())
    return digits or raw


def _ts(value: Any) -> datetime | None:
    """Meta timestamps are unix-epoch seconds as a string. Return tz-aware UTC."""
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=UTC)
    except (ValueError, TypeError, OSError):
        return None


def _int(value: Any, default: int | None = None) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _message_kind(msg: dict[str, Any]) -> str:
    """Normalize Meta's ``type`` to our ``kind`` vocabulary."""
    mtype = msg.get("type") or "text"
    if mtype == "audio":
        # A voice note carries ``voice: true``; a plain audio file does not.
        return "voice" if (msg.get("audio") or {}).get("voice") else "audio"
    if mtype in _MEDIA_TYPES or mtype in (
        "text", "location", "contacts", "reaction",
    ):
        # 'contacts' (a shared vCard) normalizes to singular 'contact'.
        return "contact" if mtype == "contacts" else mtype
    # button / interactive / order / system / unknown → keep the row as system so
    # nothing is silently dropped; the raw payload is preserved for later.
    return "system"


def _body_and_media(
    msg: dict[str, Any], kind: str,
) -> tuple[str, WhatsAppMedia | None]:
    """Extract the display text (body or caption) and any media reference."""
    mtype = msg.get("type") or "text"

    if mtype == "text":
        return (msg.get("text") or {}).get("body", "") or "", None

    if mtype == "reaction":
        return (msg.get("reaction") or {}).get("emoji", "") or "", None

    if mtype == "location":
        loc = msg.get("location") or {}
        label = loc.get("name") or loc.get("address") or ""
        coords = f"{loc.get('latitude')},{loc.get('longitude')}"
        return (f"{label} ({coords})" if label else coords), None

    if mtype == "contacts":
        cards = msg.get("contacts") or []
        names = [
            ((c.get("name") or {}).get("formatted_name") or "").strip()
            for c in cards
        ]
        return ", ".join(n for n in names if n), None

    if mtype in _MEDIA_TYPES:
        obj = msg.get(mtype) or {}
        media = WhatsAppMedia(
            wa_media_id=obj.get("id", "") or "",
            mime_type=obj.get("mime_type") or "application/octet-stream",
            filename=obj.get("filename"),
            sha256=obj.get("sha256"),
        )
        # Captions live on image/video/document; audio/sticker have none.
        return obj.get("caption", "") or "", media

    return "", None


def _build_message(
    msg: dict[str, Any], *, wa_chat_id: str, chat_kind: str, direction: str,
    sender_wa_id: str, sender_name: str, from_history: bool = False,
    is_echo: bool = False, delivery_status: str | None = None,
) -> WhatsAppMessage:
    """One normalized message. The caller decides the chat and the side."""
    kind = _message_kind(msg)
    body, media = _body_and_media(msg, kind)
    context = msg.get("context") or {}
    return WhatsAppMessage(
        wa_message_id=msg.get("id", "") or "",
        wa_chat_id=wa_chat_id,
        direction=direction,
        kind=kind,
        sender_wa_id=sender_wa_id,
        sender_name=sender_name,
        body_text=body,
        quoted_wa_message_id=context.get("id"),
        media=media,
        chat_kind=chat_kind,
        sent_at=_ts(msg.get("timestamp")),
        raw=msg,
        from_history=from_history,
        is_echo=is_echo,
        delivery_status=delivery_status,
    )


def _parse_message(
    msg: dict[str, Any],
    contacts_by_wa_id: dict[str, WhatsAppContact],
) -> WhatsAppMessage:
    """Parse one inbound message object into a normalized message."""
    sender_wa_id = msg.get("from", "") or ""
    contact = contacts_by_wa_id.get(sender_wa_id)

    # Group vs DM: Meta marks group traffic with a group id under metadata/context
    # in the coexistence payloads. Absent that, a message is a DM keyed on the
    # sender's wa_id. We read the (evolving) ``group_id`` defensively.
    group_id = msg.get("group_id") or (msg.get("context") or {}).get("group_id")
    if group_id:
        chat_kind = "group"
        wa_chat_id = str(group_id)
    else:
        chat_kind = "dm"
        # Digits only, so the chat of a live message is the chat of an echo
        # to the same person (WA-C3 P5). Meta writes `from` as digits.
        wa_chat_id = _digits(sender_wa_id)

    return _build_message(
        msg, wa_chat_id=wa_chat_id, chat_kind=chat_kind, direction="in",
        sender_wa_id=sender_wa_id,
        sender_name=contact.name if contact else "",
    )


def _parse_status(st: dict[str, Any]) -> WhatsAppStatus:
    """Parse one delivery/read status callback."""
    errors = st.get("errors") or []
    err = None
    if errors:
        first = errors[0] or {}
        err = first.get("title") or first.get("message") or str(first)
    return WhatsAppStatus(
        wa_message_id=st.get("id", "") or "",
        recipient_wa_id=st.get("recipient_id", "") or "",
        status=st.get("status", "") or "",
        timestamp=_ts(st.get("timestamp")),
        error=err,
    )


def _scan_errors(errors: Any, result: SyncResult) -> None:
    """Read Meta's ``errors[]``. Code 2593109 is a decline of the history
    import. Any other error is kept in ``result.errors`` with its code and
    its title only, never message content."""
    for err in errors if isinstance(errors, list) else []:
        if not isinstance(err, dict):
            continue
        if err.get("code") == HISTORY_DECLINED_CODE:
            result.history_declined = True
        else:
            title = str(err.get("title") or "")[:120]
            result.errors.append(f"meta error {err.get('code')}: {title}")


def _parse_messages_change(value: dict[str, Any], result: SyncResult) -> None:
    """The ``messages`` field: live inbound messages, statuses and profiles."""
    contacts_by_wa_id: dict[str, WhatsAppContact] = {}
    for c in value.get("contacts", []) or []:
        wa_id = c.get("wa_id", "") or ""
        contact = WhatsAppContact(
            wa_id=wa_id,
            phone_number=wa_id,
            name=(c.get("profile") or {}).get("name", "") or "",
        )
        contacts_by_wa_id[wa_id] = contact
        result.contacts.append(contact)

    for msg in value.get("messages", []) or []:
        result.messages.append(_parse_message(msg, contacts_by_wa_id))

    for st in value.get("statuses", []) or []:
        result.statuses.append(_parse_status(st))


def _parse_history_change(
    value: dict[str, Any], business: str, result: SyncResult,
) -> None:
    """The ``history`` field: one chunk of the coexistence import (P5).

    Meta does not confirm where the decline error sits, so this reads
    ``value.errors[]`` and the ``errors[]`` of each history item."""
    _scan_errors(value.get("errors"), result)
    for item in value.get("history", []) or []:
        if not isinstance(item, dict):
            continue
        _scan_errors(item.get("errors"), result)
        meta = item.get("metadata") or {}
        phase = _int(meta.get("phase"))
        if phase is not None:
            progress = max(0, min(100, _int(meta.get("progress"), 0) or 0))
            result.history_progress.append(HistoryProgress(
                phase=phase, progress=progress,
                chunk_order=_int(meta.get("chunk_order"))))
        for thread in item.get("threads", []) or []:
            if not isinstance(thread, dict):
                continue
            chat = _digits(thread.get("id"))
            for msg in thread.get("messages", []) or []:
                if not isinstance(msg, dict):
                    continue
                sender = _digits(msg.get("from"))
                # Ours when it is from the business number, or when Meta
                # names a recipient, which it does only for the business.
                outbound = bool(msg.get("to")) or (
                    bool(business) and sender == business)
                context = msg.get("history_context") or {}
                result.messages.append(_build_message(
                    msg, wa_chat_id=chat, chat_kind="dm",
                    direction="out" if outbound else "in",
                    sender_wa_id=sender, sender_name="", from_history=True,
                    delivery_status=normalize_delivery_status(
                        context.get("status")),
                ))


def _parse_echoes_change(
    value: dict[str, Any], business: str, result: SyncResult,
) -> None:
    """The ``smb_message_echoes`` field: a message the business sent from
    the phone app. It is ``out``, its chat is ``to``, and its sender is the
    business number with no name (P5)."""
    for msg in value.get("message_echoes", []) or []:
        if not isinstance(msg, dict):
            continue
        result.messages.append(_build_message(
            msg, wa_chat_id=_digits(msg.get("to")), chat_kind="dm",
            direction="out", sender_wa_id=business or _digits(msg.get("from")),
            sender_name="", is_echo=True,
        ))


def _parse_state_sync_change(value: dict[str, Any], result: SyncResult) -> None:
    """The ``smb_app_state_sync`` field: address-book adds and removes."""
    for item in value.get("state_sync", []) or []:
        if not isinstance(item, dict) or item.get("type") != "contact":
            continue
        action = item.get("action")
        contact = item.get("contact") or {}
        phone = _digits(contact.get("phone_number"))
        if action not in ("add", "remove") or not phone:
            continue
        result.contact_changes.append(WhatsAppContactChange(
            phone_number=phone, action=action,
            full_name=str(contact.get("full_name") or "").strip(),
            first_name=str(contact.get("first_name") or "").strip(),
            timestamp=_ts((item.get("metadata") or {}).get("timestamp")),
        ))


def parse_webhook(payload: dict[str, Any]) -> SyncResult:
    """Turn a full Cloud API webhook body into a :class:`SyncResult`.

    Aggregates messages, statuses and contacts across every ``entry`` /
    ``change`` in the payload. The ``phone_number_id`` is taken from the first
    change's metadata (a single webhook batch targets one number). Each change
    goes to the reader of its ``field`` (WA-C3 P5).
    """
    result = SyncResult()
    if not isinstance(payload, dict):
        result.errors.append("payload is not an object")
        return result

    for entry in payload.get("entry", []) or []:
        for change in (entry or {}).get("changes", []) or []:
            try:
                value = (change or {}).get("value") or {}
                meta = value.get("metadata") or {}
                if not result.phone_number_id and meta.get("phone_number_id"):
                    result.phone_number_id = meta["phone_number_id"]
                field_name = (change or {}).get("field") or "messages"
                shown = meta.get("display_phone_number")
                business = _digits(shown) if shown else ""
                if field_name == "messages":
                    _parse_messages_change(value, result)
                elif field_name == "history":
                    _parse_history_change(value, business, result)
                elif field_name == "smb_message_echoes":
                    _parse_echoes_change(value, business, result)
                elif field_name == "smb_app_state_sync":
                    _parse_state_sync_change(value, result)
                # Any other field (account_update, templates, ...) is not
                # ours to store, and it is not an error either.
            except Exception as exc:
                result.errors.append(f"change parse failed: {exc!r}")

    return result
