"""Two predicates that the chat mail tools and ``/email/search`` share (LOOP-19).

``proactive_loop.md`` §4.9.1: "find the NDA I sent to Mahesh" needs a recipient
filter and a file-name filter. Both are clauses on ``email_messages em``. A
caller adds them next to its own mailbox predicate (``mailbox_predicate`` or
``_account_scope``), so a read never leaves the one mailbox access seam and
never reaches mail of another member.
"""

from __future__ import annotations

from typing import Any

#: What the search reads. The stored column first (migration 496), and the
#: sub-select for a row that no trigger has reached. Same answer either way.
ATTACHMENT_NAMES = (
    "COALESCE(em.attachment_names, (SELECT string_agg(ea.filename, ' ' ORDER BY ea.filename) "
    "FROM email_attachments ea WHERE ea.message_id = em.id))"
)


def _like(value: str) -> str:
    """A case-insensitive substring pattern. ``%``, ``_`` and ``\\`` are literal."""
    esc = value.strip().lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{esc}%"


def recipient_clause(params: dict[str, Any], to_addr: str | None) -> str | None:
    """Any To or Cc recipient matches the words, on address or display name."""
    # A direct call that leaves the parameter out holds a FastAPI ``Query``
    # default, not a string. Only a string filters.
    if not isinstance(to_addr, str) or not to_addr.strip():
        return None
    params["to_addr"] = _like(to_addr)
    return ("EXISTS (SELECT 1 FROM jsonb_array_elements("
            " COALESCE(em.to_addresses, '[]'::jsonb)"
            " || COALESCE(em.cc_addresses, '[]'::jsonb)) AS r"
            " WHERE LOWER(r->>'email') LIKE :to_addr ESCAPE '\\'"
            " OR LOWER(r->>'name') LIKE :to_addr ESCAPE '\\')")


def attachment_name_clause(params: dict[str, Any], word: str | None) -> str | None:
    """A file name of the message holds the word."""
    if not isinstance(word, str) or not word.strip():
        return None
    params["attachment_name"] = _like(word)
    return f"LOWER({ATTACHMENT_NAMES}) LIKE :attachment_name ESCAPE '\\'"
