"""The action door of AI Studio (STU-B2a and STU-B2b-1, ``ai_studio.md`` §3.2).

``POST /studio/apps/{app_id}/actions/{action}`` runs one action that a Studio
package declares. The route registers on the router of ``actions.py``, as
``skills.py`` does, so ``main.py`` mounts no second router. The gate
``require_feature_router("workflows")`` and the prefix ``/studio`` hold here.

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
6. The approval. Class B and class C get 403 ``approval_required``. No card
   exists yet (B2c), so nothing runs.
7. The body. Only a call that an open executor runs reads the body, so a
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
   (B2a) and ``object:<name>.list`` (B2b-1). Each one parses the body here,
   and only here. An object name that the package does not hold gets 404
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
class and the outcome, and no record data. Each text in it has at most
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

import dataclasses
import json
import re
import uuid
from typing import Any

from acb_auth import UserContext, get_current_user
from acb_common import get_logger, studio_query
from acb_common.app_manifests.base import OP_PATTERN
from acb_common.studio_manifest import OBJECT_OP_CLASSES, UsesRef, parse_uses
from fastapi import Depends, HTTPException, Request
from gateway.db import tenant_session as _tenant_session
from gateway.routes.apps._common import record_app_audit_bound
from gateway.routes.studio import actions, flags
from gateway.routes.studio.actions import NOT_FOUND, router
from gateway.studio import objects_store, packages_store

__all__ = ["LIST_INPUT_KEYS", "MAX_AUDIT_TEXT", "MAX_BODY_BYTES", "OPEN_EXECUTORS", "REFUSALS",
           "router"]

_log = get_logger("gateway.studio.invoke")

#: The executors that the door opens, as (source, op): the object ``get`` of
#: B2a and the object ``list`` of B2b-1.
OPEN_EXECUTORS: frozenset[tuple[str, str | None]] = frozenset(
    {("object", "get"), ("object", "list")})

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
    SOURCE_NOT_OPEN: "This door runs only the get and list actions of an object for now.",
}

#: The 503 of a catalog that fails, and the 404 of an object that the package
#: does not hold. Each is one sentence that a model can act on.
CATALOG_DOWN = "The action catalog is not available now, so try the action again later."
NO_OBJECT = "This package holds no object for this action yet, so define the object first."

BODY_OVER_CAP = "This body is too large for this door, so send a smaller body."

BAD_RECORD_ID = "Send the record id as a UUID in the key id of a JSON object."
BAD_LIST_INPUT = (
    "Send a JSON object with only filter, sort, limit and offset, "
    "and whole numbers for limit and offset.")

#: The header value of a 503 ``query_timeout``, in seconds.
RETRY_AFTER = "2"


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


def refusal_for(ref: UsesRef, cls: str | None) -> str | None:
    """The refusal code of an action of *ref* and class *cls*, or None when
    an open executor runs it. *cls* is None when no source gives the action a
    class."""
    if cls is None:
        return NOT_IN_CATALOG
    if cls == "X":
        return CLASS_X
    if cls in ("B", "C"):
        return APPROVAL_REQUIRED
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


async def _check(db: Any, member: str, pid: str, action: str,
                 out: _Outcome) -> tuple[packages_store.Package, UsesRef | None]:
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
        out.refusal = refusal_for(ref, out.cls)
    return package, ref


async def _run_executor(request: Request, member: str, pid: str, action: str,
                        out: _Outcome) -> None:
    """Read the body, check again, then run the open executor. No session is
    open during the read, so a slow client holds no pool connection. The read
    can last as long as the client wants, so the first check proves nothing
    after it. The check and the executor share one session."""
    raw = await _read_body(request)
    if raw is None:
        out.too_large = True
        return
    async with _tenant_session() as db:
        package, ref = await _check(db, member, pid, action, out)
        if out.refusal is not None or ref is None:
            return
        if ref.source != "object":
            # The action named an object at the first check. The catalog gives
            # no class in this session, and no other source has an executor.
            out.refusal = SOURCE_NOT_OPEN
            return
        run = _run_object_list if ref.op == "list" else _run_object_get
        await run(db, package, ref, raw, out)


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
    if out.catalog_down:
        outcome, reason = "failed", CATALOG_UNAVAILABLE
    elif out.refusal is not None:
        outcome, reason = "refused", out.refusal
    elif out.too_large:
        outcome, reason = "refused", BODY_TOO_LARGE
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
    if out.query_error is not None:
        raise _query_refusal(out.query_error)
    if out.invalid:
        raise HTTPException(status_code=422, detail=BAD_RECORD_ID)
    if out.object_missing:
        raise HTTPException(status_code=404, detail={
            "code": OBJECT_MISSING, "message": NO_OBJECT})
    if out.page is not None:
        return dataclasses.asdict(out.page)
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
    async with _tenant_session() as db:
        package, ref = await _check(db, user.email, pid, action, out)
    if ref is not None and out.uses is not None and ref.source != "object":
        try:
            out.cls = await _catalog_class(out.uses)
        except Exception as exc:
            _log.warning("studio.invoke.catalog_failed", error=type(exc).__name__)
            out.catalog_down = True
        else:
            out.refusal = refusal_for(ref, out.cls)
    elif ref is not None and out.refusal is None:
        await _run_executor(request, user.email, pid, action, out)
    await record_app_audit_bound(app_id=package.id, user_email=user.email, kind="action",
                                 detail=_audit_detail(out))
    return _answer(out)
