"""Projects · the claim gate of "land before build" (LBB-2).

Spec: ``project-docs/specs/engineering_practice.md`` §7b.2, task 4041.

    GET /projects/agent-work/landing?agent_label=<label>

**What it is, and what it is not.** The gate guides agents that cooperate.
It is not a security control. It reads ``pm_agent_runs.agent_label``, which
is display data (``agent_coordination.md`` §3.7). A lane that changes its
label escapes the gate. The gate never opens, grants or shows anything. It
only refuses an agent's own new claim. The amendment of 2026-11-26 to §3.7
names this module as the ONE decision reader of the label, and
``tests/unit/test_agent_label_fence.py`` holds that.

**A counted PR** (§7b.2). A ``pm_task_refs`` row of kind ``pull_request``,
open (``state`` NULL, ``open`` or ``draft``, and ``merged_at`` NULL), on a
task that the caller sees, that is not archived, not done or cancelled, in an
open project. It is older than :data:`GATE_AGE_HOURS` for a claim, and
:data:`LIST_AGE_HOURS` for ``my_work``. Its owner run has the caller's
member and the caller's label. A NULL label binds the PRs to claims that send
no label. Its landing mark is NULL, or ``queued`` for more than
:data:`QUEUED_TTL_HOURS`, or ``parked`` on a task that is not in backlog or
todo. No run of its task waits on a person (``awaiting``).

**Another session works on it** (§7b.10, LBB-2c, task 4064). A PR does not
count while an open run on its task belongs to another session: a run whose
member is not the caller's member, or whose label is not the caller's label.
The run must still hold the task (``NOT filters.holder_free_sql``), so a run
whose lease ended, or whose connection is gone, does not hide the PR. When
that run ends, the PR counts again for the session of its owner run.

**The owner run** (§7b.8, LBB-2b, task 4056). The owner run of a reference
``f`` is the run ``n`` on the same task that held the task when the reference
was added: ``n.created_at <= f.created_at``, and ``n.ended_at`` NULL or
``>= f.created_at``. When two runs qualify, the highest ``created_at`` wins,
then the highest ``id``. A run that starts after the reference never owns it,
so a merger that claims and releases a task after the hand-over never takes
the PR. A reference with no owner run counts for nobody. ``insert_ref`` never
moves ``created_at``, so a repeat add of the same URL never moves the owner.

**No deadlock** (trap 2). The gate never refuses a claim of a task that
holds an open pull request reference, a task with the tag ``landing`` (no
case), a task where the caller already has an open run (the test of
``agent_runs._OPEN_RUN_SQL``, which reads no label), or a claim by a person.

**The three doors.** ``claim_task`` and ``set_assignees`` (``tasks.py``) and
``start_agent_run`` (``agent_runs.py``) call :func:`check_claim_gate` right
after ``claim_scope.check_claim_scope``, before any write.

**The flags.** ``CLAIM_GATE_SHADOW_ORGS`` logs ``claim.gate would_refuse``
and refuses nothing. ``CLAIM_GATE_ORGS`` refuses with 422 ``land_your_pr``
and logs ``claim.gate refused``. Either one shows the ``my_work`` rows. The
readers are :func:`claim_gate_shadow_on` and :func:`claim_gate_on`, and each
reads the settings at each call, so a SIGHUP reload applies a change. With
both empty, :func:`check_claim_gate` returns before its first statement.

The tenant and the member come from the session, never from request input
(R5). Fences: ``tests/unit/test_claim_gate_r8.py`` (R8) and
``tests/unit/test_claim_gate_edge.py``.
"""

from __future__ import annotations

import re
from typing import Any

from acb_auth import UserContext, get_current_user
from acb_common import get_logger
from fastapi import Depends, HTTPException, Query
from gateway.routes.projects.core import (
    TaskModel,
    _tenant_session,
    actor,
    resolve_visibility,
    router,
    row_to_dict,
    task_visibility_clause,
)
from gateway.routes.projects.filters import (
    AGENT_RUN_ANSWER_WAITS_SQL,
    AGENT_RUN_OPEN_STATUSES,
    BOUND_ORG_SQL,
    holder_free_sql,
)
from sqlalchemy import text

_log = get_logger("gateway.projects.claim_gate")

#: The code of the refusal. The MCP edge maps it to a text of its own
#: (``agent_edge.tools.land_your_pr_refusal``), and its copy
#: ``agent_edge.tools.CLAIM_LAND_YOUR_PR`` has a pin test.
LAND_YOUR_PR = "land_your_pr"
#: The message of the 422. The edge shows its own text.
REFUSED_TEXT = (
    "Land your open pull requests before you claim new work. Merge main into"
    " each one, verify it and queue it, then mark it with landing queued.")
#: A claim is refused for a PR older than this.
GATE_AGE_HOURS = 48
#: ``my_work`` lists a PR older than this.
LIST_AGE_HOURS = 24
#: A ``queued`` mark counts for this long. After it, the PR counts again.
QUEUED_TTL_HOURS = 24
#: The most PRs that a refusal names, and the most rows of the read.
PRS_MAX = 5
#: The ``next`` codes of a PR: a person answered on its task, or land it.
NEXT_ANSWER = "answer"
NEXT_LAND = "land"
#: The marks that ``add_task_reference`` takes (migration
#: ``pm_task_refs_landing_state_known``).
LANDING_STATES: tuple[str, ...] = ("queued", "parked", "gone")
#: The tag that exempts a task, compared with no case (migration 156).
LANDING_TAG = "landing"
#: The only shape of a URL that leaves in a refusal or a row.
PR_URL_RE = re.compile(r"^https://github\.com/[^/]+/[^/]+/pull/[0-9]+$")
#: The number at the end of a pull request ``ref`` (``owner/repo#77``).
_PR_NUMBER_RE = re.compile(r"#([0-9]{1,10})$")


# ── The flags ───────────────────────────────────────────────────────────────


def _listed(raw: str, org: object) -> bool:
    org_id = str(org or "").strip().lower()
    if not org_id:
        return False
    entries = {part.strip().lower() for part in str(raw or "").split(",") if part.strip()}
    return "*" in entries or org_id in entries


def claim_gate_on(org: object) -> bool:
    """True when ``CLAIM_GATE_ORGS`` names organization *org*, or is ``*``.

    The ONE reader of the flag. Empty means off. An empty *org* and a fault
    of the settings read give False. The caller passes the org of the
    session, never a value from request input (R5)."""
    try:
        from acb_common import get_settings

        raw = str(getattr(get_settings(), "claim_gate_orgs", "") or "")
    except Exception:
        return False
    return _listed(raw, org)


def claim_gate_shadow_on(org: object) -> bool:
    """True when ``CLAIM_GATE_SHADOW_ORGS`` names organization *org*, or is
    ``*``. The ONE reader of the flag, by the rules of :func:`claim_gate_on`."""
    try:
        from acb_common import get_settings

        raw = str(getattr(get_settings(), "claim_gate_shadow_orgs", "") or "")
    except Exception:
        return False
    return _listed(raw, org)


# ── The SQL ─────────────────────────────────────────────────────────────────

#: Trap 2, items 1 and 2. The task holds an open pull request reference, or
#: it has the tag ``landing`` (no case). One statement, with no label.
_EXEMPT_TASK_SQL = (
    "SELECT ("
    " EXISTS (SELECT 1 FROM pm_task_refs f"
    f"         WHERE f.organization_id = {BOUND_ORG_SQL}"
    "            AND f.task_id = CAST(:tid AS uuid)"
    "            AND f.kind = 'pull_request' AND f.merged_at IS NULL"
    "            AND (f.state IS NULL OR f.state IN ('open', 'draft')))"
    " OR EXISTS (SELECT 1 FROM pm_tasks t"
    "             WHERE t.id = CAST(:tid AS uuid)"
    "               AND EXISTS (SELECT 1 FROM unnest(t.tags) AS tg"
    "                            WHERE lower(btrim(tg)) = :tag))"
    ") AS exempt"
)


def _counted_sql(vis: Any) -> str:
    """The counted PRs of the caller and the label, oldest first (§7b.2).

    The owner run of each reference (§7b.8) is one LATERAL read on the task
    index of the runs. The join is inner, so a reference with no owner run
    drops out. ``:me`` is the member in lower case, and ``:label`` the cleaned
    label or NULL. Each read of ``pm_agent_runs`` names ``task_id IS NOT
    NULL``, so an app run never counts (AD-16).

    §7b.10 (LBB-2c): the last ``NOT EXISTS`` drops a reference while an open
    run of another session (another member, or ``IS DISTINCT FROM`` the
    label) holds its task. It stays inline, because the label fence names
    this function as the one decision reader of the label."""
    open_list = ", ".join(f"'{s}'" for s in AGENT_RUN_OPEN_STATUSES)
    return (
        "SELECT f.id AS ref_id, f.ref, f.url, f.task_id, t.task_number,"
        " CAST(floor(extract(epoch FROM (now() - f.created_at)) / 3600) AS int)"
        "   AS age_hours,"
        " EXISTS (SELECT 1 FROM pm_agent_runs r"
        "          WHERE r.task_id IS NOT NULL AND r.task_id = f.task_id"
        f"           AND r.status = 'in_progress' AND {AGENT_RUN_ANSWER_WAITS_SQL})"
        "   AS answered"
        " FROM pm_task_refs f"
        " JOIN pm_tasks t ON t.id = f.task_id"
        " JOIN pm_task_statuses s ON s.id = t.status_id"
        " JOIN pm_projects p ON p.id = t.project_id"
        " JOIN LATERAL (SELECT n.member_email, n.agent_label FROM pm_agent_runs n"
        "                WHERE n.task_id IS NOT NULL AND n.task_id = f.task_id"
        "                  AND n.created_at <= f.created_at"
        "                  AND (n.ended_at IS NULL OR n.ended_at >= f.created_at)"
        "                ORDER BY n.created_at DESC, n.id DESC LIMIT 1) nr ON true"
        f" WHERE f.organization_id = {BOUND_ORG_SQL}"
        "   AND f.kind = 'pull_request'"
        "   AND (f.state IS NULL OR f.state IN ('open', 'draft'))"
        "   AND f.merged_at IS NULL"
        "   AND t.archived_at IS NULL AND p.archived_at IS NULL"
        "   AND s.category NOT IN ('done', 'cancelled')"
        "   AND f.created_at < now() - make_interval(hours => CAST(:age AS int))"
        "   AND lower(btrim(nr.member_email)) = :me"
        "   AND nr.agent_label IS NOT DISTINCT FROM CAST(:label AS text)"
        "   AND (f.landing_state IS NULL"
        "        OR (f.landing_state = 'queued'"
        "            AND (f.landing_at IS NULL"
        "                 OR f.landing_at < now() - make_interval(hours => CAST(:ttl AS int))))"
        "        OR (f.landing_state = 'parked'"
        "            AND s.category NOT IN ('backlog', 'todo')))"
        "   AND NOT EXISTS (SELECT 1 FROM pm_agent_runs w"
        "                    WHERE w.task_id IS NOT NULL AND w.task_id = f.task_id"
        "                      AND w.status = 'awaiting')"
        "   AND NOT EXISTS (SELECT 1 FROM pm_agent_runs o"
        "                    WHERE o.task_id IS NOT NULL AND o.task_id = f.task_id"
        f"                     AND o.status IN ({open_list})"
        f"                     AND NOT {holder_free_sql('o')}"
        "                      AND (lower(btrim(o.member_email)) IS DISTINCT FROM :me"
        "                           OR o.agent_label IS DISTINCT FROM CAST(:label AS text)))"
        f"  AND {task_visibility_clause(vis)}"
        " ORDER BY f.created_at ASC, f.id ASC"
        " LIMIT :n"
    )


# ── The reads ───────────────────────────────────────────────────────────────


def pr_number_of(ref: Any) -> int | None:
    """The number of a pull request ``ref`` (``owner/repo#77``), or None."""
    found = _PR_NUMBER_RE.search(str(ref or ""))
    return int(found.group(1)) if found else None


def shaped_url(url: Any) -> str | None:
    """*url* only when it has the shape of a GitHub pull request URL."""
    return url if isinstance(url, str) and PR_URL_RE.fullmatch(url) else None


def _entry(row: Any) -> dict[str, Any]:
    """One PR of a refusal or a row: numbers, an id and a fixed code only.
    The URL only in the shape of :data:`PR_URL_RE`."""
    entry: dict[str, Any] = {
        "pr_number": pr_number_of(row.ref),
        "age_hours": int(row.age_hours or 0),
        "task_id": str(row.task_id),
        "task_number": int(row.task_number) if row.task_number is not None else None,
        "next": NEXT_ANSWER if row.answered else NEXT_LAND,
    }
    url = shaped_url(row.url)
    if url is not None:
        entry["url"] = url
    return entry


async def counted_prs(db: Any, user: Any, vis: Any, label: str | None, *,
                      min_age_hours: int, limit: int = PRS_MAX) -> list[dict[str, Any]]:
    """The counted PRs of the session member and *label*, oldest first.

    *label* is the cleaned label of the call (``agent_runs.agent_label_of``),
    or None. The member and the tenant come from the session (R5)."""
    me = actor(user).strip().lower()
    if me in ("", "anonymous"):
        return []
    found = (await db.execute(text(_counted_sql(vis)), {
        "me": me, "label": label, "age": int(min_age_hours),
        "ttl": QUEUED_TTL_HOURS, "n": int(limit), **vis.params,
    })).fetchall()
    return [_entry(r) for r in found]


async def _task_exempt(db: Any, task_id: str) -> bool:
    found = (await db.execute(text(_EXEMPT_TASK_SQL),
                              {"tid": str(task_id), "tag": LANDING_TAG})).fetchone()
    return bool(found is not None and found.exempt)


async def _own_open_run(db: Any, user: Any, task_id: str) -> bool:
    """Trap 2, item 3: the caller already has an open run on the task, by the
    one own-run read of ``agent_runs`` (``_OPEN_RUN_SQL``). A read only: no
    adoption and no lock. It reads no label."""
    from gateway.routes.projects import agent_runs

    try:
        via = agent_runs._agent_via(user)
    except HTTPException:
        return False
    if via is None:
        return False
    args = agent_runs._open_run_args(
        str(task_id), agent_runs._member(user), via,
        agent_runs._agent_connection(user), agent_runs._agent_session(user))
    return (await db.execute(text(agent_runs._OPEN_RUN_SQL), args)).fetchone() is not None


async def check_claim_gate(db: Any, user: Any, task_id: str, vis: Any,
                           label: str | None) -> None:
    """The gate of an agent's hold of *task_id*, before any write (§7b.2).

    The ONE check of three doors: ``claim_task``, an agent's
    ``set_assignees`` that adds its own member, and an agent's
    ``start_agent_run``. *label* is the cleaned label of the call, or None.

    A person passes at once. With both flags off it returns before its first
    statement, so the doors read nothing new. In shadow it logs
    ``claim.gate would_refuse`` and lets the hold go. With
    ``CLAIM_GATE_ORGS`` on it raises the 422 ``land_your_pr`` through
    ``tasks._refuse_claim``, inside the transaction, so nothing is written.
    """
    if getattr(user, "agent", None) is None:
        return
    org = getattr(user, "organization_id", None)
    refuse = claim_gate_on(org)
    if not (refuse or claim_gate_shadow_on(org)):
        return
    if await _task_exempt(db, task_id) or await _own_open_run(db, user, task_id):
        return
    prs = await counted_prs(db, user, vis, label, min_age_hours=GATE_AGE_HOURS)
    if not prs:
        return
    numbers = [p["pr_number"] for p in prs]
    if not refuse:
        _log.info("claim.gate would_refuse", task_id=str(task_id), prs=numbers,
                  labelled=label is not None)
        return
    _log.info("claim.gate refused", task_id=str(task_id), prs=numbers,
              labelled=label is not None)
    from gateway.routes.projects.tasks import _refuse_claim

    _refuse_claim(LAND_YOUR_PR, REFUSED_TEXT, prs=prs)


# ── The route of ``my_work`` ────────────────────────────────────────────────


@router.get("/agent-work/landing")
async def agent_work_landing(
    agent_label: str | None = Query(default=None, max_length=200),
    user: UserContext = Depends(get_current_user),
) -> dict:
    """The counted PRs over :data:`LIST_AGE_HOURS` of the caller and the
    label, for the ``land_your_pr`` rows of ``my_work`` (§7b.2).

    One row for each task, the oldest PR first, at most :data:`PRS_MAX`. A
    row is the task row with ``landing``: ``{pr_number, pr_url, age_hours,
    next}``. No rows while both flags are off for the org, and no rows for
    a person, whom the gate never binds."""
    from gateway.routes.projects import agent_runs

    org = getattr(user, "organization_id", None)
    if getattr(user, "agent", None) is None or not (
            claim_gate_on(org) or claim_gate_shadow_on(org)):
        return {"rows": []}
    label = agent_runs.clean_agent_label(agent_label)
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        prs = await counted_prs(db, user, vis, label, min_age_hours=LIST_AGE_HOURS,
                                limit=PRS_MAX * 4)
        first: dict[str, dict[str, Any]] = {}
        for pr in prs:
            first.setdefault(pr["task_id"], pr)
        picked = list(first.values())[:PRS_MAX]
        if not picked:
            return {"rows": []}
        found = (await db.execute(
            text("SELECT t.* FROM pm_tasks t WHERE t.id = ANY(CAST(:ids AS uuid[]))"
                 f" AND {task_visibility_clause(vis)}"),
            {"ids": [p["task_id"] for p in picked], **vis.params},
        )).fetchall()
    tasks = {str(r.id): row_to_dict(r, TaskModel) for r in found}
    rows = []
    for pr in picked:
        task = tasks.get(pr["task_id"])
        if task is None:
            continue
        task["landing"] = {"pr_number": pr["pr_number"], "pr_url": pr.get("url"),
                           "age_hours": pr["age_hours"], "next": pr["next"]}
        rows.append(task)
    return {"rows": rows}
