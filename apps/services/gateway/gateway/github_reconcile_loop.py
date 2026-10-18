"""The GitHub reconcile poll (WS-54 GH-5, ``github_integration.md`` §11).

GitHub does not redeliver a failed webhook by itself (§4.2 fact 9). So every
:data:`TICK_SECONDS` this loop repairs what a missed delivery left stale, for
each org with a live ``app`` row:

* an ``open`` or ``draft`` pull request whose ``fetched_at`` is older than
  :data:`PR_STALE` (or never read);
* a reference with ``checks_state = 'pending'`` whose ``checks_fetched_at`` is
  older than :data:`CHECKS_STALE` (or never read).

At most :data:`MAX_REFS` references for each org in a tick, the oldest first.
That is about 150 requests an hour for each org, well under GitHub's 5,000.

**One process for each tick.** ``SET key("github", "reconcile", <tick>) NX EX
900`` through the tenant Redis wrapper picks one process for each org.

**No session across a GitHub call** (as TR-1 and §8.5). The loop reads the
due references in one session, closes it, calls GitHub, then writes each
result in a new session.

**A merge it finds runs §12**, as the webhook does: a pull request that the
poll reads as ``merged`` or ``closed`` calls ``github_merge.on_merged`` in
the write session, and its events go out after that commit.

**A rate limit stops the org's tick** and logs
``github.reconcile.rate_limited``.

**Dark.** ``GITHUB_APP_ENABLED`` gates the START inside
:func:`start_github_reconcile_loop`, never as an ``if`` in ``main.py``
(``main.py`` says why a loop's flag has one reader). ``_organization_ids`` is
the one unbound read, a tenant-discovery site registered in
``tests/unit/test_db_engine_seam.py``. Fence:
``tests/unit/test_github_reconcile_r8.py`` (R8).
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from acb_common import get_logger, tenant_redis
from acb_common.tenant_redis import key, organization_scope
from sqlalchemy import text

from gateway import github_sync
from gateway.connections import github_app
from gateway.db import get_db, tenant_session

_log = get_logger("gateway.github_reconcile")

#: §11: the tick, and the life of its one-process guard.
TICK_SECONDS = 600.0
GUARD_TTL_S = 900
#: §11: what is stale, and how many references one org reads in a tick.
PR_STALE = timedelta(minutes=30)
CHECKS_STALE = timedelta(minutes=5)
MAX_REFS = 50
#: Organizations that run at the same time, and the bound of one.
CONCURRENCY = 3
PER_ORG_TIMEOUT_S = 240.0
STOP_GRACE_SECONDS = 30.0

_task: asyncio.Task[None] | None = None
_stopping = asyncio.Event()

#: The due references of one org, the oldest first (§11).
_DUE_SQL = (
    "SELECT id, ref, state, head_sha, checks_state, fetched_at, checks_fetched_at, "
    "  (state IN ('open', 'draft') AND (fetched_at IS NULL "
    "     OR fetched_at < now() - CAST(:pr_stale AS interval))) AS pr_due, "
    "  (checks_state = 'pending' AND head_sha IS NOT NULL AND (checks_fetched_at IS NULL "
    "     OR checks_fetched_at < now() - CAST(:checks_stale AS interval))) AS checks_due "
    "FROM pm_task_refs WHERE organization_id = CAST(:org AS uuid) "
    "AND provider = 'github' AND kind = 'pull_request' AND ("
    "  (state IN ('open', 'draft') AND (fetched_at IS NULL "
    "     OR fetched_at < now() - CAST(:pr_stale AS interval))) "
    "  OR (checks_state = 'pending' AND head_sha IS NOT NULL AND (checks_fetched_at IS NULL "
    "     OR checks_fetched_at < now() - CAST(:checks_stale AS interval)))) "
    "ORDER BY LEAST(COALESCE(fetched_at, '-infinity'), "
    "  COALESCE(checks_fetched_at, '-infinity')), id "
    "LIMIT :limit"
)

#: A read that GitHub refused stamps ``fetched_at`` too
#: (``github_sync.record_refusal``), so a refused row waits, as TR-1 does, and
#: cannot hold a slot of every tick.


def loop_enabled() -> bool:
    """True when ``GITHUB_APP_ENABLED`` is on. The one reader of the switch."""
    return github_app.app_enabled()


async def _organization_ids() -> list[str]:
    """Every organization id, from the RLS-EXEMPT ``organization`` table.

    The one unbound read of this loop (a tenant-discovery site).
    """
    db = await get_db()
    try:
        rows = (await db.execute(text("SELECT id FROM organization ORDER BY id"))).fetchall()
    finally:
        await db.close()
    return [str(r.id) for r in rows]


@dataclass
class TickResult:
    """What one org's tick did. A test reads it."""

    claimed: bool = False
    pr_reads: int = 0
    checks_reads: int = 0
    merged: int = 0
    rate_limited: bool = False


async def _claim(org: str, tick: str) -> bool:
    with organization_scope(org):
        client = tenant_redis.get_tenant_redis()
        return bool(await client.set(key("github", "reconcile", tick), "1",
                                     nx=True, ex=GUARD_TTL_S))


async def _due(org: str) -> list[Any]:
    async with tenant_session(org) as db:
        return list((await db.execute(text(_DUE_SQL), {
            "org": org, "limit": MAX_REFS,
            # asyncpg binds an interval from a timedelta, never from text.
            "pr_stale": PR_STALE, "checks_stale": CHECKS_STALE,
        })).fetchall())


async def _reconcile_pr(org: str, installation: int, row: Any, result: TickResult) -> None:
    parsed = github_sync.split_ref(str(row.ref))
    if parsed is None:
        return
    repo, number = parsed
    result.pr_reads += 1
    # No session is open here: GitHub never holds a connection.
    try:
        pull_request = await github_sync.read_pull_request(installation, repo, number)
    except github_sync.PullRequestForbidden:
        # A 403 is no per-PR refusal (fix round 2, item 2): stamp the attempt,
        # so the row waits, and leave it blocking.
        async with tenant_session(org) as db:
            await github_sync.record_attempt(db, org, str(row.ref))
        return
    async with tenant_session(org) as db:
        if pull_request is None:
            # GitHub refused the read. The row waits, and a refusal older than
            # the bound stops blocking merge-to-Done (§12.3 step 4).
            outcome = await github_sync.record_refusal(db, org, str(row.ref))
        else:
            # The ONE write path: the facts, then §12 for `merged` or `closed`.
            written, outcome = await github_sync.apply_pull_request(
                db, org, str(row.ref), pull_request)
            if not written:
                # Nothing newer at GitHub: stamp the read, so the row waits.
                await github_sync.record_read(db, org, str(row.ref))
    if outcome is not None:
        result.merged += len(outcome.moved)
        await outcome.emit_all()


#: A checks read that failed for a reason that is not a rate limit stamps
#: ``checks_fetched_at`` (fix round 3, item 2), as a refused pull request read
#: stamps ``fetched_at``. Else 50 rows of a repository that left the
#: installation fill MAX_REFS on every tick, and the rest of the org waits.
_CHECKS_STAMP_SQL = (
    "UPDATE pm_task_refs SET checks_fetched_at = now() "
    "WHERE organization_id = CAST(:org AS uuid) AND provider = 'github' "
    "AND kind = 'pull_request' AND (id = CAST(:id AS uuid) OR head_sha = :sha)"
)


async def _stamp_checks(org: str, row: Any) -> None:
    async with tenant_session(org) as db:
        await db.execute(text(_CHECKS_STAMP_SQL), {
            "org": org, "id": str(row.id), "sha": str(row.head_sha or "") or None})


async def _reconcile_checks(org: str, installation: int, row: Any, result: TickResult) -> None:
    sha = str(row.head_sha or "")
    parsed = github_sync.split_ref(str(row.ref))
    if parsed is None:
        await _stamp_checks(org, row)
        return
    result.checks_reads += 1
    try:
        got = await github_sync.fetch_checks(installation, parsed[0], sha)
    except github_sync.RateLimited:
        raise  # the tick stops, and the row is read at the next one
    except github_app.GitHubCallFailed:
        await _stamp_checks(org, row)
        raise
    if got is None:
        await _stamp_checks(org, row)
        return
    async with tenant_session(org) as db:
        await github_sync.write_checks(db, org, sha, got[0], got[1])


async def run_org(org: str, tick: str) -> TickResult:
    """One org's tick. It never raises past a rate limit or a GitHub failure."""
    result = TickResult()
    installation = await github_sync.installation_of_org(org)
    if installation is None:
        return result
    if not await _claim(org, tick):
        return result
    result.claimed = True
    seen_heads: set[str] = set()
    for row in await _due(org):
        try:
            if row.pr_due:
                await _reconcile_pr(org, installation, row, result)
            elif row.checks_due and str(row.head_sha) not in seen_heads:
                seen_heads.add(str(row.head_sha))
                await _reconcile_checks(org, installation, row, result)
        except github_sync.RateLimited:
            result.rate_limited = True
            _log.warning("github.reconcile.rate_limited")
            break
        except github_app.GitHubAppNotConfigured:
            _log.info("github.reconcile.not_configured")
            break
        except github_app.GitHubCallFailed as exc:
            _log.info("github.reconcile.read_failed", code=exc.code)
            if exc.code == "installation_gone":
                # No token for this installation: every other read of the
                # tick would ask for one again. The uninstall webhook or the
                # admin fixes the row.
                break
    return result


def _tick_id(now: float | None = None) -> str:
    return str(int((time.time() if now is None else now) // TICK_SECONDS))


async def run_tick(tick: str | None = None) -> int:
    """Each organization, :data:`CONCURRENCY` at a time. Answers how many ran."""
    tick = tick or _tick_id()
    gate = asyncio.Semaphore(CONCURRENCY)

    async def _one(org: str) -> int:
        async with gate:
            try:
                done = await asyncio.wait_for(run_org(org, tick), PER_ORG_TIMEOUT_S)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # one org must not hide the others
                _log.warning("github.reconcile.org_failed", error=type(exc).__name__)
                return 0
            return 1 if done.claimed else 0

    return sum(await asyncio.gather(*(_one(org) for org in await _organization_ids())))


async def _loop() -> None:
    while not _stopping.is_set():
        try:
            await run_tick()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # never let one bad tick end the loop
            _log.warning("github.reconcile.loop_error", error=type(exc).__name__)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(_stopping.wait(), timeout=TICK_SECONDS)


async def start_github_reconcile_loop() -> bool:
    """Start the poll when ``GITHUB_APP_ENABLED`` is on. Idempotent.

    With the switch off it starts no task, logs once, and returns False.
    """
    global _task
    if not loop_enabled():
        _log.info("github.reconcile.disabled")
        return False
    if _task is not None and not _task.done():
        return True
    _stopping.clear()
    _task = asyncio.get_running_loop().create_task(_loop(), name="github-reconcile")
    _log.info("github.reconcile.started", tick_secs=TICK_SECONDS)
    return True


async def stop_github_reconcile_loop() -> None:
    """Stop the poll and wait for a tick in progress. Safe when it never started."""
    global _task
    _stopping.set()
    task, _task = _task, None
    if task is None or task.done():
        return
    _done, pending = await asyncio.wait({task}, timeout=STOP_GRACE_SECONDS)
    for still in pending:
        _log.warning("github.reconcile.stop_timed_out")
        still.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await still


__all__ = [
    "CHECKS_STALE",
    "MAX_REFS",
    "PR_STALE",
    "TICK_SECONDS",
    "TickResult",
    "loop_enabled",
    "run_org",
    "run_tick",
    "start_github_reconcile_loop",
    "stop_github_reconcile_loop",
]
