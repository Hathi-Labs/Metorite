"""Projects · templates — a project saved as a template, and a project made from one.

Spec: ``project-docs/specs/project_management_app.md`` §14.2 (rows TPL-1a and
TPL-1b) and §14.8 (the design). Migration: the file that creates
``pm_templates``. Metorite task 18, Linear parity P7.

    POST /projects/templates                       save a project as a template
    GET  /projects/templates                       the templates of the org
    POST /projects/templates/{template_id}/apply   a new project from a template

THE one writer of ``pm_templates`` and ``pm_template_applies``.

**The flag.** ``PROJECTS_TEMPLATES_ORGS`` (default empty). While it does not
hold the org, each route answers 404 and nothing is written.
:func:`templates_allowed` is its one reader.

**Who may do what.**

* Save: the right that edits the source project, which is
  ``core.load_visible_project`` (the check of ``PATCH /nodes/{id}``).
* List: each member of the org (``feature:projects``, the router's floor). A
  template is org-wide, as a task type is since migration 175.
* Apply: the right to make a project under the parent, which is
  ``tree.create_node_in``, the one creator of ``POST /nodes``.

**The private-source rule (§14.8, the safer of the two choices).** A template
is org-wide, so each member can list it and apply it. The save therefore
refuses a source project that a member with no group and no admin right
cannot see: the project, or a project above it, must hold the ``org`` grant,
and it must not be sealed or personal. :func:`_org_reader` builds that member,
and ``load_visible_project`` asks the one visibility seam. So a template never
holds a title or a description that a lister could not read at the save.

**An outside agent.** A connection with pins or a left-out list narrows the
member's sight (D-EA-12). An agent lists and applies only a template whose
source project it can still see (:func:`_reach_clause`), so a pin narrows the
templates too. A template whose source is gone is hidden from every agent.

**The payload is data, never code.** :func:`parse_payload` checks it on each
save and on each apply: ``schema_version`` 1, no extra key, and the caps of
:data:`CAPS`. It holds no id of the source, no person, no comment and no
activity. Text in it leaves the MCP edge as ``untrusted_text``.

**The apply** runs in ONE transaction. It claims the idempotency key first
(``INSERT … ON CONFLICT DO NOTHING`` on the unique index), so a second apply
with the same key waits for the first, then returns its project. Then it makes
the project through ``tree.create_node_in``, gives it the template's statuses
(it inherits its parent's set when the two sets match, else it owns a copy),
adds each missing task type, field and tag to the space, and makes the task
tree through ``tasks.create_task_in``. A date is the start date plus the
offset, in calendar days. The apply is quiet, as the file import is: it emits
``pm.project.created`` once, and no event, link or check job for each task.

Fence: ``tests/unit/test_pm_templates_r8.py`` (R8).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import date, timedelta
from typing import Any, Literal

from acb_auth import UserContext, get_current_user
from acb_common import get_settings
from fastapi import Depends, HTTPException, Response
from gateway.routes.projects.core import (
    CLOSING_CATEGORIES,
    COMPLETED_CATEGORY,
    TRIAGE_CATEGORY,
    ProjectModel,
    Visibility,
    _tenant_session,
    actor,
    emit,
    from_jsonb,
    insert_row,
    is_team_space,
    load_visible_project,
    node_kind,
    record_activity,
    require_done_status,
    require_organization,
    resolve_visibility,
    root_project_id,
    router,
    row_to_dict,
    shadowed,
    status_order_sql,
    status_owner_id,
    update_row,
    vocabulary_scope,
)
from gateway.routes.projects.custom_fields import (
    MAX_FIELDS,
    MAX_OPTIONS,
    load_definitions,
)
from gateway.routes.projects.due_day import local_date
from gateway.routes.projects.personal import member_today, stored_zone
from gateway.routes.projects.tags import (
    MAX_TAG,
    MAX_TAGS_PER_PROJECT,
    MAX_TAGS_PER_TASK,
    load_registry_rows,
    register,
)
from gateway.routes.projects.tasks import check_task_values, create_task_in
from gateway.routes.projects.tree import (
    _tree_race_as_409,
    check_node_values,
    create_node_in,
)
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

# ── The bounds ──────────────────────────────────────────────────────────────

#: The kinds of the CHECK ``pm_templates_kind``. TPL-1a saves ``project``.
TEMPLATE_KINDS: tuple[str, ...] = ("project", "task")
PROJECT_KIND = "project"
#: The one version of the payload that this module reads and writes.
SCHEMA_VERSION = 1
#: The bounds of the text columns. The CHECKs of the migration hold them too.
NAME_MAX = 120
DESCRIPTION_MAX = 2000
#: The longest name of the project that an apply makes (the edge's
#: ``MAX_PROJECT_NAME``).
PROJECT_NAME_MAX = 200
#: The caps of one payload. A save refuses a source that breaks one, and an
#: apply refuses a stored payload that breaks one.
MAX_STATUSES = 50
MAX_TYPES = 50
MAX_TASKS = 500
#: The levels of the task tree. A task with no parent is level 1.
MAX_TASK_DEPTH = 10
MAX_TITLE = 2000
MAX_TASK_DESCRIPTION = 20_000
MAX_VOCAB_NAME = 100
MAX_OFFSET_DAYS = 3650
MAX_ESTIMATE_MINS = 2_147_483_647
MAX_TASK_FIELDS = 50
#: The bytes of the payload as JSON text. The CHECK
#: ``pm_templates_payload_size`` holds the same number.
MAX_PAYLOAD_BYTES = 1_048_576
#: Every cap, by name, for the refusal text and the fence.
CAPS: dict[str, int] = {
    "statuses": MAX_STATUSES,
    "types": MAX_TYPES,
    "fields": MAX_FIELDS,
    "tags": MAX_TAGS_PER_PROJECT,
    "tasks": MAX_TASKS,
    "depth": MAX_TASK_DEPTH,
    "bytes": MAX_PAYLOAD_BYTES,
}
#: The most templates that one list returns, by name.
LIST_LIMIT = 200
#: The shape of an idempotency key: the shape of the MCP edge's key input.
KEY_PATTERN = r"^[A-Za-z0-9._:-]{1,128}$"
_DATE_PATTERN = r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$"
#: The unique index of one name for each kind in each org.
NAME_INDEX = "uq_pm_templates_name"
#: The CHECK of the size of the payload.
SIZE_CHECK = "pm_templates_payload_size"

#: The error codes of the refusals. The chat tools and the MCP edge map each
#: one to a fixed text.
SOURCE_NOT_ORG_WIDE = "source_not_org_wide"
SOURCE_NOT_A_PROJECT = "source_not_a_project"
NAME_TAKEN = "template_name_taken"
TOO_LARGE = "template_too_large"
INVALID = "template_invalid"
KEY_USED = "key_used"
FIELD_CONFLICT = "field_conflict"
NOT_A_PROJECT_TEMPLATE = "not_a_project_template"


# ── The flag ────────────────────────────────────────────────────────────────


def templates_allowed(organization_id: str | None) -> bool:
    """True when ``PROJECTS_TEMPLATES_ORGS`` holds the org, or holds ``*``.
    Default OFF.

    The org comes from the session, never from request input (R5). It reads
    ``get_settings()`` on each call, so a flag reload applies a change with
    no restart.
    """
    org = str(organization_id or "").strip().lower()
    if not org:
        return False
    raw = str(getattr(get_settings(), "projects_templates_orgs", "") or "")
    listed = {p.strip().lower() for p in raw.split(",") if p.strip()}
    return "*" in listed or org in listed


async def require_templates(user: UserContext = Depends(get_current_user)) -> None:
    """404 while ``PROJECTS_TEMPLATES_ORGS`` does not hold the caller's org."""
    if not templates_allowed(getattr(user, "organization_id", None)):
        raise HTTPException(status_code=404, detail="Not Found")


_ENABLED = [Depends(require_templates)]


# ── Small helpers ───────────────────────────────────────────────────────────


def _refusal(status: int, error: str, message: str) -> HTTPException:
    """A refusal that says what happened and what to do next."""
    return HTTPException(status_code=status, detail={"error": error, "message": message})


def _uuid_or_404(value: Any, what: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=f"{what} not found") from exc


def _clean_text(value: Any, what: str, limit: int, *, required: bool) -> str | None:
    """A trimmed text of the body, or None. Refuses a NUL, which Postgres
    cannot store, and a text over *limit* characters."""
    if value is None:
        if required:
            raise _refusal(422, INVALID, f"Send a {what}.")
        return None
    cleaned = str(value).strip()
    if "\x00" in cleaned:
        raise _refusal(422, INVALID, f"The {what} holds a NUL character. Remove it.")
    if not cleaned:
        if required:
            raise _refusal(422, INVALID, f"The {what} holds no text. Send a {what}.")
        return None
    if len(cleaned) > limit:
        raise _refusal(422, INVALID, f"The {what} is longer than {limit} characters.")
    return cleaned


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


# ── The payload (schema_version 1) ──────────────────────────────────────────


class _Strict(BaseModel):
    """No extra key, and no silent coercion of a type."""

    model_config = ConfigDict(extra="forbid", strict=True)


class StatusSpec(_Strict):
    name: str = Field(min_length=1, max_length=MAX_VOCAB_NAME)
    category: Literal["backlog", "todo", "in_progress", "done", "cancelled", "triage"]
    color: str | None = Field(default=None, max_length=32)
    position: int = Field(default=0, ge=0, le=1_000_000)


class TypeSpec(_Strict):
    name: str = Field(min_length=1, max_length=MAX_VOCAB_NAME)
    icon: str | None = Field(default=None, max_length=64)
    color: str | None = Field(default=None, max_length=32)
    is_epic: bool = False


class FieldSpec(_Strict):
    field_key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,62}$")
    name: str = Field(min_length=1, max_length=MAX_VOCAB_NAME)
    description: str | None = Field(default=None, max_length=DESCRIPTION_MAX)
    field_type: Literal["text", "number", "date", "select", "multi_select", "boolean", "url"]
    options: list[str] = Field(default_factory=list, max_length=MAX_OPTIONS)
    position: int = Field(default=0, ge=0, le=1_000_000)
    #: Kept as the source had it. An apply never sets it (§14.8): a field
    #: that an apply adds lands on the whole space.
    required: bool = False


class TagSpec(_Strict):
    name: str = Field(min_length=1, max_length=MAX_TAG)
    color: str | None = Field(default=None, max_length=32)
    description: str | None = Field(default=None, max_length=DESCRIPTION_MAX)


class TaskSpec(_Strict):
    title: str = Field(min_length=1, max_length=MAX_TITLE)
    description: str | None = Field(default=None, max_length=MAX_TASK_DESCRIPTION)
    #: A status NAME of the template, or None for the first open lane.
    status: str | None = Field(default=None, max_length=MAX_VOCAB_NAME)
    #: A type NAME of the template, or None.
    type: str | None = Field(default=None, max_length=MAX_VOCAB_NAME)
    tags: list[str] = Field(default_factory=list, max_length=MAX_TAGS_PER_TASK)
    #: Values keyed by a ``field_key`` of the template. The apply checks each
    #: value against the definition of the space (``custom_fields.apply_values``).
    custom_fields: dict[str, Any] = Field(default_factory=dict, max_length=MAX_TASK_FIELDS)
    importance: int | None = Field(default=None, ge=0, le=3)
    leveraged: bool | None = None
    estimate_mins: int | None = Field(default=None, ge=0, le=MAX_ESTIMATE_MINS)
    #: Days from the project start. Calendar days (§14.8).
    start_offset_days: int | None = Field(default=None, ge=-MAX_OFFSET_DAYS, le=MAX_OFFSET_DAYS)
    due_offset_days: int | None = Field(default=None, ge=-MAX_OFFSET_DAYS, le=MAX_OFFSET_DAYS)
    children: list[TaskSpec] = Field(default_factory=list, max_length=MAX_TASKS)


class Counts(_Strict):
    statuses: int = Field(ge=0)
    types: int = Field(ge=0)
    fields: int = Field(ge=0)
    tags: int = Field(ge=0)
    tasks: int = Field(ge=0)


class PayloadV1(_Strict):
    schema_version: Literal[1]
    #: The work style (WM-0). No work style exists yet, so it is None.
    work_style: str | None = Field(default=None, max_length=40)
    statuses: list[StatusSpec] = Field(min_length=1, max_length=MAX_STATUSES)
    types: list[TypeSpec] = Field(default_factory=list, max_length=MAX_TYPES)
    fields: list[FieldSpec] = Field(default_factory=list, max_length=MAX_FIELDS)
    tags: list[TagSpec] = Field(default_factory=list, max_length=MAX_TAGS_PER_PROJECT)
    tasks: list[TaskSpec] = Field(default_factory=list, max_length=MAX_TASKS)
    #: Written by the save, so a list reads the numbers and not the tree.
    counts: Counts


def _raw_shape(raw: Any) -> tuple[int, int]:
    """``(tasks, depth)`` of a raw payload, walked with a stack, never with
    recursion. It runs BEFORE the model, so a payload nested a million levels
    deep is refused by its depth and never reaches a recursive parser."""
    tasks = raw.get("tasks") if isinstance(raw, dict) else None
    if not isinstance(tasks, list):
        return 0, 0
    count, deepest = 0, 0
    stack: list[tuple[Any, int]] = [(t, 1) for t in tasks]
    while stack:
        node, depth = stack.pop()
        count += 1
        deepest = max(deepest, depth)
        if count > MAX_TASKS or deepest > MAX_TASK_DEPTH:
            break
        children = node.get("children") if isinstance(node, dict) else None
        if isinstance(children, list):
            stack.extend((c, depth + 1) for c in children)
    return count, deepest


def _too_large(cap: str, found: int) -> HTTPException:
    return _refusal(
        422, TOO_LARGE,
        f"A template holds at most {CAPS[cap]} {cap} and this one has {found} or "
        f"more. Make it smaller first, then save it again.",
    )


def _walk(tasks: list[TaskSpec]) -> list[tuple[TaskSpec, int]]:
    """Each task with its level, parents before children, with a stack."""
    out: list[tuple[TaskSpec, int]] = []
    stack: list[tuple[TaskSpec, int]] = [(t, 1) for t in reversed(tasks)]
    while stack:
        task, depth = stack.pop()
        out.append((task, depth))
        stack.extend((c, depth + 1) for c in reversed(task.children))
    return out


def parse_payload(raw: Any) -> PayloadV1:
    """The payload as data that this module trusts, or a 422.

    The ONE check of a payload: the save calls it on the payload it built,
    and the apply calls it on the payload it read. It refuses a payload of
    another version, an extra key, a wrong type, a broken cap, a task that
    names a status or a type that the template does not hold, and a status
    set with no Done status.
    """
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, RecursionError) as exc:
            raise _refusal(422, INVALID, "The template is not readable JSON.") from exc
    if not isinstance(raw, dict) or raw.get("schema_version") != SCHEMA_VERSION:
        raise _refusal(
            422, INVALID,
            f"The template is not of version {SCHEMA_VERSION}. Save it again from a project.",
        )
    # The shape first, with a stack: the size below writes the JSON again,
    # and a writer recurses.
    count, depth = _raw_shape(raw)
    if count > MAX_TASKS:
        raise _too_large("tasks", count)
    if depth > MAX_TASK_DEPTH:
        raise _too_large("depth", depth)
    # The separators of the jsonb text, so the count is the one the CHECK
    # `pm_templates_payload_size` reads (`octet_length(payload::text)`).
    try:
        size = len(json.dumps(raw, ensure_ascii=False).encode("utf-8"))
    except (RecursionError, ValueError, TypeError) as exc:
        raise _refusal(422, INVALID, "The template holds a value that is not plain JSON.") from exc
    if size > MAX_PAYLOAD_BYTES:
        raise _too_large("bytes", size)
    for name in ("statuses", "types", "fields", "tags"):
        found = raw.get(name)
        if isinstance(found, list) and len(found) > CAPS[name]:
            raise _too_large(name, len(found))
    try:
        payload = PayloadV1.model_validate(raw)
    except RecursionError as exc:
        raise _too_large("depth", MAX_TASK_DEPTH + 1) from exc
    except ValidationError as exc:
        first = exc.errors()[0] if exc.errors() else {}
        where = ".".join(str(part) for part in first.get("loc", ())) or "the template"
        raise _refusal(
            422, INVALID,
            f"The template has a value of the wrong shape at {where}: {first.get('msg', 'not valid')}.",
        ) from exc
    _check_references(payload)
    return payload


def _check_references(payload: PayloadV1) -> None:
    """Names inside one payload agree: unique names, each task status and
    type in the template, and one Done status at least (D79)."""
    def unique(names: list[str], what: str) -> None:
        seen: set[str] = set()
        for n in names:
            key = n.strip().lower()
            if key in seen:
                raise _refusal(422, INVALID, f"The template names the {what} '{n}' twice.")
            seen.add(key)

    unique([s.name for s in payload.statuses], "status")
    unique([f.field_key for f in payload.fields], "field")
    unique([t.name for t in payload.tags], "tag")
    # A type is named in its case (`core.VOCABULARY_IDENTITY`), so "Bug" and
    # "bug" are two types, and only the same name twice is refused.
    exact = [t.name.strip() for t in payload.types]
    if len(set(exact)) != len(exact):
        raise _refusal(422, INVALID, "The template names one task type twice.")
    if not any(s.category == COMPLETED_CATEGORY for s in payload.statuses):
        raise _refusal(422, INVALID, "The template has no Done status. A status set keeps one.")
    statuses = {s.name.strip().lower() for s in payload.statuses}
    types = set(exact)
    walked = _walk(payload.tasks)
    for task, _depth in walked:
        if task.status is not None and task.status.strip().lower() not in statuses:
            raise _refusal(422, INVALID, f"A task names the status '{task.status}', which the template does not hold.")
        if task.type is not None and task.type.strip() not in types:
            raise _refusal(422, INVALID, f"A task names the task type '{task.type}', which the template does not hold.")
    expected = Counts(
        statuses=len(payload.statuses), types=len(payload.types),
        fields=len(payload.fields), tags=len(payload.tags), tasks=len(walked),
    )
    if payload.counts != expected:
        raise _refusal(422, INVALID, "The counts of the template do not match its rows.")


# ── Visibility ──────────────────────────────────────────────────────────────


def _org_reader(vis: Visibility) -> Visibility:
    """A member of the caller's org with no group, no grant of their own and
    no admin right. What this member sees, every member sees.

    The email is empty, so only an ``org`` grant seeds the closure, and a
    sealed or a personal node stays out (``core._VISIBLE_PROJECTS_SQL``). It
    carries no agent narrowing: it asks about the org, not about a caller.
    """
    return Visibility(
        unrestricted=False, email="", groups=(), organization_id=vis.organization_id,
    )


def _reach_clause(vis: Visibility) -> tuple[str, dict[str, Any]]:
    """The extra arm for an outside agent, or nothing for a member.

    A connection with pins or a left-out list sees less than its member
    (D-EA-12). A template carries the text of its source project, so an agent
    reaches a template only while it can see that project now. A NULL source
    matches nothing, so a template whose source is gone is hidden from every
    agent. Strict ``vis.agent``, as in ``core.task_visibility_clause``.
    """
    if vis.agent is None:
        return "", {}
    return f" AND {vis.project_clause('t.source_project_id')}", dict(vis.params)


# ── The wire ────────────────────────────────────────────────────────────────


def _template_wire(row: Any) -> dict[str, Any]:
    """One template as the routes show it: the numbers, never the tree."""
    counts = from_jsonb(row.counts) or {}
    return {
        "id": str(row.id),
        "kind": row.kind,
        "name": row.name,
        "description": row.description,
        "work_style": row.work_style,
        "counts": {k: int(counts.get(k) or 0) for k in ("statuses", "types", "fields", "tags", "tasks")},
        "created_by": row.created_by,
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


_TEMPLATE_COLUMNS = (
    "t.id, t.kind, t.name, t.description, t.work_style, t.created_by, "
    "t.created_at, t.updated_at, t.payload -> 'counts' AS counts"
)
#: The same columns, as the RETURNING of the save names them.
_RETURNED_COLUMNS = (
    "id, kind, name, description, work_style, created_by, "
    "created_at, updated_at, payload -> 'counts' AS counts"
)


# ── GET /templates ──────────────────────────────────────────────────────────


@router.get("/templates", dependencies=_ENABLED)
async def list_templates(user: UserContext = Depends(get_current_user)) -> dict:
    """The templates of the caller's org, by name.

    Each row holds the numbers of the template, never its tree. The org is
    in the WHERE as well as in RLS, two fences on purpose.
    """
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        org = require_organization(vis)
        reach, params = _reach_clause(vis)
        rows = (await db.execute(
            text(
                f"SELECT {_TEMPLATE_COLUMNS} FROM pm_templates t"
                " WHERE t.organization_id = CAST(:org AS uuid)"
                f"{reach}"
                " ORDER BY lower(t.name), t.kind, t.id"
                " LIMIT :limit"
            ),
            {**params, "org": org, "limit": LIST_LIMIT + 1},
        )).fetchall()
    more = len(rows) > LIST_LIMIT
    shown = [_template_wire(r) for r in rows[:LIST_LIMIT]]
    return {"rows": shown, "total": len(shown), "more": more}


# ── POST /templates ─────────────────────────────────────────────────────────


class TemplateSaveIn(BaseModel):
    """The body of a save. The wire forbids an extra key, the org above all."""

    model_config = ConfigDict(extra="forbid")

    project_id: str
    name: str
    description: str | None = None
    #: The day that the offsets count from, ``YYYY-MM-DD``. Absent: the
    #: earliest start or due day of the starter tasks.
    start_date: str | None = Field(default=None, pattern=_DATE_PATTERN)


_SOURCE_TASKS_SQL = (
    "SELECT t.id, t.parent_task_id, t.type_id, t.title, t.description,"
    "       t.importance, t.leveraged, t.estimate_mins, t.start_date, t.due_at,"
    "       t.tags, t.custom_fields, s.name AS status_name, s.category"
    "  FROM pm_tasks t"
    "  JOIN pm_task_statuses s ON s.id = t.status_id"
    " WHERE t.project_id = CAST(:pid AS uuid)"
    "   AND t.organization_id = CAST(:org AS uuid)"
    "   AND t.archived_at IS NULL"
    "   AND s.category <> :triage"
    " ORDER BY t.task_number NULLS LAST, t.created_at, t.id"
    " LIMIT :limit"
)


async def _source_vocabulary(db: Any, source_id: str) -> dict[str, list[Any]]:
    """The effective statuses, types, fields and tags of the source project,
    read through the seams that its boards read."""
    owner = await status_owner_id(db, source_id)
    statuses = (await db.execute(
        text(
            "SELECT name, color, position, category FROM pm_task_statuses"
            f" WHERE project_id = CAST(:owner AS uuid) ORDER BY {status_order_sql()}"
        ),
        {"owner": owner},
    )).fetchall()
    root = await root_project_id(db, source_id)
    types = (await db.execute(
        text(f"SELECT * FROM pm_task_types WHERE {vocabulary_scope()} ORDER BY name"),
        {"root": root},
    )).fetchall()
    return {
        "statuses": list(statuses),
        # Every effective row, so a task whose type is a shadowed org-wide
        # row still finds its name. The template lists each name once.
        "all_types": list(types),
        "types": shadowed(list(types), lambda r: r.name),
        "fields": await load_definitions(db, root),
        "tags": await load_registry_rows(db, root),
    }


def _due_day(due_at: Any, zone: str | None) -> date | None:
    return local_date(zone, due_at) if due_at is not None else None


async def build_payload(
    db: Any, source_id: str, org: str, saver: str, anchor: date | None,
) -> dict[str, Any]:
    """The payload of one project, as plain JSON data.

    No id of the source, no assignee, no comment and no activity. A starter
    task keeps its status only while the status is open: a done or a
    cancelled task starts open in the new project. A task parked in triage
    holds intake text from outside, so it stays out. The offsets count in
    calendar days from *anchor*, or from the earliest start or due day.
    """
    vocab = await _source_vocabulary(db, source_id)
    type_names = {str(r.id): r.name for r in vocab["all_types"]}
    field_keys = {str(f["field_key"]) for f in vocab["fields"]}
    rows = (await db.execute(
        text(_SOURCE_TASKS_SQL),
        {"pid": source_id, "org": org, "triage": TRIAGE_CATEGORY, "limit": MAX_TASKS + 1},
    )).fetchall()
    if len(rows) > MAX_TASKS:
        raise _too_large("tasks", len(rows))
    zone = await stored_zone(db, saver)
    if anchor is None:
        days = [d for r in rows for d in (r.start_date, _due_day(r.due_at, zone)) if d is not None]
        anchor = min(days) if days else None

    def offset(day: date | None) -> int | None:
        if day is None or anchor is None:
            return None
        return (day - anchor).days

    by_id: dict[str, dict[str, Any]] = {}
    for r in rows:
        values = from_jsonb(r.custom_fields) or {}
        by_id[str(r.id)] = {
            "title": r.title,
            "description": r.description,
            "status": None if r.category in CLOSING_CATEGORIES else r.status_name,
            "type": type_names.get(str(r.type_id)) if r.type_id else None,
            "tags": list(r.tags or []),
            "custom_fields": {k: v for k, v in (values or {}).items() if k in field_keys},
            "importance": r.importance,
            "leveraged": r.leveraged,
            "estimate_mins": r.estimate_mins,
            "start_offset_days": offset(r.start_date),
            "due_offset_days": offset(_due_day(r.due_at, zone)),
            "children": [],
        }
    roots: list[dict[str, Any]] = []
    for r in rows:
        node = by_id[str(r.id)]
        parent = by_id.get(str(r.parent_task_id)) if r.parent_task_id else None
        # A task whose parent stays out (archived, parked, or in another
        # project) becomes a root of the tree.
        (parent["children"] if parent is not None else roots).append(node)

    statuses = [
        {"name": s.name, "category": s.category, "color": s.color, "position": int(s.position or 0)}
        for s in vocab["statuses"]
    ]
    types = [
        {"name": t.name, "icon": t.icon, "color": t.color, "is_epic": bool(getattr(t, "is_epic", False))}
        for t in vocab["types"]
    ]
    fields = [
        {
            "field_key": str(f["field_key"]), "name": f["name"], "description": f.get("description"),
            "field_type": f["field_type"], "options": list(f.get("options") or []),
            "position": int(f.get("position") or 0), "required": bool(f.get("required")),
        }
        for f in vocab["fields"]
    ]
    tags = [{"name": t.name, "color": t.color, "description": t.description} for t in vocab["tags"]]
    count = len(rows)
    return {
        "schema_version": SCHEMA_VERSION,
        "work_style": None,
        "statuses": statuses,
        "types": types,
        "fields": fields,
        "tags": tags,
        "tasks": roots,
        "counts": {
            "statuses": len(statuses), "types": len(types), "fields": len(fields),
            "tags": len(tags), "tasks": count,
        },
    }


@router.post("/templates", status_code=201, dependencies=_ENABLED)
async def save_template(
    payload: TemplateSaveIn, user: UserContext = Depends(get_current_user),
) -> dict:
    """Save one project as a template of the org.

    The right is the right that edits the project. The project must be one
    that the whole org can see (the private-source rule of the module text).
    A space or a folder holds no tasks, so it is no source.
    """
    name = _clean_text(payload.name, "name", NAME_MAX, required=True)
    description = _clean_text(payload.description, "description", DESCRIPTION_MAX, required=False)
    source_id = _uuid_or_404(payload.project_id, "Project")
    anchor = date.fromisoformat(payload.start_date) if payload.start_date else None
    saver = actor(user).lower()

    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        source = await load_visible_project(db, vis, source_id)
        org = require_organization(vis)
        if node_kind(getattr(source, "kind", None)) == "folder" or is_team_space(source):
            raise _refusal(
                422, SOURCE_NOT_A_PROJECT,
                "A space or a folder holds no tasks. Save a template from a project.",
            )
        try:
            await load_visible_project(db, _org_reader(vis), source_id)
        except HTTPException:
            raise _refusal(
                403, SOURCE_NOT_ORG_WIDE,
                "Each member of the organization can read a template, so a template "
                "comes only from a project that the whole organization can see. Save "
                "it from such a project, or give this project the organization grant first.",
            ) from None
        built = await build_payload(db, source_id, org, saver, anchor)
        checked = parse_payload(built)
        taken = (await db.execute(
            text(
                "SELECT 1 FROM pm_templates WHERE organization_id = CAST(:org AS uuid)"
                " AND lower(name) = lower(:name) AND kind = :kind"
            ),
            {"org": org, "name": name, "kind": PROJECT_KIND},
        )).first()
        if taken is not None:
            raise _name_taken(name)
        try:
            async with db.begin_nested():
                row = (await db.execute(
                    text(
                        "INSERT INTO pm_templates (kind, name, description, work_style, payload,"
                        " source_project_id, created_by)"
                        " VALUES (:kind, :name, :description, :work_style, CAST(:payload AS jsonb),"
                        " CAST(:source AS uuid), :by)"
                        f" RETURNING {_RETURNED_COLUMNS}"
                    ),
                    {
                        "kind": PROJECT_KIND, "name": name, "description": description,
                        "work_style": checked.work_style,
                        "payload": json.dumps(built, ensure_ascii=False),
                        "source": source_id, "by": saver,
                    },
                )).fetchone()
        except IntegrityError as exc:
            if NAME_INDEX in str(exc):
                raise _name_taken(name) from None
            if SIZE_CHECK in str(exc):
                raise _too_large("bytes", MAX_PAYLOAD_BYTES + 1) from None
            raise
        return _template_wire(row)


def _name_taken(name: str) -> HTTPException:
    return _refusal(
        409, NAME_TAKEN,
        f"A template called '{name}' already exists in this organization. Pick another name.",
    )


# ── POST /templates/{template_id}/apply ─────────────────────────────────────


class TemplateApplyIn(BaseModel):
    """The body of an apply. The wire forbids an extra key, the org above all."""

    model_config = ConfigDict(extra="forbid")

    #: The client's key. The same key and the same body again return the
    #: first project and make nothing.
    idempotency_key: str = Field(pattern=KEY_PATTERN)
    #: The node to make the project under: a space, a folder or a project. A
    #: space holds no tasks, so a template never makes a space.
    parent_project_id: str
    #: The name of the new project. Absent: the template's name.
    name: str | None = None
    description: str | None = None
    #: The project start, ``YYYY-MM-DD``. Absent: the caller's today.
    start_date: str | None = Field(default=None, pattern=_DATE_PATTERN)


def request_hash(template_id: str, body: TemplateApplyIn) -> str:
    """The SHA-256 of one apply as the client sent it, the key left out."""
    shape = {
        "template_id": template_id,
        "parent_project_id": str(body.parent_project_id).strip().lower(),
        "name": body.name,
        "description": body.description,
        "start_date": body.start_date,
    }
    return hashlib.sha256(
        json.dumps(shape, sort_keys=True, separators=(",", ":")).encode("utf-8"),
    ).hexdigest()


_CLAIM_SQL = (
    "INSERT INTO pm_template_applies (applied_by, idempotency_key, request_hash)"
    " VALUES (:by, :key, :hash)"
    " ON CONFLICT (organization_id, applied_by, idempotency_key) DO NOTHING"
    " RETURNING id"
)
_CLAIMED_SQL = (
    "SELECT id, template_id, project_id, request_hash FROM pm_template_applies"
    " WHERE organization_id = CAST(:org AS uuid) AND applied_by = :by"
    "   AND idempotency_key = :key"
)


async def _replay(db: Any, vis: Visibility, org: str, by: str, key: str, digest: str) -> dict:
    """The answer to a key that an earlier apply holds.

    The claim waited for that apply to end, so this read sees its commit.
    """
    row = (await db.execute(text(_CLAIMED_SQL), {"org": org, "by": by, "key": key})).first()
    if row is None or row.request_hash != digest:
        raise _refusal(
            409, KEY_USED,
            "This idempotency_key was sent with another request. Send a new key.",
        )
    if row.project_id is None:
        raise _refusal(
            409, KEY_USED,
            "The project that this idempotency_key made is gone. Send a new key.",
        )
    project = await load_visible_project(db, vis, str(row.project_id))
    return {
        "project": row_to_dict(project, ProjectModel),
        "template_id": str(row.template_id) if row.template_id else None,
        "replayed": True,
        "own_statuses": bool(getattr(project, "owns_statuses", False)),
        "created": None,
    }


async def _apply_statuses(
    db: Any, project_id: str, payload: PayloadV1,
) -> tuple[dict[str, str], bool, int]:
    """``(lane id by lower name, owns, lanes made)`` of the new project.

    The project inherits its parent's set, as each new child does. When the
    template's set is the same set (the names and the stages), it keeps it.
    Else it owns a copy of the template's set, with no task to move (the
    switch of ``admin.set_status_set``, on an empty node), and D79 holds.
    """
    owner = await status_owner_id(db, project_id)
    lanes = (await db.execute(
        text("SELECT id, name, category FROM pm_task_statuses WHERE project_id = CAST(:o AS uuid)"),
        {"o": owner},
    )).fetchall()
    have = {(str(r.name).strip().lower(), str(r.category)) for r in lanes}
    want = {(s.name.strip().lower(), s.category) for s in payload.statuses}
    if have == want:
        return {str(r.name).strip().lower(): str(r.id) for r in lanes}, False, 0
    ids: dict[str, str] = {}
    for spec in payload.statuses:
        row = await insert_row(db, "pm_task_statuses", {
            "project_id": project_id, "name": spec.name.strip(),
            "color": spec.color or "gray", "position": spec.position,
            "category": spec.category, "is_default": False,
        })
        ids[spec.name.strip().lower()] = str(row.id)
    await db.execute(
        text("UPDATE pm_projects SET owns_statuses = true WHERE id = CAST(:id AS uuid)"),
        {"id": project_id},
    )
    await require_done_status(db, project_id)
    return ids, True, len(ids)


async def _apply_types(db: Any, root: str, payload: PayloadV1) -> tuple[dict[str, str], int]:
    """``(type id by name, types made)``. A name the space already
    holds, of its own or org-wide, is reused. A type is never the default and
    never a system type: those are each space's own."""
    rows = (await db.execute(
        text(f"SELECT * FROM pm_task_types WHERE {vocabulary_scope()}"), {"root": root},
    )).fetchall()
    by_name = {r.name: str(r.id) for r in shadowed(list(rows), lambda r: r.name)}
    made = 0
    for spec in payload.types:
        name = spec.name.strip()
        if name not in by_name:
            row = await insert_row(db, "pm_task_types", {
                "project_id": root, "name": name, "icon": spec.icon, "color": spec.color,
                "is_default": False, "is_system": False, "is_epic": spec.is_epic,
            })
            by_name[name] = str(row.id)
            made += 1
    return {t.name.strip(): by_name[t.name.strip()] for t in payload.types}, made


async def _apply_fields(db: Any, root: str, org: str, by: str, payload: PayloadV1) -> int:
    """Add each field the space lacks. A key the space holds with another
    type is a 409, because a value of the template would not fit it."""
    existing = {str(d["field_key"]): d for d in await load_definitions(db, root)}
    missing = [f for f in payload.fields if f.field_key not in existing]
    for spec in payload.fields:
        held = existing.get(spec.field_key)
        if held is not None and str(held["field_type"]) != spec.field_type:
            raise _refusal(
                409, FIELD_CONFLICT,
                f"The space already has the field '{spec.field_key}' of type "
                f"{held['field_type']}, and the template has it as {spec.field_type}. "
                "Make the project in another space, or change the field there first.",
            )
    if len(existing) + len(missing) > MAX_FIELDS:
        raise _refusal(
            409, FIELD_CONFLICT,
            f"The space would hold more than {MAX_FIELDS} custom fields. Remove some fields first.",
        )
    for spec in missing:
        # The SQL of `custom_fields.create_field`: `options` is JSONB, and
        # the tenant is explicit for the trigger of migration 161.
        await db.execute(
            text(
                "INSERT INTO pm_custom_fields (project_id, organization_id, field_key, name,"
                " description, field_type, options, position, created_by)"
                " VALUES (CAST(:root AS uuid), CAST(:org AS uuid), :key, :name, :description,"
                " :type, CAST(:options AS jsonb), :position, :by)"
            ),
            {
                "root": root, "org": org, "key": spec.field_key, "name": spec.name.strip(),
                "description": spec.description, "type": spec.field_type,
                "options": json.dumps(spec.options), "position": spec.position, "by": by,
            },
        )
    return len(missing)


async def _apply_tags(db: Any, root: str, by: str, payload: PayloadV1) -> int:
    """Register each tag the space lacks, through ``tags.register`` and its
    cap. A tag the space holds keeps its own colour."""
    before = {r.name.lower() for r in await load_registry_rows(db, root)}
    fresh = [t for t in payload.tags if t.name.strip().lower() not in before]
    await register(db, root, [t.name.strip() for t in fresh], by=by)
    for spec in fresh:
        extra = {k: v for k, v in (("color", spec.color), ("description", spec.description)) if v}
        if not extra:
            continue
        row = (await db.execute(
            text("SELECT id FROM pm_tags WHERE project_id = CAST(:root AS uuid) AND lower(name) = :key"),
            {"root": root, "key": spec.name.strip().lower()},
        )).first()
        if row is not None:
            await update_row(db, "pm_tags", str(row.id), extra)
    return len(fresh)


async def _apply_tasks(
    db: Any, user: UserContext, vis: Visibility, project_id: str, start: date,
    payload: PayloadV1, lanes: dict[str, str], types: dict[str, str],
) -> int:
    """Make the task tree through ``tasks.create_task_in``, parents first."""
    made = 0
    stack: list[tuple[TaskSpec, str | None]] = [(t, None) for t in reversed(payload.tasks)]
    while stack:
        spec, parent = stack.pop()
        values: dict[str, Any] = {"project_id": project_id, "title": spec.title}
        if spec.description is not None:
            values["description"] = spec.description
        if parent is not None:
            values["parent_task_id"] = parent
        if spec.status is not None:
            values["status_id"] = lanes[spec.status.strip().lower()]
        if spec.type is not None:
            values["type_id"] = types[spec.type.strip()]
        if spec.tags:
            values["tags"] = list(spec.tags)
        if spec.custom_fields:
            values["custom_fields"] = dict(spec.custom_fields)
        for key in ("importance", "leveraged", "estimate_mins"):
            if getattr(spec, key) is not None:
                values[key] = getattr(spec, key)
        if spec.start_offset_days is not None:
            values["start_date"] = (start + timedelta(days=spec.start_offset_days)).isoformat()
        if spec.due_offset_days is not None:
            # A date with no time: the one seam stores noon of that day in
            # the caller's zone (`personal.stored_due`, RX-5).
            values["due_at"] = (start + timedelta(days=spec.due_offset_days)).isoformat()
        first = check_task_values(user, values, narrow=False)
        task = await create_task_in(db, user, values, first, vis=vis, align=False)
        made += 1
        stack.extend((c, str(task.row.id)) for c in reversed(spec.children))
    return made


@router.post("/templates/{template_id}/apply", status_code=201, dependencies=_ENABLED)
@_tree_race_as_409
async def apply_template(
    template_id: str, payload: TemplateApplyIn, response: Response,
    user: UserContext = Depends(get_current_user),
) -> dict:
    """Make a new project from a template, in one transaction.

    201 with the new project. The same ``idempotency_key`` with the same body
    again answers 200 with the first project, ``replayed: true``, and makes
    nothing. The same key with another body answers 409.
    """
    tid = _uuid_or_404(template_id, "Template")
    parent = _uuid_or_404(payload.parent_project_id, "Project")
    name = _clean_text(payload.name, "name", PROJECT_NAME_MAX, required=False)
    description = _clean_text(payload.description, "description", DESCRIPTION_MAX, required=False)
    by = actor(user).lower()
    digest = request_hash(tid, payload)
    key = payload.idempotency_key

    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        org = require_organization(vis)
        claim = (await db.execute(
            text(_CLAIM_SQL), {"by": by, "key": key, "hash": digest},
        )).first()
        if claim is None:
            response.status_code = 200
            return await _replay(db, vis, org, by, key, digest)
        reach, params = _reach_clause(vis)
        row = (await db.execute(
            text(
                "SELECT t.* FROM pm_templates t WHERE t.id = CAST(:id AS uuid)"
                f" AND t.organization_id = CAST(:org AS uuid){reach}"
            ),
            {**params, "id": tid, "org": org},
        )).first()
        if row is None:
            raise HTTPException(status_code=404, detail="Template not found")
        if row.kind != PROJECT_KIND:
            raise _refusal(422, NOT_A_PROJECT_TEMPLATE, "This template makes no project.")
        spec = parse_payload(row.payload)
        start = (
            date.fromisoformat(payload.start_date) if payload.start_date
            else await member_today(db, by)
        )

        values: dict[str, Any] = {
            "name": name or row.name,
            "parent_project_id": parent,
        }
        if description is not None:
            values["description"] = description
        kind = check_node_values(user, values)
        node = await create_node_in(db, user, vis, values, kind)
        project_id = str(node.id)

        lanes, owns, lanes_made = await _apply_statuses(db, project_id, spec)
        root = await root_project_id(db, project_id)
        types, types_made = await _apply_types(db, root, spec)
        fields_made = await _apply_fields(db, root, org, by, spec)
        tags_made = await _apply_tags(db, root, by, spec)
        tasks_made = await _apply_tasks(db, user, vis, project_id, start, spec, lanes, types)

        await db.execute(
            text(
                "UPDATE pm_template_applies SET template_id = CAST(:tid AS uuid),"
                " project_id = CAST(:pid AS uuid) WHERE id = CAST(:id AS uuid)"
            ),
            {"tid": tid, "pid": project_id, "id": str(claim.id)},
        )
        await record_activity(
            db, activity_type="system", created_by=actor(user), project_id=project_id,
            body=f"Made from the template '{row.name}'",
            meta={"template_id": tid},
        )
        project = await load_visible_project(db, vis, project_id)
        result = {
            "project": row_to_dict(project, ProjectModel),
            "template_id": tid,
            "replayed": False,
            "own_statuses": owns,
            "created": {
                "statuses": lanes_made, "types": types_made, "fields": fields_made,
                "tags": tags_made, "tasks": tasks_made,
            },
        }

    await emit("pm.project.created", {"project_id": project_id, "name": values["name"]})
    return result


__all__ = [
    "CAPS",
    "INVALID",
    "KEY_USED",
    "MAX_PAYLOAD_BYTES",
    "MAX_TASKS",
    "MAX_TASK_DEPTH",
    "NAME_TAKEN",
    "SOURCE_NOT_ORG_WIDE",
    "TOO_LARGE",
    "apply_template",
    "build_payload",
    "list_templates",
    "parse_payload",
    "require_templates",
    "save_template",
    "templates_allowed",
]
