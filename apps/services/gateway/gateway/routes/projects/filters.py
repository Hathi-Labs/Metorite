"""Projects · the task-list filter vocabulary (WS-27k).

Spec: ``project-docs/specs/project_management_app.md`` §11.2 item 3.

*"My open bugs in Ops, grouped by assignee"* is a daily question the board had
no way to answer. This is the filter half; grouping is presentation and lives in
the browser, over rows this module makes groupable.

**One builder, used by the list endpoint AND by saved views.** A saved view is
nothing but a stored set of these filters, so if the two had separate
implementations a view would eventually show a different set of tasks than the
same filters typed by hand — which is the one thing a *saved* view may not do.

**Every filter is a WHERE clause, never a post-filter in Python.** Pagination
happens in SQL; a filter applied after `LIMIT` would silently return short
pages, and "page 2 is empty but there are 40 more tasks" is the kind of bug
people work around for months instead of reporting.
"""

from __future__ import annotations

import os
import re
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException
from gateway.routes.projects.core import task_visibility_clause, wire
from sqlalchemy import text

#: `pm_task_statuses.category`. Mirrored from the CHECK the migrations leave in
#: force (146, widened by 164's `triage` — WS-27u) and pinned by
#: `test_projects_filters`, which reads the LAST migration to constrain it.
STATUS_CATEGORIES: tuple[str, ...] = (
    "backlog", "todo", "in_progress", "done", "cancelled", "triage",
)

#: Categories that mean "this task is finished". Shared with
#: `core.CLOSING_CATEGORIES` in spirit; kept here as the *filter* vocabulary so
#: the SQL and the status-transition logic can be read independently.
CLOSED_CATEGORIES: tuple[str, ...] = ("done", "cancelled")

#: How many values one multi-valued filter may carry. A filter is a convenience;
#: an unbounded `IN` list is a way to hand the database a megabyte of literals.
MAX_MULTI = 100

#: The shortest text query either search surface will run (WS-27be).
#:
#: **It lives here, not in `search.py`, because there is one rule and two
#: readers.** `search.py` enforced this from the day it was written and the list
#: endpoint's `?q=` did not, so `GET /projects/tasks?q=a` was an unbounded
#: substring scan at the one surface nothing capped — the exact query the search
#: palette has always refused. `search.py` imports the constant from here (this
#: module is the one it already depends on, so the arrow only points one way);
#: two copies of a threshold is how they stop matching.
#:
#: **3, by D-PM-31** (owner, 2026-08-13; built 2026-10-06). `pg_trgm` extracts
#: whole trigrams from the pattern, and `%ab%` contains none — measured on 240k
#: rows, a 2-character term was a 168 ms full scan while a 3-character one is
#: 0.4 ms off `idx_pm_tasks_title_trgm` (migration 170). At 3, every accepted
#: text query is one the index can serve.
#:
#: ⚠️ **A task number is not text, and it passes at any length** (the owner's
#: exception, 2026-10-06). `#7` is an exact lookup on `task_number`, served by
#: `idx_pm_tasks_task_number` (migration 170), so the trigram reason does not
#: apply to it. :func:`task_number` decides what counts as one, and
#: :func:`short_text_query` is the one test both endpoints ask.
#:
#: Two other readers hold a copy, because they cannot import this: the chat tool
#: (`skill_projects/reads.py`) and the browser (`app/projects/lib/search.ts`).
#: `tests/unit/test_projects_search_minimum_lockstep.py` holds all three equal.
MIN_QUERY = 3


#: Whitespace at either end, plus U+FEFF. ``str.strip`` keeps U+FEFF and
#: JavaScript's ``trim`` drops it, so a pasted "\ufeff7" was a task number in
#: the browser and a 422 here. ``search.ts`` ``cleanQuery`` strips this set.
_EDGE_SPACE = re.compile(r"^[\s\ufeff]+|[\s\ufeff]+$")


def clean_query(raw: str) -> str:
    """A query with its edge whitespace (and U+FEFF) removed. The one trim
    the minimum rule reads, here and in the browser."""
    return _EDGE_SPACE.sub("", raw or "")


def task_number(raw: str) -> int | None:
    """``#42`` or ``42`` → 42, else ``None``.

    Bounded to what the column can hold: `task_number` is a BIGINT, and a
    forty-digit "number" is a phrase somebody typed, not a lookup. Without the
    bound it would also be an unbounded integer parse on user input.

    ASCII digits only. ``str.isdigit`` is also true for ``²``, and ``int("²")``
    raises, so without ``isascii`` a superscript answered a 500.
    """
    stripped = clean_query(clean_query(raw).lstrip("#"))
    if not (stripped.isascii() and stripped.isdigit()) or len(stripped) > 18:
        return None
    return int(stripped)


def short_text_query(term: str) -> bool:
    """True when ``term`` is text too short to search (D-PM-31).

    An empty term is not a query at all, and a task number passes at any
    length, so neither is "short".
    """
    return bool(term) and len(term) < MIN_QUERY and task_number(term) is None


def short_query_message(term: str) -> str:
    """The 422 detail for a short text query. The chat tool says the same
    sentence with the term in «guillemets», because it fences member text."""
    return (
        f"Give at least {MIN_QUERY} characters to search ('{term}' is"
        f" {len(term)}). A task number works at any length: #7."
    )


def split_csv(raw: str | None) -> list[str]:
    """``"a, b ,,c"`` → ``["a", "b", "c"]``.

    Blanks are dropped rather than becoming an empty-string filter value that
    matches nothing — a trailing comma is a typo, not a request for no results.
    """
    return [part.strip() for part in (raw or "").split(",") if part.strip()]


def validate_categories(values: list[str]) -> list[str]:
    """Refuse a category the schema does not have.

    422 rather than an empty board: a client filtering on `in-progress`
    (hyphen) would otherwise see "no tasks" and conclude the project is empty.
    """
    unknown = [v for v in values if v not in STATUS_CATEGORIES]
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown status category {unknown}. "
                   f"One of: {list(STATUS_CATEGORIES)}.",
        )
    return values


def like_escape(term: str) -> str:
    """Neutralise LIKE's metacharacters in a term a human typed (WS-27r).

    ⚠️ **This was a live defect, not a precaution for new code.** `_` matches
    any single character, so searching `task_id` also returned `taskXid` and
    `task-id` — and in a workspace where people search for identifiers all day,
    that is a steady drip of hits nobody asked for. `%` matches any run, so
    `50%` quietly meant `50`.

    Backslash first, or escaping it afterwards would double the backslashes
    this function has just introduced. Postgres' default LIKE escape is the
    backslash and the pattern is BOUND rather than interpolated, so
    `standard_conforming_strings` does not enter into it.
    """
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


#: The most words one text query matches one by one (owner request,
#: 2026-10-13). Words past this are ignored, so a pasted paragraph cannot
#: become a fifty-arm WHERE.
MAX_QUERY_WORDS = 8


def query_words(term: str) -> list[str]:
    """The words a text query must each find, in the order typed.

    **Why words and not the phrase.** Until 2026-10-13 both routes matched the
    query as ONE substring. "invoice approval" then missed a task whose
    description says "approval of the invoice", and the chat concluded that
    descriptions are not searched at all. Every word must still appear, in the
    title or in the description, so a phrase match is always a word match too:
    the change only widens.

    Words repeat case-insensitively only once. When no word reaches
    :data:`MIN_QUERY` (``"to do"``), the trigram index could serve none of
    them, so the whole term is the one word, which is the old phrase rule.
    """
    seen: dict[str, str] = {}
    for word in term.split():
        seen.setdefault(word.lower(), word)
    words = list(seen.values())[:MAX_QUERY_WORDS]
    if not any(len(word) >= MIN_QUERY for word in words):
        return [term]
    return words


def text_match_clause(
    term: str, *, alias: str = "t", param: str = "q",
) -> tuple[str, dict[str, str]]:
    """``(clause, params)``: every word of ``term`` is in the title or the
    description. The ONE text predicate, read by ``/projects/search`` and by
    the list endpoint's ``?q=`` alike, so the two cannot drift.

    Each arm is ``title ILIKE :w OR description ILIKE :w``, the shape the two
    trigram indexes of migration 170 serve. A word shorter than three letters
    cannot use a trigram, so the planner checks it as a filter on the rows the
    longer words found (``query_words`` keeps at least one long word).
    """
    arms: list[str] = []
    params: dict[str, str] = {}
    for i, word in enumerate(query_words(term)):
        key = f"{param}_w{i}"
        arms.append(f"({alias}.title ILIKE :{key} OR {alias}.description ILIKE :{key})")
        params[key] = f"%{like_escape(word)}%"
    return "(" + " AND ".join(arms) + ")", params


def parse_when(raw: str, *, field: str) -> datetime:
    """A query-string timestamp → a real ``datetime``.

    **Parsed here rather than left to Postgres.** ``CAST(:x AS timestamptz)``
    looks like it would take the string, but asyncpg infers the parameter's type
    from that cast and then refuses to encode a ``str`` at all — the query never
    reaches the database, and the endpoint answers 500. Binding a `datetime` is
    the only shape that works, so the parsing has to happen on this side.

    A value that is not a timestamp is a **422**: it is the client's mistake,
    and `due_before=tomorrow` deserves to be told so rather than to read as a
    server fault.

    A naive value is taken as UTC. ``due_at`` is a ``timestamptz`` and `overdue`
    compares against `now()`, so UTC is the frame this module already works in —
    stated rather than left to whatever the connection's TimeZone happens to be.
    """
    try:
        parsed = datetime.fromisoformat(raw.strip())
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail=f"'{raw}' is not a valid {field}. "
                   f"Expected an ISO 8601 date or timestamp, e.g. 2026-08-31 "
                   f"or 2026-08-31T17:00:00Z.",
        ) from None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def build_task_filters(
    *,
    parent_task_id: str | None = None,
    status_id: str | None = None,
    status_category: str | None = None,
    assignee: str | None = None,
    assignees: str | None = None,
    unassigned: bool = False,
    overdue: bool = False,
    due_before: str | None = None,
    importance_gte: int | None = None,
    q: str | None = None,
    tags: str | None = None,
    tags_all: str | None = None,
    include_archived: bool = False,
    archived_only: bool = False,
    watching: bool = False,
    viewer: str | None = None,
    top_level: bool = False,
) -> tuple[list[str], dict[str, Any]]:
    """The WHERE fragments for one task query, and their bound parameters.

    Pure: no database, no request. That is what lets the same function serve the
    endpoint and a saved view, and lets the interesting claims — *which clauses
    exist at all* — be tested without a fake Postgres agreeing with itself.

    Every fragment addresses ``t`` (``pm_tasks``); the caller supplies the
    visibility clause and any project scoping, because those need a database
    round trip and a 404 decision this function must not make.
    """
    clauses: list[str] = []
    params: dict[str, Any] = {}

    if parent_task_id:
        clauses.append("t.parent_task_id = CAST(:parent AS uuid)")
        params["parent"] = parent_task_id

    if top_level:
        # D-PM-38. A view with `subtasks = hidden` sends this. It is a
        # PRESENTATION choice that the server applies, because paging is in
        # SQL: a client that dropped subtasks after `LIMIT` would draw short
        # pages. It is not in `VIEW_FILTER_KEYS`, because the view stores the
        # `subtasks` mode and the client turns that mode into this flag.
        clauses.append("t.parent_task_id IS NULL")

    if status_id:
        clauses.append("t.status_id = CAST(:status_id AS uuid)")
        params["status_id"] = status_id

    categories = validate_categories(split_csv(status_category))[:MAX_MULTI]
    if categories:
        # Joined through the status row rather than stored on the task: the
        # category is the status's property, and denormalising it onto tasks
        # would need re-stamping every task whenever a lane is recategorised.
        clauses.append(
            "EXISTS (SELECT 1 FROM pm_task_statuses s "
            "        WHERE s.id = t.status_id AND s.category = ANY(:categories))"
        )
        params["categories"] = categories

    wanted = [a.lower() for a in split_csv(assignees)][:MAX_MULTI]
    if assignee and assignee.strip():
        wanted.append(assignee.strip().lower())
    if wanted:
        clauses.append(
            "EXISTS (SELECT 1 FROM pm_task_assignees a "
            "        WHERE a.task_id = t.id AND lower(a.assignee) = ANY(:assignees))"
        )
        params["assignees"] = sorted(set(wanted))

    if unassigned:
        # Compatible with an assignee filter on purpose — "assigned to Priya OR
        # to nobody" is not a question anyone asks, so the two together
        # correctly return nothing rather than being silently reconciled.
        clauses.append(
            "NOT EXISTS (SELECT 1 FROM pm_task_assignees a WHERE a.task_id = t.id)"
        )

    if watching:
        # WS-27bk §9.12.2 — "what am I watching", as a FILTER and not a fourth
        # lens. One task store, three lenses (D52/D53/D54): a watched-tasks
        # view would fork that, and it would not compose. As a filter it does —
        # "things I watch, in Ops, that are overdue" is one query.
        #
        # ⚠️ TWO PARAMETERS, ON PURPOSE. `watching` is the saved INTENT and
        # `viewer` is the identity that completes it. A view stores the first
        # and the endpoint supplies the second, so one shared board shows each
        # person their OWN subscriptions. Folding them into a single
        # `watched_by` address would pin one member's list into a view other
        # people open — and it would break `VIEW_FILTER_KEYS`, whose fence
        # asserts every savable key is a parameter of this function.
        #
        # ⚠️ A WATCH IS AN INTENT TO HEAR, NEVER A RIGHT TO SEE (WS-27v). This
        # clause is additive only: the endpoint's visibility clause still
        # applies, so a watcher who lost the project's grant sees nothing.
        # Filtering may narrow and must never widen.
        if not (viewer and viewer.strip()):
            # Asked to narrow to a person, and given none. Refusing is the only
            # honest answer — dropping the clause would silently return the
            # WHOLE board as though it were one person's subscriptions.
            raise HTTPException(
                status_code=422,
                detail="watching needs a signed-in viewer.",
            )
        # `lower()` on both sides because `watchable()` folds an address before
        # it is stored, so an unfolded parameter would match nothing.
        clauses.append(
            "EXISTS (SELECT 1 FROM pm_task_watchers w "
            "        WHERE w.task_id = t.id AND lower(w.watcher) = :viewer)"
        )
        params["viewer"] = viewer.strip().lower()

    if overdue:
        # Overdue means "past due AND still open". A finished task with a past
        # due date is not overdue, it is done — colouring it red forever is how
        # a board teaches people to ignore red.
        clauses.append(
            "t.due_at < now() AND NOT EXISTS ("
            "  SELECT 1 FROM pm_task_statuses s "
            "  WHERE s.id = t.status_id AND s.category = ANY(:closed))"
        )
        params["closed"] = list(CLOSED_CATEGORIES)

    if due_before:
        clauses.append("t.due_at < :due_before")
        params["due_before"] = parse_when(due_before, field="due_before")

    if importance_gte is not None:
        clauses.append("t.importance >= :importance_gte")
        params["importance_gte"] = importance_gte

    # WS-27be. A text query shorter than `MIN_QUERY` matches NOTHING rather than
    # being dropped — the same answer `/projects/search` has always given
    # (`{"rows": []}`, not a 422), now given by the list endpoint too.
    #
    # **Dropping the clause would be the worse bug.** A one-character `q` would
    # then return the whole board, so the client that sent it renders an
    # unfiltered list while believing it is filtered — a filter that silently
    # does not filter. `FALSE` keeps the contract "you asked for a substring
    # search and got the tasks that match it"; there are simply none, because we
    # will not run a substring scan that short.
    #
    # A literal, not a bound parameter: the planner constant-folds it into a
    # one-time filter and the query never touches `pm_tasks` at all, which is
    # the entire point.
    #
    # D-PM-31's exception: a query that is only a task number (`#7`, `7`) is
    # an exact lookup on `task_number`, at any length, and never a text scan.
    # The search route answers it the same way, so the two endpoints agree.
    term = clean_query(q or "")
    number = task_number(term) if term else None
    if number is not None:
        clauses.append("t.task_number = :q_number")
        params["q_number"] = number
    elif short_text_query(term):
        clauses.append("FALSE")
    elif term:
        # Every word, in the title or the description (`text_match_clause`):
        # the same predicate `/projects/search` runs.
        match, match_params = text_match_clause(term)
        clauses.append(match)
        params.update(match_params)

    # WS-27m. TWO tag filters, because both questions get asked and one cannot
    # answer the other: `tags` is ANY (`&&` — "show me bugs or regressions"),
    # `tags_all` is ALL (`@>` — "the ones that are both"). Collapsing them into
    # one parameter would silently pick a meaning, and with three tags the two
    # answers differ by almost everything.
    #
    # Both use the array operators the GIN index on `pm_tasks.tags` (146) serves,
    # so a tag filter across an imported workspace is an index scan.
    wanted_any = split_csv(tags)[:MAX_MULTI]
    if wanted_any:
        clauses.append("t.tags && CAST(:tags_any AS text[])")
        params["tags_any"] = wanted_any

    wanted_all = split_csv(tags_all)[:MAX_MULTI]
    if wanted_all:
        clauses.append("t.tags @> CAST(:tags_all AS text[])")
        params["tags_all"] = wanted_all

    if archived_only:
        # ── The archive, as a place you can go ────────────────────────────
        #
        # `include_archived` answers "show me everything"; this answers "show
        # me what I filed". They are different questions and the second one is
        # the one somebody asks when they want a task BACK — mixed in with
        # every live task, an archived one is not findable.
        #
        # ⚠️ **Two booleans rather than one tri-state, and that is a wire
        # decision, not a modelling one.** `include_archived` is already on
        # four endpoints and inside saved views (`VIEW_FILTER_KEYS`), so
        # changing its type would break stored configs. Both are read HERE,
        # in one place, so no caller can compose them wrongly: `archived_only`
        # simply wins.
        #
        # ⚠️ This is the task's OWN archive flag. A task sitting in an
        # archived PROJECT is filed by its project, not by anybody choosing to
        # file it, so it does not belong in "things I archived" — and
        # unarchiving it here would not bring it back anyway. That is a
        # project-level act (`unarchive_node`).
        clauses.append("t.archived_at IS NOT NULL")
    elif not include_archived:
        clauses.append("t.archived_at IS NULL")
        # WS-27bg. A task is also out of the default reads when the PROJECT
        # holding it is filed — that is what "archive a project and its tasks"
        # means, and D-PM-26 says it must cost no `pm_tasks` write to mean it.
        #
        # The task's own project, with no ancestor walk: `archive_node` stamps
        # the whole subtree at write time precisely so this stays a plain
        # indexed join rather than a recursive CTE on every list read.
        #
        # Deliberately governed by the SAME `include_archived` flag rather than
        # a second one: "show me the archived things" is one question, and two
        # flags would make a caller who set the obvious one still miss half the
        # answer. ⚠️ Note this is the ARCHIVE axis only — a paused or stopped
        # project's tasks are still listed, because pausing files nothing
        # (D-PM-25's two axes). What paused changes is overdue, My Work and the
        # dated views, which is slice 3.
        clauses.append(
            "EXISTS (SELECT 1 FROM pm_projects p "
            "        WHERE p.id = t.project_id AND p.archived_at IS NULL)"
        )

    return clauses, params


#: The keys a saved view's `config.filters` may carry — exactly the arguments
#: `build_task_filters` accepts, minus the ones a view has no business pinning
#: (`parent_task_id` is navigation, not a filter).
VIEW_FILTER_KEYS: frozenset[str] = frozenset({
    "status_id", "status_category", "assignee", "assignees", "unassigned",
    "overdue", "due_before", "importance_gte", "q", "tags", "tags_all",
    "include_archived", "archived_only",
    # ⚠️ `watching`, NOT `watched_by`. The view stores the INTENT — "things I
    # watch" — and the endpoint resolves it to whoever is asking. Storing an
    # address instead would pin one person's subscriptions into a view other
    # people open, so a shared board would show them a colleague's list and
    # call it theirs. Same shape as `unassigned`: a boolean whose meaning is
    # completed by the caller.
    "watching",
})

#: What a board may group by. `status` is the board's own axis; the others are
#: what §11.2 asked for by name.
#:
#: ⚠️ `category` is the STAGE axis (§9.12.3), and it stopped being decoration
#: on 2026-09-06. The spec parked it as "value latent" because every root then
#: held exactly four statuses — one per stage — so grouping by stage drew the
#: same four columns as grouping by status. Migration 196 ended that: a
#: subproject may keep its own lanes, so one subtree can hold "Doing" and
#: "In progress" meaning the same thing. `category` is the only vocabulary two
#: sets share, which makes it the only axis that stays legible across them, and
#: the one every cross-project number in §9.12.7 will rest on.
#:
#: WS-55 EA-13e (EA3-43): ``agent`` groups by the agent state of a row. The
#: client draws the lanes from ``agent_state``, and a drop between two agent
#: lanes writes nothing, because a person does not set a run state.
GROUP_BY: tuple[str, ...] = (
    "status", "category", "assignee", "project", "importance", "tag", "agent", "none",
)

#: WS-27x — the field keys a view's `shown_fields` may name. Mirrors the
#: client vocabulary (`lib/shownFields.ts`), which is the single source the
#: table's columns and the chip gate read; this tuple is the server's copy so
#: a stored view cannot accumulate junk keys.
SHOWN_FIELDS: tuple[str, ...] = (
    # ⚠️ `type` added with WS-27bh, in the SAME change as the client's
    # `FIELD_KEYS`. `shownFields.ts` warns why: a key added on one side only
    # is "a preference the server silently strips on the next save" — the
    # member ticks Type, it draws, and their next saved view drops it.
    "status", "type", "source", "assignees", "start_date", "due_at", "importance",
    # WS-55 EA-13d: `pr`, the pull request chip, in the SAME change as the
    # client's `FIELD_KEYS` and the export's `FIELD_LABELS`.
    "subtasks", "blocked", "pr", "tags", "attachments", "estimate", "created_at",
)

#: D-PM-38 — how a view draws a subtask. `nested` puts it under its parent,
#: `separate` draws it as its own row or card with a "↳ Parent" line, and
#: `hidden` leaves it out (the list endpoint's `top_level`).
#:
#: ⚠️ **No default is stored.** Each surface owns its own default (the list is
#: nested, the board is separate), so writing one here would freeze today's
#: default into every stored view — `shown_fields`' rule. Mirrored by
#: `SUBTASK_MODES` in `app/projects/lib/grouping.ts`, and
#: `test_projects_filters` fails if the two differ.
SUBTASK_MODES: tuple[str, ...] = ("nested", "separate", "hidden")

#: Every top-level key :func:`normalise_view_config` can keep. Mirrored by
#: `VIEW_CONFIG_KEYS` in `app/projects/lib/grouping.ts`, whose `toConfig`
#: writes them. `test_projects_filters` fails if the two sets differ: a key
#: the client writes and this drops is a setting that a save loses (S3 found
#: `subtasks` in that state on the client side).
VIEW_CONFIG_KEYS: frozenset[str] = frozenset({
    "filters", "group_by", "sub_group_by", "collapsed_lanes",
    "show_empty_lanes", "shown_fields", "subtasks",
})

#: A project's custom fields ride the same list as ``custom.<field_key>`` —
#: the spelling ``patch_task`` already files a custom edit under. Checked by
#: SHAPE rather than against the registry: this function is pure, and a view
#: must survive its field being deleted after the save.
_CUSTOM_FIELD_PREFIX = "custom."


def normalise_view_config(config: Any) -> dict[str, Any]:
    """A stored view's config, reduced to what the board can actually apply.

    Unknown keys are DROPPED rather than rejected. A view is a saved preference
    written by an older client, and refusing to open one because it carries a
    key this version does not know would turn every deploy into a migration of
    everybody's saved views. Dropping is forgiving in the direction that keeps
    the board usable.

    A bad *value* is different from a bad key and is not silently coerced: an
    unknown `group_by` falls back to `status` because rendering must produce
    something, and that fallback is the board's own default rather than a guess.
    """
    if not isinstance(config, dict):
        return {"filters": {}, "group_by": "status"}
    raw = config.get("filters")
    filters = {
        key: value for key, value in (raw or {}).items()
        if key in VIEW_FILTER_KEYS
    } if isinstance(raw, dict) else {}
    group_by = config.get("group_by")
    out: dict[str, Any] = {
        "filters": filters,
        "group_by": group_by if group_by in GROUP_BY else "status",
    }
    # WS-27y — lane state rides the same config. The client's rules mirrored
    # exactly (grouping.ts `fromConfig`): a sub-axis equal to the main axis is
    # nonsense and is dropped, lane keys must be strings, and the flags are
    # stored only when they say something — so a lane-less view stays
    # byte-identical to one saved before lanes existed.
    sub = config.get("sub_group_by")
    if sub in GROUP_BY and sub != "none" and sub != out["group_by"]:
        out["sub_group_by"] = sub
        lanes = config.get("collapsed_lanes")
        if isinstance(lanes, list):
            kept = [key for key in lanes if isinstance(key, str)]
            if kept:
                out["collapsed_lanes"] = kept
        if config.get("show_empty_lanes") is True:
            out["show_empty_lanes"] = True
    # WS-27x — the shown-fields set. The client's rules mirrored exactly
    # (`shownFields.sanitizeShownFields`): a list of known field keys, with
    # unknown and non-string entries dropped and duplicates collapsed to the
    # first appearance. ABSENT (or not a list) stays absent — the default set
    # is the client's to apply, and writing it here would freeze today's
    # default into every stored view. An explicitly stored empty list is KEPT:
    # "every column hidden" is a choice, not the default.
    shown = config.get("shown_fields")
    if isinstance(shown, list):
        kept_fields: list[str] = []
        for key in shown:
            if not isinstance(key, str) or key in kept_fields:
                continue
            known = key in SHOWN_FIELDS or (
                key.startswith(_CUSTOM_FIELD_PREFIX)
                and len(key) > len(_CUSTOM_FIELD_PREFIX)
            )
            if known:
                kept_fields.append(key)
        out["shown_fields"] = kept_fields
    # D-PM-38. Kept only when it is one of the three words. Absent stays
    # absent, so the surface applies its own default.
    if config.get("subtasks") in SUBTASK_MODES:
        out["subtasks"] = config["subtasks"]
    return out


#: The keys ONE MEMBER's private overlay on a shared view may carry (WS-27ae /
#: P-28). Presentation only — deliberately **no filters**: a per-person filter
#: set would make one saved view mean two different task sets for two people,
#: which is precisely what a *shared* view may not do. What a member is allowed
#: to disagree about is how their own screen is arranged.
#:
#: ⚠️ **One exception, on purpose: `subtasks` (D-PM-38, spec §12.9).** It is
#: the one key here that can FOLD ROWS. `hidden` makes the client send
#: `top_level`, so two members of one shared view can see different rows. The
#: approved design remembers the setting for each member, so this is not a
#: leak of the rule above but a named exception to it. It only ever hides
#: subtasks, never adds a task, and a hidden subtask still counts in its
#: parent's chip, so the parent still says it has children. No other key may
#: follow it without a decision of its own.
VIEW_USER_STATE_KEYS: frozenset[str] = frozenset({
    "group_by", "sub_group_by", "collapsed_lanes", "show_empty_lanes",
    "shown_fields",
    # D-PM-38 — the exception named above. `hidden` folds subtask rows for
    # this member only. The list, the calendar, the export and (since S3)
    # the delta feed all take `top_level`, so a synced Hidden board stays
    # folded. `test_every_list_surface_declares_it` is the fence.
    "subtasks",
})


def normalise_view_user_state(config: Any) -> dict[str, Any]:
    """One member's overlay on a shared view, reduced to what it may say.

    ⚠️ **Not `normalise_view_config` with a different key set, and the
    difference is the whole point.** That function fills a DEFAULT (`group_by`
    falls back to `status`) because a view must always render something. An
    overlay must not: a key it does not carry means *"I have no opinion, use the
    view's"*, and a normaliser that injected `status` would make every member's
    first collapse silently re-group the board for themselves.

    So this is absent-preserving, and it shares the vocabulary — `GROUP_BY`,
    `SHOWN_FIELDS`, the custom-field prefix — with the shared parser rather than
    restating it. Unknown keys and bad values are dropped for
    `normalise_view_config`'s reason: an overlay written by a newer client must
    not make an older one refuse to open the view.
    """
    if not isinstance(config, dict):
        return {}
    out: dict[str, Any] = {}
    for axis in ("group_by", "sub_group_by"):
        value = config.get(axis)
        if value in GROUP_BY:
            out[axis] = value
    # A sub-axis equal to the main axis is nonsense — the same rule the shared
    # parser applies, and it has to be applied to the MERGED pair, so it is
    # checked here only when this overlay states both.
    if out.get("sub_group_by") is not None and out.get("sub_group_by") == out.get("group_by"):
        out.pop("sub_group_by")
    lanes = config.get("collapsed_lanes")
    if isinstance(lanes, list):
        # An explicit empty list is KEPT: "I have expanded every lane" is an
        # opinion, and dropping it would make the shared config's collapse
        # state reappear on the next load.
        out["collapsed_lanes"] = [key for key in lanes if isinstance(key, str)]
    if isinstance(config.get("show_empty_lanes"), bool):
        out["show_empty_lanes"] = config["show_empty_lanes"]
    shown = config.get("shown_fields")
    if isinstance(shown, list):
        kept: list[str] = []
        for key in shown:
            if not isinstance(key, str) or key in kept:
                continue
            if key in SHOWN_FIELDS or (
                key.startswith(_CUSTOM_FIELD_PREFIX)
                and len(key) > len(_CUSTOM_FIELD_PREFIX)
            ):
                kept.append(key)
        out["shown_fields"] = kept
    if config.get("subtasks") in SUBTASK_MODES:
        out["subtasks"] = config["subtasks"]
    return out


#: Assignees for a page of tasks, in ONE query.
#:
#: Not a subquery on the list itself (which would repeat a task per assignee and
#: break `LIMIT`), and emphatically not one query per row: an imported ClickUp
#: workspace is hundreds of tasks, and N+1 there is a board that takes a visible
#: second to paint.
_ASSIGNEES_SQL = """
SELECT task_id, array_agg(assignee ORDER BY assignee) AS people
  FROM pm_task_assignees
 WHERE task_id = ANY(CAST(:ids AS uuid[]))
 GROUP BY task_id
"""


#: Subtask progress and open-blocker counts for a page of tasks, in TWO queries.
#:
#: The same trade `_ASSIGNEES_SQL` makes and for the same reason: a board draws
#: these badges on every card, and asking per card is N+1 across an imported
#: workspace of hundreds. Aggregated over the page's ids rather than joined onto
#: the list itself, because a join would repeat the task row per child and break
#: `LIMIT`.
#:
#: ⚠️ **Children are counted under the READER's visibility (D-PM-38, B4).** The
#: chip read every child until 2026-09-26, and the task panel's progress
#: (`relations._SUBTASKS_SQL`) read only the visible ones. So a parent with a
#: child in an ungranted project drew "0/2" on the card and "0 of 1" in the
#: panel. The two now apply the same two rules: visible to the reader, and not
#: archived. `{visible}` is `core.task_visibility_clause`, formatted by the
#: caller of this SQL, never by a request.
_SUBTASK_COUNTS_SQL = """
SELECT t.parent_task_id AS parent,
       count(*) AS total,
       count(*) FILTER (WHERE s.category = ANY(:closed)) AS done
  FROM pm_tasks t
  JOIN pm_task_statuses s ON s.id = t.status_id
 WHERE t.parent_task_id = ANY(CAST(:ids AS uuid[]))
   AND t.archived_at IS NULL
   AND {visible}
 GROUP BY t.parent_task_id
"""

#: The parents of a page of tasks, in ONE query (D-PM-38).
#:
#: ⚠️ **The visibility clause is the WHERE, not a column.** A parent the reader
#: cannot see returns no row at all, so its title never leaves the database.
#: Selecting every parent and masking the title in Python would put the title
#: in this process's memory and one careless edit away from the wire.
#:
#: `archived` is the parent's OWN flag. A parent in an archived project is
#: filed by its project, and the list's default reads already leave that out.
_PARENT_CONTEXT_SQL = """
SELECT t.id, t.task_number, t.title, t.archived_at
  FROM pm_tasks t
 WHERE t.id = ANY(CAST(:parent_ids AS uuid[]))
   AND {visible}
"""


def task_ref(task_number: Any) -> str | None:
    """``42`` → ``"#42"``. The reference the whole app draws (`card.taskRef`)."""
    return None if task_number is None else f"#{task_number}"


async def attach_parent_context(
    db: Any, vis: Any, rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Fill each row's ``parent``, mutating and returning the list (D-PM-38).

    ``parent`` is ``None`` for a top-level task. For a subtask it is
    ``{id, ref, title, archived}`` when the reader can see the parent, and
    ``{hidden: True}`` when they cannot. The hidden form adds no id and no
    title. The child's own ``parent_task_id`` was on the wire before this, and
    it says only that the task HAS a parent, which is all a reader without a
    grant may learn.

    ONE query over the distinct parent ids, never one per row. A board of
    three hundred subtasks is the case this exists for.

    ``vis`` is the caller's `core.Visibility`, resolved from the session. It
    is never built from request input (R5).
    """
    for row in rows:
        row["parent"] = None
    wanted = sorted({
        str(r["parent_task_id"]) for r in rows if r.get("parent_task_id")
    })
    if not wanted:
        return rows
    found = (await db.execute(
        text(_PARENT_CONTEXT_SQL.format(visible=task_visibility_clause(vis))),
        {"parent_ids": wanted, **vis.params},
    )).fetchall()
    seen = {
        str(r.id): {
            "id": str(r.id),
            "ref": task_ref(r.task_number),
            "title": r.title,
            "archived": r.archived_at is not None,
        }
        for r in found
    }
    for row in rows:
        parent = row.get("parent_task_id")
        if parent:
            row["parent"] = seen.get(str(parent), {"hidden": True})
    return rows

#: How many still-OPEN tasks block each of these.
#:
#: Filtered to open blockers in SQL rather than counted and filtered after: a
#: finished blocker blocks nothing (WS-27p), and a card that stays marked
#: blocked after its dependency shipped is a card people learn to ignore.
_BLOCKER_COUNTS_SQL = """
SELECT l.target_task_id AS blocked, count(*) AS blockers
  FROM pm_task_links l
  JOIN pm_tasks t ON t.id = l.source_task_id
  JOIN pm_task_statuses s ON s.id = t.status_id
 WHERE l.link_type = 'blocks'
   AND l.target_task_id = ANY(CAST(:ids AS uuid[]))
   AND NOT (s.category = ANY(:closed))
 GROUP BY l.target_task_id
"""


#: The `blocks` edges BETWEEN tasks in one window (WS-27t).
#:
#: **Both ends must be in the set**, because an arrow is drawn between two bars
#: and a bar that is not on screen has no end to attach to. The edge to an
#: off-window blocker is not lost, only undrawable — `blocked_by_count` already
#: puts a badge on the bar, which is the honest rendering of "something you
#: cannot see is holding this up".
#:
#: Only `blocks`. `relates_to` and `duplicates` are associations with no
#: direction that means anything to a schedule (WS-27p's `DIRECTED_TYPES`), and
#: drawing them as arrows would claim a sequence that was never asserted.
_WINDOW_LINKS_SQL = """
SELECT l.id, l.source_task_id AS blocker, l.target_task_id AS blocked
  FROM pm_task_links l
 WHERE l.link_type = 'blocks'
   AND l.source_task_id = ANY(CAST(:ids AS uuid[]))
   AND l.target_task_id = ANY(CAST(:ids AS uuid[]))
 ORDER BY l.id
"""


async def window_links(db: Any, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The drawable dependency edges among ``rows``, in ONE query.

    Returned beside the rows rather than folded into them: an edge belongs to
    two tasks, and hanging it off one of them makes the client reconstruct the
    other end — which is how a chart ends up drawing an arrow to a bar it has
    already scrolled past.
    """
    ids = [str(r["id"]) for r in rows if r.get("id")]
    if not ids:
        return []
    found = (await db.execute(text(_WINDOW_LINKS_SQL), {"ids": ids})).fetchall()
    return [
        {
            "id": str(r.id),
            "blocker_id": str(r.blocker),
            "blocked_id": str(r.blocked),
        }
        for r in found
    ]


async def attach_relation_counts(
    db: Any, rows: list[dict[str, Any]], vis: Any,
) -> list[dict[str, Any]]:
    """Fill each row's ``subtasks`` and ``blocked_by_count``, mutating in place.

    Every row gets both keys, including the ones with neither — a missing key
    and a zero read the same to a careless client, and "has no subtasks" is a
    state the card draws nothing for rather than an absence it guesses at.

    ``vis`` is REQUIRED (D-PM-38). The subtask count reads only the children
    the reader can see, so the chip and the panel's progress agree. A default
    would let a new caller count every child and bring B4 back.
    """

    for row in rows:
        row["subtasks"] = {"done": 0, "total": 0}
        row["blocked_by_count"] = 0
    ids = [str(r["id"]) for r in rows if r.get("id")]
    if not ids:
        return rows

    args = {"ids": ids, "closed": list(CLOSED_CATEGORIES)}
    counts = (await db.execute(
        text(_SUBTASK_COUNTS_SQL.format(visible=task_visibility_clause(vis))),
        {**args, **vis.params},
    )).fetchall()
    by_parent = {
        str(r.parent): {"done": int(r.done or 0), "total": int(r.total or 0)}
        for r in counts
    }
    blocked = (await db.execute(text(_BLOCKER_COUNTS_SQL), args)).fetchall()
    by_blocked = {str(r.blocked): int(r.blockers or 0) for r in blocked}

    for row in rows:
        key = str(row["id"])
        row["subtasks"] = by_parent.get(key, {"done": 0, "total": 0})
        row["blocked_by_count"] = by_blocked.get(key, 0)
    return rows


async def attach_assignees(db: Any, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fill each row's ``assignees``, mutating and returning the list.

    Every row gets the key, including the ones with nobody on them — a missing
    key and an empty list read the same to a careless client, and "unassigned"
    is a state the board draws, not an absence it ignores.
    """
    for row in rows:
        row["assignees"] = []
    ids = [str(r["id"]) for r in rows if r.get("id")]
    if not ids:
        return rows
    found = (await db.execute(text(_ASSIGNEES_SQL), {"ids": ids})).fetchall()
    by_task = {str(r.task_id): list(r.people or []) for r in found}
    for row in rows:
        row["assignees"] = by_task.get(str(row["id"]), [])
    return rows


# ── WS-55 EA-12a: the agent pill (external_agent_access.md §12.1, EA12-10) ──

#: The runs that are OPEN (EA12-6). The partial unique index of the
#: `pm_agent_runs` migration lists the same four values.
AGENT_RUN_OPEN_STATUSES: tuple[str, ...] = ("created", "in_progress", "awaiting", "stopping")

#: Seconds with no activity before an `in_progress` run reads `stale`.
AGENT_RUN_STALE_AFTER_S = 300

# ── WS-55 EA-14a: one AI for each task, and the lease ──────────────────────

#: A task has at most ONE open run. A run keeps the task while its last
#: activity is newer than the lease. After that, the next claim takes over.
AGENT_RUN_LEASE_DEFAULT_S = 7200
#: An `awaiting` run waits for a person, not for the agent. It keeps the task
#: this many leases, so a question that waits one night does not lose the task.
AGENT_RUN_QUESTION_LEASE_FACTOR = 3
#: The environment variable that overrides the lease, in seconds.
AGENT_RUN_LEASE_ENV = "AGENT_RUN_LEASE_SECONDS"


def agent_run_lease_s() -> int:
    """The lease in seconds. The default is two hours. A value that is not a
    whole number above zero reads as the default."""
    raw = os.environ.get(AGENT_RUN_LEASE_ENV, "").strip()
    try:
        value = int(raw)
    except ValueError:
        return AGENT_RUN_LEASE_DEFAULT_S
    return value if value > 0 else AGENT_RUN_LEASE_DEFAULT_S


def agent_run_hold_s(status: str) -> int:
    """How long a run in *status* keeps its task with no activity."""
    factor = AGENT_RUN_QUESTION_LEASE_FACTOR if status == "awaiting" else 1
    return agent_run_lease_s() * factor


def lease_expired(status: str, idle_s: float) -> bool:
    """True when a run in *status* idle for *idle_s* seconds no longer keeps
    its task. The ONE Python rule of the lease. The ``unheld`` filter reads
    the same constants in SQL (:func:`unheld_clause`)."""
    return idle_s > agent_run_hold_s(status)


def connection_gone_sql(alias: str) -> str:
    """SW-13c. True in SQL when the agent connection of the run *alias* is
    revoked or expired (``agent_connection``, migration 248). Such a run can
    never send a heartbeat again, so it keeps its task no longer than a run
    whose lease ended. A run with no ``connection_id`` is NOT gone: no code
    deletes a connection, so that state has no meaning to act on, and the
    lease rule alone decides. ONE text for the gate (``agent_runs._HOLDER_SQL``),
    the ``unheld`` filter and ``held_by``, so the three cannot disagree."""
    # The organization arm is defence in depth beside FORCE RLS, as
    # `agent_tokens._CONNECTION_LIVE_SQL` has it (fix round 1, P3).
    return (
        f"EXISTS (SELECT 1 FROM agent_connection gc WHERE gc.id = {alias}.connection_id"
        f" AND gc.organization_id = {alias}.organization_id"
        " AND (gc.revoked_at IS NOT NULL OR gc.expires_at <= now()))"
    )


def holder_free(status: str, idle_s: float, connection_gone: bool) -> bool:
    """True when an open run no longer keeps its task, so the next claim
    takes it over (EA-14a, SW-13c): its lease ended, or its connection is
    revoked or expired. The ONE Python rule of the gate and of
    ``held_by.stale``. :func:`unheld_clause` is its SQL twin."""
    return connection_gone is True or lease_expired(status, idle_s)

#: The derived state of one run, in SQL, over the alias ``r``. ONE copy:
#: ``agent_runs.py`` reads it for each run, and :func:`attach_agent_state`
#: reads it for the pill. The database ``now()`` decides ``stale`` (EA12-10).
#:
#: * ``answered`` comes BEFORE ``stale`` (EA12-5): the newest activity is a
#:   member's prompt when ``last_activity_at`` is later than the agent's.
#: * Only ``in_progress`` goes ``stale``. ``created`` reads ``queued``, and an
#:   ``awaiting`` run waits for a person, not for the agent.
#: * Every other status reads as itself.
AGENT_RUN_STATE_SQL = (
    "CASE r.status"
    " WHEN 'created' THEN 'queued'"
    " WHEN 'in_progress' THEN CASE"
    "   WHEN r.last_agent_activity_at IS NULL"
    "     OR r.last_activity_at > r.last_agent_activity_at THEN 'answered'"
    f"   WHEN r.last_activity_at < now() - interval '{AGENT_RUN_STALE_AFTER_S} seconds'"
    "     THEN 'stale'"
    "   ELSE 'working' END"
    " ELSE r.status END"
)

_OPEN_LIST = ", ".join(f"'{s}'" for s in AGENT_RUN_OPEN_STATUSES)

#: The newest line of the open run, for the hover of the pill (EA-13d, EA3-41):
#: the first line of the body of the newest `thought`, `response` or
#: `elicitation`. A leading blank line is skipped. Python cuts it to
#: :data:`AGENT_NOTE_MAX` characters.
_AGENT_NOTE_SQL = (
    "(SELECT split_part(btrim(a.content->>'body', E' \t\r\n'), E'\n', 1)"
    "   FROM pm_agent_run_activities a"
    "  WHERE a.run_id = r.id AND a.type IN ('thought', 'response', 'elicitation')"
    "  ORDER BY a.seq DESC LIMIT 1)"
)

#: The longest ``agent_note``, in characters (EA3-41, EA-13d P4).
AGENT_NOTE_MAX = 140


def unheld_clause() -> str:
    """The fragment of the ``unheld`` filter, over the task alias ``t``.

    A task is unheld when no open run keeps it. A run whose lease ended does
    not keep it, because the next claim takes the task over. SW-13c: nor does
    a run whose connection is revoked or expired (:func:`holder_free`). The
    lease is read when the clause is built, never at import, so the
    environment override works in a running process.
    """
    open_list = ", ".join(f"'{s}'" for s in AGENT_RUN_OPEN_STATUSES)
    lease = agent_run_lease_s()
    hold = lease * AGENT_RUN_QUESTION_LEASE_FACTOR
    return (
        "NOT EXISTS (SELECT 1 FROM pm_agent_runs h"
        " WHERE h.task_id = t.id"
        f"   AND h.status IN ({open_list})"
        f"   AND h.last_activity_at >= now() - (CASE WHEN h.status = 'awaiting'"
        f"        THEN {hold} ELSE {lease} END) * interval '1 second'"
        f"   AND NOT {connection_gone_sql('h')})"
    )


def agent_state_sql(task_filter: str) -> str:
    """The pill query over the tasks that *task_filter* selects (alias ``r``).

    ONE text of the pick rule. The list rows bind ``:ids`` through
    :data:`_AGENT_STATE_SQL`, and the project summary (EA-13e) binds a
    subquery, so a count and a card cannot disagree about a task's state.

    The pill of each task comes from its OPEN runs (§12.1, "The task pill"):

    1. an `awaiting` run, because a person must act, and a newer run of
       another member must not hide the question;
    2. else an `answered` run, so the answer stays in view;
    3. else the newest open run.

    A task with no open run shows a `failed` run that is the newest run of its
    task (EA12-12: the pill stays until a newer run exists). One query for the
    page, as `_ASSIGNEES_SQL` does.
    """
    return f"""
SELECT DISTINCT ON (r.task_id) r.task_id, {AGENT_RUN_STATE_SQL} AS state,
       r.client_via, r.member_email, r.status AS run_status, {_AGENT_NOTE_SQL} AS note,
       COALESCE(r.started_at, r.created_at) AS held_since,
       r.last_activity_at AS held_last,
       EXTRACT(EPOCH FROM (clock_timestamp() - r.last_activity_at)) AS idle_s,
       {connection_gone_sql('r')} AS connection_gone,
       (SELECT u.display_name FROM app_user u
         WHERE lower(u.email) = lower(r.member_email) LIMIT 1) AS member_name
  FROM pm_agent_runs r
 WHERE {task_filter}
   AND (r.status IN ({_OPEN_LIST})
        OR (r.status = 'failed'
            AND NOT EXISTS (SELECT 1 FROM pm_agent_runs n
                             WHERE n.task_id = r.task_id
                               AND (n.created_at, n.id) > (r.created_at, r.id))))
 ORDER BY r.task_id, (r.status IN ({_OPEN_LIST})) DESC,
          CASE {AGENT_RUN_STATE_SQL} WHEN 'awaiting' THEN 0 WHEN 'answered' THEN 1
               ELSE 2 END,
          r.created_at DESC, r.id DESC
"""


_AGENT_STATE_SQL = agent_state_sql("r.task_id = ANY(CAST(:ids AS uuid[]))")


def agent_runs_enabled() -> bool:
    """The flag ``agent_runs_enabled`` (default OFF). The gateway side of the
    one reader, ``acb_auth.agent_oauth.runs_enabled``, which the edge reads
    too (EA-12b)."""
    from acb_auth import agent_oauth

    return agent_oauth.runs_enabled()


def held_by_of(hit: Any) -> dict[str, Any]:
    """The ``held_by`` of a task: who keeps it, through which client, since
    when, and whether the lease ended (EA-14a). ``stale`` here is the LEASE
    (hours). It is not the idle pill state of five minutes. SW-13c: a run
    whose connection is revoked or expired reads ``stale`` too, by the same
    rule as the gate (:func:`holder_free`)."""
    from acb_auth import agent_oauth

    return {
        "member": hit.member_email,
        "member_name": hit.member_name or hit.member_email,
        "client_via": hit.client_via,
        "client_name": agent_oauth.client_label(hit.client_via),
        "since": wire(hit.held_since),
        "last_activity_at": wire(hit.held_last),
        "stale": holder_free(hit.run_status, float(hit.idle_s),
                             getattr(hit, "connection_gone", False) is True),
    }


async def attach_agent_state(db: Any, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fill each row's ``agent_state``, ``agent_client_via``,
    ``agent_member_email`` and ``agent_note``, mutating and returning the list.

    ``queued``, ``working``, ``awaiting``, ``answered``, ``stopping``,
    ``stale`` or ``failed``. ``None`` for a task with no open run. While the
    flag is off, every row gets ``None`` and no pill statement runs, so the
    task reads cost nothing until the owner turns the feature on.

    EA-13d and EA-13e ride the same call, because every route that hands a
    task row to the client already calls it, and the client merges the row
    over its own copy (a missing ``pr`` would erase the chip). So the call
    also fills ``pr`` (:func:`attach_pr_chip`) and ``agent_worked``
    (:func:`attach_agent_worked`), one batched query each.
    """
    for row in rows:
        row.update(agent_state=None, agent_client_via=None, agent_member_email=None,
                   agent_note=None, held_by=None)
    ids = [str(r["id"]) for r in rows if r.get("id")]
    if ids and agent_runs_enabled():
        found = (await db.execute(text(_AGENT_STATE_SQL), {"ids": ids})).fetchall()
        by_task = {str(r.task_id): r for r in found}
        for row in rows:
            hit = by_task.get(str(row["id"]))
            if hit is not None:
                note = " ".join(str(hit.note or "").split())
                row.update(agent_state=hit.state, agent_client_via=hit.client_via,
                           agent_member_email=hit.member_email,
                           agent_note=note[:AGENT_NOTE_MAX] or None)
                if hit.run_status in AGENT_RUN_OPEN_STATUSES:
                    row["held_by"] = held_by_of(hit)
    await attach_pr_chip(db, rows)
    await attach_agent_worked(db, rows)
    return rows


# ── WS-55 EA-13d: the PR chip (external_agent_access.md §12.2 item 2, EA3-48) ──

#: One query for the page. The partition picks ONE pull request for each task,
#: by EA3-48: an open or draft one first, the newest by ``pr_updated_at``. Else
#: the newest merged one, else the newest closed one. ``total`` counts every
#: pull request of the task whose state is read, so ``more`` is ``total - 1``.
#: A reference that GitHub has not been read for (``state`` NULL) has no word
#: for the chip, so it neither shows nor counts.
_PR_CHIP_SQL = """
SELECT x.task_id, x.ref, x.url, x.title, x.state, x.checks_state, x.checks_summary,
       x.merged_at, x.total
  FROM (
    SELECT r.task_id, r.ref, r.url, r.title, r.state, r.checks_state,
           r.checks_summary, r.merged_at,
           row_number() OVER (
             PARTITION BY r.task_id
             ORDER BY CASE r.state WHEN 'open' THEN 0 WHEN 'draft' THEN 0
                                   WHEN 'merged' THEN 1 ELSE 2 END,
                      COALESCE(r.pr_updated_at, r.updated_at) DESC,
                      r.created_at DESC, r.id DESC) AS rn,
           count(*) OVER (PARTITION BY r.task_id) AS total
      FROM pm_task_refs r
     WHERE r.task_id = ANY(CAST(:ids AS uuid[]))
       AND r.kind = 'pull_request' AND r.provider = 'github'
       AND r.state IN ('open', 'draft', 'merged', 'closed')
  ) x
 WHERE x.rn = 1
"""


def _checks_summary(value: Any) -> dict[str, int] | None:
    """The checks counts as a plain object of integers, or ``None``."""
    if isinstance(value, str):
        import json

        try:
            value = json.loads(value)
        except ValueError:
            return None
    if not isinstance(value, dict):
        return None
    return {str(k): int(v) for k, v in value.items()
            if isinstance(v, int) and not isinstance(v, bool)}


def _pr_chip(hit: Any) -> dict[str, Any]:
    repo, _, number = str(hit.ref).rpartition("#")
    merged = getattr(hit, "merged_at", None)
    return {
        "repo": repo,
        "number": int(number) if number.isdigit() else None,
        "state": hit.state,
        "checks_state": hit.checks_state,
        "checks_summary": _checks_summary(hit.checks_summary),
        "url": hit.url,
        # Beyond the shape of the spec: the hover shows the title (EA3-41),
        # and the strip's "merged this week" button reads the merge time.
        "title": hit.title,
        "merged_at": merged.isoformat() if merged is not None else None,
        "more": max(int(hit.total) - 1, 0),
    }


async def attach_pr_chip(db: Any, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fill each row's ``pr``: ``{repo, number, state, checks_state,
    checks_summary, url, title, merged_at, more}``, or ``None`` for a task with
    no pull request reference. ONE query for the whole list, never one for each
    row (EA-13d, P5). The session is bound to the tenant, so a reference of
    another organization is out of reach under RLS (P3)."""
    for row in rows:
        row["pr"] = None
    ids = [str(r["id"]) for r in rows if r.get("id")]
    if not ids:
        return rows
    found = (await db.execute(text(_PR_CHIP_SQL), {"ids": ids})).fetchall()
    by_task = {str(r.task_id): r for r in found}
    for row in rows:
        hit = by_task.get(str(row["id"]))
        if hit is not None:
            row["pr"] = _pr_chip(hit)
    return rows


#: The ``before`` of a read at this moment (see :func:`attach_agent_worked`).
_READ_HORIZON = datetime(9999, 1, 1, tzinfo=UTC)


async def attach_agent_worked(db: Any, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fill each row's ``agent_worked`` through the ONE predicate of
    ``agent_worked.py`` (EA-13e, EA3-49), read now. One query for the list."""
    from gateway.routes.projects import agent_worked

    for row in rows:
        row["agent_worked"] = False
    ids = [str(r["id"]) for r in rows if r.get("id")]
    if not ids:
        return rows
    # "Now" is every row that exists: no row is created in the future, and a
    # client clock that runs ahead of the database's cannot hide a fresh run.
    worked = await agent_worked.worked_among(db, ids, _READ_HORIZON)
    for row in rows:
        row["agent_worked"] = str(row.get("id")) in worked
    return rows
