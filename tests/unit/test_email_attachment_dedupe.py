"""Attachment inserts must name a real ON CONFLICT arbiter.

A bare ``ON CONFLICT DO NOTHING`` on email_attachments was dead code: the only
unique constraint was the primary key on a freshly generated ``id``, so it never
fired and every re-hydration re-inserted the same files. Migration 88 adds
UNIQUE (message_id, provider_attachment_id); these tests pin that both
insert sites reference it as their arbiter, so the dedupe can never silently
regress to the bare form again. The two inserts of the open route are one
helper, ``_insert_attachments``, since 2026-10-28.

The ON CONFLICT *behaviour* itself is a Postgres guarantee exercised against the
live database; here we guard the call sites, which is what regressed before.
"""
from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_ARBITER = "ON CONFLICT (message_id, provider_attachment_id)"

_INSERT_SITES = [
    _ROOT / "apps/services/email_ingestion/email_ingestion/persist.py",
    _ROOT / "apps/services/gateway/gateway/routes/email/transport/messages.py",
]


def _attachment_insert_blocks(src: str) -> list[str]:
    """Every `INSERT INTO email_attachments ... ` statement in a source file,
    up to the closing triple-quote of its SQL string."""
    blocks = []
    for m in re.finditer(r"INSERT INTO email_attachments.*?\"\"\"", src, re.S):
        blocks.append(m.group(0))
    return blocks


def test_every_attachment_insert_names_the_arbiter() -> None:
    total = 0
    for path in _INSERT_SITES:
        src = path.read_text(encoding="utf-8")
        blocks = _attachment_insert_blocks(src)
        assert blocks, f"no attachment INSERT found in {path.name}"
        for block in blocks:
            total += 1
            assert _ARBITER in block, (
                f"{path.name}: attachment INSERT does not name the "
                f"(message_id, provider_attachment_id) arbiter — a bare "
                f"ON CONFLICT DO NOTHING never dedupes:\n{block}"
            )
    assert total == 2, f"expected 2 attachment insert sites, found {total}"


def test_migration_88_creates_the_unique_index() -> None:
    mig = (_ROOT / "infra/postgres/88_email_attachments_dedupe.sql").read_text(
        encoding="utf-8"
    )
    assert "CREATE UNIQUE INDEX IF NOT EXISTS" in mig
    assert "email_attachments_message_provider_uq" in mig
    assert "(message_id, provider_attachment_id)" in mig
    # Must dedupe existing rows before adding the constraint, or the index build
    # fails on any account that already has duplicates.
    assert "DELETE FROM email_attachments" in mig
