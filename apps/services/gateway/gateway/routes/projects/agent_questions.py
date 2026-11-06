"""Projects · Questions for me — WS-55 EA-18a (Metorite task 1806).

Spec: ``project-docs/specs/external_agent_access.md`` §12.8 (D-EA-14).

    GET /projects/agent-questions?scope=&project=&sort=&offset=&limit=
        → {rows, counts, total, offset, limit, capped}

One list of the agent questions that wait on a person. EA-18b and EA-18c show
it in Projects, My Day and Approvals. No door keeps a copy.

⚠️ **Each rule here is a rule that already exists, joined.** The scope is
``analytics.scope_clause``, so a project the reader cannot see answers 404.
"Awaiting" is ``filters.AGENT_RUN_STATE_SQL``. "An open question" is
``agent_runs._OPEN_ELICITATIONS_SQL``, run as it is. Who may answer is
``agent_runs.person_may_answer_sql``, the text that the write route reads, and
the role test of ``agent_runs.holds_answer_role``. The path is
``core.visible_project_paths``.

⚠️ **Two kinds of question are narrowed** (``agent_runs.listed_question_sql``).
A question on a closed task shows to nobody: it is a ``dangling`` row of Needs
attention, and the sweep closes it. A typed archive request shows to the run's
member only, with ``why`` ``my_agent``. Only that member approves it, through
``archive-approval`` (§12.1). "Waiting on me" reads the same text.

⚠️ **Tenancy (R5).** The caller's tenant session runs each query, and each query
carries ``task_visibility_clause``. No parameter names an organization or a
person: the address, the roles and the org come from the session.

⚠️ **It only reads.** It sends no bell and writes nothing.

**The flags.** With ``AGENT_QUESTIONS_ORGS`` off for the org, the route answers
404. With ``agent_runs_enabled`` off, the list is empty, as ``needs_attention.py``
reads its run kinds.
"""

from __future__ import annotations

from typing import Any, Literal

from acb_auth import UserContext, agent_oauth, get_current_user
from fastapi import Depends, HTTPException, Query
from gateway.routes.projects.agent_runs import (
    _OPEN_ELICITATIONS_SQL,
    _member,
    _parse_uuid,
    answer_reason_sql,
    archive_request_of,
    holds_answer_role,
    listed_question_sql,
    person_may_answer_sql,
)
from gateway.routes.projects.analytics import scope_clause, scope_params
from gateway.routes.projects.core import (
    _tenant_session,
    from_jsonb,
    resolve_visibility,
    router,
    task_visibility_clause,
    visible_project_paths,
    wire,
)
from gateway.routes.projects.filters import (
    AGENT_RUN_STATE_SQL,
    agent_questions_allowed,
    agent_runs_enabled,
)
from sqlalchemy import text

#: The most rows of one page, and the page when the caller names none (§12.8).
MAX_LIMIT = 200
DEFAULT_LIMIT = 100

#: ``question.kind`` of a row. ``archive_request_of`` on the server decides it,
#: so the browser never derives it. EA-18b sends Approve and Decline of an
#: ``archive_request`` row to ``archive-approval``, never to ``/activities``,
#: which answers 422 for a typed request.
KIND_QUESTION = "question"
KIND_ARCHIVE_REQUEST = "archive_request"

#: The 403 of ``scope=everyone`` for a reader outside the review set.
EVERYONE_REFUSED = (
    "Only an owner, an admin or a manager can see every question. Open For me to "
    "see yours."
)


async def require_agent_questions(user: UserContext = Depends(get_current_user)) -> None:
    """404 while ``AGENT_QUESTIONS_ORGS`` does not hold the caller's org."""
    if not agent_questions_allowed(getattr(user, "organization_id", None)):
        raise HTTPException(status_code=404, detail="Not Found")


def questions_sql(scope_sql: str, vis: Any) -> str:
    """Each run in ``awaiting`` on a task of the scope that the reader sees, with
    ``why`` and the answer set for the reader. It binds ``:me`` and the
    visibility and scope parameters.

    ``agent_runs.listed_question_sql`` leaves out a closed task, and it shows
    a typed archive request to the run's member only. "Waiting on me" reads
    the same text.

    ``r.status = 'awaiting'`` comes before the state test, so the partial index
    ``pm_agent_runs_one_open_per_task`` serves the read. The CASE of
    ``AGENT_RUN_STATE_SQL`` alone hides the status from the planner. For
    ``awaiting`` the CASE answers ``r.status``, so both select the same rows.

    The open question of each run comes from
    ``agent_runs._OPEN_ELICITATIONS_SQL``, in a second query over these run ids,
    so the rule of an open question has one text.
    """
    return (
        f"SELECT r.id AS run_id, r.client_via,"
        f"       COALESCE(r.started_at, r.created_at) AS run_started_at,"
        f"       t.id AS task_id, t.task_number, t.title AS task_title, t.project_id,"
        f"       {answer_reason_sql('r')} AS why,"
        f"       {person_may_answer_sql('r')} AS may_answer"
        f"  FROM pm_agent_runs r"
        f"  JOIN pm_tasks t ON t.id = r.task_id"
        f" WHERE r.task_id IS NOT NULL"
        f"   AND r.status = 'awaiting' AND ({AGENT_RUN_STATE_SQL}) = 'awaiting'"
        f"   AND {listed_question_sql('r')}"
        f"   AND ({scope_sql})"
        f"   AND ({task_visibility_clause(vis, 't')})"
    )


#: The head of each open question: enough to sort and to count. It reads
#: ``_OPEN_ELICITATIONS_SQL`` as it is. A count, as the sidebar badge reads it
#: every 60 seconds, then holds no question text in memory.
_QUESTION_HEADS_SQL = (
    "SELECT q.id, q.run_id, q.created_at"
    f" FROM ({_OPEN_ELICITATIONS_SQL}) q"
)


async def read_questions(
    db: Any, vis: Any, me: str, scope_sql: str, params: dict[str, Any],
) -> list[tuple[Any, Any]]:
    """``(run, head of its question)`` for each open question the reader can
    see, whoever may answer it. The route narrows this to "For me". Oldest
    first, with the run id as the tie-break."""
    runs = (await db.execute(
        text(questions_sql(scope_sql, vis)), {**params, **vis.params, "me": me},
    )).fetchall()
    if not runs:
        return []
    heads = {
        str(q.run_id): q
        for q in (await db.execute(
            text(_QUESTION_HEADS_SQL), {"ids": [str(r.run_id) for r in runs]},
        )).fetchall()
    }
    found = [(run, heads[str(run.run_id)]) for run in runs if str(run.run_id) in heads]
    found.sort(key=lambda pair: (pair[1].created_at, str(pair[0].run_id)))
    return found


def question_row(
    run: Any, question: Any, path: list[dict[str, str]], *, review: bool,
) -> dict[str, Any]:
    """The wire shape of one row. *question* is the whole activity row. *review*
    is the role test, folded into ``can_answer`` here and nowhere else. So a
    reader outside the answer set reads false, and the write route refuses them
    too. The route gives such a reader no row at all today: "For me" leaves them
    out, and "Everyone" answers 403."""
    via = run.client_via
    meta = from_jsonb(question.signal_metadata) or {}
    return {
        "run_id": str(run.run_id),
        "activity_id": str(question.id),
        "task_id": str(run.task_id),
        "task_number": int(run.task_number) if run.task_number is not None else None,
        "task_title": run.task_title,
        "agent_name": agent_oauth.client_label(via) if via else None,
        "client_via": via,
        "asked_at": wire(question.created_at),
        "run_started_at": wire(run.run_started_at),
        "project_path": path,
        "question": {
            "kind": (KIND_ARCHIVE_REQUEST if archive_request_of(question) is not None
                     else KIND_QUESTION),
            "body": (from_jsonb(question.content) or {}).get("body"),
            "options": meta.get("options") or [],
            "allow_multiple": bool(meta.get("allow_multiple")),
        },
        "can_answer": bool(review or run.may_answer),
        "why": run.why,
    }


async def question_rows(
    db: Any, vis: Any, page: list[tuple[Any, Any]], *, review: bool,
) -> list[dict[str, Any]]:
    """The rows of one page: the whole question and the project path of each.
    A question answered between the two reads leaves the page."""
    if not page:
        return []
    whole = {
        str(q.run_id): q
        for q in (await db.execute(
            text(_OPEN_ELICITATIONS_SQL), {"ids": [str(run.run_id) for run, _ in page]},
        )).fetchall()
    }
    paths = await visible_project_paths(db, vis, [run.project_id for run, _ in page])
    return [
        question_row(run, whole[str(run.run_id)], paths.get(str(run.project_id), []),
                     review=review)
        for run, _ in page if str(run.run_id) in whole
    ]


@router.get("/agent-questions", dependencies=[Depends(require_agent_questions)])
async def list_agent_questions(
    scope: Literal["mine", "everyone"] = "mine",
    project: str | None = None,
    sort: Literal["oldest", "newest"] = "oldest",
    offset: int = Query(0, ge=0),
    limit: int = Query(DEFAULT_LIMIT, ge=0, le=MAX_LIMIT),
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """The open agent questions for the reader (spec §12.8).

    ``mine`` lists a question when the reader created the task, is an assignee,
    or is the run's member. ``everyone`` lists each question the reader can see,
    for a reader with the role owner, admin or manager, and gives anybody else
    403. ``project`` keeps one project and its subprojects, and a project the
    reader cannot see answers 404. ``counts`` and ``total`` are exact.
    ``capped`` is true when rows exist past this page.
    """
    review = holds_answer_role(user)
    if scope == "everyone" and not review:
        raise HTTPException(status_code=403, detail=EVERYONE_REFUSED)
    project_id = None if project is None else _parse_uuid(project, "Project")
    every: list[tuple[Any, Any]] = []
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        scope_sql = await scope_clause(db, vis, project_id, True)
        if agent_runs_enabled():
            every = await read_questions(
                db, vis, _member(user), scope_sql, scope_params(project_id),
            )
        mine = [pair for pair in every if pair[0].why is not None]
        chosen = every if scope == "everyone" else mine
        ordered = list(reversed(chosen)) if sort == "newest" else chosen
        page = ordered[offset:offset + limit]
        rows = await question_rows(db, vis, page, review=review)
    return {
        "rows": rows,
        "counts": {"mine": len(mine), "everyone": len(every) if review else None},
        "total": len(chosen),
        "offset": offset,
        "limit": limit,
        "capped": offset + len(page) < len(chosen),
    }


__all__ = [
    "DEFAULT_LIMIT",
    "EVERYONE_REFUSED",
    "KIND_ARCHIVE_REQUEST",
    "KIND_QUESTION",
    "MAX_LIMIT",
    "list_agent_questions",
    "question_row",
    "question_rows",
    "questions_sql",
    "read_questions",
    "require_agent_questions",
]
