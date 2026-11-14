"""The saved views of a Studio app (WS-STU AF-10a, ``ai_studio.md`` §14 AF-10a).

Four routes over ``studio/views_store.py``. They register on the router of
``actions.py``, as the sibling modules do, so ``main.py`` mounts no second
router and the gate ``require_feature_router("studio")`` holds (J1).

=====================================================  ==========================
``GET /packages/{id}/views?object=<name>``             the views that the member
                                                       reads on one object
``POST /packages/{id}/views``                          save a view (201)
``PATCH /packages/{id}/views/{view_id}``               change, share or unshare
``DELETE /packages/{id}/views/{view_id}``              delete a view
=====================================================  ==========================

**The order of the checks.** The switch ``STUDIO_ORGS`` (404, no session),
then the ids (404), then the body, read with a cap of :data:`MAX_BODY_BYTES`
(413), then the shape of the body and of the spec (422). All of this runs
before any session. Then ONE session reads the app, checks that the app holds
the object of a new view (422), writes, and writes the audit row in the same
transaction (``record_app_audit_in``). A failed audit row rolls the write
back, because a share and an unshare change who reads a view.

**Who reaches the app.** The member is ``user.email``, never request input.
The owner reads the package through ``packages_store.get_package``. Each other
member reads it through ``package_host/viewer.installed_app``, while
``PACKAGE_HOST_ORGS`` holds the org. These are the two reads of the action
door (``invoke._check``), and this module keeps no third rule. A member with
neither gets 404.

**The rules (owner to confirm at preview).**

1. A private view reads, changes and deletes for its owner only. The private
   view of another member is 404, never 403.
2. A shared view reads for every member who reaches the app. Only the owner
   of the package shares, unshares, changes or deletes one. A viewer gets 403
   ``read_only``. An unshare makes the view a private view of the owner of
   the package.
3. A viewer saves, changes and deletes their own private views. A saved view
   is state of the screen, not a write through the door.
4. The caps of the store: 50 private views of one member and 50 shared views
   on one object, and 200 of each on one app (409 ``view_cap``). A new view
   names an object that the app holds: the object row for the owner, and the
   installed version too for a viewer (422 ``view_invalid``). A name holds 1
   to 120 characters, and a spec at most 8000 bytes (422 ``view_invalid``).
5. A viewer reads each view with each part dropped that names a key the
   viewer may not read: a key that the roles of the viewer hide
   (``objects_store.query_hidden_keys``), a key of a field that the installed
   version does not declare, and each key of an object that the version does
   not declare. ``dropped`` counts the parts. A dropped value never leaves.
   The owner reads each view whole.

The write right of the app is the package owner now. After C8b, a member
whose role grants ``write: all`` on the object gets it too. That is not
built here. The module runs no SQL.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from acb_auth import UserContext, get_current_user
from acb_common import studio_query
from acb_common.studio_access import Viewer
from acb_common.studio_fields import NAME_PATTERN, storable_text
from fastapi import Depends, HTTPException, Query, Request
from gateway.db import tenant_session as _tenant_session
from gateway.package_host import viewer as _viewer
from gateway.package_host.flags import package_host_allowed
from gateway.routes.apps._common import record_app_audit_in
from gateway.routes.studio import flags
from gateway.routes.studio.actions import NOT_FOUND, router
from gateway.studio import objects_store, packages_store
from gateway.studio import views_store as store

__all__ = ["MAX_BODY_BYTES", "MAX_COLUMNS", "PROJECTED_KEYS", "SPEC_KEYS", "project_spec",
           "router"]

#: The most bytes of one body (16 KiB). A spec holds 8000 bytes at most.
MAX_BODY_BYTES = 16 * 1024
#: The most columns of one view.
MAX_COLUMNS = 50
#: The highest position of a view.
MAX_POSITION = 10_000
#: The keys of a spec. AF-3 adds ``me`` and AF-4 adds ``group`` and ``q``.
SPEC_KEYS = frozenset({"filter", "sort", "layout", "columns"})
#: The layouts of a view (AF-4): a table and a board.
LAYOUTS = ("table", "board")
#: The keys of a spec that :func:`project_spec` names. A test keeps it equal to
#: :data:`SPEC_KEYS`, so a new key of a spec needs a rule of the viewer read.
PROJECTED_KEYS = frozenset({"filter", "sort", "layout", "columns"})
_CREATE_KEYS = frozenset({"object", "name", "shared", "spec", "position"})
_PATCH_KEYS = frozenset({"name", "shared", "spec", "position"})
_NAME = re.compile(NAME_PATTERN, re.ASCII)

READ_ONLY = "read_only"
VIEW_CAP = "view_cap"
VIEW_NAME_TAKEN = "view_name_taken"
VIEW_INVALID = "view_invalid"
BODY_TOO_LARGE = "body_too_large"

NO_SHARE = ("This app is shared with you to read, so only its owner shares a view or changes "
            "a shared view.")
BODY_OVER_CAP = "This body is too large, so send a smaller view."
NOT_JSON = "The body must be a JSON object."
TOO_DEEP = "The body nests too deep to read. Flatten it."
BAD_KEYS = "A view takes object, name, shared, spec and position, and no other key."
BAD_PATCH = "A change takes name, shared, spec or position, and no other key."
EMPTY_PATCH = "Send at least one of name, shared, spec and position."
BAD_OBJECT = "object is the name of an object of the app: lower-case letters, digits and _."
BAD_NAME = "name is text of 1 to 120 characters."
BAD_SHARED = "shared is true or false."
BAD_POSITION = f"position is a whole number from 0 to {MAX_POSITION}."
BAD_SPEC = "spec is an object."
BAD_SPEC_KEYS = "spec takes filter, sort, layout and columns, and no other key."
BAD_LAYOUT = "layout is table or board."
NO_OBJECT = "The app holds no object of this name. Send the name of an object of the app."
BAD_COLUMNS = (f"columns is a list of at most {MAX_COLUMNS} field keys, each once: "
               "lower-case letters, digits and _.")


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail=NOT_FOUND)


def _invalid(message: str, code: str = VIEW_INVALID) -> HTTPException:
    return HTTPException(status_code=422, detail={"code": code, "message": message})


def _uuid(raw: str) -> str:
    """The id as the store reads it. A bad id names nothing, so it is 404."""
    try:
        return str(uuid.UUID(raw))
    except (ValueError, TypeError, AttributeError):
        raise _not_found() from None


async def _raw_body(request: Request) -> bytes:
    """The bytes of the body. The read stops at the first chunk past
    :data:`MAX_BODY_BYTES` (413). No session is open during the read."""
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BODY_BYTES:
            raise HTTPException(status_code=413, detail={
                "code": BODY_TOO_LARGE, "message": BODY_OVER_CAP})
        chunks.append(chunk)
    return b"".join(chunks)


def _parse(raw: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(raw)
    except RecursionError:
        raise _invalid(TOO_DEEP) from None
    except ValueError:
        raise _invalid(NOT_JSON) from None
    if not isinstance(payload, dict):
        raise _invalid(NOT_JSON)
    return payload


def _object_name(raw: Any) -> str:
    if isinstance(raw, str) and _NAME.fullmatch(raw):
        return raw
    raise _invalid(BAD_OBJECT)


def _view_name(raw: Any) -> str:
    if not isinstance(raw, str) or not storable_text(raw):
        raise _invalid(BAD_NAME)
    clean = raw.strip()
    if not 1 <= len(clean) <= store.MAX_NAME:
        raise _invalid(BAD_NAME)
    return clean


def _shared(raw: Any) -> bool:
    if not isinstance(raw, bool):
        raise _invalid(BAD_SHARED)
    return raw


def _position(raw: Any) -> int:
    if type(raw) is not int or not 0 <= raw <= MAX_POSITION:
        raise _invalid(BAD_POSITION)
    return raw


def check_spec(raw: Any) -> dict[str, Any]:
    """The spec of a view, after a check of its shape only. It needs no
    field, so it runs before a session. The filter and the sort take the one
    grammar of C2a (``studio_query.check_shape``). A refusal is 422."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise _invalid(BAD_SPEC)
    if not SPEC_KEYS.issuperset(raw):
        raise _invalid(BAD_SPEC_KEYS)
    try:
        studio_query.check_shape(raw.get("filter"), raw.get("sort"))
    except studio_query.QueryError as exc:
        raise _invalid(exc.message, exc.code) from None
    if "layout" in raw and raw["layout"] not in LAYOUTS:
        raise _invalid(BAD_LAYOUT)
    if "columns" in raw:
        cols = raw["columns"]
        if (not isinstance(cols, list) or len(cols) > MAX_COLUMNS
                or not all(isinstance(c, str) and _NAME.fullmatch(c) for c in cols)
                or len(set(cols)) != len(cols)):
            raise _invalid(BAD_COLUMNS)
    try:
        store.spec_text(raw)
    except store.ViewStoreError as exc:
        raise _invalid(str(exc)) from None
    return raw


def project_spec(spec: Mapping[str, Any], readable: frozenset[str]) -> tuple[dict[str, Any], int]:
    """The spec *spec* as a viewer who reads only the keys *readable* sees
    it, and the count of the parts that it drops. Pure.

    It drops each condition, the sort and each column that names another key.
    A dropped condition leaves with its value. It is an allowlist: the answer
    holds only the keys that it names (:data:`PROJECTED_KEYS`), so a key that
    a later slice adds stays out until this function reads it."""
    out: dict[str, Any] = {}
    dropped = 0
    if spec.get("layout") in LAYOUTS:
        out["layout"] = spec["layout"]
    if "filter" in spec:
        items = spec["filter"] if isinstance(spec["filter"], list) else []
        kept = [{k: c[k] for k in ("field", "op", "value") if k in c} for c in items
                if isinstance(c, Mapping) and c.get("field") in readable]
        dropped += len(items) - len(kept)
        out["filter"] = kept
    if "sort" in spec:
        sort = spec["sort"]
        if isinstance(sort, Mapping) and sort.get("field") in readable:
            out["sort"] = {k: sort[k] for k in ("field", "dir") if k in sort}
        else:
            out["sort"] = None
            dropped += sort is not None
    if "columns" in spec:
        cols = spec["columns"] if isinstance(spec["columns"], list) else []
        kept_cols = [c for c in cols if c in readable]
        dropped += len(cols) - len(kept_cols)
        out["columns"] = kept_cols
    return out, dropped


def _iso(at: datetime | None) -> str | None:
    return None if at is None else at.isoformat()


def _out(view: store.SavedView, member: str, spec: Mapping[str, Any] | None = None,
         dropped: int = 0) -> dict[str, Any]:
    """A view on the wire. It names no owner: ``mine`` says if it is the
    member's own."""
    return {"id": view.id, "object": view.object_name, "name": view.name,
            "shared": view.shared, "mine": view.owner_email == member.strip().lower(),
            "spec": dict(view.spec if spec is None else spec), "position": view.position,
            "dropped": dropped, "created_at": _iso(view.created_at),
            "updated_at": _iso(view.updated_at)}


async def _reach(db: Any, user: UserContext, pid: str) -> _viewer.InstalledApp | None:
    """None when the member owns the package *pid*. The installed app when
    the member reads it through an install. Else 404. The two reads of the
    door's ``_check``, in the same order."""
    if await packages_store.get_package(db, user.email, pid) is not None:
        return None
    if package_host_allowed(user.organization_id):
        app = await _viewer.installed_app(db, viewer=user.email, app_id=pid)
        if app is not None:
            return app
    raise _not_found()


async def _readable(db: Any, app: _viewer.InstalledApp, user: UserContext, pid: str,
                    object_name: str) -> frozenset[str]:
    """The keys of *object_name* that a viewer of *app* may name: the fields
    that the installed version declares and the object holds, less the keys
    that the roles of the viewer hide. An object that the version does not
    declare gives none."""
    if object_name not in app.object_names:
        return frozenset()
    obj = await objects_store.get_object_by_name(db, object_name, package_id=pid)
    if obj is None:
        return frozenset()
    hidden = await objects_store.query_hidden_keys(
        db, package_id=pid, object_id=obj.id, viewer=Viewer(user.email))
    if hidden is None:
        return frozenset()
    return (app.field_keys(object_name) & {f.key for f in obj.fields}) - hidden


def _read_only() -> HTTPException:
    return HTTPException(status_code=403, detail={"code": READ_ONLY, "message": NO_SHARE})


def _store_refusal(exc: store.ViewStoreError) -> HTTPException:
    if isinstance(exc, store.ViewCapReached):
        return HTTPException(status_code=409, detail={"code": VIEW_CAP, "message": str(exc)})
    if isinstance(exc, store.ViewNameTaken):
        return HTTPException(status_code=409, detail={
            "code": VIEW_NAME_TAKEN, "message": str(exc)})
    return _invalid(str(exc))


async def _audit(db: Any, user: UserContext, pid: str, op: str, shared: bool) -> None:
    """One ``app_audit`` row in the open session *db*, so the write and its row
    commit together, and a failed row rolls the write back. It names the op
    and the share flag only: no name, no filter and no value."""
    await record_app_audit_in(db, app_id=pid, user_email=user.email, kind="action",
                              detail={"op": op, "shared": shared})


async def _holds_object(db: Any, app: _viewer.InstalledApp | None, pid: str,
                        object_name: str) -> bool:
    """True when the app holds the object *object_name*: an object row of the
    package, and for a viewer an object of the installed version too. So a
    member cannot open a cap of 50 views for each name that they make up."""
    if app is not None and object_name not in app.object_names:
        return False
    return await objects_store.get_object_by_name(db, object_name, package_id=pid) is not None


@router.get("/packages/{package_id}/views")
async def list_views(
    package_id: str,
    user: UserContext = Depends(get_current_user),
    object_name: str | None = Query(None, alias="object"),
) -> dict[str, Any]:
    """The views of one object that the member reads: their own private views
    and every shared view. A viewer reads each with the parts dropped that
    name a key the viewer may not read."""
    if not flags.studio_allowed(user.organization_id):
        raise _not_found()
    pid = _uuid(package_id)
    name = _object_name(object_name)
    async with _tenant_session() as db:
        app = await _reach(db, user, pid)
        views = await store.list_views(db, user.email, package_id=pid, object_name=name)
        if app is None:
            return {"views": [_out(v, user.email) for v in views]}
        readable = await _readable(db, app, user, pid, name)
    out = []
    for view in views:
        spec, dropped = project_spec(view.spec, readable)
        out.append(_out(view, user.email, spec, dropped))
    return {"views": out}


@router.post("/packages/{package_id}/views", status_code=201)
async def create_view(
    package_id: str,
    request: Request,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Save a view of the member. Only the owner of the package saves a
    shared one."""
    if not flags.studio_allowed(user.organization_id):
        raise _not_found()
    pid = _uuid(package_id)
    body = _parse(await _raw_body(request))
    if not _CREATE_KEYS.issuperset(body):
        raise _invalid(BAD_KEYS)
    obj = _object_name(body.get("object"))
    name = _view_name(body.get("name"))
    shared = _shared(body.get("shared", False))
    position = _position(body.get("position", 0))
    spec = check_spec(body.get("spec"))
    async with _tenant_session() as db:
        app = await _reach(db, user, pid)
        if shared and app is not None:
            raise _read_only()
        if not await _holds_object(db, app, pid, obj):
            raise _invalid(NO_OBJECT)
        try:
            view = await store.create_view(db, user.email, package_id=pid, object_name=obj,
                                           name=name, shared=shared, spec=spec,
                                           position=position)
        except store.ViewStoreError as exc:
            raise _store_refusal(exc) from None
        await _audit(db, user, pid, "view_create", view.shared)
    return {"view": _out(view, user.email)}


def _op(before: bool, after: bool) -> str:
    if before == after:
        return "view_update"
    return "view_share" if after else "view_unshare"


@router.patch("/packages/{package_id}/views/{view_id}")
async def update_view(
    package_id: str,
    view_id: str,
    request: Request,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Change a view: its name, its spec, its position, or the share flag.
    A private view changes for its owner only. A shared view, and a share,
    change for the owner of the package only."""
    if not flags.studio_allowed(user.organization_id):
        raise _not_found()
    pid, vid = _uuid(package_id), _uuid(view_id)
    body = _parse(await _raw_body(request))
    if not _PATCH_KEYS.issuperset(body):
        raise _invalid(BAD_PATCH)
    if not body:
        raise _invalid(EMPTY_PATCH)
    name = _view_name(body["name"]) if "name" in body else None
    shared = _shared(body["shared"]) if "shared" in body else None
    position = _position(body["position"]) if "position" in body else None
    spec = check_spec(body["spec"]) if "spec" in body else None
    async with _tenant_session() as db:
        app = await _reach(db, user, pid)
        current = await store.get_view(db, user.email, package_id=pid, view_id=vid,
                                       for_update=True)
        if current is None:
            raise _not_found()
        after = current.shared if shared is None else shared
        if app is not None and (current.shared or after):
            raise _read_only()
        # An unshare makes the view a private view of the package owner.
        owner = user.email if (current.shared and not after) else current.owner_email
        try:
            view = await store.update_view(
                db, user.email, current,
                name=current.name if name is None else name, shared=after,
                spec=current.spec if spec is None else spec,
                position=current.position if position is None else position,
                owner_email=owner)
        except store.ViewStoreError as exc:
            raise _store_refusal(exc) from None
        if view is None:
            raise _not_found()
        await _audit(db, user, pid, _op(current.shared, view.shared), view.shared)
    return {"view": _out(view, user.email)}


@router.delete("/packages/{package_id}/views/{view_id}")
async def delete_view(
    package_id: str,
    view_id: str,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Delete a view. A private view goes for its owner only, and a shared
    view for the owner of the package only."""
    if not flags.studio_allowed(user.organization_id):
        raise _not_found()
    pid, vid = _uuid(package_id), _uuid(view_id)
    async with _tenant_session() as db:
        app = await _reach(db, user, pid)
        current = await store.get_view(db, user.email, package_id=pid, view_id=vid,
                                       for_update=True)
        if current is None:
            raise _not_found()
        if current.shared and app is not None:
            raise _read_only()
        if not await store.delete_view(db, user.email, current):
            raise _not_found()
        await _audit(db, user, pid, "view_delete", current.shared)
    return {"deleted": True, "id": current.id}
