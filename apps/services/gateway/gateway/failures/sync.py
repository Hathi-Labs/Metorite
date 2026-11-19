"""The sync job of the failure store: one task for each fingerprint (TOF-2e).

Spec: ``project-docs/specs/tool_failure_log.md`` §5.4, §7 and §11 (D225).
Fences: ``tests/unit/test_tool_failure_tasks_r8.py`` (R8, the states and the
prune), ``tests/unit/test_failure_no_tenant_text.py`` (P1),
``tests/unit/test_failure_store_r8.py`` (P9) and
``tests/unit/test_failure_capture_sites.py`` (P6: only this module and
``acb_common.failure_log`` name a ``failure_*`` SQL name).

**The writer.** :func:`seed` is a hook of ``worker.seed_hooks``. It returns for
each org except ``TOOL_FAILURE_ORG_ID``, and it enqueues one
:data:`KIND` job for each 15-minute bucket. It starts no loop. The job is
``detached`` and idempotent, with the effect ``write`` (BR-7a).

**One transaction for each fingerprint.** For each due fingerprint the job
opens ``tenant_session(TOOL_FAILURE_ORG_ID)``. It makes or moves the task and
calls ``failure_set`` in that one transaction. So a second try after a
failed try reads the task id, and makes no second task. ``failure_set`` also
refuses a second task id for a fingerprint.

**The principal.** A task is made through ``check_task_values`` and
``create_task_in``, as the member ``TOOL_FAILURE_RUN_AS`` with the source
``automation``. That member must see the project. A move goes through
``apply_status_transition`` as ``system:tool-failures`` with
``automation=True``, as GH-6 does.

**What it reads.** The platform rows of ``failure_due`` and the band of
``failure_org_band``. In the internal org it reads its own tasks, their
``pull_request`` references and their lanes. It reads no tenant row of
another org.

**The text (§7.3).** Every value of the title, the description and each
comment comes from a platform row or from this module, in a code span. No
error text and no tenant text reaches a task, so a task holds no place for
an injected instruction.

**The lanes.** The project has four lanes, found by NAME: Open, Review, Done
and Cancelled. No status category names review. A lane that a person
renamed is missing: the job logs ``failure_sync.lane_missing`` with the name,
and moves no task that needs it.

**The prune (§5.4).** :func:`prune_hook` is a hook of ``worker.prune_hooks``.
In the session of each org it deletes ``failure_events`` older than 30 days
and ``failure_org_days`` older than 90 days. In the internal org it also
calls ``failure_prune_daily`` for the rows older than 400 days. It runs in a
savepoint, and it logs and swallows a failure, so the delete of the ``jobs``
rows of that prune stays.

``TOOL_FAILURE_TASKS`` (default off) gates the seed, the job, the prune and
the GH-6 skip (:func:`is_failure_task`). :func:`tasks_enabled` and
:func:`config` are the readers of the flag and the five settings.

**The order of a run.** ``failure_due`` gives the watched rows first, then
the rows with new counts, then the muted rows. A row that waits or is muted
writes its total as seen. The rows with no task run before any git read,
and the git reads of one run stop at :data:`GIT_BUDGET_S`.
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from dataclasses import dataclass, field
from dataclasses import replace as _copy
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from acb_auth import UserContext
from acb_auth.roles import UserRole
from acb_common import get_logger, get_settings
from acb_common.db import tenant_session
from acb_common.failure_fingerprint import ERROR_CODES
from sqlalchemy import text

from gateway import build_info
from gateway.jobs import queue, worker

_log = get_logger("gateway.failures.sync")

#: The kind of the sync job.
KIND = "failure.sync"
#: The policy of the ``write`` kind (BR-7a, ``core_brief.md`` §8).
POLICY = ("D225, tool_failure_log.md §7: makes and moves the tasks of the project "
          "TOOL_FAILURE_PROJECT_ID, in the internal org TOOL_FAILURE_ORG_ID only")
#: The actor of each move and comment of the job.
ACTOR = "system:tool-failures"
#: The platform rows that one run reads, at most.
DUE_LIMIT = 200
#: The commit times that one run reads from git, at most.
MAX_RELEASES = 64
#: The seconds of git reads in one run, at most. The handler has 240 s.
GIT_BUDGET_S = 30.0
#: A release holds the fix when its commit time is at or after
#: ``fix_merged_at`` minus this (§7.4).
FIX_TOLERANCE = timedelta(seconds=10)
#: The job rewrites the counts block of a task at most once in this time.
COUNTS_EVERY = timedelta(hours=1)
#: The retention of §5.4.
EVENTS_DAYS = 30
ORG_DAYS_DAYS = 90
DAILY_DAYS = 400

#: The lanes of the project, by name (§7.1).
OPEN, REVIEW, DONE, CANCELLED = "Open", "Review", "Done", "Cancelled"
#: The states in which the job watches a task, and a move to Done goes back.
WATCHED = frozenset({"open", "fix_merged", "fix_live"})

_SHA = re.compile(r"^[0-9a-f]{7,40}$")
_BOUND_ORG = "CAST(NULLIF(current_setting('app.tenant_id', true), '') AS uuid)"

_DUE_SQL = "SELECT * FROM failure_due(CAST(:n AS integer))"
_BAND_SQL = "SELECT failure_org_band(:fp)"
_SET_SQL = (
    "SELECT failure_set(:fp, :state, CAST(:task AS uuid), "
    "CAST(:merged AS timestamptz), CAST(:live AS timestamptz), "
    "CAST(:closed AS timestamptz), :release, CAST(:shown AS bigint), "
    "CAST(:reopened AS integer))"
)
_PRUNE_EVENTS_SQL = (
    f"DELETE FROM failure_events WHERE organization_id = {_BOUND_ORG} "
    "AND occurred_at < CAST(:before AS timestamptz)"
)
_PRUNE_ORG_DAYS_SQL = (
    f"DELETE FROM failure_org_days WHERE organization_id = {_BOUND_ORG} "
    "AND day < CAST(:before AS date)"
)
_PRUNE_DAILY_SQL = "SELECT failure_prune_daily(CAST(:before AS date))"
_LANES_SQL = (
    "SELECT id, name, category FROM pm_task_statuses "
    "WHERE project_id = CAST(:owner AS uuid)"
)
_TASK_SQL = (
    "SELECT t.*, s.name AS status_name, s.category AS status_category "
    "FROM pm_tasks t JOIN pm_task_statuses s ON s.id = t.status_id "
    "WHERE t.id = CAST(:id AS uuid) FOR UPDATE OF t"
)
_MERGED_SQL = (
    "SELECT max(merged_at) FROM pm_task_refs "
    "WHERE task_id = CAST(:tid AS uuid) AND kind = 'pull_request' "
    "AND merged_at IS NOT NULL "
    "AND (CAST(:after AS timestamptz) IS NULL OR merged_at > CAST(:after AS timestamptz))"
)


# ── The settings ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Config:
    """The five settings of §11 for the job, each checked."""

    org: str
    project: str
    run_as: str
    external_min: int
    close_days: int


def _uuid(value: object) -> str:
    raw = str(value or "").strip().lower()
    try:
        return str(uuid.UUID(raw)) if raw else ""
    except ValueError:
        return ""


def tasks_enabled() -> bool:
    """``TOOL_FAILURE_TASKS`` (default off). It gates the seed, the job and
    the prune."""
    return bool(getattr(get_settings(), "tool_failure_tasks", False))


def failure_project_id() -> str:
    """``TOOL_FAILURE_PROJECT_ID`` as a lower-case UUID, or ``""``."""
    return _uuid(getattr(get_settings(), "tool_failure_project_id", ""))


def config() -> Config | None:
    """The settings of the job, or None when one of the three ids is empty
    or not valid. Then the job does nothing (§11)."""
    s = get_settings()
    org = _uuid(getattr(s, "tool_failure_org_id", ""))
    project = failure_project_id()
    run_as = str(getattr(s, "tool_failure_run_as", "") or "").strip().lower()
    if not org or not project or "@" not in run_as:
        return None
    return Config(
        org=org, project=project, run_as=run_as,
        external_min=max(1, int(getattr(s, "tool_failure_external_min", 20) or 20)),
        close_days=max(1, int(getattr(s, "tool_failure_close_days", 7) or 7)),
    )


def is_failure_task(org: object, project: object) -> bool:
    """True for a task of the project "Tool failures": ``TOOL_FAILURE_TASKS``
    is on, and the task is in ``TOOL_FAILURE_PROJECT_ID`` of the org
    ``TOOL_FAILURE_ORG_ID``. GH-6 then skips it (§7.5). With the job off,
    GH-6 keeps its rule, because nothing else would close the task."""
    cfg = config()
    return (tasks_enabled() and cfg is not None and _uuid(org) == cfg.org
            and _uuid(project) == cfg.project)


def _now() -> datetime:
    return datetime.now(UTC)


# ── The platform row ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Day:
    """One day of ``failure_daily`` in the answer of ``failure_due``."""

    day: date
    n: int
    releases: tuple[str, ...]


@dataclass(frozen=True)
class Due:
    """One row of ``failure_due``. Platform fields only (§10.1)."""

    fingerprint: str
    tool: str
    error_code: str
    exc_type: str
    code_site: str
    sources: tuple[str, ...]
    surfaces: tuple[str, ...]
    first_seen_at: datetime
    last_seen_at: datetime
    first_release: str | None
    last_release: str | None
    #: The hour of the newest count that named a release. A NULL-release
    #: count never moves it.
    last_release_at: datetime | None
    total: int
    state: str
    task_id: str | None
    fix_merged_at: datetime | None
    fix_live_at: datetime | None
    closed_at: datetime | None
    fix_release: str | None
    reopened: int
    shown_total: int
    n_1d: int
    n_7d: int
    n_30d: int
    days: tuple[Day, ...] = ()

    @classmethod
    def of(cls, row: Any) -> Due:
        raw = row.days
        if isinstance(raw, str):
            raw = json.loads(raw)
        days = tuple(
            Day(day=date.fromisoformat(str(d["day"])[:10]), n=int(d.get("n") or 0),
                releases=tuple(str(r) for r in (d.get("releases") or ())))
            for d in (raw or ()))
        return cls(
            fingerprint=str(row.fingerprint), tool=str(row.tool),
            error_code=str(row.error_code), exc_type=str(row.exc_type),
            code_site=str(row.code_site), sources=tuple(row.sources or ()),
            surfaces=tuple(row.surfaces or ()), first_seen_at=_aware(row.first_seen_at),
            last_seen_at=_aware(row.last_seen_at), first_release=row.first_release,
            last_release=row.last_release, last_release_at=_aware(row.last_release_at),
            total=int(row.total), state=str(row.state),
            task_id=str(row.task_id) if row.task_id else None,
            fix_merged_at=_aware(row.fix_merged_at), fix_live_at=_aware(row.fix_live_at),
            closed_at=_aware(row.closed_at), fix_release=row.fix_release,
            reopened=int(row.reopened), shown_total=int(row.shown_total),
            n_1d=int(row.n_1d), n_7d=int(row.n_7d), n_30d=int(row.n_30d), days=days)


def _aware(value: Any) -> Any:
    if isinstance(value, datetime) and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


@dataclass
class State:
    """The state columns of one fingerprint, as ``failure_set`` writes them."""

    state: str
    task_id: str | None
    fix_merged_at: datetime | None
    fix_live_at: datetime | None
    closed_at: datetime | None
    fix_release: str | None
    shown_total: int
    reopened: int

    @classmethod
    def of(cls, due: Due) -> State:
        return cls(due.state, due.task_id, due.fix_merged_at, due.fix_live_at,
                   due.closed_at, due.fix_release, due.shown_total, due.reopened)


# ── The pure rules (§7.2, §7.4) ─────────────────────────────────────────────


def band_at_least_3(band: str | None) -> bool:
    """True when the band names 3 organizations or more. NULL is "cannot
    tell", and the band rule does not apply (§7.2)."""
    return band is not None and band.isdigit() and int(band) >= 3


def wants_task(due: Due, band: str | None, cfg: Config) -> bool:
    """§7.2. A code that we own gets a task at once. Another code gets one
    when its count reaches the limit on 2 days in a row, or when the band
    reaches 3 organizations."""
    if ERROR_CODES.get(due.error_code, False):
        return True
    if band_at_least_3(band):
        return True
    counts = {d.day: d.n for d in due.days}
    return any(n >= cfg.external_min and counts.get(day - timedelta(days=1), 0)
               >= cfg.external_min for day, n in counts.items())


def holds_fix(release: str | None, fix_merged_at: datetime | None,
              times: dict[str, datetime]) -> bool:
    """§7.4 "holds the fix": the commit time of *release* is at or after
    *fix_merged_at* minus :data:`FIX_TOLERANCE`. A NULL release, a merge
    that is not known, and a release that git did not answer never hold it."""
    if not release or fix_merged_at is None:
        return False
    at = times.get(release)
    return at is not None and at >= fix_merged_at - FIX_TOLERANCE


def _hour(at: datetime) -> datetime:
    return at.astimezone(UTC).replace(minute=0, second=0, microsecond=0)


def repeat_after_fix(due: Due, st: State, times: dict[str, datetime]) -> bool:
    """§7.4, ``fix_live``: a count of a release that holds the fix, in an
    hour after the hour of ``fix_live_at``.

    Only a count that named a release counts. ``last_release_at`` is the hour
    of the newest such count, and a NULL-release count never moves it, so it
    never reads as a count of ``last_release``. ``last_seen_at`` moves for
    every count, so the rule never reads it. A day after the day of
    ``fix_live_at`` is wholly after that hour, so each release of such a day
    counts too: ``failure_daily.releases`` holds named releases only."""
    if st.fix_live_at is None or st.fix_merged_at is None:
        return False
    if (due.last_release_at is not None and due.last_release_at > _hour(st.fix_live_at)
            and holds_fix(due.last_release, st.fix_merged_at, times)):
        return True
    live_day = st.fix_live_at.astimezone(UTC).date()
    return any(holds_fix(r, st.fix_merged_at, times)
               for d in due.days if d.day > live_day for r in d.releases)


def repeat_after_close(due: Due, st: State, times: dict[str, datetime]) -> bool:
    """§7.4, ``closed``: a new count, and a release that holds the fix on a
    day at or after the close. A count before the close of a release that
    holds the fix reopened the task in ``fix_live`` already."""
    if st.closed_at is None or st.fix_merged_at is None or due.total <= st.shown_total:
        return False
    if (due.last_release_at is not None and due.last_release_at > _hour(st.closed_at)
            and holds_fix(due.last_release, st.fix_merged_at, times)):
        return True
    close_day = st.closed_at.astimezone(UTC).date()
    return any(holds_fix(r, st.fix_merged_at, times)
               for d in due.days if d.day >= close_day for r in d.releases)


# ── The text of the task (§7.3) ─────────────────────────────────────────────


def _code(value: object) -> str:
    """One value in a code span. Each value comes from a column that a CHECK
    of the store holds to a grammar with no backtick."""
    return "`" + str(value).replace("`", "") + "`"


def title(due: Due) -> str:
    """``Tool failure · <tool> · <error_code> · <fingerprint>``."""
    return f"Tool failure · {due.tool} · {due.error_code} · {due.fingerprint}"


#: The headings of the two blocks that the job rewrites (§7.3). Every other
#: block keeps the text that a person may have edited.
COUNTS_HEAD, SEEN_HEAD = "**Counts**", "**Seen**"


def _counts_block(due: Due, band: str | None) -> str:
    orgs = band if band is not None else "unknown"
    return "\n".join([
        COUNTS_HEAD,
        f"- Total: {_code(due.total)}",
        f"- Today (UTC): {_code(due.n_1d)}. 7 days: {_code(due.n_7d)}. "
        f"30 days: {_code(due.n_30d)}",
        f"- Organizations in 30 days: {_code(orgs)}",
    ])


def _seen_block(due: Due) -> str:
    return "\n".join([
        SEEN_HEAD,
        f"- First: {_code(due.first_seen_at.astimezone(UTC).date())}, release "
        f"{_code(due.first_release or '-')}",
        f"- Last: {_code(due.last_seen_at.astimezone(UTC).date())}, release "
        f"{_code(due.last_release or '-')}",
    ])


def with_new_counts(text_now: str | None, due: Due, band: str | None) -> str:
    """*text_now* with only its Counts and Seen blocks replaced (§7.3).

    A block runs from its heading line to the next blank line. Each other
    block stays as it is, so an edit of a person there stays. A block that a
    person removed comes back at the end."""
    blocks = (text_now or "").split("\n\n")
    for head, new in ((COUNTS_HEAD, _counts_block(due, band)), (SEEN_HEAD, _seen_block(due))):
        at = next((i for i, b in enumerate(blocks) if b.strip().startswith(head)), None)
        if at is None:
            blocks.append(new)
        else:
            blocks[at] = new
    return "\n\n".join(b for b in blocks if b.strip())


def description(due: Due, band: str | None, close_days: int) -> str:
    """The five blocks of §7.3, from platform fields only."""
    where = [f"- Sources: {', '.join(_code(s) for s in sorted(due.sources)) or _code('-')}"]
    if band_at_least_3(band):
        where.append(f"- Surfaces: {', '.join(_code(s) for s in sorted(due.surfaces))}")
    return "\n".join([
        "**What failed**",
        f"- Tool: {_code(due.tool)}",
        f"- Code: {_code(due.error_code)}",
        f"- Exception type: {_code(due.exc_type)}",
        f"- Code site: {_code(due.code_site)}",
        "",
        "**Where**",
        *where,
        "",
        _counts_block(due, band),
        "",
        _seen_block(due),
        "",
        "**The rule**",
        "Fix the cause at the code site. Move this task to Review when the pull "
        "request opens. Do not move it to Done. The job closes it "
        f"{close_days} days after the fix is live, with no repeat.",
    ])


ONE_CLOSER = ("The job closes this task after the watch. It moved the task back to "
              "Review.")
REPEAT = ("Repeat after the fix. A release that holds the fix failed again, so the "
          "job opened this task again.")


# ── The lanes and the task ──────────────────────────────────────────────────


@dataclass(frozen=True)
class Lanes:
    """The lane ids of the project, by name. None for a missing lane."""

    by_name: dict[str, str] = field(default_factory=dict)

    def get(self, name: str) -> str | None:
        return self.by_name.get(name.casefold())


async def _lanes(db: Any, project: str) -> Lanes:
    from gateway.routes.projects.core import status_owner_id

    owner = await status_owner_id(db, project)
    rows = (await db.execute(text(_LANES_SQL), {"owner": owner})).fetchall()
    return Lanes({str(r.name).strip().casefold(): str(r.id) for r in rows})


def _lane(lanes: Lanes, name: str) -> str | None:
    lane = lanes.get(name)
    if lane is None:
        _log.warning("failure_sync.lane_missing", name=name)
    return lane


async def _task(db: Any, task_id: str) -> Any | None:
    return (await db.execute(text(_TASK_SQL), {"id": task_id})).fetchone()


def _gone(task: Any | None, lanes: Lanes) -> bool:
    """§7.4 "any → muted": the task is deleted, archived or cancelled."""
    if task is None or task.archived_at is not None:
        return True
    return (str(task.status_id) == lanes.get(CANCELLED)
            or str(task.status_category) == "cancelled")


async def _move(db: Any, task: Any, lane: str) -> Any:
    from gateway.routes.projects.core import apply_status_transition

    await apply_status_transition(db, task, lane, created_by=ACTOR, automation=True)
    return await _task(db, str(task.id))


async def _comment(db: Any, task_id: str, body: str) -> None:
    from gateway.routes.projects.core import record_activity

    await record_activity(db, activity_type="comment", created_by=ACTOR,
                          task_id=task_id, body=body, automation=True)


async def _create(db: Any, cfg: Config, due: Due, band: str | None, lane: str) -> str:
    """The one task of *due*, as the member ``TOOL_FAILURE_RUN_AS``, through
    the one creator. The member must see the project."""
    from gateway.routes.projects.core import resolve_visibility_for
    from gateway.routes.projects.tasks import check_task_values, create_task_in

    user = UserContext(email=cfg.run_as, role=UserRole.EMPLOYEE, organization_id=cfg.org)
    values: dict[str, Any] = {
        "project_id": cfg.project, "title": title(due),
        "description": description(due, band, cfg.close_days),
        "source": "automation", "status_id": lane,
    }
    first = check_task_values(user, values)
    vis = await resolve_visibility_for(db, cfg.run_as)
    created = await create_task_in(db, user, values, first, vis=vis)
    return str(created.row.id)


async def _rewrite(db: Any, task: Any, due: Due, band: str | None) -> None:
    """Replace the Counts and Seen blocks of the task, and nothing else."""
    from gateway.routes.projects.core import update_row

    await update_row(db, "pm_tasks", str(task.id),
                     {"description": with_new_counts(task.description, due, band)})


async def _set(db: Any, fp: str, st: State) -> None:
    await db.execute(text(_SET_SQL), {
        "fp": fp, "state": st.state, "task": st.task_id, "merged": st.fix_merged_at,
        "live": st.fix_live_at, "closed": st.closed_at, "release": st.fix_release,
        "shown": st.shown_total, "reopened": st.reopened})


# ── One fingerprint ─────────────────────────────────────────────────────────


@dataclass
class _Run:
    """The work on one fingerprint that has a task, in one transaction."""

    db: Any
    cfg: Config
    due: Due
    lanes: Lanes
    times: dict[str, datetime]
    now: datetime
    st: State
    task: Any
    notes: list[str] = field(default_factory=list)
    moved: bool = False

    async def move(self, name: str) -> bool:
        """Move the task to the lane *name*. False when the lane is missing."""
        lane = _lane(self.lanes, name)
        if lane is None:
            return False
        self.task = await _move(self.db, self.task, lane)
        self.moved = True
        return True


async def _sync_new(db: Any, cfg: Config, due: Due, band: str | None, lanes: Lanes,
                    st: State) -> str:
    """§7.2: make the one task of a ``new`` fingerprint, when its rule holds.

    A row that waits under the limit writes its total as seen, so it leaves
    ``failure_due`` until a new count comes (TOF-2e review, F1)."""
    if not wants_task(due, band, cfg):
        if st.shown_total != due.total:
            st.shown_total = due.total
            await _set(db, due.fingerprint, st)
        return "waiting"
    lane = _lane(lanes, OPEN)
    if lane is None:
        return "lane_missing"
    st.state, st.shown_total = "open", due.total
    st.task_id = await _create(db, cfg, due, band, lane)
    await _set(db, due.fingerprint, st)
    return "made"


async def _watch(run: _Run, running: str | None) -> str | None:
    """§7.4 and §7.5 for a task that is not muted. Answers ``lane_missing``
    when a lane that the step needs is missing, else None."""
    st, cfg = run.st, run.cfg
    # §7.5 one closer: a move to Done in a watched state goes back to Review.
    if st.state in WATCHED and str(run.task.status_id) == run.lanes.get(DONE):
        if not await run.move(REVIEW):
            return "lane_missing"
        run.notes.append(ONE_CLOSER)
    if st.state == "open":
        merged = (await run.db.execute(text(_MERGED_SQL), {
            "tid": st.task_id, "after": st.fix_merged_at})).scalar()
        if merged is not None:
            st.state, st.fix_merged_at = "fix_merged", _aware(merged)
            run.notes.append(f"A pull request merged on {_code(st.fix_merged_at.date())}. "
                             "The job waits for a live release that holds the fix.")
    reopen = False
    if st.state == "fix_merged":
        if running and holds_fix(running, st.fix_merged_at, run.times):
            st.state, st.fix_live_at, st.fix_release = "fix_live", run.now, running
            close_on = (run.now + timedelta(days=cfg.close_days)).date()
            run.notes.append(f"The fix is live in release {_code(running)}. The job closes "
                             f"this task on {_code(close_on)} if no repeat comes.")
    elif st.state == "fix_live":
        if repeat_after_fix(run.due, st, run.times):
            reopen = True
        elif st.fix_live_at is not None and run.now >= st.fix_live_at + timedelta(
                days=cfg.close_days):
            if not await run.move(DONE):
                return "lane_missing"
            st.state, st.closed_at = "closed", run.now
            run.notes.append(f"Closed. No repeat came for {cfg.close_days} days after "
                             f"release {_code(st.fix_release or '-')} went live.")
    elif st.state == "closed" and repeat_after_close(run.due, st, run.times):
        reopen = True
    if reopen:
        if not await run.move(OPEN):
            return "lane_missing"
        # `fix_merged_at` stays: it is the mark after which a new merge counts.
        st.state, st.reopened = "open", st.reopened + 1
        st.fix_live_at = st.closed_at = st.fix_release = None
        run.notes.append(REPEAT)
    return None


async def _sync_one(db: Any, cfg: Config, due: Due, lanes: Lanes,
                    times: dict[str, datetime], running: str | None,
                    now: datetime) -> str:
    """The states of §7.4 for one fingerprint, in the caller's transaction.
    Answers one word for the log line."""
    band = (await db.execute(text(_BAND_SQL), {"fp": due.fingerprint})).scalar()
    st = State.of(due)
    if st.state == "new":
        return await _sync_new(db, cfg, due, band, lanes, st)
    task = await _task(db, st.task_id) if st.task_id else None
    before = _copy(st)
    if _gone(task, lanes) or (st.state == "muted"
                              and str(task.status_id) != lanes.get(OPEN)):
        # Muted: no write to the task. The counts go on, and the row writes its
        # total as seen, so it sorts after the rows with new counts (F1).
        st.state, st.shown_total = "muted", due.total
        if st != before:
            await _set(db, due.fingerprint, st)
        return "muted"
    woke = st.state == "muted"
    if woke:
        st.state = "open"  # a person moved it back to Open: write again
    run = _Run(db, cfg, due, lanes, times, now, st, task)
    missing = await _watch(run, running)
    if missing:
        return missing
    changed = st != before or run.moved
    if woke or (due.total != st.shown_total and (
            changed or _aware(run.task.updated_at) <= now - COUNTS_EVERY)):
        await _rewrite(db, run.task, due, band)
        st.shown_total = due.total
    for note in run.notes:
        await _comment(db, st.task_id, note)
    if st != before:
        await _set(db, due.fingerprint, st)
    reopened = st.reopened != before.reopened
    return "reopened" if reopened else (st.state if changed else "same")


# ── The run ─────────────────────────────────────────────────────────────────


def _releases(dues: list[Due], running: str | None) -> list[str]:
    """The releases whose commit time a run needs, the running one first."""
    out: list[str] = []
    seen: set[str] = set()

    def add(sha: str | None) -> None:
        if sha and _SHA.match(sha) and sha not in seen and len(out) < MAX_RELEASES:
            seen.add(sha)
            out.append(sha)

    add(running)
    for due in dues:
        if due.state in ("fix_live", "closed"):
            add(due.last_release)
            for d in due.days:
                for r in d.releases:
                    add(r)
    return out


async def _commit_times(shas: list[str]) -> dict[str, datetime]:
    """The commit time of each SHA, from git, with no session open.

    The reads of one run take at most :data:`GIT_BUDGET_S`, plus one call of
    ``build_info.COMMIT_TIME_TIMEOUT_S``. A SHA past the budget gets no time,
    and its state does not move on this run (§7.4)."""
    times: dict[str, datetime] = {}
    loop = asyncio.get_running_loop()
    stop = loop.time() + GIT_BUDGET_S
    for i, sha in enumerate(shas):
        if loop.time() >= stop:
            _log.warning("failure_sync.git_budget_spent", left=len(shas) - i)
            break
        try:
            at = await asyncio.to_thread(build_info.build_committed_at, sha)
        except Exception as exc:  # no time: the state does not move
            _log.warning("failure_sync.git_failed", error=type(exc).__name__)
            continue
        if at is not None:
            times[sha] = at
    return times


def running_release() -> str | None:
    """The release of the running gateway: the SHA that ``build_sha()``
    cached at start, never the ``HEAD`` of today (§7.4)."""
    sha = (build_info.build_sha() or "").strip().lower()
    return sha if _SHA.match(sha) else None


async def sync(cfg: Config, *, now: datetime | None = None,
               running: str | None = None) -> dict[str, int]:
    """One pass over the due fingerprints, in the internal org. Answers the
    count of each outcome. Each fingerprint gets its own transaction, and a
    fingerprint that fails is logged and left for the next run."""
    now = now or _now()
    running = running if running is not None else running_release()
    async with tenant_session(cfg.org) as db:
        dues = [Due.of(r) for r in (await db.execute(
            text(_DUE_SQL), {"n": DUE_LIMIT})).fetchall()]
        lanes = await _lanes(db, cfg.project) if dues else Lanes()
    counts: dict[str, int] = {}
    # A row that has no task needs no commit time. It goes first, so no wait
    # on git can delay a new task (TOF-2e review, P2d).
    await _rows(cfg, [d for d in dues if d.state == "new"], lanes, {}, running, now, counts)
    rest = [d for d in dues if d.state != "new"]
    times = await _commit_times(_releases(rest, running)) if rest else {}
    await _rows(cfg, rest, lanes, times, running, now, counts)
    if counts:
        _log.info("failure_sync.ran", **counts)
    return counts


async def _rows(cfg: Config, dues: list[Due], lanes: Lanes, times: dict[str, datetime],
                running: str | None, now: datetime, counts: dict[str, int]) -> None:
    """Each fingerprint of *dues* in its own transaction. A fingerprint that
    fails is logged with the class only, and left for the next run."""
    for due in dues:
        try:
            async with tenant_session(cfg.org) as db:
                outcome = await _sync_one(db, cfg, due, lanes, times, running, now)
        except Exception as exc:  # the class only: a message can echo a value
            _log.warning("failure_sync.row_failed", fingerprint=due.fingerprint,
                         error=type(exc).__name__,
                         status=getattr(exc, "status_code", None))
            outcome = "failed"
        counts[outcome] = counts.get(outcome, 0) + 1


async def run_job(org: str, payload: dict[str, Any], claim: queue.Claim) -> None:
    """The detached handler of :data:`KIND`. The org comes from the worker,
    never from the payload. A second try reads the task ids that the first
    try committed, so it makes no second task."""
    cfg = config()
    if tasks_enabled() and cfg is not None and str(org).lower() == cfg.org:
        await sync(cfg)
    async with tenant_session(org) as db:
        if not await queue.hold(db, claim):
            return
        await queue.complete(db, claim, now=_now())


async def seed(org: str, now: datetime) -> None:
    """The ``worker.seed_hooks`` hook: one :data:`KIND` job for each bucket,
    in the internal org only."""
    cfg = config()
    if not tasks_enabled() or cfg is None or str(org).lower() != cfg.org:
        return
    async with tenant_session(org) as db:
        await queue.enqueue(db, KIND, {}, dedupe_key=f"failure_sync:{worker.seed_bucket(now)}",
                            now=now)


# ── The prune (§5.4) ────────────────────────────────────────────────────────


async def prune(db: Any, today: date) -> dict[str, int]:
    """Delete the old failure rows of the bound org. Raises on a failure."""
    start = datetime.combine(today, time(), UTC)
    events = await db.execute(text(_PRUNE_EVENTS_SQL), {
        "before": start - timedelta(days=EVENTS_DAYS)})
    org_days = await db.execute(text(_PRUNE_ORG_DAYS_SQL), {
        "before": today - timedelta(days=ORG_DAYS_DAYS)})
    return {"events": int(events.rowcount or 0), "org_days": int(org_days.rowcount or 0)}


async def prune_daily(db: Any, today: date) -> int | None:
    """In the internal org only, delete the platform day rows older than
    :data:`DAILY_DAYS`. None in any other org. Raises on a failure."""
    cfg = config()
    # The tenant that the session binds, never a value from the caller.
    bound = str((await db.execute(text(f"SELECT CAST({_BOUND_ORG} AS text)"))).scalar()
                or "").lower()
    if cfg is None or bound != cfg.org:
        return None
    return int((await db.execute(text(_PRUNE_DAILY_SQL), {
        "before": today - timedelta(days=DAILY_DAYS)})).scalar() or 0)


async def prune_hook(db: Any, today: date) -> None:
    """The ``worker.prune_hooks`` hook, under ``TOOL_FAILURE_TASKS``. It never
    raises: a hook that raises fails the whole prune of the org, and rolls
    back the delete of its ``jobs`` rows. Each step runs in its own savepoint,
    so a failed step keeps the transaction of the prune usable and does not
    undo the other step. The log names the class only."""
    if not tasks_enabled():
        return
    gone: dict[str, int] = {}
    for step, name in ((prune, "tenant"), (prune_daily, "daily")):
        try:
            async with db.begin_nested():
                out = await step(db, today)
        except Exception as exc:  # the class only: a DB message can echo a value
            _log.warning("failure_sync.prune_failed", step=name, error=type(exc).__name__)
            continue
        if isinstance(out, dict):
            gone.update(out)
        elif out is not None:
            gone["daily"] = out
    if any(gone.values()):
        _log.info("failure_sync.pruned", **gone)


def register(*, replace: bool = False) -> None:
    """Register the kind, the seed hook and the prune hook, once."""
    worker.register(KIND, run_job, mode="detached", idempotent=True, replace=replace,
                    effect="write", policy=POLICY)
    if seed not in worker.seed_hooks:
        worker.seed_hooks.append(seed)
    if prune_hook not in worker.prune_hooks:
        worker.prune_hooks.append(prune_hook)


register()
