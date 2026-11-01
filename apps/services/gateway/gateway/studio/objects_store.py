"""The one writer of the typed object store of AI Studio (WS-STU C1).

Spec: ``project-docs/specs/ai_studio.md`` §3.1 and the §14 C1 row. Tables:
migration ``studio_objects`` (323 at build): ``studio_objects``,
``studio_object_fields``, ``studio_records`` and ``studio_record_hot``.
``tests/unit/test_studio_objects_r8.py`` runs this SQL on a real Postgres.

**Tenancy (R5).** Every function takes a session that the caller opened with
``tenant_session(org)``, so RLS binds each statement. No function takes an
org. An INSERT writes no ``organization_id``: the column DEFAULT reads the
bound tenant, and each statement filters on ``_BOUND_ORG`` too.

**The caller owns the transaction.** No function here commits, rolls back or
opens a transaction. So C1b can put several calls in one transaction. A
refusal before a statement raises a :class:`ObjectStoreError`, a
:class:`~acb_common.studio_fields.FieldError` or a
:class:`~acb_common.studio_fields.RecordError`, and the session stays usable.

**One check of a value.** Each record write runs
``acb_common.studio_fields.validate_record`` over the fields of the object,
and this module keeps no rule of a field type. A2 and PKG-13 call the same
module.

**The hot rows.** A record write fills the hot rows of the record in the
statement that writes the record, so the two cannot drift. A field that
turns hot gets its rows for the records that exist (the backfill), in the
caller's transaction, and only on an object of at most
:data:`~acb_common.studio_fields.MAX_HOT_BACKFILL` records.

**The locks, in one order.** A registry change takes the advisory lock of the
org, then ``FOR UPDATE`` on the object row. A record write takes
``FOR KEY SHARE`` on the object row, then reads the fields. So a field that
turns hot waits for each open record write of the object, and a record write
that starts later reads the field as hot. The trigger
``studio_object_fields_hot_budget`` takes the advisory lock on the same key.

**Reads are by id only.** The list, the filter and the sort are C2. A record
read names the object too, so a door of one package cannot read a record of
another package by its id.

⚠️ The store does not authenticate. It only scopes. The actor of a write is
an argument that the caller takes from the session, never from request input
(R5, contract R11). The door (B2) checks the viewer.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from acb_common.studio_fields import (
    MAX_FIELDS_PER_OBJECT,
    MAX_HOT_BACKFILL,
    MAX_HOT_PER_OBJECT,
    MAX_HOT_PER_ORG,
    MAX_LABEL_CHARS,
    MAX_OBJECTS_PER_ORG,
    MAX_OBJECTS_PER_PACKAGE,
    NAME_PATTERN,
    FieldSpec,
    hot_value,
    parse_field_spec,
    record_json,
    storable_text,
    stored_field,
    validate_record,
)
from sqlalchemy import text

_BOUND_ORG = "CAST(NULLIF(current_setting('app.tenant_id', true), '') AS uuid)"

#: The advisory lock of the org. The trigger ``studio_object_fields_hot_budget``
#: builds the same key from ``NEW.organization_id``.
_ORG_LOCK_SQL = (
    "SELECT pg_advisory_xact_lock(hashtextextended("
    f"'studio_objects:' || CAST({_BOUND_ORG} AS text), 0))"
)

#: A backfill reads and writes the records in pages of this size.
BACKFILL_PAGE = 2_000

_NAME_RE = re.compile(NAME_PATTERN, re.ASCII)
_ZERO_UUID = "00000000-0000-0000-0000-000000000000"

_OBJECT_COLUMNS = "id::text, package_id::text, name, label, created_by, created_at, updated_at"
_RECORD_COLUMNS = "id::text, object_id::text, data, created_by, updated_by, created_at, updated_at"
#: The value columns of a hot row, as ``jsonb_to_recordset`` reads them.
_HOT_SET = "AS x(k text, t text, n numeric, ts timestamptz, b boolean)"

_PACKAGE_SQL = (
    "SELECT 1 FROM apps "
    f"WHERE organization_id = {_BOUND_ORG} AND id = CAST(:pid AS uuid)"
)

_OBJECT_COUNTS_SQL = (
    "SELECT count(*) FILTER (WHERE package_id = CAST(:pid AS uuid)), count(*) "
    f"FROM studio_objects WHERE organization_id = {_BOUND_ORG}"
)

_ORG_HOT_SQL = (
    "SELECT count(*) FROM studio_object_fields "
    f"WHERE organization_id = {_BOUND_ORG} AND hot"
)

#: The object and its fields, in one statement. A name that the package
#: holds makes no row, and the store then refuses.
_DEFINE_SQL = (
    "WITH o AS ("
    "INSERT INTO studio_objects (package_id, name, label, created_by) "
    "VALUES (CAST(:pid AS uuid), :name, :label, :actor) "
    "ON CONFLICT (organization_id, package_id, name) DO NOTHING "
    f"RETURNING {_OBJECT_COLUMNS}), "
    "f AS ("
    "INSERT INTO studio_object_fields (object_id, key, type, spec, hot, position) "
    "SELECT CAST(o.id AS uuid), x.key, x.type, x.spec, x.hot, x.position FROM o "
    "CROSS JOIN jsonb_to_recordset(CAST(:fields AS jsonb)) "
    "AS x(key text, type text, spec jsonb, hot boolean, position integer) "
    "RETURNING 1) "
    "SELECT * FROM o"
)

_GET_OBJECT_SQL = (
    f"SELECT {_OBJECT_COLUMNS} FROM studio_objects "
    f"WHERE organization_id = {_BOUND_ORG} AND id = CAST(:oid AS uuid)"
)

#: A registry change locks the object row, so it waits for each record write.
_LOCK_OBJECT_SQL = _GET_OBJECT_SQL + " FOR UPDATE"

#: A record write takes the weakest lock that a registry change waits for.
#: The foreign key check of the record takes the same lock.
_SHARE_OBJECT_SQL = (
    "SELECT id FROM studio_objects "
    f"WHERE organization_id = {_BOUND_ORG} AND id = CAST(:oid AS uuid) FOR KEY SHARE"
)

_FIELDS_SQL = (
    "SELECT key, type, spec, hot FROM studio_object_fields "
    f"WHERE organization_id = {_BOUND_ORG} AND object_id = CAST(:oid AS uuid) "
    "ORDER BY position, key"
)

_ADD_FIELD_SQL = (
    "INSERT INTO studio_object_fields (object_id, key, type, spec, hot, position) "
    "VALUES (CAST(:oid AS uuid), :key, :type, CAST(:spec AS jsonb), :hot, :position) "
    "ON CONFLICT (organization_id, object_id, key) DO NOTHING RETURNING key"
)

_TOUCH_OBJECT_SQL = (
    "UPDATE studio_objects SET updated_at = now() "
    f"WHERE organization_id = {_BOUND_ORG} AND id = CAST(:oid AS uuid)"
)

_HAS_RECORDS_SQL = (
    "SELECT EXISTS (SELECT 1 FROM studio_records "
    f"WHERE organization_id = {_BOUND_ORG} AND object_id = CAST(:oid AS uuid))"
)

#: The records of one object, counted to one past the backfill cap.
_RECORD_COUNT_SQL = (
    "SELECT count(*) FROM (SELECT 1 FROM studio_records "
    f"WHERE organization_id = {_BOUND_ORG} AND object_id = CAST(:oid AS uuid) "
    "LIMIT :cap) s"
)

_SET_HOT_SQL = (
    "UPDATE studio_object_fields SET hot = :hot "
    f"WHERE organization_id = {_BOUND_ORG} AND object_id = CAST(:oid AS uuid) "
    "AND key = :key"
)

_UNHOT_SQL = (
    "DELETE FROM studio_record_hot "
    f"WHERE organization_id = {_BOUND_ORG} AND object_id = CAST(:oid AS uuid) "
    "AND field_key = :key"
)

#: One page of the backfill: the value of one field, in the order of the id.
#: The value comes back as JSON text, because a driver may decode a jsonb
#: string to a plain string, and then a parse of it fails.
_BACKFILL_READ_SQL = (
    "SELECT id::text, CAST(data -> CAST(:key AS text) AS text) FROM studio_records "
    f"WHERE organization_id = {_BOUND_ORG} AND object_id = CAST(:oid AS uuid) "
    "AND (data -> CAST(:key AS text)) IS NOT NULL AND id > CAST(:after AS uuid) "
    "ORDER BY id LIMIT :page"
)

_BACKFILL_WRITE_SQL = (
    "INSERT INTO studio_record_hot "
    "(object_id, record_id, field_key, v_text, v_num, v_ts, v_bool) "
    "SELECT CAST(:oid AS uuid), CAST(x.r AS uuid), :key, x.t, x.n, x.ts, x.b "
    "FROM jsonb_to_recordset(CAST(:rows AS jsonb)) "
    "AS x(r text, t text, n numeric, ts timestamptz, b boolean)"
)

_DROP_OBJECT_SQL = (
    "DELETE FROM studio_objects "
    f"WHERE organization_id = {_BOUND_ORG} AND id = CAST(:oid AS uuid) RETURNING id"
)

#: The record and its hot rows, in one statement.
_CREATE_RECORD_SQL = (
    "WITH r AS ("
    "INSERT INTO studio_records (object_id, data, created_by, updated_by) "
    "VALUES (CAST(:oid AS uuid), CAST(:data AS jsonb), :actor, :actor) "
    "RETURNING object_id, id, data, created_by, updated_by, created_at, updated_at), "
    "h AS ("
    "INSERT INTO studio_record_hot "
    "(object_id, record_id, field_key, v_text, v_num, v_ts, v_bool) "
    "SELECT r.object_id, r.id, x.k, x.t, x.n, x.ts, x.b FROM r "
    f"CROSS JOIN jsonb_to_recordset(CAST(:hot AS jsonb)) {_HOT_SET} "
    "RETURNING 1) "
    "SELECT r.id::text, r.object_id::text, r.data, r.created_by, r.updated_by, "
    "r.created_at, r.updated_at FROM r"
)

_GET_RECORD_SQL = (
    f"SELECT {_RECORD_COLUMNS} FROM studio_records "
    f"WHERE organization_id = {_BOUND_ORG} AND object_id = CAST(:oid AS uuid) "
    "AND id = CAST(:rid AS uuid)"
)

#: The record and its hot rows, in one statement. The DELETE takes the hot
#: rows of the keys that hold no value now, and the INSERT writes the others.
#: The two touch different rows.
_UPDATE_RECORD_SQL = (
    "WITH r AS ("
    "UPDATE studio_records SET data = CAST(:data AS jsonb), updated_by = :actor, "
    "updated_at = now() "
    f"WHERE organization_id = {_BOUND_ORG} AND object_id = CAST(:oid AS uuid) "
    "AND id = CAST(:rid AS uuid) "
    "RETURNING object_id, id, data, created_by, updated_by, created_at, updated_at), "
    "gone AS ("
    "DELETE FROM studio_record_hot h USING r "
    f"WHERE h.organization_id = {_BOUND_ORG} AND h.record_id = r.id "
    f"AND NOT EXISTS (SELECT 1 FROM jsonb_to_recordset(CAST(:hot AS jsonb)) {_HOT_SET} "
    "WHERE x.k = h.field_key) "
    "RETURNING 1), "
    "put AS ("
    "INSERT INTO studio_record_hot "
    "(object_id, record_id, field_key, v_text, v_num, v_ts, v_bool) "
    "SELECT r.object_id, r.id, x.k, x.t, x.n, x.ts, x.b FROM r "
    f"CROSS JOIN jsonb_to_recordset(CAST(:hot AS jsonb)) {_HOT_SET} "
    "ON CONFLICT (organization_id, record_id, field_key) DO UPDATE SET "
    "v_text = EXCLUDED.v_text, v_num = EXCLUDED.v_num, v_ts = EXCLUDED.v_ts, "
    "v_bool = EXCLUDED.v_bool "
    "RETURNING 1) "
    "SELECT r.id::text, r.object_id::text, r.data, r.created_by, r.updated_by, "
    "r.created_at, r.updated_at FROM r"
)

_DELETE_RECORD_SQL = (
    "DELETE FROM studio_records "
    f"WHERE organization_id = {_BOUND_ORG} AND object_id = CAST(:oid AS uuid) "
    "AND id = CAST(:rid AS uuid) RETURNING id"
)


class ObjectStoreError(ValueError):
    """A registry write that the store refuses before it reaches the database."""


class ObjectNameTaken(ObjectStoreError):
    """The package holds an object with this name already."""


class FieldKeyTaken(ObjectStoreError):
    """The object holds a field with this key already."""


class BudgetExceeded(ObjectStoreError):
    """A cap of the registry: hot fields, fields, objects, or the backfill size."""


@dataclass(frozen=True)
class StudioObject:
    """One row of ``studio_objects``, with its fields in their order."""

    id: str
    package_id: str
    name: str
    label: str | None
    created_by: str
    created_at: datetime
    updated_at: datetime
    fields: tuple[FieldSpec, ...]


@dataclass(frozen=True)
class Record:
    """One row of ``studio_records``."""

    id: str
    object_id: str
    data: dict[str, Any]
    created_by: str
    updated_by: str
    created_at: datetime
    updated_at: datetime


def _actor(actor: object) -> str:
    """The actor as the CHECKs ``studio_*_created_by`` hold it."""
    if not isinstance(actor, str) or not actor.strip() or not storable_text(actor):
        raise ObjectStoreError("actor must be a non-empty string")
    return actor.strip().lower()


def _uuid(value: object, what: str) -> str:
    """An id must be a UUID. A bad one fails here, not in a statement that
    would end the caller's transaction."""
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ObjectStoreError(f"{what} must be a UUID") from exc


def _json(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str | bytes) else value


def _object(row: Any, fields: Sequence[FieldSpec]) -> StudioObject:
    return StudioObject(
        id=str(row[0]), package_id=str(row[1]), name=str(row[2]),
        label=None if row[3] is None else str(row[3]), created_by=str(row[4]),
        created_at=row[5], updated_at=row[6], fields=tuple(fields))


def _record(row: Any) -> Record:
    return Record(
        id=str(row[0]), object_id=str(row[1]), data=dict(_json(row[2])),
        created_by=str(row[3]), updated_by=str(row[4]), created_at=row[5],
        updated_at=row[6])


def _hot_rows(fields: Sequence[FieldSpec], data: Mapping[str, Any]) -> str:
    """The hot rows of one checked record, as the JSON that the SQL reads."""
    rows = []
    for f in fields:
        row = hot_value(f, data.get(f.key))
        if row is not None:
            rows.append(row)
    return json.dumps(rows, ensure_ascii=False, allow_nan=False)


async def _fields(db: Any, object_id: str) -> list[FieldSpec]:
    rows = (await db.execute(text(_FIELDS_SQL), {"oid": object_id})).all()
    return [stored_field(str(r[0]), str(r[1]), _json(r[2]), bool(r[3])) for r in rows]


async def _lock_org(db: Any) -> None:
    await db.execute(text(_ORG_LOCK_SQL))


async def _org_hot(db: Any) -> int:
    return int((await db.execute(text(_ORG_HOT_SQL))).scalar_one())


def _check_name(name: object) -> str:
    if not isinstance(name, str) or not _NAME_RE.fullmatch(name):
        raise ObjectStoreError(
            "An object name is lower-case letters, digits and _, a letter first, at most 48.")
    return name


def _check_label(label: object) -> str | None:
    if label is None:
        return None
    if not isinstance(label, str) or len(label) > MAX_LABEL_CHARS or not storable_text(label):
        raise ObjectStoreError(f"An object label is text of at most {MAX_LABEL_CHARS} characters.")
    return label


def _check_fields(fields: Sequence[Mapping[str, Any]]) -> list[FieldSpec]:
    if isinstance(fields, str | bytes | Mapping):
        raise ObjectStoreError("fields must be a list of field specs")
    specs = [parse_field_spec(f) for f in fields]
    if len(specs) > MAX_FIELDS_PER_OBJECT:
        raise BudgetExceeded(f"An object holds at most {MAX_FIELDS_PER_OBJECT} fields.")
    keys = [s.key for s in specs]
    if len(set(keys)) != len(keys):
        raise ObjectStoreError("Each field key of an object must be unique.")
    if sum(s.hot for s in specs) > MAX_HOT_PER_OBJECT:
        raise BudgetExceeded(f"An object holds at most {MAX_HOT_PER_OBJECT} hot fields.")
    return specs


async def define_object(db: Any, actor: str, *, package_id: str, name: str,
                        label: str | None = None,
                        fields: Sequence[Mapping[str, Any]] = ()) -> StudioObject | None:
    """Define the object *name* of the package *package_id*, with its fields.

    Answers None when the bound org holds no package of the id. Raises
    :class:`ObjectNameTaken` when the package holds the name already, and
    :class:`BudgetExceeded` past a cap: 50 objects for each package, 200 for
    each org, 60 fields for each object, and the hot budget. Each field spec
    is checked by ``studio_fields.parse_field_spec``. A bare ``to`` passes on
    its shape: the manifest check (A2) knows the other objects.
    """
    who = _actor(actor)
    pid = _uuid(package_id, "package_id")
    clean = _check_name(name)
    clean_label = _check_label(label)
    specs = _check_fields(fields)
    await _lock_org(db)
    if (await db.execute(text(_PACKAGE_SQL), {"pid": pid})).first() is None:
        return None
    in_package, in_org = (await db.execute(text(_OBJECT_COUNTS_SQL), {"pid": pid})).one()
    if int(in_package) >= MAX_OBJECTS_PER_PACKAGE:
        raise BudgetExceeded(f"A package holds at most {MAX_OBJECTS_PER_PACKAGE} objects.")
    if int(in_org) >= MAX_OBJECTS_PER_ORG:
        raise BudgetExceeded(f"An organization holds at most {MAX_OBJECTS_PER_ORG} objects.")
    hot = sum(s.hot for s in specs)
    if hot and await _org_hot(db) + hot > MAX_HOT_PER_ORG:
        raise BudgetExceeded(f"An organization holds at most {MAX_HOT_PER_ORG} hot fields.")
    rows = [{"key": s.key, "type": s.type, "spec": s.stored_spec(), "hot": s.hot,
             "position": i} for i, s in enumerate(specs)]
    row = (await db.execute(text(_DEFINE_SQL), {
        "pid": pid, "name": clean, "label": clean_label, "actor": who,
        "fields": json.dumps(rows, ensure_ascii=False)})).first()
    if row is None:
        raise ObjectNameTaken("This package has an object with this name. Choose another name.")
    return _object(row, specs)


async def get_object(db: Any, object_id: str) -> StudioObject | None:
    """The object *object_id* of the bound org with its fields, or None."""
    oid = _uuid(object_id, "object_id")
    row = (await db.execute(text(_GET_OBJECT_SQL), {"oid": oid})).first()
    if row is None:
        return None
    return _object(row, await _fields(db, oid))


async def add_field(db: Any, object_id: str, spec: Mapping[str, Any]) -> FieldSpec | None:
    """Add one field to the object *object_id*. Answers None when the bound org
    holds no object of the id.

    A new field holds no value in a record yet, so a hot field needs no
    backfill. A required field goes only on an object with no records, because
    each record would then break the rule. Raises :class:`FieldKeyTaken`,
    :class:`BudgetExceeded`, or a :class:`FieldError` for a bad spec.
    """
    oid = _uuid(object_id, "object_id")
    field = parse_field_spec(spec)
    await _lock_org(db)
    if (await db.execute(text(_LOCK_OBJECT_SQL), {"oid": oid})).first() is None:
        return None
    fields = await _fields(db, oid)
    if field.key in {f.key for f in fields}:
        raise FieldKeyTaken("This object has a field with this key. Choose another key.")
    if len(fields) >= MAX_FIELDS_PER_OBJECT:
        raise BudgetExceeded(f"An object holds at most {MAX_FIELDS_PER_OBJECT} fields.")
    if field.required and (await db.execute(text(_HAS_RECORDS_SQL), {"oid": oid})).scalar_one():
        raise ObjectStoreError(
            "This object has records, so a new field cannot be required. Add it as optional.")
    if field.hot:
        _check_hot_room(sum(f.hot for f in fields), await _org_hot(db))
    await db.execute(text(_ADD_FIELD_SQL), {
        "oid": oid, "key": field.key, "type": field.type,
        "spec": json.dumps(field.stored_spec(), ensure_ascii=False), "hot": field.hot,
        "position": len(fields)})
    await db.execute(text(_TOUCH_OBJECT_SQL), {"oid": oid})
    return field


def _check_hot_room(object_hot: int, org_hot: int) -> None:
    if object_hot >= MAX_HOT_PER_OBJECT:
        raise BudgetExceeded(f"An object holds at most {MAX_HOT_PER_OBJECT} hot fields.")
    if org_hot >= MAX_HOT_PER_ORG:
        raise BudgetExceeded(f"An organization holds at most {MAX_HOT_PER_ORG} hot fields.")


async def _backfill(db: Any, oid: str, field: FieldSpec) -> int:
    """Write the hot rows of *field* for each record that holds a value. The
    object row is locked, so no record write runs during it."""
    after, written = _ZERO_UUID, 0
    while True:
        page = (await db.execute(text(_BACKFILL_READ_SQL), {
            "oid": oid, "key": field.key, "after": after, "page": BACKFILL_PAGE})).all()
        if not page:
            return written
        rows = []
        for rid, value in page:
            row = hot_value(field, json.loads(value))
            if row is not None:
                rows.append({"r": str(rid), "t": row["t"], "n": row["n"], "ts": row["ts"],
                             "b": row["b"]})
        if rows:
            await db.execute(text(_BACKFILL_WRITE_SQL), {
                "oid": oid, "key": field.key,
                "rows": json.dumps(rows, ensure_ascii=False, allow_nan=False)})
        written += len(rows)
        after = str(page[-1][0])


async def set_field_hot(db: Any, object_id: str, key: str, hot: bool) -> bool:
    """Turn the field *key* of the object *object_id* hot, or not hot.

    A field that turns hot gets its hot rows for the records that exist, in
    the caller's transaction. Raises :class:`BudgetExceeded` past the hot
    budget, or when the object holds more than
    :data:`~acb_common.studio_fields.MAX_HOT_BACKFILL` records. A field that
    turns cold loses its hot rows. Answers False when the bound org holds no
    such object or field.
    """
    oid = _uuid(object_id, "object_id")
    if type(hot) is not bool:
        raise ObjectStoreError("hot must be true or false")
    await _lock_org(db)
    if (await db.execute(text(_LOCK_OBJECT_SQL), {"oid": oid})).first() is None:
        return False
    fields = await _fields(db, oid)
    current = next((f for f in fields if f.key == key), None)
    if current is None:
        return False
    if current.hot == hot:
        return True
    if not hot:
        await db.execute(text(_SET_HOT_SQL), {"oid": oid, "key": key, "hot": False})
        await db.execute(text(_UNHOT_SQL), {"oid": oid, "key": key})
        return True
    # The type rules of a hot field are the rules of the field spec.
    field = parse_field_spec({**_spec_of(current), "hot": True})
    _check_hot_room(sum(f.hot for f in fields), await _org_hot(db))
    count = (await db.execute(text(_RECORD_COUNT_SQL), {
        "oid": oid, "cap": MAX_HOT_BACKFILL + 1})).scalar_one()
    if int(count) > MAX_HOT_BACKFILL:
        raise BudgetExceeded(
            f"This object holds more than {MAX_HOT_BACKFILL} records, so a field cannot turn hot.")
    await db.execute(text(_SET_HOT_SQL), {"oid": oid, "key": key, "hot": True})
    await _backfill(db, oid, field)
    await db.execute(text(_TOUCH_OBJECT_SQL), {"oid": oid})
    return True


def _spec_of(field: FieldSpec) -> dict[str, Any]:
    return {**field.stored_spec(), "key": field.key, "type": field.type, "hot": field.hot}


async def drop_object(db: Any, object_id: str) -> bool:
    """Delete the object *object_id*, with its fields, records and hot rows (by
    cascade). Answers False when the bound org holds no object of the id."""
    oid = _uuid(object_id, "object_id")
    await _lock_org(db)
    return (await db.execute(text(_DROP_OBJECT_SQL), {"oid": oid})).first() is not None


async def _locked_fields(db: Any, oid: str) -> list[FieldSpec] | None:
    """Lock the object row for a record write, then read its fields. None when
    the bound org holds no object of the id."""
    if (await db.execute(text(_SHARE_OBJECT_SQL), {"oid": oid})).first() is None:
        return None
    # A second statement, so its snapshot follows the lock (READ COMMITTED).
    return await _fields(db, oid)


async def create_record(db: Any, actor: str, object_id: str,
                        data: Mapping[str, Any]) -> Record | None:
    """Create one record of the object *object_id*. Answers None when the
    bound org holds no object of the id.

    Each value is checked by ``studio_fields.validate_record``, and a bad one
    raises :class:`FieldError` with its field key. The record and its hot rows
    go in one statement.
    """
    who = _actor(actor)
    oid = _uuid(object_id, "object_id")
    fields = await _locked_fields(db, oid)
    if fields is None:
        return None
    clean = validate_record(fields, data)
    row = (await db.execute(text(_CREATE_RECORD_SQL), {
        "oid": oid, "data": record_json(clean), "actor": who,
        "hot": _hot_rows(fields, clean)})).first()
    return None if row is None else _record(row)


async def get_record(db: Any, object_id: str, record_id: str) -> Record | None:
    """The record *record_id* of the object *object_id*, or None."""
    row = (await db.execute(text(_GET_RECORD_SQL), {
        "oid": _uuid(object_id, "object_id"), "rid": _uuid(record_id, "record_id")})).first()
    return None if row is None else _record(row)


async def update_record(db: Any, actor: str, object_id: str, record_id: str,
                        changes: Mapping[str, Any]) -> Record | None:
    """Change the record *record_id* of the object *object_id*. Answers None
    when the bound org holds no such record.

    *changes* names the fields that change. A ``None`` value clears a field.
    The merged record is checked whole, so a required field stays filled.
    The record and its hot rows change in one statement.
    """
    who = _actor(actor)
    oid = _uuid(object_id, "object_id")
    rid = _uuid(record_id, "record_id")
    if not isinstance(changes, Mapping):
        raise ObjectStoreError("changes must be an object of field values")
    fields = await _locked_fields(db, oid)
    if fields is None:
        return None
    current = (await db.execute(text(_GET_RECORD_SQL + " FOR NO KEY UPDATE"), {
        "oid": oid, "rid": rid})).first()
    if current is None:
        return None
    merged = {**_json(current[2]), **dict(changes)}
    clean = validate_record(fields, merged)
    row = (await db.execute(text(_UPDATE_RECORD_SQL), {
        "oid": oid, "rid": rid, "data": record_json(clean), "actor": who,
        "hot": _hot_rows(fields, clean)})).first()
    return None if row is None else _record(row)


async def delete_record(db: Any, object_id: str, record_id: str) -> bool:
    """Delete the record *record_id* of the object *object_id*, and its hot
    rows by cascade. Answers False when the bound org holds no such record."""
    oid = _uuid(object_id, "object_id")
    rid = _uuid(record_id, "record_id")
    if await _locked_fields(db, oid) is None:
        return False
    return (await db.execute(text(_DELETE_RECORD_SQL), {
        "oid": oid, "rid": rid})).first() is not None

