"""The serve route of a file of a record of a Studio app (WS-STU D13b).

Spec: ``project-docs/specs/ai_studio.md`` §14 D13b. One route on the router of
``actions.py``, so ``main.py`` mounts no second router and the gate
``require_feature_router("studio")`` holds (J1)::

    GET /studio/packages/{pid}/objects/{name}/records/{rid}/files/{fid}

It runs no SQL. ``studio/files_store.read_file`` reads the bytes.

**The order of the checks** is the door order of ``invoke._check``:

1. The switch ``STUDIO_ORGS`` (404, no session), and the ids and the object
   name (404). A bad UUID names nothing.
2. The owned package, through the shared resolver ``studio/reach.py``. The
   owner of a paused package gets 423, as at the door.
3. The install, behind ``PACKAGE_HOST_ORGS``, when the member owns no such
   package. A viewer takes a slot of ``invoke._reader_slot`` (503
   ``store_busy`` past the cap). ⚠️ The door takes the slot BEFORE it opens a
   session. This route cannot: it knows that the member is a viewer only after
   ``reach_package``, which reads. So the session and two reads run first, and
   the slot bounds the rest (the record read and the bytes). The cap still
   bounds the heavy work, but not the two reads.
4. The object of the package. A viewer needs an installed version that
   declares the object AND declares an action with ``uses`` of
   ``object:<name>.get``. The I1a gate (``invoke._needs_connection``, behind
   ``STUDIO_CONNECTION_GATE_ORGS``) runs here with that action id, and a
   refusal is 404. The type of a field of a viewer is the type of the
   installed version, not of the draft.
5. ``objects_store.get_record`` with the viewer, so the row rules of C8 bind.
6. For a viewer, ``project_record``: the keys of the installed version only.
7. The visible value of a ``file`` field of the object must hold
   ``{"id": <fid>}``. A key that the roles hide, a key of a newer version,
   and an id that another record holds are not in the visible record, so
   they fail here.
8. ``files_store.read_file``, bound to the package, the object, the record,
   the field and the file.

**EVERY other failure is 404, with one body.** Another org, no row access, an
uninstalled or paused install, a deleted record, a hidden key and a bad id
cannot be told apart. The two other answers are the 423 of the owner (the
owner knows the package) and the 503 of a viewer past the reader cap.

**The answer.** The bytes, with the type of the row. ``Content-Disposition``
is ``inline`` for ``application/pdf`` only, and ``attachment`` for each other
type. ``X-Content-Type-Options: nosniff``, and ``Cache-Control: private,
no-store`` because the answer is for one member.

The org and the member come from the session, never from the path or a body.
"""

from __future__ import annotations

import re
import uuid
from typing import Any
from urllib.parse import quote

from acb_auth import UserContext, get_current_user
from acb_common.studio_access import Viewer
from acb_common.studio_fields import NAME_PATTERN
from acb_common.studio_manifest import parse_uses
from fastapi import Depends, HTTPException
from fastapi.responses import Response
from gateway.db import tenant_session as _tenant_session
from gateway.routes.studio import flags
from gateway.routes.studio.actions import NOT_FOUND, router
from gateway.routes.studio.invoke import (
    BUSY_RETRY_AFTER,
    PACKAGE_PAUSED,
    PAUSED_STATUS,
    PAUSED_TEXT,
    READ_BUSY_MESSAGE,
    STORE_BUSY,
    _needs_connection,
    _reader_slot,
)
from gateway.studio import files_store, objects_store
from gateway.studio.reach import reach_package

__all__ = ["router"]

_NAME = re.compile(NAME_PATTERN, re.ASCII)
_ASCII_NAME = re.compile(r"[^A-Za-z0-9._-]")
#: The one type that a browser shows in the page. Each other type downloads.
INLINE_MIME = "application/pdf"


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail=NOT_FOUND)


def _uuid(raw: str) -> str:
    try:
        return str(uuid.UUID(raw))
    except (ValueError, TypeError, AttributeError):
        raise _not_found() from None


def _get_action(manifest: dict[str, Any], object_name: str) -> str | None:
    """The id of the first action with ``uses`` of ``object:<object_name>.get``,
    or None. A viewer needs it, as the door needs it for a ``get`` of the
    object. The id feeds the I1a gate."""
    actions = manifest.get("actions")
    if not isinstance(actions, list):
        return None
    for entry in actions:
        ref = parse_uses(entry.get("uses")) if isinstance(entry, dict) else None
        if ref is not None and ref.source == "object" and ref.object == object_name \
                and ref.op == "get" and isinstance(entry.get("id"), str):
            return str(entry["id"])
    return None


def _declares_get(manifest: dict[str, Any], object_name: str) -> bool:
    """True when the manifest declares a ``get`` action of the object."""
    return _get_action(manifest, object_name) is not None


def _holds_file(value: Any, file_id: str) -> bool:
    """True when the visible value of a ``file`` field names *file_id*. The
    value is ``{"id": ...}``, or a list of such objects for a ``many`` field."""
    items = value if isinstance(value, list) else [value]
    return any(isinstance(item, dict) and item.get("id") == file_id for item in items)


def disposition(mime: str, name: str) -> str:
    """The ``Content-Disposition`` value: ``inline`` for a PDF, else
    ``attachment``. The name goes as an ASCII fallback and as ``filename*``."""
    kind = "inline" if mime == INLINE_MIME else "attachment"
    plain = _ASCII_NAME.sub("_", name)[:100] or "file"
    return f"{kind}; filename=\"{plain}\"; filename*=UTF-8''{quote(name, safe='')}"


@router.get("/packages/{package_id}/objects/{object_name}/records/{record_id}/files/{file_id}")
async def get_record_file(
    package_id: str,
    object_name: str,
    record_id: str,
    file_id: str,
    user: UserContext = Depends(get_current_user),
) -> Response:
    """The bytes of the file *file_id* of the record *record_id*, for a member
    who reads the record and the file field."""
    org = user.organization_id
    if not flags.studio_allowed(org):
        raise _not_found()
    pid, rid, fid = _uuid(package_id), _uuid(record_id), _uuid(file_id)
    if not _NAME.fullmatch(object_name or ""):
        raise _not_found()
    async with _tenant_session() as db:
        reached = await reach_package(db, user.email, org, pid)
        if reached is None:
            raise _not_found()
        if reached.paused:
            raise HTTPException(status_code=PAUSED_STATUS, detail={
                "code": PACKAGE_PAUSED, "message": PAUSED_TEXT})
        package = reached.package
        with _reader_slot(str(org or ""), reached.app is not None) as admitted:
            if reached.app is not None and not admitted:
                raise HTTPException(
                    status_code=503, detail={"code": STORE_BUSY, "message": READ_BUSY_MESSAGE},
                    headers={"Retry-After": BUSY_RETRY_AFTER})
            found = await _read(db, user, package, reached.app is not None,
                                object_name, pid, rid, fid)
    if found is None:
        raise _not_found()
    return Response(content=found.content, media_type=found.mime, headers={
        "Content-Disposition": disposition(found.mime, found.name),
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "private, no-store",
    })


async def _read(db: Any, user: UserContext, package: Any, is_viewer: bool,
                object_name: str, pid: str, rid: str, fid: str) -> files_store.RecordFile | None:
    """Steps 4 to 8 of the order above. None means 404. A busy store raises 503."""
    action = _get_action(package.manifest, object_name)
    if is_viewer and (object_name not in package.object_names or action is None):
        return None
    # I1a: the same helper as the door. A refusal hides the file (404).
    if await _needs_connection(db, user.email, package.manifest, action or "",
                               org=user.organization_id):
        return None
    obj = await objects_store.get_object_by_name(db, object_name, package_id=pid)
    if obj is None:
        return None
    try:
        rec = await objects_store.get_record(db, obj.id, rid, package_id=pid,
                                             viewer=Viewer(user.email))
    except objects_store.StoreBusy:
        raise HTTPException(
            status_code=503, detail={"code": STORE_BUSY, "message": READ_BUSY_MESSAGE},
            headers={"Retry-After": BUSY_RETRY_AFTER}) from None
    except objects_store.ObjectStoreError:
        return None
    if rec is None:
        return None
    if is_viewer:
        rec = package.project_record(obj.name, rec)
        types = package.field_types(obj.name)
    else:
        types = {f.key: f.type for f in obj.fields}
    for key, kind in types.items():
        if kind == "file" and key in rec.data and _holds_file(rec.data[key], fid):
            try:
                return await files_store.read_file(
                    db, package_id=pid, object_id=obj.id, record_id=rid,
                    field_key=key, file_id=fid)
            except files_store.FileStoreError:
                return None
    return None
