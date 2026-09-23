"""Task-manager routes — shared kernel.

The shared ``router``, Pydantic models, DB infrastructure and row→model
mappers used by the ai, capture, calendar and people layers. Mirrors the email
package's ``core.py`` (the leaf module: it imports nothing from siblings).

Canonical store: ``pm_tasks`` + ``pm_task_personal``, the one task store
(D53). The routes reach it through ``item_source()`` and ``agent_source()``.
S8 PR 1 (``my_tasks_cutover.md`` §5) deleted the retired store's routes.
"""

from __future__ import annotations

import json
from typing import Any

from acb_auth import require_feature_router, require_permission
from acb_common import get_logger
from fastapi import APIRouter, HTTPException

# The shared gateway engine (BO-10 / D-CRM-4) — see the DB section below for
# why these keep their private names.
from gateway.db import get_db as _get_db  # noqa: F401
from gateway.db import get_session_factory as _get_session_factory  # noqa: F401

# The tenant-bound seam (MT-1c / H2). `_tenant_session` IS
# `acb_common.db.tenant_session`, aliased per-package for the same reason
# `_get_db` was: every request handler in this package imports it from here BY
# NAME, which is the seam the hermetic tests patch per module (mirrors
# `routes/projects/core.py`). The tenant comes from the request context — bound
# once in `_with_resolved_access` — so no call site passes one (H2). A call
# outside a bound request raises `TenantUnbound` rather than defaulting: fail
# closed, never "the usual org". `_get_db` above remains ONLY for the package's
# background consumers (broker_handlers, scheduler, calendar's rollover sweep),
# which are H4's to convert — a job must not inherit an ambient tenant.
from gateway.db import tenant_session as _tenant_session  # noqa: F401
from pydantic import BaseModel

_log = get_logger("gateway.tasks")

router = APIRouter(
    prefix="/tasks", tags=["tasks"],
    dependencies=[require_feature_router("tasks")],
)


# ── People directory (HR) access ─────────────────────────────────────────────
# Spec: project-docs/specs/colleague_onboarding.md §4 N4 (owner-answered
# 2026-08-04, "directory open, HR fields restricted").
#
# `people` is an ORG roster, not per-user rows, so the owner-predicate
# shape used by items/accounts is the wrong tool. The recorded answer is two
# rules, both expressed in the EXISTING admin vocabulary
# (`acb_auth.permissions.CAPABILITIES`) rather than a new slug — a new slug is
# nobody's grant until an admin creates it, which would switch HR features off
# for the owner too:
#
#   * READ  — everyone holding `feature:tasks` sees the directory, but the
#     HR-sensitive columns are projected away unless the caller holds
#     `admin:members:read`. That is the same floor the whole `/admin` package
#     uses (`routes/admin/_common.py:77-91`) and the same predicate `/auth/me`
#     reports as `is_admin` (`routes/admin/me.py:96`), so "can see the member
#     directory" and "can see the HR half of the people directory" are one
#     answer, not two that can drift.
#   * WRITE — `admin:members:manage`, the permission that already governs
#     member records (`routes/admin/members.py`). These rows ARE member/HR
#     records; the app was made the source of truth for them by an owner
#     decision (`routes/tasks/people.py:150-151`).
#
# An owner holds `*`, which matches both by `permission_matches`.
PEOPLE_HR_READ_PERMISSION = "admin:members:read"
PEOPLE_WRITE_PERMISSION = "admin:members:manage"


def can_read_hr_fields(user: Any) -> bool:
    """May this caller see the HR-sensitive half of a person record?

    Fails closed for anything that is not a resolved ``UserContext`` — the
    projection is the boundary, so "I could not tell" must mean "no".
    """
    check = getattr(user, "has_permission", None)
    return bool(check and check(PEOPLE_HR_READ_PERMISSION))


def can_manage_people(user: Any) -> bool:
    """May this caller WRITE a person record?

    The same permission ``require_people_write()`` enforces, asked as a
    question instead of as a gate — because a *read* route has to be able to
    answer it. The People Center renders its edit controls **absent** rather
    than disabled (spec §3.2), and it cannot do that by discovering a 403 after
    the click; it has to know before it draws. Both read the one constant, so
    the answer the UI shows and the answer the write route enforces cannot
    drift.

    Fails closed for anything that is not a resolved ``UserContext``, the same
    way ``can_read_hr_fields`` does — a UI that cannot tell must show no
    control, never an optimistic one.
    """
    check = getattr(user, "has_permission", None)
    return bool(check and check(PEOPLE_WRITE_PERMISSION))


def require_people_write() -> Any:
    """The write gate for the people directory — ONE definition, bound by
    every write route (``POST``/``PATCH``/``resume`` in ``people.py`` and
    ``POST /people/embed`` in ``capability.py``).

    Bound as a route ``dependencies=[…]`` entry, not as a parameter, so it is
    enforced for the *route* rather than for a call site somebody remembers.
    """
    return require_permission(PEOPLE_WRITE_PERMISSION)


#: The statuses a `people` row may carry — mirrored from migration 148's
#: status CHECK on `people`.
#:
#: Defined HERE, next to the write gate, and re-exported by
#: ``routes/people/core.py`` as ``STATUSES``, for the reason that file already
#: gives about ``can_read_hr_fields``: a second copy is a second answer waiting
#: to drift. It nearly did — migration 148 replaced 49's `'active' | 'inactive'
#: | …` vocabulary, and the write routes below never learned, so a status the
#: editor offered would have been refused by the database at 3am rather than by
#: the route at request time.
PEOPLE_STATUSES: tuple[str, ...] = ("active", "contractor", "alumni", "invited")

#: The two vocabularies WS-28g's migration (171) deliberately shipped WITHOUT a
#: database CHECK — see `people_center_app.md` D-PC-8 / P-6. R6 keeps a
#: constraint over live data out of the expand half, so the enforcement is
#: here, and a vocabulary enforced nowhere is a vocabulary that is already
#: wrong.
#:
#: Defined in this module for the same reason `PEOPLE_STATUSES` is, and it is
#: the *import direction* that forces it: `routes/people/*` imports from
#: `routes/tasks/*` and never the other way. A validator living in the People
#: package and imported from here would close an import cycle through
#: `routes/people/__init__`, which imports `directory`, which imports this
#: package's `people` module. `routes/people/fields.py` re-exports these three
#: names, so the People Center still reads them from its own authority.
EMPLOYMENT_TYPES: tuple[str, ...] = (
    "employee", "contractor", "intern", "vendor", "agent",
)

#: Coarse on purpose: it feeds "should this person own it or review it", never
#: a pay band (`people_center_app.md` §3.6).
SENIORITY_LEVELS: tuple[str, ...] = (
    "junior", "mid", "senior", "lead", "principal",
)


def validate_person_vocabularies(fields: dict[str, Any]) -> None:
    """Refuse a value the product's vocabulary does not contain, naming it.

    The same shape ``_validate_status`` takes for 148's CHECK: a 400 listing the
    legal words instead of a 500 naming a constraint — except that here there is
    no constraint to name yet, so without this the bad value would simply be
    stored and read back as truth.
    """
    for key, vocabulary in (
        ("employment_type", EMPLOYMENT_TYPES),
        ("seniority", SENIORITY_LEVELS),
    ):
        if key in fields and fields[key] not in (None, "") \
                and fields[key] not in vocabulary:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown {key} '{fields[key]}'. One of: {list(vocabulary)}.",
            )
    if ("birthday" in fields and fields["birthday"] not in (None, "")
            and not is_month_day(str(fields["birthday"]))):
        raise HTTPException(
            status_code=400,
            detail=(
                "birthday must be MM-DD (no year — People Center D-PC-9). "
                f"Got '{fields['birthday']}'."
            ),
        )


def is_month_day(value: str) -> bool:
    """``MM-DD``, and a real one — ``02-30`` is not a birthday.

    Validated here rather than by a ``DATE`` column because the column
    deliberately is not a date (D-PC-9): storing the year is exactly what is
    being refused, and a TEXT column with no validator collects whatever people
    type. 29 February IS a birthday, so the day table is the permissive one.
    """
    parts = value.split("-")
    if len(parts) != 2 or not all(p.isdigit() and len(p) == 2 for p in parts):
        return False
    month, day = int(parts[0]), int(parts[1])
    if not 1 <= month <= 12:
        return False
    days_in_month = (31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)
    return 1 <= day <= days_in_month[month - 1]


# ── Models (snake_case — the frontend maps to camelCase) ─────────────────────

class PersonModel(BaseModel):
    name: str
    email: str | None = None
    provider_user_id: str | None = None


class GtdItemModel(BaseModel):
    id: str
    source: str = "LOCAL"
    provider: str | None = None          # account provider ('clickup' | …); None/'local' for LOCAL
    account_id: str | None = None
    provider_task_id: str | None = None
    provider_url: str | None = None
    title: str
    notes: str | None = None
    disposition: str = "INBOX"
    next_action: str | None = None
    context: str | None = None
    energy: str | None = None
    time_estimate_mins: int | None = None
    is_two_minute: bool = False
    # Prioritization matrix inputs (see infra/postgres/68). urgent is NOT stored
    # — it's derived from due_at at read time; the 8-cell label is computed from
    # important x urgent x leveraged, never stored.
    important: bool = False
    leveraged: bool = False
    # Needs an unbroken FLOW state (creative/build/design/write/strategy work) —
    # the planner protects a long peak-energy block for it (see infra/96).
    deep_work: bool = False
    # The user dismissed the delegate/schedule suggestion ("this one's mine").
    kept_mine: bool = False
    project_id: str | None = None
    defer_until: str | None = None
    sync_state: str = "local"
    provider_status: str | None = None
    # `assignee` is the PRIMARY/display owner; `assignees` is the full set. They
    # stay in step (assignee = assignees[0]) so single-owner readers are unchanged.
    assignee: PersonModel | None = None
    assignees: list[PersonModel] = []
    is_mine: bool = True
    workflow_stage: str | None = None   # local Kanban stage (see user_settings)
    sort_key: float | None = None       # manual (drag) rank within a group/column
    parent_item_id: str | None = None   # set → this item is a subtask of another
    subtask_count: int = 0              # number of child subtasks (roll-up badge)
    archived_at: str | None = None      # set → archived (hidden from active views)
    # Waiting-For record — the OPEN one for this item.
    # `expected_by` is the deterministic overdue line (spec §6: "flags rows past
    # expected_by"); `last_nudged_at` is when a follow-up last went out (written
    # by the nudge path, which is not built yet — it reads NULL today).
    waiting_on: PersonModel | None = None
    delegated_at: str | None = None
    expected_by: str | None = None
    last_nudged_at: str | None = None
    due_at: str | None = None
    is_hard_date: bool = False
    # Timeboxing (calendar_timeboxing.md §3): the block when the task is actually
    # scheduled to be done. Distinct from due_at (deadline). null = unscheduled.
    scheduled_start: str | None = None
    scheduled_end: str | None = None
    # true (default) = the auto-mover (roll-over / replan) may move this block;
    # false = FIXED (a meeting) that stays put. See calendar_ux_review.md §5.5.
    flexible: bool = True
    # When the block was ACTUALLY worked (focus timer + completion), vs the
    # scheduled_* plan. Powers planned-vs-actual + learned estimates (§4).
    actual_start: str | None = None
    actual_end: str | None = None
    completed_at: str | None = None
    clarified_at: str | None = None
    origin: dict | None = None           # source linkage (e.g. captured from an email)
    attachments: list[dict] = []         # context refs: file/image/link descriptors
    created_at: str
    updated_at: str


# ── DB (the shared gateway engine — BO-10 / D-CRM-4) ─────────────────────────
#
# This package used to own a module-level engine of its own; the block that
# built it now lives in ``gateway/db.py`` **verbatim**, and this app is the
# proof that consuming the shared seam changes nothing (same URL coercion, same
# pool sizing, same connect timeout). Spec: crm_app.md §4 "Engine seam".
#
# The private aliases are kept on purpose rather than renamed at ~50 call sites:
# every feature module in this package imports ``_get_db`` from here by name,
# and every route test monkeypatches it on the SUT submodule. Renaming it would
# be a rename of the test seam, not of the engine.
#
# ``_get_db`` / ``_get_session_factory`` are imported at the top of this module.


# ── Helpers ──────────────────────────────────────────────────────────────────

def _key_store():
    from acb_llm.key_store import get_key_store
    return get_key_store()


def _uid(user: Any) -> str:
    return getattr(user, "email", None) or "anonymous"


def _parse_jsonb(val: Any) -> Any:
    if val is None:
        return None
    if isinstance(val, str):
        try:
            return json.loads(val)
        except ValueError:
            return None
    return val


def _person(val: Any) -> PersonModel | None:
    data = _parse_jsonb(val)
    if isinstance(data, dict) and (data.get("name") or data.get("email")):
        return PersonModel(
            name=str(data.get("name") or data.get("email") or ""),
            email=data.get("email"),
            provider_user_id=(
                str(data["provider_user_id"])
                if data.get("provider_user_id") is not None else None
            ),
        )
    return None


def _person_list(val: Any) -> list[PersonModel]:
    """A JSONB array of person dicts → [PersonModel]. Skips blanks. Order (hence
    the primary/display owner at [0]) is preserved."""
    data = _parse_jsonb(val)
    out: list[PersonModel] = []
    if isinstance(data, list):
        for entry in data:
            p = _person(entry)
            if p:
                out.append(p)
    return out


def _iso(val: Any) -> str | None:
    return val.isoformat() if val is not None else None


def _row_to_item(row: Any) -> GtdItemModel:
    """A task row, as a seam arm shapes it (``item_lens._pm_item``) → model."""
    return GtdItemModel(
        id=str(row.id),
        source=row.source,
        provider=getattr(row, "account_provider", None)
        or ("local" if row.source == "LOCAL" else None),
        account_id=str(row.account_id) if row.account_id else None,
        provider_task_id=row.provider_task_id,
        provider_url=row.provider_url,
        title=row.title,
        notes=row.description,
        disposition=row.disposition,
        next_action=row.next_action,
        context=row.context,
        energy=row.energy,
        time_estimate_mins=row.time_estimate_mins,
        is_two_minute=bool(row.is_two_minute),
        important=bool(getattr(row, "important", False)),
        leveraged=bool(getattr(row, "leveraged", False)),
        deep_work=bool(getattr(row, "deep_work", False)),
        kept_mine=bool(getattr(row, "kept_mine", False)),
        project_id=str(row.project_id) if row.project_id else None,
        defer_until=_iso(row.defer_until),
        sync_state=row.sync_state or "local",
        provider_status=row.provider_status,
        assignee=_person(row.assignee),
        # Full owner set; fall back to the single `assignee` for rows written
        # before the column existed / before their first re-sync.
        assignees=(
            _person_list(getattr(row, "assignees", None))
            or ([p] if (p := _person(row.assignee)) else [])
        ),
        is_mine=bool(row.is_mine),
        workflow_stage=getattr(row, "workflow_stage", None),
        sort_key=getattr(row, "sort_key", None),
        parent_item_id=(str(row.parent_item_id)
                        if getattr(row, "parent_item_id", None) else None),
        subtask_count=int(getattr(row, "subtask_count", 0) or 0),
        archived_at=_iso(getattr(row, "archived_at", None)),
        waiting_on=_person(getattr(row, "waiting_on", None)),
        delegated_at=_iso(getattr(row, "delegated_at", None)),
        expected_by=_iso(getattr(row, "expected_by", None)),
        last_nudged_at=_iso(getattr(row, "last_nudged_at", None)),
        due_at=_iso(row.due_at),
        is_hard_date=bool(row.is_hard_date),
        scheduled_start=_iso(getattr(row, "scheduled_start", None)),
        scheduled_end=_iso(getattr(row, "scheduled_end", None)),
        flexible=bool(getattr(row, "flexible", True)),
        actual_start=_iso(getattr(row, "actual_start", None)),
        actual_end=_iso(getattr(row, "actual_end", None)),
        completed_at=_iso(row.completed_at),
        clarified_at=_iso(row.clarified_at),
        origin=_parse_jsonb(getattr(row, "origin", None)),
        attachments=_parse_jsonb(getattr(row, "attachments", None)) or [],
        created_at=_iso(row.created_at) or "",
        updated_at=_iso(row.updated_at) or "",
    )


# Default GTD context list, seeded lazily per user on first read.
DEFAULT_CONTEXTS: list[tuple[str, str]] = [
    ("@computer", "Monitor"),
    ("@calls", "Phone"),
    ("@errands", "Car"),
    ("@office", "Building2"),
    ("@home", "Home"),
    ("@agenda", "Users"),
]
