"""The storage meter of a mailbox, its limit, and the removal of older mail
from Metorite (WS-17 EM-T6c).

Spec: ``project-docs/specs/email_app_master_plan.md`` §10.4.7, the part
"EM-T6c". Owner decision D-EM-14 and the owner answers Q1 to Q4 (§10.2).

This module is the ONE owner of four rules. Do not copy them:

* **The limit (Q1).** Each mailbox has its own limit. The setting
  ``email_mailbox_storage_limit_mb`` (default 500) times 1,048,576 is the
  limit in bytes (:func:`storage_limit_bytes`). A mailbox is at the limit when
  its meter is at or over that value (:func:`at_limit`).
* **The meter (D-EM-14).** :func:`measure_stored_bytes` sums
  ``pg_column_size`` of each column of variable length in the
  ``email_messages`` rows of the mailbox, its ``email_attachments`` rows and
  its ``email_embeddings`` rows. It writes ``stored_bytes`` and
  ``stored_bytes_at``. ``pg_column_size`` reads the size of a stored value
  from its header, so the meter fetches no body. Do not use
  ``octet_length``, and do not take the size of a whole row.
* **The preview.** :func:`preview_older` returns the count of messages and
  the bytes that a removal before a date would free. It writes nothing.
* **The removal.** :func:`advance_import_since` moves the import floor
  first, before any delete (G3). :func:`remove_older_chunk` then deletes the
  ``email_executed_rules`` rows of up to 1,000 messages, and then those
  messages. The attachment rows and the embeddings cascade. The rules, the
  learned patterns, the rule guidance, the senders, the contacts and the
  Mem0 memories stay. No Mem0 key names one mail. The last steps are
  :func:`delete_empty_thread_status`, :func:`delete_orphan_ai_drafts` (G5),
  the meter and :func:`end_limit_phase` (G4).
* **Drafts stay (G1).** The preview and the removal skip the folder
  ``drafts`` (:data:`KEPT_FOLDERS_SQL`), so an unsent draft stays. This is an
  orchestrator decision of 2026-10-04, and the owner can reverse it.

⚠️ **The removal is Metorite's copy ONLY (D-EM-14).** This module imports
nothing from ``email_ingestion.providers``, and it never calls the provider.
Metorite never deletes or changes mail in the Outlook mailbox of the member.

⚠️ **Each step takes a session, opens none and never commits.** The caller
opens one ``tenant_session(org)`` for each step or chunk. A ``commit()``
here would end ``SET LOCAL``. Fence: ``tests/unit/test_email_storage_limit.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from acb_common.settings import get_settings
from sqlalchemy import text

__all__ = [
    "ATTACHMENT_COLUMNS",
    "BYTES_PER_MB",
    "EMBEDDING_COLUMNS",
    "KEPT_FOLDERS_SQL",
    "MESSAGE_COLUMNS",
    "REMOVE_CHUNK",
    "OlderMail",
    "advance_import_since",
    "at_limit",
    "delete_empty_thread_status",
    "delete_orphan_ai_drafts",
    "end_limit_phase",
    "measure_stored_bytes",
    "preview_older",
    "remove_older_chunk",
    "storage_limit_bytes",
]

#: The limit setting is in MB, and one MB is this many bytes.
BYTES_PER_MB = 1_048_576

#: The messages that one block of a removal deletes (EM-T6c item 10).
REMOVE_CHUNK = 1000

#: The filter that keeps the drafts out of a preview and a removal (G1). An
#: unsent draft stays in Metorite. The test of the folder copies the one in
#: ``body_backfill.py``, so a NULL folder is not a draft. It reads ``em``.
KEPT_FOLDERS_SQL = "LOWER(COALESCE(em.folder, '')) NOT IN ('drafts', 'draft')"

#: The columns of variable length of each table that the meter counts. A
#: column of fixed length (a uuid, a timestamp, a boolean) is the same size in
#: every row, and a member cannot remove it by a choice of mail. The R8 fence
#: compares each tuple with ``pg_attribute`` (``attlen = -1``), so a new
#: column of variable length fails until it is named here.
MESSAGE_COLUMNS: tuple[str, ...] = (
    "provider_message_id", "thread_id", "folder", "labels", "from_address",
    "to_addresses", "cc_addresses", "bcc_addresses", "subject", "body_text",
    "body_html", "snippet", "importance", "categories", "unsubscribe_link",
    "internet_message_id",
)
ATTACHMENT_COLUMNS: tuple[str, ...] = (
    "filename", "mime_type", "provider_attachment_id", "download_url",
    "storage_path",
)
EMBEDDING_COLUMNS: tuple[str, ...] = (
    "embedding", "model", "content_hash",
)


def _sizes(alias: str, columns: tuple[str, ...]) -> str:
    """The SQL sum of ``pg_column_size`` of each column. A NULL counts 0."""
    return " + ".join(
        f"COALESCE(pg_column_size({alias}.{col}), 0)" for col in columns)


def _bytes_cte(scope_filter: str) -> str:
    """Two CTEs: ``scope`` (the messages and their own bytes) and ``total``
    (their count, and their bytes plus those of their attachments and
    embeddings). The meter and the preview share them."""
    return f"""WITH scope AS (
           SELECT em.id, ({_sizes("em", MESSAGE_COLUMNS)}) AS bytes
             FROM email_messages em
            WHERE em.account_id = :aid{scope_filter}
       ), total AS (
           SELECT (SELECT count(*) FROM scope) AS messages,
                  CAST(
                      (SELECT COALESCE(SUM(s.bytes), 0) FROM scope s)
                    + (SELECT COALESCE(SUM({_sizes("ea", ATTACHMENT_COLUMNS)}), 0)
                         FROM email_attachments ea
                         JOIN scope s ON s.id = ea.message_id)
                    + (SELECT COALESCE(SUM({_sizes("ee", EMBEDDING_COLUMNS)}), 0)
                         FROM email_embeddings ee
                         JOIN scope s ON s.id = ee.message_id)
                  AS bigint) AS bytes
       )"""


#: The meter: one statement reads the sizes and writes them on the account.
_MEASURE = text(
    _bytes_cte("")
    + """
       UPDATE email_accounts
          SET stored_bytes = (SELECT bytes FROM total),
              stored_bytes_at = now()
        WHERE id = :aid
    RETURNING stored_bytes"""
)

#: The preview of a removal. It writes nothing. It counts no draft (G1).
_PREVIEW = text(
    _bytes_cte(f" AND em.received_at < :before AND {KEPT_FOLDERS_SQL}")
    + "\n       SELECT messages, bytes FROM total"
)

#: One chunk of a removal, oldest first. A message with no ``received_at``
#: is not received before any date, so it stays. A draft stays (G1).
_CHUNK_IDS = text(
    f"""SELECT em.id FROM email_messages em
        WHERE em.account_id = :aid AND em.received_at < :before
          AND {KEPT_FOLDERS_SQL}
        ORDER BY em.received_at, em.id
        LIMIT :lim"""
)

#: The FK of ``email_executed_rules.message_id`` is SET NULL, so the rows of
#: the removed mail are deleted first (EM-T6c item 10, risk R-6).
_DELETE_EXECUTED = text(
    """DELETE FROM email_executed_rules
        WHERE account_id = :aid AND message_id = ANY(CAST(:ids AS uuid[]))"""
)

#: The attachment rows and the embeddings cascade. ``email_rule_guidance``
#: keeps its row with ``message_id`` NULL (item 11).
_DELETE_MESSAGES = text(
    """DELETE FROM email_messages
        WHERE account_id = :aid AND id = ANY(CAST(:ids AS uuid[]))"""
)

_DELETE_EMPTY_THREAD_STATUS = text(
    """DELETE FROM email_thread_status ts
        WHERE ts.account_id = :aid
          AND NOT EXISTS (SELECT 1 FROM email_messages em
                           WHERE em.account_id = ts.account_id
                             AND em.thread_id = ts.thread_id)"""
)

#: The reply drafts of the AI whose thread has no message left (G5). The
#: drafter keys a row on the mailbox and the thread, so a row of a removed
#: thread can never show again.
_DELETE_ORPHAN_AI_DRAFTS = text(
    """DELETE FROM email_ai_drafts d
        WHERE d.account_id = :aid
          AND NOT EXISTS (SELECT 1 FROM email_messages em
                           WHERE em.account_id = d.account_id
                             AND em.thread_id = d.thread_id)"""
)

#: A Resync then imports no mail older than ``before`` (EM-T6c item 12).
#: ``GREATEST`` ignores a NULL, so a mailbox from before EM-T6 gets ``before``.
_ADVANCE_IMPORT_SINCE = text(
    """UPDATE email_accounts
          SET import_since = GREATEST(import_since, CAST(:before AS timestamptz)),
              updated_at = now()
        WHERE id = :aid"""
)

#: The end of the ``limit`` phase after a removal (G4). The ``WHERE`` keeps
#: every other phase as it is, so a running import keeps its progress.
_END_LIMIT_PHASE = text(
    """UPDATE email_accounts
          SET import_phase = 'done', updated_at = now()
        WHERE id = :aid AND import_phase = 'limit'"""
)


def storage_limit_bytes() -> int:
    """The limit of one mailbox in bytes (Q1)."""
    return int(get_settings().email_mailbox_storage_limit_mb) * BYTES_PER_MB


def at_limit(stored_bytes: int | None) -> bool:
    """True when a meter of ``stored_bytes`` is at or over the limit.

    ``None`` means the meter did not run yet, and it is not at the limit."""
    if stored_bytes is None:
        return False
    return int(stored_bytes) >= storage_limit_bytes()


async def measure_stored_bytes(db: Any, account_id: str) -> int | None:
    """Measure the copy of one mailbox, write it, and return it.

    ONE statement. It sums ``pg_column_size`` of the columns of variable length
    of the messages, their attachment rows and their embeddings. It writes
    ``stored_bytes`` and ``stored_bytes_at``. It returns ``None`` when the
    account row is gone. Takes the caller's session, opens none and never
    commits."""
    value = (await db.execute(_MEASURE, {"aid": account_id})).scalar()
    return None if value is None else int(value)


@dataclass(frozen=True)
class OlderMail:
    """What a removal before a date would remove: messages and bytes."""

    messages: int
    bytes: int


async def preview_older(db: Any, account_id: str, before: datetime) -> OlderMail:
    """The count of messages received before ``before``, and the bytes that
    their removal would free. It writes nothing."""
    row = (await db.execute(
        _PREVIEW, {"aid": account_id, "before": before})).fetchone()
    if row is None:
        return OlderMail(messages=0, bytes=0)
    return OlderMail(messages=int(row.messages or 0), bytes=int(row.bytes or 0))


async def remove_older_chunk(
    db: Any, account_id: str, before: datetime, *, chunk: int = REMOVE_CHUNK,
) -> int:
    """Remove up to ``chunk`` messages received before ``before``, oldest
    first. Returns how many it removed.

    It deletes the ``email_executed_rules`` rows of those messages first, and
    then the messages. It makes no provider call. Takes the caller's session,
    opens none and never commits, so each chunk is one block of the caller."""
    ids = [str(r.id) for r in (await db.execute(
        _CHUNK_IDS,
        {"aid": account_id, "before": before, "lim": chunk})).fetchall()]
    if not ids:
        return 0
    params = {"aid": account_id, "ids": ids}
    await db.execute(_DELETE_EXECUTED, params)
    result = await db.execute(_DELETE_MESSAGES, params)
    removed = getattr(result, "rowcount", None)
    return len(ids) if removed is None or removed < 0 else int(removed)


def _rowcount(result: Any) -> int:
    removed = getattr(result, "rowcount", None)
    return 0 if removed is None or removed < 0 else int(removed)


async def delete_empty_thread_status(db: Any, account_id: str) -> int:
    """Delete each ``email_thread_status`` row of the mailbox whose thread has
    no message left. Returns how many it deleted."""
    return _rowcount(await db.execute(
        _DELETE_EMPTY_THREAD_STATUS, {"aid": account_id}))


async def delete_orphan_ai_drafts(db: Any, account_id: str) -> int:
    """Delete each ``email_ai_drafts`` row of the mailbox whose thread has no
    message left (G5). Returns how many it deleted.

    It touches no Mem0 memory. No Mem0 key names one mail, so the memories of
    the mailbox stay. The purge of a disconnect is the only Mem0 delete."""
    return _rowcount(await db.execute(
        _DELETE_ORPHAN_AI_DRAFTS, {"aid": account_id}))


async def advance_import_since(db: Any, account_id: str, before: datetime) -> None:
    """Move ``import_since`` to the later of itself and ``before``.

    The removal calls it in its first block, before any delete (G3). So a
    removal that fails part way still keeps a Resync from that mail."""
    await db.execute(_ADVANCE_IMPORT_SINCE, {"aid": account_id, "before": before})


async def end_limit_phase(db: Any, account_id: str, stored_bytes: int | None) -> bool:
    """Write ``import_phase = 'done'`` when the phase is ``limit`` and the new
    meter is under the limit (G4). Returns True when it wrote the row.

    ``None`` means the account row is gone, and it writes nothing."""
    if stored_bytes is None or at_limit(stored_bytes):
        return False
    return _rowcount(await db.execute(_END_LIMIT_PHASE, {"aid": account_id})) > 0
