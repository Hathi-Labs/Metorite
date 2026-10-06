"""Automation · intent — what does this message want?

A deterministic, dependency-free intent classifier over a message body, mirroring
the email vertical's "learned patterns short-circuit before any LLM" doctrine:
cheap keyword/pattern matching handles the overwhelming majority of a hardware
business's WhatsApp traffic, and an LLM refinement can layer on later for the
ambiguous tail. The vocabulary is Hinglish-aware ("kab tak", "kitna", "quotation")
because that is how the dealer network actually writes.

Taxonomy (matches the spec §5): order_status · quote_request · payment ·
service_issue · scheduling · social · spam. ``None`` when nothing matches (the
message stays unclassified rather than being forced into a bucket).
"""

from __future__ import annotations

import re
from typing import Any

from acb_common import get_logger
from sqlalchemy import text as _sql

_log = get_logger("gateway.whatsapp.intent")

# Bound one pass so a huge backlog (first history import) can't monopolise a
# webhook handler; the next batch drains the rest.
_MAX_PER_PASS = 500

# Ordered most-specific → most-generic. The first taxonomy whose pattern hits
# wins, so a payment-overdue message isn't swallowed by a generic greeting. Each
# pattern is a single compiled alternation over lowercased text.
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("payment", re.compile(
        r"\b(payment|invoice|paid|overdue|outstanding|balance due|"
        r"pending amount|bakaya|bhugtan|neft|rtgs|utr|cheque|"
        r"proforma|advance)\b")),
    ("order_status", re.compile(
        r"\b(order status|where is my order|dispatch|dispatched|shipped|"
        r"shipping|tracking|track|awb|courier|delivery|delivered|kab tak|"
        r"kab milega|when will i get|eta)\b")),
    ("quote_request", re.compile(
        r"\b(quote|quotation|quotation for|pricing|price list|rate|rates|"
        r"kitna|kitne ka|how much|cost|estimate|proposal|catalog|catalogue|"
        r"price of)\b")),
    ("service_issue", re.compile(
        r"\b(not working|broken|jam|jammed|error|issue|problem|complaint|"
        r"repair|service|stuck|down|failure|defective|kharab|nahi chal|"
        r"warranty|support)\b")),
    ("scheduling", re.compile(
        r"\b(meeting|schedule|call at|available|availability|book a|appointment|"
        r"demo|visit|reschedule|calendar|slot|kab free|when are you free)\b")),
    ("spam", re.compile(
        r"\b(congratulations you have won|click here to claim|limited offer|"
        r"lucky draw|loan approved|earn from home|crypto|forex|"
        r"investment opportunity|unsubscribe)\b")),
    ("social", re.compile(
        r"^\s*(hi|hii+|hello|hey|namaste|namaskar|good morning|good evening|"
        r"good night|thanks|thank you|thank u|dhanyavaad|shukriya|ok|okay|"
        r"👍|🙏|great|welcome|congrats|happy (birthday|diwali|new year))"
        r"[\s!.😊🙏👍]*$")),
]


def classify_intent(body: str | None) -> str | None:
    """Return the message intent, or ``None`` when nothing matches. Pure."""
    if not body:
        return None
    text = body.strip().lower()
    if not text:
        return None
    for label, pattern in _PATTERNS:
        if pattern.search(text):
            return label
    return None


# ── apply + post-sync hook ────────────────────────────────────────────────────

async def apply_intents(db: Any, account_id: str) -> int:
    """Classify + stamp every not-yet-processed INBOUND message on an account.

    Only inbound messages get an intent (an outbound reply has no 'ask'). The
    ``rules_processed_at`` watermark is the same one the email runner uses: set
    once, so a message is never reclassified and a redelivered webhook is a
    no-op here. Returns the number of messages processed. Caller owns the txn.
    """
    # Effective text: a transcribed voice note (transcript_text) is classified
    # exactly like a typed message when body_text is empty (W4.3).
    rows = (await db.execute(
        _sql("""SELECT id,
                       COALESCE(NULLIF(body_text, ''), transcript_text) AS body_text
                FROM wa_messages
                WHERE account_id = :aid AND direction = 'in'
                  AND rules_processed_at IS NULL
                ORDER BY sent_at ASC NULLS FIRST
                LIMIT :lim"""),
        {"aid": account_id, "lim": _MAX_PER_PASS},
    )).fetchall()
    for r in rows:
        await db.execute(
            _sql("""UPDATE wa_messages
                    SET intent = :intent, rules_processed_at = now(),
                        updated_at = now()
                    WHERE id = :id"""),
            {"intent": classify_intent(r.body_text), "id": str(r.id)},
        )
    return len(rows)


async def process_new_messages(account_id: str) -> None:
    """Post-sync ``on_new_messages`` hook: the new-message pipeline. Runs intent
    classification (inbound) then commitment extraction (both directions) over
    the newly-landed messages. Grows the way the email on_new_mail pipeline did;
    sender categorization + auto-answer execution layer on here later."""
    from gateway.routes.whatsapp.automation.commitments import apply_commitments
    from gateway.routes.whatsapp.core import _tenant_session
    # WS-20 WA-C1: the caller binds the account's tenant. The Meta webhook
    # binds the org of the account row before it fires this hook. With no
    # binding, `_tenant_session()` raises `TenantUnbound` and writes nothing.
    # The bridge routes bind nothing yet (§12.4, out of WA-C1), and
    # `fire_post_sync_hooks` logs that failure.
    async with _tenant_session() as db:
        classified = await apply_intents(db, account_id)
        commitments = await apply_commitments(db, account_id)
    _log.info("whatsapp.process_new_messages.done",
              account_id=account_id, classified=classified,
              commitments=commitments)
