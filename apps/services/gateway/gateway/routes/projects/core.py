"""Projects routes — the shared kernel.

The leaf module: it imports nothing from its siblings. It owns the shared
``router``, the Pydantic models, the row→model mapper, the SQL helpers, the
**visibility read model**, the status-transition helper and the event seam.
Spec: ``project-docs/specs/project_management_app.md`` §3 and §4 (WS-27a).

Four things here are load-bearing and worth stating once:

**The engine.** This package makes **zero** ``create_async_engine`` calls. It
consumes ``gateway.db`` — the shared seam BO-10 asked for, which WS-26a built
and proved by converting ``routes/tasks/core.py`` onto it. Adding engine
thirteen is the failure mode this seam exists to prevent.

**Visibility is grant-based, and it is a DATA boundary, not a nav one.** Unlike
``routes/crm`` — where D-CRM-3 deliberately made records org-visible to feature
holders — a project is visible only when a grant on it *or on one of its
ancestors* matches the caller (D-PM-3). Center slices are the whole point of
this app, so the scoping could not be deferred. A caller who cannot see a
project gets **404, never 403** (R5), which is why every loader in this package
takes the visibility clause rather than checking after the fetch.

**Sort keys are an allowlist, never interpolation.** Every identifier reaching
an f-string here is one of ours — a table name, a key of :data:`TASK_SORTS`, a
field name declared on a Pydantic model. Every caller value is a bound
parameter. An unknown sort key is a 422, not a slower query.

**One status transition, three effects.** ``apply_status_transition`` writes the
new ``status_id``, stamps or clears ``completed_at`` on the done boundary, and
records a ``status_change`` activity. A PATCH that writes only the column looks
correct in the UI and silently empties the timeline — the CRM learned this in
``pipeline.apply_status_transition`` and the rule is the same here.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from acb_auth import UserContext, require_feature_router
from acb_common import get_logger
from fastapi import APIRouter, HTTPException, Query

# The shared seam (BO-10 → MT-1c/H2). `_tenant_session` IS
# `acb_common.db.tenant_session`, aliased per-package for the same reason
# `_get_db` was: every submodule imports it from here BY NAME, which is the
# seam `tests/unit/_projects_fakes.bind_db` patches per module. The tenant
# comes from the request context — bound once in `_with_resolved_access` —
# so no call site passes one (H2). A call outside a bound request raises
# `TenantUnbound` rather than defaulting: fail closed, never "the usual org".
# ⚠️ It reads as UNUSED inside this module — it is a re-export. `ruff --fix`
# deletes it and takes 25 test modules with it; do not let a linter "tidy" it.
from gateway.db import tenant_session as _tenant_session
from pydantic import BaseModel
from sqlalchemy import text

_log = get_logger("gateway.projects")

router = APIRouter(
    prefix="/projects", tags=["projects"],
    dependencies=[require_feature_router("projects")],
)

#: `pm_projects.status` — the RUN STATE axis (D-PM-25), mirrored from the CHECK
#: in migration 146 as widened by 171. Kept here so a bad value is a 422 at the
#: boundary rather than an IntegrityError 500.
#:
#: ⚠️ The comment here used to say "migration 145". It was 146. Corrected while
#: widening the tuple, because a mirror that names the wrong source is a mirror
#: nobody can check.
#:
#: `archived` is RETAINED and is not a run state. It is the expand half of R6:
#: the deploy applies migrations before restarting services, so the old gateway
#: runs against the new schema for a window and must keep validating the value
#: it still knows. Nothing writes it after 171's backfill, and it leaves both
#: this tuple and the CHECK in a later release — see 171's header for the
#: trigger. Use :data:`RUN_STATES` for anything that means "the lifecycle axis".
PROJECT_STATUSES: tuple[str, ...] = (
    "queued", "active", "on_hold", "stopped", "done", "archived",
)

#: The run-state axis proper, in lifecycle order, WITHOUT the retained
#: `archived`. This is what a picker offers and what a hue map keys off; the
#: UI labels `active` "Ongoing" and `on_hold` "Paused" (D-PM-25 — the stored
#: values are not renamed, because R6 forbids renaming in place and `active` is
#: the DEFAULT on every existing row).
RUN_STATES: tuple[str, ...] = ("queued", "active", "on_hold", "stopped", "done")

#: The ONE run state in which a project's automation may act on its own.
#:
#: 🔴 This is deliberately a single shared constant rather than three guards.
#: Before it, three automation paths would each have corrupted data the moment
#: `on_hold` started meaning anything (§9.8.2):
#:
#:   1. :func:`automation.run_lifecycle_sweep` walks every root with a policy and
#:      moves stale OPEN tasks into the closing lane with **no project-status
#:      predicate at all** — so a project paused for a quarter under a 3-month
#:      policy has its backlog auto-cancelled as `system:workflow:<id>`, through
#:      the ordinary transition, indistinguishable from a person doing it.
#:   2. Recurrence advances a series when a task closes, minting new work into a
#:      project nobody is working.
#:   3. `pm.task.assigned` → agent dispatch puts an agent to work in one.
#:
#: Three copies of this rule is the CLAUDE.md §5 defect ("do not invent a second
#: way to do an existing thing") authored three times in one ticket.
#:
#: `queued` is excluded on purpose: work that has not started is not work an
#: automation should advance. `done`/`stopped` are excluded because closing the
#: leftovers of a finished or abandoned project is exactly what D-PM-26's
#: explicit **offer** covers — a user's act, audited, not a sweeper's.
RUNNABLE_STATUSES: frozenset[str] = frozenset({"active"})

#: SQL for :data:`RUNNABLE_STATUSES`, applied to a `pm_projects` alias.
#:
#: Both halves are required and they are different axes (D-PM-25): a project is
#: runnable when it is `active` **and** not filed. An archived project is out of
#: every default surface, so advancing its work would be automation acting on
#: rows the product has stopped showing.
def runnable_project_clause(alias: str = "p") -> str:
    """A predicate restricting ``alias`` (a ``pm_projects`` row) to runnable."""
    return f"{alias}.status = 'active' AND {alias}.archived_at IS NULL"


def is_runnable(project: Any) -> bool:
    """The Python half of :func:`runnable_project_clause`, same two axes.

    Takes a row or a mapping so it serves both a fetched record and a payload.

    ⚠️ **This judges ONE row and knows nothing about its ancestors.** For any
    decision about whether a task's work is live, use
    :func:`is_runnable_with_ancestors` — see the defect note there.
    """
    if project is None:
        return False
    get = project.get if isinstance(project, dict) else lambda k: getattr(project, k, None)
    return get("status") in RUNNABLE_STATUSES and get("archived_at") is None


#: One project's ancestor chain, itself included, reduced to a single verdict.
#:
#: `bool_and` over the chain: the whole chain must be runnable for the answer to
#: be true, which is "the most restrictive ancestor wins" expressed as SQL.
#: NULL-safe — `bool_and` of an empty chain is NULL, which :func:`
#: is_runnable_with_ancestors` reads as False, so a project id that resolves to
#: nothing fails closed rather than running work in a project nobody can find.
_ANCESTOR_RUNNABLE_SQL = """
WITH RECURSIVE chain AS (
    SELECT id, parent_project_id, status, archived_at
      FROM pm_projects WHERE id = CAST(:pid AS uuid)
    UNION ALL
    SELECT p.id, p.parent_project_id, p.status, p.archived_at
      FROM pm_projects p JOIN chain c ON p.id = c.parent_project_id
)
SELECT bool_and(status = 'active' AND archived_at IS NULL) FROM chain
"""


async def is_runnable_with_ancestors(db: Any, project_id: Any) -> bool:
    """Is this project runnable, **and every project above it**?

    🔴 **This exists because the WS-27bg slice-1 guards were inconsistent with
    each other, and with the UI.** Measured 2026-08-13 on a real database: the
    lifecycle sweep reads the ROOT project's state, while the recurrence spawn
    and the agent dispatch each read the task's IMMEDIATE project. So a task in
    an *active* subproject beneath a *paused* root was skipped by the sweep and
    **still spawned successors and still dispatched agents** — while
    `ProjectTree` drew that very subproject as "Paused — inherited from a parent
    project". The product said paused and the automation ran.

    A project's state governs its whole subtree, exactly as a
    `pm_project_grants` row on a root does (§3.2), and
    `app/projects/lib/tree.ts::effectiveState` is the same rule on the client.
    Three places computing "is this work live" from three different rows is the
    CLAUDE.md §5 defect; this is the one answer.

    ⚠️ Still derives, still writes nothing (D-PM-26). The walk is bounded by the
    tree's real depth, which the cycle check keeps finite.
    """
    if not project_id:
        return False
    row = (await db.execute(
        text(_ANCESTOR_RUNNABLE_SQL), {"pid": str(project_id)},
    )).scalar()
    return bool(row)

#: `pm_task_statuses.category` — the machine-readable half of a status. Name and
#: colour are the owner's; this is what completion, the personal mirror (§6.1)
#: and automation gates (§6.3) key off. `triage` (WS-27u, migration 164) is the
#: parked-at-the-front-door value that :func:`triage_exclusion_clause` keys off.
STATUS_CATEGORIES: tuple[str, ...] = (
    "backlog", "todo", "in_progress", "done", "cancelled", "triage",
)

#: The one category the default list reads exclude (WS-27u). A constant rather
#: than a literal in the clause below so the vocabulary word and the predicate
#: cannot drift apart silently.
TRIAGE_CATEGORY = "triage"

#: Categories that close a task: crossing INTO one stamps ``completed_at``,
#: crossing out clears it. ``cancelled`` counts as closed — a cancelled task is
#: not outstanding work, and leaving it open would keep it in every "what is
#: still due" read forever.
CLOSING_CATEGORIES: frozenset[str] = frozenset({"done", "cancelled"})

#: `pm_activities.type` — mirrored from the migrations, and the mirror is
#: CHECKED (`test_projects_activity_vocabulary`).
#:
#: It went out of step once and the consequence was total: migration 150 added
#: `attachment` to the database's CHECK, this tuple was not updated, and
#: ``record_activity`` refuses an unknown type BEFORE the insert — so every
#: file upload to a project task answered 422 "Unknown activity type
#: 'attachment'" and rolled back. The 25 attachment tests all passed, because
#: they monkeypatch ``record_activity`` and so never exercised the vocabulary
#: it guards. A tuple that mirrors a migration by hand needs a test that reads
#: the migration; anything else is a comment claiming to be an invariant.
ACTIVITY_TYPES: tuple[str, ...] = (
    "comment", "status_change", "field_change", "link", "assignment",
    "agent_run", "sync", "system", "attachment", "mention",
)

#: `pm_projects.source` / `pm_tasks.source`. Tasks carry two extra origins.
PROJECT_SOURCES: tuple[str, ...] = ("manual", "import", "agent")
TASK_SOURCES: tuple[str, ...] = ("manual", "import", "email", "agent", "automation")

#: Node kinds in the projects tree (migration 193). Mirrors the CHECK the
#: migration adds — test_projects_node_kind.py reads the SQL file, because a
#: hand-mirrored constraint without that test is a comment claiming to be an
#: invariant (the migration-150 lesson).
NODE_KINDS: tuple[str, ...] = ("project", "folder")

#: The depth cap on PROJECT generations: space=1, project=2, subproject=3.
#: Folders are transparent to this count and never extend it.
MAX_PROJECT_GENERATIONS = 3


def node_kind(value: object) -> str:
    """Resolve a row's kind. NULL reads as 'project' (R6 expand)."""
    return str(value or "project")


def assert_node_grammar(
    *,
    kind: str,
    parent_kind: str | None,
    parent_generation: int,
    subtree_depth: int = 1,
) -> None:
    """The tree grammar, as one pure function (owner directive 2026-08-31).

    space (root) → [folder] → project → [folder] → subproject — and stop.

    ``parent_kind`` is None at a root. ``parent_generation`` counts the
    parent's PROJECT ancestors, itself included (a folder reports its
    nearest project ancestor's count). ``subtree_depth`` is the longest
    project chain inside the node being placed, itself included — 1 for a
    new project, 0 for a new folder, larger when a move carries a subtree.

    Pure on purpose: create and move both call it, and the hermetic suite
    can exercise every refusal without a database (R8 stays honest — the
    SQL that FEEDS these numbers is proven against a real ladder).
    """
    if kind == "folder":
        if parent_kind is None:
            raise HTTPException(
                status_code=422,
                detail="A folder cannot be a space. Create it inside a "
                       "space or a project.",
            )
        if parent_kind == "folder":
            raise HTTPException(
                status_code=422, detail="A folder cannot hold another folder.",
            )
        # max(_, 1): an EMPTY folder still reserves one generation for the
        # children it exists to hold — a folder under a subproject could
        # legally contain nothing, which is a refusal, not a placement.
        if parent_generation + max(subtree_depth, 1) > MAX_PROJECT_GENERATIONS:
            raise HTTPException(
                status_code=422,
                detail="Too deep: a folder here could only hold nodes below "
                       "the subproject level, and subprojects are the floor.",
            )
        return
    if parent_generation + subtree_depth > MAX_PROJECT_GENERATIONS:
        raise HTTPException(
            status_code=422,
            detail="A subproject is the lowest level — it cannot contain "
                   "projects. Break the work down with tasks and subtasks.",
        )

#: The one system task type. Its only rule — an Epic cannot have a parent —
#: makes it structurally the root level (§3.4). There is deliberately no
#: 'Subtask' type: a subtask is a task with a parent.
EPIC_TYPE_NAME = "Epic"

#: How far up a parent chain a cycle check walks before refusing. Paca's bound,
#: for Paca's reason: a real hierarchy never approaches it, and an unbounded
#: walk over corrupted data is an unbounded query.
MAX_DEPTH = 50

MAX_PAGE_SIZE = 100

#: The permission that opens the whole portfolio — every project, granted or
#: not. D14 measured `data:org:read` at **zero** consumers, so "manager has
#: org-wide visibility" was a name; this is deliberately its first, which is why
#: granting it is registered as an owner gate.
ORG_READ = "data:org:read"

#: What a caller whose email the directory does not know is told when they try
#: to CREATE something. Reads never say this — they simply see nothing (§D-MT-1).
NO_ORGANIZATION = "Your account is not attached to an organization."


# ── Models ──────────────────────────────────────────────────────────────────
#
# Output model field names are the table's column names, 1:1, so `row_to_model`
# maps any row generically — a column added to a table and its model needs no
# mapper edit, and a column added to only one shows up as a missing field rather
# than a silently dropped value. Input models are all-optional: the same model
# serves POST and PATCH, with create-time requirements checked at the call site.

class ProjectModel(BaseModel):
    id: str
    name: str
    description: str | None = None
    parent_project_id: str | None = None
    #: 'project' or 'folder' (migration 193). NULL rows read as 'project' —
    #: resolve through `node_kind`, never `row.kind` directly.
    kind: str | None = None
    task_prefix: str | None = None
    status: str = "active"
    lead: str | None = None
    position: float | None = None
    source: str = "manual"
    clickup_id: str | None = None
    clickup_kind: str | None = None
    # WS-27z — the lifecycle policy (migration 166). ROOT rows only carry a
    # meaningful value; the sweep reads the root's and the subtree inherits.
    archive_after_months: int | None = None
    close_after_months: int | None = None
    timezone: str = "UTC"
    created_by: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    archived_at: str | None = None
    #: WS-27bg (migration 171) — which project's archive filed this row. Itself
    #: when archived directly, an ancestor when swept in with its subtree. It is
    #: what makes unarchive reversible; see the migration header.
    archived_root_id: str | None = None


class ProjectIn(BaseModel):
    name: str | None = None
    description: str | None = None
    parent_project_id: str | None = None
    # Create-time only: the write path refuses a kind change on PATCH — a
    # folder full of subprojects becoming a project would dodge the grammar.
    kind: str | None = None
    task_prefix: str | None = None
    status: str | None = None
    lead: str | None = None
    position: float | None = None
    source: str | None = None
    # WS-27z — settable on ROOT projects only (the write path refuses a child).
    archive_after_months: int | None = None
    close_after_months: int | None = None
    timezone: str | None = None


class TaskModel(BaseModel):
    id: str
    project_id: str
    root_project_id: str
    task_number: int | None = None
    parent_task_id: str | None = None
    type_id: str | None = None
    status_id: str
    title: str
    description: str | None = None
    importance: int | None = None
    estimate_mins: int | None = None
    start_date: str | None = None
    due_at: str | None = None
    completed_at: str | None = None
    tags: list[str] = []
    #: WS-27l — values keyed by `pm_custom_fields.field_key`. Always present,
    #: never null: "no custom values" and "they have not loaded" must not be the
    #: same thing to a client.
    custom_fields: dict = {}
    created_by: str | None = None
    source: str = "manual"
    clickup_id: str | None = None
    clickup_synced_at: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    archived_at: str | None = None


class TaskIn(BaseModel):
    project_id: str | None = None
    parent_task_id: str | None = None
    type_id: str | None = None
    status_id: str | None = None
    title: str | None = None
    description: str | None = None
    importance: int | None = None
    estimate_mins: int | None = None
    start_date: str | None = None
    due_at: str | None = None
    tags: list[str] | None = None
    custom_fields: dict | None = None
    source: str | None = None


class StatusModel(BaseModel):
    id: str
    project_id: str
    name: str
    color: str = "gray"
    position: int = 0
    category: str = "todo"
    is_default: bool = False


class TypeModel(BaseModel):
    id: str
    #: ``None`` is ORG-WIDE (WS-27bj, migration 175) — and it is on the wire
    #: because a client cannot otherwise tell a row it may edit from one that
    #: belongs to the whole organization, which is exactly the difference
    #: between an enabled pencil and a 409.
    project_id: str | None = None
    name: str
    icon: str | None = None
    color: str | None = None
    is_default: bool = False
    is_system: bool = False
    #: WS-27ae / P-28 (migration 168). Carries §3.4's root-level rule. Read by
    #: `core.is_epic_type`, written through `admin.create_type`/`patch_type`,
    #: and on the wire so a picker can tell a member WHY a type refuses a
    #: parent instead of surfacing it as an unexplained 422.
    is_epic: bool = False


class ActivityModel(BaseModel):
    id: str
    task_id: str | None = None
    project_id: str | None = None
    type: str
    body: str | None = None
    meta: dict | None = None
    created_by: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class GrantModel(BaseModel):
    id: str
    project_id: str
    subject: str
    created_by: str | None = None
    created_at: str | None = None


class ViewModel(BaseModel):
    id: str
    project_id: str
    name: str
    view_type: str = "list"
    config: dict | None = None
    position: float | None = None
    created_by: str | None = None


class ListResponse(BaseModel):
    """The one list shape every collection endpoint returns."""

    rows: list[dict]
    total: int


class Page:
    """Pagination, declared once and bound by every paginated route.

    A FastAPI class dependency rather than two repeated parameters per handler,
    for the CRM's reason (``routes/crm/records.py::ListParams``): the contract
    is supposed to be the same everywhere, and the way that stops being true is
    one endpoint quietly growing a different cap. ``le=`` enforces the ceiling
    here, so a caller asking for 10 000 rows gets a 422 rather than a slow
    answer.

    It also keeps the routes **callable directly** — a bare ``page: int =
    Query(1)`` default is a ``Query`` object, not an ``int``, so the hermetic
    tests could not call the handler without FastAPI resolving it first.
    """

    def __init__(
        self,
        page: int = Query(1, ge=1),
        page_size: int = Query(50, ge=1, le=MAX_PAGE_SIZE),
    ) -> None:
        self.page = page
        self.page_size = page_size

    @property
    def limit(self) -> int:
        return self.page_size

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


#: The deterministic tail EVERY sort ends with (WS-27w item 4, P-6). Without
#: it two tasks that tie on the sort key have no total order, and a tie
#: straddling a page boundary appears on both pages — or neither — depending
#: on the plan. ``{dir}`` is the direction slot the endpoint formats in.
SORT_TIEBREAK = "t.created_at {dir}, t.id {dir}"

#: :data:`STATUS_CATEGORIES` in lifecycle order, as a SQL array literal for the
#: semantic status sort. Built from the tuple rather than written twice, so the
#: rank can never drift from the vocabulary the CHECK mirrors.
_CATEGORY_RANK_ARRAY = "ARRAY[" + ", ".join(f"'{c}'" for c in STATUS_CATEGORIES) + "]"

#: Wire sort key → the ORDER BY fragment it may use, with ``{dir}`` as the
#: direction slot. This dict IS the allowlist: anything not a key here is a
#: 422, never a silent fall back to the default.
#:
#: Two rules, both pinned structurally (``test_projects_hardening``):
#:
#: * ``status`` is SEMANTIC — category rank in lifecycle order, then the lane's
#:   own board position, never the status NAME (P-6). Alphabetical status sort
#:   puts "Backlog" before "Done" only by accident of language, and every lane
#:   rename reshuffles the list.
#: * every entry ends with :data:`SORT_TIEBREAK`, so the order is total and
#:   pagination never straddles a tie.
TASK_SORTS: dict[str, str] = {
    "created_at": SORT_TIEBREAK,
    "updated_at": f"t.updated_at {{dir}} NULLS LAST, {SORT_TIEBREAK}",
    "due_at": f"t.due_at {{dir}} NULLS LAST, {SORT_TIEBREAK}",
    "importance": f"t.importance {{dir}} NULLS LAST, {SORT_TIEBREAK}",
    "title": f"t.title {{dir}}, {SORT_TIEBREAK}",
    "task_number": f"t.task_number {{dir}} NULLS LAST, {SORT_TIEBREAK}",
    "completed_at": f"t.completed_at {{dir}} NULLS LAST, {SORT_TIEBREAK}",
    "status": (
        f"(SELECT array_position({_CATEGORY_RANK_ARRAY}, s.category)"
        f" FROM pm_task_statuses s WHERE s.id = t.status_id) {{dir}} NULLS LAST, "
        f"(SELECT s.position FROM pm_task_statuses s WHERE s.id = t.status_id)"
        f" {{dir}} NULLS LAST, {SORT_TIEBREAK}"
    ),
}

DIRECTIONS: dict[str, str] = {"asc": "ASC", "desc": "DESC"}

#: Columns declared ``JSONB`` in migration 145. asyncpg has no codec for a bare
#: Python dict, so these are serialized here and cast in the statement.
JSONB_COLUMNS: frozenset[str] = frozenset({
    "meta", "config", "clickup_snapshot",
    # WS-27l. Same rule, same reason — a bare dict has no asyncpg codec,
    # so it is serialized here and cast in the statement.
    "custom_fields",
    # The personal overlay's Waiting-For subject, {name, email} (188, WS-39
    # S3a-server-2). Genuinely nullable — "I am not waiting on anyone" is the
    # normal state — so it stays OUT of JSONB_OBJECT_COLUMNS below, where an
    # absent value would read as `{}` and put every task on the Waiting list.
    "waiting_on",
})

#: JSONB columns declared ``NOT NULL DEFAULT '{}'``, where absent means an
#: EMPTY object rather than null. Kept apart from the rest because the
#: distinction is real: ``meta`` genuinely has a "no meta" state, whereas
#: "this task has no custom values" and "they have not loaded" must not read
#: the same to a client (migration 155).
JSONB_OBJECT_COLUMNS: frozenset[str] = frozenset({"custom_fields"})

#: ``TEXT[]``. asyncpg binds a Python list natively, so these must NOT go
#: through the jsonb path — serializing one would store the literal string
#: '["a"]' in a text array column.
ARRAY_COLUMNS: frozenset[str] = frozenset({"tags"})

TIMESTAMP_COLUMNS: frozenset[str] = frozenset({
    "due_at", "completed_at", "archived_at", "clickup_synced_at",
    # The personal overlay's instants (147). Same rule, same reason: bare
    # `text()` declares no column type, so an ISO string would arrive at a
    # timestamptz as text.
    "defer_until", "clarified_at",
    # The intake wrapper's reappearance instant (164, WS-27u).
    "snoozed_until",
    # The personal overlay's SCHEDULED BLOCK (187, WS-39 S3a). Four more
    # instants, same rule. ⚠️ Omitting them is not a cosmetic slip: the block
    # would be bound as TEXT to a timestamptz, which is the precise failure this
    # function's docstring describes — and the hermetic fake would keep agreeing,
    # because a fake stores whatever it is handed (R8).
    "scheduled_start", "scheduled_end", "actual_start", "actual_end",
    # The personal overlay's WAITING-FOR instants (188, WS-39 S3a-server-2).
    # Three more, and the same trap one slice later: `expected_by` is the date
    # somebody PROMISED the work by, so it is the one the overdue badge and the
    # nudge scheduler both compare against `now()`. Bound as TEXT it would
    # neither store nor compare.
    "delegated_at", "expected_by", "last_nudged_at",
})
DATE_COLUMNS: frozenset[str] = frozenset({"start_date"})


# ── Wire conversion ─────────────────────────────────────────────────────────

def wire(value: Any) -> Any:
    """One DB value → its JSON-safe form. UUIDs and instants become strings."""
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def from_jsonb(value: Any) -> Any:
    """A ``JSONB`` column as the driver hands it back.

    Raw ``text()`` over asyncpg returns jsonb as a **string** — there is no
    declared column type to decode against — so a model field typed ``dict``
    would reject it. Same rule, same reason, as ``routes/crm/core.py``.
    """
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return None
    return value


def row_to_model(row: Any, model: type[BaseModel]) -> Any:
    data: dict[str, Any] = {}
    for name in model.model_fields:
        raw = getattr(row, name, None)
        if name in JSONB_COLUMNS:
            parsed = from_jsonb(raw)
            if parsed is None and name in JSONB_OBJECT_COLUMNS:
                parsed = {}
            data[name] = parsed
        elif name in ARRAY_COLUMNS:
            data[name] = list(raw) if raw is not None else []
        else:
            data[name] = wire(raw)
    return model(**data)


def row_to_dict(row: Any, model: type[BaseModel]) -> dict[str, Any]:
    return row_to_model(row, model).model_dump()


def actor(user: Any) -> str:
    """The acting identity, from the authenticated context only (R3/R4).

    Never from a query parameter or a body field: ``created_by`` is what the
    timeline attributes an action to, and a client-supplied one would let a
    caller write history in somebody else's name.
    """
    email = (getattr(user, "email", None) or "").strip()
    return email or "anonymous"


def now() -> datetime:
    """The one clock this package reads, so tests can freeze it in one place."""
    return datetime.now(UTC)


def clean_payload(payload: BaseModel) -> dict[str, Any]:
    """A PATCH body → only the fields the caller actually sent.

    ``exclude_unset`` and not ``exclude_none``: sending ``null`` is how a client
    clears a field, and collapsing the two would make "unset" and "clear" the
    same request.
    """
    return payload.model_dump(exclude_unset=True)


# ── Visibility — the grant read model (D-PM-3) ──────────────────────────────

#: The caller's `group:<slug>` subjects. Joined through `app_user` because the
#: grant subject names a group and the session carries an email — and matched
#: case-insensitively on both sides (R10).
_MY_GROUPS_SQL = """
SELECT 'group:' || g.slug AS subject
FROM org_group g
JOIN org_group_member m ON m.group_id = g.id
JOIN app_user au ON au.id = m.user_id
WHERE lower(au.email) = :email AND au.status = 'active'
"""

#: The caller's TENANT (WS-29a/b, D-MT-1 (a)). One person belongs to exactly one
#: organization, so `X-User-Email` alone resolves it and no request carries a
#: tenant discriminator. `app_user.email` is globally UNIQUE, which is what makes
#: this a single-row answer rather than a choice the caller could influence.
#:
#: A person with no `app_user` row resolves to NULL, and every clause below then
#: matches nothing — see :attr:`Visibility.organization_id`.
_MY_ORGANIZATION_SQL = """
SELECT au.organization_id AS organization_id
FROM app_user au
WHERE lower(au.email) = :email AND au.status = 'active'
"""

#: Every project in the caller's organization, ignoring grants. This is what
#: ``data:org:read`` means AFTER WS-29b: unrestricted **within a tenant**.
_TENANT_PROJECTS_SQL = """
SELECT id FROM pm_projects WHERE organization_id = CAST(:vis_org AS uuid)
"""

#: Projects the caller may see: those carrying a matching grant, plus everything
#: beneath them. The recursion descends from the granted seeds rather than
#: walking each project's ancestry upward — same answer, and it visits a subtree
#: once instead of once per descendant.
#:
#: ⚠️ **`g.organization_id = :vis_org` IS THE SINGLE MOST DANGEROUS LINE IN THIS
#: PACKAGE** (multi_tenancy.md §6). `subject = 'org'` means "everybody", and
#: until a second organization exists that is correct. The moment one is
#: onboarded, an un-tenanted `subject = 'org'` grant hands every project in the
#: deployment to every caller in it. The predicate is on the GRANT row rather
#: than joined through `pm_projects` because D-MT-3 put the key on every table
#: precisely so this needs no join.
#:
#: ⚠️ The parentheses around the three subject arms are load-bearing. Without
#: them `AND` binds tighter than `OR` and the tenant filter would apply to the
#: `subject = 'org'` arm alone — leaving the email and group arms unscoped,
#: which is the same leak wearing a subtler hat.
#:
#: The recursive step repeats the tenant filter. The trigger in migration 161
#: already makes a cross-tenant parent impossible, so this is defence in depth:
#: the closure must not be the thing that would leak if that trigger were ever
#: dropped.
_VISIBLE_PROJECTS_SQL = """
WITH RECURSIVE granted AS (
    SELECT DISTINCT g.project_id AS id
    FROM pm_project_grants g
    WHERE g.organization_id = CAST(:vis_org AS uuid)
      AND (g.subject = 'org'
           OR lower(g.subject) = :vis_email
           OR g.subject = ANY(:vis_groups))
    UNION
    SELECT p.id
    FROM pm_projects p
    JOIN granted a ON p.parent_project_id = a.id
    WHERE p.organization_id = CAST(:vis_org AS uuid)
)
SELECT id FROM granted
"""


@dataclass(frozen=True)
class Visibility:
    """What one caller may see, rendered as a reusable SQL fragment.

    ``unrestricted`` is the ``data:org:read`` holder — the People Center's
    full-portfolio view. For everyone else, :attr:`clause` is a subquery over
    the grant closure and callers ``AND`` it into their own WHERE.

    ⚠️ **`unrestricted` means unrestricted WITHIN A TENANT, never across them.**
    Before WS-29b both clause helpers answered the literal ``TRUE`` for this
    caller, which was correct while the deployment had one organization and is a
    whole-database leak the moment it has two. Every arm of every clause below
    now carries the tenant, including this one.
    """

    unrestricted: bool
    email: str
    groups: tuple[str, ...]
    #: The caller's tenant, or ``None`` for somebody with no ``app_user`` row.
    #:
    #: ``None`` FAILS CLOSED and does so by construction rather than by a check:
    #: every clause compares a column to ``CAST(:vis_org AS uuid)``, and SQL's
    #: ``column = NULL`` is NULL, never true. A caller the directory does not
    #: know sees nothing — which is the right answer for a mention recipient or
    #: a service identity that was never onboarded, and the wrong answer to give
    #: by accident, so it is stated here.
    organization_id: str | None = None

    @property
    def params(self) -> dict[str, Any]:
        # `vis_org` is bound even when unrestricted, because the unrestricted
        # clause is no longer `TRUE` — it is the tenant.
        if self.unrestricted:
            return {"vis_org": self.organization_id}
        return {
            "vis_email": self.email,
            "vis_groups": list(self.groups),
            "vis_org": self.organization_id,
        }

    def project_clause(self, column: str = "id") -> str:
        """A predicate restricting ``column`` (a project id) to the visible set."""
        if self.unrestricted:
            return f"{column} IN ({_TENANT_PROJECTS_SQL})"
        return f"{column} IN ({_VISIBLE_PROJECTS_SQL})"


async def resolve_organization_id(db: Any, email: str) -> str | None:
    """The tenant one email belongs to, or ``None`` if the directory has no row.

    One lookup per request, on the seam every app already reads. D-MT-1 (a) is
    what makes it a lookup rather than a negotiation: the answer cannot depend on
    anything the caller sends.
    """
    clean = (email or "").strip().lower()
    if not clean:
        return None
    row = (await db.execute(
        text(_MY_ORGANIZATION_SQL), {"email": clean},
    )).fetchone()
    organization_id = getattr(row, "organization_id", None) if row else None
    return str(organization_id) if organization_id is not None else None


async def resolve_visibility(db: Any, user: UserContext) -> Visibility:
    """Read the caller's authority once per request.

    ``data:org:read`` short-circuits the group lookup: an unrestricted caller's
    groups cannot change the answer, and asking anyway would put a join on every
    portfolio read.

    ⚠️ It no longer short-circuits the TENANT lookup, and the order here is the
    whole point: the organization is resolved BEFORE the permission is consulted,
    because ``data:org:read`` widens a caller inside their organization and must
    not be able to widen them out of it.
    """
    email = actor(user).lower()
    organization_id = await resolve_organization_id(db, email)
    if user is not None and user.has_permission(ORG_READ):
        return Visibility(
            unrestricted=True, email="", groups=(),
            organization_id=organization_id,
        )
    rows = (await db.execute(text(_MY_GROUPS_SQL), {"email": email})).fetchall()
    return Visibility(
        unrestricted=False,
        email=email,
        groups=tuple(r.subject for r in rows if getattr(r, "subject", None)),
        organization_id=organization_id,
    )


#: One member's effective permissions, assembled the way `/auth/me` assembles
#: them: role grants plus per-user overrides carrying their effect.
_EFFECTIVE_PERMISSIONS_SQL = """
SELECT rp.permission AS permission, 'allow' AS effect, TRUE AS from_role
  FROM app_user au
  JOIN user_role ur           ON ur.user_id = au.id
  JOIN org_role_permission rp ON rp.role_id = ur.role_id
 WHERE lower(au.email) = :email AND au.status = 'active'
UNION ALL
SELECT o.permission AS permission, o.effect AS effect, FALSE AS from_role
  FROM app_user au
  JOIN user_permission_override o ON o.user_id = au.id
 WHERE lower(au.email) = :email AND au.status = 'active'
"""


async def resolve_visibility_for(db: Any, email: str) -> Visibility:
    """What SOMEBODY ELSE may see — the same answer, for a person who is not
    the caller.

    Needed because notifications are addressed to third parties (WS-27j): before
    telling someone they were mentioned on a task, we have to know they can open
    it, since the notification carries the task's title.

    ``resolve_visibility`` cannot serve this: it reads ``UserContext``, and the
    recipient of a mention has no request in flight. So the recipient's grants
    are read from the tables ``/auth/me`` reads, and then handed to the REAL
    ``build_access`` — the matcher stays one implementation, which is what keeps
    a wildcard like ``*`` or ``data:*`` resolving the same way here as it does
    on the request path. Re-deriving that in SQL is how two answers to "may they
    see this" start disagreeing.

    A person with no ``app_user`` row is not unrestricted and is in no groups:
    a directory-only colleague (§2's two-store split) can be assigned work and
    can be mentioned, and simply has nothing to open it with.
    """
    from acb_auth import build_access

    clean = (email or "").strip().lower()
    if not clean:
        return Visibility(unrestricted=False, email="", groups=())
    # Same order as `resolve_visibility`, for the same reason: the tenant is
    # resolved before the permission, so `data:org:read` cannot widen a
    # recipient out of their own organization. A directory-only colleague with
    # no `app_user` row resolves to None and sees nothing.
    organization_id = await resolve_organization_id(db, clean)
    rows = (await db.execute(
        text(_EFFECTIVE_PERMISSIONS_SQL), {"email": clean},
    )).fetchall()
    access = build_access(
        [r.permission for r in rows if getattr(r, "from_role", False)],
        [(r.permission, r.effect) for r in rows
         if not getattr(r, "from_role", False)],
    )
    # `.has()` is EffectiveAccess's own decision method — the one `UserContext.
    # has_permission` delegates to. Same allow/deny precedence, same wildcard
    # matching, one implementation.
    if access.has(ORG_READ):
        return Visibility(
            unrestricted=True, email="", groups=(),
            organization_id=organization_id,
        )
    group_rows = (await db.execute(
        text(_MY_GROUPS_SQL), {"email": clean},
    )).fetchall()
    return Visibility(
        unrestricted=False,
        email=clean,
        groups=tuple(r.subject for r in group_rows
                     if getattr(r, "subject", None)),
        organization_id=organization_id,
    )


def require_organization(vis: Visibility) -> str:
    """The caller's tenant, or 403 — for the writes that must DECIDE one.

    Only the creation of a ROOT ``pm_projects`` row reaches this. Everything
    else beneath a project inherits the tenant from its parent in the database
    (migration 161's ``pm_organization_from_parent`` trigger), which is what
    keeps the tenant a single decision instead of a thing 43 INSERT sites each
    have to remember — D-MT-2 (b)'s named failure mode, and the one this system
    demonstrably has.

    403 and not 404 (the R5 rule for *records*): this says nothing about what
    exists. It is the caller's own account that is not set up, and a 404 here
    would send somebody hunting for a project that was never created.
    """
    if not vis.organization_id:
        raise HTTPException(status_code=403, detail=NO_ORGANIZATION)
    return vis.organization_id


async def require_organization_of(db: Any, email: str) -> str:
    """:func:`require_organization` for a caller who has no ``Visibility``.

    The personal-project seam and both importers create root projects without
    ever building one — they are helpers reached from a route that has already
    authorized the caller, and growing them a ``Visibility`` parameter would
    push the tenant decision back out to each of their call sites, which is the
    opposite of the point.
    """
    organization_id = await resolve_organization_id(db, email)
    if not organization_id:
        raise HTTPException(status_code=403, detail=NO_ORGANIZATION)
    return organization_id


async def load_visible_project(
    db: Any, vis: Visibility, project_id: str,
) -> Any:
    """One project the caller may see, or 404.

    404 and not 403 (R5): "no such project" and "not yours" must be the same
    answer, or the error code becomes an oracle for what exists in another
    department.
    """
    row = (await db.execute(
        text(
            f"SELECT * FROM pm_projects "
            f"WHERE id = CAST(:project_id AS uuid) AND {vis.project_clause()}"
        ),
        {"project_id": project_id, **vis.params},
    )).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return row


async def load_visible_task(db: Any, vis: Visibility, task_id: str) -> Any:
    """One task the caller may see, or 404.

    Two ways in, and the second is not a convenience: a task **assigned to the
    caller** is always visible even when its project is not granted to them.
    Delegation across a Center boundary is normal — somebody in Operations is
    asked to do one thing for Finance — and without this rule that task would
    404 for the very person expected to do it.
    """
    row = (await db.execute(
        text(
            "SELECT t.* FROM pm_tasks t "
            f"WHERE t.id = CAST(:task_id AS uuid) AND {task_visibility_clause(vis)}"
        ),
        {"task_id": task_id, **vis.params},
    )).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return row


async def assert_assignable_here(
    db: Any, project_id: str, added: set[str],
) -> None:
    """Assigning somebody else requires the task to be in a REAL project.

    Owner directive 2026-08-26: *"when assigning a task to another team member,
    it must be placed under a specific project in the Projects app to ensure
    proper visibility."*

    ⚠️ What this is NOT. It is not a visibility fix. Assignment already grants
    task-level visibility on purpose — ``task_visibility_clause`` has a second
    arm matching ``pm_task_assignees``, added by WS-27j precisely so that
    delegating outward stops being a silent no-op. The assignee CAN open a task
    assigned to them in a project they hold no grant on, and that stays true.
    Assigning across a ``group:`` boundary in a TEAM project is therefore left
    alone: it works, and it was made to work deliberately.

    What it fixes is ownership, not access. A task owned by somebody else,
    sitting inside YOUR personal tree:

      * is incoherent — your Areas organise your work, and this is not your work
      * rides your lifecycle — archiving a project archives its whole subtree
        (``tree.py``), so filing away "Kitchen reno" would file away work that
        belongs to a colleague
      * gives them a project breadcrumb they cannot open, forever

    So the refusal is narrow by design: only when the task lives in a personal
    project, and only for people who are not its owner. The fix offered is
    always the same and always available — move it to a real project first,
    which ``POST /projects/tasks/{id}/move`` already does properly.
    """
    if not added:
        return
    # Takes a PROJECT id, not a task. `move_task` has to ask about the
    # DESTINATION — which is the whole point of letting promote-and-assign be
    # one call — and a signature taking a task row would have forced a fake one
    # at that call site. A helper whose parameter has to be faked is a helper
    # asking for the wrong thing.
    row = (await db.execute(
        text("SELECT personal_owner FROM pm_projects WHERE id = CAST(:pid AS uuid)"),
        {"pid": str(project_id)},
    )).fetchone()
    owner = (getattr(row, "personal_owner", None) or "").lower() or None
    if owner is None:
        return  # an ordinary project — assign whoever you like
    outsiders = sorted(who for who in added if who.lower() != owner)
    if not outsiders:
        return  # the owner assigning themselves is how capture already works
    raise HTTPException(
        status_code=422,
        detail=(
            f"This task is in a personal project, so it cannot be assigned to "
            f"{', '.join(outsiders)}. Move it to a project first — a task "
            f"somebody else owns should not live in your private space, where "
            f"they cannot see its project and where archiving your own work "
            f"would file theirs away with it."
        ),
    )


async def assert_move_keeps_privacy(
    db: Any, task: Any, new_project_id: str,
) -> None:
    """A task may only move INTO a personal project it already lives in.

    The rule in one sentence, covering all five combinations:

        team    -> team              allowed
        personal(mine) -> team       allowed  — this is PROMOTION (D53.4)
        team    -> personal          REFUSED
        personal(mine) -> personal(mine, other Area)   allowed
        personal(mine) -> personal(somebody else's)    REFUSED

    Why refuse, when the same person could see both projects anyway. Because
    moving a task into a personal tree is not an organising act, it is a
    TAKING act: `tree.py`'s project list filters `personal_owner IS NULL`, so
    the task leaves the company board, and everyone whose access came from a
    grant on the old project loses it. Every other way to get a task off a
    board — archive, delete, move to another team project — leaves a timeline
    entry and can be undone. This one would leave nothing at all.

    ⚠️ And the organising need it *looks* like it serves is already served.
    Somebody who wants to hold a team task their own way has the whole
    per-member overlay for it (D53.7/D53.8): their own disposition, context,
    energy and scheduled block, none of which the rest of the team sees. So
    refusing costs a member nothing they cannot already do, which is what
    makes this a cheap rule rather than a trade-off.

    Deliberately does NOT consult the caller. An unrestricted (`data:org:read`)
    admin can see every project, and this is not an access question — the act is
    incoherent whoever performs it. Phrasing it on the two PROJECTS rather than
    on the actor is also what lets an admin tidy somebody's own tree without a
    special case.
    """
    rows = (await db.execute(
        text(
            "SELECT id, personal_owner FROM pm_projects "
            "WHERE id IN (CAST(:old AS uuid), CAST(:new AS uuid))"
        ),
        {"old": str(task.project_id), "new": str(new_project_id)},
    )).fetchall()
    owners = {str(r.id): (r.personal_owner or "").lower() or None for r in rows}
    destination = owners.get(str(new_project_id))
    if destination is None:
        return  # moving into team work is always fine
    if owners.get(str(task.project_id)) == destination:
        return  # already inside that same person's private tree
    raise HTTPException(
        status_code=422,
        detail=(
            "That project is personal, so a task cannot be moved into it from "
            "outside. Personal projects are private — moving this task there "
            "would remove it from everyone who can currently see it, with no "
            "record. To take it off the board, archive it instead; to organise "
            "it your own way, use your own disposition, context and schedule, "
            "which nobody else sees."
        ),
    )


def task_visibility_clause(vis: Visibility, alias: str = "t") -> str:
    """The task-list counterpart of :meth:`Visibility.project_clause`.

    Same two ways in as :func:`load_visible_task`, so a task cannot be listable
    and unreadable (or the reverse) — the two would drift the moment one is
    edited alone. ``load_visible_task`` no longer writes its own copy of this
    for exactly that reason: it had one, and one copy of a two-armed predicate
    is how the arms stop matching.

    ⚠️ **The tenant is composed ABOVE the grant closure, never inside it**
    (multi_tenancy.md §6: "with the tenant predicate composed above the grant
    closure rather than tangled into it"). The outer ``AND`` is not redundant
    with the closure's own tenant filter — it is what scopes the SECOND arm:

    ``pm_task_assignees.assignee`` is a bare email (D-PM-4) matched by string,
    and nothing stops a member of organization B typing a member of A's address
    into it. Without this outer AND that row would make A's member see B's task,
    through the escape hatch rather than through a grant. That is a third leak,
    beside the two §6 names, and it is only visible if you read the arms
    separately.
    """
    tenant = f"{alias}.organization_id = CAST(:vis_org AS uuid)"
    if vis.unrestricted:
        # `data:org:read` is the whole portfolio OF ONE ORGANIZATION. This
        # answered the literal `TRUE` before WS-29b.
        return tenant
    return (
        f"({tenant}"
        f" AND ({alias}.project_id IN ({_VISIBLE_PROJECTS_SQL})"
        f"      OR EXISTS (SELECT 1 FROM pm_task_assignees a"
        f"                 WHERE a.task_id = {alias}.id"
        f"                   AND lower(a.assignee) = :vis_email)))"
    )


def triage_exclusion_clause(alias: str = "t") -> str:
    """The default-list exclusion (WS-27u): triage-parked tasks are invisible.

    A captured task is real from birth — an ordinary ``pm_tasks`` row — but it
    is PARKED: its status carries the ``triage`` category, and until a human
    rules on it it must appear on **no** board, list, calendar, timeline or
    search read unless the caller passed ``include_triage=true``.

    **This is the one copy of the predicate.** Every list surface appends this
    helper's answer when ``include_triage`` is false, rather than writing the
    clause itself — the §11.16 lesson restated for a WHERE fragment: two
    hand-written copies are how one surface quietly starts leaking the queue.
    The parameter-coverage test in ``test_projects_intake.py`` holds the other
    half (no surface may silently drop the flag).

    Joined through the status row rather than denormalised onto tasks, for
    ``build_task_filters``' reason: the category is the status's property, and
    a copy on the task would need re-stamping whenever a lane is recategorised.
    ``NOT EXISTS`` rather than ``NOT IN`` so a task whose status row somehow
    vanished stays visible — fail open into sight, never into a task nobody
    can find.
    """
    return (
        f"NOT EXISTS (SELECT 1 FROM pm_task_statuses s_triage"
        f" WHERE s_triage.id = {alias}.status_id"
        f" AND s_triage.category = '{TRIAGE_CATEGORY}')"
    )


# ── SQL helpers ─────────────────────────────────────────────────────────────
#
# Every identifier reaching an f-string below is one of ours: a literal table
# name, or a key of a dict built from a Pydantic model's declared fields. Caller
# values are always bound parameters.

def _parse(parser: Any, value: str, column: str, what: str) -> Any:
    try:
        return parser(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"'{column}' is not a valid ISO-8601 {what}: {value!r}.",
        ) from exc


def coerce_write_values(values: dict[str, Any]) -> dict[str, Any]:
    """Request-shaped values → driver-shaped values. ONE choke point.

    Every write in this package goes through :func:`insert_row` or
    :func:`update_row`, so this runs on all of them and a new endpoint inherits
    it. Bare ``text()`` declares no column types to asyncpg, so without this an
    ISO string arrives at a ``timestamptz`` and a dict at a ``jsonb``. A
    malformed instant answers **422 naming the column** rather than surfacing as
    a driver error the caller cannot act on.
    """
    out = dict(values)
    for column, value in values.items():
        if value is None or not isinstance(value, str):
            continue
        if column in TIMESTAMP_COLUMNS:
            out[column] = _parse(datetime.fromisoformat, value, column, "instant")
        elif column in DATE_COLUMNS:
            out[column] = _parse(date.fromisoformat, value, column, "date")
    return out


def _placeholder(column: str) -> str:
    """The bind expression for one column — jsonb needs the cast, nothing else."""
    return f"CAST(:{column} AS jsonb)" if column in JSONB_COLUMNS else f":{column}"


def _bindable(values: dict[str, Any]) -> dict[str, Any]:
    out = coerce_write_values(values)
    for column in JSONB_COLUMNS & out.keys():
        if out[column] is not None and not isinstance(out[column], str):
            out[column] = json.dumps(out[column])
    return out


async def insert_row(db: Any, table: str, values: dict[str, Any]) -> Any:
    columns = list(values)
    placeholders = ", ".join(_placeholder(c) for c in columns)
    return (await db.execute(
        text(
            f"INSERT INTO {table} ({', '.join(columns)}) "
            f"VALUES ({placeholders}) RETURNING *"
        ),
        _bindable(values),
    )).fetchone()


async def update_row(
    db: Any, table: str, record_id: str, values: dict[str, Any],
    *, touch: bool = True,
) -> Any:
    assignments = [f"{c} = {_placeholder(c)}" for c in values]
    if touch:
        assignments.append("updated_at = now()")
    return (await db.execute(
        text(
            f"UPDATE {table} SET {', '.join(assignments)} "
            f"WHERE id = CAST(:record_id AS uuid) RETURNING *"
        ),
        {**_bindable(values), "record_id": record_id},
    )).fetchone()


async def load_row(db: Any, table: str, record_id: str) -> Any | None:
    return (await db.execute(
        text(f"SELECT * FROM {table} WHERE id = CAST(:id AS uuid)"),
        {"id": record_id},
    )).fetchone()


async def require_row(db: Any, table: str, record_id: str, what: str) -> Any:
    row = await load_row(db, table, record_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"{what} not found")
    return row


# ── The write precondition — D-PM-20 / WS-27bi ───────────────────────────────
#
# ``If-Match: <updated_at>``. No ``version`` column: ``update_row`` already moves
# ``updated_at`` on every write that matters, and a second monotonic fact about
# one row is the CLAUDE.md §5 defect.
#
# ⚠️ The two rules below are not stylistic — both were MEASURED against a real
# database (spec §9.10.1), and both are cases where the obvious implementation
# reports a safety it does not have:
#
#   1. A NAIVE (offset-stripped) token must be REFUSED, not accepted. asyncpg
#      reinterprets a tz-less datetime in the session zone, so on a UTC box a
#      stripped offset silently compares EQUAL. It would pass every test and
#      begin mis-comparing the moment the session TZ moved.
#   2. The token is never rendered by pg's ``::text`` and never string-compared.
#      Postgres trims trailing zeros (``.1``) where the JSON encoder does not
#      (``.100000``): equal as instants, different as strings. Comparing the
#      parsed ``datetime`` objects is exact, and comparing the strings passes
#      every test written against ordinary microseconds.


def parse_precondition(token: str) -> datetime:
    """Parse an ``If-Match`` token, refusing anything that cannot compare safely."""
    raw = token.strip()
    if raw.startswith('"') and raw.endswith('"') and len(raw) > 1:
        raw = raw[1:-1]          # tolerate the quoted ETag spelling
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="If-Match must be an ISO-8601 timestamp with a UTC offset, "
                   "as returned in the row's 'updated_at'.",
        ) from None
    if parsed.tzinfo is None:
        # Rule 1. This is the whole point of the header being measured.
        raise HTTPException(
            status_code=400,
            detail="If-Match must carry a UTC offset. A timestamp without one "
                   "is ambiguous and would compare against the wrong instant.",
        )
    return parsed


def require_precondition(row: Any, if_match: str | None, current: dict) -> None:
    """Enforce ``If-Match`` against ``row.updated_at``.

    An ABSENT header still succeeds (D-PM-20): advisory now, mandatory in a later
    release. Made compulsory on day one it would break every existing caller at
    once, which is the failure this decision exists to prevent — R6's
    expand-then-tighten discipline applied to an API rather than a schema.
    """
    # Absent means absent. ⚠️ Not merely ``is None``: FastAPI only resolves a
    # ``Header(...)`` default through the HTTP layer, so an endpoint called
    # DIRECTLY -- which most of this package's tests do -- receives the sentinel
    # object itself. Treating that as a supplied token turned 18 existing tests
    # into 400s the first time this was wired.
    if not isinstance(if_match, str):
        return
    expected = parse_precondition(if_match)
    if row.updated_at != expected:          # rule 2: instants, never strings
        raise HTTPException(
            status_code=412,
            detail={
                "error": "precondition_failed",
                "message": "This row changed since you loaded it.",
                "current": current,
            },
        )


async def count_where(db: Any, table: str, column: str, value: str) -> int:
    total = (await db.execute(
        text(f"SELECT count(*) FROM {table} WHERE {column} = CAST(:value AS uuid)"),
        {"value": value},
    )).scalar()
    return int(total or 0)


# ── Org-wide vocabularies — WS-27bj / D-PM-16 ───────────────────────────────
#
# Migration 175 made ``project_id`` nullable on ``pm_task_types``,
# ``pm_custom_fields`` and ``pm_tags``. **NULL means org-wide**; a value keeps
# meaning the root project it always meant. A project's EFFECTIVE vocabulary is
# ``org-wide ∪ root-local``, and root-local **shadows** org-wide on the same
# identity.
#
# ⚠️ For tags the shadowing is a CORRECTNESS rule, not a preference.
# ``pm_tasks.tags`` stores display TEXT, not a foreign key (migration 156), so an
# org-wide "bug" and a root-local "bug" are the *same tag on every task* while
# being two registry rows with two colours. A union that returned both would make
# "what colour is this tag" unanswerable — and whichever row a renderer happened
# to hit first would be the answer, which is the bug that looks like a flicker.
#
# One seam, three tables, deliberately: this package already carries one status
# vocabulary, one visibility clause and one task store, and a second way to scope
# a vocabulary is the CLAUDE.md §5 defect.

#: Each vocabulary table → how a row's identity WITHIN one scope is written, over
#: the **column** and over the **bound value**. Each mirrors the rule that table
#: already had before 175 (`lower(name)` for tags, `name` for types, `field_key`
#: for fields) rather than inventing a new normalisation, and each is one half of
#: migration 175's index pair.
#:
#: ⚠️ The two halves are ONE entry so they cannot drift. Lowering the column but
#: not the parameter matches nothing and reports "no such tag" — a silent wrong
#: answer rather than an error, which is the shape that survives review.
VOCABULARY_IDENTITY: dict[str, tuple[str, str]] = {
    "pm_task_types": ("name", ":value"),
    "pm_custom_fields": ("field_key", ":value"),
    "pm_tags": ("lower(name)", "lower(:value)"),
}


def vocabulary_scope(alias: str = "") -> str:
    """The WHERE arm selecting one project's effective vocabulary.

    Binds ``:root`` and nothing else, on purpose — see below.

    ⚠️ **The tenant is composed onto the org-wide arm explicitly**, exactly as
    :func:`task_visibility_clause` composes it above the grant closure. The
    root-local arm is anchored by ``project_id`` — a project the caller was
    already shown — but ``project_id IS NULL`` is anchored by *nothing* on its
    own. Leaving it to RLS alone would make one forgotten ``FORCE ROW LEVEL
    SECURITY`` on one table the difference between a tenant's private vocabulary
    and every tenant's, and that is not a failure a test of this endpoint would
    show. Two independent fences, on purpose.

    The tenant is read from ``:root``'s own project row rather than taken as a
    second parameter, and that is the safer of the two: it makes the clause a
    pure function of the root, so no caller can compose it correctly-but-without
    the tenant. Every one of them has already put ``:root`` through
    :func:`load_visible_project`, so the anchor is a row the caller was
    demonstrably allowed to see — a stronger fact than an org id passed
    alongside. The cost is a primary-key point read on an already-planned query.
    """
    project = f"{alias}.project_id" if alias else "project_id"
    org = f"{alias}.organization_id" if alias else "organization_id"
    return (
        f"({project} = CAST(:root AS uuid)"
        f" OR ({project} IS NULL AND {org} = ("
        f"      SELECT p.organization_id FROM pm_projects p"
        f"       WHERE p.id = CAST(:root AS uuid))))"
    )


async def org_wide_exists(db: Any, table: str, root: str, value: Any) -> bool:
    """Does the tenant already hold an ORG-WIDE row with this identity?

    Needed because :func:`shadowed` deliberately hides the org-wide row when a
    root-local one covers it, so a create path that checked the effective list
    would not see the row its INSERT is about to collide with — and migration
    175's ``uq_*_org_*`` index would answer that with an IntegrityError, i.e. a
    500 where a 409 naming the clash belongs.

    ``table`` indexes :data:`VOCABULARY_IDENTITY`, so the interpolated fragments
    come from that literal map and never from caller input.
    """
    column, bind = VOCABULARY_IDENTITY[table]
    row = (await db.execute(
        text(
            f"SELECT 1 FROM {table} "
            f" WHERE project_id IS NULL AND {column} = {bind} "
            f"   AND organization_id = (SELECT p.organization_id FROM pm_projects p"
            f"                           WHERE p.id = CAST(:root AS uuid))"
        ),
        {"root": root, "value": value},
    )).fetchone()
    return row is not None


def is_org_wide(row: Any) -> bool:
    """Is this vocabulary row the tenant's rather than one project's?"""
    return getattr(row, "project_id", None) is None


def shadowed(rows: list[Any], identity: Callable[[Any], Any]) -> list[Any]:
    """``org-wide ∪ root-local`` collapsed so each identity appears **once**.

    Root-local wins. Pure, and separate from the SQL, because the interesting
    case is the one a query would hide: the same identity arriving twice. A
    ``DISTINCT ON`` would do it in the database and would be perfectly correct —
    it is written here instead so the tie-break can be asserted directly rather
    than inferred from an ``ORDER BY`` somebody may later "tidy" into the wrong
    order. The row counts are bounded (40 fields, 500 tags), so this costs
    nothing.

    Incoming order is preserved, and a shadowed row keeps the POSITION its
    org-wide twin held rather than jumping to where the local row sorted: the
    list is alphabetical by name and the two share a name, so the position is the
    same either way — but stating it means a later reorder cannot quietly become
    a reshuffle.
    """
    winners: dict[Any, Any] = {}
    order: list[Any] = []
    for row in rows:
        key = identity(row)
        if key not in winners:
            winners[key] = row
            order.append(key)
        elif is_org_wide(winners[key]) and not is_org_wide(row):
            winners[key] = row
    return [winners[key] for key in order]


def refuse_org_wide_write(row: Any, what: str) -> None:
    """Refuse a per-project mutation aimed at an org-wide row.

    ⚠️ **This is a guard, not a policy choice.** Every rename/merge/delete path
    in this package reads ``str(existing.project_id)`` and hands it to a
    ``CAST(:root AS uuid)``. For an org-wide row that is ``str(None)`` — the
    literal string ``"None"`` — which Postgres answers with an unhandled cast
    error, i.e. a 500 on a route that should have said no. Refusing by name is
    what turns that into an answer.

    Managing org-wide vocabularies is explicitly out of scope for WS-27bj
    (§9.11): the seam lands first, the admin surface follows. Until it does, an
    org-wide row is created deliberately and edited nowhere, which is the
    conservative half of "ship dark".
    """
    if is_org_wide(row):
        raise HTTPException(
            status_code=409,
            detail=(
                f"'{getattr(row, 'name', what)}' is an organization-wide {what} "
                "and cannot be changed from inside one project. Org-wide "
                "vocabularies are managed for the whole organization."
            ),
        )


#: The permission that may write the tenant's shared vocabulary.
#:
#: An org-wide row lands in **every project in the organization**, including ones
#: the writer cannot see. That crosses the visibility boundary the rest of this
#: package is built to respect, so it is not enough to hold a grant on the
#: project the request came through. ``admin:settings:manage`` is owner/admin
#: only (migration 130 line 201) — a manager holds ``data:org:read`` and does not
#: get this.
ORG_VOCABULARY_WRITE = "admin:settings:manage"

#: The env switch spelling this package inherits from ``ACTION_BROKER_ENFORCE``.
_ORG_VOCABULARY_FLAG = "PROJECTS_ORG_VOCABULARIES"

_TRUTHY = frozenset({"1", "on", "true", "yes"})


def org_vocabularies_enabled() -> bool:
    """Is the affordance that CREATES an org-wide row released? Default **OFF**.

    Ship dark (§9.11, `engineering_practice.md` §2): the flag gates the create,
    never the read union. Creating an org-wide row is the half that is hard to
    walk back — it appears in every project at once, and un-creating it means
    deciding what happens to the tasks that started using it — whereas reading a
    union that is empty until something creates a row is a no-op by construction.

    Read at CALL time, not import time, so the flip is a restart rather than a
    release and a test can set it around one call. Same idiom as
    ``routes/tasks/providers.py``'s ``ACTION_BROKER_ENFORCE``.
    """
    import os

    return (os.environ.get(_ORG_VOCABULARY_FLAG) or "").strip().lower() in _TRUTHY


def require_known_tenant(vis: Visibility, what: str) -> None:
    """Refuse to mint an org-wide row for a caller with no resolved tenant.

    ``Visibility.organization_id`` is ``None`` for somebody the directory has no
    ``app_user`` row for — a mention recipient, a service identity nobody
    onboarded. For READS that fails closed by construction (``column = NULL`` is
    never true, so they see nothing). A WRITE has no such luck: the insert would
    reach ``organization_id NOT NULL`` and surface as a 500 on a request that was
    simply not answerable.
    """
    if vis.organization_id is None:
        raise HTTPException(
            status_code=403,
            detail=f"An organization-wide {what} needs a caller the directory "
                   f"knows; this account has no organization.",
        )


def require_org_vocabulary_write(user: Any) -> None:
    """Both gates for minting an org-wide row: the flag, then the permission."""
    if not org_vocabularies_enabled():
        # 403 rather than R5's 404, and the distinction is worth stating: R5
        # makes 404 the answer so an error code cannot become an oracle for what
        # exists in another department. A dark FEATURE is not a resource and
        # leaks nothing about anybody — answering 404 here would only send a
        # caller hunting for a project that is sitting right in front of them.
        raise HTTPException(
            status_code=403,
            detail="Organization-wide vocabularies are not enabled here.",
        )
    if not (user is not None and user.has_permission(ORG_VOCABULARY_WRITE)):
        raise HTTPException(
            status_code=403,
            detail=(
                "An organization-wide entry applies to every project in the "
                "organization, so it needs organization settings permission."
            ),
        )


# ── Hierarchy — the two self-FKs, and their only rules ──────────────────────

async def root_project_id(db: Any, project_id: str) -> str:
    """Walk to the root of ``project_id``'s tree.

    Bounded by :data:`MAX_DEPTH` for the same reason the cycle checks are: this
    runs on the write path, and a corrupted parent chain must fail loudly rather
    than spin.
    """
    current = project_id
    for _ in range(MAX_DEPTH):
        row = (await db.execute(
            text(
                "SELECT parent_project_id FROM pm_projects "
                "WHERE id = CAST(:id AS uuid)"
            ),
            {"id": current},
        )).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Project not found")
        parent = getattr(row, "parent_project_id", None)
        if parent is None:
            return str(current)
        current = str(parent)
    raise HTTPException(
        status_code=422,
        detail="Project hierarchy is deeper than the supported maximum.",
    )


async def assert_no_project_cycle(
    db: Any, project_id: str, new_parent_id: str | None,
) -> None:
    """Refuse a re-parent that would make a project its own ancestor."""
    if new_parent_id is None:
        return
    if str(new_parent_id) == str(project_id):
        raise HTTPException(
            status_code=422, detail="A project cannot be its own parent.",
        )
    current: str | None = str(new_parent_id)
    for _ in range(MAX_DEPTH):
        if current is None:
            return
        if str(current) == str(project_id):
            raise HTTPException(
                status_code=422,
                detail="That move would put the project inside its own subtree.",
            )
        row = (await db.execute(
            text(
                "SELECT parent_project_id FROM pm_projects "
                "WHERE id = CAST(:id AS uuid)"
            ),
            {"id": current},
        )).fetchone()
        if row is None:
            return
        parent = getattr(row, "parent_project_id", None)
        current = str(parent) if parent is not None else None
    raise HTTPException(
        status_code=422,
        detail="Project hierarchy is deeper than the supported maximum.",
    )


async def assert_no_task_cycle(
    db: Any, task_id: str, new_parent_id: str | None,
) -> None:
    """Refuse a re-parent that would make a task its own ancestor."""
    if new_parent_id is None:
        return
    if str(new_parent_id) == str(task_id):
        raise HTTPException(
            status_code=422, detail="A task cannot be its own parent.",
        )
    current: str | None = str(new_parent_id)
    for _ in range(MAX_DEPTH):
        if current is None:
            return
        if str(current) == str(task_id):
            raise HTTPException(
                status_code=422,
                detail="That move would put the task inside its own subtree.",
            )
        row = (await db.execute(
            text("SELECT parent_task_id FROM pm_tasks WHERE id = CAST(:id AS uuid)"),
            {"id": current},
        )).fetchone()
        if row is None:
            return
        parent = getattr(row, "parent_task_id", None)
        current = str(parent) if parent is not None else None
    raise HTTPException(
        status_code=422,
        detail="Task hierarchy is deeper than the supported maximum.",
    )


def is_epic_type(row: Any) -> bool:
    """Does this ``pm_task_types`` row carry §3.4's root-level rule?

    **The flag first** (WS-27ae / P-28, migration 168): ``is_epic`` is what the
    rule keys off, so a project can name its top level "Initiative" and still
    get the rule. The seed-name arm behind it is kept deliberately and is not
    dead code — it is what an old row looks like in the window between the
    migration applying and the write path stamping the flag, and it is the
    predicate migration 168's backfill copies, so the two cannot disagree about
    a type that existed before the column.

    A user-created type merely *called* "Epic" still does not inherit the rule:
    the second arm keeps its ``is_system`` guard.
    """
    if bool(getattr(row, "is_epic", False)):
        return True
    return (
        bool(getattr(row, "is_system", False))
        and getattr(row, "name", "") == EPIC_TYPE_NAME
    )


async def assert_epic_has_no_parent(
    db: Any, type_id: str | None, parent_task_id: str | None,
) -> None:
    """§3.4's one structural rule: an Epic-typed task cannot have a parent.

    This is what makes Epic the root level without a ``level`` column. Which
    types carry the rule is :func:`is_epic_type`'s answer, in one place.
    """
    if parent_task_id is None or type_id is None:
        return
    row = await load_row(db, "pm_task_types", str(type_id))
    if row is None:
        return
    if is_epic_type(row):
        raise HTTPException(
            status_code=422,
            detail="An Epic cannot have a parent task; it is the top level.",
        )


async def next_task_number(db: Any, root_id: str) -> int:
    """Allocate the next human-readable number for a root project.

    One statement, so two concurrent creates cannot be handed the same number:
    the ``ON CONFLICT DO UPDATE`` re-reads and increments the committed row
    under the same lock that would have rejected the insert.
    """
    row = (await db.execute(
        text(
            "INSERT INTO pm_task_counters (project_id, last_value) "
            "VALUES (CAST(:root AS uuid), 1) "
            "ON CONFLICT (project_id) DO UPDATE "
            "SET last_value = pm_task_counters.last_value + 1 "
            "RETURNING last_value"
        ),
        {"root": root_id},
    )).fetchone()
    return int(getattr(row, "last_value", 1) or 1)


# ── Statuses ────────────────────────────────────────────────────────────────

async def load_default_status(db: Any, root_id: str) -> Any:
    """The status a new task lands in: the flagged default, else the first lane.

    Falling back to the lowest ``position`` rather than failing is deliberate —
    an owner who deletes the row that happened to carry ``is_default`` must not
    discover it by being unable to create a task.
    """
    for clause in ("AND is_default ", ""):
        row = (await db.execute(
            text(
                f"SELECT * FROM pm_task_statuses "
                f"WHERE project_id = CAST(:root AS uuid) {clause}"
                f"ORDER BY position, name LIMIT 1"
            ),
            {"root": root_id},
        )).fetchone()
        if row is not None:
            return row
    raise HTTPException(
        status_code=422,
        detail="No task statuses are configured for this project; create one first.",
    )


async def require_status_in_project(db: Any, root_id: str, status_id: str) -> Any:
    """A status, checked to belong to this project's tree.

    Without the project check a caller could move a task into another
    department's status — which would then render under a lane that project's
    board does not have, and would make the status undeletable there for a
    reason nobody could see.
    """
    row = (await db.execute(
        text(
            "SELECT * FROM pm_task_statuses "
            "WHERE id = CAST(:status_id AS uuid) AND project_id = CAST(:root AS uuid)"
        ),
        {"status_id": status_id, "root": root_id},
    )).fetchone()
    if row is None:
        raise HTTPException(
            status_code=422, detail="That status does not belong to this project.",
        )
    return row


async def apply_status_transition(
    db: Any, task: Any, new_status_id: str, *, created_by: str,
    automation: bool = False,
) -> dict[str, Any]:
    """Move a task to a new status. **Three effects, one helper.**

    1. the new ``status_id``;
    2. ``completed_at`` — stamped when the task crosses INTO a closing category,
       cleared when it crosses back out. Cleared, not left: a reopened task that
       keeps its completion stamp is done according to every report and open
       according to the board;
    3. a ``status_change`` activity naming both ends.

    Every mutator that can move a status calls this — the PATCH route today, the
    sync and the automation action later — because a write that sets only the
    column looks right in the UI and silently empties the timeline.
    """
    old_status = await require_row(
        db, "pm_task_statuses", str(task.status_id), "Status",
    )
    new_status = await require_status_in_project(
        db, str(task.root_project_id), str(new_status_id),
    )

    values: dict[str, Any] = {"status_id": str(new_status.id)}
    was_closed = old_status.category in CLOSING_CATEGORIES
    is_closed = new_status.category in CLOSING_CATEGORIES
    if is_closed and not was_closed:
        values["completed_at"] = now()
    elif was_closed and not is_closed:
        values["completed_at"] = None

    row = await update_row(db, "pm_tasks", str(task.id), values)
    await record_activity(
        db,
        activity_type="status_change",
        created_by=created_by,
        task_id=str(task.id),
        body=f"{old_status.name} → {new_status.name}",
        meta={
            "from": old_status.name,
            "to": new_status.name,
            "from_category": old_status.category,
            "to_category": new_status.category,
        },
        automation=automation,
    )

    # WS-27o — a task crossing INTO a closing category is what advances a
    # recurring series. Done here rather than in each caller because this is the
    # one place that knows the crossing happened: the board, My work, an
    # automation and a bulk edit all arrive through this helper, and a second
    # call site would be a fifth way to finish a task that forgets to recur.
    #
    # Imported inside the function so `core` — the leaf every feature module
    # imports — gains no dependency on one of them.
    successor: str | None = None
    if is_closed and not was_closed:
        from gateway.routes.projects.recurrence import spawn_successor

        successor = await spawn_successor(db, row, actor_id=created_by)

    return {
        "row": row, "from": old_status, "to": new_status,
        "recurred_to": successor,
    }


# ── The satellite bump (WS-27ae / P-27) ─────────────────────────────────────

async def touch_task(db: Any, *task_ids: Any) -> None:
    """Bump ``pm_tasks.updated_at`` for tasks whose SATELLITE rows changed.

    P-27's prerequisite, and the reason the delta feed is not a lie: a task's
    assignees, links, comments and attachments live in their own tables, so a
    feed ordered by ``pm_tasks.updated_at`` misses every edit a user plainly
    considers "the task changed". Plane hit this and bumps the parent from its
    activity task; we bump from the same spine (:func:`record_activity`) plus
    the handful of satellite writes that legitimately record no activity.

    Bounded and idempotent: ids are de-duplicated, blanks dropped, and an empty
    call issues no statement at all — so a caller does not have to guard it.

    ⚠️ ``now()`` is TRANSACTION start time, which is exactly what is wanted: a
    satellite write and this bump land on the same instant inside one request,
    so a client cannot receive the parent row stamped before the child change
    that caused it.
    """
    ids = sorted({str(t) for t in task_ids if t})
    if not ids:
        return
    await db.execute(
        text(
            "UPDATE pm_tasks SET updated_at = now() "
            "WHERE id = ANY(CAST(:touch_ids AS uuid[]))"
        ),
        {"touch_ids": ids},
    )


# ── The activity spine ──────────────────────────────────────────────────────

async def record_activity(
    db: Any,
    *,
    activity_type: str,
    created_by: str,
    task_id: str | None = None,
    project_id: str | None = None,
    body: str | None = None,
    meta: dict[str, Any] | None = None,
    automation: bool = False,
) -> Any:
    """Write one timeline row.

    The migration's CHECK requires a target, and this refuses first so the
    failure names the caller's mistake instead of surfacing as an
    IntegrityError 500 from the driver.

    ``automation=True`` (WS-27z) stamps ``meta.automation``, the one flag the
    timeline renders automated entries distinctly by. A FLAG, not a new
    activity type or a fourth actor shape: the actor stays
    ``system:workflow:<id>`` inside the one vocabulary (D-PM-4), and the row
    stays whatever type the change earns — a status move by a sweep is still a
    ``status_change``. Human writes never pass it, so their meta is unchanged.
    """
    if task_id is None and project_id is None:
        raise HTTPException(
            status_code=422,
            detail="An activity must name a task or a project.",
        )
    if activity_type not in ACTIVITY_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown activity type '{activity_type}'.",
        )
    if automation:
        meta = {**(meta or {}), "automation": True}
    # WS-27ae / P-27 — the satellite bump, at the ONE choke point rather than at
    # thirty call sites. An activity naming a task IS the statement "this task
    # changed", so anything that earns a timeline entry earns a bump: comments,
    # assignment, links, attachments, status moves, automation. The three
    # satellite writes that record no activity call `touch_task` themselves and
    # `test_projects_delta.py` fences the list.
    #
    # A project-level activity bumps nothing — there is no task to bump, and
    # `pm_projects.updated_at` is not what any feed reads.
    await touch_task(db, task_id)
    return await insert_row(db, "pm_activities", {
        "type": activity_type,
        "task_id": task_id,
        "project_id": project_id,
        "body": body,
        "meta": meta,
        "created_by": created_by,
    })


def diff_changes(before: Any, after: Any, fields: tuple[str, ...]) -> list[dict]:
    """The ``field_change`` payload: what actually moved, old and new.

    Paca's shape, and the reason a change is revertible from the timeline
    without a second audit store. Fields whose value did not change are omitted
    — a diff that lists every column makes the one edit that mattered
    unfindable.
    """
    changes: list[dict] = []
    for name in fields:
        old, new = wire(getattr(before, name, None)), wire(getattr(after, name, None))
        if old != new:
            changes.append({"field": name, "old": old, "new": new})
    return changes


#: FK-valued fields a ``field_change`` may record, and where each one's human
#: label lives: field → (table, label column). WS-27w item 2 (P-5): a change
#: entry that stores only the UUID renders as a UUID the moment the row it
#: points at is renamed or deleted, so labels are resolved AT WRITE TIME and
#: stored beside the ids — history survives a lane rename without a join.
#:
#: ``status_id`` is here even though a status move is a TRANSITION with its own
#: activity type: a future call site that diffs it anyway must still resolve
#: labels rather than store bare ids.
FK_LABEL_FIELDS: dict[str, tuple[str, str]] = {
    "status_id": ("pm_task_statuses", "name"),
    "type_id": ("pm_task_types", "name"),
    "parent_task_id": ("pm_tasks", "title"),
    "project_id": ("pm_projects", "name"),
    "parent_project_id": ("pm_projects", "name"),
}

#: Fields whose consecutive same-actor edits COALESCE into the prior activity
#: row instead of appending (WS-27w item 3, P-5): an autosaving editor
#: otherwise writes dozens of rows for one editing session, and a timeline
#: that is 40 lines of "edited description" buries the one change that
#: mattered.
COALESCED_FIELDS: frozenset[str] = frozenset({"description"})


async def _label_of(db: Any, table: str, column: str, row_id: Any) -> Any:
    """One FK target's display label — ``None`` for a cleared or deleted end."""
    if row_id is None:
        return None
    row = await load_row(db, table, str(row_id))
    return None if row is None else wire(getattr(row, column, None))


async def resolve_fk_labels(db: Any, changes: list[dict]) -> list[dict]:
    """Rewrite FK-valued diff entries to the five-key shape the timeline owes.

    ``{field, old, new}`` stays for plain values; an entry whose field is in
    :data:`FK_LABEL_FIELDS` becomes ``{field, old_id, new_id, old_label,
    new_label}`` with the labels read NOW, while the referenced rows still
    exist. Every ``field_change`` write goes through
    :func:`record_field_change` and therefore through here — the structural
    test in ``test_projects_hardening`` is what keeps that sentence true.
    """
    out: list[dict] = []
    for change in changes:
        source = FK_LABEL_FIELDS.get(str(change.get("field") or ""))
        if source is None:
            out.append(change)
            continue
        table, column = source
        old_id, new_id = change.get("old"), change.get("new")
        out.append({
            "field": change.get("field"),
            "old_id": old_id,
            "new_id": new_id,
            "old_label": await _label_of(db, table, column, old_id),
            "new_label": await _label_of(db, table, column, new_id),
        })
    return out


async def _coalescible_prior(
    db: Any, *, created_by: str, changes: list[dict],
    task_id: str | None, project_id: str | None,
    automation: bool = False,
) -> Any | None:
    """The activity row this edit folds into, or ``None`` to append normally.

    Consecutive means exactly what WS-27w says: the IMMEDIATELY previous
    activity row for this task (or project) is the same actor editing the same
    lone field. Anything in between — a comment, an assignment, another
    field's change, somebody else's edit — breaks the run, because the
    timeline must still show that those happened in that order.

    A prior row whose meta carries anything beside ``changes`` (a revert's
    ``reverted_activity_id``) is a statement of its own and is never coalesced
    into or over. The one exception is WS-27z's ``automation`` flag, which
    rides beside ``changes`` on every automated write: an automation that
    rewrites a description every run must keep coalescing (the WS-27f rule),
    so an automation-flagged prior folds an automation-flagged edit — and only
    that. A human edit never folds into an automated row or vice versa, even
    under the same actor string, because the flag is part of what the row
    asserts.
    """
    if len(changes) != 1 or str(changes[0].get("field") or "") not in COALESCED_FIELDS:
        return None
    if task_id is not None:
        clause, target = "task_id = CAST(:target AS uuid)", task_id
    elif project_id is not None:
        clause, target = "project_id = CAST(:target AS uuid)", project_id
    else:
        return None
    row = (await db.execute(
        text(
            f"SELECT * FROM pm_activities WHERE {clause} "
            "AND deleted_at IS NULL "
            "ORDER BY created_at DESC, id DESC LIMIT 1"
        ),
        {"target": str(target)},
    )).fetchone()
    if row is None or getattr(row, "type", None) != "field_change":
        return None
    prior_actor = str(getattr(row, "created_by", "") or "").strip().lower()
    if prior_actor != (created_by or "").strip().lower():
        return None
    meta = from_jsonb(getattr(row, "meta", None))
    if not isinstance(meta, dict) or set(meta) - {"automation"} != {"changes"}:
        return None
    if bool(meta.get("automation")) != automation:
        return None
    prior = [c for c in (meta.get("changes") or []) if isinstance(c, dict)]
    if len(prior) != 1 or str(prior[0].get("field") or "") != str(changes[0]["field"]):
        return None
    return row


async def record_field_change(
    db: Any,
    *,
    created_by: str,
    changes: list[dict],
    task_id: str | None = None,
    project_id: str | None = None,
    extra_meta: dict[str, Any] | None = None,
    automation: bool = False,
) -> Any:
    """Write one ``field_change`` activity — THE one door (WS-27w items 2+3).

    Every caller that records a field change comes through here, and the
    structural test walks the package's ``record_activity`` call sites to
    refuse any that do not. That single-door shape is what the two rules hang
    off:

    1. **labels at write time** — :func:`resolve_fk_labels` runs on every
       write, so an FK-valued change can never reach the table as a bare pair
       of UUIDs;
    2. **description coalescing** — a same-actor consecutive edit of a
       :data:`COALESCED_FIELDS` field UPDATES the prior row (its span of
       ``old`` → latest ``new``, and its timestamp) instead of appending.

    ``extra_meta`` rides beside ``changes`` in the meta object; a write that
    carries any (a revert naming ``reverted_activity_id``) is always appended,
    never coalesced — it is an assertion about history, not an edit in a run.
    """
    resolved = await resolve_fk_labels(db, changes)
    if extra_meta is None:
        prior = await _coalescible_prior(
            db, created_by=created_by, changes=resolved,
            task_id=task_id, project_id=project_id, automation=automation,
        )
        if prior is not None:
            prior_meta = from_jsonb(getattr(prior, "meta", None)) or {}
            first = (prior_meta.get("changes") or [{}])[0]
            merged = {**resolved[0], "old": first.get("old")}
            coalesced: dict[str, Any] = {"changes": [merged]}
            if automation:
                # The flag survives the fold — a coalesced automated edit must
                # not quietly turn back into a human-looking row.
                coalesced["automation"] = True
            # WS-27ae — the coalescing arm returns without reaching
            # `record_activity`, so it carries its own satellite bump. Every
            # caller today has already written the task row itself, which makes
            # this belt-and-braces; it is here so the invariant "a field change
            # bumps the task" holds on the FUNCTION rather than on the habits of
            # its callers.
            await touch_task(db, task_id)
            # `created_at` is bumped too, not only `updated_at`: the coalesced
            # row now records the LATEST edit, the timeline orders on
            # `created_at`, and the row is already the task's newest — so the
            # bump keeps it truthful without reordering anything.
            return await update_row(db, "pm_activities", str(prior.id), {
                "meta": coalesced,
                "created_at": now(),
            })
    meta: dict[str, Any] = {"changes": resolved}
    if extra_meta:
        meta.update(extra_meta)
    return await record_activity(
        db, activity_type="field_change", created_by=created_by,
        task_id=task_id, project_id=project_id, meta=meta,
        automation=automation,
    )


# ── Events (§6.3) ───────────────────────────────────────────────────────────

async def emit(event_type: str, payload: dict[str, Any]) -> None:
    """Publish one ``pm.*`` event on the platform's existing event seam.

    Deliberately the SAME path the ClickUp webhook already uses
    (``ingestion.event_hooks.emit_event`` → ``workflows.triggers.dispatch_event``),
    so binding this app to the automation engine is one seam rather than a new
    bus — WS-27f adds node types, not transport.

    Best-effort by construction: the import is inside the function so the
    gateway's import graph does not gain a dependency, and a failure is logged
    rather than raised. **A workflow that cannot run must never fail the write
    that triggered it** — the user's task edit already succeeded, and the
    alternative is a 500 on a successful mutation.
    """
    try:
        from ingestion.event_hooks import emit_event

        await emit_event("projects", event_type, payload)
    except Exception as exc:  # pragma: no cover — defensive, per the docstring
        # `event=` is structlog's own reserved key for the message; passing it
        # raises TypeError inside the logger and turns a swallowed bus failure
        # into the 500 this whole function exists to prevent.
        _log.warning("projects.event_emit_failed", topic=event_type, error=str(exc))


def validate_grant_subject(subject: str) -> str:
    """The grant subject vocabulary, and nothing else.

    ``org`` | ``group:<slug>`` | an email address — exactly what
    ``tenancy_and_visibility.md`` §3.2 already shipped for rooms, and §3.2 is
    binding: a second vocabulary would mean two answers to "who can see this".

    Lives here for WS-27a. WS-14 C1's done-when 5 names
    ``packages/acb_auth/acb_auth/permissions.py`` as the shared home for the
    same validator; whichever ticket lands second should lift this rather than
    keep a copy — two validators is how the two vocabularies begin.
    """
    value = (subject or "").strip()
    if not value:
        raise HTTPException(status_code=422, detail="A grant subject is required.")
    if value == "org":
        return value
    if value.startswith("group:"):
        slug = value[len("group:"):].strip()
        if not slug:
            raise HTTPException(
                status_code=422, detail="A group subject must name a group.",
            )
        return f"group:{slug.lower()}"
    if "@" in value:
        # R10 — stored folded, because every read compares folded.
        return value.lower()
    raise HTTPException(
        status_code=422,
        detail=(
            f"Unknown grant subject '{subject}'. "
            "One of: 'org', 'group:<slug>', or an email address."
        ),
    )


def validate_choice(value: str | None, allowed: tuple[str, ...], what: str) -> None:
    if value is not None and value not in allowed:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown {what} '{value}'. One of: {list(allowed)}.",
        )


#: WS-27z — the three lifecycle-policy columns (migration 166). Named once so
#: the write path's root-only guard and the tests read the same list.
LIFECYCLE_FIELDS: tuple[str, ...] = (
    "archive_after_months", "close_after_months", "timezone",
)


def validate_lifecycle_settings(values: dict[str, Any]) -> None:
    """422 on a malformed lifecycle policy, before anything is written.

    Months are whole numbers greater than zero, or ``null`` to switch the
    policy off — the migration's CHECK says the same, and refusing here names
    the field instead of surfacing an IntegrityError 500. The timezone is
    validated against the IANA database exactly the way the workflows app
    validates a schedule trigger's (``crud._timezone_is_valid``): a bad zone
    discovered by the sweep is a policy that silently measures against the
    wrong midnight; discovered at save time it is a 422 the owner can act on.
    """
    for column in ("archive_after_months", "close_after_months"):
        if column not in values or values[column] is None:
            continue
        months = values[column]
        if isinstance(months, bool) or not isinstance(months, int) or months <= 0:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"'{column}' must be a whole number of months greater "
                    f"than zero, or null to switch the policy off."
                ),
            )
    if "timezone" in values:
        name = values["timezone"]
        if name is None:
            raise HTTPException(
                status_code=422,
                detail="'timezone' cannot be null — it defaults to 'UTC'.",
            )
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            ZoneInfo(str(name))
        except (ZoneInfoNotFoundError, ValueError, KeyError):
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Unknown timezone '{name}' — use an IANA name like "
                    f"Asia/Kolkata"
                ),
            ) from None
