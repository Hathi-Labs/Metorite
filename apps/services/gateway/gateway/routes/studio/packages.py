"""The package routes of AI Studio (STU-A5, ``ai_studio.md`` §14 A5).

Six routes over the package store (A3 and A4, ``studio/packages_store.py``)
and the object store (C1, ``studio/objects_store.py``). They register on the
router of ``actions.py``, as ``skills.py`` and ``invoke.py`` do, so
``main.py`` mounts no second router. The gate ``require_feature_router
("workflows")`` and the prefix ``/studio`` hold here too.

====================================  =========================================
``POST /packages``                    create a package from a manifest (201)
``GET /packages``                     the packages of the caller, no manifest
``GET /packages/{id}``                one package, with its manifest and objects
``PUT /packages/{id}/draft``          replace the draft manifest
``POST /packages/{id}/versions``      publish the draft, and project its objects
``GET /packages/{id}/versions``       the versions of a package, no manifest
====================================  =========================================

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

**Create.** There is no idempotency key. A second create with the same
manifest id by the same member answers 409 ``package_exists`` with the id of
the first. Two creates at the same moment can both pass, and that is
accepted. A member holds at most :data:`MAX_PACKAGES_PER_MEMBER` packages.
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
from fastapi import Depends, HTTPException, Request
from gateway.db import tenant_session as _tenant_session
from gateway.routes.apps._common import record_app_audit_bound
from gateway.routes.studio import actions, flags
from gateway.routes.studio.actions import NOT_FOUND, router
from gateway.studio import objects_store
from gateway.studio import packages_store as store

__all__ = ["MAX_BODY_BYTES", "MAX_PACKAGES_PER_MEMBER", "MAX_PROBLEMS", "VERSIONS_PAGE", "router"]

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
_DIGITS = re.compile(r"[0-9]{1,10}", re.ASCII)

PACKAGE_EXISTS = "package_exists"
PACKAGE_LIMIT = "package_limit"
OBJECTS_OUT_OF_SYNC = "objects_out_of_sync"
CATALOG_UNAVAILABLE = "catalog_unavailable"
STORE_BUSY = "store_busy"
BODY_TOO_LARGE = "body_too_large"

CATALOG_DOWN = "The action catalog is not available now, so try the publish again later."
BODY_OVER_CAP = "This body is too large, so send a smaller manifest."
NOT_JSON = "The body must be a JSON object."
TOO_DEEP = "The body nests too deep to read. Flatten it."
BAD_CURSOR = "The cursor before is a version number, so send a whole number from 1."


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
    except objects_store.StoreBusy as exc:
        raise HTTPException(
            status_code=503, detail={"code": STORE_BUSY, "message": str(exc)},
            headers={"Retry-After": RETRY_AFTER}) from None
    except RecursionError:
        raise _unprocessable(TOO_DEEP) from None


async def _read_body(request: Request) -> bytes | None:
    """The bytes of the body, or None past :data:`MAX_BODY_BYTES`. The read
    stops at the first chunk past the cap."""
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BODY_BYTES:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


async def _raw_body(request: Request) -> bytes:
    """The bytes of the body. No session is open during the read. A body past
    the cap is 413, before any read of the database."""
    raw = await _read_body(request)
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
            "to": f.to, "hot": f.hot} for f in obj.fields],
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
) -> dict[str, Any]:
    """Replace the draft manifest of a package of the caller. No object row
    changes: only a publish writes rows."""
    if not flags.studio_allowed(user.organization_id):
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    pid = _package_id(package_id)
    raw = await _raw_body(request)
    await _require_owned(user.email, pid)
    manifest = _parse_json(raw)
    async with _tenant_session() as db:
        with _store_errors():
            package = await store.update_manifest(db, user.email, pid, manifest)
        if package is None:
            raise _not_found()
    return {"package": _package_out(package)}


@router.post("/packages/{package_id}/versions", status_code=201)
async def publish(
    package_id: str,
    request: Request,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Publish the draft of a package of the caller as the next version, and
    bring the object rows to its manifest, in one transaction."""
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
    known = await _catalog()
    async with _tenant_session() as db:
        with _store_errors():
            version = await store.publish_version(db, user.email, pid, notes, catalog=known)
            if version is None or version.manifest is None:
                raise _not_found()
            result = await objects_store.sync_objects(
                db, user.email, package_id=pid, objects=version.manifest.get("objects") or [])
            if result is None:
                raise _not_found()
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
