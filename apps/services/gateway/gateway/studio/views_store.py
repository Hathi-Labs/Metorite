"""The one store of the views that a member saves on a Studio app (WS-STU AF-10a).

Spec: ``project-docs/specs/ai_studio.md`` §14 AF-10a and §5.5. Migration: the
file that adds ``studio_saved_views`` (441 at build). The routes are
``routes/studio/views.py``. ``tests/unit/test_studio_saved_views_r8.py`` runs
this SQL on a real Postgres.

**One module names the table.** This is the one module that runs SQL on
``studio_saved_views``. The member purge names the table in its clause list
(``routes/admin/members.py``), and no other Python does. Fence:
``test_studio_saved_views.py::test_one_module_runs_sql_on_the_table``.

**A view holds a query, never data.** The spec is ``{filter, sort, layout,
columns}``. :func:`spec_text` checks that Postgres takes the JSON text, and
the route checks the shape before it opens a session.

**Tenancy (R5).** Each function takes a session that the caller opened with
``tenant_session()``, so RLS binds each statement. No function takes an org.
An INSERT writes no ``organization_id``: the column DEFAULT reads the bound
tenant, and each statement filters on ``_BOUND_ORG`` too.

**The member.** Each function takes the member as a required argument. A read
filters on ``shared OR owner_email = :me``, so the private view of another
member is never a row here. ⚠️ The store does not authenticate. The caller
resolves the member from the session, never from request input, and the
caller decides who may share (the route holds the rules of AF-10a).

**The caps.** :data:`MAX_PRIVATE` private views of one member, and
:data:`MAX_SHARED` shared views, on one object of one package. A second wall
holds for the package: :data:`MAX_PACKAGE_PRIVATE` private views of one member
and :data:`MAX_PACKAGE_SHARED` shared views. The route also refuses an object
that the app does not hold. A create and a move between private and shared
take the advisory lock of the package first, then count, then write. So two
writers at once cannot pass a cap.
"""

from __future__ import annotations

import json
import math
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from acb_common.studio_fields import storable_text
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

#: The most private views of one member on one object of one package.
MAX_PRIVATE = 50
#: The most shared views on one object of one package.
MAX_SHARED = 50
#: The most private views of one member on all objects of one package.
MAX_PACKAGE_PRIVATE = 200
#: The most shared views on all objects of one package.
MAX_PACKAGE_SHARED = 200
#: The most characters of the name of a view.
MAX_NAME = 120
#: The bound of the CHECK ``studio_saved_views_spec``, in bytes of JSON text.
MAX_SPEC_BYTES = 8000
#: The most views that one read returns. It is the sum of the two caps.
MAX_LIST = MAX_PRIVATE + MAX_SHARED

_BOUND_ORG = "CAST(NULLIF(current_setting('app.tenant_id', true), '') AS uuid)"

_COLUMNS = (
    "id::text, package_id::text, object_name, owner_email, name, shared, spec, "
    "position, created_at, updated_at"
)

#: The lock of one package, until the transaction ends. The
#: first column is false when no tenant is bound, and then the key is NULL
#: and Postgres takes no lock. The idiom of ``packages_store._CREATE_LOCK_SQL``.
_LOCK_SQL = (
    f"SELECT {_BOUND_ORG} IS NOT NULL, pg_advisory_xact_lock(hashtextextended("
    f"'studio_saved_views:' || CAST({_BOUND_ORG} AS text) || ':' || :pid, 0))"
)

_LIST_SQL = (
    f"SELECT {_COLUMNS} FROM studio_saved_views "
    f"WHERE organization_id = {_BOUND_ORG} AND package_id = CAST(:pid AS uuid) "
    "AND object_name = :obj AND (shared OR owner_email = :me) "
    "ORDER BY shared DESC, position, lower(name), id LIMIT :n"
)

_GET_SQL = (
    f"SELECT {_COLUMNS} FROM studio_saved_views "
    f"WHERE organization_id = {_BOUND_ORG} AND package_id = CAST(:pid AS uuid) "
    "AND id = CAST(:vid AS uuid) AND (shared OR owner_email = :me)"
)

_COUNT_PRIVATE_SQL = (
    "SELECT count(*) FROM studio_saved_views "
    f"WHERE organization_id = {_BOUND_ORG} AND package_id = CAST(:pid AS uuid) "
    "AND object_name = :obj AND NOT shared AND owner_email = :owner"
)

_COUNT_SHARED_SQL = (
    "SELECT count(*) FROM studio_saved_views "
    f"WHERE organization_id = {_BOUND_ORG} AND package_id = CAST(:pid AS uuid) "
    "AND object_name = :obj AND shared"
)

_COUNT_PACKAGE_PRIVATE_SQL = (
    "SELECT count(*) FROM studio_saved_views "
    f"WHERE organization_id = {_BOUND_ORG} AND package_id = CAST(:pid AS uuid) "
    "AND NOT shared AND owner_email = :owner"
)

_COUNT_PACKAGE_SHARED_SQL = (
    "SELECT count(*) FROM studio_saved_views "
    f"WHERE organization_id = {_BOUND_ORG} AND package_id = CAST(:pid AS uuid) "
    "AND shared"
)

_INSERT_SQL = (
    "INSERT INTO studio_saved_views "
    "(package_id, object_name, owner_email, name, shared, spec, position) "
    "VALUES (CAST(:pid AS uuid), :obj, :me, :name, :shared, CAST(:spec AS jsonb), :pos) "
    f"RETURNING {_COLUMNS}"
)

_UPDATE_SQL = (
    "UPDATE studio_saved_views SET name = :name, shared = :shared, "
    "spec = CAST(:spec AS jsonb), position = :pos, owner_email = :owner, updated_at = now() "
    f"WHERE organization_id = {_BOUND_ORG} AND package_id = CAST(:pid AS uuid) "
    "AND id = CAST(:vid AS uuid) "
    f"RETURNING {_COLUMNS}"
)

_DELETE_SQL = (
    "DELETE FROM studio_saved_views "
    f"WHERE organization_id = {_BOUND_ORG} AND package_id = CAST(:pid AS uuid) "
    "AND id = CAST(:vid AS uuid)"
)

#: SQLSTATE of a unique key and of a CHECK.
_UNIQUE = "23505"
_CHECK = "23514"


class ViewStoreError(ValueError):
    """A refused write. The text never echoes a value."""


class ViewCapReached(ViewStoreError):
    """The cap of private or of shared views of the object, or of the
    package, is reached."""

    def __init__(self, shared: bool, package: bool = False) -> None:
        if package:
            cap = MAX_PACKAGE_SHARED if shared else MAX_PACKAGE_PRIVATE
        else:
            cap = MAX_SHARED if shared else MAX_PRIVATE
        kind = "shared views" if shared else "private views of one member"
        where = "An app" if package else "An object"
        super().__init__(f"{where} holds at most {cap} {kind}. Delete a view, then "
                         "save again.")
        self.shared = shared
        self.package = package


class ViewNameTaken(ViewStoreError):
    """The member holds a view of this name on the object."""

    def __init__(self) -> None:
        super().__init__("You hold a view of this name on this object. Use another name.")


@dataclass(frozen=True)
class SavedView:
    """One saved view, as the store reads it."""

    id: str
    package_id: str
    object_name: str
    owner_email: str
    name: str
    shared: bool
    spec: dict[str, Any]
    position: int
    created_at: datetime | None
    updated_at: datetime | None


def _member(member: str) -> str:
    clean = (member or "").strip().lower()
    if "@" not in clean:
        raise ViewStoreError("The member is the address of the session.")
    return clean


def _id(raw: str, name: str) -> str:
    try:
        return str(uuid.UUID(str(raw)))
    except (ValueError, TypeError, AttributeError):
        raise ViewStoreError(f"{name} is not an id.") from None


def _storable(value: Any) -> bool:
    """True when Postgres takes *value* in jsonb: text with no NUL and no lone
    surrogate, and no number that is not finite."""
    if isinstance(value, str):
        return storable_text(value)
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, Mapping):
        return all(isinstance(k, str) and storable_text(k) and _storable(v)
                   for k, v in value.items())
    if isinstance(value, list | tuple):
        return all(_storable(v) for v in value)
    return value is None or isinstance(value, bool | int)


def spec_text(spec: Mapping[str, Any]) -> str:
    """The JSON text of *spec* for the INSERT. Pure, so the route runs it
    before a session. It raises :class:`ViewStoreError` for a value that
    Postgres refuses, and for a text over :data:`MAX_SPEC_BYTES` bytes.

    jsonb writes a float such as ``1e-05`` longer than Python does, so the
    CHECK can still refuse a spec close to the cap. The store maps that
    refusal to the same error."""
    if not isinstance(spec, Mapping) or not _storable(spec):
        raise ViewStoreError("The spec holds a value that cannot be stored.")
    out = json.dumps(spec, ensure_ascii=False)
    if len(out.encode("utf-8")) > MAX_SPEC_BYTES:
        raise ViewStoreError(f"A view spec holds at most {MAX_SPEC_BYTES} bytes of JSON.")
    return out


def _json(value: Any) -> dict[str, Any]:
    if isinstance(value, str | bytes):
        value = json.loads(value)
    return dict(value) if isinstance(value, Mapping) else {}


def _row(row: Any) -> SavedView:
    return SavedView(
        id=str(row[0]), package_id=str(row[1]), object_name=str(row[2]),
        owner_email=str(row[3]), name=str(row[4]), shared=bool(row[5]), spec=_json(row[6]),
        position=int(row[7]), created_at=row[8], updated_at=row[9])


def _sqlstate(exc: DBAPIError) -> str | None:
    orig = getattr(exc, "orig", None)
    code = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    return None if code is None else str(code)


async def _lock(db: Any, pid: str) -> None:
    row = (await db.execute(text(_LOCK_SQL), {"pid": pid})).first()
    if row is None or not row[0]:
        raise ViewStoreError("No tenant is bound.")


async def _check_cap(db: Any, pid: str, obj: str, *, shared: bool, owner: str) -> None:
    sql, binds = ((_COUNT_SHARED_SQL, {"pid": pid, "obj": obj}) if shared
                  else (_COUNT_PRIVATE_SQL, {"pid": pid, "obj": obj, "owner": owner}))
    held = int((await db.execute(text(sql), binds)).scalar_one())
    if held >= (MAX_SHARED if shared else MAX_PRIVATE):
        raise ViewCapReached(shared)
    sql, binds = ((_COUNT_PACKAGE_SHARED_SQL, {"pid": pid}) if shared
                  else (_COUNT_PACKAGE_PRIVATE_SQL, {"pid": pid, "owner": owner}))
    held = int((await db.execute(text(sql), binds)).scalar_one())
    if held >= (MAX_PACKAGE_SHARED if shared else MAX_PACKAGE_PRIVATE):
        raise ViewCapReached(shared, package=True)


async def _write(db: Any, sql: str, binds: dict[str, Any]) -> Any:
    """Run one write. A taken name and a refused CHECK raise the store errors."""
    try:
        return (await db.execute(text(sql), binds)).first()
    except DBAPIError as exc:
        state = _sqlstate(exc)
        if state == _UNIQUE:
            raise ViewNameTaken() from None
        if state == _CHECK:
            raise ViewStoreError("The view does not fit the rules of the store.") from None
        raise


async def list_views(db: Any, member: str, *, package_id: str,
                     object_name: str) -> list[SavedView]:
    """The views of the object *object_name* of the package *package_id* that
    *member* reads: the member's own private views and every shared view.
    Shared views first, then by position and name."""
    rows = (await db.execute(text(_LIST_SQL), {
        "pid": _id(package_id, "package_id"), "obj": object_name, "me": _member(member),
        "n": MAX_LIST})).all()
    return [_row(r) for r in rows]


async def get_view(db: Any, member: str, *, package_id: str, view_id: str,
                   for_update: bool = False) -> SavedView | None:
    """The view *view_id* of the package *package_id*, or None when *member*
    does not read it (another member's private view is None)."""
    sql = _GET_SQL + (" FOR UPDATE" if for_update else "")
    row = (await db.execute(text(sql), {
        "pid": _id(package_id, "package_id"), "vid": _id(view_id, "view_id"),
        "me": _member(member)})).first()
    return None if row is None else _row(row)


async def create_view(db: Any, member: str, *, package_id: str, object_name: str,
                      name: str, shared: bool, spec: Mapping[str, Any],
                      position: int = 0) -> SavedView:
    """Write a new view of *member*. It takes the lock of the package, checks
    the caps of its kind, then writes. The caller checks that the app holds
    the object *object_name*. Raises :class:`ViewCapReached`,
    :class:`ViewNameTaken` or :class:`ViewStoreError`."""
    me, pid = _member(member), _id(package_id, "package_id")
    body = spec_text(spec)
    await _lock(db, pid)
    await _check_cap(db, pid, object_name, shared=shared, owner=me)
    row = await _write(db, _INSERT_SQL, {
        "pid": pid, "obj": object_name, "me": me, "name": name, "shared": bool(shared),
        "spec": body, "pos": int(position)})
    return _row(row)


async def update_view(db: Any, member: str, current: SavedView, *, name: str,
                      shared: bool, spec: Mapping[str, Any], position: int,
                      owner_email: str) -> SavedView | None:
    """Write the new state of *current*, a view that *member* read with
    :func:`get_view` in this session. A move between private and shared takes
    the lock of the object and checks the cap of the new kind first. Answers
    None when the row is gone."""
    _member(member)
    owner = _member(owner_email)
    pid = _id(current.package_id, "package_id")
    body = spec_text(spec)
    if bool(shared) != current.shared or owner != current.owner_email:
        await _lock(db, pid)
        await _check_cap(db, pid, current.object_name, shared=bool(shared), owner=owner)
    row = await _write(db, _UPDATE_SQL, {
        "pid": pid, "vid": _id(current.id, "view_id"), "name": name, "shared": bool(shared),
        "spec": body, "pos": int(position), "owner": owner})
    return None if row is None else _row(row)


async def delete_view(db: Any, member: str, current: SavedView) -> bool:
    """Delete *current*, a view that *member* read with :func:`get_view` in
    this session. True when a row went."""
    _member(member)
    gone = await db.execute(text(_DELETE_SQL), {
        "pid": _id(current.package_id, "package_id"), "vid": _id(current.id, "view_id")})
    return int(gone.rowcount or 0) == 1
