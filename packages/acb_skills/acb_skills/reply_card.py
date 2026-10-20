"""The reply card: one email reply, confirmed and done in the chat (EM-CR).

Spec ``project-docs/specs/email_app_master_plan.md`` §18. The owner asked on
2026-10-20 for a reply that the member confirms in the chat and that then
completes, with no Email app opened. The email assistant's ``compose_reply``
shows ONE card. The card holds the mail it answers, the recipients that the
gateway derived, a Reply or Reply all toggle, the editable body, and the
buttons Send, Save to Drafts and Discard.

The card is the confirmation card of :func:`acb_skills.ask_tools.request_confirmation`
with a ``reply`` form. No second card mechanism exists. This module holds
the parts that two processes must agree on:

* the answer of the card, ``APPROVE {"reply": {...}}`` (:func:`parse_reply_answer`);
* the record of that answer, which ``POST /agent/respond-input`` writes when
  the member's browser posts it (:func:`record_reply_answer`), and which the
  act route of the gateway takes once (:func:`take_reply_answer`).

**Why a record.** The tool may send even in a run that read outside mail
(H-236 holds such a run), because the member confirmed this one reply. So the
body that leaves must be the body that the MEMBER submitted. The gateway does
not take the tool's word for it. The respond-input route stores a digest of
the answer, keyed by the card's request id and bound to the member who
answered. The act route sends only an answer whose digest matches, for the
same member, once. A model has no tool that writes the record. Fence:
``tests/unit/test_email_chat_reply.py``.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Any

from acb_common import get_logger

_log = get_logger("acb_skills.reply_card")

__all__ = [
    "MAX_REPLY_ANSWER",
    "MAX_REPLY_BODY",
    "MAX_REPLY_RECIPIENTS",
    "RECORD_NAMESPACE",
    "RECORD_TTL_S",
    "REPLY_ACTIONS",
    "REPLY_ANSWER_KEYS",
    "REPLY_ANSWER_PREFIX",
    "ReplyChoice",
    "looks_like_reply_answer",
    "parse_reply_answer",
    "record_reply_answer",
    "reply_answer_digest",
    "take_reply_answer",
]

#: The prefix of an Approve with a payload. The rows card uses the same one
#: (``ask_tools.ROWS_ANSWER_PREFIX``), and the JSON key tells the two apart.
REPLY_ANSWER_PREFIX = "APPROVE "

#: What the member may choose on the card. Discard is ``REJECT``.
REPLY_ACTIONS: frozenset[str] = frozenset({"send", "save"})

#: The keys of ``answer.reply``, exactly. ``to`` and ``cc`` are the lists the
#: member SAW. The gateway never sends to them: it derives the recipients
#: again and refuses the answer when the two differ.
REPLY_ANSWER_KEYS: frozenset[str] = frozenset({"action", "reply_all", "body", "to", "cc"})

#: The longest body the card takes. A longer one is refused, never cut.
MAX_REPLY_BODY = 20_000
#: The most addresses of one list.
MAX_REPLY_RECIPIENTS = 100
_MAX_ADDRESS = 320
#: The longest answer that can be honest: the body escaped as JSON (six
#: characters for each one at most), the two lists, and the frame.
MAX_REPLY_ANSWER = (len(REPLY_ANSWER_PREFIX) + 6 * MAX_REPLY_BODY
                    + 2 * MAX_REPLY_RECIPIENTS * (_MAX_ADDRESS + 4) + 200)

#: The Redis namespace of the records (``cc:<org>:reply-card:<request id>``).
RECORD_NAMESPACE = "reply-card"
#: A card waits an hour at most (``wait_user_future``), and the act follows
#: at once. The record lives a little longer than the wait.
RECORD_TTL_S = 3700


@dataclass(frozen=True)
class ReplyChoice:
    """What the member chose on a reply card."""

    action: str
    reply_all: bool
    body: str
    to: tuple[str, ...]
    cc: tuple[str, ...]


def _addresses(value: Any) -> tuple[str, ...] | None:
    if not isinstance(value, list) or len(value) > MAX_REPLY_RECIPIENTS:
        return None
    out: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip() or len(item) > _MAX_ADDRESS:
            return None
        out.append(item.strip())
    return tuple(out)


def parse_reply_answer(answer: Any) -> ReplyChoice | None:
    """The member's choice, or None when *answer* is not a reply answer.

    Strict, and it fails closed. The answer is ``APPROVE`` and one JSON
    object with the one key ``reply``. That object holds exactly
    :data:`REPLY_ANSWER_KEYS`. A missing key, an extra key (a ``bcc``, an
    ``attachments``), an unknown action, an empty body, or a body over
    :data:`MAX_REPLY_BODY` reads as no answer. Nothing is trimmed to fit.
    """
    text = answer if isinstance(answer, str) else ""
    if len(text) > MAX_REPLY_ANSWER:
        return None
    text = text.strip()
    if not text.upper().startswith(REPLY_ANSWER_PREFIX):
        return None
    try:
        payload = json.loads(text[len(REPLY_ANSWER_PREFIX):])
    except (TypeError, ValueError, RecursionError):
        return None
    if not isinstance(payload, dict) or set(payload) != {"reply"}:
        return None
    reply = payload["reply"]
    if not isinstance(reply, dict) or set(reply) != REPLY_ANSWER_KEYS:
        return None
    action, reply_all, body = reply["action"], reply["reply_all"], reply["body"]
    if action not in REPLY_ACTIONS or not isinstance(reply_all, bool):
        return None
    if not isinstance(body, str) or not body.strip() or len(body) > MAX_REPLY_BODY:
        return None
    to, cc = _addresses(reply["to"]), _addresses(reply["cc"])
    if to is None or cc is None or not to:
        return None
    return ReplyChoice(action=action, reply_all=reply_all, body=body, to=to, cc=cc)


def looks_like_reply_answer(answer: Any) -> bool:
    """True when *answer* parses as a reply answer."""
    return parse_reply_answer(answer) is not None


def reply_answer_digest(request_id: str, responder: str, answer: str) -> str:
    """The digest of one answer: the card, the member and the exact text."""
    parts = "\x1f".join((str(request_id or ""), str(responder or "").strip().lower(),
                         str(answer or "")))
    return hashlib.sha256(parts.encode("utf-8")).hexdigest()


def _tenant_redis() -> Any:
    """The tenant Redis client. A seam, so a test passes a fake client."""
    from acb_common.tenant_redis import get_tenant_redis

    return get_tenant_redis()


async def record_reply_answer(
    organization_id: str | None, request_id: str, responder: str | None, answer: str,
) -> bool:
    """Record the digest of a reply answer that the member's browser posted.

    ``POST /agent/respond-input`` calls it BEFORE it resolves the card, with
    the caller's own tenant and email from the session (R5). An answer that
    is not a reply answer is not recorded. The FIRST answer of a card wins
    (``SET NX``): a second post, from this member or from another member of
    a shared room, cannot replace it. Never raises. A fault records nothing,
    so the act route refuses, and nothing is sent.
    """
    if not (organization_id and request_id and responder):
        return False
    if not looks_like_reply_answer(answer):
        return False
    digest = reply_answer_digest(request_id, responder, answer)
    try:
        from acb_common.tenant_redis import key, organization_scope

        with organization_scope(str(organization_id)):
            client = _tenant_redis()
            done = await client.set(key(RECORD_NAMESPACE, str(request_id)), digest,
                                    nx=True, ex=RECORD_TTL_S)
    except Exception as exc:  # the class only: a value never reaches a log
        _log.warning("reply_card.record_failed", error=type(exc).__name__)
        return False
    _log.info("reply_card.answer_recorded", first=bool(done))
    return bool(done)


async def take_reply_answer(
    organization_id: str | None, request_id: str, responder: str | None, answer: str,
) -> bool:
    """True when the member posted exactly *answer* to the card *request_id*.

    The act route of the gateway calls it, with its own tenant and the
    caller's email from the session. The record is read and deleted in ONE
    command (``GETDEL``), so one answer acts once. A missing record, another
    member, another text and a fault are each False.
    """
    if not (organization_id and request_id and responder):
        return False
    try:
        from acb_common.tenant_redis import key, organization_scope

        with organization_scope(str(organization_id)):
            stored = await _tenant_redis().getdel(key(RECORD_NAMESPACE, str(request_id)))
    except Exception as exc:
        _log.warning("reply_card.take_failed", error=type(exc).__name__)
        return False
    if isinstance(stored, bytes):
        stored = stored.decode("utf-8", errors="replace")
    if not isinstance(stored, str) or not stored:
        return False
    expected = reply_answer_digest(request_id, responder, answer)
    return hmac.compare_digest(stored, expected)
