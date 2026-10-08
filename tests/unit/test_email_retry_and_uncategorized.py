"""Repairing failed rule runs, and what "uncategorized" is allowed to mean.

Three defects, all found by looking at the live account after the Analytics
trust panel shipped.

**1. The scheduler passed a UUID where every signature said str.** asyncpg
returns ``row.id`` as a UUID OBJECT, and the account-sync loop handed it
straight into the shared new-mail pipeline. Anything that merely put it in a SQL
parameter worked, so the wrong type was invisible for months — until the reply
drafter did string work with it and raised ``'asyncpg.pgproto.pgproto.UUID'
object has no attribute 'strip'``. The LABEL action on the same rule succeeded,
so the rule looked fine and simply never produced a draft.

**2. "Uncategorized" was counting classified mail.** The panel reported 142
uncategorized arrivals in 30 days and advised running a backfill. 125 of them
were inbox mail the rules HAD processed — every single one carrying a thread
status (Done / Awaiting / FYI / Needs-reply). Conversation mail is classified
per-THREAD; the per-message ``categories`` array stays empty by design. Nothing
was behind. The panel was inventing work and asking the user to pay for it.

**3. Failed runs had no way back.** 184 rows were stranded APPLIED-but-refused,
and the only repair was re-running the rules, which re-classifies (model calls)
and re-enters the drafting path. A retry should replay the decision already
made, and must never draft or send.
"""
from __future__ import annotations

import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from email_ingestion import scheduler as sched
from gateway.routes.email.automation import analytics as a
from gateway.routes.email.automation import runner as m
from tests.unit._email_fakes import bind_db

# ── the account id is a string, everywhere ──────────────────────────────────


def test_the_sync_loop_launches_with_a_string_account_id() -> None:
    """``row.id`` is a UUID object. It is threaded through sync, rules, drafting
    and memory scoping as ``account_id: str``, so it has to be one."""
    src = inspect.getsource(sched)
    assert "(str(row.id), row.sync_interval_secs)" in src, (
        "passing row.id raw makes the whole new-mail pipeline receive a UUID "
        "where it is annotated str; it fails only where something does string "
        "work with it, which is why the reply drafter was the one to crash"
    )


# ── what a retry is allowed to do ───────────────────────────────────────────


def test_a_retry_never_drafts_sends_or_forwards() -> None:
    """A retry replays a decision made possibly weeks ago. A label and a move
    are idempotent; a reply is not, and sending one unattended as part of a bulk
    repair is exactly the outward-facing surprise a user cannot undo."""
    assert {"REPLY", "DRAFT_EMAIL", "FORWARD"} <= m._RETRY_SKIPPED_ACTIONS
    src = inspect.getsource(m.retry_failed_executions)
    assert "_RETRY_SKIPPED_ACTIONS" in src


def test_a_retry_reclassifies_nothing() -> None:
    """It replays the rule already chosen — no matcher, so no model calls and no
    chance of a different decision on a message the user has since read."""
    src = inspect.getsource(m.retry_failed_executions)
    # The split steps of EM-T4a-2 PR-B1 are matchers too.
    for matcher in ("_match_email_to_rule", "_match_email_to_rules_multi",
                    "read_rule_match", "ask_rule_match", "read_classification"):
        assert matcher not in src, (
            f"{matcher} would turn a repair into a re-run, which is the thing "
            "the user asked to avoid because it re-enters drafting"
        )


def test_a_retry_reads_the_current_message_id() -> None:
    """The stale id is what stranded these rows. Replaying with the id recorded
    on the failed row would reproduce the same 404 forever."""
    src = inspect.getsource(m.retry_failed_executions)
    assert "em.provider_message_id" in src
    assert "er.provider_message_id" not in src


def test_only_failed_rows_are_retried() -> None:
    """APPLIED rows already did their work and LABEL/MOVE would be redundant;
    PENDING is a preview awaiting a human verdict, not a failure."""
    src = inspect.getsource(m.retry_failed_executions)
    assert "er.status = 'FAILED'" in src


def test_a_retry_that_fails_again_stays_failed() -> None:
    """~92 of the live failures are the provider racing us — the message was
    re-keyed or deleted in Outlook. Those cannot be repaired, and flipping them
    to APPLIED would hide them exactly as the original bug did."""
    src = inspect.getsource(m.retry_failed_executions)
    assert 'status = "FAILED" if (errors and not taken) else "APPLIED"' in src


def test_a_retry_never_resurrects_deleted_mail() -> None:
    """The commonest cause of these failures is the provider racing us, and the
    commonest form of that is the user deleting or junking the message — 92 of
    the 138 live failures ended in trash. Replaying a MOVE_FOLDER against one
    would lift it back OUT of the bin into a category folder. A repair must
    never be able to undo a deletion."""
    src = inspect.getsource(m.retry_failed_executions)
    assert "'trash', 'junk', 'spam', 'drafts', 'draft'" in src
    assert "LOWER(COALESCE(em.folder, '')) NOT IN" in src


async def test_nothing_to_retry_is_not_an_error() -> None:
    """The common case once the backlog is cleared: report zero, touch no
    provider, and do not raise."""
    db = AsyncMock()
    db.execute.return_value = MagicMock(
        fetchall=MagicMock(return_value=[]),
        scalar=MagicMock(return_value="u@example.com"),
    )
    import unittest.mock as _mock
    with _mock.patch.object(m, "_tenant_session", bind_db(db)):
        out = await m.retry_failed_executions("acc-1")
    assert out["considered"] == 0 and out["repaired"] == 0


# ── what "uncategorized" means ──────────────────────────────────────────────


async def test_conversation_mail_is_not_uncategorized() -> None:
    """Its classification lives on the THREAD. Counting an empty per-message
    array as unclassified reported 142 arrivals as outstanding work when 125 of
    them were filed correctly, and told the user to spend model calls redoing
    it."""
    db = AsyncMock()
    db.execute.return_value = MagicMock(fetchall=MagicMock(return_value=[]))
    await a._categories(db, "m.account_id = :aid", {"uid": "u", "days": 30},
                        "TRUE", "FALSE")
    sql = str(db.execute.call_args[0][0])
    assert "email_thread_status ts" in sql
    assert "cat IS NULL" in sql, (
        "the thread status must only apply when the MESSAGE has no category, "
        "or a labelled message is counted twice"
    )


async def test_a_thread_status_renders_as_its_display_name() -> None:
    """The thread table stores NEEDS_REPLY / AWAITING; a message label carries
    "Reply" / "Awaiting Reply". Unmapped, the chart grew two rows for one
    thing — "Done 41" directly above "DONE 69"."""
    db = AsyncMock()
    db.execute.return_value = MagicMock(fetchall=MagicMock(return_value=[]))
    await a._categories(db, "m.account_id = :aid", {"uid": "u", "days": 30},
                        "TRUE", "FALSE")
    sql = str(db.execute.call_args[0][0])
    for token in ("'NEEDS_REPLY' THEN 'Needs Reply'",
                  "'AWAITING' THEN 'Awaiting Reply'",
                  "'DONE' THEN 'Done'"):
        assert token in sql


async def test_genuinely_unseen_mail_still_reports_as_uncategorized() -> None:
    """The tightening must not make the bucket unreachable — mail in a
    user-created folder is never touched by the inbox-only rule run, and that
    is a real gap the panel should keep surfacing."""
    db = AsyncMock()
    db.execute.return_value = MagicMock(fetchall=MagicMock(return_value=[
        SimpleNamespace(category="(uncategorized)", count=18, prev_count=4)]))
    out = await a._categories(db, "m.account_id = :aid",
                              {"uid": "u", "days": 30}, "TRUE", "FALSE")
    assert out == [{"category": "(uncategorized)", "count": 18,
                    "prev_count": 4}]
