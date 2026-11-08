"""The package routes of AI Studio (STU-A5, ``ai_studio.md`` §14 A5 and A5c-1).

Nine routes over the package store (A3 and A4, ``studio/packages_store.py``)
and the object store (C1, ``studio/objects_store.py``). They register on the
router of ``actions.py``, as ``skills.py`` and ``invoke.py`` do, so
``main.py`` mounts no second router. The gate ``require_feature_router
("studio")`` and the prefix ``/studio`` hold here too (J1).

=======================================  ======================================
``POST /packages``                       create a package from a manifest (201)
``GET /packages``                        the packages of the caller, no manifest
``GET /packages/{id}``                   one package, its manifest and objects
``PUT /packages/{id}/draft``             replace the draft manifest
``POST /packages/{id}/versions``         publish the draft, project its objects
``GET /packages/{id}/versions``          a page of 100 versions, no manifest
``GET /packages/{id}/versions/{n}``      one version, its manifest and hash
``GET /packages/{id}/delete-preview``    what a delete removes, and its token
``DELETE /packages/{id}``                delete the package (200 or 202)
``POST /packages/{id}/install``          an org or group install (201 or 200)
=======================================  ======================================

**The switch.** The first line of each handler reads ``STUDIO_ORGS``. An org
outside it gets 404 ``Not found`` for each body, because the handler takes
the raw request and reads no byte before that line. Fence:
``test_each_handler_checks_the_switch_first``.

**Tenancy and the member (R5).** The org is the bound session
(``_tenant_session()``), and the member is ``user.email``. No route reads an
org, a member or a package from the body. A key such as ``owner_email`` or
``organization_id`` in a body changes nothing. A bad id, a package of another
member, a package of another org and an App Workshop app all answer 404
``Not found``. An answer names no owner.

**The order of the checks.** The switch, then the id. A body route on a
package then reads the raw body, then reads the package as the member, then
parses the body. The body read stops past :data:`MAX_BODY_BYTES` (413), and no
session is open during it, so a big body costs no read of the database. A 413
says nothing about the package, so a stranger can get it too. The parse comes
after the owner read, so a bad body cannot tell a stranger that the package
exists. The store checks the owner again under its own row lock.

**The rule: the manifest is the only source, and publish projects it into
rows.**

1. No route defines an object by hand. Only the draft manifest does.
2. A draft save writes no object row. Rows follow the last published version.
3. A publish runs ``publish_version`` and then ``objects_store.sync_objects``
   on the manifest of the NEW version (the immutable copy), in ONE
   ``tenant_session``. A refusal raises, the session rolls back, and no
   version row stays. After each publish, the rows equal the objects of the
   live version.
4. ``pages`` and the other parts of a manifest are not rows. A sync ignores
   them.

**One lock order.** The ``apps`` row ``FOR UPDATE`` (inside
``publish_version``), then the org advisory lock (inside ``sync_objects``).
``define_object`` takes them the other way, so ``sync_objects`` is its only
caller. Fences: ``test_define_object_has_one_caller`` and
``test_each_publish_or_rollback_is_followed_by_a_sync``. A5b adds ``rollback``
here and must call ``sync_objects`` after it.

**The install (WS-PKG PKG-8a).** While ``PACKAGE_HOST_ORGS`` holds the org, a
publish keeps one private install of the owner at the new version, after the
sync and in the same session (``package_host/studio_app.py``). The org lock of
the install store comes after the two locks above. A refusal of the install
store is 409 or 422 with its ``detail_for`` of the member, and it rolls the
publish back. With the switch off, a publish writes no install.

**The org and group install (WS-PKG PKG-8b, ``package_host.md`` §6.1).**
``POST /packages/{id}/install`` with the body ``{"scope": "org"}`` or
``{"scope": "group:<slug>"}``. The scope comes from the body, and the member
and the org come from the session. The order: the two switches
(``STUDIO_ORGS``, then ``PACKAGE_HOST_ORGS``, each 404 with no session), the
id, the raw body (at most :data:`MAX_INSTALL_BODY_BYTES`), the owner read
(404), the scope and the optional ``version`` (422), then the right (403).
Each scope needs ``studio:publish`` (D180.3: the verb gates the share to
others), and the check fails closed. A group install also needs a group of
that name in the bound org (``host_store.group_exists``, else 403
``unknown_group``). The caller need not be a member of the group. Then one
session locks the ``apps`` row ``FOR KEY SHARE`` (``store.lock_for_install``,
404 when it is gone) and reads the live version: 409 ``not_published`` when
there is none, and 409 ``version_changed`` when the body names another
version. It keeps the install at that version and writes one ``app_audit``
row (kind ``action``, op ``package.install``) in the same transaction. The
answer is 201 for a new install and 200 for a move. A store refusal is its
``detail_for`` of the member. A later publish does not move an org or group
install, so the holder approves a new version with a new call. Only the
owner reaches the route until J2.

**The answers.** 422 holds ``{message, errors: [{path, message}]}`` with at
most :data:`MAX_PROBLEMS` errors. 409 holds ``{code, message, ...}``:
``package_exists`` and ``package_limit`` at create, and ``objects_out_of_sync``
with ``problems`` at publish. 503 ``catalog_unavailable`` and ``store_busy``
(with ``Retry-After``) are the two waits that a retry can fix.

**The audit.** A publish writes its ``app_audit`` row inside the store
transaction (kind ``publish``). A create, and a sync that changed rows, write
a row through ``record_app_audit_bound`` (kind ``action``) after the session
closes. ⚠️ Never ``record_app_audit``: its unbound session writes no row under
FORCE RLS (task 1317). A draft save and a 404 write no row. The rows hold the
op, a hash, a size and names, and no manifest text.

**The expected hash (STU-G1c review round 1).** A draft save takes the query
``?based_on=<hash>``, and a publish takes ``based_on`` in its body. The value
is ``studio_manifest.manifest_hash`` of the draft that the caller read. The
store compares it under the row lock: a different hash is 409
``draft_changed``, and nothing is written. So a save that lands between the
read of a publish and its POST cannot go live in place of the draft that was
checked, and of two saves of one read only one writes. With no ``based_on``
a route behaves as before. A value that is not 64 hex digits is 422.

**Record actions on a draft save (STU-G3c).** A draft save adds the record
actions that an object lacks, by ``acb_skills.studio_tools.complete_record_actions``,
the one rule of ``studio_save`` (STU-BETA-2). So an object that a member adds in
the Studio IDE opens with its five actions. The answer adds ``record_actions``
with the ``added`` ids and the ``skipped`` object names (the app is too big).
The ``based_on`` check reads the stored draft, so the addition never moves it.

**Create.** There is no idempotency key. A second create with the same
manifest id by the same member answers 409 ``package_exists`` with the id of
the first. Two creates at the same moment can both pass, and that is
accepted. A member holds at most :data:`MAX_PACKAGES_PER_MEMBER` packages.

**The delete (A5c-1).** Two steps, with no state in the database. The owner
reads ``GET /packages/{id}/delete-preview``: each object with its field
count and its record count (up to 100,000), the versions, the installs, the
installs that reach another member, and the token ``confirm``. Then the
owner sends ``DELETE /packages/{id}`` with the body ``{"confirm": token}``
(at most :data:`MAX_DELETE_BODY_BYTES`). A missing or malformed token is
422. A token that is not the token of now is 409 ``counts_changed`` with the
new preview, and nothing is deleted. Only the owner reaches either route:
another member, another org and an App Workshop app get 404. No route here
has an admin path, and no body key changes the member.

Each step runs in its own ``tenant_session``: the ``apps`` row ``FOR
UPDATE``, the org lock, then a chunk of records (``store.delete_package``).
No lock stays between two steps, so the other writes of the org run. The
last step of a delete deletes the ``apps`` row and writes the audit row in
the store, in one transaction. The answer is 200 ``{deleted: true}``, and
then each route answers 404 for the id.

A call ends at :data:`DELETE_BUDGET_SECONDS`. Each purge stops at that end,
and a step goes on only while one more step fits. Else the step is the last
of the call. In its own transaction it writes a partial audit row (op
``package.delete.partial``, the records of this call) and builds the
preview of now. The answer is 202 ``{deleted: false, remaining, confirm}``,
and the caller sends the DELETE again with the new ``confirm``. Each call
that ends with records left logs ``studio.packages.delete_partial``.

A later step checks the object names of the preview that the member
confirmed. A name that a publish added between two steps ends the call with
409 ``counts_changed``, the new preview, ``records_removed`` and a text that
names that count. A ``store_busy`` on a later step ends the call with 202,
because the earlier steps hold, or with 503 when the preview is busy too. A
step waits at most 1 s for the ``apps`` row. When another call deleted the
package between two steps, the answer is 404. Each of these ends writes the
partial audit row of the earlier steps.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import re
import uuid
from collections.abc import Iterator
from typing import Any

from acb_auth import UserContext, get_current_user
from acb_common import get_logger
from acb_common.studio_manifest import manifest_hash
from acb_skills.studio_tools import complete_record_actions
from fastapi import Depends, HTTPException, Request, Response
from gateway.db import tenant_session as _tenant_session
from gateway.package_host import store as host_store
from gateway.package_host import studio_app
from gateway.package_host.flags import package_host_allowed
from gateway.routes.apps._common import record_app_audit_bound, record_app_audit_in
from gateway.routes.studio import actions, flags
from gateway.routes.studio.actions import NOT_FOUND, router
from gateway.studio import objects_store
from gateway.studio import packages_store as store

__all__ = ["COUNTS_CHANGED", "DELETE_BUDGET_SECONDS", "DRAFT_CHANGED", "MAX_BODY_BYTES",
           "MAX_DELETE_BODY_BYTES", "MAX_PACKAGES_PER_MEMBER", "MAX_PROBLEMS", "VERSIONS_PAGE",
           "router"]

_log = get_logger("gateway.studio.packages")

#: The most bytes of one body (300 KiB). A manifest holds 256 KiB at most.
MAX_BODY_BYTES = 300 * 1024
#: The most packages that one member holds in one org.
MAX_PACKAGES_PER_MEMBER = 100
#: The most errors or problems in one answer.
MAX_PROBLEMS = 25
#: The most names in the audit row of a sync.
MAX_AUDIT_NAMES = 50
#: The header value of a 503 ``store_busy``, in seconds.
RETRY_AFTER = "2"
#: The most versions in one page of the versions list.
VERSIONS_PAGE = store.MAX_VERSIONS_PAGE
#: The most bytes of the body of a delete (4 KiB). It holds the token only.
MAX_DELETE_BODY_BYTES = 4 * 1024
#: The longest run of the steps of one delete call, in seconds. Each purge
#: stops at the end of the budget, and a step goes on only while one more step
#: (``store.STEP_SECONDS``) fits. Past it, the last step builds the token of a
#: 202, so one count of ``objects_store.COUNT_TIMEOUT_MS`` can run after the
#: budget. The sum stays below the client timeout of the chat tool
#: (``acb_skills.studio_tools._send``). Fence:
#: ``test_the_worst_call_ends_before_the_chat_tool_gives_up``.
DELETE_BUDGET_SECONDS = 8.0
#: The most bytes of the body of an install (4 KiB). It holds the scope only.
MAX_INSTALL_BODY_BYTES = 4 * 1024
#: The right of an org or a group install: the share verb (D180.3,
#: ``package_host.md`` §6.1). It is the one read of the verb in
#: ``routes/studio``. Fence:
#: ``test_studio_route_gate.py::test_the_share_verb_is_read_by_the_install_only``.
SHARE_RIGHT = "studio:publish"
_DIGITS = re.compile(r"[0-9]{1,10}", re.ASCII)
_HASH = re.compile(r"[0-9a-f]{64}", re.ASCII)

PACKAGE_EXISTS = "package_exists"
PACKAGE_LIMIT = "package_limit"
OBJECTS_OUT_OF_SYNC = "objects_out_of_sync"
CATALOG_UNAVAILABLE = "catalog_unavailable"
STORE_BUSY = "store_busy"
BODY_TOO_LARGE = "body_too_large"
DRAFT_CHANGED = "draft_changed"
COUNTS_CHANGED = "counts_changed"
NOT_PUBLISHED = "not_published"
VERSION_CHANGED = "version_changed"
FORBIDDEN = "forbidden"
UNKNOWN_GROUP = "unknown_group"

CATALOG_DOWN = "The action catalog is not available now, so try the publish again later."
BODY_OVER_CAP = "This body is too large, so send a smaller manifest."
NOT_JSON = "The body must be a JSON object."
TOO_DEEP = "The body nests too deep to read. Flatten it."
BAD_CURSOR = "The cursor before is a version number, so send a whole number from 1."
BAD_VERSION = "The version is a whole number from 1, so send a version that the list names."
BAD_HASH = ("based_on is the hash of the draft that you read: 64 hexadecimal digits. Send it, "
            "or leave it out.")
BAD_CONFIRM = ("confirm is the token of the delete preview: 64 hexadecimal digits. Read the "
               "preview, then send its token.")
NO_SHARE_RIGHT = ("Only a holder of studio:publish installs an app for the organization or "
                  "for a group. Ask the owner of your organization for that right.")
NO_SUCH_GROUP = ("Your organization has no group with this name, so nothing was installed. "
                 "Check the name of the group, then install again.")
BAD_VERSION = ("version is the live version that you approve: a whole number from 1. Send it, "
               "or leave it out.")
LIVE_MOVED = ("The live version of this app changed after your read, so nothing was "
              "installed. Read the live version, then install it again.")
NOT_LIVE = ("This app has no published version, so nothing can be installed. Publish it "
            "first, then install it again.")


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail=NOT_FOUND)


def _unprocessable(message: str, errors: list[dict[str, str]] | None = None) -> HTTPException:
    return HTTPException(status_code=422, detail={"message": message, "errors": errors or []})


def _package_id(raw: str) -> str:
    """The id as the store reads it. A bad id names nothing, so it is 404."""
    try:
        return str(uuid.UUID(raw))
    except (ValueError, TypeError, AttributeError):
        raise _not_found() from None


def _based_on(raw: object, path: str = "$.based_on") -> str | None:
    """The expected hash of the stored draft, or None when the caller sends
    none. Any other value than 64 hex digits is 422, and the error names
    *path*: ``$.based_on`` in a body, ``?based_on`` in a query. It says
    nothing about the package."""
    if raw is None:
        return None
    if isinstance(raw, str) and _HASH.fullmatch(raw.strip().lower()):
        return raw.strip().lower()
    raise _unprocessable(BAD_HASH, [{"path": path, "message": BAD_HASH}])


def _cursor(raw: str | None) -> int | None:
    """The ``before`` cursor of the versions list: a version number from 1 to
    the most that the store holds, or None. Any other value is 422, before a
    read of the database. It says nothing about the package."""
    if raw is None:
        return None
    if _DIGITS.fullmatch(raw) and 1 <= int(raw) <= store.MAX_VERSION:
        return int(raw)
    raise _unprocessable(BAD_CURSOR, [{"path": "before", "message": BAD_CURSOR}])


@contextlib.contextmanager
def _store_errors() -> Iterator[None]:
    """The refusals of the two stores as HTTP answers. A refusal that raises
    inside a session block rolls the whole session back."""
    try:
        yield
    except store.PackageStoreError as exc:
        raise _unprocessable(
            str(exc),
            [{"path": e.path, "message": e.message} for e in exc.errors[:MAX_PROBLEMS]],
        ) from None
    except objects_store.ObjectsOutOfSync as exc:
        raise HTTPException(status_code=409, detail={
            "code": OBJECTS_OUT_OF_SYNC,
            "message": "The objects of the manifest do not fit the stored objects. "
            "Nothing was published.",
            "problems": [
                {"kind": p.kind, "object": p.object, "field": p.field, "rule": p.rule,
                 "message": p.message} for p in exc.problems[:MAX_PROBLEMS]],
        }) from None
    except store.DraftChanged as exc:
        raise HTTPException(status_code=409, detail={
            "code": DRAFT_CHANGED, "message": str(exc)}) from None
    except objects_store.StoreBusy as exc:
        raise HTTPException(
            status_code=503, detail={"code": STORE_BUSY, "message": str(exc)},
            headers={"Retry-After": RETRY_AFTER}) from None
    except RecursionError:
        raise _unprocessable(TOO_DEEP) from None


async def _read_body(request: Request, cap: int = MAX_BODY_BYTES) -> bytes | None:
    """The bytes of the body, or None past *cap*. The read stops at the
    first chunk past the cap."""
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > cap:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


async def _raw_body(request: Request, cap: int = MAX_BODY_BYTES) -> bytes:
    """The bytes of the body. No session is open during the read. A body past
    *cap* is 413, before any read of the database."""
    raw = await _read_body(request, cap)
    if raw is None:
        raise HTTPException(status_code=413, detail={
            "code": BODY_TOO_LARGE, "message": BODY_OVER_CAP})
    return raw


async def _json_body(request: Request, *, empty_ok: bool = False) -> dict[str, Any]:
    """The JSON object of the body, read by :func:`_raw_body`."""
    return _parse_json(await _raw_body(request), empty_ok=empty_ok)


def _parse_json(raw: bytes, *, empty_ok: bool = False) -> dict[str, Any]:
    """The JSON object of *raw*. Text that is not a JSON object is 422. An
    empty body is ``{}`` when *empty_ok*."""
    if not raw.strip() and empty_ok:
        return {}
    try:
        payload = json.loads(raw)
    except RecursionError:
        raise _unprocessable(TOO_DEEP) from None
    except ValueError:
        raise _unprocessable(NOT_JSON) from None
    if not isinstance(payload, dict):
        raise _unprocessable(NOT_JSON)
    return payload


async def _require_owned(member: str, pid: str) -> None:
    """404 unless *member* owns the package *pid* in the bound org. It opens
    and closes its own session, so the catalog read after it holds none."""
    async with _tenant_session() as db:
        if await store.get_package(db, member, pid) is None:
            raise _not_found()


async def _catalog() -> dict[str, str]:
    """The catalog as ``{id: class}``, for the publish check. It opens its own
    session, so the caller runs it BEFORE the store session. A failure is 503."""
    try:
        rows = await actions.catalog_rows()
    except Exception as exc:
        _log.warning("studio.packages.catalog_failed", error=type(exc).__name__)
        raise HTTPException(status_code=503, detail={
            "code": CATALOG_UNAVAILABLE, "message": CATALOG_DOWN}) from None
    return {str(r["id"]): str(r["class"]) for r in rows}


def _package_out(package: store.Package, *, manifest: bool = True) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": package.id, "slug": package.slug, "name": package.name,
        "description": package.description, "visibility": package.visibility,
        "status": package.status, "live_version": package.live_version,
        "created_at": package.created_at, "updated_at": package.updated_at,
    }
    if manifest:
        out["manifest"] = package.manifest
        # The one hash of the stored draft: the ``based_on`` of a save (STU-G3a). Added only.
        out["manifest_hash"] = manifest_hash(package.manifest)
    return out


def _summary_out(found: store.PackageSummary) -> dict[str, Any]:
    return {
        "id": found.id, "slug": found.slug, "name": found.name,
        "description": found.description, "status": found.status,
        "live_version": found.live_version, "manifest_id": found.manifest_id,
        "created_at": found.created_at, "updated_at": found.updated_at,
    }


def _object_out(obj: objects_store.StudioObject) -> dict[str, Any]:
    return {
        "id": obj.id, "name": obj.name, "label": obj.label,
        "fields": [{
            "key": f.key, "type": f.type, "label": f.label, "required": f.required,
            "many": f.many, "options": None if f.options is None else list(f.options),
            "to": f.to, "hot": f.hot,
            "min": f.min, "max": f.max,
            "units": None if f.units is None else list(f.units),
            "format": f.format} for f in obj.fields],
        "rules": [r.stored() for r in obj.constraints],
        "created_at": obj.created_at, "updated_at": obj.updated_at,
    }


def _version_out(version: store.PackageVersion) -> dict[str, Any]:
    return {
        "version": version.version, "sha256": version.bundle_sha256,
        "notes": version.release_notes, "published_by": version.published_by,
        "published_at": version.published_at, "review_status": version.review_status,
    }


def _sync_out(result: objects_store.SyncResult) -> dict[str, Any]:
    return {
        "objects_created": list(result.objects_created),
        "fields_added": [list(pair) for pair in result.fields_added],
        "hot_changed": [list(row) for row in result.hot_changed],
        "rules_set": list(result.rules_set),
    }


async def _keep_install(db: Any, user: UserContext, pid: str,
                        version: store.PackageVersion) -> None:
    """PKG-8a: the private install of the owner follows the publish, in its
    session. With ``PACKAGE_HOST_ORGS`` off for the org, it writes nothing.
    A refusal of the install store is its ``detail_for`` of the member and
    nothing else, and the raise rolls the publish back."""
    if not package_host_allowed(user.organization_id) or version.manifest is None:
        return
    try:
        await studio_app.keep_private_install(
            db, app_id=pid, version=version.version, owner=user.email,
            manifest=version.manifest)
    except host_store.InstallError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail_for(user.email)) from None


def _sync_audit(version: int, result: objects_store.SyncResult) -> dict[str, Any]:
    """The audit detail of a sync that changed rows: names and counts, no value."""
    return {
        "op": "package.objects_sync", "version": version,
        "objects": list(result.objects_created)[:MAX_AUDIT_NAMES],
        "fields": [f"{o}.{k}" for o, k in result.fields_added][:MAX_AUDIT_NAMES],
        "hot_changed": len(result.hot_changed),
        "rules": list(result.rules_set)[:MAX_AUDIT_NAMES],
    }


@router.post("/packages", status_code=201)
async def create_package(
    request: Request,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """A new private draft package of the caller, from the manifest in the body."""
    if not flags.studio_allowed(user.organization_id):
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    manifest = await _json_body(request)
    with _store_errors():
        encoded = store.check_manifest(manifest)
    async with _tenant_session() as db:
        with _store_errors():
            held = await store.list_summaries(db, user.email)
            for found in held:
                if found.manifest_id == manifest["id"]:
                    raise HTTPException(status_code=409, detail={
                        "code": PACKAGE_EXISTS, "package_id": found.id,
                        "message": "You have a package with this manifest id. "
                        "Save to it with PUT, or choose a new id."})
            if len(held) >= MAX_PACKAGES_PER_MEMBER:
                raise HTTPException(status_code=409, detail={
                    "code": PACKAGE_LIMIT,
                    "message": f"You hold {MAX_PACKAGES_PER_MEMBER} packages, the most "
                    "that one member holds."})
            package = await store.create_package(db, user.email, manifest)
    await record_app_audit_bound(
        app_id=package.id, user_email=user.email, kind="action",
        detail={"op": "package.create",
                "sha256": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
                "bytes": len(encoded.encode("utf-8"))})
    return {"package": _package_out(package)}


@router.get("/packages")
async def list_packages(user: UserContext = Depends(get_current_user)) -> dict[str, Any]:
    """The packages of the caller in the bound org, newest change first. The
    answer holds no manifest."""
    if not flags.studio_allowed(user.organization_id):
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    async with _tenant_session() as db:
        found = await store.list_summaries(db, user.email)
    return {"packages": [_summary_out(f) for f in found]}


@router.get("/packages/{package_id}")
async def get_package(
    package_id: str,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """One package of the caller, with its draft manifest and its object rows."""
    if not flags.studio_allowed(user.organization_id):
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    pid = _package_id(package_id)
    async with _tenant_session() as db:
        package = await store.get_package(db, user.email, pid)
        if package is None:
            raise _not_found()
        objects = await objects_store.list_objects(db, pid)
    return {"package": _package_out(package), "objects": [_object_out(o) for o in objects]}


@router.put("/packages/{package_id}/draft")
async def save_draft(
    package_id: str,
    request: Request,
    user: UserContext = Depends(get_current_user),
    based_on: str | None = None,
) -> dict[str, Any]:
    """Replace the draft manifest of a package of the caller. No object row
    changes: only a publish writes rows. With ``?based_on=``, the stored draft
    must still have that hash, or the answer is 409 ``draft_changed``."""
    if not flags.studio_allowed(user.organization_id):
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    pid = _package_id(package_id)
    raw = await _raw_body(request)
    await _require_owned(user.email, pid)
    manifest = _parse_json(raw)
    expected = _based_on(based_on, "?based_on")
    # The record actions of each object (STU-BETA-2), by the one rule of ``studio_save``. A member
    # who adds an object in the IDE gets the same five actions as Mira's save gives, so the page
    # opens. The hash check reads the STORED draft, so this change never moves it.
    manifest, added, skipped = complete_record_actions(manifest)
    async with _tenant_session() as db:
        with _store_errors():
            package = await store.update_manifest(db, user.email, pid, manifest,
                                                  expected_hash=expected)
        if package is None:
            raise _not_found()
    return {"package": _package_out(package),
            "record_actions": {"added": [a["id"] for a in added], "skipped": skipped}}


@router.post("/packages/{package_id}/versions", status_code=201)
async def publish(
    package_id: str,
    request: Request,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Publish the draft of a package of the caller as the next version, and
    bring the object rows to its manifest, in one transaction. With
    ``based_on`` in the body, the stored draft must still have that hash, or
    the answer is 409 ``draft_changed`` and nothing is written."""
    if not flags.studio_allowed(user.organization_id):
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    pid = _package_id(package_id)
    raw = await _raw_body(request)
    await _require_owned(user.email, pid)
    body = _parse_json(raw, empty_ok=True)
    notes = body.get("notes", "")
    if not isinstance(notes, str):
        raise _unprocessable("The release notes are text.", [
            {"path": "$.notes", "message": "The release notes are text."}])
    expected = _based_on(body.get("based_on"))
    known = await _catalog()
    async with _tenant_session() as db:
        with _store_errors():
            version = await store.publish_version(db, user.email, pid, notes, catalog=known,
                                                  expected_hash=expected)
            if version is None or version.manifest is None:
                raise _not_found()
            result = await objects_store.sync_objects(
                db, user.email, package_id=pid, objects=version.manifest.get("objects") or [])
            if result is None:
                raise _not_found()
            await _keep_install(db, user, pid, version)
            rows = await objects_store.list_objects(db, pid)
    if result.changed:
        await record_app_audit_bound(
            app_id=pid, user_email=user.email, kind="action",
            app_version=version.version, detail=_sync_audit(version.version, result))
    return {"version": _version_out(version), "objects": [_object_out(o) for o in rows],
            "sync": _sync_out(result)}


@router.get("/packages/{package_id}/versions")
async def list_versions(
    package_id: str,
    user: UserContext = Depends(get_current_user),
    before: str | None = None,
) -> dict[str, Any]:
    """One page of the versions of a package of the caller, newest first, with
    no manifest: at most :data:`VERSIONS_PAGE`. The cursor ``before`` is a
    version number, and the page holds the versions older than it.
    ``next_before`` is the cursor of the next page, or None on the last page."""
    if not flags.studio_allowed(user.organization_id):
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    pid = _package_id(package_id)
    cursor = _cursor(before)
    async with _tenant_session() as db:
        if await store.get_package(db, user.email, pid) is None:
            raise _not_found()
        found = await store.list_versions(db, user.email, pid, limit=VERSIONS_PAGE,
                                          before=cursor)
    # Version 1 is the oldest, so a full page that ends at it has no next page.
    more = len(found) == VERSIONS_PAGE and found[-1].version > 1
    return {"versions": [_version_out(v) for v in found],
            "next_before": found[-1].version if more else None}


@router.get("/packages/{package_id}/versions/{version}")
async def get_version(
    package_id: str,
    version: str,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """One version of a package of the caller, with its manifest and the
    ``manifest_hash`` of that manifest (STU-G5). The Studio IDE reads the live
    version with it, to show what a publish changes. It writes nothing. A
    package of another member or org, and a version that does not exist, answer
    404. A version that is not a whole number from 1 is 422."""
    if not flags.studio_allowed(user.organization_id):
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    pid = _package_id(package_id)
    if not (_DIGITS.fullmatch(version) and 1 <= int(version) <= store.MAX_VERSION):
        raise _unprocessable(BAD_VERSION, [{"path": "version", "message": BAD_VERSION}])
    async with _tenant_session() as db:
        if await store.get_package(db, user.email, pid) is None:
            raise _not_found()
        found = await store.get_version(db, user.email, pid, int(version))
    if found is None or found.manifest is None:
        raise _not_found()
    return {"version": {**_version_out(found), "manifest": found.manifest},
            "manifest_hash": manifest_hash(found.manifest)}


# ── The delete (A5c-1) ─────────────────────────────────────────────────────


def _confirm(body: dict[str, Any]) -> str:
    """The token of the body. A missing token, or one that is not 64 hex
    digits, is 422. It says nothing about the package."""
    raw = body.get("confirm")
    if isinstance(raw, str) and _HASH.fullmatch(raw.strip().lower()):
        return raw.strip().lower()
    raise _unprocessable(BAD_CONFIRM, [{"path": "$.confirm", "message": BAD_CONFIRM}])


def _preview_out(found: store.DeletePreview) -> dict[str, Any]:
    """The preview as the answer holds it: names and counts, no manifest text."""
    return {
        "package": {"id": found.id, "name": found.name, "slug": found.slug},
        "objects": [{"name": o.name, "fields": o.fields, "records": o.records,
                     "capped": o.capped} for o in found.objects],
        "totals": {"objects": len(found.objects), "fields": found.fields,
                   "records": found.records, "records_capped": found.capped,
                   "versions": found.versions, "installs": found.installs,
                   "other_member_installs": found.other_member_installs,
                   "own_entries": found.own_entries},
        "confirm": found.confirm,
    }


def _busy(exc: Exception) -> HTTPException:
    return HTTPException(status_code=503, detail={"code": STORE_BUSY, "message": str(exc)},
                         headers={"Retry-After": RETRY_AFTER})


@router.get("/packages/{package_id}/delete-preview")
async def delete_preview(
    package_id: str,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """What a delete of a package of the caller removes, and the token
    ``confirm`` that the DELETE must send. A read: it changes nothing."""
    if not flags.studio_allowed(user.organization_id):
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    pid = _package_id(package_id)
    async with _tenant_session() as db:
        with _store_errors():
            found = await store.delete_preview(db, user.email, pid)
        if found is None:
            raise _not_found()
    return _preview_out(found)


@router.delete("/packages/{package_id}")
async def delete_package(
    package_id: str,
    request: Request,
    response: Response,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Delete a package of the caller, with the token of its delete preview.

    Each step is its own transaction, and no lock stays between two steps.
    The first step checks the token under the locks. 200 when the package is
    gone, 202 with what is left and a new token past the budget."""
    if not flags.studio_allowed(user.organization_id):
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    pid = _package_id(package_id)
    raw = await _raw_body(request, MAX_DELETE_BODY_BYTES)
    await _require_owned(user.email, pid)
    token = _confirm(_parse_json(raw, empty_ok=True))
    end_at = objects_store._clock() + DELETE_BUDGET_SECONDS
    removed = 0
    names: tuple[str, ...] | None = None
    while True:
        try:
            async with _tenant_session() as db:
                step = await store.delete_package(
                    db, user.email, pid, confirm=token if names is None else None,
                    names=names, removed=removed, end_at=end_at)
        except store.CountsChanged as exc:
            # Only the first step raises it, so the call removed nothing.
            raise HTTPException(status_code=409, detail={
                "code": COUNTS_CHANGED, "message": str(exc), "records_removed": 0,
                "preview": _preview_out(exc.preview)}) from None
        except objects_store.StoreBusy as exc:
            if names is None:
                # The first step: the call removed nothing yet.
                raise _busy(exc) from None
            # A later step waited too long for the apps row, and that wait
            # ended its transaction. The earlier steps of the call hold, so a
            # new session writes their partial audit row.
            async with _tenant_session() as db:
                await store.record_partial(db, user.email, pid, removed, "busy")
            _log.info("studio.packages.delete_partial", package_id=pid, records=removed,
                      changed=False, busy=True)
            raise _busy(exc) from None
        if step is None:
            raise _not_found()
        removed += step.removed
        if step.deleted:
            _log.info("studio.packages.deleted", package_id=pid, records=removed)
            return {"deleted": True, "package_id": pid, "records_removed": removed}
        names = step.names
        if step.left is None and not step.busy and not step.gone:
            continue
        # The call ends with records left, or another call deleted the
        # package. The last step wrote the partial audit row.
        _log.info("studio.packages.delete_partial", package_id=pid, records=removed,
                  changed=step.changed, busy=step.busy)
        if step.gone:
            raise _not_found()
        if step.left is None:
            raise _busy(objects_store.StoreBusy(objects_store._BUSY_MESSAGE))
        if step.changed:
            raise HTTPException(status_code=409, detail={
                "code": COUNTS_CHANGED,
                "message": store.COUNTS_CHANGED_LATER_TEXT.format(removed=removed),
                "records_removed": removed, "preview": _preview_out(step.left)})
        response.status_code = 202
        return {"deleted": False, "package_id": pid, "remaining": step.left.records,
                "remaining_capped": step.left.capped, "records_removed": removed,
                "confirm": step.left.confirm}


# ── The org and group install (WS-PKG PKG-8b) ──────────────────────────────


def _install_out(found: host_store.Install) -> dict[str, Any]:
    """The install as the answer holds it. It names no member: the scope of
    an org or a group install is ``org`` or ``group:<slug>``."""
    return {"id": found.id, "app_id": found.app_id, "version": found.version,
            "scope_kind": found.scope_kind, "scope_subject": found.scope_subject}


def _forbidden(message: str) -> HTTPException:
    return HTTPException(status_code=403, detail={"code": FORBIDDEN, "message": message})


def _refused(user: UserContext, exc: host_store.InstallError) -> HTTPException:
    """A refusal of the install store: its ``detail_for`` of the member, and
    nothing else. It never names the private install of another member."""
    return HTTPException(status_code=exc.status, detail=exc.detail_for(user.email or ""))


def _approved(raw: object) -> int | None:
    """The version that the caller approves, or None when the body names
    none. Any other value than a whole number from 1 is 422. It says nothing
    about the package."""
    if raw is None:
        return None
    if isinstance(raw, int) and not isinstance(raw, bool) and 1 <= raw <= store.MAX_VERSION:
        return raw
    raise _unprocessable(BAD_VERSION, [{"path": "$.version", "message": BAD_VERSION}])


@router.post("/packages/{package_id}/install", status_code=201)
async def install_package(
    package_id: str,
    request: Request,
    response: Response,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Install the live version of a package of the caller for the whole org
    or for one group (``package_host.md`` §6.1, PKG-8b).

    The body is ``{"scope": "org"}`` or ``{"scope": "group:<slug>"}``, with an
    optional ``version``. Each scope needs ``studio:publish`` (D180.3), and a
    group must exist in the org. 201 for a new install, 200 when the call
    moved the install at that scope to the live version."""
    if not flags.studio_allowed(user.organization_id):
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    if not package_host_allowed(user.organization_id):
        raise _not_found()
    pid = _package_id(package_id)
    raw = await _raw_body(request, MAX_INSTALL_BODY_BYTES)
    await _require_owned(user.email, pid)
    body = _parse_json(raw)
    try:
        kind, subject = studio_app.install_scope(body.get("scope"))
    except host_store.InstallError as exc:
        raise _refused(user, exc) from None
    approved = _approved(body.get("version"))
    # D180.3: the share verb gates each install for other people. Fail closed.
    if not user.has_permission(SHARE_RIGHT):
        raise _forbidden(NO_SHARE_RIGHT)
    async with _tenant_session() as db:
        if kind == "group" and not await host_store.group_exists(db, group=subject):
            raise HTTPException(status_code=403, detail={
                "code": UNKNOWN_GROUP, "message": NO_SUCH_GROUP})
        with _store_errors():
            # The apps row first, then the org lock of the install store: the
            # lock order of a publish (``package_host.md`` §5.5).
            target = await store.lock_for_install(db, user.email, pid)
            if target is None:
                raise _not_found()
            if target.live_version is None:
                raise HTTPException(status_code=409, detail={
                    "code": NOT_PUBLISHED, "message": NOT_LIVE})
            if approved is not None and approved != target.live_version:
                raise HTTPException(status_code=409, detail={
                    "code": VERSION_CHANGED, "message": LIVE_MOVED,
                    "live_version": target.live_version})
            version = await store.get_version(db, user.email, pid, target.live_version)
            if version is None or version.manifest is None:
                raise _not_found()
        try:
            kept, made = await studio_app.keep_scope_install(
                db, app_id=pid, version=version.version, scope_kind=kind,
                scope_subject=subject, member=user.email, manifest=version.manifest)
        except host_store.InstallError as exc:
            raise _refused(user, exc) from None
        # The audit row commits with the install, or neither does.
        await record_app_audit_in(
            db, app_id=pid, user_email=user.email, kind="action", app_version=kept.version,
            detail={"op": "package.install", "scope_kind": kind, "scope": subject,
                    "version": kept.version, "created": made})
    if not made:
        response.status_code = 200
    _log.info("studio.packages.installed", package_id=pid, scope_kind=kind,
              version=kept.version, created=made)
    return {"install": _install_out(kept), "created": made}
