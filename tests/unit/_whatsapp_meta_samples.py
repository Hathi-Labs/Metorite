"""Meta's sample webhook bodies for the three coexistence fields (WS-20 WA-C3).

Spec: ``project-docs/specs/whatsapp_message_manager.md`` §12.4.1. The shapes
follow Meta, "Onboard WhatsApp Business app users" (fetched 2026-10-07):

* ``history`` — ``value.history[]`` holds ``{metadata: {phase, chunk_order,
  progress}, threads: [{id, messages: [...]}]}``. A message from the business
  carries ``to``. Each message carries ``history_context.status``.
* ``smb_message_echoes`` — ``value.message_echoes[]`` holds a message that the
  business sent from the phone app, with ``from`` (the business) and ``to``.
* ``smb_app_state_sync`` — ``value.state_sync[]`` holds a contact ``add`` or
  ``remove``. A remove carries no names.

All three carry ``value.metadata.phone_number_id``. The parser tests and the R8
suite build their bodies here, so the two cannot drift apart.
"""

from __future__ import annotations

from typing import Any

#: The business number of the samples, as Meta writes it in ``metadata``.
BUSINESS_NUMBER = "15550783881"
#: The customer of the samples. Meta writes a user number as digits.
CUSTOMER = "16505551234"
#: Meta's error code when the member turned history sharing off in the app.
DECLINED_CODE = 2593109


def _envelope(field: str, pnid: str, value: dict[str, Any]) -> dict[str, Any]:
    return {
        "object": "whatsapp_business_account",
        "entry": [{"id": "102290129340398", "changes": [{
            "field": field,
            "value": {
                "messaging_product": "whatsapp",
                "metadata": {"display_phone_number": BUSINESS_NUMBER,
                             "phone_number_id": pnid},
                **value,
            },
        }]}],
    }


def history_body(pnid: str, *, inbound_id: str, outbound_id: str,
                 phase: int = 0, chunk_order: int = 1, progress: int = 55,
                 customer: str = CUSTOMER) -> dict[str, Any]:
    """One ``history`` chunk with one thread of two messages.

    The first message is from the customer and reads ``READ``. The second
    is from the business, carries ``to`` and reads ``DELIVERED``. The second
    one makes a promise, so a commitment pass that reads it writes a row.
    """
    return _envelope("history", pnid, {"history": [{
        "metadata": {"phase": phase, "chunk_order": chunk_order,
                     "progress": progress},
        "threads": [{
            "id": customer,
            "messages": [
                {"from": customer, "id": inbound_id,
                 "timestamp": "1739230955", "type": "text",
                 "text": {"body": "Is the order shipped?"},
                 "history_context": {"status": "READ"}},
                {"from": BUSINESS_NUMBER, "to": customer, "id": outbound_id,
                 "timestamp": "1739231000", "type": "text",
                 "text": {"body": "Yes, I will send the tracking number tomorrow"},
                 "history_context": {"status": "DELIVERED"}},
            ],
        }],
    }]})


def history_progress_body(pnid: str, *, phase: int, progress: int,
                          chunk_order: int = 1) -> dict[str, Any]:
    """A ``history`` chunk that carries only its progress, with no thread."""
    return _envelope("history", pnid, {"history": [{
        "metadata": {"phase": phase, "chunk_order": chunk_order,
                     "progress": progress},
        "threads": [],
    }]})


def history_declined_body(pnid: str, *, where: str = "value") -> dict[str, Any]:
    """The decline. Meta does not confirm where the error sits, so ``where``
    puts it on ``value.errors[]`` or on ``value.history[].errors[]``."""
    error = {"code": DECLINED_CODE,
             "title": "History sync is turned off by the business from the "
                      "WhatsApp Business App",
             "message": "History sync is turned off by the business from the "
                        "WhatsApp Business App"}
    if where == "value":
        return _envelope("history", pnid, {"errors": [error]})
    return _envelope("history", pnid, {"history": [{"errors": [error]}]})


def echo_body(pnid: str, *, message_id: str, to: str = CUSTOMER,
              body: str = "I will send the invoice today",
              timestamp: str = "1790000100") -> dict[str, Any]:
    """One ``smb_message_echoes`` message that the business sent from the
    phone app."""
    return _envelope("smb_message_echoes", pnid, {"message_echoes": [{
        "from": BUSINESS_NUMBER, "to": to, "id": message_id,
        "timestamp": timestamp, "type": "text", "text": {"body": body},
    }]})


def state_sync_body(pnid: str, *, action: str, phone: str = "+1 650-555-1234",
                    full_name: str = "Pablo Morales",
                    first_name: str = "Pablo") -> dict[str, Any]:
    """One ``smb_app_state_sync`` contact change. A remove has no names."""
    contact: dict[str, Any] = {"phone_number": phone}
    if action == "add":
        contact.update({"full_name": full_name, "first_name": first_name})
    return _envelope("smb_app_state_sync", pnid, {"state_sync": [{
        "type": "contact", "contact": contact, "action": action,
        "metadata": {"timestamp": "1739231100"},
    }]})


def live_message_body(pnid: str, *, message_id: str, sender: str = CUSTOMER,
                      body: str = "hello", name: str = "Pablo M",
                      timestamp: str = "1790000000") -> dict[str, Any]:
    """One live inbound ``messages`` change, with the sender's profile."""
    return _envelope("messages", pnid, {
        "contacts": [{"profile": {"name": name}, "wa_id": sender}],
        "messages": [{"from": sender, "id": message_id, "timestamp": timestamp,
                      "type": "text", "text": {"body": body}}],
    })


def live_status_body(pnid: str, *, message_id: str, status: str,
                     recipient: str = CUSTOMER) -> dict[str, Any]:
    """One live ``statuses`` callback for a message the business sent."""
    return _envelope("messages", pnid, {"statuses": [{
        "id": message_id, "status": status, "timestamp": "1790000200",
        "recipient_id": recipient,
    }]})
