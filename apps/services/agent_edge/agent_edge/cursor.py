"""The opaque page cursor of the MCP edge (WS-55 EA-3b-1).

Spec ``project-docs/specs/external_agent_access.md`` §9.1, "The opaque cursor"
and "A bad cursor".

A cursor is ``<payload>.<tag>``.

* ``<payload>`` is the base64url form (no padding) of a JSON object with four
  keys: ``page``, ``size``, ``filter`` and ``connection``.
* ``filter`` is the hex SHA-256 of the sorted-key JSON of every tool input
  except ``limit`` and ``cursor``.
* ``<tag>`` is the base64url HMAC-SHA256 of the ``<payload>`` text. Its key is
  the HMAC-SHA256 of ``agent_edge_signing_key`` over the label ``cursor-v1``.

A cursor fixes the page size, so a ``limit`` sent with a cursor is ignored.
A cursor with a bad tag, another filter or another connection is refused,
and the tool answers ``isError: true``. A key rotation ends every open cursor
in the same way, and the agent lists again.

The module does no I/O.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

#: The label the cursor key is derived over.
LABEL = b"cursor-v1"

#: The longest cursor the edge reads. A real one is about 200 characters.
MAX_CHARS = 1024

#: The inputs that are not part of the filter.
_NOT_FILTER = frozenset({"limit", "cursor"})


class BadCursor(ValueError):
    """The cursor is malformed, forged, or belongs to another list."""


@dataclass(frozen=True)
class Position:
    """Where a cursor points: a 1-based page of a fixed size."""

    page: int
    size: int


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def _key(signing_key: str) -> bytes:
    return hmac.new(signing_key.encode("utf-8"), LABEL, hashlib.sha256).digest()


def _tag(signing_key: str, payload: str) -> str:
    return _b64(hmac.new(
        _key(signing_key), payload.encode("ascii"), hashlib.sha256).digest())


def filter_hash(arguments: Mapping[str, Any]) -> str:
    """The hex SHA-256 of the sorted-key JSON of every input but the page ones.

    An input sent as ``null`` counts as not sent, so the two forms give one
    filter.
    """
    kept = {k: v for k, v in arguments.items()
            if k not in _NOT_FILTER and v is not None}
    text = json.dumps(kept, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True)
    return hashlib.sha256(text.encode("ascii")).hexdigest()


def encode(signing_key: str, *, page: int, size: int,
           arguments: Mapping[str, Any], connection_id: str) -> str:
    """A cursor for *page* of *size* rows of this list and this connection."""
    body = {
        "page": int(page),
        "size": int(size),
        "filter": filter_hash(arguments),
        "connection": connection_id,
    }
    payload = _b64(json.dumps(body, sort_keys=True, separators=(",", ":"))
                   .encode("ascii"))
    return f"{payload}.{_tag(signing_key, payload)}"


def decode(signing_key: str, cursor: str, *,
           arguments: Mapping[str, Any], connection_id: str) -> Position:
    """The position of *cursor*, or raise :class:`BadCursor`.

    The tag is checked first, with ``hmac.compare_digest``. Only a cursor
    that this edge signed is then read.
    """
    if not isinstance(cursor, str) or not cursor or len(cursor) > MAX_CHARS:
        raise BadCursor("malformed")
    payload, sep, tag = cursor.partition(".")
    if not sep or not payload or not tag or not payload.isascii():
        raise BadCursor("malformed")
    if not hmac.compare_digest(_tag(signing_key, payload), tag):
        raise BadCursor("tag")
    try:
        body = json.loads(_unb64(payload))
    except (binascii.Error, ValueError):
        raise BadCursor("malformed") from None
    if not isinstance(body, dict):
        raise BadCursor("malformed")
    if body.get("connection") != connection_id:
        raise BadCursor("connection")
    if body.get("filter") != filter_hash(arguments):
        raise BadCursor("filter")
    page, size = body.get("page"), body.get("size")
    if (not isinstance(page, int) or isinstance(page, bool) or page < 1
            or not isinstance(size, int) or isinstance(size, bool) or size < 1):
        raise BadCursor("malformed")
    return Position(page=page, size=size)


__all__ = [
    "LABEL",
    "MAX_CHARS",
    "BadCursor",
    "Position",
    "decode",
    "encode",
    "filter_hash",
]
