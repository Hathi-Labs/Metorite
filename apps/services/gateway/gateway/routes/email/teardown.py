"""The teardown of a mailbox: one teardown, three paths (WS-17 EM-G11f1, f1b, f1c).

Spec: ``project-docs/specs/email_app_master_plan.md`` §12.3.13.9 and
§12.3.13.9.2 (PR-G7, PR-G7b, PR-G7c).

The module has two calls, and each path that deletes a mailbox calls both:

1. :func:`read_teardown_plans` runs INSIDE the transaction of the caller,
   BEFORE the delete of the caller. The cascade of that delete removes the
   filter ids, so the plan keeps them. The read runs under FORCE RLS in the
   session of the caller. It makes no network call.
2. :func:`finish_teardowns` runs AFTER the commit of the caller, with no
   session open across a network call. For each plan it stops the sync loop
   (when the caller asks), removes the filters that Metorite made, revokes
   the Google grant, drops the Graph subscription and deletes the Redis keys
   of the mailbox. Then it bumps the My Day of each owner that still exists.

The paths:

* The disconnect (``transport/accounts.py::delete_account``) reads the plan
  of one mailbox in its ownership block. It stops the loop BEFORE its
  ``DELETE`` (EM-T4f), so it asks :func:`finish_teardowns` for no stop.
* The member purge (``routes/admin/members.py::purge_member_rows``) reads the
  plans of each mailbox of the member before the first statement of
  ``_PURGE_DELETES``. Both doors (the admin route and the operator door) call
  :func:`finish_teardowns` after their session block exits. The member purge
  has no lock bound, so the loop stops after the commit.
* The org purge (``routes/operator.py::purge_organization``, PR-G7c) passes
  a callback to ``acb_auth.offboard.purge_tenant_organization``. The purge
  runs the callback in its tenant session, before its first delete. The
  callback reads the plans of each mailbox of the org (:class:`OrgScope`)
  and writes one ``email_detached_purges`` row for each. The door calls
  :func:`finish_teardowns` after the purge returns. The owners are gone, so
  no My Day is bumped.

The rules of this module:

* A failure of a leg never blocks the caller. A leg logs and never raises.
* No log line holds a token, a refresh token or mail text. A failure logs the
  error class or a fixed reason.
* The revoke sends the token in the form body of a POST, never in the URL.
* Google keeps one grant for each Google account and each OAuth client. So a
  revoke ends the grant of EVERY Metorite row of that Gmail account, in any
  organization. Migration 263's ``email_gmail_grant_shared`` answers whether
  another such row exists, and the revoke runs only on a clear ``false``. A
  ``true``, a ``NULL`` or a failed check skips it (review round 1, P1). A
  ``NULL`` logs ``owner_blind`` and a failed check logs ``check_failed``, each
  at WARNING (review round 2).
* An Outlook mailbox removes the rules that Metorite made (PR-G7b). The rule
  ids are the ones that ``senders.py`` stores, never a rule of the member.
  Microsoft has no revoke for one delegated grant, so the grant stays.
* A scope of more than ``TEARDOWN_MAX_MAILBOXES`` mailboxes answers 409 before
  any delete. ``TEARDOWN_CONCURRENCY`` mailboxes run at once, each with the
  bound ``MAILBOX_TEARDOWN_BOUND_S`` on its legs. The stop of a purge has its
  own bound ``STOP_SYNC_BOUND_S``, before the legs. A door waits
  ``TEARDOWN_WAIT_S`` at most, and the rest continue in a task that
  ``_TEARDOWNS`` holds. ``/internal/drain`` counts that task.

Fences: ``tests/unit/test_email_mailbox_teardown.py``,
``tests/unit/test_email_purge_wiring.py`` (R8) and the R8 case in
``tests/unit/test_email_disconnect_order.py``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from typing import Any
from uuid import UUID

import httpx
from fastapi import HTTPException
from gateway.routes.email.core import _decrypt_credentials, _instantiate_provider, _log
from pydantic import BaseModel
from sqlalchemy import text

#: Google's endpoint that revokes an OAuth grant. A POST of the refresh token
#: ends the grant, and the access tokens of the grant end with it.
GOOGLE_REVOKE_URL = "https://oauth2.googleapis.com/revoke"

#: The bound on the revoke call. A slow Google must never hold back the 204.
REVOKE_TIMEOUT_S = 5.0

#: The bound on the whole filter removal, for every filter of the mailbox.
FILTER_REMOVAL_TIMEOUT_S = 10.0

#: The bound on the Redis deletes of one mailbox.
CACHE_DROP_TIMEOUT_S = 5.0

#: The bound on the best-effort Graph call (EM-T4f). A slow or failed call
#: must never hold back the answer of the caller.
SUBSCRIPTION_DELETE_TIMEOUT_S = 5.0

#: The cap on the mailboxes of one scope (§12.3.13.9.2). Above it the plan
#: read raises a 409 before any delete, so no purge skips a revoke without a
#: record. Above the cap, the operator asks for a change of the cap.
TEARDOWN_MAX_MAILBOXES = 1000

#: The mailboxes that :func:`finish_teardowns` runs at once.
TEARDOWN_CONCURRENCY = 4

#: The bound on all of the legs of one mailbox.
MAILBOX_TEARDOWN_BOUND_S = 30.0

#: A door waits this long at most. The mailboxes that remain continue in a
#: task that ``_TEARDOWNS`` holds.
TEARDOWN_WAIT_S = 60.0

#: The bound on the stop of one sync loop in a purge. It runs BEFORE the bound
#: of the legs and outside it, so a slow stop cannot use the time of the
#: revoke. ``remove_account_sync`` swallows the cancel of its wait, so at this
#: bound the stop returns and the legs go on (fix round 1, review P2).
STOP_SYNC_BOUND_S = 15.0

#: The page size of one Redis ``DELETE``.
_CACHE_DELETE_BATCH = 200

#: Graph statuses that mean the subscription is gone: 204 deleted it now, and
#: 404 says it was gone before.
_SUBSCRIPTION_GONE = frozenset({204, 404})

#: The stored values of ``email_accounts.provider`` that hold filters that
#: Metorite made (``senders.py::_create_block_filter``). ``microsoft`` is the
#: stored value of an Outlook row (``email_ingestion.providers.factory``). An
#: IMAP row has no filters.
FILTER_PROVIDERS = frozenset({"gmail", "microsoft"})

#: Another Gmail row of this address, in any tenant (migration 263). One bit,
#: never a row. NULL means that the function cannot tell.
GRANT_SHARED_SQL = (
    "SELECT public.email_gmail_grant_shared(:email, CAST(:exclude AS uuid))")

#: The filter ids that Metorite made for one mailbox. The owner predicate
#: repeats the one of the ownership read, in the same block.
FILTER_IDS_SQL = """SELECT n.auto_archive_filter_id AS fid
   FROM email_newsletters n
   JOIN email_accounts a ON a.id = n.account_id
   WHERE n.account_id = :id AND a.user_id = :uid
     AND n.auto_archive_filter_id IS NOT NULL
   ORDER BY n.created_at, n.id"""

#: The mailboxes of one member. ``_PURGE_DELETES`` deletes ``email_accounts``
#: with THIS clause, and the purge rows of ``memory_purge.py`` use it too. So
#: the plans, the purge rows and the delete name one set of rows.
MEMBER_MAILBOXES_WHERE = "lower(user_id) = :email"

#: The plan of one mailbox, in the ownership block of the disconnect. The
#: owner predicate is the one of the ``DELETE``.
MAILBOX_PLAN_SQL = """SELECT organization_id, provider, credentials_encrypted,
                             user_id, email_address, webhook_subscription_id
                        FROM email_accounts
                       WHERE id = :id AND user_id = :uid"""

#: The plans of each mailbox of one member. ``FOR UPDATE`` holds each row
#: until the commit, so a sync cannot rotate the credentials or add a child
#: row between this read and the delete. A sync phase in flight makes this
#: read wait, as the delete would wait. ``LIMIT`` reads one row above the cap.
MEMBER_PLANS_SQL = f"""SELECT id, organization_id, provider, credentials_encrypted,
                              user_id, email_address, webhook_subscription_id
                         FROM email_accounts
                        WHERE {MEMBER_MAILBOXES_WHERE}
                        ORDER BY created_at, id
                        LIMIT :limit
                        FOR UPDATE"""

#: The count of the mailboxes of one member, for the detail of the 409.
MEMBER_MAILBOX_COUNT_SQL = (
    f"SELECT count(*) FROM email_accounts WHERE {MEMBER_MAILBOXES_WHERE}")

#: The filter ids of each mailbox of one member.
MEMBER_FILTER_IDS_SQL = f"""SELECT n.account_id, n.auto_archive_filter_id AS fid
                              FROM email_newsletters n
                             WHERE n.account_id IN (
                                   SELECT id FROM email_accounts
                                    WHERE {MEMBER_MAILBOXES_WHERE})
                               AND n.auto_archive_filter_id IS NOT NULL
                             ORDER BY n.created_at, n.id"""

#: The mailboxes of one organization: the org purge (EM-G11f1c). The clause
#: names the org itself, so a role that bypasses RLS still reads one tenant.
#: The detached purge rows of ``memory_purge.py`` use it too, so the plans and
#: the purge rows name one set of rows. ``DELETE FROM organization`` deletes
#: that set by its cascade.
ORG_MAILBOXES_WHERE = "organization_id = CAST(:org AS uuid)"

#: The plans of each mailbox of one organization. ``FOR UPDATE`` and
#: ``LIMIT`` as in ``MEMBER_PLANS_SQL``.
ORG_PLANS_SQL = f"""SELECT id, organization_id, provider, credentials_encrypted,
                           user_id, email_address, webhook_subscription_id
                      FROM email_accounts
                     WHERE {ORG_MAILBOXES_WHERE}
                     ORDER BY created_at, id
                     LIMIT :limit
                     FOR UPDATE"""

#: The count of the mailboxes of one organization, for the detail of the 409.
ORG_MAILBOX_COUNT_SQL = (
    f"SELECT count(*) FROM email_accounts WHERE {ORG_MAILBOXES_WHERE}")

#: The filter ids of each mailbox of one organization.
ORG_FILTER_IDS_SQL = f"""SELECT n.account_id, n.auto_archive_filter_id AS fid
                           FROM email_newsletters n
                          WHERE n.account_id IN (
                                SELECT id FROM email_accounts
                                 WHERE {ORG_MAILBOXES_WHERE})
                            AND n.auto_archive_filter_id IS NOT NULL
                          ORDER BY n.created_at, n.id"""


# ── the plan ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TeardownPlan:
    """What the teardown of one mailbox needs, read before the delete.

    ``owner`` is the ``user_id`` of the row, the key of the Mem0 writers.
    ``org`` is the ``organization_id`` of the row, never request input (R5).
    """

    account_id: str
    org: str | None
    owner: str
    provider: str
    email_address: str | None
    credentials_encrypted: Any
    subscription_id: str | None
    filter_ids: tuple[str, ...] = ()

    def with_deleted_row(self, row: Any) -> TeardownPlan:
        """The plan with the values of the ``RETURNING`` row of a ``DELETE``.

        The disconnect reads its plan before it stops the loop, and the loop
        can rotate the credentials or replace the subscription until then.
        So the values of the row that the ``DELETE`` removed win.
        """
        return replace(
            self,
            provider=row.provider,
            credentials_encrypted=row.credentials_encrypted,
            subscription_id=row.webhook_subscription_id,
            email_address=row.email_address,
        )


@dataclass(frozen=True)
class MailboxScope:
    """One mailbox of its owner: the disconnect."""

    account_id: str
    owner: str


@dataclass(frozen=True)
class MemberScope:
    """Each mailbox of one member: the member purge. ``email`` is the
    lower-cased address, as ``_PURGE_DELETES`` binds it."""

    email: str


@dataclass(frozen=True)
class OrgScope:
    """Each mailbox of one organization: the org purge (EM-G11f1c). ``org``
    is the id that the purge found by its slug on the server, never request
    input (R11)."""

    org: str


class TeardownCounts(BaseModel):
    """What a door tells its caller about the mailboxes of a purge.

    ``finished`` counts the mailboxes whose legs all succeeded. ``failed``
    counts the ones with a leg that failed or a bound that passed. Each
    failure has its own log line. ``continuing`` counts the ones that still
    run in the held task when the door answers.
    """

    planned: int = 0
    finished: int = 0
    failed: int = 0
    continuing: int = 0


def _org_of(value: Any) -> str | None:
    return str(value) if value else None


def _plan_of(row: Any, account_id: str, filter_ids: Sequence[str]) -> TeardownPlan:
    return TeardownPlan(
        account_id=account_id,
        org=_org_of(row.organization_id),
        owner=str(row.user_id or ""),
        provider=str(row.provider or ""),
        email_address=row.email_address,
        credentials_encrypted=row.credentials_encrypted,
        subscription_id=row.webhook_subscription_id,
        filter_ids=tuple(filter_ids),
    )


def _unique_ids(values: Sequence[Any]) -> list[str]:
    """Each id once, in the order of its first row. An empty id is dropped."""
    seen: dict[str, None] = {}
    for value in values:
        fid = str(value or "").strip()
        if fid:
            seen.setdefault(fid, None)
    return list(seen)


async def read_teardown_plans(
    db: Any, scope: MailboxScope | MemberScope | OrgScope,
) -> list[TeardownPlan]:
    """The plans of the mailboxes of ``scope``. Run it INSIDE the transaction
    of the caller and BEFORE the delete of the caller.

    It runs under FORCE RLS in the session of the caller. It makes no network
    call, and it waits for no task. An empty list means that no mailbox
    matched: the disconnect answers 404 for that.

    A member scope or an org scope of more than ``TEARDOWN_MAX_MAILBOXES``
    mailboxes raises a 409 with the count. The caller has deleted nothing
    yet, so its transaction rolls back whole.
    """
    if isinstance(scope, MailboxScope):
        return await _mailbox_plan(db, scope)
    if isinstance(scope, MemberScope):
        return await _many_plans(
            db, {"email": scope.email}, plans_sql=MEMBER_PLANS_SQL,
            count_sql=MEMBER_MAILBOX_COUNT_SQL, filters_sql=MEMBER_FILTER_IDS_SQL,
            whose="This member")
    if isinstance(scope, OrgScope):
        return await _many_plans(
            db, {"org": scope.org}, plans_sql=ORG_PLANS_SQL,
            count_sql=ORG_MAILBOX_COUNT_SQL, filters_sql=ORG_FILTER_IDS_SQL,
            whose="This organization")
    raise TypeError(f"unknown teardown scope {type(scope).__name__}")


async def _mailbox_plan(db: Any, scope: MailboxScope) -> list[TeardownPlan]:
    row = (await db.execute(
        text(MAILBOX_PLAN_SQL), {"id": scope.account_id, "uid": scope.owner},
    )).fetchone()
    if row is None:
        return []
    filter_ids = (await read_filter_ids(db, scope.account_id, scope.owner)
                  if row.provider in FILTER_PROVIDERS else [])
    return [_plan_of(row, scope.account_id, filter_ids)]


async def _many_plans(
    db: Any, params: dict[str, Any], *, plans_sql: str, count_sql: str,
    filters_sql: str, whose: str,
) -> list[TeardownPlan]:
    """The plans of a purge: each mailbox of one member or one organization.
    The three statements share one clause, so they name one set of rows."""
    rows = (await db.execute(
        text(plans_sql), {**params, "limit": TEARDOWN_MAX_MAILBOXES + 1},
    )).fetchall()
    if len(rows) > TEARDOWN_MAX_MAILBOXES:
        count = int((await db.execute(text(count_sql), params)).scalar() or 0)
        _log.warning("email.teardown.over_cap", mailboxes=count,
                     cap=TEARDOWN_MAX_MAILBOXES)
        raise HTTPException(status_code=409, detail=(
            f"{whose} has {count} mailboxes. A purge tears down "
            f"{TEARDOWN_MAX_MAILBOXES} at most, so nothing was deleted."))
    if not rows:
        return []
    by_mailbox: dict[str, list[Any]] = {}
    if any(row.provider in FILTER_PROVIDERS for row in rows):
        for hit in (await db.execute(text(filters_sql), params)).fetchall():
            by_mailbox.setdefault(str(hit.account_id), []).append(hit.fid)
    plans = []
    for row in rows:
        mailbox = str(row.id)
        ids = (_unique_ids(by_mailbox.get(mailbox, []))
               if row.provider in FILTER_PROVIDERS else [])
        plans.append(_plan_of(row, mailbox, ids))
    return plans


async def read_filter_ids(db: Any, account_id: str, owner: str) -> list[str]:
    """The ids of the filters that Metorite made for one mailbox.

    ``senders.py::_create_block_filter`` writes each id into
    ``email_newsletters.auto_archive_filter_id``. The caller runs this inside
    the block of its ownership read, so RLS binds the read to the tenant.
    Each id appears once, in the order of its row.
    """
    rows = (await db.execute(
        text(FILTER_IDS_SQL), {"id": account_id, "uid": owner})).fetchall()
    return _unique_ids([row.fid for row in rows])


# ── after the commit ─────────────────────────────────────────────────────────


#: The teardowns that still run. The set holds a strong reference to each task
#: until it ends, so a door that stops waiting does not lose the rest.
#: ``/internal/drain`` counts it (:func:`live_teardown_count`), so a deploy
#: waits for a teardown before it stops the process.
_TEARDOWNS: set[asyncio.Task[None]] = set()


def live_teardown_count() -> int:
    """The held teardown tasks of this process that still run."""
    return sum(1 for task in _TEARDOWNS if not task.done())


@dataclass
class _Progress:
    finished: int = 0
    failed: int = 0
    done: set[str] = field(default_factory=set)


async def finish_teardowns(
    org: str | None,
    plans: Sequence[TeardownPlan],
    *,
    open_session: Callable[[], Any],
    stop_loops: bool,
    bump_owners: bool,
) -> TeardownCounts:
    """Tear down each mailbox of ``plans``. Run it AFTER the commit of the
    caller, with no session of the caller open.

    ``open_session`` is the tenant session of the caller, for the shared-grant
    check. ``stop_loops`` stops the sync loop of each mailbox first. The
    disconnect stopped its loop before its ``DELETE``, so it passes false.
    ``bump_owners`` bumps the My Day of each owner after the legs. A purged
    member does not exist any more, so a purge passes false.

    It never raises. A door waits ``TEARDOWN_WAIT_S`` at most. A purge door
    calls it LAST, after its own log line and its Console mirror, so a stop
    during the wait loses neither (fix round 1). The answer counts the
    mailboxes that finished, the ones that failed and the ones that continue.
    """
    plans = list(plans)
    if not plans:
        return TeardownCounts()
    progress = _Progress()
    _log.info("email.teardown.started", org=org, mailboxes=len(plans))
    task = asyncio.ensure_future(_run_teardowns(
        org, plans, progress, open_session=open_session,
        stop_loops=stop_loops, bump_owners=bump_owners))
    _TEARDOWNS.add(task)
    task.add_done_callback(_TEARDOWNS.discard)
    # ``asyncio.wait`` cancels nothing: at the bound, and when the request is
    # cancelled, the task goes on.
    await asyncio.wait({task}, timeout=TEARDOWN_WAIT_S)
    finished, failed = progress.finished, progress.failed
    continuing = len(plans) - finished - failed
    if continuing:
        _log.warning("email.teardown.continuing", org=org,
                     mailboxes=continuing, finished=finished, failed=failed)
    return TeardownCounts(planned=len(plans), finished=finished,
                          failed=failed, continuing=continuing)


async def _run_teardowns(
    org: str | None,
    plans: list[TeardownPlan],
    progress: _Progress,
    *,
    open_session: Callable[[], Any],
    stop_loops: bool,
    bump_owners: bool,
) -> None:
    gate = asyncio.Semaphore(TEARDOWN_CONCURRENCY)

    async def _one(plan: TeardownPlan) -> None:
        async with gate:
            ok = False
            try:
                if stop_loops:
                    await _bounded_stop(plan.account_id)
                async with asyncio.timeout(MAILBOX_TEARDOWN_BOUND_S):
                    ok = await _teardown_one(plan, open_session=open_session)
            except Exception as exc:
                _log.warning("email.teardown.mailbox_failed",
                             account_id=plan.account_id,
                             error=type(exc).__name__)
            # A cancel skips this, so the mailbox stays unfinished.
            progress.done.add(plan.account_id)
            if ok:
                progress.finished += 1
            else:
                progress.failed += 1

    try:
        await asyncio.gather(*(_one(plan) for plan in plans))
    except asyncio.CancelledError:
        # A stop of the process. The credentials went with the rows, so
        # nothing can run these legs again. The line names each mailbox, so
        # the owner can remove the grant or the filters by hand. No ledger
        # keeps a token after the delete (fix round 1).
        _log.warning("email.teardown.cancelled", org=org,
                     unfinished=sorted(plan.account_id for plan in plans
                                       if plan.account_id not in progress.done))
        raise
    if bump_owners:
        # Each owner once, with the id of its first mailbox for the log.
        owners: dict[tuple[str | None, str], str] = {}
        for plan in plans:
            owners.setdefault((plan.org or org, plan.owner), plan.account_id)
        for (owner_org, owner), account_id in owners.items():
            await _bump_my_day(owner_org, owner, account_id)
    _log.info("email.teardown.finished", org=org, mailboxes=len(plans),
              finished=progress.finished, failed=progress.failed)


async def _bounded_stop(account_id: str) -> None:
    """Stop one sync loop, with its own bound ``STOP_SYNC_BOUND_S``."""
    expired = False
    try:
        async with asyncio.timeout(STOP_SYNC_BOUND_S) as bound:
            await stop_sync(account_id)
        # ``remove_account_sync`` swallows the cancel of its wait, so the
        # bound can pass with no ``TimeoutError``. Ask the bound itself.
        expired = bound.expired()
    except TimeoutError:
        expired = True
    if expired:
        _log.warning("email.teardown.stop_timed_out", account_id=account_id)


async def _teardown_one(
    plan: TeardownPlan, *, open_session: Callable[[], Any],
) -> bool:
    """The legs of one mailbox, in order. Each leg logs and never raises.
    True when each leg succeeded."""
    # The filters go first, while the grant is still valid.
    results = [
        await remove_metorite_filters(plan),
        await revoke_grant(plan, open_session),
        await drop_graph_subscription(plan),
        await drop_mailbox_cache(plan.org, plan.account_id),
    ]
    return all(results)


# ── the stop of the sync loop ────────────────────────────────────────────────


async def stop_sync(account_id: str) -> bool:
    """Stop the sync loop of a mailbox. True when a loop ran before the stop.

    The answer tells a failed disconnect whether to start the loop again. A
    mailbox with sync off, or a box with ``EMAIL_SYNC_ENABLED`` off, has no
    loop, and a failed disconnect must not start one. The disconnect calls it
    before its ``DELETE``. A purge calls it through :func:`finish_teardowns`,
    after its commit.
    """
    try:
        from email_ingestion.scheduler import (
            get_scheduler_status,
            remove_account_sync,
        )
        had_loop = account_id in get_scheduler_status()["accounts"]
        await remove_account_sync(account_id)
    except Exception as exc:
        _log.warning("email.disconnect.stop_sync_failed",
                     account_id=account_id, error=type(exc).__name__)
        return False
    return had_loop


# ── the filters that Metorite made ───────────────────────────────────────────


async def remove_metorite_filters(plan: TeardownPlan) -> bool:
    """Delete each filter that Metorite made for the mailbox, best effort.

    A Gmail row deletes its Gmail filters (gap D2). An Outlook row deletes
    its Inbox message rules (PR-G7b). Only a row of ``FILTER_PROVIDERS``
    with at least one id gets a call. The provider comes from
    ``_instantiate_provider``, and ``delete_filter`` refreshes an expired
    access token. A failure of one filter logs its error class, and the next
    filter still goes. The whole step has a bound of
    ``FILTER_REMOVAL_TIMEOUT_S``. It never raises. It answers False when a
    filter or the step failed.

    It runs only after the delete commits, with the ids that the plan kept,
    and before the revoke ends the grant. It holds no session open, because
    each delete is a network call.
    """
    if plan.provider not in FILTER_PROVIDERS or not plan.filter_ids:
        return True
    account_id = plan.account_id
    removed = failed = 0
    try:
        async with asyncio.timeout(FILTER_REMOVAL_TIMEOUT_S):
            creds, _store = _decrypt_credentials(plan.credentials_encrypted)
            provider = _instantiate_provider(plan.provider, creds)
            for fid in plan.filter_ids:
                try:
                    await provider.delete_filter(fid)
                except Exception as exc:
                    failed += 1
                    _log.warning(
                        "email.disconnect.filter_remove_failed",
                        account_id=account_id, error=type(exc).__name__,
                        status=_status_of(exc))
                    continue
                removed += 1
    except Exception as exc:
        # The bound, a credential blob that does not decrypt, or a provider
        # that does not build.
        _log.warning("email.disconnect.filters_failed", account_id=account_id,
                     error=type(exc).__name__, removed=removed, failed=failed)
        return False
    _log.info("email.disconnect.filters_removed", account_id=account_id,
              removed=removed, failed=failed)
    return failed == 0


def _status_of(exc: BaseException) -> int | None:
    """The HTTP status that an error carries, or None. Never its text."""
    return getattr(getattr(exc, "response", None), "status_code", None)


# ── the revoke at Google, unless the grant is shared ────────────────────────


def _http_client() -> httpx.AsyncClient:
    """The client of the revoke call. Tests replace it with a mock transport."""
    return httpx.AsyncClient(timeout=REVOKE_TIMEOUT_S)


async def _post_revoke(token: str) -> int:
    """POST one token to Google, and return the HTTP status.

    The token goes in the form body. It is never in the URL, so no access log
    and no ``httpx`` log line can hold it.
    """
    async with _http_client() as client:
        resp = await client.post(GOOGLE_REVOKE_URL, data={"token": token})
    return resp.status_code


def _grant_token(creds: Any) -> str | None:
    """The token that ends the grant: the refresh token, or else the access
    token. Google revokes the refresh token of an access token too."""
    if not isinstance(creds, dict):
        return None
    for name in ("refresh_token", "access_token"):
        value = creds.get(name)
        if isinstance(value, str) and value.strip():
            return value
    return None


#: The verdicts of :func:`grant_shared_elsewhere`. Only ``GRANT_FREE`` lets
#: the revoke run. Each other verdict is the reason of ``email.revoke_skipped``.
GRANT_FREE = "free"
GRANT_SHARED = "shared_grant"
GRANT_OWNER_BLIND = "owner_blind"
GRANT_CHECK_FAILED = "check_failed"
GRANT_NO_ADDRESS = "no_address"


async def grant_shared_elsewhere(
    open_session: Callable[[], Any], email: Any, account_id: str,
) -> str:
    """The verdict on another Metorite Gmail row of ``email``, in any tenant.

    One short block of ``open_session``, which is the tenant session of the
    caller. Migration 263's function answers one bit across tenants and
    never a row. The verdicts:

    * ``GRANT_FREE``: the function answered ``false``. No other row exists.
    * ``GRANT_SHARED``: the function answered ``true``.
    * ``GRANT_OWNER_BLIND``: the function answered ``NULL``, because its
      owner cannot see through RLS. That is a deploy fault, so the caller
      logs it at WARNING.
    * ``GRANT_CHECK_FAILED``: the call raised. This logs the error class.
    * ``GRANT_NO_ADDRESS``: the row holds no address to ask about.

    The caller skips the revoke unless the verdict is ``GRANT_FREE``. It
    never raises.
    """
    address = str(email or "").strip()
    if not address:
        return GRANT_NO_ADDRESS
    try:
        exclude: str | None = str(UUID(str(account_id).strip()))
    except (TypeError, ValueError, AttributeError):
        exclude = None
    try:
        async with asyncio.timeout(REVOKE_TIMEOUT_S):
            async with open_session() as db:
                answer = (await db.execute(
                    text(GRANT_SHARED_SQL),
                    {"email": address, "exclude": exclude})).scalar()
    except Exception as exc:
        _log.warning("email.disconnect.grant_check_failed",
                     account_id=exclude or str(account_id)[:64],
                     error=type(exc).__name__)
        return GRANT_CHECK_FAILED
    if answer is True:
        return GRANT_SHARED
    if answer is False:
        return GRANT_FREE
    return GRANT_OWNER_BLIND


async def revoke_grant(plan: TeardownPlan, open_session: Callable[[], Any]) -> bool:
    """Revoke the OAuth grant of a deleted Gmail mailbox, best effort (gap D1).

    Only a ``gmail`` plan gets a call. Microsoft has no revoke for one
    delegated grant, so an Outlook plan revokes nothing. An IMAP row has no
    grant.

    Google keeps one grant for each Google account and OAuth client. So when
    :func:`grant_shared_elsewhere` does not answer ``GRANT_FREE``, the revoke
    is skipped, and ``email.revoke_skipped`` logs the verdict as its reason.
    ``shared_grant`` is an INFO line. ``owner_blind``, ``check_failed`` and
    ``no_address`` are WARNING lines, because each one is a fault. A wrong
    revoke breaks a live mailbox of another member. A skipped one leaves a
    grant that the member can remove at Google.

    Google answers 200 when the grant ends. Any other outcome logs
    ``email.revoke_failed`` with the provider and a fixed reason. The call
    has a bound of ``REVOKE_TIMEOUT_S``. It never raises. It answers False
    for a fault: a failed revoke, or a skip that is not ``shared_grant``.
    """
    if plan.provider != "gmail":
        return True
    account_id = plan.account_id
    verdict = await grant_shared_elsewhere(
        open_session, plan.email_address, account_id)
    if verdict != GRANT_FREE:
        emit = _log.info if verdict == GRANT_SHARED else _log.warning
        emit("email.revoke_skipped", account_id=account_id, provider="gmail",
             reason=verdict)
        return verdict == GRANT_SHARED
    reason = await _revoke_reason(plan.credentials_encrypted)
    if reason is None:
        _log.info("email.disconnect.grant_revoked", account_id=account_id,
                  provider="gmail")
        return True
    _log.warning("email.revoke_failed", account_id=account_id,
                 provider="gmail", reason=reason)
    return False


async def _revoke_reason(creds_blob: Any) -> str | None:
    """Run the revoke. None when Google ended the grant, else the reason."""
    try:
        creds, _store = _decrypt_credentials(creds_blob)
    except Exception:
        return "unreadable_credentials"
    token = _grant_token(creds)
    if token is None:
        return "no_token"
    try:
        async with asyncio.timeout(REVOKE_TIMEOUT_S):
            status = await _post_revoke(token)
    except TimeoutError:
        return "timeout"
    except httpx.HTTPError:
        return "transport_error"
    except Exception as exc:
        return type(exc).__name__
    return None if status == 200 else f"http_{status}"


# ── the Graph subscription ───────────────────────────────────────────────────


async def drop_graph_subscription(plan: TeardownPlan) -> bool:
    """Remove the Graph push subscription of a deleted mailbox, best effort.

    Only a ``microsoft`` plan with a subscription id gets a call. The provider
    comes from ``_instantiate_provider``, the gateway adapter over
    ``build_provider``. The whole call has a bound of
    ``SUBSCRIPTION_DELETE_TIMEOUT_S``.

    ``delete_subscription`` returns the HTTP status of Graph. 204 and 404 mean
    the subscription is gone, and log ``email.disconnect.subscription_deleted``.
    Any other status, an error or a timeout logs
    ``email.disconnect.subscription_delete_failed``. The log holds the status
    or the error class, never a token or an error text. A refreshed token
    needs no write, because the row is gone.

    The provider has no close method, so the httpx client of this call stays
    open until the process collects it. §10.4.6 records that follow-up.
    """
    sub_id = plan.subscription_id
    account_id = plan.account_id
    if plan.provider != "microsoft" or not sub_id:
        return True
    try:
        async with asyncio.timeout(SUBSCRIPTION_DELETE_TIMEOUT_S):
            creds, _store = _decrypt_credentials(plan.credentials_encrypted)
            provider = _instantiate_provider(plan.provider, creds)
            graph_status = await provider.delete_subscription(str(sub_id))
    except Exception as exc:
        response = getattr(exc, "response", None)
        _log.warning(
            "email.disconnect.subscription_delete_failed",
            account_id=account_id,
            error=type(exc).__name__,
            status=getattr(response, "status_code", None),
        )
        return False
    if graph_status in _SUBSCRIPTION_GONE:
        _log.info("email.disconnect.subscription_deleted",
                  account_id=account_id, sub=str(sub_id)[:12],
                  status=graph_status)
        return True
    _log.warning("email.disconnect.subscription_delete_failed",
                 account_id=account_id, error=None, status=graph_status)
    return False


# ── the Redis keys of the mailbox, and the My Day bump ──────────────────────


def _mailbox_namespaces() -> tuple[str, ...]:
    """The Redis namespaces whose keys carry the mailbox id as the first part.

    * ``digest.py``: the brief cache, ``key(ns, account_id, day, digest)``.
    * ``automation/replyzero.py``: the status back-off mark,
      ``key(ns, account_id, thread_id, message_id)``.
    * ``email_ingestion/llm_cap.py``: the daily budget count,
      ``key(ns, account_id, day)``.

    The HTML, the inline pictures and the attachment bytes carry a message row
    id, not the mailbox id. They end within one hour. The imports are local,
    so this module adds no import cycle.
    """
    from email_ingestion.llm_cap import BUDGET_NAMESPACE
    from gateway.routes.email.automation.replyzero import STATUS_BACKOFF_NAMESPACE
    from gateway.routes.email.digest import BRIEF_CACHE_NAMESPACE

    return (BRIEF_CACHE_NAMESPACE, STATUS_BACKOFF_NAMESPACE, BUDGET_NAMESPACE)


def _redis() -> Any:
    """The tenant Redis client. Tests replace it."""
    from acb_common.tenant_redis import get_tenant_redis

    return get_tenant_redis()


async def _drop_namespace(client: Any, namespace: str, mailbox: str) -> int:
    """Delete each key of one namespace under one mailbox. The count.

    The caller binds the organization. ``match`` builds a pattern under the
    tenant prefix, so a scan never leaves the tenant. Each key that the scan
    gives is built again with ``key``, because the client takes a ``TenantKey``
    only. The rest of the key after the mailbox id is ONE part, so the wire
    key of the new ``TenantKey`` is the key that the scan gave.
    """
    from acb_common.tenant_redis import key, match

    prefix = key(namespace, mailbox).value + ":"
    batch: list[Any] = []
    dropped = 0
    async for raw in client.scan_iter(match(namespace, mailbox)):
        name = raw.decode() if isinstance(raw, bytes) else str(raw)
        rest = name[len(prefix):] if name.startswith(prefix) else ""
        if not rest:
            continue
        try:
            batch.append(key(namespace, mailbox, rest))
        except ValueError:
            continue
        if len(batch) >= _CACHE_DELETE_BATCH:
            dropped += int(await client.delete(*batch) or 0)
            batch = []
    if batch:
        dropped += int(await client.delete(*batch) or 0)
    return dropped


async def drop_mailbox_cache(org: str | None, account_id: str) -> bool:
    """Delete the Redis keys of a deleted mailbox.

    ``org`` comes from the row that the plan read, never from request input
    (R5). The keys use the mailbox id in its canonical UUID form, as the
    writers do. The deletes have a bound of ``CACHE_DROP_TIMEOUT_S``. A
    failure logs the error class only. It never raises. It answers False
    when the deletes failed.
    """
    if not org:
        return True
    try:
        mailbox = str(UUID(str(account_id).strip()))
    except (TypeError, ValueError, AttributeError):
        return True
    return await _drop_keys(org, mailbox)


async def _bump_my_day(org: str | None, owner: str, account_id: str) -> None:
    """Bump the My Day version of an owner that still exists. It does nothing
    while ``MY_DAY_CACHE`` is off. It never raises."""
    if not org:
        return
    try:
        from gateway.routes.shell import cache as feed_cache

        await feed_cache.bump_member(org, owner)
    except Exception as exc:
        _log.warning("email.disconnect.my_day_bump_failed",
                     account_id=_canonical(account_id),
                     error=type(exc).__name__)


def _canonical(account_id: str) -> str:
    try:
        return str(UUID(str(account_id).strip()))
    except (TypeError, ValueError, AttributeError):
        return str(account_id)[:64]


async def _drop_keys(org: str, mailbox: str) -> bool:
    from acb_common.tenant_redis import organization_scope

    dropped = 0
    try:
        async with asyncio.timeout(CACHE_DROP_TIMEOUT_S):
            with organization_scope(org):
                client = _redis()
                for namespace in _mailbox_namespaces():
                    dropped += await _drop_namespace(client, namespace, mailbox)
    except Exception as exc:
        _log.warning("email.disconnect.cache_drop_failed", account_id=mailbox,
                     error=type(exc).__name__, dropped=dropped)
        return False
    _log.info("email.disconnect.cache_dropped", account_id=mailbox,
              keys=dropped)
    return True
