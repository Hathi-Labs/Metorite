"""Projects · the personal lens — one task store, seen as my own work.

Spec: ``project-docs/specs/project_management_app.md`` §3.11-§3.12, §6.1 ·
**D-PM-6 (revised 2026-08-06)** · ticket WS-27e.

    GET   /projects/my/inbox                     → my work, with my overlay
    GET   /projects/my/tasks/{task_id}           → one of them, same shape
    GET   /projects/my/project                   → my personal project
    POST  /projects/my/project                   → …creating it if absent
    POST  /projects/my/tasks                     → quick capture into it
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
)
from gateway.routes.projects.filters import attach_assignees
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
    #: Set by the nudge SENDER, which is owner-gated and unbuilt. Accepted here
    #: so the field is not forgotten when it ships; nothing writes it today.
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

    # The grant is what the visibility model reads; `personal_owner` is only the
    # fast path to finding it. Both, so neither is load-bearing alone.
    await db.execute(
        text(
            "INSERT INTO pm_project_grants (project_id, subject, created_by) "
            "VALUES (CAST(:pid AS uuid), :who, :who) "
            "ON CONFLICT (project_id, subject) DO NOTHING"
        ),
        {"pid": project_id, "who": email},
    )
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
    title = (payload.title or "").strip()
    if not title:
        raise HTTPException(status_code=422, detail="A task needs a title.")

    email = actor(user).lower()
    async with _tenant_session() as db:
        project = await ensure_personal_project(db, email)
        project_id = str(project.id)
        status = await load_default_status(db, project_id)
        task = await insert_row(db, "pm_tasks", {
            "project_id": project_id,
            "root_project_id": project_id,
            "task_number": await next_task_number(db, project_id),
            "status_id": str(status.id),
            "title": title,
            "description": payload.notes,
            "due_at": payload.due_at,
            "created_by": email,
            "source": "manual",
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
        if payload.next_action or payload.context:
            await _upsert_personal(db, task_id, email, {
                "next_action": payload.next_action,
                "context": payload.context,
            })
        await record_activity(
            db, activity_type="system", created_by=email, task_id=task_id,
            body="Captured",
        )
        result = row_to_dict(task, TaskModel)

    await emit("pm.task.created", {"task_id": task_id, "project_id": project_id,
                                   "title": title})
    return result


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
    values = clean_payload(payload)
    if values.get("disposition") is not None and values["disposition"] not in DISPOSITIONS:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown disposition. One of: {list(DISPOSITIONS)}.",
        )
    if values.get("energy") is not None and values["energy"] not in ENERGIES:
        raise HTTPException(
            status_code=422, detail=f"Unknown energy. One of: {list(ENERGIES)}.",
        )

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
#:
#: Binds: ``:who`` (lower-cased email), ``:vis_org``, ``:archived``.
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
  )
"""


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
       (SELECT count(*) FROM pm_tasks c
         WHERE c.parent_task_id = t.id AND c.archived_at IS NULL)
                            AS subtask_count,
       (SELECT count(*) FROM pm_task_assignees a2 WHERE a2.task_id = t.id)
                            AS assignee_count,
       EXISTS (SELECT 1 FROM pm_task_assignees a3
               WHERE a3.task_id = t.id AND lower(a3.assignee) = :who)
                            AS is_mine
""" + MY_TASKS_FROM


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
    _apply_overlay(task, row)
    return task, effective


@router.get("/my/inbox")
async def my_inbox(
    user: UserContext = Depends(get_current_user),
    disposition: str | None = None,
    context: str | None = None,
    include_deferred: bool = False,
    include_done: bool = False,
    include_archived: bool = False,
    page: Page = Depends(),
) -> ListResponse:
    """My work — the org's tasks and my own, as one list, with my overlay.

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
    params: dict[str, Any] = {"who": email, "archived": include_archived}
    if not include_deferred:
        # The tickler: a deferred task is not in the inbox until its date.
        clauses.append("(p.defer_until IS NULL OR p.defer_until <= now())")
    if context:
        clauses.append("lower(p.context) = :context")
        params["context"] = context.strip().lower()

    sql = _MY_TASKS_SQL + ("".join(f" AND {c}" for c in clauses))
    items: list[dict[str, Any]] = []
    async with _tenant_session() as db:
        params["vis_org"] = await resolve_organization_id(db, email)
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

    total = len(items)
    window = items[page.offset : page.offset + page.limit]
    return ListResponse(rows=window, total=total)


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
    sql = _MY_TASKS_SQL + " AND t.id = CAST(:tid AS uuid)"
    async with _tenant_session() as db:
        row = (await db.execute(text(sql), {
            "who": email,
            "vis_org": await resolve_organization_id(db, email),
            # Reachable when archived: the client reads a task back after
            # archiving it, and a 404 there would look like the task was
            # destroyed rather than filed.
            "archived": True,
            "tid": task_id,
        })).fetchone()
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
        params: dict[str, Any] = {
            "who": email,
            "vis_org": await resolve_organization_id(db, email),
            # A week never shows archived work. There is no `include_archived`
            # here on purpose: "show me the archive" is a list question, and a
            # calendar that could answer it would draw archived tasks over live
            # ones in the same hour.
            "archived": False,
            **window,
        }
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
        rows = (await db.execute(
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
        return {
            "rows": [
                {"context": r.context, "total": int(r.total)} for r in rows
            ],
            "total": len(rows),
        }


# ── Completion, from the personal side ──────────────────────────────────────

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
    from gateway.routes.projects.core import apply_status_transition

    email = actor(user).lower()
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        task = await load_visible_task(db, vis, task_id)
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
        await _upsert_personal(db, task_id, email, {"disposition": "DONE"})
        result = row_to_dict(moved["row"], TaskModel)

    await emit("pm.task.status_changed", {
        "task_id": task_id, "from": moved["from"].name, "to": moved["to"].name,
        "to_category": moved["to"].category,
    })
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
