"""Projects · the search surface (WS-27r).

Spec: ``project-docs/specs/project_management_app.md`` §11.2 item 10, §11.18.

    GET /projects/search?q=parser   → ranked tasks, across every visible project

*"`?q=` exists on the list endpoint; there is no search surface."*

**Why this is not `GET /tasks?q=` with a nicer client.** The list endpoint
answers *"which tasks match these filters, in this order, on this page"*. Search
answers *"what did you mean"*, and the difference is not cosmetic:

* **it ranks.** A task whose TITLE starts with the term is a better answer than
  one that mentions it in paragraph four of its description. The list's
  ordering is an allowlist of columns (`TASK_SORTS`) and deliberately cannot
  express relevance — adding a `sort=relevance` would be a sort key that only
  works when `q` is present, which is a worse contract than a second endpoint.
* **it is capped, not paged.** Nobody pages through search results; they retype.
  A `LIMIT` with no `OFFSET` is the honest shape, and it keeps the ranking
  meaningful — page 2 of a relevance ordering is where relevance has run out.
* **it names the project.** A hit is useless without knowing where it lives,
  and the list endpoint returns `project_id` because its caller already has the
  tree. A search palette does not.

Everything that decides *what a caller may see* is still shared:
`task_visibility_clause` and the same archived rule, so search can never
surface a task the list would not.

**`#123` is a task number, not a phrase.** A workspace numbers its tasks and
people quote those numbers; searching for `#42` and getting every task whose
description contains "42" is a search box that has ignored what you typed.

**LIKE metacharacters are escaped** — see `like_escape`. That was a live defect
on the list endpoint too, not a new-code precaution.

**Pickers pass `?exclude_relatives_of=<task_id>`** (WS-27w item 5). A parent/
relation/duplicate picker anchored on a task must not offer the task itself,
its ancestors, its descendants, or anything already related in either
direction — every one of those is a choice the write path will 422, and a
picker that offers refusals teaches people the picker is broken. The write-time
guards stay; this is their read-side twin.

**Comments are deliberately not searched.** They are the largest text in the
system and the least likely to be what somebody is looking for by name; a
comment hit would also have to be rendered as its task, which makes ranking
across the two incomparable. Recorded so the absence reads as a decision.
"""

from __future__ import annotations

from typing import Any

from acb_auth import UserContext, get_current_user
from fastapi import Depends
from gateway.routes.projects.core import (
    MAX_DEPTH,
    _tenant_session,
    load_visible_task,
    resolve_visibility,
    router,
    task_visibility_clause,
    triage_exclusion_clause,
    wire,
)
from gateway.routes.projects.filters import (
    MIN_QUERY,
    attach_parent_context,
    like_escape,
)
from sqlalchemy import text

# `MIN_QUERY` — the shortest text query either search surface will run — is
# imported above and RE-EXPORTED here (`__all__`), not defined here (WS-27be).
# Shorter than it and every query is a table scan returning half the workspace;
# both surfaces answer an EMPTY result rather than a 422, because a search box
# types one character on the way to typing three and an error flashing in a
# palette on every keystroke is noise the user cannot act on.
#
# It moved to `filters` because `filters.build_task_filters` now enforces the
# same threshold on the list endpoint's `?q=`, and the two must agree by
# construction rather than by two people remembering. `filters` is the module
# this one already imports, so the dependency arrow only points one way — and
# the threshold's own rationale, including the trigram-length caveat, is written
# beside the constant there.

#: The most hits one search returns. Not a page — see the module docstring.
MAX_HITS = 50


def task_number(raw: str) -> int | None:
    """``#42`` or ``42`` → 42, else ``None``.

    Bounded to what the column can hold: `task_number` is a BIGINT, and a
    forty-digit "number" is a phrase somebody typed, not a lookup. Without the
    bound it would also be an unbounded integer parse on user input.
    """
    stripped = raw.strip().lstrip("#").strip()
    if not stripped.isdigit() or len(stripped) > 18:
        return None
    return int(stripped)


#: Ranked in SQL, because the ordering has to be applied BEFORE the limit.
#:
#: Ranking in Python over a capped set would sort whichever fifty rows the
#: database happened to return — so the best answer is only in the list if it
#: was already in the arbitrary fifty, which is the kind of bug that looks like
#: "search is bad at long queries" rather than like a defect.
#:
#: The four tiers, and why each earns its place above the next:
#:   0  the task number, typed exactly. Unambiguous, and never more than one.
#:   1  the title STARTS with the term — what somebody typing a name means.
#:   2  the title contains it.
#:   3  only the description contains it.
#: Ties break on recency, then on id so the order is total and a repeated
#: search does not reshuffle.
#:
#: ⚠️ `:number` is CAST explicitly. Without it Postgres has nothing to infer the
#: type from — `$1 IS NOT NULL` names no column — and asyncpg answers
#: `AmbiguousParameterError: could not determine data type of parameter $1`.
#: Every hermetic test passed with the cast missing, because a Python fake has
#: no type system to be ambiguous about; only the live run found it.
_SEARCH_SQL = """
SELECT t.id, t.title, t.task_number, t.project_id, t.status_id, t.due_at,
       t.completed_at, t.parent_task_id, p.name AS project_name, s.name AS status_name,
       s.category,
       CASE
         WHEN CAST(:number AS bigint) IS NOT NULL
              AND t.task_number = CAST(:number AS bigint) THEN 0
         WHEN t.title ILIKE :prefix THEN 1
         WHEN t.title ILIKE :term THEN 2
         ELSE 3
       END AS rank
  FROM pm_tasks t
  JOIN pm_projects p ON p.id = t.project_id
  JOIN pm_task_statuses s ON s.id = t.status_id
 WHERE {visible}
   AND {triage}
   AND t.archived_at IS NULL{exclude}
   AND (t.title ILIKE :term
        OR t.description ILIKE :term
        OR (CAST(:number AS bigint) IS NOT NULL
            AND t.task_number = CAST(:number AS bigint)))
 ORDER BY rank, t.updated_at DESC, t.id
 LIMIT :cap
"""


#: The clause `exclude_relatives_of` adds (WS-27w item 5, P-7). A fragment
#: rather than a second statement, so everything else about search — ranking,
#: visibility, the archived rule, the cap — is exactly the same query.
_EXCLUDE_SQL = "\n   AND NOT (t.id = ANY(CAST(:excluded AS uuid[])))"


async def relative_task_ids(db: Any, task_id: str) -> set[str]:
    """Every task a picker anchored on ``task_id`` must not offer.

    Four classes, matching the write-time guards one for one: the task itself,
    its ancestors, its descendants (all three are what `assert_no_task_cycle`
    would 422), and anything already related in EITHER direction (what the
    link upsert would merely re-assert). The write-time guards stay untouched
    — this is their read-side twin, so a relation/duplicate/parent picker
    cannot offer a choice the write path will refuse.

    Walks are bounded by :data:`MAX_DEPTH` for the cycle checks' reason: an
    unbounded walk over data somebody can create is a denial-of-service
    surface, and a real hierarchy never approaches the bound.
    """
    anchor = str(task_id)
    out: set[str] = {anchor}

    # Ancestors — one hop at a time, the same walk `assert_no_task_cycle` does.
    current = anchor
    for _ in range(MAX_DEPTH):
        row = (await db.execute(
            text("SELECT parent_task_id FROM pm_tasks WHERE id = CAST(:id AS uuid)"),
            {"id": current},
        )).fetchone()
        parent = getattr(row, "parent_task_id", None) if row is not None else None
        if parent is None or str(parent) in out:
            break
        out.add(str(parent))
        current = str(parent)

    # Descendants — frontier by frontier, the `assert_no_block_cycle` shape.
    frontier = {anchor}
    for _ in range(MAX_DEPTH):
        if not frontier:
            break
        rows = (await db.execute(
            text(
                "SELECT id FROM pm_tasks "
                "WHERE parent_task_id = ANY(CAST(:ids AS uuid[]))"
            ),
            {"ids": sorted(frontier)},
        )).fetchall()
        frontier = {str(r.id) for r in rows} - out
        out |= frontier

    # Already related, both directions — two single-column reads rather than
    # one OR, because a link is readable from either side and the picker must
    # exclude it from either side too.
    for column, other in (
        ("source_task_id", "target_task_id"),
        ("target_task_id", "source_task_id"),
    ):
        rows = (await db.execute(
            text(
                f"SELECT * FROM pm_task_links "
                f"WHERE {column} = CAST(:tid AS uuid)"
            ),
            {"tid": anchor},
        )).fetchall()
        out |= {
            str(getattr(r, other)) for r in rows
            if getattr(r, other, None) is not None
        }
    return out


@router.get("/search")
async def search_tasks(
    q: str = "",
    limit: int = MAX_HITS,
    exclude_relatives_of: str | None = None,
    # WS-27u. Search reaches every task in the app, so it is the surface where
    # leaking the intake queue costs most — and the surface the duplicate
    # picker needs the flag on, since "is this already captured" is a question
    # about triage-parked tasks specifically. One predicate, in core.
    include_triage: bool = False,
    user: UserContext = Depends(get_current_user),
) -> dict:
    """Ranked task hits across every project the caller can see.

    Global by default and by design: the point of a search surface is finding
    the thing you cannot navigate to, and scoping it to the selected project
    would make it a filter with a different name.
    """
    term = q.strip()
    if len(term) < MIN_QUERY:
        return {"rows": [], "total": 0, "truncated": False, "query": term}

    cap = max(1, min(int(limit), MAX_HITS))
    escaped = like_escape(term)

    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        exclude_sql = ""
        exclude_params: dict[str, Any] = {}
        if exclude_relatives_of:
            # The anchor must be visible to the caller — an unreadable id is a
            # 404 (R5), exactly as it is on the list endpoint's project filter,
            # so the parameter cannot be used to probe for existence.
            await load_visible_task(db, vis, exclude_relatives_of)
            exclude_sql = _EXCLUDE_SQL
            exclude_params["excluded"] = sorted(
                await relative_task_ids(db, exclude_relatives_of)
            )
        rows = (await db.execute(
            text(_SEARCH_SQL.format(
                visible=task_visibility_clause(vis),
                triage="TRUE" if include_triage else triage_exclusion_clause(),
                exclude=exclude_sql,
            )),
            {
                **vis.params,
                **exclude_params,
                "term": f"%{escaped}%",
                "prefix": f"{escaped}%",
                "number": task_number(term),
                # One more than the cap, so "there are more" is a fact rather
                # than a guess — the WS-27q lesson: silence about truncation is
                # the only unacceptable answer.
                "cap": cap + 1,
            },
        )).fetchall()

        hits = [
            {
                "id": str(r.id),
                "title": r.title,
                "task_number": r.task_number,
                "project_id": str(r.project_id),
                "project_name": r.project_name,
                "status_id": str(r.status_id),
                "status_name": r.status_name,
                "category": r.category,
                "due_at": wire(r.due_at),
                "completed_at": wire(r.completed_at),
                "rank": int(r.rank),
                "parent_task_id": (
                    str(r.parent_task_id)
                    if getattr(r, "parent_task_id", None) else None
                ),
            }
            for r in rows[:cap]
        ]
        # D-PM-38. Search always shows subtasks, so each hit names its parent,
        # under the reader's grants.
        await attach_parent_context(db, vis, hits)
        return {
            "rows": hits,
            "total": len(hits),
            "truncated": len(rows) > cap,
            "query": term,
        }


__all__ = ["MAX_HITS", "MIN_QUERY", "relative_task_ids", "task_number"]
