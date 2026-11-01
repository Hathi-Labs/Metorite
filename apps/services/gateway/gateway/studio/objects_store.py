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
opens a transaction. So C1b can put several calls in one transaction. The
one SAVEPOINT is the query's (C2a, below), and it ends nothing of the
caller's transaction. A
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

**The locks, in one order.** Every write takes the advisory lock of the org
first, and then the row lock of the object. A registry change takes
``FOR UPDATE`` on the object row. A record write takes ``FOR KEY SHARE`` on it,
and then reads the fields. So a field that turns hot waits for each open
record write of the object, and a record write that starts later reads the
field as hot. The trigger ``studio_object_fields_hot_budget`` takes the
advisory lock on the same key. A read takes no lock.

The org lock is exclusive, so the writes of one org run one at a time, and
each holds the lock until its transaction ends. So two joined transactions
never wait for each other. A shared lock for record writes fails here. Two
transactions each hold it, and then each waits for the other before it can
take the exclusive lock of a registry change. That is a deadlock.
``test_studio_objects_r8.py`` runs the interleaving that deadlocked before.

**The reads.** :func:`get_record` reads one record by its id. The door (B2)
reads an object of one package by the name in its manifest
(:func:`get_object_by_name`). :func:`query_records` (C2a) reads a page of the
records of one object, with a filter and a sort. Each record function names
the package and the object. A statement finds a record only when the object
row of the bound org has that package. So a door of one package cannot read
or write a record of another package, also with a true object id.

**The query (C2a).** ``acb_common.studio_query`` holds the grammar: the
shape, the ops of each type and the caps. This module holds the SQL, because
it is the one module with SQL of the four tables. :func:`build_query` and
:func:`build_count` are pure, and they bind each field key and each value.
The SQL text takes a column name and an operator from a constant map only.
One statement reads a page and its total, so one snapshot serves both. The
candidates of each part are the hot rows of one hot condition, or the
records of the object by the records index. Each other condition tests the
candidate id. A condition that reads ``data`` runs after a lookup of the
record by its primary key, so it runs only on the candidates that the hot
conditions keep. With a filter on a field that is not hot, the guard
``hot_or_small`` counts those candidates in the same statement (at most
:data:`~acb_common.studio_query.SMALL_OBJECT`). A MATERIALIZED CTE then holds
them, and the count and the page read it. The statement also checks that
each field it reads as hot is still hot. If not, the store reads the fields
again and runs the query once more. The whole call has one deadline. Its
statements run in a SAVEPOINT, and each gets the time that is left as a LOCAL
``statement_timeout``. A timeout rolls back the SAVEPOINT only, so the
caller's transaction stays usable, and the old timeout comes back.
⚠️ ``v_num`` takes no index condition as the app role, because no ``numeric``
comparison is LEAKPROOF (``docs/rls_and_indexes.md``). So a number filter
reads the hot rows of its field. A sort on a number still reads the index in
order.

⚠️ The store does not authenticate. It only scopes. The actor of a write is
an argument that the caller takes from the session, never from request input
(R5, contract R11). The door (B2) checks the viewer.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from acb_common import studio_query
from acb_common.paging import page_line
from acb_common.studio_fields import (
    HOT_TEXT_CHARS,
    MAX_FIELDS_PER_OBJECT,
    MAX_HOT_BACKFILL,
    MAX_HOT_PER_OBJECT,
    MAX_HOT_PER_ORG,
    MAX_LABEL_CHARS,
    MAX_OBJECTS_PER_ORG,
    MAX_OBJECTS_PER_PACKAGE,
    NAME_PATTERN,
    FieldSpec,
    check_can_be_hot,
    hot_value,
    parse_field_spec,
    record_json,
    storable_text,
    stored_field,
    validate_record,
)
from acb_common.studio_query import Condition, Query, QueryError
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

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

#: Only a Studio package owns an object. An App Workshop app (``format`` is
#: ``code`` or NULL) owns none (STU-B2a, the gap of C1).
_PACKAGE_SQL = (
    "SELECT 1 FROM apps "
    f"WHERE organization_id = {_BOUND_ORG} AND id = CAST(:pid AS uuid) "
    "AND format = 'manifest'"
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

#: An object of one package by its name. The pair is unique in an org.
_OBJECT_BY_NAME_SQL = (
    f"SELECT {_OBJECT_COLUMNS} FROM studio_objects "
    f"WHERE organization_id = {_BOUND_ORG} AND package_id = CAST(:pid AS uuid) "
    "AND name = :name"
)

#: A registry change locks the object row, so it waits for each record write.
_LOCK_OBJECT_SQL = _GET_OBJECT_SQL + " FOR UPDATE"

#: A record write takes the weakest lock that a registry change waits for.
#: The foreign key check of the record takes the same lock. The object must be
#: of the package that the caller names.
_SHARE_OBJECT_SQL = (
    "SELECT id FROM studio_objects "
    f"WHERE organization_id = {_BOUND_ORG} AND id = CAST(:oid AS uuid) "
    "AND package_id = CAST(:pid AS uuid) FOR KEY SHARE"
)

#: The object of a record statement is of the package that the caller names.
_IN_PACKAGE = (
    "EXISTS (SELECT 1 FROM studio_objects o "
    f"WHERE o.organization_id = {_BOUND_ORG} AND o.id = CAST(:oid AS uuid) "
    "AND o.package_id = CAST(:pid AS uuid))"
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
    f"AND id = CAST(:rid AS uuid) AND {_IN_PACKAGE}"
)

#: The record and its hot rows, in one statement. The DELETE takes the hot
#: rows of the keys that hold no value now, and the INSERT writes the others.
#: The two touch different rows.
_UPDATE_RECORD_SQL = (
    "WITH r AS ("
    "UPDATE studio_records SET data = CAST(:data AS jsonb), updated_by = :actor, "
    "updated_at = now() "
    f"WHERE organization_id = {_BOUND_ORG} AND object_id = CAST(:oid AS uuid) "
    f"AND id = CAST(:rid AS uuid) AND {_IN_PACKAGE} "
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
    f"AND id = CAST(:rid AS uuid) AND {_IN_PACKAGE} RETURNING id"
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

    Answers None when the bound org holds no Studio package of the id. An App
    Workshop app is not a package, so it answers None too. Raises
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


async def get_object_by_name(db: Any, name: str, *, package_id: str) -> StudioObject | None:
    """The object *name* of the package *package_id* with its fields, or None.

    A manifest names an object by its name (``object:<name>.<op>``), and the
    door (B2) reads the id here. The name is unique in a package, so the
    answer is one object or None. An object of another package never answers.
    """
    pid = _uuid(package_id, "package_id")
    clean = _check_name(name)
    row = (await db.execute(text(_OBJECT_BY_NAME_SQL), {"pid": pid, "name": clean})).first()
    if row is None:
        return None
    return _object(row, await _fields(db, str(row[0])))


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
    # The hot rule only. The other rules of a spec bind a new spec, never a
    # stored one (see `studio_fields.stored_field`).
    check_can_be_hot(current)
    field = replace(current, hot=True)
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


async def drop_object(db: Any, object_id: str) -> bool:
    """Delete the object *object_id*, with its fields, records and hot rows (by
    cascade). Answers False when the bound org holds no object of the id."""
    oid = _uuid(object_id, "object_id")
    await _lock_org(db)
    return (await db.execute(text(_DROP_OBJECT_SQL), {"oid": oid})).first() is not None


async def _locked_fields(db: Any, oid: str, pid: str) -> list[FieldSpec] | None:
    """Take the org lock, then lock the object row for a record write, then
    read its fields. None when the bound org holds no object of the id in the
    package *pid*."""
    await _lock_org(db)
    if (await db.execute(text(_SHARE_OBJECT_SQL), {"oid": oid, "pid": pid})).first() is None:
        return None
    # A second statement, so its snapshot follows the lock (READ COMMITTED).
    return await _fields(db, oid)


async def create_record(db: Any, actor: str, object_id: str, data: Mapping[str, Any], *,
                        package_id: str) -> Record | None:
    """Create one record of the object *object_id* of the package *package_id*.
    Answers None when the bound org holds no such object in that package.

    Each value is checked by ``studio_fields.validate_record``, and a bad one
    raises :class:`FieldError` with its field key. The record and its hot rows
    go in one statement.
    """
    who = _actor(actor)
    oid = _uuid(object_id, "object_id")
    fields = await _locked_fields(db, oid, _uuid(package_id, "package_id"))
    if fields is None:
        return None
    clean = validate_record(fields, data)
    row = (await db.execute(text(_CREATE_RECORD_SQL), {
        "oid": oid, "data": record_json(clean), "actor": who,
        "hot": _hot_rows(fields, clean)})).first()
    return None if row is None else _record(row)


async def get_record(db: Any, object_id: str, record_id: str, *,
                     package_id: str) -> Record | None:
    """The record *record_id* of the object *object_id* of the package
    *package_id*, or None."""
    row = (await db.execute(text(_GET_RECORD_SQL), {
        "oid": _uuid(object_id, "object_id"), "rid": _uuid(record_id, "record_id"),
        "pid": _uuid(package_id, "package_id")})).first()
    return None if row is None else _record(row)


async def update_record(db: Any, actor: str, object_id: str, record_id: str,
                        changes: Mapping[str, Any], *, package_id: str) -> Record | None:
    """Change the record *record_id* of the object *object_id* of the package
    *package_id*. Answers None when the bound org holds no such record.

    *changes* names the fields that change. A ``None`` value clears a field.
    The merged record is checked whole, so a required field stays filled.
    The record and its hot rows change in one statement.
    """
    who = _actor(actor)
    oid = _uuid(object_id, "object_id")
    rid = _uuid(record_id, "record_id")
    pid = _uuid(package_id, "package_id")
    if not isinstance(changes, Mapping):
        raise ObjectStoreError("changes must be an object of field values")
    fields = await _locked_fields(db, oid, pid)
    if fields is None:
        return None
    current = (await db.execute(text(_GET_RECORD_SQL + " FOR NO KEY UPDATE"), {
        "oid": oid, "rid": rid, "pid": pid})).first()
    if current is None:
        return None
    merged = {**_json(current[2]), **dict(changes)}
    clean = validate_record(fields, merged)
    row = (await db.execute(text(_UPDATE_RECORD_SQL), {
        "oid": oid, "rid": rid, "pid": pid, "data": record_json(clean), "actor": who,
        "hot": _hot_rows(fields, clean)})).first()
    return None if row is None else _record(row)


async def delete_record(db: Any, object_id: str, record_id: str, *,
                        package_id: str) -> bool:
    """Delete the record *record_id* of the object *object_id* of the package
    *package_id*, and its hot rows by cascade. Answers False when the bound org
    holds no such record."""
    oid = _uuid(object_id, "object_id")
    rid = _uuid(record_id, "record_id")
    pid = _uuid(package_id, "package_id")
    if await _locked_fields(db, oid, pid) is None:
        return False
    return (await db.execute(text(_DELETE_RECORD_SQL), {
        "oid": oid, "rid": rid, "pid": pid})).first() is not None


# --- the query (C2a) --------------------------------------------------------
#
# ⚠️ An f-string below takes only an int index, a module constant, or a local
# that holds SQL built here. A field key and a value are always binds.
# `test_studio_query.py` reads the source of each `_q_*` and `build_*`
# function, and fails on any other name in an f-string.
#
# The shape of one page statement (one snapshot serves the page and its total):
#
#   v     the count of the fields that the query reads as hot and that are
#         hot now. A field that turned cold after the store read the fields
#         has lost its hot rows, so the store reads the fields again.
#   g     (a filter on a field that is not hot) the count of the candidates
#         that the hot conditions keep, up to one past SMALL_OBJECT. `m` reads
#         nothing when it passes SMALL_OBJECT, in the same snapshot.
#   t     the count of the records that match, up to the cap
#   p     the ids of the page: the candidates, then each condition, then the
#         order and the page. A sort reads the head (the records with a value,
#         in the order of the hot index) and the tail (the records with no
#         value, in the order of the id). `hc` counts the head, so the tail
#         reads only the rows that the page needs, and none when the head
#         fills the page.
#   r     each record of the page, by its primary key
#
# The candidates of a part are the hot rows of one condition (`d`), or the
# records of the object (`r0`). Each other condition tests the candidate id.
# A condition that reads `data` reads it after a lookup of the record by its
# key (`LATERAL ... OFFSET 0`), so it runs only on the candidates that the
# hot conditions keep. With a filter on a field that is not hot, the
# candidates go into the MATERIALIZED CTE `m` once, and the count and the
# page read `m` (at most SMALL_OBJECT records, by the guard).

#: The object of a query, of the package that the caller names. No lock.
_QUERY_OBJECT_SQL = (
    "SELECT id FROM studio_objects "
    f"WHERE organization_id = {_BOUND_ORG} AND id = CAST(:oid AS uuid) "
    "AND package_id = CAST(:pid AS uuid)"
)

#: The org and the object of each alias of the two record tables. One
#: constant each, so each part of a query names the same org and object.
_OF_ORG = f"organization_id = {_BOUND_ORG}"
_OF_OBJECT = "object_id = CAST(:oid AS uuid)"

#: The columns of a record, in the order that `_record` reads them.
_QUERY_COLUMNS = (
    "r.id::text, r.object_id::text, r.data, r.created_by, r.updated_by, "
    "r.created_at, r.updated_at"
)
_RECORD_ROW = "r.id, r.object_id, r.data, r.created_by, r.updated_by, r.created_at, r.updated_at"

#: The value column of `studio_record_hot` for each type of the op table.
_HOT_COLUMN: Mapping[str, str] = {
    "text": "v_text", "email": "v_text", "select": "v_text", "number": "v_num",
    "money": "v_num", "date": "v_ts", "datetime": "v_ts", "checkbox": "v_bool",
}
assert set(_HOT_COLUMN) == set(studio_query.OPS_BY_TYPE), "each type of the op table has a column"

#: The SQL type of each value column.
_SQL_TYPE: Mapping[str, str] = {
    "v_text": "text", "v_num": "numeric", "v_ts": "timestamptz", "v_bool": "boolean",
}

#: The comparison of each op that compares.
_CMP: Mapping[str, str] = {"eq": "=", "ne": "<>", "lt": "<", "lte": "<=", "gt": ">", "gte": ">="}

#: The count of the guard stops one past the limit, and `m` needs it at the limit.
_GUARD_CAP = "CAST(:small AS bigint) + 1"
_GUARD_GATE = "(SELECT g.n FROM g) <= CAST(:small AS bigint)"

#: The fields that the query reads as hot and that are hot now.
_STILL_HOT_SQL = (
    f"SELECT count(*) AS n FROM studio_object_fields f WHERE f.{_OF_ORG} AND f.{_OF_OBJECT} "
    "AND f.hot AND f.key = ANY(CAST(:hk AS text[]))"
)

_TIMEOUT_READ_SQL = "SELECT current_setting('statement_timeout')"
#: ``SET LOCAL`` as a function, so the value is a bind.
_TIMEOUT_SQL = "SELECT set_config('statement_timeout', :ms, true)"
#: The SQLSTATE of a statement that the timeout cancelled.
_QUERY_CANCELED = "57014"
_TIMEOUT_MESSAGE = "The query took too long. Narrow the filter."
_CHANGED_MESSAGE = "The fields of this object changed during the query. Try again."

#: The clock of the deadline. A test replaces it.
_clock = time.monotonic


@dataclass(frozen=True)
class RecordPage:
    """One page of the records of a query (C2a)."""

    records: tuple[Record, ...]
    #: The count of the records that match, when it is below
    #: :data:`~acb_common.studio_query.TOTAL_CAP`. Else None.
    total: int | None
    #: :data:`~acb_common.studio_query.TOTAL_CAP` when the count reached it.
    #: Else None.
    total_at_least: int | None
    offset: int
    limit: int
    #: The line of ``paging.page_line``: "" when the page holds everything.
    line: str


def _needs_row(cond: Condition) -> bool:
    """True when the SQL of *cond* reads ``data``: each condition on a field
    that is not hot, ``contains``, and a text of 256 characters or more."""
    if not cond.field.hot:
        return True
    if cond.op == "contains":
        return True
    if _HOT_COLUMN[cond.field.type] == "v_text" and cond.op in ("eq", "ne"):
        return len(cond.value) >= HOT_TEXT_CHARS
    if _HOT_COLUMN[cond.field.type] == "v_text" and cond.op == "in":
        return any(len(v) >= HOT_TEXT_CHARS for v in cond.value)
    return False


def _drive_rank(cond: Condition) -> int | None:
    """How well the hot rows of *cond* serve as the candidates: 0 for an
    index condition (a text match, a time), 1 for a scan of the field, 2 for
    any value, 3 for ``ne`` (it keeps most rows). None when *cond* cannot
    give the candidates."""
    if not cond.field.hot or _needs_row(cond):
        return None
    if cond.op == "is_empty":
        return None if cond.value else 2
    if cond.op == "ne":
        return 3
    if cond.field.type == "checkbox" and not cond.value:
        return None  # it matches a record with no hot row
    col = _HOT_COLUMN[cond.field.type]
    if col == "v_ts" or (col == "v_text" and cond.op in ("eq", "in")):
        return 0
    return 1


def _q_hot_row(i: int, rid: str) -> str:
    """The hot row of the field of condition *i*, for the record id *rid*."""
    return (
        f"SELECT 1 FROM studio_record_hot h{i} "
        f"WHERE h{i}.{_OF_ORG} AND h{i}.{_OF_OBJECT} "
        f"AND h{i}.field_key = CAST(:fk{i} AS text) AND h{i}.record_id = {rid}"
    )


def _q_data(kind: str, i: int) -> str:
    """The value of a field that is not hot, read from ``data`` with the
    bound key of condition *i*, in the SQL type of its hot column."""
    kref = f"CAST(:fk{i} AS text)"
    if kind in ("text", "email", "select"):
        return f"(r.data ->> {kref})"
    if kind == "number":
        return (f"(CASE WHEN jsonb_typeof(r.data -> {kref}) = 'number' "
                f"THEN CAST(r.data ->> {kref} AS numeric) END)")
    if kind == "money":
        return (f"(CASE WHEN jsonb_typeof(r.data -> {kref} -> 'minor') = 'number' "
                f"THEN CAST(r.data -> {kref} ->> 'minor' AS numeric) END)")
    if kind == "date":
        return (f"(CASE WHEN jsonb_typeof(r.data -> {kref}) = 'string' "
                f"THEN CAST((r.data ->> {kref}) || 'T00:00:00Z' AS timestamptz) END)")
    if kind == "datetime":
        return (f"(CASE WHEN jsonb_typeof(r.data -> {kref}) = 'string' "
                f"THEN CAST(r.data ->> {kref} AS timestamptz) END)")
    return (f"(CASE WHEN jsonb_typeof(r.data -> {kref}) = 'boolean' "
            f"THEN CAST(r.data ->> {kref} AS boolean) END)")


def _q_compare(expr: str, i: int, cond: Condition, params: dict[str, Any]) -> str:
    """A comparison of *expr* with the value of condition *i*."""
    kind = _SQL_TYPE[_HOT_COLUMN[cond.field.type]]
    if cond.op == "in":
        params[f"v{i}"] = list(cond.value)
        return f"{expr} = ANY(CAST(:v{i} AS {kind}[]))"
    params[f"v{i}"] = cond.value
    if cond.until is not None:  # eq on a day
        params[f"u{i}"] = cond.until
        return f"{expr} >= CAST(:v{i} AS {kind}) AND {expr} < CAST(:u{i} AS {kind})"
    cmp = _CMP[cond.op]
    return f"{expr} {cmp} CAST(:v{i} AS {kind})"


def _q_text_eq(h: str, i: int, value: str, params: dict[str, Any]) -> str:
    """Text eq on the hot row *h*. ``v_text`` holds the first 256 characters,
    so a value of 256 or more is checked against ``data`` too."""
    params[f"v{i}"] = value
    if len(value) < HOT_TEXT_CHARS:
        return f"{h}.v_text = CAST(:v{i} AS text)"
    params[f"p{i}"] = value[:HOT_TEXT_CHARS]
    return (f"{h}.v_text = CAST(:p{i} AS text) "
            f"AND r.data ->> CAST(:fk{i} AS text) = CAST(:v{i} AS text)")


def _q_text_in(h: str, i: int, values: Sequence[str], params: dict[str, Any]) -> str:
    """Text ``in`` on the hot row *h*, with the check of :func:`_q_text_eq`."""
    longs = [v for v in values if len(v) >= HOT_TEXT_CHARS]
    params[f"v{i}"] = [v for v in values if len(v) < HOT_TEXT_CHARS]
    frag = f"{h}.v_text = ANY(CAST(:v{i} AS text[]))"
    if not longs:
        return frag
    params[f"p{i}"] = [v[:HOT_TEXT_CHARS] for v in longs]
    params[f"l{i}"] = longs
    return (f"({frag} OR ({h}.v_text = ANY(CAST(:p{i} AS text[])) "
            f"AND r.data ->> CAST(:fk{i} AS text) = ANY(CAST(:l{i} AS text[]))))")


def _q_contains(h: str, i: int, pattern: str, params: dict[str, Any]) -> str:
    """Text ``contains`` on the hot row *h*. A ``v_text`` of 256 characters
    may hold a cut text, so the test then reads ``data``."""
    params[f"v{i}"] = pattern
    return (f"((char_length({h}.v_text) < {HOT_TEXT_CHARS} "
            f"AND {h}.v_text ILIKE CAST(:v{i} AS text) ESCAPE '\\') "
            f"OR (char_length({h}.v_text) = {HOT_TEXT_CHARS} "
            f"AND r.data ->> CAST(:fk{i} AS text) ILIKE CAST(:v{i} AS text) ESCAPE '\\'))")


def _q_frag(h: str, i: int, cond: Condition, params: dict[str, Any]) -> str:
    """The test of the value of condition *i* on the hot row *h*."""
    col = _HOT_COLUMN[cond.field.type]
    if col == "v_text" and cond.op == "eq":
        return _q_text_eq(h, i, cond.value, params)
    if col == "v_text" and cond.op == "ne":
        return f"NOT ({_q_text_eq(h, i, cond.value, params)})"
    if col == "v_text" and cond.op == "in":
        return _q_text_in(h, i, cond.value, params)
    if cond.op == "contains":
        return _q_contains(h, i, cond.value, params)
    hcol = f"{h}.{col}"
    return _q_compare(hcol, i, cond, params)


def _q_hot(i: int, cond: Condition, params: dict[str, Any], rid: str) -> str:
    """Condition *i* on a hot field: a test of its hot row ``h<i>``. A record
    with no value has no hot row, so only ``is_empty`` matches it."""
    params[f"fk{i}"] = cond.field.key
    row = _q_hot_row(i, rid)
    if cond.op == "is_empty":
        return f"NOT EXISTS ({row})" if cond.value else f"EXISTS ({row})"
    if cond.field.type == "checkbox" and not cond.value:
        # Not ticked: no ticked hot row, so a record with no value matches too.
        params[f"v{i}"] = True
        return f"NOT EXISTS ({row} AND h{i}.v_bool = CAST(:v{i} AS boolean))"
    frag = _q_frag(f"h{i}", i, cond, params)
    return f"EXISTS ({row} AND {frag})"


def _q_cold(i: int, cond: Condition, params: dict[str, Any]) -> str:
    """Condition *i* on a field that is not hot: a test of ``data``. A missing
    value reads as NULL, so only ``is_empty`` matches it."""
    params[f"fk{i}"] = cond.field.key
    if cond.op == "is_empty":
        if cond.value:
            return f"(r.data -> CAST(:fk{i} AS text)) IS NULL"
        return f"(r.data -> CAST(:fk{i} AS text)) IS NOT NULL"
    expr = _q_data(cond.field.type, i)
    if cond.op == "contains":
        params[f"v{i}"] = cond.value
        return f"{expr} ILIKE CAST(:v{i} AS text) ESCAPE '\\'"
    if cond.field.type == "checkbox" and not cond.value:
        return f"{expr} IS NOT TRUE"  # false, or no value
    return _q_compare(expr, i, cond, params)


def _q_lookup(rid: str) -> str:
    """The record of the id *rid*, by its primary key. ``OFFSET 0`` keeps it
    a lookup for each candidate, so the planner cannot scan the table."""
    return (f"CROSS JOIN LATERAL (SELECT {_RECORD_ROW} FROM studio_records r "
            f"WHERE r.{_OF_ORG} AND r.{_OF_OBJECT} AND r.id = {rid} OFFSET 0) r")


def _q_where(conditions: Sequence[Condition], params: dict[str, Any], rid: str,
             skip: int | None = None, only_hot: bool = False) -> str:
    """Each condition, with its index in *conditions*, so one statement holds
    one bind for each condition. *only_hot* leaves out a field that is not hot."""
    parts = [_IN_PACKAGE]
    for i, cond in enumerate(conditions):
        if i == skip or (only_hot and not cond.field.hot):
            continue
        parts.append(_q_hot(i, cond, params, rid) if cond.field.hot else _q_cold(i, cond, params))
    return " AND ".join(parts)


def _q_source(conditions: Sequence[Condition], params: dict[str, Any]) -> tuple[str, int | None]:
    """The candidates ``c`` of a part, and the condition that they meet
    already: the hot rows of the best condition, or the records of the object."""
    ranked = sorted((rank, j) for j, cond in enumerate(conditions)
                    if (rank := _drive_rank(cond)) is not None)
    if not ranked:
        # In the order of the id: the records index `(org, object, id)` gives
        # it, so a LIMIT stops on the index and no plan scans the table.
        return (f"(SELECT r0.id FROM studio_records r0 "
                f"WHERE r0.{_OF_ORG} AND r0.{_OF_OBJECT} ORDER BY r0.id) c"), None
    j = ranked[0][1]
    cond = conditions[j]
    params[f"fk{j}"] = cond.field.key
    frag = "TRUE" if cond.op == "is_empty" else _q_frag("d", j, cond, params)
    return (f"(SELECT d.record_id AS id FROM studio_record_hot d "
            f"WHERE d.{_OF_ORG} AND d.{_OF_OBJECT} AND d.field_key = CAST(:fk{j} AS text) "
            f"AND {frag}) c"), j


def _q_filtered(conditions: Sequence[Condition], params: dict[str, Any], what: str,
                only_hot: bool = False, gate: str = "TRUE") -> str:
    """``SELECT what`` from the candidates ``c`` that meet each condition.
    *gate* is a test of the whole statement, as the guard of ``m``."""
    source, skip = _q_source(conditions, params)
    used = [c for c in conditions if c.field.hot or not only_hot]
    lookup = f" {_q_lookup('c.id')}" if any(_needs_row(c) for c in used) else ""
    where = _q_where(conditions, params, "c.id", skip, only_hot)
    return f"SELECT {what} FROM {source}{lookup} WHERE {gate} AND {where}"


def _q_count(conditions: Sequence[Condition], params: dict[str, Any], cap: str,
             only_hot: bool = False) -> str:
    """The count of the candidates that meet each condition, up to *cap*."""
    counted = _q_filtered(conditions, params, "1", only_hot)
    return f"SELECT count(*) AS n FROM ({counted} LIMIT {cap}) x"


def _q_head(query: Query, params: dict[str, Any], what: str) -> str:
    """``SELECT what`` from the records with a value of the sort field that
    meet each condition, driven by the hot rows ``s`` of the sort field."""
    assert query.sort is not None
    params["sk"] = query.sort.field.key
    lookup = f" {_q_lookup('s.record_id')}" if any(_needs_row(c) for c in query.conditions) else ""
    where = _q_where(query.conditions, params, "s.record_id")
    return (f"SELECT {what} FROM studio_record_hot s{lookup} "
            f"WHERE s.{_OF_ORG} AND s.{_OF_OBJECT} AND s.field_key = CAST(:sk AS text) "
            f"AND {where}")


def _q_sorted_ids(query: Query, params: dict[str, Any]) -> str:
    """The ids of the page of a sort, as the head and then the tail. ``hc``
    is the count of the head up to the end of the page."""
    assert query.sort is not None
    col = _HOT_COLUMN[query.sort.field.type]
    kind = _SQL_TYPE[col]
    dirn = " DESC" if query.sort.desc else ""
    head = _q_head(query, params, f"0 AS part, s.{col} AS k, s.record_id AS id")
    no_value = Condition(query.sort.field, "is_empty", True)
    tail = _q_filtered((*query.conditions, no_value), params,
                       f"1 AS part, CAST(NULL AS {kind}) AS k, c.id AS id")
    return (f"({head} ORDER BY s.{col}{dirn}, s.record_id{dirn} "
            "LIMIT :lim OFFSET :off) "
            f"UNION ALL ({tail} ORDER BY c.id "
            "LIMIT (SELECT greatest(0, CAST(:lim AS bigint) "
            "- least(CAST(:lim AS bigint), greatest(0, hc.n - :off))) FROM hc) "
            "OFFSET (SELECT greatest(0, CAST(:off AS bigint) - hc.n) FROM hc))")


def _q_small_ids(query: Query, params: dict[str, Any]) -> str:
    """The ids of the page from ``m``, the candidates of a filter on a field
    that is not hot. ``m`` holds at most SMALL_OBJECT records, so the order
    is a sort in memory."""
    if query.sort is None:
        return ("SELECT 0 AS part, CAST(NULL AS text) AS k, m.id AS id FROM m "
                "ORDER BY m.id LIMIT :lim OFFSET :off")
    params["sk"] = query.sort.field.key
    col = _HOT_COLUMN[query.sort.field.type]
    dirn = " DESC" if query.sort.desc else ""
    return ("SELECT CASE WHEN s.k IS NULL THEN 1 ELSE 0 END AS part, s.k AS k, m.id AS id "
            f"FROM m LEFT JOIN LATERAL (SELECT s.{col} AS k FROM studio_record_hot s "
            f"WHERE s.{_OF_ORG} AND s.{_OF_OBJECT} AND s.field_key = CAST(:sk AS text) "
            "AND s.record_id = m.id OFFSET 0) s ON true "
            f"ORDER BY 1, 2{dirn}, (CASE WHEN s.k IS NULL THEN NULL ELSE m.id END){dirn}, 3 "
            "LIMIT :lim OFFSET :off")


def build_query(query: Query, *, object_id: str, package_id: str, limit: int,
                offset: int) -> tuple[str, dict[str, Any]]:
    """The one statement of a page of *query*: its SQL and its binds. Pure.

    Each row holds the total (``t.n``), the count of the guard (``g.n``, or
    NULL with no filter on a field that is not hot), the count of the fields
    that are still hot (``v.n``) and one record. A page past the end gives one
    row with the counts and no record. With no sort, the records
    come in the order of their id. With a sort, the records with a value come
    first, in the order of the hot index and then the id, both in the
    direction of the sort. The records with no value come after, in the
    ascending order of their id.
    """
    params: dict[str, Any] = {"oid": object_id, "pid": package_id, "lim": limit,
                              "off": offset, "cap": studio_query.TOTAL_CAP,
                              "hk": list(query.hot_keys())}
    dirn = " DESC" if query.sort is not None and query.sort.desc else ""
    guard = "CAST(NULL AS bigint)"
    if query.cold():
        params["small"] = studio_query.SMALL_OBJECT
        count_hot = _q_count(query.conditions, params, _GUARD_CAP, only_hot=True)
        found = _q_filtered(query.conditions, params, "c.id AS id", gate=_GUARD_GATE)
        with_sql = f"WITH g AS ({count_hot}), m AS MATERIALIZED ({found}), "
        guard = "(SELECT g.n FROM g)"
        total = "SELECT count(*) AS n FROM (SELECT 1 FROM m LIMIT :cap) x"
        ids = _q_small_ids(query, params)
    elif query.sort is not None:
        count_head = _q_head(query, params, "1")
        with_sql = ("WITH hc AS MATERIALIZED (SELECT count(*) AS n FROM "
                    f"({count_head} LIMIT CAST(:lim AS bigint) + CAST(:off AS bigint)) x), ")
        total = _q_count(query.conditions, params, ":cap")
        ids = _q_sorted_ids(query, params)
    else:
        with_sql = "WITH "
        total = _q_count(query.conditions, params, ":cap")
        page = _q_filtered(query.conditions, params,
                           "0 AS part, CAST(NULL AS text) AS k, c.id AS id")
        ids = f"{page} ORDER BY c.id LIMIT :lim OFFSET :off"
    return (f"{with_sql}v AS ({_STILL_HOT_SQL}), t AS ({total}), p AS ({ids}) "
            f"SELECT t.n, {guard}, v.n, {_QUERY_COLUMNS} FROM t CROSS JOIN v "
            f"LEFT JOIN (p {_q_lookup('p.id')}) ON true "
            f"ORDER BY p.part, p.k{dirn}, (CASE WHEN p.part = 0 THEN p.id END){dirn}, p.id"), params


def build_count(conditions: Sequence[Condition], *, object_id: str, package_id: str,
                cap: int) -> tuple[str, dict[str, Any]]:
    """The count of the records that meet *conditions*, up to *cap*, as the
    CTE ``t`` of :func:`build_query` reads it. Pure."""
    params: dict[str, Any] = {"oid": object_id, "pid": package_id, "cap": cap}
    return _q_count(conditions, params, ":cap"), params


def _is_timeout(exc: DBAPIError) -> bool:
    """True when the LOCAL ``statement_timeout`` cancelled the statement."""
    orig = getattr(exc, "orig", None)
    code = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    return code == _QUERY_CANCELED


class _Timed:
    """The session of a query, under one deadline. Before each statement it
    sets the LOCAL ``statement_timeout`` to the time that is left, and it
    refuses with ``query_timeout`` when none is left."""

    def __init__(self, db: Any, deadline: float) -> None:
        self._db = db
        self._deadline = deadline

    async def execute(self, stmt: Any, params: dict[str, Any]) -> Any:
        left = self._deadline - _clock()
        if left <= 0:
            raise QueryError(studio_query.TIMEOUT, _TIMEOUT_MESSAGE)
        await self._db.execute(text(_TIMEOUT_SQL), {"ms": str(max(1, int(left * 1000)))})
        return await self._db.execute(stmt, params)


@asynccontextmanager
async def _time_limit(db: Any, deadline: float) -> AsyncIterator[_Timed]:
    """Run the statements of a query in a SAVEPOINT, under one deadline.

    A timeout aborts the SAVEPOINT only, so the caller's transaction stays
    usable (C1b joins several store calls in one). The old timeout comes back
    after each query, also after a success.
    """
    old = str((await db.execute(text(_TIMEOUT_READ_SQL))).scalar_one())
    try:
        async with db.begin_nested():
            yield _Timed(db, deadline)
    except DBAPIError as exc:
        if _is_timeout(exc):
            raise QueryError(studio_query.TIMEOUT, _TIMEOUT_MESSAGE) from None
        raise
    finally:
        await db.execute(text(_TIMEOUT_SQL), {"ms": old})


def _check_hot_or_small(rows: Sequence[Any], query: Query) -> None:
    """``hot_or_small``: a filter on a field that is not hot reads ``data``,
    so it runs only when the hot filters leave at most
    :data:`~acb_common.studio_query.SMALL_OBJECT` records. The page statement
    holds the count, so a write between two statements cannot pass the guard."""
    guard, small = rows[0][1], studio_query.SMALL_OBJECT
    if guard is not None and int(guard) > small:
        key = query.cold()[0].field.key
        raise QueryError(
            studio_query.NOT_INDEXED,
            f"Field `{key}` is not hot, and the filter leaves more than {small} records to "
            "check. Turn it hot, or add a filter on a hot field.", key)


async def _read_page(timed: _Timed, oid: str, pid: str, filter: Any, sort: Any, size: int,
                     start: int) -> list[Any]:
    """The fields, then the page statement. A field that turned cold after
    the store read the fields has lost its hot rows, so the statement says so,
    and the store reads the fields and runs the query once more."""
    for _ in range(2):
        query = studio_query.parse_query(await _fields(timed, oid), filter, sort)
        sql, params = build_query(query, object_id=oid, package_id=pid, limit=size, offset=start)
        rows = (await timed.execute(text(sql), params)).all()
        if int(rows[0][2]) == len(query.hot_keys()):
            _check_hot_or_small(rows, query)
            return rows
    raise QueryError(studio_query.TIMEOUT, _CHANGED_MESSAGE)


async def query_records(db: Any, *, package_id: str, object_id: str, filter: Any = None,
                        sort: Any = None, limit: Any = None,
                        offset: Any = None) -> RecordPage | None:
    """One page of the records of the object *object_id* of the package
    *package_id*, with a filter and a sort (C2a). Answers None when the bound
    org holds no such object in that package.

    ``acb_common.studio_query`` checks the input. A refusal raises
    :class:`~acb_common.studio_query.QueryError`, and the session stays
    usable. The whole call has one deadline of
    :data:`~acb_common.studio_query.TIMEOUT_MS`. One statement reads the
    page, its total and the guard, so a write between two statements cannot
    put a record on the page twice or pass the guard. If a field turns cold
    twice during the call, the store raises ``query_timeout``, and the caller
    may try again. A record with no value of the sort field comes
    after the records with a value. The total is exact below
    :data:`~acb_common.studio_query.TOTAL_CAP`.
    """
    deadline = _clock() + studio_query.TIMEOUT_MS / 1000
    oid = _uuid(object_id, "object_id")
    pid = _uuid(package_id, "package_id")
    start, size = studio_query.page_bounds(offset, limit)
    studio_query.check_shape(filter, sort)
    async with _time_limit(db, deadline) as timed:
        found = await timed.execute(text(_QUERY_OBJECT_SQL), {"oid": oid, "pid": pid})
        if found.first() is None:
            return None
        rows = await _read_page(timed, oid, pid, filter, sort, size, start)
    count = int(rows[0][0])
    records = tuple(_record(row[3:]) for row in rows if row[3] is not None)
    exact = count < studio_query.TOTAL_CAP
    shown = len(records)
    reach = studio_query.MAX_OFFSET
    if exact:
        line = page_line(count, start, shown, noun="records", reach=reach)
    else:
        line = page_line(None, start, shown, more=shown == size, noun="records", reach=reach)
    return RecordPage(records=records, total=count if exact else None,
                      total_at_least=None if exact else count, offset=start, limit=size,
                      line=line)
