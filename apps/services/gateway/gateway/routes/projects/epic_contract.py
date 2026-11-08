"""Projects · the contract of an epic (SP-8a, Metorite task 2291).

Spec: ``project-docs/specs/project_management_app.md`` §13.3 and §13.6 row
SP-8. Migration: the file that creates ``pm_epic_contracts``.

THE one writer of ``pm_epic_contracts``. An epic carries one contract: the
outcome, 3 to 7 acceptance checks, what is out of scope, the risk tier, the
appetite in credits and in review hours, the workstream and the spec link.

    GET /projects/tasks/{task_id}/epic-contract   the contract, or null
    PUT /projects/tasks/{task_id}/epic-contract   replaces the whole contract

The PUT body is ``{"epic_contract": {...}}``: the same block, under the same
key, that ``POST /projects/tasks`` takes. So the MCP input ``epic_contract``
of ``update_task`` and ``create_task`` is one body field on each route.

``GET /projects/tasks/{task_id}`` carries the same object as ``epic_contract``
(:func:`epic_contract_of`), and ``POST /projects/tasks`` takes an optional
``epic_contract`` for a task of an epic type (:func:`save_contract`).

**PUT replaces the whole contract.** A part that the body leaves out is
cleared. The MCP block of ``update_task`` and the chat tool
``set_epic_contract`` send the whole contract too. So a writer reads the
contract first and sends back each part that it keeps.

**The rules, in code (§13.3).** Each save holds 3 to 7 checks. No draft with
fewer checks is stored: the chat card holds a draft until the member
approves. Each check has a text of 1 to :data:`MAX_CHECK_CHARS` characters.
A check without an ``id`` gets one from its text, so a save of the same text
keeps the same id. Two checks with one id are refused. The risk tier is LOW,
STANDARD or HIGH, and the lead sets it. No code proposes a tier in SP-8a.

**Only an epic.** A write on a task that is not an epic answers 422 with
``core.NOT_EPIC_DETAIL`` (:func:`core.assert_task_is_epic`). A read of a task
that is not an epic gives ``null``. When the type of a task stops being an
epic, the row stays and the reads ignore it.

**Who may write.** The rule of the task: a caller who can see the task can
write its contract, as for every other field of the task. A pinned agent
connection sees only the tasks inside its pin (``core.Visibility``), so it
reads and writes only those contracts. A hidden task answers 404.

**The version.** ``If-Match`` carries the version of the TASK
(``pm_tasks.updated_at``). A save replaces the whole contract, so a header
that the caller sends matches strictly (``core.require_exact_version``, the
one strict check of a replace): any change after the read gives 412. A
signed agent must send it (428 without it). A member without it writes, as
D-PM-20 allows, and the app form will send it. A save writes one
``field_change`` activity, which touches the task, so the next writer must
read again.

**Dark (``EPIC_CONTRACT_ORGS``).** :func:`epic_contracts_allowed` reads the
list on each call, so a reload applies a flip with no restart. Off, both
routes answer 404 with :data:`CONTRACTS_OFF_DETAIL`, ``GET /tasks/{id}``
carries ``epic_contract: null``, and a create that sends a contract answers
the same 404 and makes no task. The table stays, and is harmless dark.

**The record.** A save that changes something writes ONE ``field_change``
activity through ``core.record_field_change``. It names each part that
changed (``epic_outcome``, ``epic_acceptance`` ...). It copies no long text:
only the tier and the two appetite numbers carry the old and new values.
``meta.via`` comes from the actor seam (``core.record_activity``).

**The tenant and the actor.** The session sets both. ``organization_id`` is
the column DEFAULT, the bound tenant. ``updated_by`` is the caller.

Fence: ``tests/unit/test_epic_contract_r8.py`` (R8).
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any

from acb_auth import UserContext, get_current_user
from acb_common import get_settings
from fastapi import Depends, Header, HTTPException
from gateway.routes.projects.core import (
    TaskModel,
    _tenant_session,
    actor,
    assert_task_is_epic,
    emit,
    from_jsonb,
    load_visible_task,
    lock_task,
    record_field_change,
    require_exact_version,
    resolve_visibility,
    router,
    row_to_dict,
    task_is_epic,
)
from pydantic import BaseModel, ConfigDict, Strict
from sqlalchemy import text

#: The count of acceptance checks in each save (§13.3).
MIN_CHECKS = 3
MAX_CHECKS = 7
#: The caps of each text, in characters. The edge holds the same numbers
#: (``agent_edge.tools.EPIC_*``), and a test pins the two equal.
MAX_CHECK_CHARS = 300
MAX_EVIDENCE_CHARS = 1000
MAX_OUTCOME_CHARS = 500
MAX_OUT_OF_SCOPE_CHARS = 2000
MAX_WORKSTREAM_CHARS = 120
MAX_SPEC_LINK_CHARS = 500
#: The largest appetite. A larger number is a 422, never a stored typo.
MAX_APPETITE_CREDITS = 1_000_000_000
MAX_APPETITE_REVIEW_HOURS = 100_000
#: The risk tiers. The migration that makes the table holds the same CHECK.
RISK_TIERS: tuple[str, ...] = ("LOW", "STANDARD", "HIGH")
#: The shape of a check id that a writer sends.
_CHECK_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,40}")

#: The parts of a contract, in the order of the record and of the reply.
CONTRACT_PARTS: tuple[str, ...] = (
    "outcome", "acceptance", "out_of_scope", "risk_tier",
    "appetite_credits", "appetite_review_hours", "workstream", "spec_link",
)
#: The parts whose old and new values the activity may carry: short values
#: with a fixed shape. The text parts name only the part.
_VALUE_PARTS: frozenset[str] = frozenset(
    {"risk_tier", "appetite_credits", "appetite_review_hours"})

#: The fixed texts of a refusal. The edge maps each one to a text of its own.
CHECK_COUNT_DETAIL = (
    f"An epic contract needs {MIN_CHECKS} to {MAX_CHECKS} acceptance checks. "
    f"Nothing changed. Send from {MIN_CHECKS} to {MAX_CHECKS} checks."
)
DUPLICATE_CHECK_DETAIL = (
    "Two acceptance checks have the same id or the same text. Nothing changed. "
    "Give each check its own text."
)
VERSION_REQUIRED_DETAIL = (
    "An agent sends If-Match with the version of the task. Read the task, "
    "then send its version."
)
#: The 404 of both routes, and of a create with a contract, while
#: ``EPIC_CONTRACT_ORGS`` does not hold the org. A text of its own, so the
#: edge and the chat tell it from a task that is not there.
CONTRACTS_OFF_DETAIL = "Epic contracts are not on for this organization."


def epic_contracts_allowed(organization_id: str | None) -> bool:
    """True when ``EPIC_CONTRACT_ORGS`` holds the org, or holds ``*``.
    Default OFF.

    The org comes from the session or from a row that the session bound,
    never from request input (R5). It reads ``get_settings()`` on each call,
    so a flag reload needs no restart.
    """
    org = str(organization_id or "").strip().lower()
    if not org:
        return False
    raw = str(getattr(get_settings(), "epic_contract_orgs", "") or "")
    listed = {p.strip().lower() for p in raw.split(",") if p.strip()}
    return "*" in listed or org in listed


def refuse_when_off(organization_id: str | None) -> None:
    """404 with :data:`CONTRACTS_OFF_DETAIL` while the org is off."""
    if not epic_contracts_allowed(organization_id):
        raise HTTPException(status_code=404, detail=CONTRACTS_OFF_DETAIL)


async def require_epic_contracts(user: UserContext = Depends(get_current_user)) -> None:
    """The route dependency. It runs before the body is read."""
    refuse_when_off(getattr(user, "organization_id", None))


_ENABLED = [Depends(require_epic_contracts)]


class AcceptanceCheckIn(BaseModel):
    """One acceptance check. ``id`` is optional: the server makes one."""

    model_config = ConfigDict(extra="forbid")

    id: str | None = None
    text: str = ""
    done: bool = False
    evidence: str | None = None


class EpicContractIn(BaseModel):
    """The body of ``PUT …/epic-contract``, and the ``epic_contract`` of a
    create. Any other key is a 422, for every caller. The tenant and the
    actor are never keys here."""

    model_config = ConfigDict(extra="forbid")

    outcome: str | None = None
    acceptance: list[AcceptanceCheckIn] | None = None
    out_of_scope: str | None = None
    risk_tier: str | None = None
    #: Strict: a number only. "40" and true are a 422, never a stored guess.
    appetite_credits: Annotated[float, Strict()] | None = None
    appetite_review_hours: Annotated[float, Strict()] | None = None
    workstream: str | None = None
    spec_link: str | None = None


class EpicContractPut(BaseModel):
    """The body of ``PUT …/epic-contract``: the whole contract, under the
    key that the create uses too. Any other key is a 422."""

    model_config = ConfigDict(extra="forbid")

    epic_contract: EpicContractIn


def _refuse(detail: str) -> HTTPException:
    return HTTPException(status_code=422, detail=detail)


def _text(value: str | None, name: str, cap: int) -> str | None:
    """A stripped text, ``None`` for blank. Refuses a NUL and a long text."""
    if value is None:
        return None
    if "\x00" in value:
        raise _refuse(f"The {name} holds a character that Metorite cannot store.")
    out = value.strip()
    if len(out) > cap:
        raise _refuse(f"The {name} is longer than {cap} characters. Nothing changed.")
    return out or None


def _number(value: float | None, name: str, cap: int) -> float | None:
    if value is None:
        return None
    if not math.isfinite(value) or value < 0 or value > cap:
        raise _refuse(f"The {name} is a number from 0 to {cap}. Nothing changed.")
    return float(value)


def check_id(text_: str) -> str:
    """The id of a check that came without one: ``c`` and 8 hex of its text.

    The same text gives the same id, so a resave keeps the id."""
    return "c" + hashlib.sha256(text_.encode("utf-8")).hexdigest()[:8]


def clean_contract(payload: EpicContractIn) -> dict[str, Any]:
    """The stored values of a contract, or a 422 with a fixed text.

    The rules run before any read, so a refusal touches nothing.
    """
    raw = payload.acceptance or []
    if not MIN_CHECKS <= len(raw) <= MAX_CHECKS:
        raise _refuse(CHECK_COUNT_DETAIL)
    checks: list[dict[str, Any]] = []
    for item in raw:
        body = _text(item.text, "check text", MAX_CHECK_CHARS)
        if body is None:
            raise _refuse("Each acceptance check needs a text. Nothing changed.")
        given = (item.id or "").strip()
        if given and not _CHECK_ID_RE.fullmatch(given):
            raise _refuse(
                "A check id holds letters, digits, '-' and '_', at most 40. "
                "Nothing changed. Leave the id out and Metorite makes one.")
        checks.append({
            "id": given or check_id(body),
            "text": body,
            "done": bool(item.done),
            "evidence": _text(item.evidence, "check evidence", MAX_EVIDENCE_CHARS),
        })
    ids = [c["id"] for c in checks]
    texts = [c["text"].casefold() for c in checks]
    if len(set(ids)) != len(ids) or len(set(texts)) != len(texts):
        raise _refuse(DUPLICATE_CHECK_DETAIL)
    tier = (payload.risk_tier or "").strip().upper() or None
    if tier is not None and tier not in RISK_TIERS:
        raise _refuse("The risk tier is LOW, STANDARD or HIGH. Nothing changed.")
    return {
        "outcome": _text(payload.outcome, "outcome", MAX_OUTCOME_CHARS),
        "acceptance": checks,
        "out_of_scope": _text(payload.out_of_scope, "out of scope text",
                              MAX_OUT_OF_SCOPE_CHARS),
        "risk_tier": tier,
        "appetite_credits": _number(payload.appetite_credits, "appetite in credits",
                                    MAX_APPETITE_CREDITS),
        "appetite_review_hours": _number(payload.appetite_review_hours,
                                         "appetite in review hours",
                                         MAX_APPETITE_REVIEW_HOURS),
        "workstream": _text(payload.workstream, "workstream", MAX_WORKSTREAM_CHARS),
        "spec_link": _text(payload.spec_link, "spec link", MAX_SPEC_LINK_CHARS),
    }


def _num_out(value: Any) -> float | None:
    if value is None:
        return None
    return float(value) if isinstance(value, Decimal | int | float) else None


def _checks_out(value: Any) -> list[dict[str, Any]]:
    """The stored checks, each in the four keys, whatever the row holds."""
    found = from_jsonb(value)
    if not isinstance(found, list):
        return []
    out: list[dict[str, Any]] = []
    for item in found:
        if not isinstance(item, dict):
            continue
        out.append({
            "id": str(item.get("id") or ""),
            "text": str(item.get("text") or ""),
            "done": bool(item.get("done")),
            "evidence": item.get("evidence") if isinstance(item.get("evidence"), str)
            else None,
        })
    return out


def contract_out(row: Any) -> dict[str, Any]:
    """One ``pm_epic_contracts`` row as the wire object."""
    updated = getattr(row, "updated_at", None)
    return {
        "outcome": getattr(row, "outcome", None),
        "acceptance": _checks_out(getattr(row, "acceptance", None)),
        "out_of_scope": getattr(row, "out_of_scope", None),
        "risk_tier": getattr(row, "risk_tier", None),
        "appetite_credits": _num_out(getattr(row, "appetite_credits", None)),
        "appetite_review_hours": _num_out(getattr(row, "appetite_review_hours", None)),
        "workstream": getattr(row, "workstream", None),
        "spec_link": getattr(row, "spec_link", None),
        "updated_by": getattr(row, "updated_by", None),
        "updated_at": updated.isoformat() if isinstance(updated, datetime) else None,
    }


#: The read. The tenant clause rides on the read as well as on RLS (R5).
_CONTRACT_SQL = (
    "SELECT * FROM pm_epic_contracts"
    " WHERE task_id = CAST(:tid AS uuid) AND organization_id = CAST(:org AS uuid)"
)


async def _stored(db: Any, task: Any) -> Any | None:
    return (await db.execute(text(_CONTRACT_SQL), {
        "tid": str(task.id), "org": str(task.organization_id),
    })).fetchone()


async def epic_contract_of(db: Any, task: Any) -> dict[str, Any] | None:
    """The contract of a visible task row, or ``None``.

    ``None`` for a task that is not an epic, even when a row from an earlier
    epic type stays (§13.3), and for an epic with no contract yet. ``None``
    too while ``EPIC_CONTRACT_ORGS`` does not hold the org of the task, which
    is the bound tenant.
    """
    if not epic_contracts_allowed(str(getattr(task, "organization_id", "") or "")):
        return None
    if not await task_is_epic(db, getattr(task, "type_id", None)):
        return None
    row = await _stored(db, task)
    return contract_out(row) if row is not None else None


_UPSERT_SQL = (
    "INSERT INTO pm_epic_contracts (task_id, outcome, acceptance, out_of_scope,"
    " risk_tier, appetite_credits, appetite_review_hours, workstream, spec_link,"
    " updated_by, updated_at)"
    " VALUES (CAST(:tid AS uuid), :outcome, CAST(:acceptance AS jsonb), :out_of_scope,"
    " :risk_tier, :appetite_credits, :appetite_review_hours, :workstream, :spec_link,"
    " :by, now())"
    " ON CONFLICT (task_id) DO UPDATE SET"
    " outcome = EXCLUDED.outcome, acceptance = EXCLUDED.acceptance,"
    " out_of_scope = EXCLUDED.out_of_scope, risk_tier = EXCLUDED.risk_tier,"
    " appetite_credits = EXCLUDED.appetite_credits,"
    " appetite_review_hours = EXCLUDED.appetite_review_hours,"
    " workstream = EXCLUDED.workstream, spec_link = EXCLUDED.spec_link,"
    " updated_by = EXCLUDED.updated_by, updated_at = now()"
    " RETURNING *"
)


def _comparable(contract: dict[str, Any] | None) -> dict[str, Any]:
    if contract is None:
        return {part: None for part in CONTRACT_PARTS}
    return {part: contract.get(part) for part in CONTRACT_PARTS}


def changed_parts(before: dict[str, Any] | None,
                  after: dict[str, Any]) -> list[dict[str, Any]]:
    """The ``field_change`` items of a save: one for each part that moved.

    The field is ``epic_<part>``, which the timeline prints as "Epic outcome".
    A text part carries no value. The tier and the appetite carry old and new.
    """
    old, new = _comparable(before), _comparable(after)
    out: list[dict[str, Any]] = []
    for part in CONTRACT_PARTS:
        if old[part] == new[part]:
            continue
        item: dict[str, Any] = {"field": f"epic_{part}"}
        if part in _VALUE_PARTS:
            item["old"], item["new"] = old[part], new[part]
        out.append(item)
    return out


async def save_contract(db: Any, user: UserContext, task: Any,
                        values: dict[str, Any]) -> tuple[dict[str, Any], list[dict]]:
    """Store *values* (from :func:`clean_contract`) as the contract of *task*.

    The caller has loaded the task through visibility and checked that it is
    an epic. A save that changes nothing writes nothing. Returns the contract
    as it is now and the parts that changed.
    """
    stored = await _stored(db, task)
    before = contract_out(stored) if stored is not None else None
    if before is not None and not changed_parts(before, values):
        return before, []
    row = (await db.execute(text(_UPSERT_SQL), {
        "tid": str(task.id),
        "outcome": values["outcome"],
        "acceptance": json.dumps(values["acceptance"]),
        "out_of_scope": values["out_of_scope"],
        "risk_tier": values["risk_tier"],
        "appetite_credits": values["appetite_credits"],
        "appetite_review_hours": values["appetite_review_hours"],
        "workstream": values["workstream"],
        "spec_link": values["spec_link"],
        "by": actor(user),
    })).fetchone()
    after = contract_out(row)
    changes = changed_parts(before, after)
    if changes:
        # Through the ONE field_change door (WS-27w). It touches the task,
        # so the version of the task moves and a stale writer gets 412.
        await record_field_change(
            db, created_by=actor(user), task_id=str(task.id), changes=changes,
        )
    return after, changes


async def _require_version(db: Any, task: Any, if_match: str | None,
                           user: UserContext) -> None:
    """``If-Match`` on the version of the task, strict for every caller.

    ``precondition_holds`` passes a token from before a satellite bump, and
    a contract save is itself such a bump. A whole-contract replace on that
    token would drop another writer's save in silence. So a header that the
    caller sends matches strictly (``core.require_exact_version``, the one
    strict check of a replace). A signed agent must send it. A member may
    leave it out (D-PM-20).
    """
    if not isinstance(if_match, str):
        if getattr(user, "agent", None) is not None:
            raise HTTPException(status_code=428, detail=VERSION_REQUIRED_DETAIL)
        return
    await require_exact_version(db, str(task.id), if_match)


def _reply(task: Any, contract: dict[str, Any] | None, *, epic: bool) -> dict[str, Any]:
    return {
        "task_id": str(task.id),
        "is_epic": epic,
        "epic_contract": contract,
        # The token of the next write (`If-Match`).
        "version": task.updated_at,
    }


@router.get("/tasks/{task_id}/epic-contract", dependencies=_ENABLED)
async def get_epic_contract(
    task_id: str, user: UserContext = Depends(get_current_user),
) -> dict:
    """The contract of one task. ``epic_contract`` is ``null`` for a task
    that is not an epic, and for an epic with no contract yet."""
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        task = await load_visible_task(db, vis, task_id)
        epic = await task_is_epic(db, getattr(task, "type_id", None))
        row = await _stored(db, task) if epic else None
        return _reply(task, contract_out(row) if row is not None else None, epic=epic)


@router.put("/tasks/{task_id}/epic-contract", dependencies=_ENABLED)
async def put_epic_contract(
    task_id: str, payload: EpicContractPut,
    user: UserContext = Depends(get_current_user),
    if_match: str | None = Header(None, alias="If-Match"),
) -> dict:
    """Replace the whole contract of an epic. Each part that the body leaves
    out is cleared. Returns the contract, the new version, ``changed`` (the
    ``field_change`` fields) and ``task``, the task row after the save."""
    values = clean_contract(payload.epic_contract)
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        # 404 first, so a caller who cannot see the task learns nothing more.
        await load_visible_task(db, vis, task_id)
        # The lock holds the version check and the save together.
        task = await lock_task(db, task_id)
        await _require_version(db, task, if_match, user)
        await assert_task_is_epic(db, getattr(task, "type_id", None))
        contract, changes = await save_contract(db, user, task, values)
        after = await lock_task(db, task_id)
        result = _reply(after, contract, epic=True)
        result["changed"] = [c["field"] for c in changes]
        # The task row as it is now, so a caller merges it over its copy, and
        # the edge reports the save with the new version (as a PATCH does).
        result["task"] = row_to_dict(after, TaskModel)
    if changes:
        await emit("pm.task.updated", {"task_id": str(task.id)})
    return result


__all__ = [
    "CHECK_COUNT_DETAIL",
    "CONTRACTS_OFF_DETAIL",
    "CONTRACT_PARTS",
    "DUPLICATE_CHECK_DETAIL",
    "MAX_APPETITE_CREDITS",
    "MAX_APPETITE_REVIEW_HOURS",
    "MAX_CHECKS",
    "MAX_CHECK_CHARS",
    "MAX_EVIDENCE_CHARS",
    "MAX_OUTCOME_CHARS",
    "MAX_OUT_OF_SCOPE_CHARS",
    "MAX_SPEC_LINK_CHARS",
    "MAX_WORKSTREAM_CHARS",
    "MIN_CHECKS",
    "RISK_TIERS",
    "VERSION_REQUIRED_DETAIL",
    "AcceptanceCheckIn",
    "EpicContractIn",
    "EpicContractPut",
    "changed_parts",
    "check_id",
    "clean_contract",
    "contract_out",
    "epic_contract_of",
    "epic_contracts_allowed",
    "get_epic_contract",
    "put_epic_contract",
    "refuse_when_off",
    "require_epic_contracts",
    "save_contract",
]
