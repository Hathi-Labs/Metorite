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

from email_ingestion import html_tier

logger = logging.getLogger(__name__)

#: The most characters of HTML that :func:`_html_to_text` reads. The text cap
#: is 500 KB, so 2 MiB of HTML is enough, and it bounds the time of each call
#: (WS-17 EM-S3 fix round 1).
HTML_TEXT_INPUT_CAP = 2 * 1024 * 1024

# Possessive quantifiers (`*+`) never backtrack. The whitespace runs end at a
# letter, a `/` or a `>`, which are not whitespace, so each one matches the
# same text as the greedy form did.
_BR_RE = re.compile(r"(?i)<\s*+br\s*+/?>")
_BLOCK_END_RE = re.compile(r"(?i)</\s*+(p|div|tr|li|h[1-6]|table)\s*+>")


def _strip_tags(s: str) -> str:
    """Remove each ``<...>`` with one or more characters inside.

    It removes the same text as ``re.sub(r"<[^>]+>", "", s)``, in one pass.
    The regex tried each ``<`` to the end of the text when no ``>`` followed,
    so a run of ``<`` cost quadratic time.
    """
    out: list[str] = []
    pos = 0
    while True:
        start = s.find("<", pos)
        if start < 0:
            break
        end = s.find(">", start + 1)
        if end < 0:
            break  # no ``>`` after this ``<``, so no later ``<`` closes either
        if end == start + 1:  # ``<>`` holds no character, so it stays
            out.append(s[pos:end])
            pos = end
            continue
        out.append(s[pos:start])
        pos = end + 1
    out.append(s[pos:])
    return "".join(out)


def _html_to_text(s: str) -> str:
    """Crude HTML→plain-text (block/break tags → newlines, strip the rest,
    unescape entities). Mirrors gateway.routes.email.signature.html_to_text;
    inlined here so the ingestion package needs no gateway import. Used to
    populate body_text from body_html when a provider returns HTML only — so
    full-text search (which indexes body_text) can match it.

    ⚠️ **Linear time (WS-17 EM-S3 fix round 1).** The sync calls it inside
    its transaction. The input stops at :data:`HTML_TEXT_INPUT_CAP`, and no
    step backtracks. The old patterns ``<[^>]+>`` and ``[ \\t]+\\n`` took
    31 s on 80 KB of hostile input. Fence:
    ``tests/unit/test_email_html_hot_only.py::TestTheHtmlToText``."""
    s = (s or "")[:HTML_TEXT_INPUT_CAP]
    s = _BR_RE.sub("\n", s)
    s = _BLOCK_END_RE.sub("\n", s)
    s = _strip_tags(s)
    s = _html.unescape(s)
    # Remove the spaces and tabs at the end of each line, as `[ \t]+\n` did.
    # The last line keeps none either, and the strip below removes them anyway.
    s = "\n".join(line.rstrip(" \t") for line in s.split("\n"))
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
    runs under the tenant binding.

    🔴 **No cold HTML (WS-17 EM-S3, §14.4.3 item 2).** The HTML goes through
    ``html_tier.COLD_SAFE_HTML_SET``. With ``html_tier.hot_only()`` true, a
    cold row gets its text and keeps the HTML that it holds, NULL for a row
    that held none. The row decides by its own ``received_at``, so a
    candidate needs no date."""
    cold_before = html_tier.cold_before()
    for b in fetched:
        await db.execute(text(
            f"""UPDATE email_messages
                  SET body_text = :bt, {html_tier.COLD_SAFE_HTML_SET},
                      snippet = :sn,
                      has_attachments = COALESCE(:ha, has_attachments),
                      updated_at = now()
                WHERE id = :id"""),
            {"id": b.id, "bt": b.body_text, "bh": b.body_html,
             "html_cold_before": cold_before,
             "sn": b.snippet, "ha": b.has_attachments},
        )
    if fetched:
        logger.info("body_backfill.done account=%s hydrated=%d", account_id,
                    len(fetched))
    return len(fetched)
