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
Since D-PM-31 was built (2026-10-06), a query that is only a number is an
EXACT lookup, and it passes at any length. Every other query needs
`MIN_QUERY` characters, or the route answers 422.

**LIKE metacharacters are escaped** — see `like_escape`. That was a live defect
on the list endpoint too, not a new-code precaution.

**Pickers pass `?exclude_relatives_of=<task_id>`** (WS-27w item 5). A parent/
relation/duplicate picker anchored on a task must not offer the task itself,
its ancestors, its descendants, or anything already related in either
direction — every one of those is a choice the write path will 422, and a
picker that offers refusals teaches people the picker is broken. The write-time
guards stay; this is their read-side twin.

**The description is searched, by WORD, and a description hit says why**
(owner request, 2026-10-13). The Projects chat told the owner that a
description "is not in that index at all" and offered to open 120 tasks one
at a time. The route matched descriptions all along. Two gaps made the claim
look true: the query was ONE substring, so words that sit apart never matched,
and a hit carried no text, so nothing showed why it came back. Now every word
must appear in the title or the description (``filters.text_match_clause``),
and a description hit carries ``snippet``: at most :data:`SNIPPET_CHARS`
characters around the first word it holds.

**Comments are deliberately not searched.** They are the largest text in the
system and the least likely to be what somebody is looking for by name; a
comment hit would also have to be rendered as its task, which makes ranking
across the two incomparable. Recorded so the absence reads as a decision.
"""

from __future__ import annotations

import uuid
from typing import Any

from acb_auth import UserContext, get_current_user
from fastapi import Depends, HTTPException
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
    clean_query,
    like_escape,
    query_words,
    short_query_message,
    short_text_query,
    task_number,
    text_match_clause,
)
from sqlalchemy import text

# `MIN_QUERY` — the shortest text query either search surface will run — is
# imported above and RE-EXPORTED here (`__all__`), not defined here (WS-27be).
# So is `task_number`, because the list endpoint's `?q=` now asks it too.
#
# D-PM-31 (built 2026-10-06): a TEXT query shorter than `MIN_QUERY` is a 422
# here, with a sentence that says what to type. The palette and the duplicate
# picker gate on the same rule before they call (`app/projects/lib/search.ts`),
# so the 422 reaches only a caller that skipped the gate — the chat tool once
# did, and an empty answer told it "no task matches", which was false. The
# list endpoint still answers EMPTY for a short `?q=`, because a filter box
# types one character on the way to three.
#
# It moved to `filters` because `filters.build_task_filters` now enforces the
# same threshold on the list endpoint's `?q=`, and the two must agree by
# construction rather than by two people remembering. `filters` is the module
# this one already imports, so the dependency arrow only points one way — and
# the threshold's own rationale, including the trigram-length caveat, is written
# beside the constant there.

#: The most hits one search returns. Not a page — see the module docstring.
MAX_HITS = 50


#: Ranked in SQL, because the ordering has to be applied BEFORE the limit.
#:
#: Ranking in Python over a capped set would sort whichever fifty rows the
#: database happened to return — so the best answer is only in the list if it
#: was already in the arbitrary fifty, which is the kind of bug that looks like
#: "search is bad at long queries" rather than like a defect.
#:
#: The six tiers, and why each earns its place above the next:
#:   0  the task number, typed exactly. Unambiguous, and never more than one.
#:   1  the title STARTS with the whole query — what somebody typing a name means.
#:   2  the title contains the whole query.
#:   3  the title holds every word, apart.
#:   4  the description contains the whole query.
#:   5  every word is found, across the title and the description.
#: Ties break on recency, then on id so the order is total and a repeated
#: search does not reshuffle. Tiers 0-3 are TITLE matches, 4-5 are DESCRIPTION
#: matches (:data:`DESCRIPTION_RANK`), and only those carry a snippet.
#:
#: **Two steps, and the snippet is the second** (owner request, 2026-10-13).
#: ``hits`` ranks and caps. The outer select then finds, for each of the at
#: most 51 hits, where its description holds the query (or the first word it
#: holds) and cuts a window there. The window is cut in SQL, so a long
#: description never leaves the database. ``strpos(lower(…))`` and not the
#: ILIKE: a position is needed, and the needles are the raw words, not the
#: escaped patterns.
#:
#: ⚠️ `:number` is CAST explicitly. Without it Postgres has nothing to infer the
#: type from — `$1 IS NOT NULL` names no column — and asyncpg answers
#: `AmbiguousParameterError: could not determine data type of parameter $1`.
#: Every hermetic test passed with the cast missing, because a Python fake has
#: no type system to be ambiguous about; only the live run found it.
#:
#: ``{match}`` is one of the two arms below. A query that is only a task number
#: (`#7`, `7`) gets the EXACT arm, at any length (D-PM-31's exception): it is
#: an equality on `idx_pm_tasks_task_number`, and it never runs the text scan.
#: Every other query gets the TEXT arm.
_SEARCH_SQL = """
WITH hits AS (
SELECT t.id, t.title, t.task_number, t.project_id, t.status_id, t.due_at,
       t.completed_at, t.parent_task_id, t.updated_at, t.description,
       p.name AS project_name, s.name AS status_name, s.category,
       CASE
         WHEN CAST(:number AS bigint) IS NOT NULL
              AND t.task_number = CAST(:number AS bigint) THEN 0
         WHEN t.title ILIKE :prefix THEN 1
         WHEN t.title ILIKE :term THEN 2
         WHEN {title_words} THEN 3
         WHEN t.description ILIKE :term THEN 4
         ELSE 5
       END AS rank
  FROM pm_tasks t
  JOIN pm_projects p ON p.id = t.project_id
  JOIN pm_task_statuses s ON s.id = t.status_id
 WHERE {visible}
   AND {triage}
   AND t.archived_at IS NULL{exclude}
   AND {match}
 ORDER BY rank, t.updated_at DESC, t.id
 LIMIT :cap
)
SELECT h.id, h.title, h.task_number, h.project_id, h.status_id, h.due_at,
       h.completed_at, h.parent_task_id, h.project_name, h.status_name,
       h.category, h.rank,
       greatest(n.at - :snippet_lead, 1) AS snippet_from,
       CASE WHEN n.at IS NOT NULL
            THEN substr(h.description, greatest(n.at - :snippet_lead, 1),
                        :snippet_window)
       END AS snippet_raw,
       length(h.description) AS description_length
  FROM hits h
  LEFT JOIN LATERAL (
    SELECT strpos(lower(h.description), lower(x.needle)) AS at
      FROM unnest(CAST(:needles AS text[])) WITH ORDINALITY AS x(needle, ord)
     WHERE h.rank >= :description_rank
       AND strpos(lower(h.description), lower(x.needle)) > 0
     ORDER BY x.ord
     LIMIT 1
  ) n ON TRUE
 ORDER BY h.rank, h.updated_at DESC, h.id
"""

#: The arm for a task-number query: an exact match, served by an index.
#: A text query's arm is ``filters.text_match_clause``: every word, in the
#: title or the description, the same predicate the list endpoint runs.
_NUMBER_MATCH = "t.task_number = CAST(:number AS bigint)"

#: The first rank that is a description match. Below it the title explains
#: the hit, so no snippet is cut.
DESCRIPTION_RANK = 4

#: The most characters a snippet carries, ellipses included. Small on
#: purpose: it says WHY a task matched, and ``task_detail`` reads the rest.
SNIPPET_CHARS = 160
#: How many characters of context come before the hit.
SNIPPET_LEAD = 50
#: The window SQL cuts. Whitespace collapses after the cut, so the window is
#: wider than the cap, and :func:`snippet_of` trims to the cap.
SNIPPET_WINDOW = SNIPPET_CHARS + 80


def _title_words_clause(words: list[str]) -> str:
    """Tier 3: the title alone holds every word."""
    return "(" + " AND ".join(
        f"t.title ILIKE :q_w{i}" for i in range(len(words))
    ) + ")"


def snippet_of(
    raw: str | None, *, starts_inside: bool, ends_inside: bool,
) -> str | None:
    """A window of description text → one plain line of at most
    :data:`SNIPPET_CHARS` characters, with an ellipsis on each cut side.

    Plain text, never markup: whitespace (newlines, tabs, control spacing)
    collapses to single spaces, so a snippet is one line and a reader that
    parses lines cannot be handed a forged row. The route returns it as a
    JSON string, and the chat tool fences it in «guillemets».

    ``starts_inside`` / ``ends_inside`` say the window was cut from the
    middle of the description. A cut drops the half word at that edge, when
    a space is close enough to cut at.

    ⚠️ The front half word is dropped only when the window STARTS on a
    non-space character, which is the only way it can start inside a word.
    A window that starts on whitespace has no half word, and its first token
    can be the hit itself: 50 spaces of indentation before "approval"
    collapse to "approval …", and dropping the first token removed the very
    word that matched (review of #941).
    """
    text_ = " ".join((raw or "").split())
    if not text_:
        return None
    if starts_inside and not (raw or " ")[0].isspace():
        space = text_.find(" ")
        if 0 <= space < 20:
            text_ = text_[space + 1:]
    head = "…" if starts_inside else ""
    budget = SNIPPET_CHARS - len(head)
    cut = ends_inside or len(text_) > budget
    if cut:
        budget -= 1
        if len(text_) > budget:
            text_ = text_[:budget]
            space = text_.rfind(" ")
            if space > budget - 20:
                text_ = text_[:space]
        text_ = text_.rstrip()
    return head + text_ + ("…" if cut else "")


def _snippet_for(row: Any) -> str | None:
    """The snippet of one hit, or ``None`` when the title explains the hit
    or the description held no needle (``snippet_raw`` is NULL)."""
    if int(getattr(row, "rank", 0)) < DESCRIPTION_RANK:
        return None
    raw = getattr(row, "snippet_raw", None)
    if not raw:
        return None
    start = int(getattr(row, "snippet_from", 1) or 1)
    length = int(getattr(row, "description_length", 0) or 0)
    return snippet_of(
        raw,
        starts_inside=start > 1,
        ends_inside=start - 1 + len(raw) < length,
    )


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
    term = clean_query(q)
    if not term:
        return {"rows": [], "total": 0, "truncated": False, "query": term}
    if short_text_query(term):
        raise HTTPException(status_code=422, detail=short_query_message(term))
    number = task_number(term)

    cap = max(1, min(int(limit), MAX_HITS))
    escaped = like_escape(term)
    # The words bind for a number query too: the CASE reads them for tier 3,
    # and a number query never reaches that tier (tier 0 or no row at all).
    words = query_words(term)
    text_match, word_params = text_match_clause(term)

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
                match=text_match if number is None else _NUMBER_MATCH,
                title_words=_title_words_clause(words),
            )),
            {
                **vis.params,
                **exclude_params,
                **word_params,
                "term": f"%{escaped}%",
                "prefix": f"{escaped}%",
                "number": number,
                # The whole query first, then each word: the snippet centres
                # on the first of them the description holds.
                "needles": list(dict.fromkeys([term, *words])),
                "description_rank": DESCRIPTION_RANK,
                "snippet_lead": SNIPPET_LEAD,
                "snippet_window": SNIPPET_WINDOW,
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
                "snippet": _snippet_for(r),
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


# ── Task references: `#171` in a chat answer (owner report, 2026-10-13) ──────
#
# The chat draws a bare `#171` as the task pill when it is a task the member
# can see (`ChatTaskRef.tsx`, `lib/taskRefs.ts`). One answer can name
# a dozen numbers, so this read takes them all at once: one request per
# answer, never one per number.
#
# **Visibility is the search's own.** The same `task_visibility_clause` and
# the same triage rule, so a reference can never name a task that the search
# box would hide. A number the member cannot see returns nothing, and the chip
# stays text.
#
# **A number is unique per ROOT project, not per organization**
# (`pm_tasks UNIQUE (root_project_id, task_number)`), so two spaces can each
# hold a #5. The read returns up to `REF_HITS_PER_NUMBER` hits per number, open
# tasks first, so the client can tell one from two. It links only on one.
#
# Archived tasks are included: an answer can name finished work, and the task
# panel opens an archived task. They sort after the open ones.
#
# **The task's own root first** (WS-57 TR-5). The task card sends ``root``,
# the root project of the task it shows. For a number that a visible task in
# that root carries, the read returns only the tasks of that root. For any
# other number it returns what it returns with no root. So `#12` on a card
# names #12 of the same space, and a #12 elsewhere does not make it "two".
# The chat sends no root, and its answer does not change. The root narrows
# what the visibility clause already allows. It never widens it.

#: The most numbers one lookup takes. The client sends at most this many.
MAX_REFS = 50

#: The most hits one number returns. Two already say "not one"; the third
#: leaves room for an archived twin beside two open tasks.
REF_HITS_PER_NUMBER = 3

# `in_root` sorts first inside each number, so the cap of `:per` never cuts a
# task of the root in favour of one outside it. `has_root` is true for a
# number when one visible task of the root carries it, and then the outer
# WHERE keeps only those. With no root, `:root` is NULL, both are false and
# the read is the one it was before TR-5.
_REFS_SQL = """
SELECT id, title, task_number, status_name, category, archived
  FROM (
    SELECT t.id, t.title, t.task_number, s.name AS status_name, s.category,
           (t.archived_at IS NOT NULL) AS archived,
           COALESCE(t.root_project_id = CAST(:root AS uuid), false) AS in_root,
           bool_or(COALESCE(t.root_project_id = CAST(:root AS uuid), false))
             OVER (PARTITION BY t.task_number) AS has_root,
           row_number() OVER (
             PARTITION BY t.task_number
             ORDER BY COALESCE(t.root_project_id = CAST(:root AS uuid), false) DESC,
                      (t.archived_at IS NOT NULL), t.updated_at DESC, t.id
           ) AS k
      FROM pm_tasks t
      JOIN pm_task_statuses s ON s.id = t.status_id
     WHERE {visible}
       AND {triage}
       AND t.task_number = ANY(CAST(:numbers AS bigint[]))
  ) hits
 WHERE k <= :per
   AND (in_root OR NOT has_root)
 ORDER BY task_number, k
"""


def ref_root(raw: str | None) -> str | None:
    """The ``root`` filter as a uuid string, or ``None`` for none.

    A value that is not a uuid is dropped, not refused: it can only narrow
    the answer, so a bad one costs the caller its preference and no more.
    """
    if not raw:
        return None
    try:
        return str(uuid.UUID(str(raw)))
    except (TypeError, ValueError):
        return None


async def lookup_task_refs(
    db: Any, vis: Any, numbers: list[int], root: str | None = None,
) -> list[Any]:
    """The visible tasks that carry ``numbers``: the one read for a ``#N``.

    The search route below and the links from text (``text_links.py``) both
    call it, so a pill and a saved link can never name two tasks. ``root``
    prefers that root's task for each number (see the note above).
    """
    if not numbers:
        return []
    return list((await db.execute(
        text(_REFS_SQL.format(
            visible=task_visibility_clause(vis),
            triage=triage_exclusion_clause(),
        )),
        {**vis.params, "numbers": list(numbers), "per": REF_HITS_PER_NUMBER, "root": root},
    )).fetchall())


def ref_numbers(raw: str) -> list[int]:
    """``"171,#87, 171,x"`` → ``[87, 171]``: the distinct task numbers, sorted.

    Each item goes through :func:`task_number`, the one rule of what a task
    number is. Junk is dropped, not refused: a chat answer is not a form, and
    one bad item must not cost the others their pills. More than
    :data:`MAX_REFS` numbers is a 422, because the client batches at that size
    and a longer list is a caller that skipped the cap.
    """
    numbers = sorted({
        n for n in (task_number(part) for part in (raw or "").split(","))
        if n is not None and n > 0
    })
    if len(numbers) > MAX_REFS:
        raise HTTPException(
            status_code=422,
            detail=f"Ask for at most {MAX_REFS} task numbers at once.",
        )
    return numbers


@router.get("/search/refs")
async def task_refs(
    numbers: str = "",
    root: str = "",
    user: UserContext = Depends(get_current_user),
) -> dict:
    """The tasks the caller can see that carry these numbers, in one read.

    ``?numbers=171,87``. Each row is ``{id, title, task_number, status_name,
    category, archived}``. A number with no visible task has no row.
    ``&root=<uuid>`` prefers that root's task for each number (TR-5).
    """
    wanted = ref_numbers(numbers)
    if not wanted:
        return {"rows": []}
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        rows = await lookup_task_refs(db, vis, wanted, ref_root(root))
    return {
        "rows": [
            {
                "id": str(r.id),
                "title": r.title,
                "task_number": int(r.task_number),
                "status_name": r.status_name,
                "category": r.category,
                "archived": bool(r.archived),
            }
            for r in rows
        ],
    }


__all__ = [
    "DESCRIPTION_RANK", "MAX_HITS", "MAX_REFS", "MIN_QUERY", "REF_HITS_PER_NUMBER",
    "SNIPPET_CHARS", "lookup_task_refs", "ref_numbers", "ref_root", "relative_task_ids",
    "snippet_of", "task_number",
]
