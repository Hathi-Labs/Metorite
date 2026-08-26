"""Reading the audit trail — the page cursor, and what it may be trusted for.

Spec: ``project-docs/specs/operator_identity_and_access.md`` §8.1 done-whens
25-26 · **D64.5**. Board: WS-31 **CP-12f**.

**Policy only. No SQL.** The query lives in :mod:`customer_console.store`,
which is the same split the four modules before this one keep.

⚠️ **Read H-7 before you change the ordering.** ``created_at`` comes from
``now()``, which is the **transaction-start** timestamp, so a transaction that
opens early and commits late stamps a time EARLIER than a row already committed
by a newer transaction. That is measured, not theoretical — see
:data:`CURSOR_IS_EPHEMERAL` for what it costs here and why this surface can pay
it when migration 168's delta feed could not.
"""
from __future__ import annotations

import base64
import binascii
import uuid
from dataclasses import dataclass
from datetime import datetime

__all__ = [
    "CURSOR_IS_EPHEMERAL",
    "DEFAULT_LIMIT",
    "MAX_LIMIT",
    "Cursor",
    "CursorInvalid",
    "clamp_limit",
    "decode_cursor",
    "encode_cursor",
]

#: A page nobody asked to size. Big enough that the common question ("what
#: happened today") is one request, small enough to render.
DEFAULT_LIMIT = 50
#: The ceiling. A caller asking for more is CLAMPED rather than refused: it
#: still receives a ``next_cursor``, so no row becomes unreachable, and a
#: refusal here would only teach clients to retry with a smaller number.
MAX_LIMIT = 200

#: ⚠️ **The one property this cursor does NOT have, stated once.**
#:
#: A keyset cursor over ``(created_at, id)`` can MISS a row: one whose
#: transaction opened before the reader's page and committed after it, and
#: whose start-stamp therefore lands inside a window the reader has already
#: scrolled past. Reproduced on real Postgres 16 in
#: ``test_operator_activity.py::test_a_late_commit_can_be_missed_by_a_scroll``.
#:
#: **Why this surface may pay that and the delta feed may not.** H-7's defect
#: is PERMANENT because migration 168's client persists its cursor and advances
#: it forever, so a row stamped behind the high-water mark is never offered
#: again. This cursor is **ephemeral**: it is built from the rows of one
#: response, it is discarded when the reader stops scrolling, and every fresh
#: read starts at the newest row. The same test proves the missed row IS
#: present on the next fresh read. The loss is bounded to one scroll, not to
#: the life of a consumer.
#:
#: **So the rule is: never persist this cursor, and never drive a sync from it.**
#: If somebody needs a durable feed of ``control_audit``, that is a different
#: endpoint with a different ordering guarantee, and H-7 must be answered first
#: rather than inherited.
CURSOR_IS_EPHEMERAL = True


class CursorInvalid(Exception):
    """The cursor did not decode. Callers map this to **400**, never 500.

    A garbage cursor is a client mistake or a hand-edited URL, and both should
    read as "your request was wrong" rather than as the server falling over.
    """


@dataclass(frozen=True)
class Cursor:
    """Where the previous page stopped — the LAST row it returned."""

    created_at: datetime
    row_id: str


def clamp_limit(raw: int | None) -> int:
    """How many rows to serve. Never zero, never above :data:`MAX_LIMIT`.

    A zero or negative limit clamps UP to the default rather than returning an
    empty page. An empty page with a ``next_cursor`` is an infinite loop for
    any client that pages until the rows run out.
    """
    if raw is None:
        return DEFAULT_LIMIT
    if raw < 1:
        return DEFAULT_LIMIT
    return min(raw, MAX_LIMIT)


def encode_cursor(created_at: datetime, row_id: str) -> str:
    """Pack a position into one opaque string.

    Opaque rather than two query parameters so the ordering key stays an
    implementation detail. A client that could read ``(created_at, id)`` would
    start building cursors by hand, and then the ordering could never change
    without breaking it.
    """
    raw = f"{created_at.isoformat()}|{row_id}".encode()
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(token: str | None) -> Cursor | None:
    """Unpack a cursor. ``None`` means "start at the newest row".

    Raises :class:`CursorInvalid` for anything malformed. ⚠️ A cursor that
    fails to parse must NOT silently fall back to the first page: a client
    paging through 10,000 rows would restart from the top and loop forever,
    which presents as a hung console rather than as an error.
    """
    if token is None or not token.strip():
        return None
    padded = token + "=" * (-len(token) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        raise CursorInvalid("invalid cursor") from exc

    stamp, sep, row_id = raw.partition("|")
    if not sep or not stamp or not row_id:
        raise CursorInvalid("invalid cursor")
    try:
        created_at = datetime.fromisoformat(stamp)
    except ValueError as exc:
        raise CursorInvalid("invalid cursor") from exc
    # ⚠️ The id is checked HERE, not at the query. The store casts it to UUID,
    # and a non-UUID reaching that cast is a DatabaseError — a 500 for what is
    # really a malformed request. Parsing it here keeps the refusal a 400.
    try:
        uuid.UUID(row_id)
    except ValueError as exc:
        raise CursorInvalid("invalid cursor") from exc
    return Cursor(created_at=created_at, row_id=row_id)
