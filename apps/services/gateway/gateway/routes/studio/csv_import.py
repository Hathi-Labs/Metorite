"""The CSV import of a Studio object (WS-STU C11b, task 1419).

Spec: ``project-docs/specs/ai_studio.md`` §14 C11. Tier HIGH. Two routes over
the planner ``studio/csv_import.py`` and the batch ledger ``studio/batch_store.py``.
They register on the router of ``actions.py``, as the sibling modules do.

=========================================================  ====================
``POST /packages/{id}/objects/{name}/import/preview``      plan a CSV, write nothing
``POST /packages/{id}/objects/{name}/import``              plan it again and write it
=========================================================  ====================

**Dark.** Both answer 404 until ``STUDIO_ORGS`` holds the org (the one switch
of Studio). No new flag.

**Who.** The owner of the package only. Any other member gets 404, so the
route does not tell that the package exists. The member is ``user.email`` and
the org is the session's, never request input (R5).

**The two calls are one plan.** Each call carries the CSV and the map. The
import plans the CSV again, and the plan is deterministic, so the digest of
the preview is the digest of the import. A list of more than
:data:`~gateway.studio.csv_import.APPROVAL_ROWS` valid rows needs ONE approval:
the body names ``approve_digest``, the digest that the preview answered. A
different digest, or none, answers 409 ``approval_required`` with the digest
and writes nothing.

**Row rules.** Each row is a create write through ``batch_store.run_chunk``,
which passes ``Viewer(member)`` to ``apply_writes``. So the row rules and the
caps bind a row as they bind a write of the door. A refused row fails ONE item
and the import goes on. A chunk is :data:`~gateway.studio.batch_store.CHUNK_WRITES`
writes, and each chunk is its own transaction, so the org lock is free
between chunks.

**Invalid rows.** A CSV with an invalid row writes nothing, unless the body
sets ``skip_invalid: true``. Then the valid rows go in and the answer lists the
rows that did not.

**An import that stops.** A crash, or a ``StoreBusy`` that does not clear, leaves
the batch ``open``. The rows that are done stay, and the answer says so with
``stopped`` and ``pending``. There is no resume route yet (a later slice). The
member removes the done rows from the CSV and sends the rest again.

**The audit.** One ``app_audit`` row for the import. It
names the op, the row counts and the batch id, never a cell.
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from typing import Any

from acb_auth import UserContext, get_current_user
from acb_common.studio_access import Viewer
from acb_common.studio_fields import NAME_PATTERN
from fastapi import Depends, HTTPException, Request
from gateway.db import tenant_session as _tenant_session
from gateway.routes.apps._common import record_app_audit_in
from gateway.routes.studio import flags
from gateway.routes.studio.actions import NOT_FOUND, router
from gateway.studio import batch_store, objects_store, packages_store
from gateway.studio import csv_import as planner

__all__ = ["MAX_BODY_BYTES", "router"]

#: The CSV, as JSON text: up to the planner's cap, plus the room of a JSON escape.
MAX_BODY_BYTES = planner.MAX_BYTES * 2 + 16 * 1024
#: Tries of one chunk that answers ``StoreBusy``, and of a deadlock.
CHUNK_TRIES = 3
#: The most chunks of one request. 1000 rows of 1 write is 20 chunks of 50.
MAX_CHUNKS = 25

BODY_TOO_LARGE = "body_too_large"
IMPORT_INVALID = "import_invalid"
APPROVAL_REQUIRED = "approval_required"
ROWS_INVALID = "rows_invalid"

_NAME = re.compile(NAME_PATTERN, re.ASCII)
_KEYS = frozenset({"csv", "mapping", "currency", "approve_digest", "skip_invalid"})


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail=NOT_FOUND)


def _invalid(message: str, code: str = IMPORT_INVALID) -> HTTPException:
    return HTTPException(status_code=422, detail={"code": code, "message": message})


def _uuid(raw: str) -> str:
    try:
        return str(uuid.UUID(raw))
    except (ValueError, TypeError, AttributeError):
        raise _not_found() from None


async def _raw_body(request: Request) -> bytes:
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BODY_BYTES:
            raise HTTPException(
                status_code=413,
                detail={
                    "code": BODY_TOO_LARGE,
                    "message": "This body is too large. Split the CSV.",
                },
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _body(raw: bytes) -> dict[str, Any]:
    try:
        body = json.loads(raw)
    except RecursionError:
        raise _invalid("The body nests too deep to read.") from None
    except ValueError:
        raise _invalid("The body must be a JSON object.") from None
    if not isinstance(body, dict) or not _KEYS.issuperset(body):
        raise _invalid(
            "Send csv, mapping, currency, approve_digest and skip_invalid, and no other key."
        )
    return body


def _mapping(raw: Any) -> dict[str, str] | None:
    if raw is None:
        return None
    if (
        not isinstance(raw, dict)
        or len(raw) > planner.MAX_COLUMNS
        or not all(isinstance(k, str) and isinstance(v, str) for k, v in raw.items())
    ):
        raise _invalid("mapping is an object of column header to field key.")
    return dict(raw)


def _plan_view(plan: planner.Plan, pid: str) -> dict[str, Any]:
    return {
        "total_rows": plan.total_rows,
        "valid_rows": plan.valid_rows,
        "preview": list(plan.preview),
        "errors": [{"row": e.row, "field": e.field, "message": e.message} for e in plan.errors],
        "errors_truncated": plan.errors_truncated,
        "needs_approval": plan.needs_approval,
        "skipped_columns": list(plan.skipped_columns),
        "digest": plan.digest(pid),
    }


async def _owned_object(
    db: Any, user: UserContext, pid: str, name: str
) -> objects_store.StudioObject:
    """The object, for the owner of the package. Anyone else, and an object
    that the package does not hold, is 404."""
    if await packages_store.get_package(db, user.email, pid) is None:
        raise _not_found()
    obj = await objects_store.get_object_by_name(db, name, package_id=pid)
    if obj is None:
        raise _not_found()
    return obj


def _read(body: dict[str, Any]) -> tuple[planner.Table, dict[str, str] | None, str | None]:
    text = body.get("csv")
    if not isinstance(text, str):
        raise _invalid("csv is the text of the file.")
    currency = body.get("currency")
    if currency is not None and not isinstance(currency, str):
        raise _invalid("currency is three capital letters, as INR.")
    try:
        return planner.parse_csv(text), _mapping(body.get("mapping")), currency
    except planner.CsvImportError as exc:
        raise _invalid(str(exc)) from None


def _plan(
    table: planner.Table,
    mapping: dict[str, str],
    obj: objects_store.StudioObject,
    currency: str | None,
) -> planner.Plan:
    try:
        return planner.plan_import(table, obj.fields, mapping, object_id=obj.id, currency=currency)
    except planner.CsvImportError as exc:
        raise _invalid(str(exc)) from None


@router.post("/packages/{package_id}/objects/{object_name}/import/preview")
async def preview_import(
    package_id: str,
    object_name: str,
    request: Request,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Plan a CSV and write nothing. With no ``mapping`` the answer holds a
    suggested map, and the plan of that map when it covers each required field."""
    if not flags.studio_allowed(user.organization_id):
        raise _not_found()
    pid = _uuid(package_id)
    if not _NAME.fullmatch(object_name):
        raise _not_found()
    body = _body(await _raw_body(request))
    table, mapping, currency = _read(body)
    async with _tenant_session() as db:
        obj = await _owned_object(db, user, pid, object_name)
    suggested = planner.suggest_mapping(table.headers, obj.fields)
    out: dict[str, Any] = {
        "headers": list(table.headers),
        "suggested_mapping": suggested,
        "fields": [
            {
                "key": f.key,
                "type": f.type,
                "required": f.required,
                "mappable": f.lines is None and f.type not in planner.UNMAPPABLE,
            }
            for f in obj.fields
        ],
    }
    use = mapping if mapping is not None else suggested
    try:
        out.update(
            _plan_view(
                planner.plan_import(table, obj.fields, use, object_id=obj.id, currency=currency),
                pid,
            )
        )
    except planner.CsvImportError as exc:
        # No complete map yet: the screen shows the map and this one sentence.
        out["plan_problem"] = str(exc)
    return out


async def _run_chunk(
    user: UserContext, bid: str, writes: dict[str, Any]
) -> batch_store.ChunkResult:
    """ONE chunk, in its own session and transaction. A busy store or a
    deadlock tries again, up to :data:`CHUNK_TRIES`."""
    for attempt in range(CHUNK_TRIES):
        try:
            async with _tenant_session() as db:
                return await batch_store.run_chunk(
                    db, user.email, bid, writes, viewer=Viewer(user.email)
                )
        except objects_store.StoreBusy:
            if attempt == CHUNK_TRIES - 1:
                raise
            await asyncio.sleep(0.05 * (attempt + 1))
    raise AssertionError("unreachable")  # pragma: no cover


@router.post("/packages/{package_id}/objects/{object_name}/import")
async def run_import(
    package_id: str,
    object_name: str,
    request: Request,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Import a CSV into the object, as the owner, through the batch ledger."""
    if not flags.studio_allowed(user.organization_id):
        raise _not_found()
    pid = _uuid(package_id)
    if not _NAME.fullmatch(object_name):
        raise _not_found()
    body = _body(await _raw_body(request))
    table, mapping, currency = _read(body)
    if mapping is None:
        raise _invalid("mapping is required to import.")
    skip = body.get("skip_invalid", False)
    if not isinstance(skip, bool):
        raise _invalid("skip_invalid is true or false.")
    approve = body.get("approve_digest")
    if approve is not None and not isinstance(approve, str):
        raise _invalid("approve_digest is the digest of the preview.")
    async with _tenant_session() as db:
        obj = await _owned_object(db, user, pid, object_name)
    plan = _plan(table, mapping, obj, currency)
    view = _plan_view(plan, pid)
    if plan.errors and not skip:
        raise HTTPException(
            status_code=422,
            detail={
                "code": ROWS_INVALID,
                "message": "Some rows are not valid. Fix them, or send "
                "skip_invalid to import the valid rows.",
                "errors": view["errors"],
                "errors_truncated": view["errors_truncated"],
            },
        )
    if not plan.items:
        raise _invalid("The CSV holds no valid row to import.")
    if plan.needs_approval and approve != view["digest"]:
        raise HTTPException(
            status_code=409,
            detail={
                "code": APPROVAL_REQUIRED,
                "digest": view["digest"],
                "valid_rows": plan.valid_rows,
                "message": "This import is large. Approve the digest of the preview to run it.",
            },
        )
    async with _tenant_session() as db:
        batch = await batch_store.open_batch(
            db,
            user.email,
            package_id=pid,
            action=planner.ACTION,
            items=plan.items,
            expect_digest=view["digest"],
        )
    writes = plan.writes_by_key()
    written: dict[str, str] = {}
    failed: dict[str, str] = {}
    status = batch.status
    pending = batch.total
    stopped = None
    try:
        for _ in range(MAX_CHUNKS):
            res = await _run_chunk(user, batch.id, writes)
            written.update(res.written)
            failed.update(res.failed)
            status, pending = res.status, res.pending
            if pending == 0 or status != batch_store.STATUS_OPEN:
                break
    except objects_store.StoreBusy:
        stopped = "store_busy"
    async with _tenant_session() as db:
        await record_app_audit_in(
            db,
            app_id=pid,
            user_email=user.email,
            kind="action",
            detail={
                "op": "csv_import",
                "batch": batch.id,
                "rows": plan.total_rows,
                "written": len(written),
                "failed": len(failed),
                "skipped": plan.total_rows - plan.valid_rows,
            },
        )
    return {
        "batch_id": batch.id,
        "status": status,
        "written": len(written),
        "failed": [
            {"row": int(k.removeprefix("row-")), "code": v}
            for k, v in sorted(failed.items(), key=lambda kv: int(kv[0].removeprefix("row-")))
        ],
        "invalid": view["errors"],
        "invalid_truncated": view["errors_truncated"],
        "pending": pending,
        "stopped": stopped,
    }
