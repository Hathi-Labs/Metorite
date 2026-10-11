"""CRM · auto_lead — an unknown inbound sender becomes a lead (WS-26d-autolead).

Spec: ``project-docs/specs/crm_app.md`` §9 ``WS-26d-autolead`` · D-CRM-9 ·
D-CRM-12. Migration: ``crm_auto_lead_cursors``.

**This module registers no routes.** Like ``broker_handlers``, it is deliberately
absent from ``routes/crm/__init__.py``: its one entry point,
:func:`create_leads_from_new_mail`, is called from the email app's shared
new-mail hook (``routes/email/scheduler_hooks.py::process_new_mail``) — the one
seam the background scheduler, the manual-sync route and the Graph webhook all
funnel through, so mail is considered identically however it arrived.

**It lives in ``routes/crm`` and not in the email package** because what it
does is write a CRM record: it owns a CRM cursor table, it goes through the
CRM's own service write path, and it is gated by a CRM owner gate. It borrows
the email automation package's PUBLIC identity primitives
(``sender_scope`` / ``resolve_org_domains`` / ``normalize_domain``) rather than
copying them — D-CRM-4 declined to import another route package's *private*
helper, and "is this person a colleague?" already has one public answer across
the automation package. A third copy of it here would be the drift the rule
exists to prevent.

Five properties are load-bearing, and each one is a way this feature can do
real damage rather than merely be wrong:

1. **The flag is read BEFORE the step is entered.** ``CRM_AUTO_LEAD`` ships OFF
   and :func:`auto_lead_enabled` is its single definition, but the *call site*
   is what is guarded — with the flag off this module opens no session, issues
   no query and creates nothing, and ``process_new_mail`` does not even import
   :func:`create_leads_from_new_mail`. (It does import *this module* to read
   the predicate; that is one ``sys.modules`` lookup after boot, because
   ``routes/crm`` is mounted by ``main.py`` regardless. One definition of what
   the flag means is worth more than saving it — two places that must agree
   about a flag is how a loop runs with the flag off.)

2. **THREE cursor facts, and they answer three different questions.**
   ``process_new_mail`` is also reached by deep resyncs and by the first-ever
   sync of a newly connected mailbox, and neither marks the mail it classifies
   as history.

   * ``activated_at`` — the start of the CURRENT ON epoch.
     ``received_at > activated_at`` is the backfill discriminator: mail that
     ARRIVED before this epoch began mints nothing, whenever it is classified.
   * ``processed_watermark`` — the incremental cursor over
     ``rules_processed_at``.
   * ``last_run_at`` — when this step last RAN. It is what detects dormancy,
     and on a gap it CLAMPS ``activated_at`` forward to
     ``now - REANCHOR_GAP_SECONDS`` rather than resetting it, so the OFF/outage
     backlog stays excluded while the message that woke the step survives.
     ⚠️ The step is invoked only when a sync PERSISTED mail
     (``email_ingestion/scheduler.py:463-472``), never once per period, so this
     column trips overnight and over weekends routinely — which is why
     re-anchoring has to be cheap. See :func:`_reanchor_if_dormant`.

   Without the first, connecting a second mailbox mints a lead per unknown
   sender in a year of mail — each born ``zoho_dirty`` and queued for the live
   Zoho tenant within one 600s cycle.

3. **"Unknown" is three questions, and "external" is only necessary.**
   :func:`_is_unknown_sender` mirrors ``senders._maybe_block_cold``'s two steps
   (the cold-sender memo, then "have we ever emailed them") and adds a third
   (no ``crm_contacts`` / ``crm_leads`` row already carries the address).
   Separately, :func:`_is_external_sender` runs TWO gates, because
   ``sender_scope`` fails SAFE to ``"external"`` — the wrong direction here. A
   lead row for your own CFO, pushed into the live Zoho tenant, is what the
   second gate exists to prevent, and it is SUFFIX-aware: a colleague on
   ``mail.fracktal.in`` is a colleague.

4. **The first activity is metadata, never content** (D-CRM-12 applied to what
   a machine writes). ``type='system'`` — deliberately outside the Zoho push
   predicate, so the activity never leaves the native CRM — subject and sender
   in ``meta``, and ``body`` empty. Sender + subject is the proportionate
   disclosure for a cold inbound inquiry on an org-visible record; the mail
   body is not, and no snippet of it is either. This module never reads
   ``body_text`` or ``snippet``.

5. **The watermark advances over the successful PREFIX only.** A failure is not
   a reason to skip work: the step opens a second session per lead (through
   ``create_record``) while holding the batch's own, so pool exhaustion fails
   many candidates at once, and an unconditional advance would step over every
   one of them permanently. On the first lead-write failure the cursor stops
   moving and the cycle logs ``sync.auto_lead_stalled`` at WARNING every cycle
   until a human looks. That trades silent loss for a visible stall on a poison
   head message, deliberately: fail closed toward the CRM.

**The tenant is the one the email hook bound** (WS-53 CRM-T3,
``crm_platform.md`` §4.3). :func:`create_leads_from_new_mail` reads
``current_tenant()`` once and opens every session as ``tenant_session(org)``.
With no tenant bound it opens no session at all. No step calls ``.commit()``:
a commit inside a ``tenant_session`` ends ``SET LOCAL``, and each statement
after it would run with no tenant. ``tests/unit/test_crm_sync_tenancy.py``
fences both.

**One race is accepted and recorded** (spec §9): two concurrent
``process_new_mail`` invocations for one account can read the same watermark
and double-mint. The cost is one visible, hand-deletable duplicate lead. The
fix that suggests itself — a UNIQUE index on ``crm_leads.email`` — is a
deploy-blocking constraint on a column where 1,516 imported rows may already
carry duplicates, which is exactly the shape migration 148 had to defuse. Do
not add it.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from acb_auth import UserContext, UserRole
from acb_common import get_settings
from acb_common.db import bind_tenant, current_tenant, release_tenant
from gateway.routes.crm.core import (
    LEADS,
    LeadIn,
    _log,
    _tenant_session,
    bump_last_activity,
    insert_row,
    is_mirror_mode,
    now,
    savepoint,
)
from gateway.routes.crm.records import create_record
from gateway.routes.email.automation.identity import (
    normalize_domain,
    resolve_org_domains,
    sender_scope,
)
from sqlalchemy import text

#: How many candidate messages one cycle may consider. A cap rather than a
#: full drain because this runs inside the mail-sync hook: an unbounded batch
#: after a busy weekend would hold the hook (and, transitively, the account's
#: next sync) for as long as it took. The remainder is NOT dropped — the
#: watermark advances only over what was considered, so the next cycle picks it
#: up — and the overflow is COUNTED in the log line, because silent truncation
#: reads as "covered everything".
MAX_CANDIDATES_PER_CYCLE = 200

#: How long a gap in this step's own RUNS means the ON epoch ended — and,
#: because the anchor is CLAMPED to it rather than reset, also how much recent
#: mail survives that gap.
#:
#: ⚠️ **This is not "six scheduler periods".** The step does not run once per
#: period: ``email_ingestion/scheduler.py:463-472`` fires the hook only when a
#: sync actually persisted mail, so a quiet mailbox does not run it at all.
#: What this measures is therefore "how long since mail last arrived AND was
#: processed", which on a real mailbox means it trips overnight and over
#: weekends as a matter of course. That is precisely why the re-anchor clamps
#: instead of resetting: tripping has to be cheap, because it is routine.
#:
#: One hour is the trade. Larger admits more of a genuine OFF window's backlog;
#: smaller starts excluding mail that arrived during an ordinary evening lull
#: just before the message that woke the step.
#:
#: The guard itself is not optional: ``activated_at`` alone stops a deep
#: resync, but it does nothing about a flag that was on, turned off for four
#: weeks, and turned back on — the cursor still carries day-1's anchor, so the
#: first ON cycle would mint the entire OFF window in one batch, measured at 27
#: leads for a 27-day window, each pushing unattended into the live tenant.
REANCHOR_GAP_SECONDS = 3600

#: The activity ``type`` the originating message is logged as. **Not 'note'.**
#: ``sync_zoho.push_activities`` pushes ``type IN ('note', 'task')`` only, so
#: 'system' is how this row stays inside the native CRM — the mail's subject
#: and sender never reach the Zoho tenant even though the lead itself does
#: (D-CRM-9). ``tests/unit/test_crm_auto_lead.py`` pins the exclusion against
#: that statement's own text rather than trusting this comment.
ACTIVITY_TYPE = "system"

#: ``crm_leads.source`` — the vocabulary is CHECK-constrained by migration 144
#: and validated at the boundary by ``core.validate_source``.
LEAD_SOURCE = "email"

#: The folder a candidate must be in, and the folder the "have we ever emailed
#: them" probe reads. Bound as parameters rather than written as SQL literals
#: so both are one lowercase comparison against a value the caller can see.
INBOX_FOLDER = "inbox"
SENT_FOLDER = "sent"

#: Ceilings on the two attacker-controlled strings that reach TEXT columns —
#: the sender's display name (which becomes ``first_name``/``last_name`` and
#: therefore ``lead_name``) and the subject line. Nothing upstream bounds
#: either: a display name is whatever the sending server put in the header, and
#: it lands in a column every CRM list, board card and Zoho push then carries.
MAX_NAME_CHARS = 120
MAX_SUBJECT_CHARS = 500
#: Appended when either ceiling bites, so a clipped value reads as clipped
#: rather than as the sender's actual name.
CLIP_MARKER = "…"

#: The candidate predicate, written ONCE and shared by the fetch and the
#: overflow count. Two copies would let the count answer a different question
#: from the batch — and the count is the number an operator reads to decide
#: whether the cap is hurting.
_CANDIDATE_WHERE = (
    "account_id = :account_id "
    "AND LOWER(folder) = :folder "
    "AND rules_processed_at IS NOT NULL "
    "AND rules_held_back_at IS NULL "
    "AND received_at > :activated_at "
    "AND rules_processed_at > :watermark"
)

#: ⚠️ The projection is the privacy boundary: ``body_text`` and ``snippet`` are
#: absent on purpose and must stay absent. Nothing downstream can leak a body
#: it was never handed.
#:
#: ``ORDER BY rules_processed_at, id`` — the ``id`` is a TIEBREAK, and without
#: it the ordering of rows sharing a timestamp is undefined, so the cap could
#: cut a tie group in a different place than the watermark assumes and lose the
#: rows in between. See :func:`_drop_boundary_group`.
_CANDIDATE_SQL = (
    "SELECT id, subject, from_address, received_at, thread_id, "
    "internet_message_id, rules_processed_at "
    f"FROM email_messages WHERE {_CANDIDATE_WHERE} "
    "ORDER BY rules_processed_at, id LIMIT :limit"
)

_CANDIDATE_COUNT_SQL = f"SELECT COUNT(*) FROM email_messages WHERE {_CANDIDATE_WHERE}"

#: "Have we ever emailed them" — ``_maybe_block_cold``'s second step, with the
#: one difference this module needs.
#:
#: ⚠️ That helper asks it with jsonb containment (``to_addresses @> :json``),
#: which Postgres evaluates EXACTLY: an owner who wrote to
#: ``Asha@AcmeRobotics.com`` has not, as far as ``@>`` is concerned, emailed
#: ``asha@acmerobotics.com`` — and she replies in lower case, so the reply
#: minted a lead for somebody already in the middle of a conversation. Here the
#: probe folds case explicitly. ``_maybe_block_cold`` is left alone: it is the
#: email package's predicate and its blast radius is the cold-email blocker,
#: not this.
_EVER_EMAILED_SQL = (
    "SELECT 1 FROM email_messages "
    "WHERE account_id = :account_id AND LOWER(folder) = :folder "
    "AND EXISTS (SELECT 1 FROM jsonb_array_elements(to_addresses) recipient "
    "WHERE lower(recipient->>'email') = :address) LIMIT 1"
)


def auto_lead_enabled() -> bool:
    """``CRM_AUTO_LEAD`` — the single definition of what the flag means.

    Ships OFF. ON means unknown inbound senders become CRM leads unattended,
    each born ``zoho_dirty`` and therefore queued for the LIVE Zoho tenant
    (D-CRM-9) with no confirmation card anywhere on a scheduler hook and no
    delete tool. Flipping it is an OWNER-GATE act (``work_plan.md`` §6 (b)).

    Read by ``process_new_mail`` BEFORE it enters the step, never inside it —
    see property 1 in the module docstring.
    """
    return bool(getattr(get_settings(), "crm_auto_lead", False))


def _new_stats() -> dict[str, int]:
    """The counters the log line carries. Declared in one place so a counter
    added later cannot be missing from the line that reports it."""
    return {
        "candidates": 0,
        "overflow": 0,
        "boundary_deferred": 0,
        "created": 0,
        "skipped_internal": 0,
        "skipped_known": 0,
        "skipped_unusable": 0,
        "deduped_in_batch": 0,
        "errors": 0,
        "activity_errors": 0,
        "reanchored": 0,
    }


async def create_leads_from_new_mail(account_id: str) -> dict[str, int]:
    """Turn this account's newly-classified inbound mail into leads.

    The entry point ``process_new_mail`` calls, and the only public write path
    in this module. Returns its counters so a caller (and a test) can see what
    a cycle considered rather than only what it created — "created 0" and
    "considered 0" are different facts and one of them is a bug report.
    """
    stats = _new_stats()
    # WS-53 CRM-T3 (`crm_platform.md` §4.3). The tenant is the one the email
    # hook ALREADY bound: the sync loop, the Graph webhook and the request
    # jobs each bind the mailbox's organization before `process_new_mail`
    # runs (`routes/email/scheduler_hooks.py`). It is read once, here, and
    # passed to every session by value. With no tenant bound, the step opens
    # NO session — every `crm_*` table has FORCE RLS, so an unbound session
    # reads nothing and refuses every write.
    #
    # Each step is its own `tenant_session(org)`, and none calls `.commit()`:
    # a commit ends `SET LOCAL`, so each statement after it would run with no
    # tenant. The fence is `tests/unit/test_crm_sync_tenancy.py`.
    org = current_tenant()
    if not org:
        _log.warning("crm.auto_lead_unbound", account_id=account_id)
        return stats
    async with _tenant_session(org) as db:
        account = await _load_account(db, account_id, org)
    if account is None:
        # The row is gone, has no owner, or belongs to ANOTHER tenant — under
        # FORCE RLS the three read the same. Write nothing in each case.
        _log.warning("crm.auto_lead_account_unavailable", account_id=account_id)
        return stats
    async with _tenant_session(org) as db:
        cursor = await _load_or_activate_cursor(db, account_id)
    if cursor is None:
        # The insert was `ON CONFLICT DO NOTHING`, so None means a cursor row
        # for this account exists that this tenant cannot see. Say so: a
        # silent return here stops auto-lead for the mailbox with no trace.
        _log.warning("crm.auto_lead_cursor_unavailable", account_id=account_id)
        return stats
    async with _tenant_session(org) as db:
        cursor, reanchored = await _reanchor_if_dormant(db, account_id, cursor)
    stats["reanchored"] = int(reanchored)
    # ⚠️ NO early return here, and that is the whole correction. This step
    # is only ever invoked because a sync PERSISTED mail, so the batch that
    # tripped the dormancy test is the batch containing the message that
    # woke it up. Returning early discarded exactly that message, forever.
    async with _tenant_session(org) as db:
        await _run_batch(db, org, account, cursor, stats)
    _emit(account_id, stats)
    return stats


async def _run_batch(
    db: Any, org: str, account: Any, cursor: Any, stats: dict[str, int],
) -> None:
    """One cycle's candidates, considered in order, cursor stamped once.

    Runs inside the caller's ``tenant_session(org)``, which commits the
    activities and the cursor stamp together when the block exits.
    """
    account_id = str(account.id)
    if await is_mirror_mode(db, org):
        # Mirror mode (CRM-Z6): Zoho owns new leads, so create none. Advance
        # the watermark, or every cycle re-reads the same batch.
        candidates, _next = await _load_candidates(db, account_id, cursor)
        stats["candidates"] = len(candidates)
        stamp = candidates[-1].rules_processed_at if candidates else None
        if candidates:
            _log.info("crm.auto_lead.skipped_mirror", account_id=account_id,
                      candidates=len(candidates))
        await _stamp_cursor(db, account_id, watermark=stamp, run_at=now())
        return
    candidates, next_stamp = await _load_candidates(db, account_id, cursor)
    capped = next_stamp is not None
    if capped and candidates[-1].rules_processed_at == next_stamp:
        # …and ONLY then. A cap that fell between two timestamp groups cut
        # nothing in half, so deferring its last group would shrink the batch
        # by one message every single cycle for no reason.
        candidates = _drop_boundary_group(candidates, stats)
    stats["candidates"] = len(candidates)
    if not candidates:
        # A cap that dropped a whole batch into the boundary group is a stall,
        # not a quiet cycle, and the two must not read the same in the log.
        if stats["boundary_deferred"]:
            _log.warning("sync.auto_lead_stalled", account_id=account_id,
                         reason="boundary_group_fills_the_cap",
                         boundary_deferred=stats["boundary_deferred"])
        await _stamp_cursor(db, account_id, run_at=now())
        return
    if capped:
        stats["overflow"] = await _count_overflow(
            db, account_id, cursor, len(candidates),
        )
    domains = await _internal_domains(db, account_id, account.email_address)
    seen: set[str] = set()

    #: The stamp of the last message in the contiguous prefix that did NOT
    #: fail to write its lead. Everything after the first failure is
    #: deliberately left for the next cycle: re-considering a message whose
    #: lead already exists costs one indexed SELECT (step 3 of
    #: `_is_unknown_sender` finds it and skips), and that is far cheaper than
    #: the alternative, which is stepping the cursor over work that was lost.
    advance_to: Any = None
    blocked = False
    for message in candidates:
        failed = await _consider(
            db, org, account, message, domains, seen, stats,
        )
        if failed:
            blocked = True
        elif not blocked:
            advance_to = message.rules_processed_at

    await _stamp_cursor(db, account_id, watermark=advance_to, run_at=now())
    if advance_to is None and stats["errors"]:
        # WARNING, and it repeats every cycle: an INFO line saying "created 0"
        # is what a stuck head message looked like before, which is to say it
        # looked like a quiet mailbox.
        _log.warning("sync.auto_lead_stalled", account_id=account_id,
                     reason="lead_write_failed_on_the_first_candidate",
                     candidates=stats["candidates"], errors=stats["errors"])


# ── The account, and the cursor that decides what is history ────────────────

async def _load_account(db: Any, account_id: str, org: str) -> Any | None:
    """The mailbox's own address and its owner.

    ``email_accounts.user_id`` holds the owner's EMAIL (the column is TEXT and
    every reader compares it to one — see ``routes/email/core._account_scope``),
    which is what makes a created lead somebody's follow-up rather than
    'anonymous'. An account with no owner recorded is skipped: attributing a
    lead to nobody is worse than not creating it.

    ⚠️ The ``organization_id`` predicate is not redundant with RLS. FORCE RLS
    on ``email_accounts`` lives only in the generated phase 4, which production
    has not applied, so without the predicate a call bound to tenant Y would
    read tenant X's mailbox and write X's senders into Y's CRM. Every later
    read of the batch keys on this account, so this one check gates them all.
    The email scheduler filters each per-org statement the same way.
    """
    row = (await db.execute(text(
        "SELECT id, user_id, email_address FROM email_accounts "
        "WHERE id = :id AND organization_id = CAST(:org AS uuid)"
    ), {"id": account_id, "org": org})).fetchone()
    if row is None or not str(getattr(row, "user_id", "") or "").strip():
        return None
    return row


async def _read_cursor(db: Any, account_id: str) -> Any | None:
    return (await db.execute(text(
        "SELECT account_id, activated_at, processed_watermark, last_run_at "
        "FROM crm_auto_lead_cursors WHERE account_id = :account_id"
    ), {"account_id": account_id})).fetchone()


async def _load_or_activate_cursor(db: Any, account_id: str) -> Any | None:
    """Read the account's cursor, creating it on the first ON-state run.

    Activation stamps all three timestamps to the SAME instant, so the
    activating cycle itself mints nothing: everything already in the mailbox
    arrived before auto-lead existed for this account, which is precisely the
    deep-resync case. ``ON CONFLICT DO NOTHING`` + a re-read means a concurrent
    activation is one row and one activation instant, not a primary-key error
    on the mail path.

    Runs inside the caller's ``tenant_session(org)``, which commits the row
    when its block exits. The row's ``organization_id`` comes from the column
    DEFAULT, which reads the bound tenant (migration 241).
    """
    row = await _read_cursor(db, account_id)
    if row is not None:
        return row
    at = now()
    await db.execute(text(
        "INSERT INTO crm_auto_lead_cursors "
        "(account_id, activated_at, processed_watermark, last_run_at) "
        "VALUES (:account_id, :activated_at, :processed_watermark, :last_run_at) "
        "ON CONFLICT (account_id) DO NOTHING"
    ), {"account_id": account_id, "activated_at": at,
        "processed_watermark": at, "last_run_at": at})
    _log.info("crm.auto_lead_activated", account_id=account_id)
    return await _read_cursor(db, account_id)


def _aware(value: Any) -> datetime:
    """A stored instant as tz-aware UTC.

    ``TIMESTAMPTZ`` comes back aware from asyncpg; this exists so that a naive
    value from any other source cannot raise mid-cycle on the subtraction and
    take the whole step down through the hook's ``except``.
    """
    if not isinstance(value, datetime):
        return now()
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


async def _reanchor_if_dormant(
    db: Any, account_id: str, cursor: Any,
) -> tuple[Any, bool]:
    """Did this step stop running? Then CLAMP the epoch — never reset it.

    ``activated_at`` guards a deep RESYNC. It does nothing about a flag that
    was on, turned off for four weeks and turned back on — the cursor still
    carries the old anchor, so the first ON cycle would mint the whole OFF
    window at once, unattended, into a live tenant (27 leads, measured).

    **The anchor is clamped to ``now - REANCHOR_GAP_SECONDS``, not set to
    ``now``, and the batch still runs.** Both halves of that are load-bearing,
    and the first version of this function got both wrong for the same reason:
    it assumed the step is invoked once per scheduler period. **It is not.**
    ``email_ingestion/scheduler.py:463-472`` reads ``synced`` off the sync
    result and calls the hook **only when mail was actually persisted**, so a
    mailbox with no new mail does not run this step at all — which means the
    cycle that trips the dormancy test is *always* the cycle carrying the
    message that woke it. Resetting the anchor to ``now`` and returning early
    therefore excluded that message permanently: no mail 22:00→07:30, a cold
    prospect writes at 07:30:50, the step runs at 07:31:05, and the one lead it
    existed to catch fell a quarter-minute the wrong side of its own anchor.
    Every night, every weekend.

    Clamping keeps the fail-closed property that matters — anything received
    longer ago than the gap width stays excluded, so a real OFF window or a
    multi-day outage still mints nothing from its backlog — while admitting
    everything received inside the last ``REANCHOR_GAP_SECONDS``, which is
    where the triggering message always is.

    ⚠️ **Documented residual:** mail received in the final hour of an OFF
    window IS minted when the flag comes back on. It is bounded by TIME, not by
    one batch — one gap-width of mail (an hour), drained across however many
    cycles the per-cycle cap takes — and it is the deliberate price of never
    dropping the message that woke the step. `crm_app.md` §9 records it and
    ``work_plan.md`` §6 (b) tells the owner about it before they flip.

    ``last_run_at`` rather than ``processed_watermark`` is still the clock
    here, but the honest reason is narrower than first claimed: both freeze on
    a mailbox that receives nothing, since neither advances when the hook never
    fires. What separates them is a cycle that ran and found no *candidates* —
    mail that landed outside the inbox, or was held back, or predates the
    anchor. That cycle advances ``last_run_at`` and not the watermark, and it
    is also exactly the state a deliberate stall (property 5) holds the
    watermark in; keying dormancy on the watermark would re-anchor past a stall
    after an hour and quietly undo it.

    Returns the cursor to run the batch against — re-read, because the anchor
    it carries has just changed.
    """
    gap = (now() - _aware(cursor.last_run_at)).total_seconds()
    if gap <= REANCHOR_GAP_SECONDS:
        return cursor, False
    # `max` is a floor, not arithmetic: inside this branch `last_run_at` is
    # always older than the window, so it resolves to the window's start. It
    # exists so a clock that jumped backwards cannot move the anchor EARLIER
    # than the last known-good run and re-admit history.
    anchor = max(
        _aware(cursor.last_run_at),
        now() - timedelta(seconds=REANCHOR_GAP_SECONDS),
    )
    await db.execute(text(
        "UPDATE crm_auto_lead_cursors "
        "SET activated_at = :activated_at, updated_at = now() "
        "WHERE account_id = :account_id"
    ), {"account_id": account_id, "activated_at": anchor})
    # The watermark is deliberately NOT touched: OFF-window mail is excluded by
    # the anchor predicate whatever its `rules_processed_at` says, and moving
    # the watermark forward here would skip the triggering batch a second way.
    _log.warning("sync.auto_lead_reanchored", account_id=account_id,
                 gap_seconds=int(gap), threshold_seconds=REANCHOR_GAP_SECONDS,
                 anchored_at=anchor.isoformat())
    return await _read_cursor(db, account_id), True


async def _stamp_cursor(
    db: Any, account_id: str, *, run_at: Any, watermark: Any = None,
) -> None:
    """Record that the step ran, and how far it got.

    ``last_run_at`` moves on EVERY cycle — that is what stops a quiet mailbox
    looking dormant. ``processed_watermark`` moves only when there is a
    successful prefix to move it over, which is what stops a failure being
    treated as work done.
    """
    assignments = ["last_run_at = :last_run_at", "updated_at = now()"]
    params: dict[str, Any] = {"account_id": account_id, "last_run_at": run_at}
    if watermark is not None:
        assignments.insert(0, "processed_watermark = :processed_watermark")
        params["processed_watermark"] = watermark
    await db.execute(text(
        f"UPDATE crm_auto_lead_cursors SET {', '.join(assignments)} "
        "WHERE account_id = :account_id"
    ), params)


def _cursor_params(account_id: str, cursor: Any) -> dict[str, Any]:
    return {
        "account_id": account_id,
        "folder": INBOX_FOLDER,
        "activated_at": cursor.activated_at,
        "watermark": cursor.processed_watermark,
    }


async def _load_candidates(
    db: Any, account_id: str, cursor: Any,
) -> tuple[list[Any], Any]:
    """Classified inbox mail this account has not been considered for yet.

    Both cursor predicates apply. Ordered by ``rules_processed_at`` ascending
    so the batch's maximum IS the new watermark — ordering by ``received_at``
    would advance the cursor past messages the cap left behind.

    Fetches one MORE than the cap and returns that extra row's stamp as the
    second element (``None`` when the batch was short). Peeking one row is what
    turns "there is more" from an inference off a full page into a fact, and it
    is also the only way to know whether the cap fell *inside* a group of rows
    sharing a timestamp — see the caller.
    """
    rows = list((await db.execute(
        text(_CANDIDATE_SQL),
        {**_cursor_params(account_id, cursor),
         "limit": MAX_CANDIDATES_PER_CYCLE + 1},
    )).fetchall())
    if len(rows) > MAX_CANDIDATES_PER_CYCLE:
        return (rows[:MAX_CANDIDATES_PER_CYCLE],
                rows[MAX_CANDIDATES_PER_CYCLE].rules_processed_at)
    return rows, None


def _drop_boundary_group(
    candidates: list[Any], stats: dict[str, int],
) -> list[Any]:
    """Drop the trailing rows that share the cap's last timestamp.

    The watermark is a timestamp, so advancing it to a stamp the cap cut
    *through* would step over the rest of that group forever. Dropping the
    whole boundary group means the next cycle sees it complete; the rows come
    back deduped, so re-considering them is free.

    ⚠️ Theoretically this can empty the batch — if every row in a full cap
    shares one stamp, nothing is left and the cursor cannot move. That is
    reported as a stall rather than as a quiet cycle. It cannot happen in
    production: ``rules_processed_at`` is stamped per message inside the rules
    runner's own loop, one transaction per message, so 200 messages carrying a
    single identical instant would require 200 messages to be stamped by one
    statement — which no writer of that column does.
    """
    boundary = candidates[-1].rules_processed_at
    kept = [row for row in candidates if row.rules_processed_at != boundary]
    stats["boundary_deferred"] = len(candidates) - len(kept)
    return kept


async def _count_overflow(
    db: Any, account_id: str, cursor: Any, taken: int,
) -> int:
    """How many candidates the cap left for the next cycle.

    Only asked when the fetch came back over the cap — a short batch is the
    whole remainder by definition, and a COUNT on every quiet cycle is a scan
    nobody reads.
    """
    total = (await db.execute(
        text(_CANDIDATE_COUNT_SQL), _cursor_params(account_id, cursor),
    )).scalar()
    return max(0, int(total or 0) - taken)


# ── Who the sender is ───────────────────────────────────────────────────────

async def _internal_domains(
    db: Any, account_id: str, account_address: str | None,
) -> frozenset[str]:
    """The account's own domain plus every configured org domain.

    Composed from the automation package's two PUBLIC helpers rather than
    imported from ``cleanup._internal_domains`` (private) or restated here:
    ``resolve_org_domains`` is the one place the ``org_domains`` setting is
    normalised, and reading that column raw is a documented defect — a user who
    typed ``ops@acme.com`` or ``@acme.com`` has their org recognised by
    ``normalize_domain`` and NOT by an ad-hoc ``lstrip('@')``.
    """
    domains = {normalize_domain(account_address or "")}
    domains |= set(await resolve_org_domains(db, account_id))
    return frozenset(domain for domain in domains if domain)


def _is_internal_domain(domain: str, internal_domains: frozenset[str]) -> bool:
    """Is this domain ours, or a SUBDOMAIN of one of ours?

    Exact matching alone let ``cfo@mail.fracktal.in`` through while
    ``cfo@fracktal.in`` was caught — and mail from a company's own
    ``mail.``/``corp.``/regional subdomains is routine. The suffix test is
    anchored on a leading dot so ``notfracktal.in`` is not a subdomain of
    ``fracktal.in``, which is the mistake a bare ``endswith`` makes.
    """
    return any(
        domain == internal or domain.endswith(f".{internal}")
        for internal in internal_domains
    )


def _is_external_sender(
    address: str, account_address: str | None, internal_domains: frozenset[str],
) -> bool:
    """Two gates, answering two different questions. Both must pass.

    **Gate 1** is ``sender_scope`` — the automation package's own answer to "is
    this me, or my own domain?", asked from the account's address alone.

    **Gate 2** is the configured internal-domain list. It is not a restatement
    of gate 1: ``sender_scope`` fails SAFE to ``"external"`` on an unparseable
    address (the wrong direction when the consequence is a lead row in a live
    Zoho tenant), the extra ``org_domains`` reach it through a different
    normalisation than ``resolve_org_domains`` applies, and it matches domains
    exactly where this one matches subdomains too. Routing the configured list
    through gate 2 only means there is exactly one normalisation of it here
    rather than two that can disagree.

    An address with no domain at all fails gate 2: we do not mint a lead from
    something we could not parse.
    """
    if sender_scope(address, account_address or "") != "external":
        return False
    domain = normalize_domain(address)
    return bool(domain) and not _is_internal_domain(domain, internal_domains)


async def _is_unknown_sender(db: Any, account_id: str, address: str) -> bool:
    """Three steps — the two ``_maybe_block_cold`` already answers, plus ours.

    1. the cold-sender memo: this account has already decided something about
       this address (flagged cold, or whitelisted). Either way it is not a
       stranger who just wrote in.
    2. have we ever emailed them — case-insensitively, unlike the containment
       form the cold blocker uses. See :data:`_EVER_EMAILED_SQL`.
    3. ours: no CRM row already carries the address. A contact or a lead means
       the company already knows this person, and a second lead for them is
       the duplicate a human then has to merge. This step is also what makes
       re-considering a message free, which is what lets the watermark hold
       still after a failure without costing anything.
    """
    memo = (await db.execute(text(
        "SELECT status FROM email_cold_senders "
        "WHERE account_id = :account_id AND from_email = :address"
    ), {"account_id": account_id, "address": address})).fetchone()
    if memo:
        return False
    replied = (await db.execute(text(_EVER_EMAILED_SQL), {
        "account_id": account_id, "folder": SENT_FOLDER, "address": address,
    })).fetchone()
    if replied:
        return False
    for table in ("crm_contacts", "crm_leads"):
        known = (await db.execute(text(
            f"SELECT 1 FROM {table} WHERE lower(email) = :address LIMIT 1"
        ), {"address": address})).fetchone()
        if known:
            return False
    return True


# ── One candidate ───────────────────────────────────────────────────────────

def _clip(value: str | None, limit: int) -> str | None:
    """Bound one attacker-controlled string, marking it when it bites."""
    if value is None:
        return None
    text_value = str(value)
    if len(text_value) <= limit:
        return text_value
    return text_value[: limit - len(CLIP_MARKER)] + CLIP_MARKER


def _sender(message: Any) -> tuple[str, str]:
    """``(address, display name)`` off the message's ``from_address`` JSONB.

    The driver hands back a dict or the raw text depending on how the row was
    read; both shapes appear in this codebase, so both are handled here rather
    than at four call sites. The display name is CLIPPED here, once, so every
    consumer of it downstream is bounded by construction.
    """
    raw = getattr(message, "from_address", None)
    if not isinstance(raw, dict):
        try:
            raw = json.loads(raw or "{}")
        except (TypeError, ValueError):
            raw = {}
    if not isinstance(raw, dict):
        raw = {}
    return (
        str(raw.get("email") or "").strip().lower(),
        _clip(str(raw.get("name") or "").strip(), MAX_NAME_CHARS) or "",
    )


def _split_display_name(raw: str) -> tuple[str | None, str | None]:
    """The mail's display name → ``(first_name, last_name)``.

    **Stripped before it is split.** Providers pad and quote this field, and
    splitting first turns ``'"Asha" '`` into a first name of ``'"Asha"'`` and a
    surname of ``''``. What lands here is then run through §3.3's fallback
    chain by ``create_record`` — this function must never build the display
    name itself, because a name hand-built here would be a SECOND answer to
    "what is this lead called" and the wrong one is the one that gets stored
    (the "Asha Asha" trap, §8 B-series).

    A display name that is an address — which is what every provider puts there
    for a sender who set none — yields ``(None, None)``, so the chain falls
    through to the email local part rather than minting a lead named
    ``noreply@example.com``.
    """
    name = (raw or "").strip().strip('"').strip("'").strip()
    if not name or "@" in name:
        return None, None
    parts = name.split()
    if len(parts) == 1:
        return parts[0], None
    return parts[0], " ".join(parts[1:])


def _principal(owner_email: str) -> UserContext:
    """The identity ``create_record`` attributes the lead to.

    Carries the account owner's address and **no access at all**
    (``UserContext`` defaults to ``NO_ACCESS``): the only thing this principal
    is for is the ``owner_email`` default, and an unattended writer must not
    carry authority it could later be asked for. ``UserRole.AGENT`` says out
    loud that no human is behind this write — the same principal shape
    ``routes/apps/actions.py`` uses for an agent-initiated action.
    """
    return UserContext(email=owner_email.strip(), role=UserRole.AGENT)


async def _consider(
    db: Any, org: str, account: Any, message: Any,
    internal_domains: frozenset[str], seen: set[str], stats: dict[str, int],
) -> bool:
    """Decide about ONE candidate message, and count the decision.

    Returns True when the LEAD write failed — the only outcome that must hold
    the watermark. A failure to write the lead's first activity does not: the
    lead is already committed, so re-considering the message next cycle finds
    it at step 3 and skips, meaning the activity would never be retried and
    the cursor would stall forever on work that cannot be redone.
    """
    address, display_name = _sender(message)
    if not address:
        stats["skipped_unusable"] += 1
        return False
    if address in seen:
        # In-batch de-duplication. Not an optimisation: the SELECT guard in
        # `_is_unknown_sender` reads a row `create_record` has committed on
        # ANOTHER session, so without this a sender who wrote twice in one
        # batch is a coin flip between one lead and two.
        stats["deduped_in_batch"] += 1
        return False
    seen.add(address)
    if not _is_external_sender(address, account.email_address, internal_domains):
        stats["skipped_internal"] += 1
        return False
    if not await _is_unknown_sender(db, str(account.id), address):
        stats["skipped_known"] += 1
        return False
    try:
        lead = await _create_lead(org, account, address, display_name)
    except Exception as exc:
        stats["errors"] += 1
        _log.warning("crm.auto_lead_message_failed",
                     account_id=str(account.id),
                     message_id=str(getattr(message, "id", "")),
                     error=str(exc)[:200])
        return True
    # Counted the moment the lead is COMMITTED, not after its activity: the
    # row exists and will push to Zoho either way, and a log line reading
    # `created=0` beside a lead that is already queued is the report that
    # sends somebody looking in the wrong place.
    stats["created"] += 1
    try:
        async with savepoint(db):
            await _log_origin_activity(
                db, lead, account, message, address, display_name,
            )
    except Exception as exc:
        stats["activity_errors"] += 1
        _log.warning("crm.auto_lead_activity_failed",
                     account_id=str(account.id),
                     lead_id=str(lead.get("id", "")),
                     message_id=str(getattr(message, "id", "")),
                     error=str(exc)[:200])
    return False


async def _create_lead(
    org: str, account: Any, address: str, display_name: str,
) -> dict[str, Any]:
    """Create the lead through the CRM's own service write path.

    ``records.create_record`` — never a raw INSERT. That path is where
    ``_resolve_status`` (the NOT NULL ``status_id``), the ``owner_email``
    default, ``validate_source`` and, through ``insert_row``,
    ``mark_dirty_on_insert`` all live; raw SQL silently loses all four, and
    only the last of them is visible in the row afterwards.

    It opens and commits its OWN session (it is the same function
    ``POST /crm/leads`` calls), which is also why the caller counts a created
    lead before it writes the activity: by the time this returns, the row is
    committed and queued for Zoho.

    ``create_record`` opens ``_tenant_session()`` with the tenant of the
    context. This function binds *org* around the call, so the lead lands in
    the tenant the email hook bound and in no other (WS-53 CRM-T3).
    """
    first_name, last_name = _split_display_name(display_name)
    # Absent, never explicitly null: ``clean_payload`` is ``exclude_unset``, so
    # a field passed as None is a deliberate "clear this" that lands in the
    # INSERT as a NULL column. Omitting it lets the column's own default apply,
    # which is the difference between "we did not learn a surname" and "this
    # lead has no surname".
    fields: dict[str, Any] = {"email": address, "source": LEAD_SOURCE}
    if first_name:
        fields["first_name"] = first_name
    if last_name:
        fields["last_name"] = last_name
    owner = str(account.user_id)
    token = bind_tenant(org)
    try:
        return await create_record(
            LEADS, LeadIn(**fields), _principal(owner),
        )
    finally:
        release_tenant(token)


async def _log_origin_activity(
    db: Any, lead: dict[str, Any], account: Any, message: Any,
    address: str, display_name: str,
) -> None:
    """The lead's first timeline entry: metadata about the mail, never the mail.

    ``type='system'`` keeps it out of ``sync_zoho.push_activities``' ``type IN
    ('note', 'task')`` predicate, so the subject and the sender stay inside the
    native CRM even though the lead itself is queued for the tenant (D-CRM-9).
    ``body`` is empty and stays empty: the record is org-visible to every
    ``feature:crm`` holder (D-CRM-3), and sender + subject is the proportionate
    disclosure for a cold inbound inquiry — the same line D-CRM-12 draws for
    what the agent renders, applied to what a machine writes.
    """
    await insert_row(db, "crm_activities", {
        "type": ACTIVITY_TYPE,
        "subject": _clip(getattr(message, "subject", None), MAX_SUBJECT_CHARS),
        "body": None,
        "occurred_at": getattr(message, "received_at", None),
        "meta": {
            "source": "auto_lead",
            "sender_name": display_name or None,
            "sender_address": address,
            "received_at": _instant(getattr(message, "received_at", None)),
            "message_id": str(getattr(message, "id", "") or "") or None,
            "internet_message_id": getattr(message, "internet_message_id", None),
            "thread_id": getattr(message, "thread_id", None),
            "account_id": str(account.id),
        },
        "created_by": str(account.user_id),
        LEADS.activity_column: lead["id"],
    })
    await bump_last_activity(db, LEADS.table, lead["id"])


def _instant(value: Any) -> str | None:
    """A timestamp as text for the ``meta`` blob — jsonb holds no datetimes."""
    if value is None:
        return None
    isoformat = getattr(value, "isoformat", None)
    return isoformat() if callable(isoformat) else str(value)


def _emit(account_id: str, stats: dict[str, int]) -> None:
    """One line per cycle, carrying every counter — including ``overflow``.

    ⚠️ Never pass ``event=`` to a structlog logger; it is the message parameter
    and raises at call time.
    """
    _log.info("crm.auto_lead_cycle", account_id=account_id, **stats)
