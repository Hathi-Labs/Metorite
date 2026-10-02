"""Search body-backfill — hydrate empty message bodies so FTS can find them.

The gap this closes: some providers (notably Outlook/Graph) sync message
HEADERS only; the full body is fetched lazily the first time a user opens the
message (see the gateway's get_message hydration). Until then ``body_text`` is
empty — so full-text search cannot match on the body of any message the user
hasn't opened. For "reliably search ALL emails" that's a real recall hole:
months of unopened Outlook mail are invisible to body search.

This module drains that backlog in the background. After each account's normal
sync tick, phase (e) of the scheduler takes a BOUNDED batch of the account's
empty-body messages (newest first), fetches each body via the
already-authenticated provider, and persists the body + snippet. Bounded per
tick so it never stalls a sync cycle; over successive ticks the backlog empties.
The partial index ``idx_email_messages_missing_body`` (migration 72) keeps the
candidate scan cheap even on large mailboxes.

Three steps, so no session stays open across the provider calls (WS-17
EM-T4a-1, ``email_app_master_plan.md`` §10.4.6):

1. ``select_missing_bodies`` reads the candidates. It takes a session.
2. ``fetch_bodies`` calls the provider. It takes NO session.
3. ``write_bodies`` writes the bodies. It takes a session.

The read and write steps open no session and never commit. The scheduler opens
one ``tenant_session(org)`` for each, and the seam commits when the block exits.
A ``commit()`` here would end ``SET LOCAL``. R7:
``tests/unit/test_email_scheduler_tenancy.py``.

Idempotent and self-limiting: once a message has a body it no longer matches the
candidate query, so it's touched exactly once.
"""

from __future__ import annotations

import html as _html
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text

logger = logging.getLogger(__name__)

_TAG_RE = re.compile(r"<[^>]+>")


def _html_to_text(s: str) -> str:
    """Crude HTML→plain-text (block/break tags → newlines, strip the rest,
    unescape entities). Mirrors gateway.routes.email.signature.html_to_text;
    inlined here so the ingestion package needs no gateway import. Used to
    populate body_text from body_html when a provider returns HTML only — so
    full-text search (which indexes body_text) can match it."""
    s = re.sub(r"(?i)<\s*br\s*/?>", "\n", s or "")
    s = re.sub(r"(?i)</\s*(p|div|tr|li|h[1-6]|table)\s*>", "\n", s)
    s = _TAG_RE.sub("", s)
    s = _html.unescape(s)
    s = re.sub(r"[ \t]+\n", "\n", s)
    return re.sub(r"\n{3,}", "\n\n", s).strip()

# How many bodies to hydrate per sync tick. Small enough that the extra
# provider round-trips never dominate a sync cycle; the backlog drains over
# successive ticks. Tunable via the caller if a box needs to catch up faster.
DEFAULT_BATCH = 25

# Mirror the gateway body caps (core.MAX_BODY_TEXT_BYTES / _HTML_BYTES) so a
# backfilled body is stored exactly like a lazily-hydrated one.
_MAX_BODY_TEXT_BYTES = 500_000
_MAX_BODY_HTML_BYTES = 2_000_000


def _truncate(value: str | None, max_bytes: int) -> str | None:
    """Cut to max_bytes on a UTF-8 boundary (matches the gateway's _truncate_body
    marker), or pass through when it fits / is empty."""
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


@dataclass(frozen=True)
class BodyCandidate:
    """One message with an empty body, as the read step found it."""

    id: str
    provider_message_id: str


@dataclass(frozen=True)
class FetchedBody:
    """The body of one message, ready for the write step."""

    id: str
    body_text: str
    body_html: str | None
    snippet: str
    has_attachments: bool | None


async def select_missing_bodies(
    db: Any, account_id: str, *, batch: int = DEFAULT_BATCH,
) -> list[BodyCandidate]:
    """The read step: up to ``batch`` empty-body messages of one account.

    Takes the caller's session, opens none and never commits. The candidates
    are plain values, so they outlive the block that read them."""
    rows = (await db.execute(text(
        """SELECT id, provider_message_id
             FROM email_messages
            WHERE account_id = :aid
              AND (body_text IS NULL OR body_text = '')
              AND LOWER(COALESCE(folder, '')) NOT IN ('drafts', 'draft')
            ORDER BY received_at DESC NULLS LAST
            LIMIT :lim"""),
        {"aid": account_id, "lim": batch},
    )).fetchall()
    return [BodyCandidate(id=str(r.id), provider_message_id=r.provider_message_id)
            for r in rows]


async def fetch_bodies(
    provider: Any, account_id: str, candidates: Sequence[BodyCandidate],
) -> list[FetchedBody]:
    """The fetch step: get each body from an already-authenticated provider.

    Takes NO session. The caller holds none open across these provider calls
    (EM-T4a-1). Best-effort: a per-message provider error is logged and
    skipped, so one bad message never stalls the batch and the bodies of the
    others still reach the write step."""
    fetched: list[FetchedBody] = []
    for r in candidates:
        try:
            full = await provider.get_message(r.provider_message_id)
        except Exception as exc:
            logger.debug("body_backfill.fetch_failed account=%s msg=%s err=%s",
                         account_id, r.provider_message_id, str(exc)[:120])
            continue
        raw_text = (full.body_text or "").strip()
        raw_html = getattr(full, "body_html", None) or ""
        # Outlook/Graph often returns HTML only (empty body_text). FTS indexes
        # body_text, so derive plain text from the HTML — otherwise the row keeps
        # matching the empty-body candidate query and is re-fetched every tick,
        # and its body is never searchable.
        if not raw_text and raw_html:
            raw_text = _html_to_text(raw_html)
        body_text = _truncate(raw_text, _MAX_BODY_TEXT_BYTES)
        body_html = _truncate(raw_html, _MAX_BODY_HTML_BYTES) if raw_html else None
        # Nothing to store (a genuinely empty message) — stamp a single space so
        # the row stops matching the empty-body candidate query and we don't
        # re-fetch it forever.
        if not body_text and not body_html:
            body_text = " "
        elif not body_text:
            # HTML existed but stripped to nothing — still mark non-empty so the
            # candidate query stops re-selecting this row.
            body_text = " "
        snippet = (getattr(full, "snippet", "") or body_text or "")[:200]
        fetched.append(FetchedBody(
            id=r.id, body_text=body_text, body_html=body_html, snippet=snippet,
            has_attachments=getattr(full, "has_attachments", None)))
    return fetched


async def write_bodies(
    db: Any, account_id: str, fetched: Sequence[FetchedBody],
) -> int:
    """The write step: persist each fetched body. Returns how many it wrote.

    Takes the caller's session, opens none and never commits. The seam
    commits when the caller's ``tenant_session`` block exits, so each UPDATE
    runs under the tenant binding."""
    for b in fetched:
        await db.execute(text(
            """UPDATE email_messages
                  SET body_text = :bt, body_html = :bh, snippet = :sn,
                      has_attachments = COALESCE(:ha, has_attachments),
                      updated_at = now()
                WHERE id = :id"""),
            {"id": b.id, "bt": b.body_text, "bh": b.body_html,
             "sn": b.snippet, "ha": b.has_attachments},
        )
    if fetched:
        logger.info("body_backfill.done account=%s hydrated=%d", account_id,
                    len(fetched))
    return len(fetched)
