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

from acb_common import get_logger
from sqlalchemy import text

from gateway.connections import github as github_connection
from gateway.connections import github_app, store
from gateway.db import tenant_session

_log = get_logger("gateway.github_sync")

#: §8.5 step 1: at most this many pages of 100 check runs.
CHECK_PAGES = 3
#: §8.5: the checks reads that run at once in one process.
CONCURRENCY = 4

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
#: ``unreadable_since``.
#:
#: ⚠️ **The same second writes too** (fix round 1, item 5). GitHub's
#: ``updated_at`` has whole seconds, so a pull request that opens and merges in
#: one second sends two events with one stamp. ``<`` dropped the ``closed``
#: event, and merge-to-Done never ran. A replay writes the same facts, so
#: ``<=`` is safe. One tie-break stays: at the SAME second a row in ``merged``
#: or ``closed`` never goes back to ``open`` or ``draft``, so a late ``opened``
#: cannot undo a merge.
_PR_UPDATE_SQL = (
    "UPDATE pm_task_refs SET "
    "title = COALESCE(CAST(:title AS text), title), state = CAST(:state AS text), "
    "checks_state = CASE WHEN CAST(:sha AS text) IS NOT NULL "
    "  AND head_sha IS DISTINCT FROM CAST(:sha AS text) "
    "  THEN 'pending' ELSE checks_state END, "
    "head_sha = COALESCE(CAST(:sha AS text), head_sha), "
    "head_ref = COALESCE(CAST(:head_ref AS text), head_ref), "
    "pr_updated_at = CAST(:updated AS timestamptz), "
    "merged_at = CAST(:merged AS timestamptz), "
    "unreadable_since = NULL, "
    "fetched_at = now(), updated_at = now() "
    "WHERE organization_id = CAST(:org AS uuid) AND provider = 'github' "
    "AND kind = 'pull_request' AND ref = :ref "
    "AND (pr_updated_at IS NULL OR pr_updated_at < CAST(:updated AS timestamptz) "
    "  OR (pr_updated_at = CAST(:updated AS timestamptz) "
    "      AND NOT (state IN ('merged', 'closed') "
    "               AND CAST(:state AS text) IN ('open', 'draft')))) "
    "RETURNING id, task_id, state, head_sha"
)

#: The answers of a pull request read that mean "the App cannot read this":
#: no access (404), gone (410), forbidden (403 with no rate limit), and a
#: renamed repository (301, redirects are off). A 5xx is not one of them.
REFUSED_STATUSES: frozenset[int] = frozenset({301, 403, 404, 410})

#: A read that found nothing newer: stamp it, and clear the refusal marker.
_READ_OK_SQL = (
    "UPDATE pm_task_refs SET fetched_at = now(), unreadable_since = NULL "
    "WHERE organization_id = CAST(:org AS uuid) AND provider = 'github' "
    "AND kind = 'pull_request' AND ref = :ref"
)

#: A refused read: stamp it, and keep the FIRST refusal time.
_REFUSED_SQL = (
    "UPDATE pm_task_refs SET fetched_at = now(), "
    "unreadable_since = COALESCE(unreadable_since, now()) "
    "WHERE organization_id = CAST(:org AS uuid) AND provider = 'github' "
    "AND kind = 'pull_request' AND ref = :ref "
    "RETURNING unreadable_since"
)


async def write_pull_request(
    db: Any, org: str, ref: str, pull_request: dict[str, Any],
) -> list[Any]:
    """Write one pull request's facts onto the org's references (§8.4).

    Returns the rows it wrote, and none for an event older than the row.
    """
    from gateway.routes.projects.refs import _clean_title, github_state

    updated = _stamp(pull_request.get("updated_at"))
    if updated is None:
        return []
    head = _obj(pull_request.get("head"))
    sha = str(head.get("sha") or "").lower()
    head_ref = str(head.get("ref") or "")[:255] or None
    return list((await db.execute(text(_PR_UPDATE_SQL), {
        "org": org, "ref": ref,
        "title": _clean_title(str(pull_request.get("title") or "")) or None,
        "state": github_state("pull_request", pull_request),
        "sha": sha if _SHA_RE.match(sha) else None,
        "head_ref": head_ref,
        "updated": updated,
        "merged": _stamp(pull_request.get("merged_at")),
    })).fetchall())


async def apply_pull_request(
    db: Any, org: str, ref: str, pull_request: dict[str, Any], *, delivery: str | None = None,
) -> tuple[list[Any], Any]:
    """THE write path of a pull request's facts (fix round 1, item 1).

    The webhook, the poll and the lazy read of ``GET /refs`` all call it. It
    writes the facts (:func:`write_pull_request`). When the write puts the
    reference in ``merged`` or ``closed``, it runs merge-to-Done (§12) in the
    same transaction. Returns ``(written rows, MergeOutcome or None)``. The
    caller emits the outcome's events after its commit.

    A read that writes nothing (an older answer) runs no rule, so a late
    ``closed`` for a pull request that opened again moves nothing.
    """
    from gateway.routes.projects import github_merge

    written = await write_pull_request(db, org, ref, pull_request)
    state = written[0].state if written else None
    if state not in ("merged", "closed"):
        return written, None
    merged_at = _stamp(pull_request.get("merged_at")) if state == "merged" else None
    login = _obj(pull_request.get("merged_by")).get("login")
    outcome = await github_merge.on_merged(
        db, org, ref, merged_at, str(login) if login else None, delivery=delivery,
    )
    return written, outcome


async def record_read(db: Any, org: str, ref: str) -> None:
    """A read that found nothing newer: stamp the row, clear the refusal."""
    await db.execute(text(_READ_OK_SQL), {"org": org, "ref": ref})


async def record_refusal(db: Any, org: str, ref: str) -> Any:
    """A read that GitHub refused (§12.3 step 4). Returns a ``MergeOutcome``
    or ``None``.

    It stamps the row and keeps the FIRST refusal time. When that time is
    older than ``github_merge.UNREADABLE_AFTER``, the reference stops
    blocking, so this runs the rule again for each task that holds it.
    """
    from gateway.routes.projects import github_merge

    rows = (await db.execute(text(_REFUSED_SQL), {"org": org, "ref": ref})).fetchall()
    since = min((r.unreadable_since for r in rows if r.unreadable_since), default=None)
    if since is None or not github_merge.past_unreadable_bound(since):
        return None
    return await github_merge.on_unblocked(db, org, ref)


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


def checks_job(org: str, repo: str, sha: str) -> Any:
    """A background job for the webhook's answer."""
    async def _job() -> None:
        await read_checks(org, repo, sha)

    return _job


__all__ = [
    "CHECK_PAGES",
    "CONCURRENCY",
    "REFUSED_STATUSES",
    "RateLimited",
    "apply_pull_request",
    "checks_job",
    "fetch_checks",
    "fold_checks",
    "installation_of_org",
    "read_checks",
    "read_pull_request",
    "record_read",
    "record_refusal",
    "split_ref",
    "split_repo",
    "write_checks",
    "write_pull_request",
]
