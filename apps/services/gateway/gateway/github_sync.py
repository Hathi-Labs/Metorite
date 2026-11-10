"""The GitHub reads that the webhook, the refs route and the poll share (WS-54 GH-5).

Spec: ``project-docs/specs/github_integration.md`` §8.4, §8.5 and §11.

* :func:`write_pull_request` writes one pull request's facts onto every
  reference of the org, only when GitHub's ``updated_at`` is newer than the
  row's ``pr_updated_at`` (§8.6). The webhook and the poll call it.
* :func:`read_pull_request` is ``GET /repos/{o}/{r}/pulls/{n}`` with the
  installation token.
* :func:`fold_checks` folds the check runs and the combined status into ONE
  state: ``failing``, ``pending``, ``passing`` or ``none``, and the counts.
  Pure.
* :func:`read_checks` runs as a background task after the webhook answers. It
  holds NO database session while it calls GitHub: it reads the org's
  installation in one session, closes it, calls GitHub, then writes in a new
  session, and only rows whose ``head_sha`` still equals the sha. So a late
  read for an old head never writes over the new head. At most
  :data:`CONCURRENCY` reads run at once in a process. A read that fails keeps
  the row and leaves the repair to the poll.

Every call goes through ``connections/github_app.py`` (a fixed host, timeouts,
a response cap). Fences: ``tests/unit/test_github_checks.py`` and
``tests/unit/test_github_reconcile_r8.py``.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime
from typing import Any

from acb_common import get_logger, tenant_redis
from sqlalchemy import text

from gateway.connections import github as github_connection
from gateway.connections import github_app, store
from gateway.db import tenant_session

_log = get_logger("gateway.github_sync")

#: §8.5 step 1: at most this many pages of 100 check runs.
CHECK_PAGES = 3
#: §8.5: the checks reads that run at once in one process.
CONCURRENCY = 4

#: §8.5, fix round 1 item 7: the checks reads of ONE head are joined. A
#: workflow of 30 jobs sends about 60 ``check_run`` events, and each read is
#: two calls or more. The first event of a head reads at once and LEADS. Each
#: event in the next :data:`JOIN_WINDOW_S` only marks "read again". When the
#: window ends, the leader reads once more if a mark is there. So a burst gives
#: two reads, and a long run one read in each window.
JOIN_WINDOW_S = 20.0
#: The most trailing reads that one leader makes before it lets go.
MAX_TRAILING_READS = 15

#: §8.5 step 3, the words of GitHub.
FAIL_CONCLUSIONS: frozenset[str] = frozenset({
    "failure", "timed_out", "cancelled", "action_required", "startup_failure",
})
PENDING_STATUSES: frozenset[str] = frozenset({
    "queued", "in_progress", "waiting", "requested", "pending",
})
SKIP_CONCLUSIONS: frozenset[str] = frozenset({"neutral", "skipped", "stale"})

_NAME_RE = re.compile(r"\A[A-Za-z0-9._-]{1,100}\Z")
_SHA_RE = re.compile(r"\A[0-9a-f]{40}\Z")

_SEMAPHORE: asyncio.Semaphore | None = None
_SEMAPHORE_LOOP: Any = None


class RateLimited(Exception):
    """GitHub answered a rate limit (a 429, or a 403 marked as one)."""


def _obj(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def split_repo(repo: str) -> tuple[str, str] | None:
    """``owner/repo`` as two checked names, or ``None``."""
    owner, slash, name = (repo or "").partition("/")
    if not slash or not all(_NAME_RE.match(p) and p not in (".", "..") for p in (owner, name)):
        return None
    return owner, name


def split_ref(ref: str) -> tuple[str, int] | None:
    """``owner/repo#N`` as ``("owner/repo", N)``, or ``None``."""
    repo, sep, number = (ref or "").partition("#")
    if not sep or not number.isdigit() or len(number) > 10 or split_repo(repo) is None:
        return None
    return repo, int(number)


def _stamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


# ── The pull request write (§8.4, §8.6) ─────────────────────────────────────

#: One pull request's facts onto every reference of the org, and only when
#: GitHub's ``updated_at`` is NOT OLDER than the row's (§8.6). A new head makes
#: the CI state ``pending`` until the checks read writes. A good read clears
#: the refusal markers.
#:
#: ⚠️ **The same second** (fix round 1 item 5, fix round 2 item 3). GitHub's
#: ``updated_at`` has whole seconds, so two events of one second carry one
#: stamp. ``pr_rank`` settles their order: an open event is 0, a ``merged`` or
#: ``closed`` one is 1, and a ``reopened`` one is 2, because a reopen follows a
#: close. At an equal stamp an event writes only when its rank is not lower
#: than the row's. So a late ``opened`` cannot undo a merge, and a close then
#: a reopen in one second ends ``open``, in either order of delivery. A READ
#: (the poll, the lazy read) is the state at GitHub now, so at an equal stamp
#: it always writes. A row with no rank yet reads as 1 when it is terminal.
#:
#: ⚠️ **A merge is final** (fix round 5, item 3). GitHub never unmerges a pull
#: request, so a row in ``merged`` takes only a ``merged`` write. A stale read
#: or a late event of ``closed`` or ``open`` cannot turn it back. Without
#: this, a stale ``closed`` over ``merged`` in one second let a later read
#: count the move INTO ``merged`` a second time (the probe PROBE5b).
#:
#: ⚠️ **The old state comes back** (fix round 2, item 1). The CTE locks the
#: rows and returns each one's state from BEFORE the write, so the caller runs
#: merge-to-Done only for a row that moves INTO ``merged`` or ``closed``.
_PR_UPDATE_SQL = (
    "WITH old AS ("
    "  SELECT id, state AS old_state FROM pm_task_refs "
    "  WHERE organization_id = CAST(:org AS uuid) AND provider = 'github' "
    "  AND kind = 'pull_request' AND ref = :ref FOR UPDATE) "
    "UPDATE pm_task_refs r SET "
    "title = COALESCE(CAST(:title AS text), r.title), state = CAST(:state AS text), "
    "checks_state = CASE WHEN CAST(:sha AS text) IS NOT NULL "
    "  AND r.head_sha IS DISTINCT FROM CAST(:sha AS text) "
    "  THEN 'pending' ELSE r.checks_state END, "
    "head_sha = COALESCE(CAST(:sha AS text), r.head_sha), "
    "head_ref = COALESCE(CAST(:head_ref AS text), r.head_ref), "
    "pr_rank = CASE WHEN CAST(:is_read AS boolean) AND CAST(:rank AS smallint) = 0 "
    "  AND r.pr_updated_at = CAST(:updated AS timestamptz) "
    "  AND COALESCE(r.pr_rank, CASE WHEN r.state IN ('merged', 'closed') THEN 1 ELSE 0 END) >= 1 THEN CAST(2 AS smallint) "
    "  ELSE CAST(:rank AS smallint) END, "
    "pr_updated_at = CAST(:updated AS timestamptz), "
    "merged_at = CAST(:merged AS timestamptz), "
    "unreadable_since = NULL, unblocked_at = NULL, "
    "fetched_at = now(), updated_at = now() "
    "FROM old WHERE r.id = old.id "
    "AND (r.pr_updated_at IS NULL OR r.pr_updated_at < CAST(:updated AS timestamptz) "
    "  OR (r.pr_updated_at = CAST(:updated AS timestamptz) "
    "      AND (CAST(:is_read AS boolean) "
    "           OR CAST(:rank AS smallint) >= COALESCE(r.pr_rank, CASE WHEN r.state IN ('merged', 'closed') THEN 1 ELSE 0 END)))) "
    "AND NOT (r.state IS NOT DISTINCT FROM 'merged' "
    "         AND CAST(:state AS text) IS DISTINCT FROM 'merged') "
    "RETURNING r.id, r.task_id, r.state, r.head_sha, old.old_state"
)

#: The terminal states of a pull request reference.
TERMINAL: frozenset[str] = frozenset({"merged", "closed"})

#: The answers of a pull request read that mean "this ONE pull request is out
#: of reach": no access (404), gone (410), and a renamed repository (301,
#: redirects are off). ⚠️ A 403 is NOT one of them (fix round 2, item 2). An
#: IP allow list of the customer, or a secondary rate limit with no
#: ``retry-after``, refuses EVERY pull request of the installation, and a
#: sibling merge must not finish a task while its other pull request is open.
#: A 403 raises :class:`PullRequestForbidden`, and the reference keeps
#: blocking. A 5xx is not a refusal either.
REFUSED_STATUSES: frozenset[int] = frozenset({301, 404, 410})

#: A read that found nothing newer: stamp it, and clear the refusal markers.
_READ_OK_SQL = (
    "UPDATE pm_task_refs SET fetched_at = now(), unreadable_since = NULL, "
    "unblocked_at = NULL "
    "WHERE organization_id = CAST(:org AS uuid) AND provider = 'github' "
    "AND kind = 'pull_request' AND ref = :ref"
)

#: An attempt that told us nothing (a 403): stamp it only, so the row waits
#: and does not hold a slot of every tick. The markers stay as they are.
_ATTEMPT_SQL = (
    "UPDATE pm_task_refs SET fetched_at = now() "
    "WHERE organization_id = CAST(:org AS uuid) AND provider = 'github' "
    "AND kind = 'pull_request' AND ref = :ref"
)

#: A refused read: stamp it, keep the FIRST refusal time, and set
#: ``unblocked_at`` ONCE, on the read that first finds the bound crossed
#: (fix round 2, item 1). ``crossed`` names that read.
#:
#: ⚠️ **Only an open or draft row can block** (fix round 4, item 1). A row in
#: ``merged`` or ``closed`` never blocked, so its refusal marks nothing and
#: crosses nothing. Before the fix, a merged pull request that later answered
#: 404 "crossed" the bound, and the rule moved the task to Done again after
#: the agent had moved it back. Every refused row is still stamped.
_REFUSED_SQL = (
    "UPDATE pm_task_refs SET fetched_at = now(), "
    "unreadable_since = CASE WHEN state IN ('open', 'draft') "
    "  THEN COALESCE(unreadable_since, now()) ELSE unreadable_since END, "
    "unblocked_at = CASE WHEN state IN ('open', 'draft') AND unblocked_at IS NULL "
    "  AND unreadable_since IS NOT NULL "
    "  AND unreadable_since <= now() - make_interval(secs => :bound_s) THEN now() "
    "  ELSE unblocked_at END "
    "WHERE organization_id = CAST(:org AS uuid) AND provider = 'github' "
    "AND kind = 'pull_request' AND ref = :ref "
    "RETURNING task_id, unreadable_since, "
    "  (state IN ('open', 'draft') AND unblocked_at = now()) AS crossed"
)


class PullRequestForbidden(Exception):
    """GitHub answered 403 with no rate limit: not a per-PR refusal."""


def event_rank(state: str | None, action: str | None) -> int:
    """The order of an event inside one second (§8.6). Pure."""
    if state in TERMINAL:
        return 1
    return 2 if action == "reopened" else 0


async def write_pull_request(
    db: Any, org: str, ref: str, pull_request: dict[str, Any], *, action: str | None = None,
) -> list[Any]:
    """Write one pull request's facts onto the org's references (§8.4).

    *action* is the webhook's ``action``, and ``None`` for a READ (the poll,
    the lazy read). Returns the rows it wrote, each with its ``old_state``,
    and none for an event older than the row.
    """
    from gateway.routes.projects.refs import _clean_title, github_state

    updated = _stamp(pull_request.get("updated_at"))
    if updated is None:
        return []
    head = _obj(pull_request.get("head"))
    sha = str(head.get("sha") or "").lower()
    head_ref = str(head.get("ref") or "")[:255] or None
    state = github_state("pull_request", pull_request)
    return list((await db.execute(text(_PR_UPDATE_SQL), {
        "org": org, "ref": ref,
        "title": _clean_title(str(pull_request.get("title") or "")) or None,
        "state": state,
        "sha": sha if _SHA_RE.match(sha) else None,
        "head_ref": head_ref,
        "updated": updated,
        "merged": _stamp(pull_request.get("merged_at")),
        "rank": event_rank(state, action),
        "is_read": action is None,
    })).fetchall())


def moved_into_terminal(written: list[Any]) -> set[str]:
    """The tasks whose reference this write moved to a state that runs §12.
    Pure. Two moves count (§12.1):

    * INTO ``merged`` from any other state, ``closed`` too (fix round 3,
      item 1). A reopen that no one saw leaves the row ``closed``, and the
      merge after it must still run the rule;
    * INTO ``closed`` (no merge) from a state that is not terminal. A close
      can finish a task whose other pull request merged earlier.
    """
    moved: set[str] = set()
    for r in written:
        old = getattr(r, "old_state", None)
        if (r.state == "merged" and old != "merged") or (
                r.state == "closed" and old not in TERMINAL):
            moved.add(str(r.task_id))
    return moved


async def apply_pull_request(
    db: Any, org: str, ref: str, pull_request: dict[str, Any], *,
    delivery: str | None = None, action: str | None = None,
) -> tuple[list[Any], Any]:
    """THE write path of a pull request's facts (fix round 1, item 1).

    The webhook, the poll and the lazy read of ``GET /refs`` all call it. It
    writes the facts (:func:`write_pull_request`), then runs merge-to-Done
    (§12) in the same transaction, for the tasks whose reference the write
    moved INTO ``merged`` or ``closed``. Returns ``(written rows,
    MergeOutcome or None)``. The caller emits the events after its commit.

    ⚠️ **A transition, not a state** (fix round 2, item 1). A read of a pull
    request that is already ``merged`` writes too (§8.6), and the rule must
    not run again. Else a task that an agent or a workflow moved back to In
    progress went to Done again at every lazy read and every ``edited``
    event, and a recurring task gained a successor each time.
    """
    from gateway.routes.projects import github_merge

    written = await write_pull_request(db, org, ref, pull_request, action=action)
    moved = moved_into_terminal(written)
    if not moved:
        return written, None
    state = written[0].state
    merged_at = _stamp(pull_request.get("merged_at")) if state == "merged" else None
    login = _obj(pull_request.get("merged_by")).get("login")
    outcome = await github_merge.on_merged(
        db, org, ref, merged_at, str(login) if login else None, delivery=delivery,
        only_tasks=moved,
    )
    return written, outcome


async def record_read(db: Any, org: str, ref: str) -> None:
    """A read that found nothing newer: stamp the row, clear the refusal."""
    await db.execute(text(_READ_OK_SQL), {"org": org, "ref": ref})


async def record_attempt(db: Any, org: str, ref: str) -> None:
    """A read that told nothing (a 403): stamp the row only."""
    await db.execute(text(_ATTEMPT_SQL), {"org": org, "ref": ref})


async def record_refusal(db: Any, org: str, ref: str) -> Any:
    """A read that GitHub refused (§12.3 step 4). Returns a ``MergeOutcome``
    or ``None``.

    It stamps the row and keeps the FIRST refusal time. The ONE read that
    first finds that time older than ``github_merge.UNREADABLE_AFTER`` sets
    ``unblocked_at`` and runs the rule again for the tasks of that row. A
    later refused read runs nothing (fix round 2, item 1).
    """
    from gateway.routes.projects import github_merge

    rows = (await db.execute(text(_REFUSED_SQL), {
        # An INT through make_interval (test_sql_interval_shape.py, round 6).
        "org": org, "ref": ref, "bound_s": int(github_merge.UNREADABLE_AFTER.total_seconds()),
    })).fetchall()
    crossed = {str(r.task_id) for r in rows if r.crossed}
    if not crossed:
        return None
    return await github_merge.on_unblocked(db, org, ref, only_tasks=crossed)


# ── The reads ───────────────────────────────────────────────────────────────


def _check(answer: github_app.Answer) -> github_app.Answer:
    if answer.rate_limited():
        raise RateLimited
    return answer


async def read_pull_request(installation_id: int, repo: str, number: int) -> dict[str, Any] | None:
    """``GET /repos/{o}/{r}/pulls/{n}``, or ``None`` when GitHub refuses it
    (one of :data:`REFUSED_STATUSES`).

    Raises :class:`RateLimited`, ``GitHubCallFailed`` (a 5xx too) or
    ``GitHubAppNotConfigured``.
    """
    names = split_repo(repo)
    if names is None or number <= 0:
        return None
    answer = _check(await github_app.installation_call(
        installation_id, "GET", f"/repos/{names[0]}/{names[1]}/pulls/{int(number)}"))
    if answer.ok and isinstance(answer.body, dict):
        return answer.body
    if answer.status in REFUSED_STATUSES:
        return None
    if answer.status == 403:
        # Not a per-PR refusal (fix round 2, item 2): the reference keeps blocking.
        _log.info("github.read.forbidden", status=403)
        raise PullRequestForbidden
    # A 5xx or an odd answer is not a refusal: the next read tries again.
    raise github_app.GitHubCallFailed(f"status_{answer.status}")


async def _check_runs(installation_id: int, repo: tuple[str, str], sha: str) -> list[dict[str, Any]]:
    runs: list[dict[str, Any]] = []
    for page in range(1, CHECK_PAGES + 1):
        answer = _check(await github_app.installation_call(
            installation_id, "GET",
            f"/repos/{repo[0]}/{repo[1]}/commits/{sha}/check-runs?per_page=100&page={page}"))
        if not answer.ok or not isinstance(answer.body, dict):
            raise github_app.GitHubCallFailed(f"status_{answer.status}")
        items = answer.body.get("check_runs")
        if not isinstance(items, list):
            break
        runs += [r for r in items if isinstance(r, dict)]
        if len(items) < 100:
            break
    return runs


async def _combined_status(installation_id: int, repo: tuple[str, str], sha: str) -> dict[str, Any]:
    answer = _check(await github_app.installation_call(
        installation_id, "GET", f"/repos/{repo[0]}/{repo[1]}/commits/{sha}/status"))
    if not answer.ok or not isinstance(answer.body, dict):
        raise github_app.GitHubCallFailed(f"status_{answer.status}")
    return answer.body


# ── The fold (§8.5 steps 3 and 4) ───────────────────────────────────────────


def fold_checks(
    check_runs: list[dict[str, Any]], combined: dict[str, Any] | None,
) -> tuple[str, dict[str, int]]:
    """``(state, summary)`` for one head. Pure.

    ``failing`` when a run concluded in :data:`FAIL_CONCLUSIONS` or the
    combined status is ``failure`` or ``error``. Else ``pending`` when a run is
    not finished or the combined status is ``pending`` with one context or
    more. Else ``passing`` when a run or a status exists. Else ``none``.
    ``neutral``, ``skipped`` and ``stale`` count as skipped.
    """
    counts = {"passed": 0, "failed": 0, "pending": 0, "skipped": 0, "total": 0}
    run_failed = run_pending = False
    for run in check_runs:
        status = str(run.get("status") or "").lower()
        conclusion = str(run.get("conclusion") or "").lower()
        if status != "completed" or status in PENDING_STATUSES:
            counts["pending"] += 1
            run_pending = True
        elif conclusion in FAIL_CONCLUSIONS:
            counts["failed"] += 1
            run_failed = True
        elif conclusion in SKIP_CONCLUSIONS:
            counts["skipped"] += 1
        else:
            counts["passed"] += 1
    combined = combined or {}
    contexts = [s for s in combined.get("statuses") or [] if isinstance(s, dict)]
    for status_row in contexts:
        state = str(status_row.get("state") or "").lower()
        if state in ("failure", "error"):
            counts["failed"] += 1
        elif state == "pending":
            counts["pending"] += 1
        elif state == "success":
            counts["passed"] += 1
        else:
            counts["skipped"] += 1
    counts["total"] = len(check_runs) + len(contexts)
    combined_state = str(combined.get("state") or "").lower()
    if run_failed or combined_state in ("failure", "error"):
        return "failing", counts
    if run_pending or (combined_state == "pending" and len(contexts) > 0):
        return "pending", counts
    if counts["total"] > 0:
        return "passing", counts
    return "none", counts


# ── The checks read (§8.5) ──────────────────────────────────────────────────

_HEAD_HELD_SQL = (
    "SELECT 1 FROM pm_task_refs WHERE organization_id = CAST(:org AS uuid) "
    "AND provider = 'github' AND kind = 'pull_request' AND head_sha = :sha LIMIT 1"
)

_CHECKS_WRITE_SQL = (
    "UPDATE pm_task_refs SET checks_state = :state, "
    "checks_summary = CAST(:summary AS jsonb), checks_fetched_at = now(), "
    "updated_at = now() "
    "WHERE organization_id = CAST(:org AS uuid) AND provider = 'github' "
    "AND kind = 'pull_request' AND head_sha = :sha"
)


def _semaphore() -> asyncio.Semaphore:
    """One semaphore for each event loop of the process."""
    global _SEMAPHORE, _SEMAPHORE_LOOP
    loop = asyncio.get_running_loop()
    if _SEMAPHORE is None or _SEMAPHORE_LOOP is not loop:
        _SEMAPHORE, _SEMAPHORE_LOOP = asyncio.Semaphore(CONCURRENCY), loop
    return _SEMAPHORE


async def installation_of_org(org: str) -> int | None:
    """The installation id of the org's live ``app`` row, or ``None``."""
    async with tenant_session(org) as db:
        row = await store.get(db, org, github_connection.PROVIDER, github_connection.OWNING_APP)
    return github_connection.installation_of(row)


async def fetch_checks(
    installation_id: int, repo: str, sha: str,
) -> tuple[str, dict[str, int]] | None:
    """The GitHub half of a checks read: no database session is open here.
    ``None`` for a bad name or sha. Raises :class:`RateLimited` and the
    errors of the HTTP door."""
    names = split_repo(repo)
    if names is None or not _SHA_RE.match(sha or ""):
        return None
    runs = await _check_runs(installation_id, names, sha)
    combined = await _combined_status(installation_id, names, sha)
    return fold_checks(runs, combined)


async def write_checks(db: Any, org: str, sha: str, state: str, summary: dict[str, int]) -> int:
    """Write a checks result, only onto rows that still hold *sha* (§8.5 step 5)."""
    result = await db.execute(text(_CHECKS_WRITE_SQL), {
        "org": org, "sha": sha, "state": state,
        "summary": json.dumps(summary, separators=(",", ":")),
    })
    return int(getattr(result, "rowcount", 0) or 0)


async def read_checks(org: str, repo: str, sha: str) -> None:
    """The background checks read of §8.5. It never raises."""
    sha = (sha or "").lower()
    if split_repo(repo) is None or not _SHA_RE.match(sha):
        return
    try:
        async with _semaphore():
            async with tenant_session(org) as db:
                row = await store.get(db, org, github_connection.PROVIDER,
                                      github_connection.OWNING_APP)
                held = (await db.execute(text(_HEAD_HELD_SQL), {"org": org, "sha": sha})).first()
            installation = github_connection.installation_of(row)
            if installation is None or held is None:
                return
            # The session is closed: GitHub never holds a connection.
            got = await fetch_checks(installation, repo, sha)
            if got is None:
                return
            async with tenant_session(org) as db:
                await write_checks(db, org, sha, got[0], got[1])
    except Exception as exc:  # a read that fails keeps the row (§8.5)
        _log.info("github.checks.read_failed", error=type(exc).__name__)


def _join_ttl() -> int:
    return max(int(JOIN_WINDOW_S * 3), 5)


async def read_checks_joined(
    org: str, repo: str, sha: str, *, trailing_in_background: bool = False,
) -> int:
    """The checks read of §8.5, joined for each ``(org, head)``. Never raises.

    ``SET key("github", "checks", <sha>, "lead") NX`` through the tenant Redis
    wrapper picks one leader. A job that does not lead sets the ``again`` mark
    and returns. The leader reads at once, then waits :data:`JOIN_WINDOW_S`,
    and reads again only while a mark is there (the trailing read, so the last
    ``completed`` is never lost). Answers the reads that this call made.

    With *trailing_in_background* the wait and the trailing reads run in a
    task of their own, so a webhook answer never holds the request (or a
    restart) for the window. A Redis outage reads once, unjoined. A mark set
    in the last moment after the leader lets go is read by the poll (§11),
    which reads a pending head after 5 minutes.
    """
    sha = (sha or "").lower()
    if split_repo(repo) is None or not _SHA_RE.match(sha):
        return 0
    with tenant_redis.organization_scope(org):
        try:
            client = tenant_redis.get_tenant_redis()
            leads = bool(await client.set(client.key("github", "checks", sha, "lead"), "1",
                                          nx=True, ex=_join_ttl()))
            if not leads:
                await client.set(client.key("github", "checks", sha, "again"), "1",
                                 ex=_join_ttl())
                return 0
        except Exception as exc:  # the class only: an outage reads, unjoined
            _log.info("github.checks.join_unavailable", error=type(exc).__name__)
            await read_checks(org, repo, sha)
            return 1
    await read_checks(org, repo, sha)
    if trailing_in_background:
        task = asyncio.get_running_loop().create_task(_trail(org, repo, sha))
        _TRAILS.add(task)
        task.add_done_callback(_TRAILS.discard)
        return 1
    return 1 + await _trail(org, repo, sha)


#: The trailing loops that a webhook started, held so the loop keeps them.
_TRAILS: set[asyncio.Task[int]] = set()


async def _trail(org: str, repo: str, sha: str) -> int:
    """The leader's wait and its trailing reads, then the lead goes."""
    reads = 0
    with tenant_redis.organization_scope(org):
        try:
            client = tenant_redis.get_tenant_redis()
            lead = client.key("github", "checks", sha, "lead")
            again = client.key("github", "checks", sha, "again")
        except Exception as exc:  # the class only
            _log.info("github.checks.join_failed", error=type(exc).__name__)
            return 0
        try:
            for _ in range(MAX_TRAILING_READS):
                await asyncio.sleep(JOIN_WINDOW_S)
                if not await client.getdel(again):
                    break
                await client.set(lead, "1", ex=_join_ttl())
                await read_checks(org, repo, sha)
                reads += 1
        except Exception as exc:  # the class only
            _log.info("github.checks.join_failed", error=type(exc).__name__)
        finally:
            try:
                await client.delete(lead)
                # ⚠️ A mark set between the last GETDEL and the DELETE saw the
                # lead still held, so its job stopped (fix round 2, item 4).
                # Look once more AFTER the lead is gone. A mark set after the
                # DELETE belongs to a job that leads by itself.
                if await client.getdel(again):
                    await read_checks(org, repo, sha)
                    reads += 1
            except Exception as exc:  # the class only
                _log.info("github.checks.join_release_failed", error=type(exc).__name__)
    return reads


def checks_job(org: str, repo: str, sha: str) -> Any:
    """A background job for the webhook's answer: a JOINED read (item 7)."""
    async def _job() -> None:
        await read_checks_joined(org, repo, sha, trailing_in_background=True)

    return _job


__all__ = [
    "CHECK_PAGES",
    "CONCURRENCY",
    "REFUSED_STATUSES",
    "TERMINAL",
    "PullRequestForbidden",
    "RateLimited",
    "apply_pull_request",
    "checks_job",
    "event_rank",
    "fetch_checks",
    "fold_checks",
    "installation_of_org",
    "moved_into_terminal",
    "read_checks",
    "read_checks_joined",
    "read_pull_request",
    "record_attempt",
    "record_read",
    "record_refusal",
    "split_ref",
    "split_repo",
    "write_checks",
    "write_pull_request",
]
