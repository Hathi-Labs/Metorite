"""Projects · the personal lens — one task store, seen as my own work.

Spec: ``project-docs/specs/project_management_app.md`` §3.11-§3.12, §6.1 ·
**D-PM-6 (revised 2026-08-06)** · ticket WS-27e.

    GET   /projects/my/inbox                     → my work, with my overlay
    GET   /projects/my/inbox?untriaged=true      → …the rows I have not looked at (S6e)
    GET   /projects/my/led                       → the projects I lead (S6e)
    GET   /projects/my/tasks/{task_id}/lanes     → the lanes one of mine can be in (S6e)
    GET   /projects/my/tasks/{task_id}           → one of them, same shape
    GET   /projects/my/project                   → my personal project
    POST  /projects/my/project                   → …creating it if absent
    POST  /projects/my/tasks                     → quick capture into it
    POST  /projects/my/tasks/batch               → many captures, one transaction
    POST  /projects/my/tasks/{task_id}/organize  → one clarify decision, atomically
    PATCH /projects/tasks/{task_id}/personal     → set MY overlay on a task
    GET   /projects/my/contexts                  → the contexts I actually use

**There is no sync here, and that is the whole point.** A task assigned to a
member is not copied into their inbox — it *is* the row in their inbox. So
completing it in the personal view completes it for the project, because there
is one row and one status; and a project manager watching the board sees the
same fact at the same instant.

What is per-member is the **overlay**: disposition, context, energy, defer.
Two people assigned the same task legitimately hold different ones — the person
doing it says NEXT, the person who delegated it says WAITING — which a single
column on ``pm_tasks`` could not express. That is not an edge case; it is what
delegation looks like.

**Identity comes from the session, never from a parameter.** Every route here
resolves the caller and scopes to them; there is deliberately no `?member=`,
so no request can be made to read or write somebody else's practice.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from acb_auth import UserContext, get_current_user
from fastapi import Depends, HTTPException
from gateway.routes.projects.core import (
    _MY_GROUPS_SQL,
    _VISIBLE_PROJECTS_SQL,
    CLOSING_CATEGORIES,
    ListResponse,
    Page,
    TaskModel,
    _bindable,
    _placeholder,
    _tenant_session,
    actor,
    clean_payload,
    coerce_write_values,
    emit,
    from_jsonb,
    insert_row,
    load_default_status,
    load_visible_task,
    next_task_number,
    now,
    record_activity,
    require_organization_of,
    resolve_organization_id,
    resolve_visibility,
    router,
    row_to_dict,
    status_owner_id,
    touch_task,
    update_row,
)
from gateway.routes.projects.filters import attach_assignees

# `notifications` imports only `core` and `watchers`, so this direction adds no
# cycle — the same note `notifications` itself carries about `watchers`, and the
# same import `tasks`, `bulk` and `activities` already take.
from gateway.routes.projects.notifications import EXCERPT_CHARS, notify
from gateway.routes.projects.tasks import MoveTask, move_task_in
from pydantic import BaseModel
from sqlalchemy import text

#: Migration 48's vocabulary, unchanged — WS-27h has to move every gtd_items row
#: onto these and a renamed disposition would make that a translation.
DISPOSITIONS: tuple[str, ...] = (
    "INBOX", "NEXT", "WAITING", "SOMEDAY", "PROJECT", "REFERENCE", "DONE", "TRASH",
)

ENERGIES: tuple[str, ...] = ("low", "medium", "high")

#: The name a member's personal project is created with. Not shown as a project
#: in team lists — it carries `personal_owner`, which every team read excludes.
PERSONAL_PROJECT_NAME = "My tasks"


class PersonalIn(BaseModel):
    """The overlay a member may set on a task. All optional; `null` clears.

    The second group is the **scheduled block** (migration 187, WS-39 S3a). It
    is on the overlay for the same reason ``disposition`` is: two people
    assigned one task each block their own time for it, and a column on
    ``pm_tasks`` would let one silently overwrite the other. The task's
    ``due_at`` is the opposite case — one deadline, shared by everyone — and
    stays on the task, reachable only through the ordinary task routes.
    """

    disposition: str | None = None
    next_action: str | None = None
    context: str | None = None
    energy: str | None = None
    time_estimate_mins: int | None = None
    is_two_minute: bool | None = None
    defer_until: str | None = None

    # ── the block ───────────────────────────────────────────────────────────
    scheduled_start: str | None = None
    scheduled_end: str | None = None
    #: NULL means "never stated", which the reader resolves as flexible. It is
    #: deliberately not the same as an explicit ``false`` — see migration 187.
    flexible: bool | None = None
    is_hard_date: bool | None = None
    actual_start: str | None = None
    actual_end: str | None = None

    # ── the prioritisation matrix (migration 188) ───────────────────────────
    #: The Eisenhower IMPORTANT axis. ⚠️ Not `pm_tasks.importance`, which is the
    #: shared per-task Priority integer the Projects table edits — see 188's
    #: header. `urgent` is the other axis and is deliberately absent: it is
    #: DERIVED from `due_at`, never stored, so accepting it here would create a
    #: second answer to a question the deadline already answers.
    important: bool | None = None
    leveraged: bool | None = None
    deep_work: bool | None = None
    kept_mine: bool | None = None

    #: Manual drag rank in this member's own list. Float so a drop between two
    #: neighbours takes the midpoint instead of renumbering everything after it.
    sort_key: float | None = None

    # ── Waiting-For (migration 188) ─────────────────────────────────────────
    #: {name, email} of whoever the work is with, or null to clear.
    #: ⚠️ 188 CHECKs that `waiting_on IS NULL OR delegated_at IS NOT NULL` — a
    #: chase with no since-when renders with no age — so `set_personal`
    #: validates the MERGED pair before writing, exactly as the block does.
    waiting_on: dict | None = None
    delegated_at: str | None = None
    expected_by: str | None = None
    #: When I last chased the person I am waiting on.
    #: ⚠️ This said "set by the nudge SENDER, which is owner-gated and unbuilt",
    #: which conflated two acts. `POST /tasks/{id}/nudge` writes it now — one
    #: in-app notification, AGENT-SAFE (§9.12.9). An OUTWARD message (mail,
    #: WhatsApp) is the owner-gated one, and is still unbuilt. Accepted on a
    #: PATCH as well, so a client can clear it.
    last_nudged_at: str | None = None


class CaptureIn(BaseModel):
    title: str
    next_action: str | None = None
    context: str | None = None
    due_at: str | None = None
    #: The body of the thought, not just its headline. `pm_tasks.description`
    #: is where it lands — a fact about the WORK, shared by everyone assigned,
    #: which is why it is here and not on the overlay. Added for the Tasks
    #: lens (WS-39 S3a-client): the app has captured notes since it shipped,
    #: and a capture route that silently dropped them would have lost the
    #: contents of every emailed-in task on the first page load after cutover.
    notes: str | None = None


#: How many captures one batch request may carry. The Tasks app's multi-line
#: capture pastes a list, and a list is tens of lines, not thousands. Bounded
#: for the reason `bulk.MAX_BULK` is: one request that can fill the table is a
#: denial-of-service surface, not a feature.
MAX_BATCH = 100


class BatchCaptureIn(BaseModel):
    """Many thoughts, one transaction. Each item is an ordinary :class:`CaptureIn`."""

    items: list[CaptureIn]


class OrganizeAssignee(BaseModel):
    """Who a decision hands the work to. `email` is the identity when present;
    `name` alone is accepted because the picker may hold a person with no
    address yet (the same rule ``lens.ts::lensDelegateItem`` applies)."""

    name: str
    email: str | None = None
    provider_user_id: str | None = None


class OrganizeIn(BaseModel):
    """One clarify decision — the personal-lens twin of
    ``routes/tasks/items.py::OrganizeRequest`` (WS-39 S6a).

    Two of that model's fields are deliberately absent. ``account_id`` named a
    connected workspace, and there are none (D52). ``status`` was a provider
    stage; under one store the lane is the project's own status, and the
    client resolves a stage NAME to a ``status_id`` through
    ``PATCH /projects/tasks/{id}`` (my_tasks_cutover.md §4.6), not here.
    """

    kind: str
    next_action: str | None = None
    outcome: str | None = None
    context: str | None = None
    energy: str | None = None
    time_estimate_mins: int | None = None
    due_at: str | None = None
    project_id: str | None = None
    assignee: OrganizeAssignee | None = None
    subtasks: list[str] | None = None


#: A clarify `kind` → the overlay disposition it states. The vocabulary
#: `routes/tasks/items.py::_KIND_TO_DISPOSITION` carried, moved here because
#: that module retires with `gtd_items` and this one does not. One change from
#: it, and it is the point: ``do-now`` is DONE there and is NOT a disposition
#: here — under one store a task is completed through the project's done
#: lane (`_complete`), never by writing DONE onto my view of it
#: (task_manager_app.md §13.5a decision 1).
ORGANIZE_KINDS: dict[str, str] = {
    "next": "NEXT", "calendar": "NEXT", "delegate": "WAITING",
    "someday": "SOMEDAY", "do-now": "DONE", "reference": "REFERENCE",
    "trash": "TRASH", "project": "NEXT",
}

#: The kinds that carry an ACTION, so a next action is required.
_ACTIONABLE_KINDS = ("next", "calendar", "delegate", "project")


# ── The derived disposition ─────────────────────────────────────────────────

def derive_disposition(
    *, status_category: str, is_mine: bool, has_assignee: bool,
) -> str:
    """The disposition a member has NOT stated, read off the task itself.

    The same lens the ClickUp pull has always applied (``routes/tasks/sync.py``),
    lifted here so both halves of the app agree about what an untriaged task
    means:

        closed in the tool          → DONE
        backlog-ish                 → SOMEDAY
        assigned to me              → NEXT
        assigned to somebody else   → WAITING
        unassigned                  → INBOX

    Derived, never written. Storing it on first read would turn "never triaged"
    into "triaged to NEXT" and quietly empty the Weekly Review — the one
    question that review exists to ask is which tasks the member has not looked
    at, and that is exactly the rows with no stated disposition.
    """
    if status_category in CLOSING_CATEGORIES:
        return "DONE"
    if status_category == "backlog":
        return "SOMEDAY"
    if is_mine:
        return "NEXT"
    if has_assignee:
        return "WAITING"
    return "INBOX"


# ── The personal project ────────────────────────────────────────────────────

async def _load_personal_project(db: Any, email: str) -> Any | None:
    """This member's personal ROOT — the inbox their captures land in.

    ⚠️ ``AND parent_project_id IS NULL`` is load-bearing since migration **191**.
    Before it, ``personal_owner`` was unique across every row so "the row with
    my address on it" and "my root" were the same question. They are not any
    more: the column now means *private to this person* at every depth, and a
    member's categories carry it too. Without the predicate this returns an
    arbitrary node of the tree — and the row it returns is used as a WRITE
    TARGET by :func:`ensure_personal_project` and :func:`capture`, so a quick
    capture would land in whichever category the planner happened to pick.

    Keyed on the email alone and NOT on the tenant, which is safe for exactly
    one reason and it is worth naming: D-MT-1 (a) makes `app_user.email`
    globally unique, so an email identifies one person in one organization. If
    D-MT-1 is ever revisited this lookup is one of the places that has to grow a
    tenant predicate — the project it returns is then used as a write target.
    """
    return (await db.execute(
        text(
            "SELECT * FROM pm_projects "
            "WHERE lower(personal_owner) = :who AND parent_project_id IS NULL"
        ),
        {"who": email},
    )).fetchone()


async def ensure_personal_project(db: Any, email: str) -> Any:
    """This member's personal project, created on first use.

    Idempotent by the partial unique index on ``lower(personal_owner)``: two
    concurrent captures cannot mint two personal projects, and the loser of that
    race re-reads the winner's rather than failing the capture.

    It is an ordinary project — it gets statuses, a counter and a grant like any
    other. That is what makes a private todo a first-class task: the board, the
    timeline, automation and agent dispatch all work on it with no special case.
    """
    existing = await _load_personal_project(db, email)
    if existing is not None:
        return existing

    # WS-29a. A personal project is a ROOT project, so nothing upstream can
    # supply its tenant — this is the second (and last) place in the package
    # that decides one. Resolved from the directory rather than taken from a
    # `Visibility` because two of the three callers do not have one, and a
    # signature change would push the decision back out to them.
    organization_id = await require_organization_of(db, email)

    project = await insert_row(db, "pm_projects", {
        "name": PERSONAL_PROJECT_NAME,
        "description": "Work only you can see. Tasks assigned to you from team "
                       "projects appear in your inbox without living here.",
        "personal_owner": email,
        "created_by": email,
        "source": "manual",
        "organization_id": organization_id,
        # A personal project is a ROOT, so it owns its statuses — the four
        # seeded below. Migration 196's CHECK refuses a root that owns nothing,
        # because a root has nothing above it to inherit from.
        "owns_statuses": True,
    })
    project_id = str(project.id)
    await _grant_owner(db, project_id, email)
    # Ordered, and the order is the whole answer: a capture lands in the FIRST
    # lane. There is no `is_default` on a status any more (2026-09-06) — see
    # `core.load_default_status` — and "Inbox" leads because it is first.
    for position, (name, category) in enumerate((
        ("Inbox", "backlog"),
        ("Next", "todo"),
        ("Doing", "in_progress"),
        ("Done", "done"),
    )):
        await insert_row(db, "pm_task_statuses", {
            "project_id": project_id, "name": name, "category": category,
            "position": (position + 1) * 10,
        })
    return project


async def _grant_owner(db: Any, project_id: str, email: str) -> None:
    """The grant every personal node carries — root and child alike.

    The grant is what the visibility model reads; `personal_owner` is only the
    fast path to finding it. Both, so neither is load-bearing alone.
    """
    await db.execute(
        text(
            "INSERT INTO pm_project_grants (project_id, subject, created_by) "
            "VALUES (CAST(:pid AS uuid), :who, :who) "
            "ON CONFLICT (project_id, subject) DO NOTHING"
        ),
        {"pid": project_id, "who": email},
    )


async def find_personal_child(
    db: Any, email: str, root: Any, name: str,
) -> Any | None:
    """A LIVE child of my root called ``name``, case-insensitively, or None.

    The one name-collision lookup. ``organize`` reuses a match;
    ``POST /my/areas`` refuses on one. An archived Area of the same name is
    not a match: it stays archived, and a new one may be minted beside it.
    """
    return (await db.execute(
        text(
            "SELECT * FROM pm_projects "
            "WHERE parent_project_id = CAST(:root AS uuid) "
            "  AND lower(personal_owner) = :who "
            "  AND lower(name) = :name AND archived_at IS NULL "
            "ORDER BY created_at LIMIT 1"
        ),
        {"root": str(root.id), "who": email, "name": name.strip().lower()},
    )).fetchone()


async def mint_personal_child(db: Any, email: str, root: Any, name: str) -> Any:
    """A CHILD of this member's personal root, private like the root.

    THE ONE minting path for a personal node below the root (WS-39 S6a and
    #391's Areas, reconciled 2026-09-23). Before it only
    :func:`ensure_personal_project` wrote ``personal_owner``, and only for a
    root. A child carries the owner too — since migration 191 the column
    means *private to this person* at every depth — so the team tree
    (``tree.py``, ``personal_owner IS NULL``) never lists it, and
    ``assert_move_keeps_privacy`` reads root and child as one private tree.

    ⚠️ **It INHERITS the root's lanes** (``owns_statuses`` false, migration
    196), and that is a decision rather than an omission. A member's private
    tree has ONE lane vocabulary — Inbox, Next, Doing, Done — so a task moved
    from the root into a child keeps its ``status_id`` and never passes
    through ``remap_one_status``, and a lane renamed on the root is renamed
    everywhere at once. A child that owned a copy of the set would be four
    more rows per Area and a remap on every move.

    It carries the owner's grant row, like the root: the grant is what the
    visibility model reads, and ``personal_owner`` is only the fast path.

    Does NOT check the name. Callers decide what a collision means:
    :func:`ensure_personal_child` reuses, ``create_my_area`` refuses.
    """
    clean = (name or "").strip()
    if not clean:
        raise HTTPException(status_code=422, detail="A project needs a name.")
    child = await insert_row(db, "pm_projects", {
        "name": clean,
        "personal_owner": email,
        "created_by": email,
        "source": "manual",
        "parent_project_id": str(root.id),
        # The tenant travels with the row (R5). Read off the root rather than
        # the directory: the root already decided it, and a second lookup is
        # a second chance to answer differently.
        "organization_id": getattr(root, "organization_id", None),
        "owns_statuses": False,
    })
    await _grant_owner(db, str(child.id), email)
    return child


async def ensure_personal_child(db: Any, email: str, name: str) -> Any:
    """Find or create, by name.

    Two clarifies into "Website redesign" share one Area rather than minting
    two, the way a folder named twice is one folder. The root is minted first
    if this is the member's first use of their tree.
    """
    clean = (name or "").strip()
    if not clean:
        raise HTTPException(status_code=422, detail="A project needs a name.")
    root = await ensure_personal_project(db, email)
    existing = await find_personal_child(db, email, root, clean)
    if existing is not None:
        return existing
    return await mint_personal_child(db, email, root, clean)


@router.get("/my/project")
async def get_my_project(user: UserContext = Depends(get_current_user)) -> dict:
    """My personal project, or 404 if I have never captured anything."""
    email = actor(user).lower()
    async with _tenant_session() as db:
        row = await _load_personal_project(db, email)
        if row is None:
            raise HTTPException(status_code=404, detail="No personal project yet")
        return {"id": str(row.id), "name": row.name}


@router.post("/my/project", status_code=201)
async def create_my_project(user: UserContext = Depends(get_current_user)) -> dict:
    email = actor(user).lower()
    async with _tenant_session() as db:
        row = await ensure_personal_project(db, email)
        return {"id": str(row.id), "name": row.name}


@router.post("/my/tasks", status_code=201)
async def capture(
    payload: CaptureIn, user: UserContext = Depends(get_current_user),
) -> dict:
    """Quick capture — a thought into my personal project, assigned to me.

    GTD's first discipline is that capture must be frictionless, so this takes a
    title and nothing else is required: no project to choose, no status to pick.
    The task it creates is an ordinary ``pm_tasks`` row, which is what lets a
    captured thought later be moved into a team project without being recreated.
    """
    title = _clean_title(payload)

    email = actor(user).lower()
    async with _tenant_session() as db:
        project = await ensure_personal_project(db, email)
        project_id = str(project.id)
        status = await load_default_status(db, project_id)
        overlay = None
        if payload.next_action or payload.context:
            overlay = {"next_action": payload.next_action,
                       "context": payload.context}
        task = await create_personal_task(
            db, email, project_id, project_id, str(status.id), {
                "title": title,
                "description": payload.notes,
                "due_at": payload.due_at,
                "source": "manual",
            }, overlay)
        return row_to_dict(task, TaskModel)


async def create_personal_task(
    db: Any, email: str, project_id: str, root_id: str, status_id: str,
    values: dict[str, Any], overlay: dict[str, Any] | None = None,
) -> Any:
    """One `pm_tasks` row in the member's own tree, assigned to them.

    The one capture path. `POST /projects/my/tasks` calls it, and so does the
    AI seam's pm arm for every email and WhatsApp capture
    (`routes/projects/item_lens.py`). Four effects, one helper: the row, the
    assignee row (what the board's "mine" reads), the member's overlay when
    there is one, and the `pm.task.created` event the workflow engine binds
    triggers to (`routes/workflows/catalog.py`). A second capture site that
    forgot the event would create tasks no automation ever sees.

    `values` carries the task columns beside the ones this helper owns:
    `title`, `description`, `due_at`, `source`, `parent_task_id`, `origin`.

    **A capture states INBOX** (S8a, `task_manager_app.md` §13.5a decision 4).
    A row with no overlay DERIVES its disposition off the lane, and the
    personal root's first lane is `backlog`, so a fresh capture read as
    SOMEDAY: `/my/inbox?disposition=INBOX`, the browser's Inbox and
    `insight_counts` never showed it. The old store defaulted the column to
    INBOX. So when the caller states no disposition, the overlay row is
    written with `disposition = 'INBOX'` and `clarified_at` NULL — a stated
    inbox item until clarified. A SUBTASK (`parent_task_id` set) is a step,
    not a capture, and gets no default: `POST /projects/tasks` writes none
    either, and the two ways of adding a step must agree.
    """
    stated = dict(overlay or {})
    if stated.get("disposition") is None and not values.get("parent_task_id"):
        stated["disposition"] = "INBOX"
    overlay = stated or None
    task = await insert_row(db, "pm_tasks", {
        "project_id": project_id,
        "root_project_id": root_id,
        "task_number": await next_task_number(db, root_id),
        "status_id": status_id,
        "created_by": email,
        **values,
    })
    task_id = str(task.id)
    await db.execute(
        text(
            "INSERT INTO pm_task_assignees (task_id, assignee, assigned_by) "
            "VALUES (CAST(:tid AS uuid), :who, :who) "
            "ON CONFLICT (task_id, assignee) DO NOTHING"
        ),
        {"tid": task_id, "who": email},
    )
    if overlay:
        await _upsert_personal(db, task_id, email, overlay)
    await record_activity(
        db, activity_type="system", created_by=email, task_id=task_id,
        body="Captured",
    )
    await emit("pm.task.created", {
        "task_id": task_id, "project_id": project_id,
        "title": str(values.get("title") or ""),
    })
    return task


def _clean_title(payload: CaptureIn) -> str:
    """The title, or the 422 a blank one earns — BEFORE any write."""
    title = (payload.title or "").strip()
    if not title:
        raise HTTPException(status_code=422, detail="A task needs a title.")
    return title


@router.post("/my/tasks/batch", status_code=201)
async def capture_batch(
    payload: BatchCaptureIn, user: UserContext = Depends(get_current_user),
) -> ListResponse:
    """Many captures, ONE transaction — the multi-line capture box.

    WS-39 S6a. All or nothing: a paste of twelve lines that lands seven is
    worse than one that lands none, because the person has to work out which
    seven. The shape is validated up front (a blank line is a 422 before any
    row is written), and a failure on any item rolls the rest back.

    Answers in ``/my/inbox``'s exact row shape, in the order sent, so the
    client swaps its optimistic rows by index.
    """
    items = payload.items or []
    if not items:
        raise HTTPException(status_code=422, detail="No tasks to capture.")
    if len(items) > MAX_BATCH:
        raise HTTPException(
            status_code=422,
            detail=f"At most {MAX_BATCH} tasks per batch; got {len(items)}.",
        )
    for item in items:
        _clean_title(item)

    email = actor(user).lower()
    async with _tenant_session() as db:
        project = await ensure_personal_project(db, email)
        project_id = str(project.id)
        status = await load_default_status(db, project_id)
        rows: list[dict[str, Any]] = []
        for item in items:
            # The ONE capture path: `create_personal_task` owns the row, the
            # assignee, the overlay and the `pm.task.created` event.
            overlay = None
            if item.next_action or item.context:
                overlay = {"next_action": item.next_action,
                           "context": item.context}
            task = await create_personal_task(
                db, email, project_id, project_id, str(status.id), {
                    "title": _clean_title(item),
                    "description": item.notes,
                    "due_at": item.due_at,
                    "source": "manual",
                }, overlay)
            rows.append(await _read_my_task(db, email, str(task.id)))
    return ListResponse(rows=rows, total=len(rows))


# ── The overlay ─────────────────────────────────────────────────────────────

async def _upsert_personal(
    db: Any, task_id: str, email: str, values: dict[str, Any],
) -> Any:
    """Write MY overlay row for a task. Never anybody else's."""
    columns = ["task_id", "member_email", *values]
    assignments = ", ".join(f"{c} = EXCLUDED.{c}" for c in values)
    # `pm_task_personal` has a COMPOSITE key, so the shared `update_row` helper
    # — which keys on `id` — cannot serve it; this upsert is written out.
    #
    # ⚠️ The placeholders and the binds go through the SHARED helpers rather
    # than being spelled inline, and migration 188 is why. This function used to
    # emit a bare `:{column}` for every value and bind through
    # `coerce_write_values`, which is correct for exactly as long as the overlay
    # holds no jsonb. `waiting_on` is jsonb, and it needs BOTH halves that the
    # inline form skips: `_placeholder` adds the `CAST(... AS jsonb)` without
    # which Postgres refuses to bind text to jsonb, and `_bindable` runs the
    # `json.dumps` without which a bare dict reaches asyncpg, which has no codec
    # for it. Using the seam means the next jsonb column on this table needs no
    # change here at all — which is the point of there being one seam.
    return (await db.execute(
        text(
            f"INSERT INTO pm_task_personal ({', '.join(columns)}) "
            f"VALUES (CAST(:task_id AS uuid), :member_email, "
            f"{', '.join(_placeholder(c) for c in values)}) "
            f"ON CONFLICT (task_id, member_email) DO UPDATE "
            f"SET {assignments}, updated_at = now() "
            f"RETURNING *"
        ),
        {"task_id": task_id, "member_email": email, **_bindable(values)},
    )).fetchone()


def _as_utc(value: Any) -> Any:
    """A naive instant is read as UTC; anything else is returned untouched.

    ⚠️ **This is what keeps a `datetime-local` input out of a 500.** The browser's
    `<input type="datetime-local">` submits `2026-08-25T10:00` — **no offset** —
    and `coerce_write_values` parses that to a NAIVE `datetime`, while the value
    read back from a `timestamptz` column is AWARE. Comparing the two raises
    ``TypeError: can't compare offset-naive and offset-aware datetimes``, which
    surfaces as a 500 on the calendar's most ordinary interaction — exactly the
    "client error reported as a server fault" that `_reject_impossible_block`
    exists to prevent, reintroduced one line below the fix.

    UTC is this package's existing convention for a naive instant, not a new
    rule: `filters.py::_instant`, `delta.py:414`, `custom_fields.py:194` and
    `recurrence.py:215` all say ``parsed if parsed.tzinfo else
    parsed.replace(tzinfo=UTC)``. Postgres would apply the SESSION TimeZone to a
    naive bind, so leaving it unstated makes the stored instant depend on a
    connection setting.
    """
    if isinstance(value, datetime) and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


async def _reject_impossible_block(
    db: Any, task_id: str, email: str, values: dict[str, Any],
) -> None:
    """422 when the overlay's block would end at or before it starts.

    Mirrors migration 187's `pm_task_personal_block_order_check` over the MERGED
    row (stored ∪ payload), because a partial PATCH cannot be judged on its own.
    A field explicitly sent as `null` clears the stored value, so `clean_payload`
    keeping it in `values` is load-bearing here: `"scheduled_end": None` must be
    read as "unset it", not as "leave it alone".
    """
    keys = ("scheduled_start", "scheduled_end")
    if not any(k in values for k in keys):
        return
    stored = (await db.execute(
        text(
            "SELECT scheduled_start, scheduled_end FROM pm_task_personal "
            "WHERE task_id = CAST(:task_id AS uuid) AND lower(member_email) = :who"
        ),
        {"task_id": task_id, "who": email},
    )).fetchone()

    merged = coerce_write_values({k: values[k] for k in keys if k in values})
    start = _as_utc(merged.get("scheduled_start",
                               getattr(stored, "scheduled_start", None)))
    end = _as_utc(merged.get("scheduled_end",
                             getattr(stored, "scheduled_end", None)))

    # Half-open is legal on purpose: a block with a start and no end is an
    # open-ended one, which the calendar renders as "started, still going".
    if start is not None and end is not None and end <= start:
        raise HTTPException(
            status_code=422,
            detail=(
                "scheduled_end must be after scheduled_start "
                f"(got start={start.isoformat()}, end={end.isoformat()})."
            ),
        )


async def _reject_waiting_without_since(
    db: Any, task_id: str, email: str, values: dict[str, Any],
) -> None:
    """422 when the overlay would say "waiting on somebody" since never.

    Mirrors migration 188's `pm_task_personal_waiting_since_check` over the
    MERGED row, for the same reason `_reject_impossible_block` does: a PATCH is
    partial, so whether `{"waiting_on": {...}}` alone is legal depends on
    whether a `delegated_at` is ALREADY STORED. Judged on the payload alone it
    would reach the constraint and surface as a 500 — the API blaming itself for
    the caller's omission.

    The rule is not bookkeeping. The Waiting-For view's whole job is
    who / what / **since when** (`task_manager_app.md` §6), and a row without
    the since-when renders as a chase with no age — which is precisely the
    column a person scans to decide whether to nudge.
    """
    keys = ("waiting_on", "delegated_at")
    if not any(k in values for k in keys):
        return
    stored = (await db.execute(
        text(
            "SELECT waiting_on, delegated_at FROM pm_task_personal "
            "WHERE task_id = CAST(:task_id AS uuid) AND lower(member_email) = :who"
        ),
        {"task_id": task_id, "who": email},
    )).fetchone()

    merged = coerce_write_values({k: values[k] for k in keys if k in values})
    who = merged.get("waiting_on", getattr(stored, "waiting_on", None))
    since = merged.get("delegated_at", getattr(stored, "delegated_at", None))

    # Clearing `waiting_on` is always legal — that is how a delegation resolves,
    # and it must not be blocked by the absence of a date it is removing.
    if who is not None and since is None:
        raise HTTPException(
            status_code=422,
            detail=(
                "delegated_at is required when waiting_on is set — the "
                "Waiting-For list is who / what / since-when, and a row with "
                "no since-when has no age to scan."
            ),
        )


def validate_overlay(values: dict[str, Any]) -> dict[str, Any]:
    """The two vocabulary checks every overlay write makes, or a 422.

    One function because there are now three writers — ``set_personal``, the
    bulk ``personal`` action and ``organize`` — and three copies of a
    vocabulary check is how one of them accepts a word the others refuse.
    """
    if values.get("disposition") is not None and values["disposition"] not in DISPOSITIONS:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown disposition. One of: {list(DISPOSITIONS)}.",
        )
    if values.get("energy") is not None and values["energy"] not in ENERGIES:
        raise HTTPException(
            status_code=422, detail=f"Unknown energy. One of: {list(ENERGIES)}.",
        )
    return values


@router.patch("/tasks/{task_id}/personal")
async def set_personal(
    task_id: str, payload: PersonalIn,
    user: UserContext = Depends(get_current_user),
) -> dict:
    """Set my overlay on a task.

    Writes **only** to ``pm_task_personal``. It cannot touch the task's shared
    columns — a member filing something as SOMEDAY must not move it on the
    team's board — which is the structural half of "the overlay is never
    clobbered", now true in both directions.
    """
    values = validate_overlay(clean_payload(payload))

    email = actor(user).lower()
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        # Seeing the task is the floor. Assignment already satisfies it
        # (`load_visible_task`), so a task delegated across a Center boundary is
        # triageable by the person asked to do it — which is the case that would
        # otherwise be unusable.
        await load_visible_task(db, vis, task_id)

        # ── The block must still be a block AFTER the merge ─────────────────
        #
        # Migration 187's CHECK refuses `scheduled_end <= scheduled_start`, and
        # the database is right to. But a PATCH is partial: a caller may send
        # only `scheduled_end`, and whether that is legal depends on the start
        # ALREADY STORED. Validating just the payload would let that request
        # through to the constraint and surface a 500 for what is a 422 — a
        # client error reported as a server fault, on the calendar's most
        # ordinary interaction (drag the bottom edge of a block).
        #
        # So the merged pair is what is checked. One extra read on a write path,
        # in exchange for the API telling the truth about whose fault it is.
        await _reject_impossible_block(db, task_id, email, values)
        await _reject_waiting_without_since(db, task_id, email, values)

        # Triage is recorded even when nothing changed: "when did I last look at
        # this" is the Weekly Review's question, and a no-op PATCH is still a
        # member looking at it.
        values["clarified_at"] = now()
        row = await _upsert_personal(db, task_id, email, values)
        return _personal_to_dict(row)


def _iso(row: Any, field: str) -> str | None:
    """An instant column as ISO-8601, or None. Written once because the block
    added six of them and six inline conditionals is how one gets it wrong."""
    value = getattr(row, field, None)
    return value.isoformat() if value is not None else None


def _personal_to_dict(row: Any) -> dict[str, Any]:
    return {
        "task_id": str(getattr(row, "task_id", "")),
        "disposition": getattr(row, "disposition", None),
        "next_action": getattr(row, "next_action", None),
        "context": getattr(row, "context", None),
        "energy": getattr(row, "energy", None),
        "time_estimate_mins": getattr(row, "time_estimate_mins", None),
        "is_two_minute": bool(getattr(row, "is_two_minute", False)),
        "defer_until": _iso(row, "defer_until"),
        # The block. ⚠️ `flexible` and `is_hard_date` are passed through as
        # tri-state (None / True / False) rather than coerced with `bool()`:
        # None means "never stated" and collapsing it to False here would erase
        # the distinction migration 187 keeps a nullable column to preserve.
        "scheduled_start": _iso(row, "scheduled_start"),
        "scheduled_end": _iso(row, "scheduled_end"),
        "flexible": getattr(row, "flexible", None),
        "is_hard_date": getattr(row, "is_hard_date", None),
        "actual_start": _iso(row, "actual_start"),
        "actual_end": _iso(row, "actual_end"),
        # ── The matrix + rank (188). Same tri-state rule as `flexible` above:
        # never `bool()`-ed, because "has not triaged" and "decided: not
        # important" are different answers and only one of them should be
        # nudged.
        "important": getattr(row, "important", None),
        "leveraged": getattr(row, "leveraged", None),
        "deep_work": getattr(row, "deep_work", None),
        "kept_mine": getattr(row, "kept_mine", None),
        "sort_key": getattr(row, "sort_key", None),
        # ── Waiting-For (188). `waiting_on` goes through `from_jsonb` because
        # bare `text()` over asyncpg hands jsonb back as a STRING — there is no
        # declared column type to decode against — and a client typed `dict`
        # would otherwise receive '{"email": "..."}' as text.
        "waiting_on": from_jsonb(getattr(row, "waiting_on", None)),
        "delegated_at": _iso(row, "delegated_at"),
        "expected_by": _iso(row, "expected_by"),
        "last_nudged_at": _iso(row, "last_nudged_at"),
        # Written since 147 (on every triage), projected since now. It was
        # collecting a real value that no caller could read — the Weekly
        # Review's "when did I last look at this" had no way to ask.
        "clarified_at": _iso(row, "clarified_at"),
    }


#: Overlay columns that are passed through EXACTLY as stored — tri-state, never
#: coerced. NULL means "this member has never stated it", which is a different
#: answer from `false`/`0` and is the one the triage nudge looks for.
_OVERLAY_PASSTHROUGH = (
    "next_action", "context", "energy", "time_estimate_mins",
    "flexible", "is_hard_date",
    "important", "leveraged", "deep_work", "kept_mine", "sort_key",
)

#: Overlay instants. Rendered ISO-8601, or None.
_OVERLAY_INSTANTS = (
    "scheduled_start", "scheduled_end", "actual_start", "actual_end",
    "defer_until", "delegated_at", "expected_by", "last_nudged_at",
    "clarified_at",
)


def _apply_overlay(task: dict[str, Any], row: Any) -> None:
    """Copy THIS member's overlay off a `_MY_TASKS_SQL` row onto the task dict.

    Written as one function because `my_inbox` and `my_calendar` had grown two
    copies of the same fifteen lines, and the failure mode of that duplication
    is not hypothetical: it is silent, and it is one-sided. A field added to the
    list projection but not the calendar projection produces a calendar where
    that field is simply absent — no error, no 500, just a flag that never
    arrives — and the hermetic fake agrees, because a fake answers `None` for a
    column nobody selected. Migration 188 adds nine at once, which is nine
    chances to make that mistake twice.

    Both callers project the SAME overlay on purpose. The two surfaces disagree
    about which rows they want — a window versus an inbox — never about what a
    task looks like once chosen, and one shape means the client needs one mapper
    rather than two that drift.
    """
    for field in _OVERLAY_PASSTHROUGH:
        task[field] = getattr(row, f"p_{field}", None)
    for field in _OVERLAY_INSTANTS:
        value = getattr(row, f"p_{field}", None)
        task[field] = value.isoformat() if value is not None else None
    # `is_two_minute` is the one deliberate exception to the tri-state rule: it
    # is a "did the 2-minute rule fire" marker with no meaningful unset state,
    # and it read as a plain bool before 188. Kept that way rather than widened
    # in passing — a wire-shape change is not a free rider on a column add.
    task["is_two_minute"] = bool(getattr(row, "p_is_two_minute", False))
    # jsonb over bare `text()` arrives as a STRING (no declared column type to
    # decode against), so a client typed `dict` would otherwise be handed
    # '{"email": "..."}' as text.
    task["waiting_on"] = from_jsonb(getattr(row, "p_waiting_on", None))


# ── The inbox ───────────────────────────────────────────────────────────────

#: **The membership + tenancy skeleton, defined once.**
#:
#: Split out of ``_MY_TASKS_SQL`` for WS-39 S3a-client slice 2, when the day
#: planner became a second reader of "which tasks are this member's". It is the
#: FROM/JOIN/WHERE half only, so a caller supplies its own SELECT list and gets
#: the same answer to the only question that must never have two answers.
#:
#: ⚠️ Two clauses here are load-bearing and neither is obvious:
#:
#: * ``t.organization_id = :vis_org`` sits ABOVE both arms (WS-29b). The first
#:   arm reaches tasks by matching a bare, unvalidated email, so without it
#:   another organization can place a row in this member's list by typing their
#:   address. The GRANT clause is deliberately absent — the tenant is not.
#: * the personal-project arm keeps a task I captured and then unassigned. Drop
#:   it and clearing my own name off a private todo makes it vanish from the
#:   only place it exists.
#: * the WAITING arm (WS-39 S6a, 2026-09-23) keeps a task I delegated AWAY.
#:   Delegating replaces the assignee with the person doing the work, so the
#:   first arm stops matching the moment I hand it over — and a team task I
#:   was waiting on vanished from the only list that exists to chase it.
#:   Measured as a 404 on `organize`'s read-back; `lensDelegateItem` had the
#:   same hole. The chase is mine until my overlay stops saying so.
#:
#:   ⚠️ **BOUNDED by my grant closure, and the bound is the rule: the overlay
#:   may NARROW a grant, never WIDEN one.** An overlay row is a thing the
#:   member writes for themselves, and an unbounded arm would be a read grant
#:   no revocation reaches — remove Alice from a group and from every task,
#:   and her WAITING rows would keep the team's work in her inbox, her
#:   calendar and her read-back. So the arm carries the closure the write side
#:   checks (`Visibility.project_clause`, `_VISIBLE_PROJECTS_SQL`) on the
#:   task's ROOT. The closure is the member's own grants whoever they are:
#:   `data:org:read` widens the BOARD, not a member's private list.
#:   Consequence, accepted and recorded in `my_tasks_cutover.md` §5 S6a: a
#:   member who reached a task by assignment ALONE and delegates it loses it
#:   from their lists, because they hold no grant to keep it by.
#:
#: Binds: ``:who`` (lower-cased email), ``:vis_org``, ``:archived``, and the
#: closure's ``:vis_email`` and ``:vis_groups``. :func:`my_tasks_binds` is
#: the one place they are assembled, so a composer cannot forget one — a
#: missing bind raises at the seam before a row is returned (the R7 fence
#: the ``:archived`` note below describes).
MY_TASKS_FROM = """
FROM pm_tasks t
JOIN pm_task_statuses s ON s.id = t.status_id
LEFT JOIN pm_task_personal p
       ON p.task_id = t.id AND lower(p.member_email) = :who
LEFT JOIN pm_projects proj ON proj.id = t.project_id
WHERE (t.archived_at IS NULL OR CAST(:archived AS boolean))
  AND t.organization_id = CAST(:vis_org AS uuid)
  AND (
        EXISTS (SELECT 1 FROM pm_task_assignees a
                WHERE a.task_id = t.id AND lower(a.assignee) = :who)
     OR lower(proj.personal_owner) = :who
     OR (p.disposition = 'WAITING'
         AND t.root_project_id IN (""" + _VISIBLE_PROJECTS_SQL + """))
  )
"""


async def my_tasks_binds(
    db: Any, who: str, *, archived: bool, **extra: Any,
) -> dict[str, Any]:
    """Every bind ``MY_TASKS_FROM`` needs, assembled once.

    ``who`` is the member. The tenant and the grant closure are resolved from
    the directory, never taken from a request. Every composer of the fragment
    — the inbox, the single read, the calendar, the planner's ``_LensSource``
    and the AI seam's ``_PmLens`` — binds through here, so the WAITING arm's
    closure cannot be left half-bound by one of them.

    The closure is the member's OWN grants and groups. ``resolve_visibility``
    would answer ``unrestricted`` for a ``data:org:read`` holder and drop the
    groups; that permission widens the board, and a member's private list is
    not the board (the fragment's docstring).
    """
    email = (who or "").strip().lower()
    groups = (await db.execute(text(_MY_GROUPS_SQL), {"email": email})).fetchall()
    return {
        "who": email,
        "vis_org": await resolve_organization_id(db, email),
        "vis_email": email,
        "vis_groups": [r.subject for r in groups if getattr(r, "subject", None)],
        "archived": archived,
        **extra,
    }


#: My work: everything assigned to me, plus everything in my personal project.
#:
#: The second arm matters — a task I captured and then unassigned is still mine
#: to see; without it, clearing my own name off a private todo would make it
#: vanish from the only place it exists.
#:
#: ⚠️ **``:archived`` is a REQUIRED bind, deliberately.** The archived filter used
#: to be a literal here, which meant the Archive view had no source at all. It
#: could have been made optional by defaulting to "active only" — but a caller
#: who forgets an optional filter leaks archived rows into a live list SILENTLY,
#: and this SQL is the one place in the app where "everything assigned to me"
#: is computed. Left as a bind with no default, SQLAlchemy raises
#: ``StatementError: A value is required for bind parameter 'archived'`` on the
#: first request — loud, at the seam, before any row is returned. That is the
#: fence (R7): there is no test to forget, because the query cannot run.
#:
#: ⚠️ ``t.organization_id = :vis_org`` is composed ABOVE both arms (WS-29b), for
#: the same reason as ``me.assigned_to_me``: the first arm reaches tasks by
#: matching a bare, unvalidated email, so without it another organization can
#: place a row in this member's inbox by typing their address. The GRANT clause
#: is still deliberately absent — the tenant is not.
_MY_TASKS_SQL = """
SELECT t.*,
       s.category           AS status_category,
       p.disposition        AS p_disposition,
       p.next_action        AS p_next_action,
       p.context            AS p_context,
       p.energy             AS p_energy,
       p.time_estimate_mins AS p_time_estimate_mins,
       p.is_two_minute      AS p_is_two_minute,
       p.defer_until        AS p_defer_until,
       p.scheduled_start    AS p_scheduled_start,
       p.scheduled_end      AS p_scheduled_end,
       p.flexible           AS p_flexible,
       p.is_hard_date       AS p_is_hard_date,
       p.actual_start       AS p_actual_start,
       p.actual_end         AS p_actual_end,
       p.important          AS p_important,
       p.leveraged          AS p_leveraged,
       p.deep_work          AS p_deep_work,
       p.kept_mine          AS p_kept_mine,
       p.sort_key           AS p_sort_key,
       p.waiting_on         AS p_waiting_on,
       p.delegated_at       AS p_delegated_at,
       p.expected_by        AS p_expected_by,
       p.last_nudged_at     AS p_last_nudged_at,
       p.clarified_at       AS p_clarified_at,
       s.name               AS workflow_stage,
       proj.name            AS project_name,
       (SELECT count(*) FROM pm_tasks c
         WHERE c.parent_task_id = t.id AND c.archived_at IS NULL)
                            AS subtask_count,
       (SELECT count(*) FROM pm_task_assignees a2 WHERE a2.task_id = t.id)
                            AS assignee_count,
       EXISTS (SELECT 1 FROM pm_task_assignees a3
               WHERE a3.task_id = t.id AND lower(a3.assignee) = :who)
                            AS is_mine
""" + MY_TASKS_FROM

#: The inbox's order, and it is not cosmetic. `my_inbox` pages in Python over
#: the rows this query returns, and an unordered SELECT may answer two
#: consecutive page requests in two orders — a row twice, another never, and
#: no error anywhere. The rule is the one `tasks/lib/ordering.ts` applies:
#: hand-ranked rows first, newest next, and the id so two rows created in
#: the same instant still have one order (S8a).
_INBOX_ORDER = " ORDER BY p.sort_key ASC NULLS LAST, t.created_at DESC, t.id"


def _project_task(row: Any) -> tuple[dict[str, Any], str]:
    """One ``_MY_TASKS_SQL`` row → the wire task, plus its EFFECTIVE disposition.

    Extracted for the reason `_apply_overlay` was: there were two copies of
    this loop body, WS-39 S3a-client needed a third (the single-task read), and
    the failure mode of the duplication is silent and one-sided — a fact added
    to the list projection but not the calendar's produces a calendar where
    that fact is simply absent. No error, no 500, and the hermetic fake agrees,
    because a fake answers ``None`` for a column nobody selected.

    Three of the four facts set here are NEW to the wire in this slice, and each
    one was measured absent rather than assumed:

    * ``is_mine`` — computed by the SQL since WS-27e and then dropped on the
      floor, because ``TaskModel`` has no such field and ``row_to_dict`` copies
      only model fields. The Tasks client reads ``raw.is_mine ?? true``, so
      every task another member owns would have rendered as the caller's own.
    * ``workflow_stage`` — the team's board column (§13.4a). The status *id* was
      on the wire; the NAME, which is the only part a human reads, was not.
    * ``subtask_count`` — the roll-up badge. Counted over non-archived children
      so archiving a subtask decrements it, which is what the badge claims.

    ``is_triaged`` was already on the inbox's wire and is now on all three, for
    the one-shape reason above.
    """
    effective = getattr(row, "p_disposition", None) or derive_disposition(
        status_category=str(getattr(row, "status_category", "") or ""),
        is_mine=bool(getattr(row, "is_mine", False)),
        has_assignee=int(getattr(row, "assignee_count", 0) or 0) > 0,
    )
    task = row_to_dict(row, TaskModel)
    task["disposition"] = effective
    # Whether the member has actually triaged it — the Weekly Review reads this,
    # and it is the distinction a stored default would have destroyed.
    task["is_triaged"] = getattr(row, "p_disposition", None) is not None
    task["is_mine"] = bool(getattr(row, "is_mine", False))
    task["workflow_stage"] = getattr(row, "workflow_stage", None)
    # The MAPPED half of the status, and the only half two projects share.
    # `workflow_stage` is the project's own name for the lane — "IN PROCESS" in
    # one space, "Building" in another — so a personal list spanning projects
    # cannot group by it without inventing a group per space. The category is
    # the vocabulary both spaces key off, and this query has selected it since
    # WS-39 for `derive_disposition` (line 702) and then dropped it on the
    # floor: `row_to_dict` copies model fields only, and `TaskModel` has no
    # such field. Owner directive 2026-09-03 — Tasks sees the mapped status.
    task["status_category"] = getattr(row, "status_category", None)
    task["subtask_count"] = int(getattr(row, "subtask_count", 0) or 0)
    # S6e — the project's NAME beside its id. A member reached by assignment
    # alone may hold no grant on the project, so `/projects/nodes` cannot be
    # relied on to name it for them. The join is already here.
    task["project_name"] = getattr(row, "project_name", None)
    # Where the task came from (`pm_tasks.origin`, migration 211): an email
    # capture names its sender. `TaskModel` has no such field, so the column
    # rode in `t.*` and fell on the floor. One projection here serves the
    # inbox, the calendar and the single read alike (S8a).
    task["origin"] = from_jsonb(getattr(row, "origin", None))
    _apply_overlay(task, row)
    return task, effective


#: "I have never looked at this" — no STATED disposition of mine (no overlay
#: row, or a row with ``disposition`` NULL) — AND it came from a company
#: board. Triage is a disposition write, which Clarify always makes. A
#: context, an estimate or a planner block (``apply_blocks`` upserts the
#: scheduled block onto the same row) is not a triage: the member may never
#: have seen who put the task there. A capture in my own tree is not "from
#: Projects" either. One spelling, shared by the route and the live check
#: (`tests/live/live_ws39_s6e.py`), so the two cannot drift.
UNTRIAGED_CLAUSE = (
    "(p.task_id IS NULL OR p.disposition IS NULL) AND proj.personal_owner IS NULL"
)


@router.get("/my/inbox")
async def my_inbox(
    user: UserContext = Depends(get_current_user),
    disposition: str | None = None,
    context: str | None = None,
    include_deferred: bool = False,
    include_done: bool = False,
    include_archived: bool = False,
    untriaged: bool = False,
    page: Page = Depends(),
) -> ListResponse:
    """My work — the org's tasks and my own, as one list, with my overlay.

    ``untriaged=true`` (WS-39 S6e, §4.8 point 2) narrows to the rows I have
    NEVER looked at: no ``pm_task_personal`` row of mine exists for them. It
    is the "From Projects" group at the top of My Tasks' inbox — a task a
    colleague assigned to me on a board, which the derivation rule of D53
    would otherwise file straight into Next Actions as if I had chosen it.
    A DISPOSITION write is the triage (``UNTRIAGED_CLAUSE`` says why a
    context or a planner block is not). Composed on the one membership
    fragment. Each row also carries ``assigned_by`` — who put it there.

    **No visibility clause**, deliberately, and for the same reason
    ``/assigned-to-me`` has none: assignment is itself the strongest claim to a
    task, so scoping this by project grants would hide work from the person
    asked to do it.

    Filtering by ``disposition`` matches the *effective* one — stated where the
    member has triaged, derived otherwise — so "show me my next actions" answers
    the same way whether or not they have been through the inbox. Filtering on
    the stored column alone would show an empty Next list to somebody with
    twenty assigned tasks.
    """
    if disposition is not None and disposition not in DISPOSITIONS:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown disposition. One of: {list(DISPOSITIONS)}.",
        )

    email = actor(user).lower()
    clauses: list[str] = []
    extra: dict[str, Any] = {}
    if not include_deferred:
        # The tickler: a deferred task is not in the inbox until its date.
        clauses.append("(p.defer_until IS NULL OR p.defer_until <= now())")
    if context:
        clauses.append("lower(p.context) = :context")
        extra["context"] = context.strip().lower()
    if untriaged:
        clauses.append(UNTRIAGED_CLAUSE)

    sql = _MY_TASKS_SQL + ("".join(f" AND {c}" for c in clauses)) + _INBOX_ORDER
    items: list[dict[str, Any]] = []
    async with _tenant_session() as db:
        params = await my_tasks_binds(
            db, email, archived=include_archived, **extra,
        )
        rows = (await db.execute(text(sql), params)).fetchall()
        for row in rows:
            task, effective = _project_task(row)
            if not include_done and effective in ("DONE", "TRASH"):
                continue
            if disposition is not None and effective != disposition:
                continue
            items.append(task)
        # After the filters, not before: the board draws a face on every card,
        # so this is one extra query for the page rather than N for the rows —
        # and paying it for rows the filters just dropped is waste.
        await attach_assignees(db, items)
        if untriaged:
            await _attach_assigned_by(db, email, items)

    total = len(items)
    window = items[page.offset : page.offset + page.limit]
    return ListResponse(rows=window, total=total)


async def _attach_assigned_by(
    db: Any, email: str, items: list[dict[str, Any]],
) -> None:
    """Who assigned each row to ME — ``pm_task_assignees.assigned_by`` for my
    own assignee row. One query for the page. A row I hold by another arm
    (my personal project) has no such row and reads ``None``."""
    for row in items:
        row["assigned_by"] = None
    ids = [str(r["id"]) for r in items if r.get("id")]
    if not ids:
        return
    found = (await db.execute(
        text(
            "SELECT task_id, assigned_by FROM pm_task_assignees "
            " WHERE lower(assignee) = :who "
            "   AND task_id = ANY(CAST(:ids AS uuid[]))"
        ),
        {"who": email, "ids": ids},
    )).fetchall()
    by_task = {str(r.task_id): getattr(r, "assigned_by", None) for r in found}
    for row in items:
        row["assigned_by"] = by_task.get(str(row["id"]))


# ── The projects I lead (WS-39 S6e, §4.8 point 1) ───────────────────────────
#
# `pm_projects.lead` is one address, indexed (`idx_pm_projects_lead`). A
# project I lead with no task assigned to me is invisible through the one
# membership fragment — nothing in it is MINE — yet it is the project I am
# answerable for. This route is the Projects view of My Tasks: the project,
# how much open work it holds, and the part of that work that is on my own
# plate, first.

#: Open work on one project, direct children only. The same question the
#: Areas count answers (`list_my_areas`), spelled through the closed
#: vocabulary rather than a category equality so `cancelled` closes too.
_OPEN_ON_PROJECT_SQL = """
SELECT count(*) FROM pm_tasks t
 WHERE t.project_id = CAST(:pid AS uuid)
   AND t.archived_at IS NULL
   AND NOT EXISTS (SELECT 1 FROM pm_task_statuses s
                    WHERE s.id = t.status_id
                      AND s.category IN ('done', 'cancelled'))
"""


def _led_project_to_dict(row: Any, open_tasks: int) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "name": row.name,
        "task_prefix": getattr(row, "task_prefix", None),
        "open_tasks": int(open_tasks),
        "my_tasks": [],
    }


@router.get("/my/led")
async def my_led_projects(
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """The projects where I am the lead, with their open work and mine.

    Tenant-bound on ``organization_id``, like every read here. The
    personal tree (``personal_owner IS NOT NULL``) is excluded: a member is
    never "lead" of their own inbox, and the column is NULL there anyway.
    Archived projects are excluded — leading a closed project is history.

    ``my_tasks`` is the member's OPEN tasks assigned to them in that project,
    read through ``_MY_TASKS_SQL`` so each carries the overlay and the same
    shape as ``/my/inbox`` — it is the same row, and the client's store
    holds one shape. ``open_tasks`` counts everybody's.
    """
    email = actor(user).lower()
    async with _tenant_session() as db:
        rows = await led_projects_for(db, email)
    return {"rows": rows, "total": len(rows)}


async def led_projects_for(
    db: Any, email: str, org: Any | None = None,
) -> list[dict[str, Any]]:
    """The route's body, on a session — so the live check (R8) can ask the
    question from another tenant's side by overriding ``org``, the way
    ``live_ws39_s6a.py`` overrides ``vis_org``."""
    who = (email or "").strip().lower()
    org = org if org is not None else await resolve_organization_id(db, who)
    # A member the directory does not know has no tenant. Bound through as
    # NULL, the way `my_tasks_binds` binds it: `= CAST(NULL AS uuid)` matches
    # nothing and the answer is an empty list, not a 500 on `CAST('None')`.
    org_bind = str(org) if org is not None else None
    rows = (await db.execute(
        text(
            "SELECT * FROM pm_projects "
            " WHERE organization_id = CAST(:vis_org AS uuid) "
            "   AND lower(lead) = :who "
            "   AND personal_owner IS NULL "
            "   AND archived_at IS NULL "
            " ORDER BY lower(name)"
        ),
        {"vis_org": org_bind, "who": who},
    )).fetchall()
    if not rows:
        return []
    led: dict[str, dict[str, Any]] = {}
    for row in rows:
        count = (await db.execute(
            text(_OPEN_ON_PROJECT_SQL), {"pid": str(row.id)},
        )).scalar() or 0
        led[str(row.id)] = _led_project_to_dict(row, count)

    # My own open work on those projects, in the inbox's shape. One query
    # over the one fragment, narrowed to the led ids so Postgres does the
    # narrowing rather than the loop below.
    sql = _MY_TASKS_SQL + " AND t.project_id = ANY(CAST(:led_ids AS uuid[]))"
    mine: list[dict[str, Any]] = []
    params = {
        **await my_tasks_binds(db, who, archived=False, led_ids=list(led)),
        "vis_org": org_bind,
    }
    for task_row in (await db.execute(text(sql), params)).fetchall():
        task, effective = _project_task(task_row)
        if effective in ("DONE", "TRASH") or not task["is_mine"]:
            continue
        mine.append(task)
    await attach_assignees(db, mine)
    for task in mine:
        led[str(task["project_id"])]["my_tasks"].append(task)
    return list(led.values())


@router.get("/my/tasks/{task_id}")
async def my_task(
    task_id: str, user: UserContext = Depends(get_current_user),
) -> dict:
    """One task, in exactly the shape ``/my/inbox`` gives it.

    Spec: ``task_manager_app.md`` §13.5 · **D53** · ticket WS-39 S3a-client.

    **Why this exists.** A `GtdItem` edit is not one write any more. Changing a
    title touches ``pm_tasks``; changing a disposition touches
    ``pm_task_personal``; both at once is two requests to two routes that each
    answer with their own half. The client needs the WHOLE task back — that is
    what its store holds — and the three ways of getting it are: have the client
    stitch two partial responses together, widen one of the write routes to
    return the merge, or read the task back through the projection that already
    defines what a task looks like to this member. Only the third leaves one
    definition of the shape.

    ``GET /projects/tasks/{id}`` is NOT that route and must not be mistaken for
    it: it answers with the task as the PROJECT sees it — no overlay, so no
    disposition, no context, no block. A member reading their own task through
    it would find their triage missing and, worse, would find it MISSING rather
    than refused.

    404 when the task exists but is not mine, which is the same answer as when
    it does not exist — deliberately. ``_MY_TASKS_SQL`` decides membership, and
    it is the same clause the list uses, so a task cannot be readable singly and
    invisible in the list.
    """
    email = actor(user).lower()
    async with _tenant_session() as db:
        return await _read_my_task(db, email, task_id)


@router.get("/my/tasks/{task_id}/lanes")
async def my_task_lanes(
    task_id: str, user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """The lanes one of MY tasks can be in — id, name, category, position
    — behind the same membership check as the single read (WS-39 S6e repair).

    The shared task body draws a Status select. ``/nodes/{id}/statuses`` is
    behind the project grant, and a member who reaches a task by assignment
    alone holds none, so that read 404s and the select is dead. Here the
    membership fragment is the grant: ``_read_my_task`` answers 404 for a
    task that is not mine, exactly as ``/my/tasks/{id}`` does.

    A sibling read rather than a field on ``/my/tasks/{id}``, on purpose.
    ``test_the_inbox_and_the_calendar_project_the_same_task_shape`` holds the
    three personal readers to one key set, so the client's one mapper never
    reads ``undefined`` off the short one — and a lane list on every inbox
    row would be a query per root on every page. ``status_owner_id`` walks to
    the node that owns the set (migration 196), the board's own resolution.
    """
    email = actor(user).lower()
    async with _tenant_session() as db:
        task = await _read_my_task(db, email, task_id)
        owner = await status_owner_id(db, str(task["project_id"]))
        rows = (await db.execute(
            text(
                "SELECT id, name, category, position, is_default "
                "  FROM pm_task_statuses WHERE project_id = CAST(:root AS uuid) "
                " ORDER BY position, name"
            ),
            {"root": owner},
        )).fetchall()
    lanes = [
        {
            "id": str(r.id), "name": r.name, "category": r.category,
            "position": r.position, "is_default": bool(getattr(r, "is_default", False)),
        }
        for r in rows
    ]
    return {"rows": lanes, "total": len(lanes)}


async def _read_my_task(db: Any, email: str, task_id: str) -> dict[str, Any]:
    """One task in ``/my/inbox``'s shape, or 404 — the read every personal
    write answers with (WS-39 S6a: batch and organize read back through here,
    for the reason ``my_task``'s docstring gives)."""
    sql = _MY_TASKS_SQL + " AND t.id = CAST(:tid AS uuid)"
    # Reachable when archived: the client reads a task back after archiving
    # it, and a 404 there would look like the task was destroyed rather than
    # filed.
    row = (await db.execute(
        text(sql), await my_tasks_binds(db, email, archived=True, tid=task_id),
    )).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="No such task")
    task, _ = _project_task(row)
    await attach_assignees(db, [task])
    return task


@router.get("/my/calendar")
async def my_calendar(
    start: str,
    end: str,
    include_done: bool = False,
    user: UserContext = Depends(get_current_user),
) -> ListResponse:
    """My scheduled blocks in a window — the Calendar app's one read.

    Spec: ``calendar_focus_os.md`` §10 · **D54** · ticket WS-39 S3a.

    **Why this exists rather than filtering ``/my/inbox`` client-side.** The
    inbox answers "what is on my plate", which is unbounded; a calendar week
    asks for a handful of rows out of it. Pushing the window into SQL is what
    lets migration 187's partial index do the work — verified as an
    ``Index Scan``, not merely present (``tests/live/live_ws39_s3a.sql`` CHECK
    7). The old calendar read the whole item list into the browser and filtered
    there, which is affordable at a hundred tasks and not at ten thousand.

    **The window is half-open, ``[start, end)``**, so consecutive weeks tile
    without overlapping and a block starting exactly at midnight belongs to one
    day rather than two.

    **Scoped to the caller, and only ever to the caller.** The identity comes
    from the session; there is deliberately no ``?member=``. Somebody else's
    calendar is a different question with a different answer (whose blocks are
    legible to whom is out of scope by D54.4), and the way that question gets
    answered accidentally is a parameter like this one.

    ⚠️ Returns tasks with **my block attached**, not bare blocks: the calendar
    draws a task, and a payload of blocks would send it back for every title.

    **DONE and TRASH are excluded unless ``include_done``** — the same rule, the
    same parameter name and the same effective-disposition derivation
    ``/my/inbox`` applies, because they are two lenses on one list and a task
    the member trashed must not keep occupying an hour of their week. The
    disposition is EFFECTIVE (stated where triaged, derived otherwise), so a
    task closed on the team's board leaves the calendar without anyone having
    triaged it personally.
    """
    # ⚠️ Parsed explicitly, NOT through `coerce_write_values`. That helper keys
    # off an allow-list of COLUMN names, and these two are bind parameters — so
    # it would pass them through as strings and bind text to a timestamptz
    # comparison. The same trap as the columns themselves; different reason, so
    # naming it here rather than widening the column list with two non-columns.
    #
    # ⚠️ Both ends go through `_as_utc`, for the reason written there: a caller
    # may send one bound with an offset and the other without (a
    # `datetime-local` picker on one end of the week and an ISO instant on the
    # other), and comparing those two raises `TypeError` → 500 on the line
    # below. It also stops a naive bound being resolved by the CONNECTION's
    # TimeZone in the SQL comparison, which would silently shift the week.
    try:
        window = {
            "start": _as_utc(datetime.fromisoformat(start)),
            "end": _as_utc(datetime.fromisoformat(end)),
        }
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=422,
            detail="start and end must be ISO-8601 instants.",
        ) from exc
    if window["end"] <= window["start"]:
        raise HTTPException(status_code=422, detail="end must be after start.")

    email = actor(user).lower()
    sql = _MY_TASKS_SQL + (
        " AND p.scheduled_start >= :start AND p.scheduled_start < :end"
    )
    items: list[dict[str, Any]] = []
    async with _tenant_session() as db:
        # A week never shows archived work. There is no `include_archived`
        # here on purpose: "show me the archive" is a list question, and a
        # calendar that could answer it would draw archived tasks over live
        # ones in the same hour.
        params = await my_tasks_binds(db, email, archived=False, **window)
        rows = (await db.execute(text(sql), params)).fetchall()
        for row in rows:
            task, effective = _project_task(row)
            if not include_done and effective in ("DONE", "TRASH"):
                continue
            items.append(task)
        await attach_assignees(db, items)

    items.sort(key=lambda t: t["scheduled_start"] or "")
    # Deliberately unpaged: a window is already the bound, and a paged calendar
    # week would be a page of Tuesday.
    return ListResponse(rows=items, total=len(items))


@router.get("/my/contexts")
async def my_contexts(user: UserContext = Depends(get_current_user)) -> dict:
    """The contexts this member actually uses, with counts.

    Derived from their own rows rather than from a configured list: a context
    vocabulary somebody has to maintain is a context vocabulary that goes stale,
    and GTD contexts are personal by nature.
    """
    email = actor(user).lower()
    async with _tenant_session() as db:
        rows = await member_contexts(db, email)
        return {
            "rows": [
                {"context": r.context, "total": int(r.total)} for r in rows
            ],
            "total": len(rows),
        }


async def member_contexts(db: Any, email: str) -> list[Any]:
    """The contexts one member uses, from their overlay rows, with counts.

    Shared by `GET /projects/my/contexts` and the AI seam's pm arm
    (`routes/projects/item_lens.py`). One query, so the prompts and the
    picker agree about which contexts exist.
    """
    return (await db.execute(
        text(
            "SELECT p.context AS context, count(*) AS total "
            "FROM pm_task_personal p "
            "JOIN pm_tasks t ON t.id = p.task_id "
            "WHERE lower(p.member_email) = :who AND p.context IS NOT NULL "
            "  AND t.archived_at IS NULL "
            "GROUP BY p.context ORDER BY count(*) DESC, p.context"
        ),
        {"who": email},
    )).fetchall()


# ── Completion, from the personal side ──────────────────────────────────────

async def complete_for_member(db: Any, task: Any, email: str) -> dict[str, Any]:
    """Move a task into its done lane, and mark it DONE on MY overlay.

    The one completion path. `POST /projects/tasks/{id}/complete` calls it,
    and so does the AI seam when an email thread closes
    (`routes/projects/item_lens.py`). Two effects, one helper, because a
    caller that moved the status and forgot the overlay would leave a
    finished task in the member's Next list, and the reverse would mark a
    team task finished in one list while the board still shows it open.

    Three more effects ride on it, so no caller can forget one: the
    `pm.task.status_changed` event the workflow engine binds triggers to
    (`routes/workflows/catalog.py`), and, for a task captured from an email,
    the thread is marked Done through `propagate_task_done_to_thread`, the
    same hop `PATCH /tasks/items/{id}` makes on the retiring store. That hop
    is best-effort: the close stands if the mailbox is unreachable.

    `task` is any row carrying `id`, `project_id`, `status_id` and, when it
    has one, `origin`.
    """
    import contextlib

    from gateway.routes.projects.core import apply_status_transition

    # The owner's chosen done lane, not whichever one sorts first. This read
    # was its own SQL and never consulted `is_default`, so a root holding
    # both "Done" and "Shipped" completed into whichever carried the lower
    # position regardless of which the owner had marked. `load_default_status`
    # is the one place that question is answered, and it raises the same 422
    # when the project has no done status at all.
    done = await load_default_status(
        db, await status_owner_id(db, str(task.project_id)), "done",
    )
    moved = await apply_status_transition(
        db, task, str(done.id), created_by=email,
    )
    # And the member's own view of it follows, so a completed task does not
    # sit in their Next list contradicting the board.
    await _upsert_personal(db, str(task.id), email, {"disposition": "DONE"})
    if getattr(task, "origin", None):
        # projects -> tasks is the allowed import direction. Function-local so
        # the leaf this package imports at load gains no edge.
        from gateway.routes.tasks.email_link import propagate_task_done_to_thread

        with contextlib.suppress(Exception):
            await propagate_task_done_to_thread(db, task)
    await emit("pm.task.status_changed", {
        "task_id": str(task.id), "from": moved["from"].name,
        "to": moved["to"].name, "to_category": moved["to"].category,
    })
    return moved


@router.post("/tasks/{task_id}/complete")
async def complete_task(
    task_id: str, user: UserContext = Depends(get_current_user),
) -> dict:
    """Tick a task off from my inbox.

    This moves the task's SHARED status into its project's done lane — it is not
    a personal-only "done". That is the cohesion the one-store design buys:
    finishing something in your own list finishes it for the project, at the
    same instant, because there is one row. A personal-only completion would be
    a member quietly marking a team task finished while the board still shows it
    open, which is the exact drift a mirror produces.
    """
    email = actor(user).lower()
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        task = await load_visible_task(db, vis, task_id)
        moved = await complete_for_member(db, task, email)
        return row_to_dict(moved["row"], TaskModel)


# ── Organize: one clarify decision, atomically ──────────────────────────────

def _validate_decision(payload: OrganizeIn) -> str:
    """The decision's own rules, or a 400 — the same messages
    ``routes/tasks/items.py::organize_item`` answers, so the Clarify card's
    error handling did not have to learn a second vocabulary at the cutover.
    Returns the disposition the kind states."""
    disposition = ORGANIZE_KINDS.get(payload.kind)
    if disposition is None:
        raise HTTPException(status_code=400, detail=f"Unknown kind: {payload.kind}")
    if payload.kind in _ACTIONABLE_KINDS and not (payload.next_action or "").strip():
        raise HTTPException(status_code=400, detail="next_action is required")
    if payload.kind == "delegate" and not payload.assignee:
        raise HTTPException(status_code=400, detail="assignee is required")
    if payload.kind == "project" and not (payload.outcome or "").strip():
        raise HTTPException(status_code=400, detail="outcome is required")
    if payload.kind == "calendar" and not (payload.due_at or "").strip():
        # GTD hard landscape: a calendar decision WITHOUT a date silently
        # produced a hard-date item with no date — invisible on the Calendar
        # view. Refuse with the reason instead.
        raise HTTPException(status_code=400,
                            detail="due_at is required for a calendar decision")
    validate_overlay({"energy": payload.energy})
    return disposition


def _is_delegated(payload: OrganizeIn) -> bool:
    """Sort→Shape: OWNER is an axis independent of SIZE and WHEN. The legacy
    ``kind="delegate"`` always delegates; any other actionable kind ALSO
    delegates when it carries an assignee — so a task can be a project,
    delegated, with a deadline, all at once."""
    return payload.kind == "delegate" or (
        payload.kind in ("next", "project", "calendar")
        and payload.assignee is not None
    )


async def _add_subtasks(
    db: Any, email: str, parent: Any, titles: list[str],
) -> list[str]:
    """Child ``pm_tasks`` under ``parent``: same project, self-assigned, in the
    order given. Blank titles are skipped. Returns the new ids."""
    project_id = str(parent.project_id)
    root = str(parent.root_project_id)
    status = await load_default_status(db, await status_owner_id(db, project_id))
    created: list[str] = []
    for raw in titles:
        title = (raw or "").strip()
        if not title:
            continue
        # The ONE capture path, with a parent: row, assignee, activity and
        # the `pm.task.created` event come from `create_personal_task`.
        child = await create_personal_task(
            db, email, project_id, root, str(status.id), {
                "title": title,
                "parent_task_id": str(parent.id),
                "source": "manual",
                # R5: the tenant travels with the row, read off the parent it
                # hangs from rather than inferred later.
                "organization_id": getattr(parent, "organization_id", None),
            })
        created.append(str(child.id))
    if created:
        # Subtask membership is a satellite of the PARENT (WS-27ae / P-27):
        # its `{done, total}` changed and no statement above touched its row.
        await touch_task(db, str(parent.id))
    return created


async def _organize(
    db: Any, vis: Any, email: str, task: Any, payload: OrganizeIn,
) -> str:
    """Apply one decision to ``task`` inside the caller's transaction.

    Returns the EFFECTIVE disposition written. The order below is the order
    the guards must see things in: the move first, so the assign guard judges
    the DESTINATION; then the shared deadline; then my overlay; then the
    children, which inherit the project the move chose.
    """
    task_id = str(task.id)
    disposition = _validate_decision(payload)
    delegated = _is_delegated(payload)
    if delegated:
        disposition = "WAITING"

    # ── 1. Where it lives, and who owns it — through the ONE move seam ─────
    #
    # `kind="project"` mints a child of my personal root named for the
    # outcome and files the task there. A task that lives on a TEAM board is
    # refused by `assert_move_keeps_privacy` inside `move_task_in` (D62: a
    # move into a private tree takes the task off the board with no record),
    # and the refusal rolls the freshly minted child back with it.
    project_id = payload.project_id
    if payload.kind == "project":
        if delegated:
            # A private child is where a project outcome lives, and the
            # assign guard refuses a colleague there. Say so before minting
            # one, in the words the Clarify card shows.
            raise HTTPException(
                status_code=400,
                detail="A delegated task cannot become a private project. "
                       "Choose Next and pick a project to delegate into.",
            )
        child = await ensure_personal_child(db, email, payload.outcome or "")
        project_id = str(child.id)
    who = None
    if delegated and payload.assignee is not None:
        who = (payload.assignee.email or payload.assignee.name).strip().lower()
    move = MoveTask(
        project_id=project_id if project_id and project_id != str(task.project_id) else None,
        assignees=[who] if who else None,
    )
    if move.project_id or move.assignees is not None:
        await move_task_in(db, vis, task, move, by=email)
        task = await load_visible_task(db, vis, task_id)

    # ── 2. The shared deadline — one fact, on the task ──────────────────────
    if (payload.due_at or "").strip():
        await update_row(db, "pm_tasks", task_id, {"due_at": payload.due_at})

    # ── 3. My overlay ───────────────────────────────────────────────────────
    if payload.kind == "do-now":
        # Completed for the PROJECT, then the two-minute marker on my view.
        # Writing DONE onto the overlay alone would leave the board open
        # (§13.5a decision 1). `complete_for_member` is the one completion
        # path: it emits the status event and closes an email thread too.
        await complete_for_member(db, task, email)
    values: dict[str, Any] = {
        "disposition": disposition,
        "next_action": (payload.next_action or "").strip() or None,
        "context": payload.context,
        "energy": payload.energy,
        "time_estimate_mins": payload.time_estimate_mins,
        "is_two_minute": payload.kind == "do-now",
        # Written on EVERY decision, as `items.py` did: a re-clarify from
        # "calendar" to "next" must drop the hard date, or the task stays
        # pinned to a day nobody chose.
        "is_hard_date": payload.kind == "calendar",
        "clarified_at": now(),
    }
    if delegated and payload.assignee is not None:
        # No `expected_by`: `due_at` is the task's own deadline, written above.
        # NULL means nobody promised, and the overdue line reads `due_at` live
        # (settled 2026-08-02, task_manager_app.md §13.4).
        values["waiting_on"] = {
            "name": payload.assignee.name, "email": payload.assignee.email,
        }
        values["delegated_at"] = now()
        # The delegator's day keeps no block for work they handed away. The
        # planner's `carry_forward` also refuses WAITING rows, so a block that
        # somehow survived cannot roll into tomorrow either.
        values["scheduled_start"] = None
        values["scheduled_end"] = None
    await _upsert_personal(db, task_id, email, values)

    # ── 4. The steps — children of the task where it now lives ─────────────
    if payload.subtasks:
        await _add_subtasks(db, email, task, payload.subtasks)
    return disposition


@router.post("/my/tasks/{task_id}/organize")
async def organize_my_task(
    task_id: str, payload: OrganizeIn,
    user: UserContext = Depends(get_current_user),
) -> dict:
    """Apply one clarify decision — ONE transaction (WS-39 S6a).

    The Tasks app's Clarify card ends in one of eight decisions, and each is
    several writes: my overlay, the task's deadline, its assignees, a move,
    children. Sent as separate requests they can fail between each other and
    leave a task half-clarified — WAITING with nobody assigned, or assigned
    with nobody waiting. One request, one transaction, or nothing.

    ``kind == "project"`` mints a child of the personal root
    (:func:`ensure_personal_child`) named for the outcome. Answers in
    ``/my/inbox``'s shape.
    """
    email = actor(user).lower()
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        task = await load_visible_task(db, vis, task_id)
        title = str(getattr(task, "title", "") or "")
        disposition = await _organize(db, vis, email, task, payload)
        result = await _read_my_task(db, email, task_id)

    # Teach the clarification memory from the COMMITTED decision — the block
    # above has committed. Fire-and-forget and best-effort, so it never slows
    # or breaks organize (the same call `items.py` makes).
    from gateway.routes.tasks.task_memory import remember_decision_background
    remember_decision_background(
        title=title,
        disposition=disposition,
        next_action=(payload.next_action or "").strip() or None,
        owner=(payload.assignee.name if payload.assignee else None),
        project=(
            payload.outcome.strip()
            if payload.kind == "project" and payload.outcome else None
        ),
        context=payload.context,
    )
    await emit("pm.task.updated", {"task_id": task_id})
    return result


class DeferIn(BaseModel):
    until: str


@router.post("/tasks/{task_id}/defer")
async def defer_task(
    task_id: str, payload: DeferIn,
    user: UserContext = Depends(get_current_user),
) -> dict:
    """Hide a task from my inbox until a date. Mine only — the team's board is
    unaffected, because deferring is a statement about my attention, not about
    the work."""
    email = actor(user).lower()
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        await load_visible_task(db, vis, task_id)
        row = await _upsert_personal(db, task_id, email, {
            "defer_until": payload.until, "disposition": "SOMEDAY",
        })
        return _personal_to_dict(row)


@router.post("/tasks/{task_id}/nudge")
async def nudge_task(
    task_id: str, user: UserContext = Depends(get_current_user),
) -> dict:
    """Tell the person I am waiting on that I am waiting on them.

    WS-27bk wave 6, spec §9.12.9, H-113. Migration 188 shipped the four
    Waiting-For columns and the Tasks app has drawn them since — including
    "nudged 3d ago". Nothing ever wrote `last_nudged_at`, because the act that
    should write it did not exist. This is that act.

    ⚠️ **IN-APP, and only in-app.** It writes one `pm_notifications` row through
    the shared `notify()`, the same path a mention and an assignment take. An
    OUTWARD nudge — mail, WhatsApp — is Action-Broker work and owner-gated
    (CLAUDE.md §3a rule 3). Two comments in the tree said "the nudge" was
    owner-gated without saying which one they meant, and this slice corrects
    both rather than leaving the reader to guess.

    ⚠️ **Explicit, never automatic.** §9.12.9: "A follow-up that always pings
    somebody is a tool people stop using." Nothing here runs on a timer. A
    member presses it, or nobody is told anything.

    ⚠️ **The stamp follows the NOTIFICATION, not the request.** If the person
    cannot open the task, `notify` delivers nothing — and stamping
    `last_nudged_at` anyway would draw "nudged just now" beside a chase that
    reached nobody. So the write is conditional and the refusals come back in
    the body for the surface to show.
    """
    email = actor(user).lower()
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        task = await load_visible_task(db, vis, task_id)

        stored = (await db.execute(
            text(
                "SELECT waiting_on FROM pm_task_personal "
                "WHERE task_id = CAST(:tid AS uuid) AND member_email = :who"
            ),
            {"tid": task_id, "who": email},
        )).fetchone()
        waiting_on = from_jsonb(getattr(stored, "waiting_on", None))
        if not waiting_on:
            # 409, not 422: the payload is fine, the STATE is not. There is
            # nobody to chase because this task is not delegated.
            raise HTTPException(
                status_code=409,
                detail="You are not waiting on anybody for this task.",
            )

        who = str(waiting_on.get("email") or "").strip().lower()
        if not who:
            raise HTTPException(
                status_code=409,
                detail="The person you are waiting on has no email to notify.",
            )
        # ⚠️ Said by name rather than left to `notifiable`, which drops an agent
        # silently and would report "nobody was told" with no reason.
        # `pm_notifications_recipient_is_human` refuses the row anyway.
        if who.startswith("agent:"):
            raise HTTPException(
                status_code=409,
                detail="An agent has no inbox to nudge. Agents are handed work "
                       "by dispatch, not by a notification.",
            )

        sent = await notify(
            db, recipients=[who], kind="nudge", task_id=task_id,
            actor_id=email, excerpt=(task.title or "")[:EXCERPT_CHARS],
        )

        stamped: Any = None
        if sent["notified"]:
            stamped = await _upsert_personal(
                db, task_id, email, {"last_nudged_at": now()},
            )

        return {
            **sent,
            # None until somebody was actually told, so the surface can draw
            # "nudged …" from the same fact the stamp records.
            "last_nudged_at": (
                _iso(stamped, "last_nudged_at") if stamped is not None else None
            ),
        }


# -- Areas: a member's own categories (WS-39 S6b) ----------------------------
#
# An **Area** is a child of the personal root carrying `personal_owner`. That
# is the whole shape, and migration 191 is what makes it work: before it,
# `personal_owner` was unique across every row, so it could only ever mark a
# root. 191 re-keyed the index onto the root, and the column now means
# *private to this person at any depth*.
#
# **Why this slice exists**, and it is not symmetry with the Projects app.
# H-29 says it in as many words: the `gtd_*` backfill CREATES Areas from a
# member's old `gtd_projects`, and the owner may not arm it until the app can
# rename or delete one. Otherwise a member wakes to categories they did not
# make and cannot remove. These four routes are the precondition on the
# cutover, not a convenience beside it.
#
# WARNING: authorization here is the LOOKUP, not a check after it. Every route
# resolves the caller from the session and finds the Area by
# `(personal_owner = me, and it has a parent)`. Somebody else's Area is not
# refused, it is NOT FOUND, because the query never had a way to reach it.
# There is deliberately no `?member=`, matching this module's header rule.

#: Long enough for "Home: renovation and council paperwork", short enough that
#: a sidebar row cannot become a text file.
AREA_NAME_MAX = 120


class AreaIn(BaseModel):
    name: str


def _clean_area_name(raw: str) -> str:
    name = (raw or "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="An area needs a name.")
    if len(name) > AREA_NAME_MAX:
        raise HTTPException(
            status_code=422,
            detail=f"An area name is at most {AREA_NAME_MAX} characters.",
        )
    return name


def _area_to_dict(row: Any, open_tasks: int | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": str(row.id),
        "name": row.name,
        "archived": getattr(row, "archived_at", None) is not None,
    }
    if open_tasks is not None:
        out["open_tasks"] = int(open_tasks)
    return out


async def _load_my_area(db: Any, email: str, area_id: str) -> Any:
    """One Area of mine, or 404.

    The two predicates together ARE the authorization. `personal_owner` says
    the row is private to me, and `parent_project_id IS NOT NULL` says it is a
    category rather than my root — so this can never return the root itself
    and let a rename or a delete reach it.
    """
    row = (await db.execute(
        text(
            "SELECT * FROM pm_projects "
            " WHERE id = CAST(:aid AS uuid) "
            "   AND lower(personal_owner) = :who "
            "   AND parent_project_id IS NOT NULL"
        ),
        {"aid": area_id, "who": email},
    )).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="No such area")
    return row


@router.get("/my/areas")
async def list_my_areas(
    include_archived: bool = False,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """My categories, with how much live work each holds.

    The count is `open_tasks` rather than every task, because the question a
    sidebar answers is "is there anything here for me". A category whose work
    is all done should read as empty.
    """
    email = actor(user).lower()
    async with _tenant_session() as db:
        root = await _load_personal_project(db, email)
        if root is None:
            return {"rows": [], "total": 0}
        clause = "" if include_archived else " AND p.archived_at IS NULL"
        rows = (await db.execute(
            text(
                "SELECT p.*, ( "
                "   SELECT count(*) FROM pm_tasks t "
                "     JOIN pm_task_statuses s ON s.id = t.status_id "
                "    WHERE t.project_id = p.id "
                "      AND t.archived_at IS NULL "
                "      AND s.category <> 'done' "
                " ) AS open_tasks "
                "  FROM pm_projects p "
                " WHERE p.parent_project_id = CAST(:root AS uuid) "
                "   AND lower(p.personal_owner) = :who "
                + clause +
                " ORDER BY lower(p.name)"
            ),
            {"root": str(root.id), "who": email},
        )).fetchall()
    return {
        "rows": [_area_to_dict(r, getattr(r, "open_tasks", 0)) for r in rows],
        "total": len(rows),
    }


@router.post("/my/areas", status_code=201)
async def create_my_area(
    payload: AreaIn, user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Mint a category under my personal root.

    WARNING: `owns_statuses` is FALSE, and that is load-bearing. An Area
    inherits its root's four lanes, so a task moved between two of my Areas
    keeps its status. A category that owned its own set would strand every
    task the moment it moved — the defect `move_node` had to grow
    `remap_task_statuses` to repair.
    """
    email = actor(user).lower()
    name = _clean_area_name(payload.name)
    async with _tenant_session() as db:
        # Creating an Area can be the first use of the personal tree, so the
        # root may not exist yet. Reusing `ensure_personal_project` keeps ONE
        # place that decides what a personal root looks like, and it seeds the
        # lanes this Area is about to inherit.
        root = await ensure_personal_project(db, email)
        # ONE lookup and ONE mint, shared with `organize` kind=project
        # (`ensure_personal_child`). Here a name that already exists is a
        # refusal; there it is a reuse. The helpers carry the rule that is
        # the same in both: the owner, the inherited lanes, the grant row.
        if await find_personal_child(db, email, root, name) is not None:
            raise HTTPException(
                status_code=409,
                detail=f"You already have an area called {name}.",
            )
        area = await mint_personal_child(db, email, root, name)
        result = _area_to_dict(area, 0)
    await emit("pm.project.created", {"project_id": result["id"], "name": name})
    return result


@router.patch("/my/areas/{area_id}")
async def rename_my_area(
    area_id: str, payload: AreaIn,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Rename one. It is the only field an Area has."""
    email = actor(user).lower()
    name = _clean_area_name(payload.name)
    async with _tenant_session() as db:
        area = await _load_my_area(db, email, area_id)
        await db.execute(
            text(
                "UPDATE pm_projects SET name = :name, updated_at = now() "
                " WHERE id = CAST(:aid AS uuid)"
            ),
            {"name": name, "aid": str(area.id)},
        )
        after = await _load_my_area(db, email, area_id)
        result = _area_to_dict(after)
    await emit("pm.project.updated", {"project_id": area_id})
    return result


@router.delete("/my/areas/{area_id}")
async def remove_my_area(
    area_id: str, user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Remove a category, without removing what is in it.

    Two outcomes, and the row count decides which:

    - **Empty** — the row goes. A category somebody made by accident should
      not leave a tombstone in their own sidebar.
    - **Holds tasks** — ARCHIVED, never deleted. `pm_tasks.project_id` is what
      a task lives on, so deleting the project would take the work with it, or
      refuse on the foreign key. The member asked to tidy a list, not to lose
      a month of captures.

    The response says which happened, because "deleted" and "archived" are
    different promises and the UI has to tell the truth about them.
    """
    email = actor(user).lower()
    async with _tenant_session() as db:
        area = await _load_my_area(db, email, area_id)
        # Counted BEFORE the write, for `archive_node`'s reason: afterwards the
        # honest number is unobtainable.
        held = int((await db.execute(
            text(
                "SELECT count(*) FROM pm_tasks "
                " WHERE project_id = CAST(:aid AS uuid)"
            ),
            {"aid": str(area.id)},
        )).scalar() or 0)

        if held == 0:
            await db.execute(
                text("DELETE FROM pm_projects WHERE id = CAST(:aid AS uuid)"),
                {"aid": str(area.id)},
            )
            outcome = "deleted"
        else:
            await db.execute(
                text(
                    "UPDATE pm_projects "
                    "   SET archived_at = now(), "
                    "       archived_root_id = CAST(:aid AS uuid) "
                    " WHERE id = CAST(:aid AS uuid) AND archived_at IS NULL"
                ),
                {"aid": str(area.id)},
            )
            outcome = "archived"
        # Attached to the ROOT, not to the area, and for both outcomes. A
        # hard delete leaves nothing to hang it on — `record_activity` refuses
        # an entry that names neither a task nor a project, which is right —
        # and an archived area's own timeline is filed away with it. The
        # member's root is the one timeline that survives either way, so
        # "Area 'Scratch' deleted" stays findable.
        await record_activity(
            db, activity_type="system", created_by=email,
            project_id=str(area.parent_project_id),
            body=f"Area '{area.name}' {outcome}",
        )
    await emit(f"pm.project.{outcome}", {"project_id": area_id})
    return {"id": area_id, "outcome": outcome, "tasks": held}
