"""The action door of AI Studio (STU-B2a, B2b-1 and B2c-1, ``ai_studio.md`` §3.2).

``POST /studio/apps/{app_id}/actions/{action}`` runs one action that a Studio
package declares. The route registers on the router of ``actions.py``, as
``skills.py`` does, so ``main.py`` mounts no second router. The gate
``require_feature_router("studio")`` and the prefix ``/studio`` hold here (J1).

**The order of the checks.** Each check runs before the next one:

1. The switch. An org outside ``STUDIO_ORGS`` gets 404 ``Not found``, for
   each body. The route takes the raw request, so FastAPI parses no body
   before this check.
2. The shape of the action. An ``action`` that is not an A2 action id
   (``OP_PATTERN``, at most 48 characters) names nothing, so it gets 404
   ``Not found`` before any read.
3. The package. ``packages_store.get_package`` reads it for the caller. A
   package of another member or of another org, an App Workshop app and an id
   that names nothing all get 404 ``Not found``. The answer names nothing.
4. The declaration. The path names an ``actions[].id`` of the manifest. An
   id that the manifest does not declare gets 403 ``not_declared``.
5. The class. An ``object:<name>.<op>`` action takes its class from
   :data:`~acb_common.studio_manifest.OBJECT_OP_CLASSES`. Each other source
   takes its class from the B1 catalog, ``actions.catalog_rows``. Those two
   are the only sources of a class. Class X gets 403 ``class_x``, and an id
   that the catalog does not serve gets 403 ``not_in_catalog``. A catalog
   that fails gets 503 ``catalog_unavailable``.
6. The approval. Class B and class C get 403 ``approval_required``. The one
   exception is a DIRECT call of a member (B2c-1): it runs the create, the
   update and the delete of an object (:data:`DIRECT_EXECUTORS`). No card
   exists yet (B2c-2), so no other write runs.
7. The body. Only a call that an open or a direct executor runs reads the body, so a
   non-owner and a refused action read no byte. No session is open during the
   read, because a wait on a slow client must not hold a pool connection. The
   read stops past :data:`MAX_BODY_BYTES`, and the answer is 413
   ``body_too_large``.
8. The second check, in a new session. :func:`_check` runs again: it reads
   the package as this member, then checks the declaration and the class on
   that fresh manifest. A slow client can pass the first check and hold the
   read open. Without this check, it reads after it lost access or after the
   package changed. The owner-only rule hides this today, but J2 (sharing) and
   C8 (row rules) build on this door. A miss gets the same 404, and a
   declaration that is gone gets the same 403. An action that now names
   another source gets 403 ``source_not_open``.
9. The executor, in that session. The door opens two: ``object:<name>.get``
   (B2a) and ``object:<name>.list`` (B2b-1). For a direct call it also runs
   the create, the update and the delete of an object (B2c-1). Each one
   parses the body here, and only here. An object name that the package does not hold gets 404
   ``object_missing``, and a record id that names nothing gets 404 ``Not
   found``. Each other class A action gets 403 ``source_not_open`` (B2b-2 and
   B2b-3 name the next executors).

**The list.** An empty body means ``{}``. The door accepts only the keys of
:data:`LIST_INPUT_KEYS`, and ``limit`` and ``offset`` must each be an ``int``.
So a refused body reads nothing, and ``Infinity`` never reaches
``paging.bounds``, which raises ``OverflowError`` on it. Then
``objects_store.query_records`` checks the rest, and it is the one grammar. A
:class:`~acb_common.studio_query.QueryError` gets 422 ``{code, message,
field}``. ``query_timeout`` gets 503 with ``Retry-After``. The store leaves
the session usable after each refusal.

**Tenancy and the member (R5).** The org is the bound session
(``_tenant_session()``), and the member is ``user.email``. No check reads an
org, a member or a package from the path, the query or the body. A key such
as ``organization_id`` in a body changes nothing.

**The audit.** Each call that reaches a package of the caller, and that the
door serves or refuses, writes one ``app_audit`` row (kind ``action``) through
``record_app_audit_bound``. The row holds the action id, its ``uses``, its
class and the outcome, and no record data. The row of a direct write also
names its record by ``record_id`` (B2c-1). Each text in it has at most
:data:`MAX_AUDIT_TEXT` characters. A 404 at either read of the package writes
no row. A refused query gives the row its code in ``reason``, and never its
input. A raw
fault, such as a failed statement, gets 500 and writes no row, because the
session is not safe to write on.

⚠️ **The audit is best-effort, on purpose, for a class A read only.** It
opens its own session after the read, and a failed audit logs and never
fails the read. A read changes nothing, so a lost row loses no change. B2c
must not copy this: it writes its audit row in the same transaction as the
write, and a failed audit fails the write.
"""

from __future__ import annotations

import contextlib
import dataclasses
import json
import re
import uuid
from collections.abc import Iterator
from typing import Any

from acb_auth import UserContext, get_current_user
from acb_auth import direct_proof as _direct_proof
from acb_common import get_logger, studio_query
from acb_common.app_manifests.base import OP_PATTERN
from acb_common.studio_fields import FieldError, RecordError
from acb_common.studio_manifest import OBJECT_OP_CLASSES, UsesRef, parse_uses
from acb_common.studio_rules import COMPUTED_FIELD, FormulaError, RuleViolation
from fastapi import Depends, HTTPException, Request
from gateway.db import tenant_session as _tenant_session
from gateway.routes.apps._common import record_app_audit_bound, record_app_audit_in
from gateway.routes.studio import actions, flags
from gateway.routes.studio.actions import NOT_FOUND, router
from gateway.studio import objects_store, packages_store

__all__ = ["DIRECT_EXECUTORS", "LIST_INPUT_KEYS", "MAX_AUDIT_TEXT", "MAX_BODY_BYTES",
           "OPEN_EXECUTORS", "REFUSALS", "WRITERS_PER_ORG", "WRITERS_TOTAL", "router"]

_log = get_logger("gateway.studio.invoke")

#: The executors that the door opens, as (source, op): the object ``get`` of
#: B2a and the object ``list`` of B2b-1.
OPEN_EXECUTORS: frozenset[tuple[str, str | None]] = frozenset(
    {("object", "get"), ("object", "list")})

#: The executors that run for a DIRECT call only (STU-B2c-1): the create, the
#: update and the delete of an object. A call is direct when the BFF signed it
#: for the member (``acb_auth.direct_proof``). Any other call gets
#: ``approval_required`` for these, as before.
DIRECT_EXECUTORS: frozenset[tuple[str, str | None]] = frozenset(
    {("object", "create"), ("object", "update"), ("object", "delete")})

#: The most writes in flight for one org, and in the process (B2c-1). The door
#: counts them BEFORE it opens a session, so a call past the cap holds no
#: connection. The gateway runs one uvicorn worker, so a count in the process
#: is the count of the box. ⚠️ The total binds only the second and later
#: writers of an org. The first writer of an org always gets a slot, so two
#: busy orgs cannot shut out every other org (fix round 1).
WRITERS_PER_ORG = 3
WRITERS_TOTAL = 6

#: The keys of the body of a list. The one input schema is their only source.
LIST_INPUT_KEYS: frozenset[str] = frozenset(studio_query.list_input_schema()["properties"])

#: The A2 action id (``ActionDef.id``). A path ``action`` of another shape
#: names nothing, so the door reads nothing for it.
_ACTION_ID = re.compile(OP_PATTERN)

#: The most characters of one text in an audit row.
MAX_AUDIT_TEXT = 200

#: The most bytes of one body (1 MiB). The grammar caps a list body far below
#: this. B2c may raise it for writes.
MAX_BODY_BYTES = 1024 * 1024

NOT_DECLARED = "not_declared"
CLASS_X = "class_x"
NOT_IN_CATALOG = "not_in_catalog"
APPROVAL_REQUIRED = "approval_required"
SOURCE_NOT_OPEN = "source_not_open"
CATALOG_UNAVAILABLE = "catalog_unavailable"
OBJECT_MISSING = "object_missing"
BODY_TOO_LARGE = "body_too_large"

#: The code of each 403 and one sentence that a model can act on.
REFUSALS: dict[str, str] = {
    NOT_DECLARED: "This app declares no action with this id, so use an id from its manifest.",
    CLASS_X: "This action is class X, so no app can run it.",
    NOT_IN_CATALOG: (
        "The catalog does not serve this action now, so change the action in the manifest."),
    APPROVAL_REQUIRED: (
        "This action writes data and needs an approval, which this door cannot ask for yet."),
    SOURCE_NOT_OPEN: (
        "This door runs only the actions of an object: get and list, and create, update "
        "and delete from the member's own screen."),
}

#: The 503 of a catalog that fails, and the 404 of an object that the package
#: does not hold. Each is one sentence that a model can act on.
CATALOG_DOWN = "The action catalog is not available now, so try the action again later."
NO_OBJECT = "This package holds no object for this action yet, so publish the package."

BODY_OVER_CAP = "This body is too large for this door, so send a smaller body."

BAD_RECORD_ID = "Send the record id as a UUID in the key id of a JSON object."
BAD_LIST_INPUT = (
    "Send a JSON object with only filter, sort, limit and offset, "
    "and whole numbers for limit and offset.")

#: The header value of a 503 ``query_timeout``, in seconds.
RETRY_AFTER = "2"

#: The header value of a 503 ``store_busy``, in seconds (B2c-1).
BUSY_RETRY_AFTER = "1"

STORE_BUSY = "store_busy"
FIELD_INVALID = "field_invalid"
RULE_REFUSED = "rule_refused"
RECORD_INVALID = "record_invalid"
BAD_INPUT = "bad_input"

BUSY_MESSAGE = "This app is busy now, so try the write again in a moment."
BAD_WRITE_INPUT = (
    "Send a JSON object with only data for a create, id and data for an update, "
    "and id for a delete.")
STORE_REFUSED = "The store refuses this write, so check the id and the data."


@dataclasses.dataclass
class _Fail:
    """A refusal or a fault of a direct write that the door answers: the
    outcome and reason of its audit row, and the HTTP error. It is built
    inside the session and RAISED after the session closes, because an
    ``HTTPException`` inside the block would roll back the audit row."""

    outcome: str
    reason: str | None
    error: HTTPException


@dataclasses.dataclass
class _Outcome:
    """What one call did. The audit row and the answer come from it."""

    action: str
    uses: str | None = None
    cls: str | None = None
    refusal: str | None = None
    record: objects_store.Record | None = None
    page: objects_store.RecordPage | None = None
    query_error: studio_query.QueryError | None = None
    missing: bool = False
    object_missing: bool = False
    invalid: bool = False
    catalog_down: bool = False
    too_large: bool = False
    #: The id of the record that a direct delete removed.
    deleted: str | None = None
    #: The record of a direct write, for its audit row: the new id of a
    #: create that ran, or the id in the body of an update or a delete when
    #: it is a UUID. It is an id and holds no record data.
    record_id: str | None = None
    #: The answer of a direct write that failed or was refused (B2c-1).
    fail: _Fail | None = None
    #: True when the call wrote its own audit row, in the session of the write,
    #: or wrote none because it held no connection.
    audited: bool = False


def _app_id(raw: str) -> str:
    try:
        return str(uuid.UUID(raw))
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(status_code=422, detail="The app id must be a UUID") from None


def _cap(value: str | None) -> str | None:
    """A free text of an audit row, cut to :data:`MAX_AUDIT_TEXT`."""
    return None if value is None else value[:MAX_AUDIT_TEXT]


def _declared(manifest: dict[str, Any], action: str) -> dict[str, Any] | None:
    """The ``actions[]`` entry of *manifest* whose id is *action*, or None."""
    found = manifest.get("actions")
    if not isinstance(found, list):
        return None
    for entry in found:
        if isinstance(entry, dict) and entry.get("id") == action:
            return entry
    return None


def refusal_for(ref: UsesRef, cls: str | None, direct: bool = False) -> str | None:
    """The refusal code of an action of *ref* and class *cls*, or None when
    an open executor runs it. *cls* is None when no source gives the action a
    class. A class B or C object action runs only for a *direct* call, and only
    when :data:`DIRECT_EXECUTORS` holds it (B2c-1)."""
    if cls is None:
        return NOT_IN_CATALOG
    if cls == "X":
        return CLASS_X
    if cls in ("B", "C"):
        return None if direct and (ref.source, ref.op) in DIRECT_EXECUTORS else APPROVAL_REQUIRED
    if cls != "A" or (ref.source, ref.op) not in OPEN_EXECUTORS:
        return SOURCE_NOT_OPEN
    return None


async def _catalog_class(uses: str) -> str | None:
    """The class that the B1 catalog gives *uses*, or None. It opens its own
    session, so the caller runs it after its own session closes."""
    for row in await actions.catalog_rows():
        if row.get("id") == uses:
            return str(row.get("class"))
    return None


async def _read_body(request: Request) -> bytes | None:
    """The bytes of the body, or None past :data:`MAX_BODY_BYTES`. The read
    stops at the first chunk past the cap, so the door holds no more than one
    chunk over the cap. The executor parses the bytes."""
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BODY_BYTES:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


def _json_object(raw: bytes) -> dict[str, Any] | None:
    """The JSON object of the body *raw*, or None. The door parses the body
    here, after the switch and the package. A body that is not JSON, or that
    nests too deep to parse, gives None."""
    try:
        payload = json.loads(raw)
    except (ValueError, RecursionError):
        return None
    return payload if isinstance(payload, dict) else None


def _record_id(raw: bytes) -> str | None:
    """The record id of the body *raw*, or None."""
    found = (_json_object(raw) or {}).get("id")
    return found if isinstance(found, str) else None


def _list_input(raw: bytes) -> dict[str, Any]:
    """The input of a list, from the body *raw*. An empty body means ``{}``.

    It refuses a key outside :data:`LIST_INPUT_KEYS`, and a ``limit`` or an
    ``offset`` that is not an ``int``. So a bool, a null, a float and
    ``Infinity`` stop here. The sentence never echoes a key or a value.
    """
    if not raw:
        return {}
    payload = _json_object(raw)
    if (payload is None or not LIST_INPUT_KEYS.issuperset(payload)
            or any(type(payload[k]) is not int for k in ("limit", "offset") if k in payload)):
        raise studio_query.QueryError(studio_query.VALUE_INVALID, BAD_LIST_INPUT)
    return payload


async def _run_object_get(db: Any, package: packages_store.Package, ref: UsesRef,
                          raw: bytes, out: _Outcome) -> None:
    """Read one record of the object *ref* of *package*. The id comes from
    the body, checked by the id rule of C1."""
    rid = _record_id(raw)
    if rid is None:
        out.invalid = True
        return
    try:
        # The store checks each id before its statement, so a refusal here
        # leaves the session usable.
        obj = await objects_store.get_object_by_name(
            db, str(ref.object), package_id=package.id)
        if obj is None:
            out.object_missing = True
            return
        rec = await objects_store.get_record(db, obj.id, rid, package_id=package.id)
    except objects_store.ObjectStoreError:
        out.invalid = True
        return
    if rec is None:
        out.missing = True
    else:
        out.record = rec


async def _run_object_list(db: Any, package: packages_store.Package, ref: UsesRef,
                           raw: bytes, out: _Outcome) -> None:
    """Read one page of the records of the object *ref* of *package*. The body
    is checked first, so a refused body reads nothing."""
    try:
        body = _list_input(raw)
        obj = await objects_store.get_object_by_name(
            db, str(ref.object), package_id=package.id)
        if obj is None:
            out.object_missing = True
            return
        # The store checks each id and the input before its first statement,
        # and a timeout rolls back its SAVEPOINT only. So a refusal here
        # leaves the session usable.
        page = await objects_store.query_records(
            db, package_id=package.id, object_id=obj.id, filter=body.get("filter"),
            sort=body.get("sort"), limit=body.get("limit"), offset=body.get("offset"))
    except studio_query.QueryError as exc:
        out.query_error = exc
        return
    except objects_store.ObjectStoreError as exc:
        # The body named no id, so this is a stored name or id that the store
        # refuses. The package holds no object that the action can read.
        _log.warning("studio.invoke.stored_value_refused", error=type(exc).__name__)
        out.object_missing = True
        return
    if page is None:
        out.object_missing = True
    else:
        out.page = page


async def _check(db: Any, member: str, pid: str, action: str, out: _Outcome,
                 direct: bool = False) -> tuple[packages_store.Package, UsesRef | None]:
    """One run of the checks on the session *db*: the package as *member*, the
    declaration, then the class of an object action. Each other source takes
    its class from the catalog, outside the session.

    The door runs this function twice: before the body read, and again after
    it, on the fresh manifest. So it sets each field of *out* that it decides.
    A package that *member* cannot read raises 404.
    """
    out.uses, out.cls, out.refusal = None, None, None
    package = await packages_store.get_package(db, member, pid)
    if package is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    entry = _declared(package.manifest, action)
    ref = None if entry is None else parse_uses(entry.get("uses"))
    if entry is None or ref is None:
        out.refusal = NOT_DECLARED
        return package, ref
    out.uses = str(entry["uses"])
    if ref.source == "object":
        out.cls = OBJECT_OP_CLASSES.get(str(ref.op))
        out.refusal = refusal_for(ref, out.cls, direct)
    return package, ref


#: The writes in flight, by org. An org with none has no entry.
_IN_FLIGHT: dict[str, int] = {}

#: The keys that the body of each direct write takes, and no other.
_WRITE_KEYS: dict[str, frozenset[str]] = {
    "create": frozenset({"data"}),
    "update": frozenset({"id", "data"}),
    "delete": frozenset({"id"}),
}


@contextlib.contextmanager
def _writer_slot(org: str, wanted: bool) -> Iterator[bool]:
    """Take one slot of the writers of *org*, when *wanted*. Yields True when
    the call holds a slot. The count runs with no ``await``, so no other call
    interleaves. The door takes the slot BEFORE it opens a session, so a call
    past the cap holds no connection. The slot goes back in ``finally``, and an
    org with no writer has no entry.

    The first writer of an org always gets a slot. A second or later writer
    needs fewer than :data:`WRITERS_PER_ORG` of its org, and fewer than
    :data:`WRITERS_TOTAL` in all."""
    mine = _IN_FLIGHT.get(org, 0)
    if not wanted or mine >= WRITERS_PER_ORG \
            or (mine > 0 and sum(_IN_FLIGHT.values()) >= WRITERS_TOTAL):
        yield False
        return
    _IN_FLIGHT[org] = _IN_FLIGHT.get(org, 0) + 1
    try:
        yield True
    finally:
        left = _IN_FLIGHT.get(org, 1) - 1
        if left > 0:
            _IN_FLIGHT[org] = left
        else:
            _IN_FLIGHT.pop(org, None)


def _busy() -> _Fail:
    return _Fail("failed", STORE_BUSY, HTTPException(
        status_code=503, detail={"code": STORE_BUSY, "message": BUSY_MESSAGE},
        headers={"Retry-After": BUSY_RETRY_AFTER}))


def _refused(code: str, message: str, rule: str | None = None,
             field: str | None = None) -> _Fail:
    """A 422 with the one shape: ``code``, ``message``, ``rule`` and
    ``fieldErrors``. The text never echoes a value."""
    errors = [] if field is None else [{"field": field, "message": message}]
    return _Fail("refused", code, HTTPException(status_code=422, detail={
        "code": code, "message": message, "rule": rule, "fieldErrors": errors}))


def _set_write_error(out: _Outcome, exc: Exception) -> bool:
    """Set the answer of *out* for the error *exc* of a direct write, and say
    whether it did. A fault that it does not know stays a fault (500).

    ⚠️ The order is load-bearing. ``StoreBusy`` is a subclass of
    ``ObjectStoreError``, so it comes first. A busy org is a 503 that can work
    later, and not a refused write."""
    if isinstance(exc, objects_store.StoreBusy):
        out.fail = _busy()
    elif isinstance(exc, objects_store.RecordNotFound):
        out.missing = True
    elif isinstance(exc, FieldError):
        out.fail = _refused(FIELD_INVALID, exc.message, field=exc.field)
    elif isinstance(exc, RuleViolation):
        computed = isinstance(exc, FormulaError) and exc.code == COMPUTED_FIELD
        out.fail = _refused(COMPUTED_FIELD if computed else RULE_REFUSED, exc.message,
                            rule=exc.rule, field=getattr(exc, "field", None))
    elif isinstance(exc, studio_query.QueryError):
        out.fail = _Fail("failed" if exc.code == studio_query.TIMEOUT else "refused", exc.code,
                         _query_refusal(exc))
    elif isinstance(exc, RecordError):
        out.fail = _refused(RECORD_INVALID, str(exc))
    elif isinstance(exc, objects_store.ObjectStoreError):
        out.fail = _refused(RECORD_INVALID, STORE_REFUSED)
    else:
        return False
    return True


def _uuid_or_none(value: str) -> str | None:
    """*value* as a canonical UUID, or None. An audit row names a record by
    its id only, so a text that is not a UUID never reaches the row."""
    try:
        return str(uuid.UUID(value))
    except (ValueError, TypeError, AttributeError):
        return None


def _write_input(op: str, raw: bytes) -> dict[str, Any] | None:
    """The body of a direct write, or None. It takes exactly the keys of
    :data:`_WRITE_KEYS`, ``data`` is an object and ``id`` is a string. The
    store checks the id rule of C1."""
    body = _json_object(raw)
    if body is None or set(body) != _WRITE_KEYS[op]:
        return None
    if "data" in body and not isinstance(body["data"], dict):
        return None
    if "id" in body and not isinstance(body["id"], str):
        return None
    return body


async def _do_object_write(db: Any, member: str, package: packages_store.Package,
                           ref: UsesRef, raw: bytes, out: _Outcome) -> None:
    """Run one create, update or delete as *member*, through ONE
    ``apply_writes`` call with one write. It sets *out* and never raises an
    ``HTTPException``. The caller writes the audit row."""
    op = str(ref.op)
    body = _write_input(op, raw)
    if body is None:
        out.fail = _refused(BAD_INPUT, BAD_WRITE_INPUT)
        return
    if "id" in body:
        out.record_id = _uuid_or_none(body["id"])
    try:
        obj = await objects_store.get_object_by_name(
            db, str(ref.object), package_id=package.id)
    except objects_store.ObjectStoreError:
        obj = None
    if obj is None:
        out.object_missing = True
        return
    write = objects_store.Write(op=op, object_id=obj.id, record_id=body.get("id"),
                                data=body.get("data"))
    try:
        answers = await objects_store.apply_writes(
            db, member, package_id=package.id, writes=[write])
    except objects_store.BatchError as exc:
        # The SAVEPOINT of the batch rolled back, so the session is usable.
        if not _set_write_error(out, exc.cause):
            raise
        return
    except Exception as exc:
        # A refusal before the batch, such as an id that is not a UUID.
        if not _set_write_error(out, exc):
            raise
        return
    done = answers[0]
    if op == "delete":
        out.deleted = str(body["id"])
    elif isinstance(done, objects_store.Record):
        out.record = done
        out.record_id = done.id


async def _run_object_write(db: Any, member: str, package: packages_store.Package,
                            ref: UsesRef, raw: bytes, out: _Outcome) -> None:
    """The write and its audit row, in the ONE session *db*. The audit row is
    written for each outcome that leaves the session usable. A failed audit
    raises, so ``tenant_session`` rolls the write back (B2c-1)."""
    await _do_object_write(db, member, package, ref, raw, out)
    await record_app_audit_in(db, app_id=package.id, user_email=member, kind="action",
                              detail=_audit_detail(out))
    out.audited = True


async def _run_executor(request: Request, user: UserContext, pid: str, action: str,
                        out: _Outcome, direct: bool, first: UsesRef | None) -> None:
    """Read the body, check again, then run the open executor. No session is
    open during the read, so a slow client holds no pool connection. The read
    can last as long as the client wants, so the first check proves nothing
    after it. The check and the executor share one session.

    A direct write takes its writer slot BEFORE the session (B2c-1), and the
    same session holds the write and its audit row."""
    member = str(user.email)
    raw = await _read_body(request)
    if raw is None:
        out.too_large = True
        return
    wants = direct and first is not None and (first.source, first.op) in DIRECT_EXECUTORS
    with _writer_slot(str(user.organization_id or ""), wants) as admitted:
        if wants and not admitted:
            # No session was opened, so no audit row can be written.
            out.fail, out.audited = _busy(), True
            return
        async with _tenant_session() as db:
            package, ref = await _check(db, member, pid, action, out, direct)
            if out.refusal is not None or ref is None:
                return
            if ref.source != "object":
                # The action named an object at the first check. The catalog gives
                # no class in this session, and no other source has an executor.
                out.refusal = SOURCE_NOT_OPEN
                return
            if (ref.source, ref.op) in DIRECT_EXECUTORS:
                if not (direct and admitted):
                    # The manifest changed during the read to name a write.
                    out.refusal = SOURCE_NOT_OPEN
                    return
                await _run_object_write(db, member, package, ref, raw, out)
                return
            run = _run_object_list if ref.op == "list" else _run_object_get
            await run(db, package, ref, raw, out)


def _is_direct(request: Request, user: UserContext) -> bool:
    """True when the call is a DIRECT call of a member (B2c-1).

    All of these hold: the proof of the BFF verifies for this member, this
    method and this path, the call is no outside agent, and the user is a
    member with an address. Anything else is False, and the call gets today's
    ``approval_required``. The proof is read once, before the body."""
    email = str(user.email or "")
    if user.agent is not None or user.is_agent or "@" not in email \
            or email.strip().lower().startswith("system:"):
        return False
    return bool(_direct_proof.is_direct_proof(
        _direct_proof.header_value(request.headers), email, request.method, request.url.path))


def _query_refusal(exc: studio_query.QueryError) -> HTTPException:
    """The answer to a refused query. A timeout is 503, because the same call
    can work later. Each other code is 422."""
    if exc.code == studio_query.TIMEOUT:
        return HTTPException(status_code=503, detail={"code": exc.code, "message": exc.message},
                             headers={"Retry-After": RETRY_AFTER})
    return HTTPException(status_code=422, detail={
        "code": exc.code, "message": exc.message, "field": exc.field})


def _audit_detail(out: _Outcome) -> dict[str, Any]:
    """The audit row of *out*: the action, its class and the outcome. It holds
    no record data and no value of the body, and each text is capped."""
    reason: str | None
    if out.catalog_down:
        outcome, reason = "failed", CATALOG_UNAVAILABLE
    elif out.refusal is not None:
        outcome, reason = "refused", out.refusal
    elif out.too_large:
        outcome, reason = "refused", BODY_TOO_LARGE
    elif out.fail is not None:
        outcome, reason = out.fail.outcome, out.fail.reason
    elif out.query_error is not None:
        timed_out = out.query_error.code == studio_query.TIMEOUT
        outcome, reason = "failed" if timed_out else "refused", out.query_error.code
    elif out.invalid:
        outcome, reason = "refused", "bad_input"
    elif out.object_missing:
        outcome, reason = "not_found", OBJECT_MISSING
    elif out.missing:
        outcome, reason = "not_found", None
    else:
        outcome, reason = "ran", None
    detail: dict[str, Any] = {"action": _cap(out.action), "uses": _cap(out.uses),
                              "class": _cap(out.cls), "outcome": outcome}
    if reason is not None:
        detail["reason"] = reason
    if out.record_id is not None:
        detail["record_id"] = out.record_id
    return detail


def _answer(out: _Outcome) -> dict[str, Any]:
    """The answer of the call *out*, or the refusal that it raises."""
    if out.catalog_down:
        raise HTTPException(status_code=503, detail={
            "code": CATALOG_UNAVAILABLE, "message": CATALOG_DOWN})
    if out.refusal is not None:
        raise HTTPException(status_code=403, detail={
            "code": out.refusal, "message": REFUSALS[out.refusal]})
    if out.too_large:
        raise HTTPException(status_code=413, detail={
            "code": BODY_TOO_LARGE, "message": BODY_OVER_CAP})
    if out.fail is not None:
        raise out.fail.error
    if out.query_error is not None:
        raise _query_refusal(out.query_error)
    if out.invalid:
        raise HTTPException(status_code=422, detail=BAD_RECORD_ID)
    if out.object_missing:
        raise HTTPException(status_code=404, detail={
            "code": OBJECT_MISSING, "message": NO_OBJECT})
    if out.page is not None:
        return dataclasses.asdict(out.page)
    if out.deleted is not None:
        return {"deleted": True, "id": out.deleted}
    if out.missing or out.record is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    return {"record": dataclasses.asdict(out.record)}


@router.post("/apps/{app_id}/actions/{action}")
async def invoke_action(
    app_id: str,
    action: str,
    request: Request,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Run the action *action* of the caller's package *app_id*. See the
    module docstring for the order of the checks."""
    if not flags.studio_allowed(user.organization_id):
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    if not _ACTION_ID.fullmatch(action):
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    pid = _app_id(app_id)
    out = _Outcome(action=action)
    # The proof is read once, here, before any body read (B2c-1).
    direct = _is_direct(request, user)
    async with _tenant_session() as db:
        package, ref = await _check(db, user.email, pid, action, out, direct)
    if ref is not None and out.uses is not None and ref.source != "object":
        try:
            out.cls = await _catalog_class(out.uses)
        except Exception as exc:
            _log.warning("studio.invoke.catalog_failed", error=type(exc).__name__)
            out.catalog_down = True
        else:
            out.refusal = refusal_for(ref, out.cls)
    elif ref is not None and out.refusal is None:
        await _run_executor(request, user, pid, action, out, direct, ref)
    if not out.audited:
        await record_app_audit_bound(app_id=package.id, user_email=user.email, kind="action",
                                     detail=_audit_detail(out))
    return _answer(out)
