"""The embedding sweep's content_hash must match its SQL candidate predicate.

``select_pending_embeddings`` selects "messages needing an embedding" in SQL by
comparing the stored ``content_hash`` against a hash Postgres recomputes from
``coalesce(subject,'') || E'\\n\\n' || coalesce(body,'')``. The Python side must
store the sha256 of the *byte-identical* string, or every message with (say) a
trailing newline in its body hashes differently on the two sides, is re-selected
every sweep tick, and the backlog never drains — the "broken sweep SQL" the
2026-07 review flagged. These tests pin the Python side of that contract.

Postgres' ``convert_to(...,'UTF8')`` + ``sha256`` + ``encode(...,'hex')`` is
byte-identical to Python's ``.encode('utf-8')`` + ``sha256`` + ``hexdigest()``,
so we assert the Python equivalent of the SQL hash directly — the contract holds
by construction of those two well-defined primitives.
"""
from __future__ import annotations

import hashlib

from email_ingestion.email_embeddings import _content_hash, _hash_source


def _pg_equivalent_hash(subject: str, body: str) -> str:
    """What Postgres computes for the candidate predicate, replicated in Python.

    ``encode(sha256(convert_to(coalesce(subject,'') || E'\\n\\n'
    || coalesce(body,''), 'UTF8')), 'hex')`` — convert_to(...,'UTF8') yields the
    UTF-8 bytes, exactly what ``str.encode('utf-8')`` produces.
    """
    source = f"{subject}\n\n{body}"
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def test_hash_source_does_not_strip() -> None:
    # A trailing newline in the body is the common case (most bodies have one).
    # If _hash_source stripped, the stored hash would never equal the SQL hash
    # of the raw column, and the message would thrash forever.
    assert _hash_source("Subject", "Body text\n") == "Subject\n\nBody text\n"
    assert _hash_source("  padded  ", "  body  ") == "  padded  \n\n  body  "


def test_hash_source_uses_double_newline_separator() -> None:
    assert _hash_source("S", "B") == "S\n\nB"


def test_hash_source_coalesces_none_to_empty() -> None:
    # Mirrors SQL coalesce(...,'') for a null subject or body.
    assert _hash_source(None, "body") == "\n\nbody"
    assert _hash_source("subj", None) == "subj\n\n"
    assert _hash_source(None, None) == "\n\n"


def test_stored_hash_matches_the_sql_predicate_hash() -> None:
    # The whole point: the hash the sweep STORES must equal the hash the SQL
    # candidate predicate RECOMPUTES — for tricky inputs, not just clean ones.
    cases = [
        ("Invoice #42", "Please pay by Friday.\n"),        # trailing newline
        ("  spaced  ", "\ttabbed body\r\n"),               # mixed whitespace
        ("Ünïcödé ✉", "Grüße 🚀\n\nregards\n"),            # non-ASCII + emoji
        ("", ""),                                          # empty both
        (None, "body only"),                               # null subject
    ]
    for subject, body in cases:
        stored = _content_hash(_hash_source(subject, body))
        # coalesce(NULL,'') == '' — normalise the same way for the PG mirror.
        expected = _pg_equivalent_hash(subject or "", body or "")
        assert stored == expected, (subject, body)
