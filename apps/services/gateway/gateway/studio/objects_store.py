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
opens a transaction. A query (C2a) and a batch (C1b) each open a SAVEPOINT,
and it ends nothing of the caller's transaction. A
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

**The wait for the org lock is bounded.** A waiter holds a pool connection for
the whole wait, so the writers of one org can delay each other tenant.
:func:`_lock_org` waits at most :data:`ORG_LOCK_WAIT_MS`, for every caller.
Past that it raises :class:`StoreBusy`, in its own SAVEPOINT, so the caller's
transaction stays usable. The old ``lock_timeout`` comes back as soon as the
lock is held, so the bound covers the wait only. The bound limits the delay and
does not end it, so B2b admits a bounded number of writers for each org.

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

**The aggregate (C2b).** :func:`aggregate_records` groups the records of one
object by one hot field and takes a count, a sum or an average of each group.
``studio_query.parse_aggregate`` checks the input, and :func:`build_aggregate`
is pure. It reuses the candidates, the guard, the deadline and the check of the
hot fields of C2a, in one statement. The candidates come in no order, because
an order with no LIMIT made the planner sort the whole object. Each candidate
reads the hot row of the group field and of each measure field by its key.
The 50 groups with the most records stay, and one row ``other`` holds the rest.
A money sum reads the currency of each amount from ``data`` in the same
statement, and two currencies refuse the sum.

**The batch (C1b).** :func:`apply_writes` is the one path of a record write.
:func:`create_record`, :func:`update_record` and :func:`delete_record` are a
batch of one, with the same signatures and answers, so no single call skips
a rule. The lock order is the org lock once, then the object rows in
ascending object id (``FOR KEY SHARE``), then the record rows in ascending
``(object_id, record_id)`` (``FOR NO KEY UPDATE``). The store then applies
the writes in list order. The checks run after all the writes, so a stock
move is judged on the net. ``range`` runs on the last value of each written
record. A ``state_machine`` (C6) judges the net move of each record, from
its data before the batch to its data after it, with no SQL. A create takes
the ``initial`` state when the field is absent. ``unique`` and
``group_sum_min`` run once for each key that
``studio_rules.touched_keys`` names. The whole batch runs in one SAVEPOINT.
Any error rolls back the SAVEPOINT and raises :class:`BatchError`, so the
caller's transaction stays usable with its earlier work.

**The rules (C1b).** ``acb_common.studio_rules`` holds the grammar, and
``studio_objects.constraints`` holds the rules of an object.
:func:`set_constraints` replaces them, under the org lock and then the
``FOR UPDATE`` of the object row, in that order. A declaration does not check
old data. A rule binds each later write, and a ``unique`` key that clashes
already is refused only when a later write touches it. A field that a rule
holds cannot turn cold while the rule stands. A rule that the store cannot
read fails closed: each write of its object is refused.

**The formulas (C7a).** A rule of kind ``formula`` fills one field, its target,
from the other fields of the same record (``acb_common/studio_expr.py`` holds the
closed tree, and ``studio_rules.plan_formulas`` checks the formulas of an object
as a set). :func:`_lock_objects` builds the plan once for each object of a batch
that holds a create or an update (a delete computes nothing),
and a stored formula that does not fit refuses each write. :func:`_apply_write`
refuses a key for a target (also with the same value), validates the record
without its targets, and then runs each formula in order of dependency. So
``mark.after`` and the hot rows hold the stored result, and a ``range``, a
``unique``, a ``group_sum_min`` and a guard of C6 read it. A division by zero, a
currency mismatch or a result that is too large raises
:class:`~acb_common.studio_rules.FormulaError`, and the batch writes nothing. A
declaration of a new formula, or of one with a changed target, expression, scale,
rounding or currency, on an object that holds records is
refused (:func:`_check_formulas_first`), because there is no backfill. A removed
formula leaves its stored values as plain values.

**The roll-ups (C7b).** A rule of kind ``rollup`` fills a field of a parent record
with the sum, count, min or max of a hot field of the child records of another
object of the same package, found through a hot relation field of the child
(``acb_common/studio_rules.py`` holds the grammar and the pure checks). The
store recomputes each stale parent after all the writes of the batch, in the
same SAVEPOINT and under the org lock, so a read is exact. :func:`_find_rollups`
finds the parent objects from the package rules after the org lock, and
:func:`_lock_objects` locks them with the others in ascending id. The parent
records follow the child records, ``FOR NO KEY UPDATE`` in ascending id.
:func:`_recompute_parents` runs one aggregate (:func:`build_rollup`) for each
parent and each roll-up, and :func:`_derive_parent` writes the parent: the
formulas run again, the record is validated, and the hot rows are rebuilt. The
rules of the parent run on the derived write too, the key rules included. At
most :data:`~acb_common.studio_rules.MAX_ROLLUP_PARENTS` parents and
:data:`~acb_common.studio_rules.MAX_ROLLUP_STATEMENTS` aggregates run for each
batch, under the one deadline of the batch. A parent id that names no record is
skipped. A child that a money roll-up counts, in another currency than the rule,
is refused. A roll-up that does not fit now, or that the store cannot read,
refuses the batch.

**The lines (C13).** A relation field of a parent with ``lines`` names a line
object of the same package and its field ``via`` that points back. The parent
holds no value for it (``studio_fields.validate_record``). A write with
``parent`` names the create or the update of its parent earlier in the batch:
a linked create gets ``via`` from that record, and a linked update must be a
line of it. A line that names a parent by itself, on a create or a move, must
name a record of the parent object of this package, in one statement after
the writes. A record of another package or another org is not found, so the
line is refused. :func:`_find_lines` reads the relations after the org lock,
and a relation that no longer fits its line object refuses the batch.

**The hierarchy (C18).** A rule of kind ``hierarchy`` makes the records of one
object a tree, through one hot relation field that points to the same object.
After the writes of a batch, :func:`_check_trees` walks up and down from each
record that got a new parent, in one statement for each object. It refuses a
cycle, a parent that is no record of this object, and a tree of more than
:data:`~acb_common.studio_rules.MAX_TREE_DEPTH` levels. :func:`read_tree` reads
a tree or the whole forest, to that depth, in one recursive statement.
``tests/unit/test_studio_hierarchy_r8.py`` runs both on a real Postgres.

**The sync of a manifest (A5).** :func:`sync_objects` brings the rows of one
package to the ``objects`` of a published manifest, and the publish route is
its one caller. It adds an object (:func:`define_object`, whose one caller it
is), adds a field, flips ``hot``, and brings each rules list to the manifest
through :func:`set_constraints`. The phases run in this order: rules pass 1
(only the stored rules that stay), fields that turn cold, new objects, new
fields, fields that turn hot, and rules pass 2 (the full lists). So a rule
that goes is gone before its field turns cold or a roll-up checks its
source, and a swap of hot fields at a cap passes, also when a rule moves
with it. It builds the whole plan from reads, then writes in a SAVEPOINT. An
object that goes, a field that goes or changes, a new required field on an
object with records, and each refusal of a store call (a rule included) raise
:class:`ObjectsOutOfSync` with a list of :class:`SyncProblem`. A refusal of a
field step names the field. ⚠️ The lock
order: the caller holds the ``apps`` row ``FOR UPDATE``, and the sync takes
the org lock after it. :func:`list_objects` reads the objects of one package
in two statements.

**The delete of a package (A5c-1).** ``packages_store.delete_package`` owns
it, in the same lock order: the ``apps`` row ``FOR UPDATE``, then
:func:`lock_org`. :func:`purge_records` deletes the records of the package
in chunks of :data:`PURGE_CHUNK` under the 5 s deadline, and the cascade
from ``apps`` deletes the objects, the fields and each hot row that is left.
:func:`package_counts` counts the objects, the fields and the records (up to
:data:`COUNT_CAP` for each object) for the preview.

**On a floor of 0 or less, a write that does not lower a group never reads
the group.** The store adds up the net change of the batch on each key of
``group_sum_min``, in exact ``Decimal``, from the values that it read under
its locks. With a net change of zero or more, it skips the sum. The org lock
makes that safe, and a group with no rows starts at a sum of 0, which meets
the floor. So a receipt on a broken key passes, and a repair is possible. A
floor above 0 always reads its group, because a new group starts below it. A
key with no rows passes, because a group that no longer exists holds nothing
to break. A key with rows and no amount sums to 0, and the floor judges it.

**Why the checks are correct.** Under READ COMMITTED, a check and a write of
two transactions can pass each other, unless the writers run one at a time.
Every record write takes the exclusive org lock first, and holds it to the
end of its transaction. Each check runs in a later statement, so its new
snapshot holds each earlier committed write of the org. A database unique
index cannot hold these rules, because no tenant gets DDL. A check reuses the
builders of C2a, so it reads the hot rows only, under the deadline of
``_time_limit``.

⚠️ **The known limit.** The org lock serializes all record writes of one
org, also the writes of an object with no rule. A finer lock is a later
slice. A writer that skips the store skips the rules: raw SQL, and the
cascade of a package hard delete. A write that lowers a group past about
500,000 rows can reach ``query_timeout``, and so can each write on a floor
above 0. A running balance table is a later slice.

**The row rules (C8a).** ``studio_objects.access`` holds the grants of an
object (``acb_common/studio_access.py`` holds the grammar), and the sync of a
publish writes it, so a rule binds the published manifest and never the
draft. Each of :func:`get_record`, :func:`query_records`,
:func:`apply_writes`, :func:`create_record`, :func:`update_record` and
:func:`delete_record` takes a REQUIRED keyword ``viewer``. The store reads the
owner of the package from its ``apps`` row: the owner passes every rule, and
so does each viewer of an object with no ``access``. Else the scope of the
viewer's roles binds. A row rule joins the one statement as one ``EXISTS`` on
the hot rows of the person fields (:func:`_q_access`). A hidden key leaves
each answer, a filter or a sort on it is refused, and a write that names it is
refused. A change or a delete of a record that the viewer cannot read answers
"not found", and a write must leave a record that the viewer can read. A
formula target that reads a hidden key is hidden too, and so is a roll-up
target over a source that the viewer cannot read whole. The text of a rule
that reads a hidden key, or of a key rule over records that the viewer cannot
read, names no field (:func:`_masked`). The parent of a line must be a record
that the viewer can read. ``test_studio_row_rules.py`` fails each call of the
six that passes no viewer. ⚠️ :func:`aggregate_records` and :func:`read_tree` (C18)
take no viewer yet, and no route calls them. A door of either must add one first.

⚠️ The store does not authenticate. It only scopes. The actor of a write is
an argument that the caller takes from the session, never from request input
(R5, contract R11). The door (B2) checks the package, and the viewer's email
comes from the session too.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from dataclasses import field as dc_field
from datetime import datetime
from decimal import Decimal
from typing import Any

from acb_common import studio_query
from acb_common.paging import page_line
from acb_common.studio_access import (
    AccessError,
    Grant,
    Scope,
    Viewer,
    access_reads,
    blocked_keys,
    check_access_fit,
    parse_access,
    rollup_hidden,
    scope_for,
    stored_access,
    with_formulas,
)
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
    FieldError,
    FieldSpec,
    RecordError,
    check_can_be_hot,
    hot_value,
    lines_problem,
    parse_field_spec,
    record_json,
    relation_key,
    storable_text,
    stored_field,
    validate_record,
)
from acb_common.studio_query import Condition, Query, QueryError, parse_query
from acb_common.studio_rules import (
    FORMULA,
    GROUP_SUM_MIN,
    HIERARCHY,
    MAX_ROLLUP_PARENTS,
    MAX_ROLLUP_STATEMENTS,
    MAX_TREE_DEPTH,
    MAX_WRITES_PER_BATCH,
    RANGE,
    ROLLUP,
    ROLLUP_OF_ROLLUP,
    STATE_MACHINE,
    UNIQUE,
    ConstraintError,
    ConstraintViolation,
    FormulaPlan,
    RollupError,
    RollupSource,
    Rule,
    RuleViolation,
    add_change,
    check_group_sum,
    check_kind_known,
    check_move,
    check_range,
    check_rollup_currency,
    check_rollup_inputs,
    check_rollups,
    check_rule_is_hot,
    check_rules_fit,
    check_tree,
    check_unique_count,
    floor_holds_at_zero,
    key_filter,
    parse_rules,
    plan_formulas,
    refuse_computed_keys,
    rollup_parents,
    rollup_result,
    rule_reads,
    run_formulas,
    stored_rules,
    touched_keys,
    tree_moves,
    where_filter,
    with_initial,
    with_rollup_zero,
    without_targets,
)
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

_BOUND_ORG = "CAST(NULLIF(current_setting('app.tenant_id', true), '') AS uuid)"

#: The advisory lock of the org. The trigger ``studio_object_fields_hot_budget``
#: builds the same key from ``NEW.organization_id``.
_ORG_LOCK_SQL = (
    "SELECT pg_advisory_xact_lock(hashtextextended("
    f"'studio_objects:' || CAST({_BOUND_ORG} AS text), 0))"
)

#: The longest wait for the org lock, in milliseconds. A waiter holds a pool
#: connection for the whole wait, and it can try again, so the pool matters more.
ORG_LOCK_WAIT_MS = 1_000

#: The old ``lock_timeout`` and the bound, in one statement. ``set_config`` as
#: a function, so the value is a bind.
_LOCK_WAIT_SQL = "SELECT current_setting('lock_timeout'), set_config('lock_timeout', :ms, true)"
_LOCK_WAIT_BACK_SQL = "SELECT set_config('lock_timeout', :ms, true)"
#: The SQLSTATEs of a wait that gives up: ``lock_timeout`` stopped it, or
#: Postgres picked it as the victim of a deadlock.
_BUSY_SQLSTATES = ("55P03", "40P01")
_BUSY_MESSAGE = "This app is busy. Try again in a moment."
#: The next step of a refusal at the hot budget. One sentence with the cap.
_TURN_COLD = ", so turn a hot field cold first."

#: A backfill reads and writes the records in pages of this size.
BACKFILL_PAGE = 2_000

_NAME_RE = re.compile(NAME_PATTERN, re.ASCII)
_ZERO_UUID = "00000000-0000-0000-0000-000000000000"

_OBJECT_COLUMNS = (
    "id::text, package_id::text, name, label, created_by, created_at, updated_at, constraints, "
    "access"
)
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
#: of the package that the caller names. The org lock came in an earlier
#: statement, so this snapshot holds the last rules of the object.
#: C8a: the grants of the object, and the owner of the package in lower case.
#: The owner passes every row rule, so the store reads the owner here and never
#: takes it from a caller. The ``apps`` row takes no lock.
_SHARE_OBJECT_SQL = (
    "SELECT name, constraints, access, (SELECT lower(a.owner_email) FROM apps a "
    f"WHERE a.organization_id = {_BOUND_ORG} AND a.id = CAST(:pid AS uuid)) "
    "FROM studio_objects "
    f"WHERE organization_id = {_BOUND_ORG} AND id = CAST(:oid AS uuid) "
    "AND package_id = CAST(:pid AS uuid) FOR KEY SHARE"
)

#: C8a. The rules and the grants of one object of the package, and the owner
#: of the package, in one statement, for a read. No lock.
_VIEW_SQL = (
    "SELECT o.constraints, o.access, lower(a.owner_email) FROM studio_objects o "
    f"JOIN apps a ON a.organization_id = {_BOUND_ORG} AND a.id = o.package_id "
    f"WHERE o.organization_id = {_BOUND_ORG} AND o.id = CAST(:oid AS uuid) "
    "AND o.package_id = CAST(:pid AS uuid)"
)

#: C8a. The grants of the objects of the package that a roll-up reads, by name.
_SOURCE_ACCESS_SQL = (
    "SELECT name, access FROM studio_objects "
    f"WHERE organization_id = {_BOUND_ORG} AND package_id = CAST(:pid AS uuid) "
    "AND name = ANY(CAST(:names AS text[]))"
)

#: C8a. The grants of one object of the package, for the parent of a line.
_OBJECT_ACCESS_SQL = (
    "SELECT access FROM studio_objects "
    f"WHERE organization_id = {_BOUND_ORG} AND id = CAST(:oid AS uuid) "
    "AND package_id = CAST(:pid AS uuid)"
)

#: C8a. The grants of an object. NULL means an open object.
_SET_ACCESS_SQL = (
    "UPDATE studio_objects SET access = CAST(:access AS jsonb), updated_at = now() "
    f"WHERE organization_id = {_BOUND_ORG} AND id = CAST(:oid AS uuid) "
    f"AND package_id = CAST(:pid AS uuid) RETURNING {_OBJECT_COLUMNS}"
)

#: A declaration locks the object row of the package that the caller names.
_LOCK_PACKAGE_OBJECT_SQL = (
    f"SELECT {_OBJECT_COLUMNS} FROM studio_objects "
    f"WHERE organization_id = {_BOUND_ORG} AND id = CAST(:oid AS uuid) "
    "AND package_id = CAST(:pid AS uuid) FOR UPDATE"
)

#: The objects of one package that hold a roll-up rule, with their rules (C7b).
_ROLLUP_OBJECTS_SQL = (
    "SELECT id::text, name, constraints FROM studio_objects "
    f"WHERE organization_id = {_BOUND_ORG} AND package_id = CAST(:pid AS uuid) "
    "AND constraints @> CAST(:probe AS jsonb)"
)
_ROLLUP_PROBE = '[{"kind": "rollup"}]'

#: The names of the objects that a batch writes. A name never changes, and every
#: registry change takes the org lock, so this read needs no row lock.
_OBJECT_NAMES_SQL = (
    "SELECT id::text, name FROM studio_objects "
    f"WHERE organization_id = {_BOUND_ORG} AND package_id = CAST(:pid AS uuid) "
    "AND id = ANY(CAST(:oids AS uuid[]))"
)

#: Each lines relation of one package (C13): the parent object, the key of its
#: field, the field of the line object that names the parent, and the id of the
#: line object, which a name of the same package finds. Each alias names the org.
_LINES_SQL = (
    "SELECT p.id::text, p.name, f.key, f.spec ->> 'lines', c.id::text, c.name "
    "FROM studio_object_fields f "
    f"JOIN studio_objects p ON p.organization_id = {_BOUND_ORG} AND p.id = f.object_id "
    "AND p.package_id = CAST(:pid AS uuid) "
    f"LEFT JOIN studio_objects c ON c.organization_id = {_BOUND_ORG} "
    "AND c.package_id = CAST(:pid AS uuid) AND c.name = f.spec ->> 'to' "
    f"WHERE f.organization_id = {_BOUND_ORG} AND f.type = 'relation' "
    "AND (f.spec -> 'lines') IS NOT NULL ORDER BY p.id, f.key"
)

_SET_RULES_SQL = (
    "UPDATE studio_objects SET constraints = CAST(:rules AS jsonb), updated_at = now() "
    f"WHERE organization_id = {_BOUND_ORG} AND id = CAST(:oid AS uuid) "
    f"AND package_id = CAST(:pid AS uuid) RETURNING {_OBJECT_COLUMNS}"
)

#: The object of a record statement is of the package that the caller names.
_IN_PACKAGE = (
    "EXISTS (SELECT 1 FROM studio_objects o "
    f"WHERE o.organization_id = {_BOUND_ORG} AND o.id = CAST(:oid AS uuid) "
    "AND o.package_id = CAST(:pid AS uuid))"
)

#: The parent records that the lines of a batch name (C13), of one parent
#: object of the package. A parent of another package or another org is not
#: found, so its line is refused.
_LINE_PARENTS_SQL = (
    "SELECT id::text FROM studio_records "
    f"WHERE organization_id = {_BOUND_ORG} AND object_id = CAST(:oid AS uuid) "
    f"AND id = ANY(CAST(:ids AS uuid[])) AND {_IN_PACKAGE}"
)

#: C8a. The row rule of a viewer on a row of ``studio_records`` with no alias:
#: a hot row of one of the person fields ``:akeys`` holds the viewer ``:me``.
#: The text index of the hot rows serves it. A person field stays hot while a
#: grant reads it, and a field with no hot rows reads nothing (it fails closed).
_ACCESS_ROW_SQL = (
    "EXISTS (SELECT 1 FROM studio_record_hot ac "
    f"WHERE ac.organization_id = {_BOUND_ORG} AND ac.object_id = CAST(:oid AS uuid) "
    "AND ac.record_id = studio_records.id AND ac.field_key = ANY(CAST(:akeys AS text[])) "
    "AND ac.v_text = CAST(:me AS text))"
)

#: C8a. A parent of a line that the viewer can read. A parent that the viewer
#: cannot read is not found, so the refusal is the one of a parent that is gone.
_LINE_PARENTS_SCOPED_SQL = f"{_LINE_PARENTS_SQL} AND {_ACCESS_ROW_SQL}"

#: Every object of one package, and then every field of those objects. The
#: caller (``sync_objects``) holds the org lock, so the two reads agree.
_LIST_OBJECTS_SQL = (
    f"SELECT {_OBJECT_COLUMNS} FROM studio_objects "
    f"WHERE organization_id = {_BOUND_ORG} AND package_id = CAST(:pid AS uuid) "
    "ORDER BY created_at, name"
)

_LIST_FIELDS_SQL = (
    "SELECT object_id::text, key, type, spec, hot FROM studio_object_fields "
    f"WHERE organization_id = {_BOUND_ORG} AND object_id IN ("
    f"SELECT id FROM studio_objects WHERE organization_id = {_BOUND_ORG} "
    "AND package_id = CAST(:pid AS uuid)) ORDER BY object_id, position, key"
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

#: A batch locks each record that it changes, before its first write.
_LOCK_RECORD_SQL = _GET_RECORD_SQL + " FOR NO KEY UPDATE"

#: C8a. A record that the row rule of the viewer reads, for a get and for the
#: lock of a change or a delete. A record that the viewer cannot read is not found.
_GET_RECORD_SCOPED_SQL = f"{_GET_RECORD_SQL} AND {_ACCESS_ROW_SQL}"
_LOCK_RECORD_SCOPED_SQL = _GET_RECORD_SCOPED_SQL + " FOR NO KEY UPDATE"

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
    """A cap: hot fields, fields, objects, the backfill size, or the size of a batch."""


class RecordNotFound(ObjectStoreError):
    """A write names no such object or record in the package of the batch."""


class StoreBusy(ObjectStoreError):
    """The org lock did not come in time. The write ran no statement, and the
    caller's transaction stays usable. A caller may try again, and a route
    answers 503 with ``Retry-After``."""

    code = "store_busy"


class AccessDenied(ObjectStoreError):
    """C8a. The roles of the viewer give no write on the object, or the write
    would leave a record that the viewer cannot read. The text names the
    object and never a record."""

    code = "access_denied"


class BatchError(Exception):
    """One refused batch. Its SAVEPOINT rolled back, so it wrote nothing.

    ``index`` is the place of the write in the list. A rule runs after all
    the writes, so its index is the first write that touched the key.
    ``cause`` is the error. A database error keeps its text to ``cause``,
    because that text holds the statement and its binds, and so member data.
    """

    def __init__(self, index: int, cause: Exception) -> None:
        said = cause if isinstance(cause, ValueError) else type(cause).__name__
        super().__init__(f"Write {index} of the batch: {said}")
        self.index = index
        self.cause = cause


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
    constraints: tuple[Rule, ...] = ()
    #: C8a. The grants of the object, or None for an open object.
    access: tuple[Grant, ...] | None = None

    @property
    def computed(self) -> tuple[str, ...]:
        """The keys of the fields that a formula (C7a) or a roll-up (C7b) computes.
        A caller writes none of them, so a Form shows each one read-only and
        sends no key."""
        return tuple(str(r.target) for r in self.constraints if r.kind in (FORMULA, ROLLUP))


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


@dataclass(frozen=True)
class Write:
    """One write of a batch. ``create`` takes ``data``. ``update`` takes
    ``record_id`` and ``data``, the fields that change. ``delete`` takes
    ``record_id``.

    ``parent`` (C13) links a line to its parent in the same batch: the place
    of an earlier create or update of the parent record. A create of a line
    gets the parent field filled with that id. An update of a line must be a
    line of that parent, and cannot change the parent field. A delete takes
    no ``parent``."""

    op: str
    object_id: str
    record_id: str | None = None
    data: Mapping[str, Any] | None = None
    parent: int | None = None


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
        created_at=row[5], updated_at=row[6], fields=tuple(fields),
        constraints=stored_rules(_json(row[7])), access=stored_access(_json(row[8])))


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


def _sqlstate(exc: DBAPIError) -> str | None:
    orig = getattr(exc, "orig", None)
    code = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    return None if code is None else str(code)


async def _lock_org(db: Any) -> None:
    """Take the advisory lock of the org, and wait at most
    :data:`ORG_LOCK_WAIT_MS`. Each write and each registry change comes here.

    The wait sits in its own SAVEPOINT, because a lock timeout aborts the
    transaction around it. The lock stays when the SAVEPOINT releases. Raises
    :class:`StoreBusy` at the bound, and when Postgres picks this wait as the
    victim of a deadlock.

    ⚠️ **The one lock order is the ``apps`` row, then this lock.** A deadlock
    on a later statement stays a database error. So a caller that holds the
    ``apps`` row ``FOR UPDATE`` takes this lock next, before any other
    statement. A publish (A5, ``publish_version`` and then
    :func:`sync_objects`) and the delete of a package (A5c-1,
    ``packages_store.delete_package`` and then :func:`lock_org`) do that. No
    holder of this lock then waits for an ``apps`` row: a record write locks
    object rows and record rows only, and the foreign key of a record names
    ``studio_objects``, never ``apps``. The one exception is
    :func:`define_object`, whose foreign key check reads the ``apps`` row
    ``FOR KEY SHARE``, so its one caller is the sync of a publish, which holds
    that row already. Fence:
    ``test_r8_two_deletes_and_a_publish_in_one_org_never_deadlock`` in
    ``test_studio_package_delete_r8.py``.
    """
    wait = await db.execute(text(_LOCK_WAIT_SQL), {"ms": str(ORG_LOCK_WAIT_MS)})
    old = str(wait.one()[0])
    try:
        async with db.begin_nested():
            await db.execute(text(_ORG_LOCK_SQL))
    except DBAPIError as exc:
        if _sqlstate(exc) in _BUSY_SQLSTATES:
            raise StoreBusy(_BUSY_MESSAGE) from None
        raise
    finally:
        await db.execute(text(_LOCK_WAIT_BACK_SQL), {"ms": old})


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
        raise BudgetExceeded(f"An object holds at most {MAX_HOT_PER_OBJECT} hot fields{_TURN_COLD}")
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
        # The delete of a package (A5c-1) frees the room, so the refusal names it.
        raise BudgetExceeded(f"An organization holds at most {MAX_OBJECTS_PER_ORG} objects, "
                             "so delete a package that you do not use, or ask its owner "
                             "to delete it.")
    hot = sum(s.hot for s in specs)
    if hot and await _org_hot(db) + hot > MAX_HOT_PER_ORG:
        raise BudgetExceeded(
            f"An organization holds at most {MAX_HOT_PER_ORG} hot fields{_TURN_COLD}")
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
        raise BudgetExceeded(f"An object holds at most {MAX_HOT_PER_OBJECT} hot fields{_TURN_COLD}")
    if org_hot >= MAX_HOT_PER_ORG:
        raise BudgetExceeded(
            f"An organization holds at most {MAX_HOT_PER_ORG} hot fields{_TURN_COLD}")


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


def _check_no_rule_reads(rules: Sequence[Rule], key: str) -> None:
    """A field that turns cold loses its hot rows, so a rule that reads them
    would pass each write with no check."""
    for rule in rules:
        if key in rule.hot_keys():
            raise ObjectStoreError(
                f"Field `{key}` must stay hot, because rule `{rule.name}` reads it. "
                "Remove the rule first.")


def _check_no_access_reads(grants: Sequence[Grant] | None, key: str) -> None:
    """C8a. A person field that a row rule reads stays hot: the rule reads its
    hot rows, so a cold field would read no record."""
    if key in access_reads(grants):
        raise ObjectStoreError(
            f"Field `{key}` must stay hot, because a row rule of `access` reads it. "
            "Change the access first.")


async def _package_rollups(db: Any, pid: str, *, strict: bool = False) -> list[tuple[str, str, Rule]]:
    """Each roll-up rule of the package *pid*, as ``(object id, object name, rule)``.
    The org lock is held. With *strict*, a stored roll-up that this code cannot
    read raises :class:`RollupError`, because its source is unknown and a write
    of the package could skip it. A rule must never pass because the store
    could not read it."""
    out: list[tuple[str, str, Rule]] = []
    rows = (await db.execute(text(_ROLLUP_OBJECTS_SQL), {"pid": pid, "probe": _ROLLUP_PROBE})).all()
    for row in rows:
        raw = _json(row[2])
        for item, rule in zip(raw, stored_rules(raw), strict=True):
            if rule.kind == ROLLUP:
                out.append((str(row[0]), str(row[1]), rule))
            elif strict and isinstance(item, Mapping) and item.get("kind") == ROLLUP:
                raise RollupError(
                    rule.name, str(row[1]), "declaration",
                    f"Object `{row[1]}` holds a roll-up that the store cannot read. "
                    "Declare the rules of the object again.")
    return out


async def _check_no_rollup_reads(db: Any, row: Any, key: str) -> None:
    """A field of a source that a roll-up of the package reads stays hot: its
    hot rows are what the roll-up sums, so a cold field would skip the sum."""
    for _, owner, rule in await _package_rollups(db, str(row[1])):
        if rule.source == str(row[2]) and key in rule.source_keys():
            raise ObjectStoreError(
                f"Field `{key}` must stay hot, because rule `{rule.name}` of object `{owner}` "
                "reads it. Remove the rule first.")


async def set_field_hot(db: Any, object_id: str, key: str, hot: bool) -> bool:
    """Turn the field *key* of the object *object_id* hot, or not hot.

    A field that turns hot gets its hot rows for the records that exist, in
    the caller's transaction. Raises :class:`BudgetExceeded` past the hot
    budget, or when the object holds more than
    :data:`~acb_common.studio_fields.MAX_HOT_BACKFILL` records. A field that
    turns cold loses its hot rows. A field that a rule reads stays hot, and
    the store raises :class:`ObjectStoreError`. Answers False when the bound
    org holds no such object or field.
    """
    oid = _uuid(object_id, "object_id")
    if type(hot) is not bool:
        raise ObjectStoreError("hot must be true or false")
    await _lock_org(db)
    row = (await db.execute(text(_LOCK_OBJECT_SQL), {"oid": oid})).first()
    if row is None:
        return False
    fields = await _fields(db, oid)
    current = next((f for f in fields if f.key == key), None)
    if current is None:
        return False
    if current.hot == hot:
        return True
    if not hot:
        _check_no_rule_reads(_object(row, fields).constraints, key)
        _check_no_access_reads(_object(row, fields).access, key)
        await _check_no_rollup_reads(db, row, key)
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


def _formula_key(rule: Rule) -> str:
    """What makes a stored result stale: the target, the expression, and the
    scale, rounding and currency. The name and the message do not, so a rename
    or a new message passes. Key order does not matter."""
    stored = rule.stored()
    kept = {k: stored.get(k) for k in ("target", "expr", "scale", "rounding", "currency")}
    return json.dumps(kept, sort_keys=True, ensure_ascii=False)


def _rollup_key(rule: Rule) -> str:
    """What makes a stored roll-up stale: the target, the source, the relation,
    the aggregate, the read field, the filter and the currency. The name and the
    message do not. Key order does not matter."""
    stored = rule.stored()
    kept = {k: stored.get(k) for k in (
        "target", "source", "via", "agg", "field", "where", "currency")}
    return json.dumps(kept, sort_keys=True, ensure_ascii=False)


def _computed_key(rule: Rule) -> str:
    return _formula_key(rule) if rule.kind == FORMULA else _rollup_key(rule)


async def _check_formulas_first(db: Any, oid: str, old: Sequence[Rule],
                                new: Sequence[Rule]) -> None:
    """A new or changed formula or roll-up needs an object with no record (C7a,
    C7b). There is no backfill, and a stored result that the rule did not
    compute is worse than a refusal. A rule that stays as it is, and a removed
    one, pass: a removed rule leaves its stored values as plain values."""
    kinds = (FORMULA, ROLLUP)
    kept = {(r.kind, _computed_key(r)) for r in old if r.kind in kinds}
    fresh = [r for r in new if r.kind in kinds and (r.kind, _computed_key(r)) not in kept]
    if fresh and (await db.execute(text(_HAS_RECORDS_SQL), {"oid": oid})).scalar_one():
        raise ConstraintError(
            fresh[0].name, f"Rule `{fresh[0].name}`: declare before the first record. This "
            "object holds records, so a new or changed formula or roll-up would leave their "
            "results stale.")


async def _rollup_sources(db: Any, pid: str, rules: Sequence[Rule]) -> dict[str, RollupSource]:
    """The objects of the package that the roll-ups of *rules* name, with their
    fields and rules. A name that the package does not hold is left out, and
    the check then refuses the rule. The org lock is held."""
    out: dict[str, RollupSource] = {}
    for name in sorted({str(r.source) for r in rules if r.kind == ROLLUP}):
        row = (await db.execute(text(_OBJECT_BY_NAME_SQL), {"pid": pid, "name": name})).first()
        if row is not None:
            out[name] = RollupSource(
                name, tuple(await _fields(db, str(row[0]))), stored_rules(_json(row[7])))
    return out


async def set_constraints(db: Any, object_id: str, constraints: Any, *,
                          package_id: str) -> StudioObject | None:
    """Replace the rules of the object *object_id* of the package *package_id*.
    Answers None when the bound org holds no such object in that package.

    ``studio_rules.parse_rules`` checks the shape before any statement, and
    ``studio_rules.check_rules_fit`` checks the rules against the fields. Each
    raises :class:`~acb_common.studio_rules.ConstraintError`. This function
    never turns a field hot. The org lock comes first, then the object row.
    ⚠️ A declaration does not check old data. A rule binds each later write.
    """
    oid = _uuid(object_id, "object_id")
    pid = _uuid(package_id, "package_id")
    rules = parse_rules(constraints)
    await _lock_org(db)
    binds = {"oid": oid, "pid": pid}
    locked = (await db.execute(text(_LOCK_PACKAGE_OBJECT_SQL), binds)).first()
    if locked is None:
        return None
    fields = await _fields(db, oid)
    name = str(locked[2])
    check_rules_fit(rules, fields, object_name=name, sources=await _rollup_sources(db, pid, rules))
    readers = [(owner, rule) for _, owner, rule in await _package_rollups(db, pid)
               if rule.source == name]
    check_rollup_inputs(name, rules, readers)
    await _check_formulas_first(db, oid, stored_rules(_json(locked[7])), rules)
    row = (await db.execute(text(_SET_RULES_SQL), {
        **binds, "rules": json.dumps([r.stored() for r in rules], ensure_ascii=False)})).one()
    return _object(row, fields)


def _access_json(grants: Sequence[Grant] | None) -> str | None:
    """The grants as the store keeps them, or None for an open object."""
    if grants is None:
        return None
    return json.dumps([g.stored() for g in grants], ensure_ascii=False)


async def _write_access(db: Any, oid: str, pid: str, grants: Sequence[Grant] | None) -> Any:
    return (await db.execute(text(_SET_ACCESS_SQL), {
        "oid": oid, "pid": pid, "access": _access_json(grants)})).one()


async def set_access(db: Any, object_id: str, access: Any, *,
                     package_id: str) -> StudioObject | None:
    """C8a. Replace the grants of the object *object_id* of the package
    *package_id*. None makes the object open. Answers None when the bound org
    holds no such object in that package.

    ``studio_access.parse_access`` checks the shape before any statement, and
    ``check_access_fit`` checks the grants against the fields: a ``where``
    names a hot person field, and ``hide`` names fields of the object. Each
    raises :class:`~acb_common.studio_access.AccessError`. The roles are not
    checked here, because A2 checked them at the publish. The org lock comes
    first, then the object row, as :func:`set_constraints` does.
    """
    oid = _uuid(object_id, "object_id")
    pid = _uuid(package_id, "package_id")
    grants = parse_access(access)
    await _lock_org(db)
    locked = (await db.execute(text(_LOCK_PACKAGE_OBJECT_SQL), {"oid": oid, "pid": pid})).first()
    if locked is None:
        return None
    fields = await _fields(db, oid)
    check_access_fit(grants, fields)
    return _object(await _write_access(db, oid, pid, grants), fields)


# --- the sync of a manifest (STU-A5) ----------------------------------------

#: The kinds of a sync problem.
OBJECT_REMOVED = "object_removed"
FIELD_REMOVED = "field_removed"
FIELD_CHANGED = "field_changed"
REQUIRED_OVER_RECORDS = "required_over_records"
RULES_REFUSED = "rules_refused"
FIELD_REFUSED = "field_refused"
STORE_REFUSED = "store_refused"
#: C8a. The access of an object that the store refuses.
ACCESS_REFUSED = "access_refused"

#: The parts of a field that a sync cannot change. ``hot`` is not one of them.
_FIELD_ATTRS = ("type", "many", "options", "to", "required", "label", "lines")


@dataclass(frozen=True)
class SyncProblem:
    """One reason that a manifest does not fit the stored objects. ``message``
    is one sentence. It names the object, the field or the rule, and no value."""

    kind: str
    object: str
    field: str | None = None
    rule: str | None = None
    message: str = ""


class ObjectsOutOfSync(ObjectStoreError):
    """The manifest does not fit the stored objects. The sync wrote nothing.
    ``problems`` lists each reason."""

    def __init__(self, problems: Sequence[SyncProblem]) -> None:
        super().__init__("The objects of the manifest do not fit the stored objects.")
        self.problems: list[SyncProblem] = list(problems)


@dataclass(frozen=True)
class SyncResult:
    """What a sync wrote. All four are empty when the rows already matched."""

    objects_created: tuple[str, ...] = ()
    fields_added: tuple[tuple[str, str], ...] = ()
    hot_changed: tuple[tuple[str, str, bool], ...] = ()
    rules_set: tuple[str, ...] = ()
    #: C8a. The objects whose grants changed.
    access_set: tuple[str, ...] = ()

    @property
    def changed(self) -> bool:
        return bool(self.objects_created or self.fields_added or self.hot_changed or self.rules_set
                    or self.access_set)


@dataclass
class _Item:
    """One object of the manifest, checked, with what the sync does for it."""

    name: str
    label: str | None
    raw_fields: list[Mapping[str, Any]]
    fields: list[FieldSpec]
    raw_rules: Any
    rules: tuple[Rule, ...]
    row: StudioObject | None = None
    add: list[Mapping[str, Any]] = dc_field(default_factory=list)
    heat: list[tuple[str, bool]] = dc_field(default_factory=list)
    set_rules: bool = False
    #: C8a. The grants of the manifest, or None for an open object.
    access: tuple[Grant, ...] | None = None
    set_access: bool = False


async def list_objects(db: Any, package_id: str) -> list[StudioObject]:
    """The objects of the package *package_id* in the bound org, with their
    fields and rules, in the order of their creation. It checks no owner:
    the caller reads the package as its member first. Two statements."""
    pid = _uuid(package_id, "package_id")
    rows = (await db.execute(text(_LIST_OBJECTS_SQL), {"pid": pid})).all()
    if not rows:
        return []
    by_object: dict[str, list[FieldSpec]] = {}
    found = (await db.execute(text(_LIST_FIELDS_SQL), {"pid": pid})).all()
    for oid, key, kind, spec, hot in found:
        by_object.setdefault(str(oid), []).append(
            stored_field(str(key), str(kind), _json(spec), bool(hot)))
    return [_object(r, by_object.get(str(r[0]), [])) for r in rows]


def _rules_json(rules: Sequence[Rule]) -> str:
    """The rules as the store keeps them, in one stable text, for a compare."""
    return json.dumps([r.stored() for r in rules], sort_keys=True, ensure_ascii=False)


def _read_entry(entry: Any, problems: list[SyncProblem]) -> _Item | None:
    """One object of the manifest, or None with a problem. The manifest check
    of A2 ran at publish, so this only guards the store."""
    name = entry.get("name") if isinstance(entry, Mapping) else None
    try:
        clean = _check_name(name)
        label = _check_label(entry.get("label"))
        raw_fields = list(entry.get("fields") or [])
        fields = _check_fields(raw_fields)
    except FieldError as exc:
        problems.append(SyncProblem(
            FIELD_REFUSED, str(name), field=exc.field, message=exc.message))
        return None
    except ObjectStoreError as exc:
        problems.append(SyncProblem(STORE_REFUSED, str(name), message=str(exc)))
        return None
    raw_rules = entry.get("rules") or []
    try:
        rules = parse_rules(raw_rules)
    except ConstraintError as exc:
        problems.append(SyncProblem(RULES_REFUSED, clean, rule=exc.rule, message=exc.message))
        return None
    try:
        access = parse_access(entry.get("access"))
    except AccessError as exc:
        problems.append(SyncProblem(ACCESS_REFUSED, clean, message=exc.message))
        return None
    return _Item(clean, label, raw_fields, fields, raw_rules, rules, access=access)


async def _plan_fields(db: Any, item: _Item, row: StudioObject,
                       problems: list[SyncProblem]) -> None:
    """The field plan of an object that the rows hold: a new field is added,
    a change of ``hot`` flips, and each other difference is a problem."""
    old = {f.key: f for f in row.fields}
    has_records: bool | None = None
    for raw, spec in zip(item.raw_fields, item.fields, strict=True):
        cur = old.get(spec.key)
        if cur is None:
            if spec.required:
                if has_records is None:
                    has_records = bool((await db.execute(
                        text(_HAS_RECORDS_SQL), {"oid": row.id})).scalar_one())
                if has_records:
                    problems.append(SyncProblem(
                        REQUIRED_OVER_RECORDS, item.name, field=spec.key,
                        message=f"Field `{spec.key}` of `{item.name}` is new and required, but the "
                        "object holds records. Add it as optional."))
                    continue
            item.add.append(raw)
            continue
        changed = [a for a in _FIELD_ATTRS if getattr(cur, a) != getattr(spec, a)]
        if changed:
            problems.append(SyncProblem(
                FIELD_CHANGED, item.name, field=spec.key,
                message=f"Field `{spec.key}` of `{item.name}` changed ({', '.join(changed)}). "
                "A published field cannot change yet."))
        elif cur.hot != spec.hot:
            item.heat.append((spec.key, spec.hot))
    wanted = {f.key for f in item.fields}
    for key in old:
        if key not in wanted:
            problems.append(SyncProblem(
                FIELD_REMOVED, item.name, field=key,
                message=f"Field `{key}` of `{item.name}` is not in the manifest. "
                "A published field cannot go yet."))


async def _plan_sync(db: Any, rows: Sequence[StudioObject],
                     objects: Sequence[Any]) -> tuple[list[_Item], list[SyncProblem]]:
    """The whole plan, from reads only. A problem in the list means no write."""
    problems: list[SyncProblem] = []
    items: list[_Item] = []
    by_name = {r.name: r for r in rows}
    for entry in objects:
        item = _read_entry(entry, problems)
        if item is None:
            continue
        item.row = by_name.get(item.name)
        if item.row is None:
            item.set_rules = bool(item.rules)
            item.set_access = item.access is not None
        else:
            await _plan_fields(db, item, item.row, problems)
            item.set_rules = _rules_json(item.rules) != _rules_json(item.row.constraints)
            item.set_access = _access_json(item.access) != _access_json(item.row.access)
        items.append(item)
    kept = {i.name for i in items}
    for row in rows:
        if row.name not in kept and not any(p.object == row.name for p in problems):
            problems.append(SyncProblem(
                OBJECT_REMOVED, row.name,
                message=f"Object `{row.name}` is not in the manifest. A published object "
                "cannot go yet."))
    return items, problems


@dataclass
class _Done:
    """What the write phase did so far, and the object and the field it works
    on. The two name the place of a refusal."""

    ids: dict[str, str] = dc_field(default_factory=dict)
    where: str = ""
    field: str | None = None
    created: list[str] = dc_field(default_factory=list)
    added: list[tuple[str, str]] = dc_field(default_factory=list)
    heated: list[tuple[str, str, bool]] = dc_field(default_factory=list)
    ruled: list[str] = dc_field(default_factory=list)
    accessed: list[str] = dc_field(default_factory=list)


async def _sync_new_objects(db: Any, who: str, pid: str, items: Sequence[_Item],
                            done: _Done) -> None:
    for it in items:
        done.where, done.field = it.name, None
        if it.row is not None:
            continue
        made = await define_object(
            db, who, package_id=pid, name=it.name, label=it.label, fields=it.raw_fields)
        if made is None:
            raise ObjectStoreError("The package is gone.")
        done.ids[it.name] = made.id
        done.created.append(it.name)


async def _sync_new_fields(db: Any, items: Sequence[_Item], done: _Done) -> None:
    for it in items:
        for raw in it.add:
            done.where, done.field = it.name, str(raw["key"])
            if await add_field(db, done.ids[it.name], raw) is None:
                raise ObjectStoreError("The object is gone.")
            done.added.append((it.name, str(raw["key"])))


async def _flip(db: Any, it: _Item, done: _Done, key: str, hot: bool) -> None:
    done.where, done.field = it.name, key
    if await set_field_hot(db, done.ids[it.name], key, hot):
        done.heated.append((it.name, key, hot))


async def _sync_heat(db: Any, items: Sequence[_Item], done: _Done, *, hot: bool) -> None:
    for it in items:
        for key, want in it.heat:
            if want is hot:
                await _flip(db, it, done, key, hot)


def _rule_form(rule: Rule) -> str:
    return json.dumps(rule.stored(), sort_keys=True, ensure_ascii=False)


def _staying_rules(it: _Item) -> list[Rule] | None:
    """The first pass of the rules of *it*: each stored rule that stays. A rule
    stays when the manifest holds the same stored form, or a formula or a
    roll-up with the same computed key. None when no stored rule goes, so the
    first pass writes nothing for *it*."""
    if it.row is None or not it.set_rules:
        return None
    forms = {_rule_form(r) for r in it.rules}
    keys = {(r.kind, _computed_key(r)) for r in it.rules if r.kind in (FORMULA, ROLLUP)}
    stored = it.row.constraints
    kept = [r for r in stored if _rule_form(r) in forms
            or (r.kind in (FORMULA, ROLLUP) and (r.kind, _computed_key(r)) in keys)]
    return None if len(kept) == len(stored) else kept


async def _set_rules(db: Any, pid: str, it: _Item, done: _Done, rules: Any) -> None:
    done.where, done.field = it.name, None
    if await set_constraints(db, done.ids[it.name], rules, package_id=pid) is None:
        raise ObjectStoreError("The object is gone.")


async def _sync_rules_first(db: Any, pid: str, items: Sequence[_Item],
                            done: _Done) -> dict[str, str]:
    """Pass 1 of the rules: for each object, only the stored rules that stay.
    So each removal lands before a field turns cold, before a field takes the
    room in the hot budget, and before a roll-up checks its source. The list
    is a subset of a stored list, so it needs no new field and no new hot
    field. Answers the list that each object holds after it."""
    first: dict[str, str] = {}
    for it in items:
        kept = _staying_rules(it)
        if kept is not None:
            await _set_rules(db, pid, it, done, [r.stored() for r in kept])
            first[it.name] = _rules_json(kept)
    return first


async def _sync_rules_full(db: Any, pid: str, items: Sequence[_Item], done: _Done,
                           first: Mapping[str, str]) -> None:
    """Pass 2 of the rules: the full manifest list of each object, through the
    one store path :func:`set_constraints`. A list that equals the stored one
    writes nothing.

    A roll-up checks the stored rules of its source, and a source checks the
    stored roll-ups that read it (C7b). Pass 1 removed each rule that goes, so
    the order of the objects in the manifest never decides a valid manifest.
    """
    for it in items:
        if it.set_rules and first.get(it.name) != _rules_json(it.rules):
            await _set_rules(db, pid, it, done, it.raw_rules)
    done.ruled.extend(it.name for it in items if it.set_rules)


async def _sync_access_first(db: Any, pid: str, items: Sequence[_Item], done: _Done) -> None:
    """C8a, pass 1 of the grants: each stored grant list that changes goes, so
    a person field that it reads may turn cold. The sync runs in one
    transaction, so no reader sees the object open."""
    for it in items:
        if it.row is not None and it.set_access and it.row.access is not None:
            done.where, done.field = it.name, None
            await _write_access(db, it.row.id, pid, None)


async def _sync_access_full(db: Any, pid: str, items: Sequence[_Item], done: _Done) -> None:
    """C8a, pass 2 of the grants: the manifest list of each object, through the
    one store path :func:`set_access`, after the fields are in place and hot."""
    for it in items:
        if it.set_access and it.access is not None:
            done.where, done.field = it.name, None
            if await set_access(db, done.ids[it.name], [g.stored() for g in it.access],
                                package_id=pid) is None:
                raise ObjectStoreError("The object is gone.")
    done.accessed.extend(it.name for it in items if it.set_access)


async def _apply_sync(db: Any, who: str, pid: str, items: Sequence[_Item]) -> SyncResult:
    """Write the plan in one SAVEPOINT, so a refusal leaves no part of it.

    The phases run in this order: rules pass 1 (the stored rules that stay),
    fields that turn cold, new objects, new fields, fields that turn hot, and
    rules pass 2 (the full lists). A rule that goes is gone before its field
    turns cold. A cold field frees its room in the hot budget before a new
    object, a new field or a field that turns hot takes it, so a swap at a
    cap passes, also when a rule moves with it. A rule needs its fields in
    place and hot, so pass 2 comes last. The store still refuses a cold flip
    that a staying rule reads. A refusal of a field step names the field.
    The grants of C8a go first and come back last, in the same way.
    """
    done = _Done(ids={it.name: it.row.id for it in items if it.row is not None})
    try:
        async with db.begin_nested():
            await _sync_access_first(db, pid, items, done)
            first = await _sync_rules_first(db, pid, items, done)
            await _sync_heat(db, items, done, hot=False)
            await _sync_new_objects(db, who, pid, items, done)
            await _sync_new_fields(db, items, done)
            await _sync_heat(db, items, done, hot=True)
            await _sync_rules_full(db, pid, items, done, first)
            await _sync_access_full(db, pid, items, done)
    except StoreBusy:
        raise
    except AccessError as exc:
        raise ObjectsOutOfSync([SyncProblem(
            ACCESS_REFUSED, done.where, message=exc.message)]) from None
    except ConstraintError as exc:
        raise ObjectsOutOfSync([SyncProblem(
            RULES_REFUSED, done.where, rule=exc.rule, message=exc.message)]) from None
    except FieldError as exc:
        raise ObjectsOutOfSync([SyncProblem(
            FIELD_REFUSED, done.where, field=exc.field or done.field,
            message=exc.message)]) from None
    except ObjectStoreError as exc:
        raise ObjectsOutOfSync([SyncProblem(
            STORE_REFUSED, done.where, field=done.field, message=str(exc))]) from None
    return SyncResult(
        tuple(done.created), tuple(done.added), tuple(done.heated), tuple(done.ruled),
        tuple(done.accessed))


async def sync_objects(db: Any, actor: str, *, package_id: str,
                       objects: Sequence[Any]) -> SyncResult | None:
    """Bring the stored objects of the package *package_id* to the list
    *objects*, the ``objects`` of one manifest (STU-A5, ``ai_studio.md`` §14).

    The manifest is the only source. The call adds an object with
    :func:`define_object`, a field with :func:`add_field`, a change of ``hot``
    with :func:`set_field_hot`, and a rules list with :func:`set_constraints`.
    The rules go in two passes, removals first, so the order of the objects
    in the manifest never decides a roll-up, and a rule that goes frees its
    field before a hot swap. A list of rules that equals the stored one
    writes nothing. ``pages`` and each other part of a manifest are not rows,
    so this call ignores them.

    It takes the org lock, reads the rows, and builds the whole plan before a
    write. Each other difference is a problem: an object that the manifest no
    longer holds, a field that is gone, a field that changed (its type, ``many``,
    ``options``, ``to``, ``required`` or label), a new required field on an
    object with records, and a refusal of a store call, rules included. A
    problem raises :class:`ObjectsOutOfSync` with the list, and the write phase
    runs in a SAVEPOINT, so no part of it stays. Records are never rewritten.

    Answers None when the bound org holds no Studio package of the id, and the
    :class:`SyncResult` otherwise. It raises :class:`StoreBusy` when the org
    lock does not come in time.

    ⚠️ **The lock order.** The caller holds the ``apps`` row ``FOR UPDATE``
    (inside ``packages_store.publish_version``), and this call takes the org
    lock after it. :func:`define_object` takes them the other way, so a caller
    of it outside a publish can deadlock with a publish. This function is its
    one caller (``test_define_object_has_one_caller``).
    """
    who = _actor(actor)
    pid = _uuid(package_id, "package_id")
    if isinstance(objects, str | bytes | Mapping) or not isinstance(objects, Sequence):
        raise ObjectStoreError("objects must be a list of objects")
    await _lock_org(db)
    if (await db.execute(text(_PACKAGE_SQL), {"pid": pid})).first() is None:
        return None
    rows = await list_objects(db, pid)
    items, problems = await _plan_sync(db, rows, objects)
    if problems:
        raise ObjectsOutOfSync(problems)
    return await _apply_sync(db, who, pid, items)


# --- the delete of a package (A5c-1) ----------------------------------------
#
# ``packages_store.delete_package`` owns the delete. It locks the ``apps`` row
# FOR UPDATE, takes the org lock through :func:`lock_org`, and then calls
# :func:`purge_records` here, because this module holds all SQL of the four
# tables. The cascade from ``apps`` deletes the objects, the fields and the hot
# rows of the records that are left. So the purge is the only step whose size
# has no cap: a record count has no limit.

#: The most records that one step of a package delete removes.
PURGE_CHUNK = 500
#: A count of the records of one object stops past this number. The preview
#: of a delete shows "100,000 or more" for a larger object.
COUNT_CAP = 100_000
#: The longest run of the counts of one preview, in milliseconds: the deadline
#: of C1b. The route of a delete adds it once past its budget (A5c-1).
COUNT_TIMEOUT_MS = studio_query.TIMEOUT_MS
#: The longest run of one purge, in milliseconds. A caller can make it end
#: earlier with ``deadline``, and never later.
PURGE_TIMEOUT_MS = studio_query.TIMEOUT_MS

#: The objects of one package by name, with their field count and their record
#: count up to one past the cap (``:cap``). One statement for the package.
_PACKAGE_COUNTS_SQL = (
    "SELECT o.name, "
    "(SELECT count(*) FROM studio_object_fields f "
    f"WHERE f.organization_id = {_BOUND_ORG} AND f.object_id = o.id), "
    "(SELECT count(*) FROM (SELECT 1 FROM studio_records r "
    f"WHERE r.organization_id = {_BOUND_ORG} AND r.object_id = o.id LIMIT :cap) c) "
    f"FROM studio_objects o WHERE o.organization_id = {_BOUND_ORG} "
    "AND o.package_id = CAST(:pid AS uuid) ORDER BY o.name"
)

#: The object names of one package, for the check of a later step (A5c-1).
_PACKAGE_OBJECT_NAMES_SQL = (
    "SELECT name FROM studio_objects "
    f"WHERE organization_id = {_BOUND_ORG} AND package_id = CAST(:pid AS uuid) ORDER BY name"
)

#: The object ids of one package, in id order, for the purge.
_PACKAGE_OBJECT_IDS_SQL = (
    "SELECT id::text FROM studio_objects "
    f"WHERE organization_id = {_BOUND_ORG} AND package_id = CAST(:pid AS uuid) ORDER BY id"
)

#: At most ``:n`` records of one object, and their hot rows by cascade. The
#: unique key ``(organization_id, object_id, id)`` serves the subselect.
#: ⚠️ The ids come from an ARRAY of a subselect that names no outer column, so
#: Postgres runs it once (an InitPlan) and reads each record by its key. With
#: ``id IN (subselect)``, stale statistics after a bulk load made the planner
#: run the LIMIT again for each record of the object: 21.5 s for 500 of 60,000
#: records on the dev stack (2026-11-05). Fence:
#: ``test_r8_a_purge_after_a_bulk_load_reads_a_bounded_number_of_rows``.
_PURGE_SQL = (
    "DELETE FROM studio_records "
    f"WHERE organization_id = {_BOUND_ORG} AND object_id = CAST(:oid AS uuid) "
    "AND id = ANY(ARRAY(SELECT id FROM studio_records "
    f"WHERE organization_id = {_BOUND_ORG} AND object_id = CAST(:oid AS uuid) "
    "ORDER BY id LIMIT :n))"
)


@dataclass(frozen=True)
class ObjectCount:
    """The size of one object of a package, for the preview of a delete.

    ``records`` holds at most :data:`COUNT_CAP`. ``capped`` is True when the
    object holds more."""

    name: str
    fields: int
    records: int
    capped: bool


async def lock_org(db: Any) -> None:
    """Take the advisory lock of the org with the bound of :func:`_lock_org`.

    The delete of a package (A5c-1) calls it right after it locks the
    ``apps`` row ``FOR UPDATE``: the one lock order. The lock holds to the end
    of the caller's transaction. Raises :class:`StoreBusy` at the bound."""
    await _lock_org(db)


@asynccontextmanager
async def bounded_wait(db: Any, ms: int) -> AsyncIterator[None]:
    """Run the block with a LOCAL ``lock_timeout`` of *ms*, and put the old
    value back after it (A5c-1, the ``apps`` row of a delete step).

    A wait past the bound, or a deadlock at it, raises :class:`StoreBusy`.
    It reuses the statements and the codes of :func:`_lock_org`, but it opens
    no SAVEPOINT. So a busy wait aborts the caller's transaction, and the
    caller ends that transaction."""
    wait = await db.execute(text(_LOCK_WAIT_SQL), {"ms": str(ms)})
    old = str(wait.one()[0])
    try:
        yield
    except DBAPIError as exc:
        if _sqlstate(exc) in _BUSY_SQLSTATES:
            raise StoreBusy(_BUSY_MESSAGE) from None
        raise
    await db.execute(text(_LOCK_WAIT_BACK_SQL), {"ms": old})


def _deadline(ms: int = studio_query.TIMEOUT_MS, cap: float | None = None) -> float:
    """A deadline of *ms* from now, and never later than *cap* (a time of
    ``_clock``)."""
    end = float(_clock() + ms / 1000)
    return end if cap is None else min(end, float(cap))


async def object_names(db: Any, package_id: str) -> list[str]:
    """The names of the objects of the package *package_id* in the bound org,
    in name order (A5c-1). A later step of a delete compares them with the
    names of the preview that the member confirmed. One statement on the
    unique key of a name, under the 5 s deadline. A timeout raises
    :class:`StoreBusy`."""
    pid = _uuid(package_id, "package_id")
    try:
        async with _time_limit(db, _deadline(), _BUSY_MESSAGE) as timed:
            rows = (await timed.execute(text(_PACKAGE_OBJECT_NAMES_SQL), {"pid": pid})).all()
    except QueryError:
        raise StoreBusy(_BUSY_MESSAGE) from None
    return [str(r[0]) for r in rows]


async def package_counts(db: Any, package_id: str, *,
                         cap: int = COUNT_CAP) -> list[ObjectCount]:
    """The objects of the package *package_id* in the bound org, by name, with
    their field count and their record count up to *cap* (A5c-1).

    It checks no owner: the caller reads the package as its member first. The
    statement runs under the 5 s deadline of :func:`_time_limit`. A timeout
    raises :class:`StoreBusy`, and the caller's transaction stays usable."""
    pid = _uuid(package_id, "package_id")
    if isinstance(cap, bool) or not isinstance(cap, int) or cap < 1:
        raise ObjectStoreError("cap must be a whole number from 1")
    try:
        async with _time_limit(db, _deadline(COUNT_TIMEOUT_MS), _BUSY_MESSAGE) as timed:
            rows = (await timed.execute(
                text(_PACKAGE_COUNTS_SQL), {"pid": pid, "cap": cap + 1})).all()
    except QueryError:
        raise StoreBusy(_BUSY_MESSAGE) from None
    return [ObjectCount(name=str(r[0]), fields=int(r[1]), records=min(int(r[2]), cap),
                        capped=int(r[2]) > cap) for r in rows]


async def purge_records(db: Any, package_id: str, *, limit: int = PURGE_CHUNK,
                        deadline: float | None = None) -> int:
    """Delete at most *limit* records of the package *package_id*, and their
    hot rows by cascade (A5c-1). Answers the number of records deleted.

    ⚠️ The caller holds the ``apps`` row ``FOR UPDATE`` and then the org lock
    (:func:`lock_org`), in that order. Each record write takes the org lock,
    so no record of the package comes in while the caller holds it. So an
    answer below *limit* means that the package holds no record now.

    The statements run under one deadline of :data:`PURGE_TIMEOUT_MS`
    (:func:`_time_limit`), or under *deadline* (a time of ``_clock``) when it
    comes first. So the route of a delete keeps each purge inside its budget.
    A timeout rolls back this call only, deletes nothing, and raises
    :class:`StoreBusy`. A retry continues: nothing is lost. The purge runs no
    rule of an object, because each record of the package goes."""
    pid = _uuid(package_id, "package_id")
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ObjectStoreError("limit must be a whole number from 1")
    left = limit
    try:
        async with _time_limit(db, _deadline(PURGE_TIMEOUT_MS, deadline),
                               _BUSY_MESSAGE) as timed:
            found = (await timed.execute(text(_PACKAGE_OBJECT_IDS_SQL), {"pid": pid})).all()
            for (oid,) in found:
                if left <= 0:
                    break
                gone = await timed.execute(text(_PURGE_SQL), {"oid": str(oid), "n": left})
                left -= int(gone.rowcount or 0)
    except QueryError:
        raise StoreBusy(_BUSY_MESSAGE) from None
    return limit - left


async def create_record(db: Any, actor: str, object_id: str, data: Mapping[str, Any], *,
                        package_id: str, viewer: Viewer) -> Record | None:
    """Create one record of the object *object_id* of the package *package_id*.
    Answers None when the bound org holds no such object in that package.

    Each value is checked by ``studio_fields.validate_record``, and a bad one
    raises :class:`FieldError` with its field key. The record and its hot rows
    go in one statement. This is a batch of one, so a rule of the object runs,
    and a refusal raises
    :class:`~acb_common.studio_rules.ConstraintViolation`. The scope of
    *viewer* binds (C8a), and a refusal of it raises :class:`AccessDenied`.
    """
    who = _actor(actor)
    made = await _one_write(db, who, package_id, Write("create", object_id, data=data), viewer)
    return made if isinstance(made, Record) else None


def _check_viewer(viewer: Any) -> Viewer:
    """C8a. The viewer is a :class:`~acb_common.studio_access.Viewer`, which
    the caller builds from the session."""
    if not isinstance(viewer, Viewer):
        raise ObjectStoreError("viewer must be a Viewer of the session's member")
    return viewer


@dataclass(frozen=True)
class _View:
    """C8a. What one viewer reads of one object: the scope of the viewer's
    roles, the rules of the object, and the roll-up targets that it hides."""

    scope: Scope
    rules: tuple[Rule, ...] = ()
    tainted: frozenset[str] = frozenset()

    def hidden(self, data: Mapping[str, Any]) -> frozenset[str]:
        """The keys hidden on the record *data*, with each formula that reads one."""
        if self.scope.open and not self.tainted:
            return frozenset()
        return with_formulas(self.scope.hidden_for(data) | self.tainted, self.rules)

    def maybe_hidden(self) -> frozenset[str]:
        """The keys that a filter, a sort and a write refuse."""
        return with_formulas(self.scope.maybe_hidden() | self.tainted, self.rules)

    def shown(self, record: Record) -> Record:
        """*record* with each hidden key gone."""
        hidden = self.hidden(record.data)
        if not hidden:
            return record
        return replace(record, data={k: v for k, v in record.data.items() if k not in hidden})


async def _tainted(db: Any, pid: str, rules: Sequence[Rule], viewer: Viewer,
                   owner: bool) -> frozenset[str]:
    """The roll-up targets of an object with *rules* that *viewer* may not
    read (C8a). One statement, only for a roll-up and a viewer who is not the
    owner. A source that the package no longer holds hides the target."""
    names = sorted({str(r.source) for r in rules if r.kind == ROLLUP and r.source is not None})
    if owner or not names:
        return frozenset()
    rows = (await db.execute(text(_SOURCE_ACCESS_SQL), {"pid": pid, "names": names})).all()
    sources = {str(r[0]): scope_for(stored_access(_json(r[1])), viewer, owner=False) for r in rows}
    return rollup_hidden(rules, sources)


async def _view(db: Any, oid: str, pid: str, viewer: Viewer) -> _View | None:
    """The view of *viewer* on the object *oid* of the package *pid*, or None
    when the package holds no such object. The owner comes from the ``apps``
    row of the package, never from the caller."""
    row = (await db.execute(text(_VIEW_SQL), {"oid": oid, "pid": pid})).first()
    if row is None:
        return None
    owner = row[2] is not None and str(row[2]) == viewer.email
    rules = stored_rules(_json(row[0]))
    scope = scope_for(stored_access(_json(row[1])), viewer, owner=owner)
    return _View(scope, rules, await _tainted(db, pid, rules, viewer, owner))


def _scoped(sql: str, scoped_sql: str, scope: Scope, binds: dict[str, Any]) -> str:
    """*scoped_sql* with the binds of the row rule when one binds *scope*,
    else *sql*."""
    if not scope.row_bound:
        return sql
    binds["akeys"] = list(scope.where_keys)
    binds["me"] = scope.me
    return scoped_sql


async def get_record(db: Any, object_id: str, record_id: str, *,
                     package_id: str, viewer: Viewer) -> Record | None:
    """The record *record_id* of the object *object_id* of the package
    *package_id*, or None.

    C8a: a record that the row rule of *viewer* does not read answers None, as
    a record that is not there does, and each hidden key leaves the answer."""
    who = _check_viewer(viewer)
    oid = _uuid(object_id, "object_id")
    binds = {"oid": oid, "rid": _uuid(record_id, "record_id"),
             "pid": _uuid(package_id, "package_id")}
    view = await _view(db, oid, binds["pid"], who)
    if view is None or not view.scope.reads:
        return None
    sql = _scoped(_GET_RECORD_SQL, _GET_RECORD_SCOPED_SQL, view.scope, binds)
    row = (await db.execute(text(sql), binds)).first()
    return None if row is None else view.shown(_record(row))


async def update_record(db: Any, actor: str, object_id: str, record_id: str,
                        changes: Mapping[str, Any], *, package_id: str,
                        viewer: Viewer) -> Record | None:
    """Change the record *record_id* of the object *object_id* of the package
    *package_id*. Answers None when the bound org holds no such record, and
    when the row rule of *viewer* does not read it (C8a).

    *changes* names the fields that change. A ``None`` value clears a field.
    The merged record is checked whole, so a required field stays filled.
    The record and its hot rows change in one statement. This is a batch of
    one, so a rule of the object runs.
    """
    who = _actor(actor)
    write = Write("update", object_id, record_id, changes)
    done = await _one_write(db, who, package_id, write, viewer)
    return done if isinstance(done, Record) else None


async def delete_record(db: Any, actor: str, object_id: str, record_id: str, *,
                        package_id: str, viewer: Viewer) -> bool:
    """Delete the record *record_id* of the object *object_id* of the package
    *package_id*, and its hot rows by cascade. Answers False when the bound org
    holds no such record, and when the row rule of *viewer* does not read it
    (C8a). This is a batch of one, so a rule of the object runs.

    The deleted record keeps no actor, but a roll-up parent of it gets a derived
    write (C7b), and that write names *actor* in ``updated_by``, as a batch does.
    """
    who = _actor(actor)
    write = Write("delete", object_id, record_id)
    return await _one_write(db, who, package_id, write, viewer) is True


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
    "relation": "v_text", "person": "v_text",
}
assert set(_HOT_COLUMN) == set(studio_query.OPS_BY_TYPE), "each type of the op table has a column"

#: The 32 hex digits of an id, with or without the four dashes. The same shape as
#: `studio_fields._UUID_FORM_RE`, so a hot and a cold relation agree.
_UUID_FORM = "'^[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}$'"

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
_RULE_TIMEOUT_MESSAGE = "The rules of this object took too long to check. The batch wrote nothing."
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
    if kind == "relation":
        # The id, in the one form that `studio_fields.relation_key` gives a hot row.
        rid = f"(CASE WHEN jsonb_typeof(r.data -> {kref}) = 'object' THEN r.data -> {kref} ->> 'id' END)"
        return f"(CASE WHEN {rid} ~ {_UUID_FORM} THEN CAST({rid} AS uuid)::text ELSE {rid} END)"
    if kind in ("text", "email", "select", "person"):
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


def _q_access(rid: str) -> str:
    """C8a. The row rule of the viewer on the record id *rid*: a hot row of one
    of the person fields ``:akeys`` holds the viewer ``:me``. One ``EXISTS``
    holds the UNION of the viewer's row rules."""
    return (f"EXISTS (SELECT 1 FROM studio_record_hot ac WHERE ac.{_OF_ORG} AND ac.{_OF_OBJECT} "
            f"AND ac.record_id = {rid} AND ac.field_key = ANY(CAST(:akeys AS text[])) "
            "AND ac.v_text = CAST(:me AS text))")


def _q_where(conditions: Sequence[Condition], params: dict[str, Any], rid: str,
             skip: int | None = None, only_hot: bool = False) -> str:
    """Each condition, with its index in *conditions*, so one statement holds
    one bind for each condition. *only_hot* leaves out a field that is not hot.

    C8a: when *params* holds the viewer (``me``, set by :func:`build_query`
    only), each part also holds the row rule. So the count, the guard, the head
    and the tail of a page read the same records. A check of a rule (C1b) and a
    roll-up (C7b) set no viewer, and they read every record."""
    parts = [_IN_PACKAGE]
    if "me" in params:
        parts.append(_q_access(rid))
    for i, cond in enumerate(conditions):
        if i == skip or (only_hot and not cond.field.hot):
            continue
        parts.append(_q_hot(i, cond, params, rid) if cond.field.hot else _q_cold(i, cond, params))
    return " AND ".join(parts)


def _q_source(conditions: Sequence[Condition], params: dict[str, Any],
              ordered: bool = True) -> tuple[str, int | None]:
    """The candidates ``c`` of a part, and the condition that they meet
    already: the hot rows of the best condition, or the records of the object.
    *ordered* False drops the order of the records, for a read of each
    candidate with no LIMIT (an aggregate of C2b)."""
    ranked = sorted((rank, j) for j, cond in enumerate(conditions)
                    if (rank := _drive_rank(cond)) is not None)
    if not ranked:
        # In the order of the id: the records index `(org, object, id)` gives
        # it, so a LIMIT stops on the index and no plan scans the table. With
        # no LIMIT, that order made the planner pick a Seq Scan and a Sort of
        # an object of 50,000 records (C2b), so an aggregate reads no order.
        order = " ORDER BY r0.id" if ordered else ""
        return (f"(SELECT r0.id FROM studio_records r0 "
                f"WHERE r0.{_OF_ORG} AND r0.{_OF_OBJECT}{order}) c"), None
    j = ranked[0][1]
    cond = conditions[j]
    params[f"fk{j}"] = cond.field.key
    frag = "TRUE" if cond.op == "is_empty" else _q_frag("d", j, cond, params)
    return (f"(SELECT d.record_id AS id FROM studio_record_hot d "
            f"WHERE d.{_OF_ORG} AND d.{_OF_OBJECT} AND d.field_key = CAST(:fk{j} AS text) "
            f"AND {frag}) c"), j


def _q_filtered(conditions: Sequence[Condition], params: dict[str, Any], what: str,
                only_hot: bool = False, gate: str = "TRUE", ordered: bool = True) -> str:
    """``SELECT what`` from the candidates ``c`` that meet each condition.
    *gate* is a test of the whole statement, as the guard of ``m``. *ordered*
    goes to :func:`_q_source`."""
    source, skip = _q_source(conditions, params, ordered)
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
                offset: int, access: tuple[Sequence[str], str] | None = None
                ) -> tuple[str, dict[str, Any]]:
    """The one statement of a page of *query*: its SQL and its binds. Pure.

    Each row holds the total (``t.n``), the count of the guard (``g.n``, or
    NULL with no filter on a field that is not hot), the count of the fields
    that are still hot (``v.n``) and one record. A page past the end gives one
    row with the counts and no record. With no sort, the records
    come in the order of their id. With a sort, the records with a value come
    first, in the order of the hot index and then the id, both in the
    direction of the sort. The records with no value come after, in the
    ascending order of their id.

    C8a: *access* is the row rule of the viewer, ``(person keys, address)``.
    Each part of the statement then holds it (:func:`_q_access`).
    """
    params: dict[str, Any] = {"oid": object_id, "pid": package_id, "lim": limit,
                              "off": offset, "cap": studio_query.TOTAL_CAP,
                              "hk": list(query.hot_keys())}
    if access is not None:
        params["akeys"], params["me"] = list(access[0]), access[1]
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


def build_group_sum(conditions: Sequence[Condition], field_key: str, *, object_id: str,
                    package_id: str) -> tuple[str, dict[str, Any]]:
    """The sum of the hot field *field_key* over the records that meet
    *conditions*, for ``group_sum_min`` (C1b). Pure.

    The candidates are those of a query, so each part names the org, the
    object and the package, and each key and value is a bind. Each candidate
    reads its hot row of the field by the primary key. A record with no value
    has no hot row, and adds nothing to the sum.

    The answer is NULL only when the key has no rows, and then the rule passes
    (``studio_rules.check_group_sum``). A key with rows and no amount gives 0.
    """
    params: dict[str, Any] = {"oid": object_id, "pid": package_id, "sk": field_key}
    ids = _q_filtered(conditions, params, "c.id AS id")
    return (f"SELECT CASE WHEN count(*) > 0 THEN coalesce(sum(s.v_num), 0) END FROM ({ids}) g "
            "LEFT JOIN LATERAL (SELECT s.v_num FROM studio_record_hot s "
            f"WHERE s.{_OF_ORG} AND s.{_OF_OBJECT} AND s.field_key = CAST(:sk AS text) "
            "AND s.record_id = g.id OFFSET 0) s ON true"), params


def _is_timeout(exc: DBAPIError) -> bool:
    """True when the LOCAL ``statement_timeout`` cancelled the statement."""
    return _sqlstate(exc) == _QUERY_CANCELED


class _Timed:
    """The session of a query, under one deadline. Before each statement it
    sets the LOCAL ``statement_timeout`` to the time that is left, and it
    refuses with ``query_timeout`` when none is left."""

    def __init__(self, db: Any, deadline: float, message: str) -> None:
        self._db = db
        self._deadline = deadline
        self._message = message

    async def execute(self, stmt: Any, params: dict[str, Any]) -> Any:
        left = self._deadline - _clock()
        if left <= 0:
            raise QueryError(studio_query.TIMEOUT, self._message)
        await self._db.execute(text(_TIMEOUT_SQL), {"ms": str(max(1, int(left * 1000)))})
        return await self._db.execute(stmt, params)


@asynccontextmanager
async def _time_limit(db: Any, deadline: float,
                      message: str = _TIMEOUT_MESSAGE) -> AsyncIterator[_Timed]:
    """Run the statements of a query in a SAVEPOINT, under one deadline.

    A timeout aborts the SAVEPOINT only, so the caller's transaction stays
    usable. The old timeout comes back after each query, also after a success.
    The checks of a batch (C1b) use it too, with their own *message*.
    """
    old = str((await db.execute(text(_TIMEOUT_READ_SQL))).scalar_one())
    try:
        async with db.begin_nested():
            yield _Timed(db, deadline, message)
    except DBAPIError as exc:
        if _is_timeout(exc):
            raise QueryError(studio_query.TIMEOUT, message) from None
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
                     start: int, access: tuple[Sequence[str], str] | None = None) -> list[Any]:
    """The fields, then the page statement. A field that turned cold after
    the store read the fields has lost its hot rows, so the statement says so,
    and the store reads the fields and runs the query once more."""
    for _ in range(2):
        query = studio_query.parse_query(await _fields(timed, oid), filter, sort)
        sql, params = build_query(query, object_id=oid, package_id=pid, limit=size, offset=start,
                                  access=access)
        rows = (await timed.execute(text(sql), params)).all()
        if int(rows[0][2]) == len(query.hot_keys()):
            _check_hot_or_small(rows, query)
            return rows
    raise QueryError(studio_query.TIMEOUT, _CHANGED_MESSAGE)


def _refuse_hidden_query(view: _View, items: Sequence[Mapping[str, Any]], sort: Any) -> None:
    """C8a. A filter or a sort on a key that is hidden from the viewer would
    show the value, so it is refused before any read of a record."""
    hidden = view.maybe_hidden()
    if not hidden:
        return
    # Fix round 1 (P2): `check_shape` refuses a field name that is not text, and
    # this test of the type stays, so a list never reaches the set (a TypeError).
    for item in items:
        key = item["field"]
        if isinstance(key, str) and key in hidden:
            raise QueryError(studio_query.OP_NOT_ALLOWED,
                             f"Field `{key}` is hidden from you, so a query cannot "
                             "filter by it.", key)
    key = sort.get("field") if isinstance(sort, Mapping) else None
    if isinstance(key, str) and key in hidden:
        raise QueryError(studio_query.OP_NOT_ALLOWED,
                         f"Field `{key}` is hidden from you, so a query cannot sort "
                         "by it.", key)


def _empty_page(start: int, size: int) -> RecordPage:
    """C8a. The page of a viewer whose roles read no record of the object."""
    line = page_line(0, start, 0, noun="records", reach=studio_query.MAX_OFFSET)
    return RecordPage(records=(), total=0, total_at_least=None, offset=start, limit=size, line=line)


async def query_records(db: Any, *, package_id: str, object_id: str, viewer: Viewer,
                        filter: Any = None, sort: Any = None, limit: Any = None,
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

    C8a: the row rule of *viewer* joins that one statement, so the page and
    its total hold only the records that the viewer reads. A filter or a sort
    on a hidden key is refused, and each hidden key leaves each record.
    """
    who = _check_viewer(viewer)
    deadline = _clock() + studio_query.TIMEOUT_MS / 1000
    oid = _uuid(object_id, "object_id")
    pid = _uuid(package_id, "package_id")
    start, size = studio_query.page_bounds(offset, limit)
    items = studio_query.check_shape(filter, sort)
    async with _time_limit(db, deadline) as timed:
        view = await _view(timed, oid, pid, who)
        if view is None:
            return None
        _refuse_hidden_query(view, items, sort)
        if not view.scope.reads:
            return _empty_page(start, size)
        access = (view.scope.where_keys, who.email) if view.scope.row_bound else None
        rows = await _read_page(timed, oid, pid, filter, sort, size, start, access)
    count = int(rows[0][0])
    records = tuple(view.shown(_record(row[3:])) for row in rows if row[3] is not None)
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


# --- the aggregate (C2b) ----------------------------------------------------
#
# ⚠️ The f-string fence of C2a binds each `_q_agg_*` and `build_aggregate` too.
#
# The shape of one aggregate statement (one snapshot serves the groups, the
# total and the check of the currency):
#
#   g, m  (a filter on a field that is not hot) the guard and the candidates,
#         as in a page
#   v     the count of the fields that the aggregate reads as hot and that are
#         hot now
#   x     one row for each candidate: the key of its group, and the hot value
#         of each field that a measure reads. Each read is a lookup of one hot
#         row by its primary key. A money field also reads its currency from
#         `data`, by the primary key of the record, for a record with a value
#   a     one row for each group: the records, and the sum and the count of
#         the values of each field. A money field also keeps its lowest and
#         its highest currency
#   w     the count of the groups, the total over every group, and the lowest
#         and the highest currency of each money field
#   r, p  (a group) the rank of each group, by its records and then its key.
#         `p` holds the groups of rank 1 to `:groups`, then one row `other`
#         for the rest
#
# The candidates are those of a page: the hot rows of one hot condition, or
# the records of the object by the records index. A record with no value of
# the group field has the key NULL, so it joins the group with no value. A
# checkbox has two states, so a record with no value joins `false`.

#: The hot row of the group field, for each candidate.
_GROUP_ROW = (
    "LEFT JOIN LATERAL (SELECT gk.v_text, gk.v_num, gk.v_ts, gk.v_bool FROM studio_record_hot gk "
    f"WHERE gk.{_OF_ORG} AND gk.{_OF_OBJECT} AND gk.field_key = CAST(:gk AS text) "
    "AND gk.record_id = c.id OFFSET 0) gk ON true"
)

#: The key of a text group. `v_text` holds the first 256 characters, so a
#: value of 256 characters reads the whole text from `data`, as `eq` does.
_TEXT_KEY = (
    f"(CASE WHEN gk.v_text IS NULL OR char_length(gk.v_text) < {HOT_TEXT_CHARS} THEN gk.v_text "
    "ELSE (SELECT gr.data ->> CAST(:gk AS text) FROM studio_records gr "
    f"WHERE gr.{_OF_ORG} AND gr.{_OF_OBJECT} AND gr.id = c.id) END)"
)

#: The key of each type of a group with no bucket.
_GROUP_KEY: Mapping[str, str] = {
    "text": _TEXT_KEY, "email": "gk.v_text", "select": "gk.v_text", "relation": "gk.v_text",
    "number": "gk.v_num", "checkbox": "coalesce(gk.v_bool, false)",
}

#: The key of a date or a datetime group: the day or the month in UTC.
_BUCKET_KEY: Mapping[str, str] = {
    "day": "date_trunc('day', gk.v_ts AT TIME ZONE 'UTC')",
    "month": "date_trunc('month', gk.v_ts AT TIME ZONE 'UTC')",
}

#: The key of an aggregate with no group: one group for every record.
_NO_GROUP_KEY = "CAST(NULL AS text)"
assert set(_GROUP_KEY) | {"date", "datetime"} == studio_query.GROUP_TYPES, \
    "each type of a group has a key"

_CURRENCY_MESSAGE = ("Field `{key}` holds amounts in more than one currency, or an amount with no "
                     "currency. A sum never adds two currencies. Narrow the filter.")


@dataclass(frozen=True)
class AggregateGroup:
    """One group of an aggregate (C2b)."""

    #: The key: a text, a number, a bool, or the day (``YYYY-MM-DD``) or the
    #: month (``YYYY-MM``) in UTC. None for the records with no value, for
    #: ``other`` and for the total.
    key: Any
    #: The records of the group.
    count: int
    #: One value for each measure, in the order of the measures. A count is an
    #: ``int``. A sum of a number is a ``Decimal``, and a sum of money is an
    #: ``int`` of minor units. A sum with no value is 0. An average is a
    #: ``Decimal``, or None with no value.
    values: tuple[Any, ...]


@dataclass(frozen=True)
class AggregateResult:
    """The groups of an aggregate (C2b)."""

    measures: tuple[studio_query.Measure, ...]
    #: For each measure: the one currency of a money sum, or None.
    currencies: tuple[str | None, ...]
    #: The groups with the most records, at most
    #: :data:`~acb_common.studio_query.MAX_GROUPS`, in the order of the key.
    #: A select is in the order of its options. The group with no value comes
    #: last. Empty with no group.
    groups: tuple[AggregateGroup, ...]
    #: The groups past the cap, folded into one. None when none fold.
    other: AggregateGroup | None
    #: The count of the groups in :attr:`other`.
    folded: int
    #: The measures over every record that the filter keeps.
    total: AggregateGroup


def _q_agg_key(agg: studio_query.Aggregate, params: dict[str, Any]) -> tuple[str, str]:
    """The key of the group of each candidate, and the join of its hot row."""
    if agg.group is None:
        return _NO_GROUP_KEY, ""
    params["gk"] = agg.group.field.key
    if agg.group.bucket is not None:
        key = _BUCKET_KEY[agg.group.bucket]
    else:
        key = _GROUP_KEY[agg.group.field.type]
    return key, f" {_GROUP_ROW}"


def _q_agg_value(j: int) -> str:
    """The hot row of the field *j* of the measures, for each candidate."""
    return (f"LEFT JOIN LATERAL (SELECT mv{j}.v_num FROM studio_record_hot mv{j} "
            f"WHERE mv{j}.{_OF_ORG} AND mv{j}.{_OF_OBJECT} AND mv{j}.field_key = CAST(:mk{j} AS text) "
            f"AND mv{j}.record_id = c.id OFFSET 0) mv{j} ON true")


def _q_agg_currency(j: int) -> str:
    """The currency of the money field *j*, from ``data``, for a candidate
    with a value. An amount with no currency reads as ``''``, so the check
    fails closed."""
    return (f"(CASE WHEN mv{j}.v_num IS NOT NULL THEN coalesce((SELECT "
            f"mc{j}.data -> CAST(:mk{j} AS text) ->> 'currency' FROM studio_records mc{j} "
            f"WHERE mc{j}.{_OF_ORG} AND mc{j}.{_OF_OBJECT} AND mc{j}.id = c.id), '') END)")


def build_aggregate(agg: studio_query.Aggregate, *, object_id: str,
                    package_id: str) -> tuple[str, dict[str, Any]]:
    """The one statement of the aggregate *agg*: its SQL and its binds. Pure.

    The first row holds the count of the fields that are still hot (``v.n``),
    the count of the guard (or NULL with no filter on a field that is not
    hot), and then the columns of ``w``: the count of the groups, the records,
    and for each field of the measures its sum and its count of values. A
    money field adds its lowest and its highest currency. With a group, each
    row then holds the columns of ``p``: the part (0 for a kept group, 1 for
    ``other``), the rank, the key, the records, and the sum and the count of
    each field. With no group in the result, the row holds NULL there.
    """
    params: dict[str, Any] = {"oid": object_id, "pid": package_id,
                              "hk": list(agg.hot_keys()), "groups": studio_query.MAX_GROUPS}
    conditions = agg.conditions
    guard = "CAST(NULL AS bigint)"
    if agg.query().cold():
        params["small"] = studio_query.SMALL_OBJECT
        count_hot = _q_count(conditions, params, _GUARD_CAP, only_hot=True)
        found = _q_filtered(conditions, params, "c.id AS id", gate=_GUARD_GATE, ordered=False)
        with_sql = f"WITH g AS ({count_hot}), m AS MATERIALIZED ({found}), "
        guard = "(SELECT g.n FROM g)"
        cands = "m c"
    else:
        ids = _q_filtered(conditions, params, "c.id AS id", ordered=False)
        with_sql = "WITH "
        cands = f"({ids}) c"
    key, joins = _q_agg_key(agg, params)
    cols = sums = totals = keep = rest = ""
    for j, field in enumerate(agg.measure_fields()):
        params[f"mk{j}"] = field.key
        joins += f" {_q_agg_value(j)}"
        cols += f", mv{j}.v_num AS n{j}"
        sums += f", sum(x.n{j}) AS s{j}, count(x.n{j}) AS c{j}"
        totals += f", sum(a.s{j}) AS ts{j}, sum(a.c{j}) AS tc{j}"
        keep += f", r.s{j}, r.c{j}"
        rest += f", sum(r.s{j}), sum(r.c{j})"
        if field.type == "money":
            cur = _q_agg_currency(j)
            cols += f", {cur} AS q{j}"
            sums += f", min(x.q{j}) AS lo{j}, max(x.q{j}) AS hi{j}"
            totals += f", min(a.lo{j}) AS tlo{j}, max(a.hi{j}) AS thi{j}"
    head = (f"{with_sql}v AS ({_STILL_HOT_SQL}), "
            f"x AS (SELECT {key} AS k{cols} FROM {cands}{joins}), "
            f"a AS (SELECT x.k, count(*) AS n{sums} FROM x GROUP BY x.k), "
            f"w AS (SELECT count(*) AS groups, coalesce(sum(a.n), 0) AS n{totals} FROM a)")
    if agg.group is None:
        return f"{head} SELECT v.n, {guard}, w.* FROM v CROSS JOIN w", params
    ranks = "SELECT a.*, row_number() OVER (ORDER BY a.n DESC, a.k ASC NULLS LAST) AS rk FROM a"
    kept = f"SELECT 0 AS part, r.rk, r.k, r.n{keep} FROM r WHERE r.rk <= :groups"
    folded = f"SELECT 1, NULL, NULL, sum(r.n){rest} FROM r WHERE r.rk > :groups HAVING count(*) > 0"
    return (f"{head}, r AS ({ranks}), p AS (({kept}) UNION ALL ({folded})) "
            f"SELECT v.n, {guard}, w.*, p.* FROM v CROSS JOIN w LEFT JOIN p ON true "
            "ORDER BY p.part, p.rk"), params


def _plain_number(value: Decimal) -> Decimal:
    """A number key in one form: ``1`` and ``1.0`` are one group, so the key
    of a whole number has no decimal places."""
    if value == value.to_integral_value():
        return value.quantize(Decimal(1))
    return value.normalize()


def _group_key(group: studio_query.GroupBy, raw: Any) -> Any:
    if raw is None:
        return None
    if group.bucket == "day":
        return raw.date().isoformat()
    if group.bucket == "month":
        return f"{raw.year:04d}-{raw.month:02d}"
    if group.field.type == "number":
        return _plain_number(Decimal(raw))
    if group.field.type == "checkbox":
        return bool(raw)
    return str(raw)


def _group_order(group: studio_query.GroupBy) -> Callable[[AggregateGroup], tuple[Any, ...]]:
    """The order of the kept groups: the key, a select in the order of its
    options, and the group with no value last."""
    options = {o: i for i, o in enumerate(group.field.options or ())}

    def order(g: AggregateGroup) -> tuple[Any, ...]:
        if g.key is None:
            return (2,)
        if group.field.type == "select":
            return (0, options[g.key], "") if g.key in options else (1, 0, str(g.key))
        return (0, g.key)
    return order


def _one_currency(field: FieldSpec, low: Any, high: Any) -> str | None:
    """The one currency of a money sum, or None when no record holds an
    amount. Two currencies, or an amount with no currency, refuse the sum."""
    if low is None and high is None:
        return None
    if low != high or not low:
        raise QueryError(studio_query.OP_NOT_ALLOWED, _CURRENCY_MESSAGE.format(key=field.key),
                         field.key)
    return str(low)


def _measure_values(agg: studio_query.Aggregate, count: int,
                    sums: Mapping[str, tuple[Any, Any]]) -> tuple[Any, ...]:
    """The value of each measure of one group, from its records and the sum
    and the count of the values of each field."""
    out: list[Any] = []
    for measure in agg.measures:
        if measure.field is None:
            out.append(count)
            continue
        total, values = sums[measure.field.key]
        if measure.op == "average":
            out.append(None if not values else Decimal(total) / Decimal(values))
        elif measure.field.type == "money":
            out.append(int(total or 0))
        else:
            out.append(Decimal(total or 0))
    return tuple(out)


def _aggregate_result(agg: studio_query.Aggregate, rows: Sequence[Any]) -> AggregateResult:
    """The result of the rows of :func:`build_aggregate`. A money sum of two
    currencies raises :class:`QueryError` here, before any value leaves."""
    fields = agg.measure_fields()
    first = rows[0]
    at = 2
    n_groups, n_records = int(first[at]), int(first[at + 1])
    at += 2
    totals: dict[str, tuple[Any, Any]] = {}
    currency: dict[str, str | None] = {}
    for field in fields:
        totals[field.key] = (first[at], int(first[at + 1] or 0))
        at += 2
        if field.type == "money":
            currency[field.key] = _one_currency(field, first[at], first[at + 1])
            at += 2
    currencies = tuple(currency.get(m.field.key) if m.field is not None and m.op == "sum" else None
                       for m in agg.measures)
    total = AggregateGroup(None, n_records, _measure_values(agg, n_records, totals))
    if agg.group is None:
        return AggregateResult(agg.measures, currencies, (), None, 0, total)
    kept: list[AggregateGroup] = []
    other: AggregateGroup | None = None
    for row in rows:
        part = row[at]
        if part is None:
            continue  # no record, so no group
        count = int(row[at + 3])
        sums = {f.key: (row[at + 4 + 2 * i], int(row[at + 5 + 2 * i] or 0))
                for i, f in enumerate(fields)}
        values = _measure_values(agg, count, sums)
        if int(part) == 0:
            kept.append(AggregateGroup(_group_key(agg.group, row[at + 2]), count, values))
        else:
            other = AggregateGroup(None, count, values)
    kept.sort(key=_group_order(agg.group))
    return AggregateResult(agg.measures, currencies, tuple(kept), other,
                           max(0, n_groups - studio_query.MAX_GROUPS), total)


async def _read_aggregate(timed: _Timed, oid: str, pid: str, filter: Any, group_by: Any,
                          measures: Any) -> tuple[studio_query.Aggregate, list[Any]]:
    """The fields, then the aggregate statement. A field that turned cold
    after the store read the fields has lost its hot rows, so the store reads
    the fields and runs the aggregate once more, as :func:`_read_page` does."""
    for _ in range(2):
        agg = studio_query.parse_aggregate(await _fields(timed, oid), filter, group_by, measures)
        sql, params = build_aggregate(agg, object_id=oid, package_id=pid)
        rows = (await timed.execute(text(sql), params)).all()
        if int(rows[0][0]) == len(agg.hot_keys()):
            _check_hot_or_small(rows, agg.query())
            return agg, rows
    raise QueryError(studio_query.TIMEOUT, _CHANGED_MESSAGE)


async def aggregate_records(db: Any, *, package_id: str, object_id: str, filter: Any = None,
                            group_by: Any = None,
                            measures: Any = None) -> AggregateResult | None:
    """Group the records of the object *object_id* of the package
    *package_id*, and take a count, a sum or an average of each group (C2b).
    Answers None when the bound org holds no such object in that package.

    ``acb_common.studio_query.parse_aggregate`` checks the input: the filter of
    C2a, one group and the measures, each on a hot field. A refusal raises
    :class:`~acb_common.studio_query.QueryError`, and the session stays usable.
    One statement reads the groups, the total and the currency of a money sum,
    under the one deadline of :data:`~acb_common.studio_query.TIMEOUT_MS` and
    the guard ``hot_or_small``. A money sum is refused unless the ``data`` of
    each record that it adds holds one currency, as the CRM does (HANDOFF
    H-333). The :data:`~acb_common.studio_query.MAX_GROUPS` groups with the
    most records stay, and the rest fold into :attr:`AggregateResult.other`.
    """
    deadline = _clock() + studio_query.TIMEOUT_MS / 1000
    oid = _uuid(object_id, "object_id")
    pid = _uuid(package_id, "package_id")
    studio_query.check_aggregate_shape(filter, group_by, measures)
    async with _time_limit(db, deadline) as timed:
        found = await timed.execute(text(_QUERY_OBJECT_SQL), {"oid": oid, "pid": pid})
        if found.first() is None:
            return None
        agg, rows = await _read_aggregate(timed, oid, pid, filter, group_by, measures)
    return _aggregate_result(agg, rows)


# --- the batch (C1b) --------------------------------------------------------
#
# The steps of one batch, in one SAVEPOINT:
#
#   1. the org lock, once
#   2. the object rows, in ascending object id, then the fields and the rules
#   3. the record rows, in ascending (object_id, record_id)
#   4. the writes, in list order
#   5. `range` on the last value of each written record
#   6. `unique` and `group_sum_min`, once for each touched key. On a floor of
#      0 or less, a key that the batch does not lower reads nothing.
#
# Steps 5 and 6 run after all the writes, so a stock move is judged on the net.
# Step 6 reads in a later statement than the lock, so its snapshot holds each
# committed write of the org (READ COMMITTED).

_WRITE_OPS = ("create", "update", "delete")


@dataclass(frozen=True)
class _Held:
    """An object of a batch, under its row lock."""

    name: str
    fields: list[FieldSpec]
    rules: tuple[Rule, ...]
    #: The formulas of the object, checked once for the batch (C7a). None when it has none.
    plan: FormulaPlan | None = None
    #: C8a. The scope of the viewer of the batch on this object.
    scope: Scope | None = None
    #: C8a. The roll-up targets of this object that the viewer may not read.
    tainted: frozenset[str] = frozenset()
    #: C8a fix round 1 (P1-4). The keys that the viewer may not write, because
    #: a rule answers from data that the viewer cannot read.
    blocked: frozenset[str] = frozenset()

    def view(self) -> _View:
        """The view of the viewer of the batch, as a read builds it."""
        assert self.scope is not None
        return _View(self.scope, self.rules, self.tainted)

    @property
    def computed(self) -> frozenset[str]:
        """The keys that a formula or a roll-up fills. No caller writes them."""
        return frozenset(str(r.target) for r in self.rules if r.kind in (FORMULA, ROLLUP))


@dataclass
class _Mark:
    """One record that a batch writes. ``before`` is its data before the
    batch, and ``after`` its data now. None means no such record."""

    object_id: str
    index: int
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    #: True for a parent that only a roll-up changed (C7b). A formula of the parent
    #: can read a roll-up target and fill a key, so its key rules run too. A key rule
    #: that reads no field that the derived write changed is skipped
    #: (:func:`_derived_moves`).
    derived: bool = False


class _Batch:
    """The state of one batch. ``index`` names the write that runs now, so an
    error gets its place in the list."""

    def __init__(self, who: str, pid: str, steps: Sequence[Write], viewer: Viewer) -> None:
        self.who = who
        self.pid = pid
        self.steps = steps
        #: C8a. The viewer of the batch, and whether the viewer owns the package.
        self.viewer = viewer
        self.owner = False
        self.objects: dict[str, _Held] = {}
        self.marks: dict[tuple[str, str], _Mark] = {}
        self.index = 0
        #: The roll-ups that read an object of this batch (C7b): the object id of
        #: the parent, its name and the rule.
        self.rollups: list[tuple[str, str, Rule]] = []
        #: The id of each object of the batch, by name.
        self.names: dict[str, str] = {}
        #: The fields that any roll-up of the package reads, for each source name.
        self.readers: dict[str, set[str]] = {}
        #: The first write that made each parent stale, for the place of an error.
        self.firsts: dict[tuple[str, str], int] = {}
        #: The lines relation of each line object of the batch (C13), by its id.
        self.lines: dict[str, _Lines] = {}
        #: The id of the record that each create of the batch made, by its place.
        self.made: dict[int, str] = {}
        #: The last write that gave each line a new parent (C13), for the place
        #: of a refusal.
        self.moved: dict[tuple[str, str], int] = {}


@dataclass(frozen=True)
class _Lines:
    """One lines relation of the package (C13): the parent object, its field,
    and ``via``, the field of the line object that names the parent."""

    parent_id: str
    parent: str
    key: str
    via: str


def _clean_write(write: Any) -> Write:
    """One write with its ids in the UUID form. Runs no statement."""
    if not isinstance(write, Write) or write.op not in _WRITE_OPS:
        raise ObjectStoreError(f"A write is a Write with the op {', '.join(_WRITE_OPS)}.")
    oid = _uuid(write.object_id, "object_id")
    if write.parent is not None and (
            write.op == "delete" or type(write.parent) is not int or write.parent < 0):
        raise ObjectStoreError("parent is the place of an earlier write, on a create or an update.")
    if write.op == "create":
        if write.record_id is not None:
            raise ObjectStoreError("A create takes no record_id, because the store gives the id.")
        return replace(write, object_id=oid)
    rid = _uuid(write.record_id, "record_id")
    if write.op == "update" and not isinstance(write.data, Mapping):
        raise ObjectStoreError("changes must be an object of field values")
    if write.op == "delete" and write.data is not None:
        raise ObjectStoreError("A delete takes no data.")
    return replace(write, object_id=oid, record_id=rid)


def _clean_writes(writes: Any) -> list[Write]:
    """The checks of a batch that need no statement, the cap first."""
    if isinstance(writes, str | bytes | Mapping) or not isinstance(writes, Sequence):
        raise ObjectStoreError("writes must be a list of writes")
    if len(writes) > MAX_WRITES_PER_BATCH:
        raise BudgetExceeded(f"A batch holds at most {MAX_WRITES_PER_BATCH} writes.")
    out: list[Write] = []
    for i, write in enumerate(writes):
        try:
            clean = _clean_write(write)
            if clean.parent is not None and (
                    clean.parent >= i or out[clean.parent].op not in ("create", "update")):
                raise ObjectStoreError(
                    "parent is the place of an earlier create or update in the batch.")
            out.append(clean)
        except ObjectStoreError as exc:
            raise BatchError(i, exc) from exc
    return out


async def _find_rollups(db: Any, batch: _Batch) -> None:
    """Step 1b (C7b). Find the parent objects: the objects of the package whose
    rules hold a roll-up that reads an object of this batch. The org lock is
    held, and every change of a rule takes it too, so the answer is stable.
    A package with no roll-up costs one statement."""
    found = await _package_rollups(db, batch.pid, strict=True)
    if not found:
        return
    rows = (await db.execute(text(_OBJECT_NAMES_SQL), {
        "pid": batch.pid, "oids": sorted({s.object_id for s in batch.steps})})).all()
    batch.names = {str(r[1]): str(r[0]) for r in rows}
    for oid, owner, rule in found:
        batch.readers.setdefault(str(rule.source), set()).update(rule.source_keys())
        if rule.source in batch.names:
            batch.rollups.append((oid, owner, rule))


async def _lock_objects(db: Any, batch: _Batch) -> None:
    """Step 2. The second statement of each object follows its row lock. The
    parent objects of the roll-ups join the list, so all come in one order."""
    first: dict[str, int] = {}
    writes: set[str] = set()
    for i, step in enumerate(batch.steps):
        first.setdefault(step.object_id, i)
        if step.op != "delete":
            writes.add(step.object_id)
    for parent, _, _ in batch.rollups:
        first.setdefault(parent, 0)
        writes.add(parent)  # a derived write computes the formulas of the parent
    stepped = {step.object_id for step in batch.steps}
    for oid in sorted(first):
        batch.index = first[oid]
        row = (await db.execute(text(_SHARE_OBJECT_SQL), {"oid": oid, "pid": batch.pid})).first()
        if row is None:
            raise RecordNotFound("This package holds no such object.")
        fields = await _fields(db, oid)
        rules = stored_rules(_json(row[1]))
        # A delete computes nothing, so only a create or an update builds the plan.
        plan = plan_formulas(rules, str(row[0]), fields) if oid in writes else None
        batch.owner = row[3] is not None and str(row[3]) == batch.viewer.email
        scope = scope_for(stored_access(_json(row[2])), batch.viewer, owner=batch.owner)
        if oid in stepped and not scope.writes:
            raise AccessDenied(f"Your roles in this app cannot change records of `{row[0]}`.")
        tainted = await _tainted(db, batch.pid, rules, batch.viewer, batch.owner)
        # Fix round 1 (P1-4): the roll-ups of other objects that read this one.
        readers = [r for _, _, r in batch.rollups if r.source == str(row[0])]
        hidden = _View(scope, rules, tainted).maybe_hidden()
        blocked = blocked_keys(scope, rules, hidden, readers)
        batch.objects[oid] = _Held(str(row[0]), fields, rules, plan, scope, tainted, blocked)


async def _lock_records(db: Any, batch: _Batch) -> None:
    """Step 3. A record that two writes name is locked once."""
    first: dict[tuple[str, str], int] = {}
    for i, step in enumerate(batch.steps):
        if step.record_id is not None:
            first.setdefault((step.object_id, step.record_id), i)
    for (oid, rid), index in sorted(first.items()):
        batch.index = index
        binds = {"oid": oid, "rid": rid, "pid": batch.pid}
        scope = batch.objects[oid].scope
        assert scope is not None
        # C8a: a record that the row rule of the viewer does not read is not found.
        sql = _scoped(_LOCK_RECORD_SQL, _LOCK_RECORD_SCOPED_SQL, scope, binds)
        row = (await db.execute(text(sql), binds)).first()
        if row is None:
            raise RecordNotFound("This package holds no such record.")
        data = dict(_json(row[2]))
        batch.marks[(oid, rid)] = _Mark(oid, index, data, data)


def _live_mark(batch: _Batch, step: Write) -> _Mark:
    """The mark of the record of an update or a delete. An earlier write of
    the batch may have deleted it."""
    mark = batch.marks[(step.object_id, str(step.record_id))]
    if mark.after is None:
        raise RecordNotFound("An earlier write of this batch deleted this record.")
    return mark


# --- the lines (C13) ------------------------------------------------------------
#
# A lines relation is a relation field of a parent object, with `lines` naming
# the field of the line object (`via`) that points back to the parent. The
# parent holds no value for it. The door saves a parent and its lines as ONE
# batch, so they are written all or none. Three steps join the batch:
#
#   1c. `_find_lines` reads the lines relations of the package after the org
#       lock, and `_check_lines_fit` checks each one against the fields of its
#       line object after the object locks. A relation that does not fit
#       refuses the batch (it fails closed).
#   4.  A write with `parent` is linked to the create or the update of its parent
#       earlier in the batch (`_link`). A linked create gets `via` from that
#       record, and a linked update must be a line of it. Neither names `via`.
#   4a. `_check_line_parents` runs after the writes. A line that names a new
#       parent by itself, on a create or a move, must name a record of the
#       parent object of this package. A record of another package or another
#       org is not found, so the line is refused.


def _parent_of(via: str, data: Mapping[str, Any] | None) -> str | None:
    """The parent id that a line names in its field *via*, in the one form of
    a hot relation, or None."""
    raw = None if data is None else data.get(via)
    if isinstance(raw, Mapping) and isinstance(raw.get("id"), str):
        return relation_key(raw["id"])
    return None


def _link(batch: _Batch, step: Write) -> tuple[_Lines, str] | None:
    """The lines relation of a linked write, and the id of its parent record,
    or None for a write with no ``parent``. Runs no statement."""
    if step.parent is None:
        return None
    link = batch.lines.get(step.object_id)
    parent = batch.steps[step.parent]
    if link is None or parent.object_id != link.parent_id:
        raise RecordError("This write is not a line of the record of its parent write.")
    if isinstance(step.data, Mapping) and link.via in step.data:
        raise FieldError(link.via, f"Field `{link.via}` of a line comes from the record that "
                         "it is saved with. Remove it from the line.")
    rid = batch.made[step.parent] if parent.op == "create" else str(parent.record_id)
    return link, rid


async def _find_lines(db: Any, batch: _Batch) -> None:
    """Step 1c (C13). The lines relation of each line object of the batch. The
    org lock is held, and every change of a field takes it too, so the answer is
    stable. One statement. An object that two relations name refuses the batch,
    because its parent would be unclear."""
    rows = (await db.execute(text(_LINES_SQL), {"pid": batch.pid})).all()
    first: dict[str, int] = {}
    for i, step in enumerate(batch.steps):
        first.setdefault(step.object_id, i)
    for parent_id, parent, key, via, child_id, child in rows:
        if child_id is None or str(child_id) not in first:
            continue
        if str(child_id) in batch.lines:
            batch.index = first[str(child_id)]
            raise RecordError(f"Object `{child}` is the lines of two fields. "
                              "Publish the app again with one.")
        batch.lines[str(child_id)] = _Lines(str(parent_id), str(parent), str(key), str(via))


def _check_lines_fit(batch: _Batch) -> None:
    """Step 1c (C13), after the object locks. Fail closed: a stored lines
    relation that does not fit its line object now refuses the batch. The rule
    is ``studio_fields.lines_problem``, the one that A2 runs."""
    for oid, link in batch.lines.items():
        held = batch.objects[oid]
        spec = FieldSpec(key=link.key, type="relation", to=held.name, lines=link.via)
        problem = lines_problem(link.parent, spec, held.name, held.fields)
        if problem is not None:
            batch.index = next(i for i, s in enumerate(batch.steps) if s.object_id == oid)
            raise RecordError(f"The lines of `{link.parent}` do not fit now. {problem}")


def _plain_id(value: str) -> bool:
    """True for a record id in its one form: 36 lower-case characters with 4
    dashes. Python also reads `urn:uuid:`, `uuid:`, braces, capitals and 32 digits
    as a UUID, and Postgres refuses some of them, so the store takes none of them
    for a parent."""
    try:
        return str(uuid.UUID(value)) == value
    except ValueError:
        return False


def _bad_parent(link: _Lines) -> FieldError:
    return FieldError(link.via, f"Field `{link.via}` takes a record id of `{link.parent}` in "
                      "one form: 36 lower-case characters with 4 dashes. Send the id as the "
                      "app gave it.")


def _line_moves(batch: _Batch, oid: str, before: Mapping[str, Any] | None,
                after: Mapping[str, Any]) -> bool:
    """True when a write gives a line a new parent (C13). It runs before the
    statement of the write. A write that sets the parent field must send the
    one form of the id, also for the same parent, so a refusal comes before
    any statement reads it. A write that leaves the value as it is checks
    nothing, so an old value never blocks another change of the line."""
    link = batch.lines.get(oid)
    raw = None if link is None else after.get(link.via)
    rid = raw.get("id") if isinstance(raw, Mapping) else None
    if link is None or not isinstance(rid, str):
        return False
    old = None if before is None else before.get(link.via)
    if isinstance(old, Mapping) and old.get("id") == rid:
        return False
    if not _plain_id(rid):
        raise _bad_parent(link)
    return before is None or relation_key(rid) != _parent_of(link.via, before)


def _new_parents(batch: _Batch) -> dict[str, dict[str, tuple[int, _Lines]]]:
    """The parent ids that the lines of the batch name by themselves, by
    parent object, each with the last write that moved the line. A write that
    keeps the parent of the line names none.

    A linked create took its parent from the batch, so it needs no read. Its
    parent must still be there at the end of the batch: a later delete of the
    parent refuses the line. An id of another form is refused again here, so
    no such id reaches the statement."""
    out: dict[str, dict[str, tuple[int, _Lines]]] = {}
    for (oid, rid), mark in sorted(batch.marks.items(), key=lambda kv: kv[1].index):
        link = batch.lines.get(oid)
        if link is None or mark.after is None:
            continue
        new = _parent_of(link.via, mark.after)
        if new is None or (mark.before is not None and new == _parent_of(link.via, mark.before)):
            continue
        batch.index = batch.moved.get((oid, rid), mark.index)
        if not _plain_id(new):
            raise _bad_parent(link)
        if mark.before is None and batch.steps[mark.index].parent is not None:
            parent = batch.marks.get((link.parent_id, new))
            if parent is None or parent.after is None:
                raise _no_parent(link)
            continue
        out.setdefault(link.parent_id, {}).setdefault(new, (batch.index, link))
    return out


async def _parent_scope(db: Any, batch: _Batch, oid: str) -> Scope:
    """C8a. The scope of the viewer of the batch on the parent object *oid*.
    A parent object that the batch writes has its scope already. A parent
    object that is gone reads nothing."""
    held = batch.objects.get(oid)
    if held is not None and held.scope is not None:
        return held.scope
    if batch.owner:
        return Scope(batch.viewer.email, True)
    rows = (await db.execute(text(_OBJECT_ACCESS_SQL), {"oid": oid, "pid": batch.pid})).all()
    if not rows:
        return Scope(batch.viewer.email, False)
    return scope_for(stored_access(_json(rows[0][0])), batch.viewer, owner=False)


def _no_parent(link: _Lines) -> FieldError:
    return FieldError(link.via, f"Field `{link.via}` names no `{link.parent}` record of this "
                      f"app. Pick a record of `{link.parent}` in this app.")


async def _check_line_parents(db: Any, batch: _Batch, deadline: float) -> None:
    """Step 4a (C13). Each new parent of a line is a record of the parent
    object of this package. One statement for each parent object, under the one
    deadline of the batch. The org lock is held, and every record write takes
    it first, so no parent goes before the commit."""
    wanted = _new_parents(batch)
    if not wanted:
        return
    async with _time_limit(db, deadline, _RULE_TIMEOUT_MESSAGE) as timed:
        for parent_id, ids in sorted(wanted.items()):
            binds = {"oid": parent_id, "pid": batch.pid, "ids": sorted(ids)}
            # C8a: a parent that the viewer cannot read is not found, so its
            # refusal is the one of a parent that is not there.
            scope = await _parent_scope(timed, batch, parent_id)
            sql = _scoped(_LINE_PARENTS_SQL, _LINE_PARENTS_SCOPED_SQL, scope, binds)
            found = {str(r[0]) for r in (await timed.execute(text(sql), binds)).all()}
            for rid, (index, link) in sorted(ids.items(), key=lambda kv: kv[1][0]):
                if rid not in found:
                    batch.index = index
                    raise _no_parent(link)


def _refuse_hidden_write(held: _Held, data: Any) -> None:
    """C8a. A write that names a key hidden from the viewer is refused: the
    viewer cannot read the value back, and a refusal of it would show it."""
    if held.scope is None or held.scope.open or not isinstance(data, Mapping):
        return
    hidden = held.scope.maybe_hidden()
    for key in sorted(str(k) for k in data if k in hidden):
        raise FieldError(key, f"Field `{key}` is hidden from you, so a write cannot set it.")


def _changed(held: _Held, before: Mapping[str, Any] | None,
             after: Mapping[str, Any]) -> set[str]:
    """The keys that one write of the viewer sets or changes. The keys that a
    formula or a roll-up fills are left out, because the store fills them."""
    old = before or {}
    keys = {k for k in set(old) | set(after) if old.get(k) != after.get(k)}
    return keys - held.computed


def _check_write(held: _Held, before: Mapping[str, Any] | None,
                 after: Mapping[str, Any] | None) -> None:
    """C8a and fix round 1. The rules of one write of a viewer that a row rule
    binds (``Scope.write_problem`` and ``studio_access.blocked_keys``). Each
    refusal is :class:`AccessDenied`, and its text names the object and at
    most one key that the viewer can see. It names no value, and it is the
    same for each value, so a viewer learns nothing by trying values. The
    owner and a viewer of an open object pass."""
    scope = held.scope
    if scope is None:
        return
    problem = scope.write_problem(before, after)
    if problem is not None:
        code, key = problem
        if code == "row":
            raise AccessDenied(f"Your roles in this app cannot change this record of `{held.name}`.")
        if code == "leaves":
            keys = ", ".join(f"`{k}`" for k in scope.write_keys)
            raise AccessDenied(f"This write leaves a record of `{held.name}` that you cannot "
                               f"change. Name yourself in {keys or 'the record'}.")
        if code == "guarded":
            raise AccessDenied(f"Your roles cannot set `{key}` of `{held.name}`. A role that "
                               "changes every record sets it.")
        raise AccessDenied(f"This write would show you more of this record of `{held.name}`, "
                           "so your roles cannot make it.")
    if after is None or not held.blocked:
        return
    for key in sorted(_changed(held, before, after) & held.blocked):
        raise AccessDenied(f"Your roles cannot set `{key}` of `{held.name}`, because a rule "
                           "over data that you cannot read uses it.")


async def _apply_write(db: Any, batch: _Batch, step: Write) -> Record | bool:
    """Step 4, for one write. Only this function runs the three write statements."""
    oid = step.object_id
    held = batch.objects[oid]
    plain = held.fields if held.plan is None else list(held.plan.plain)
    linked = _link(batch, step)
    if step.op == "create":
        _refuse_hidden_write(held, step.data)
        if linked is not None and isinstance(step.data, Mapping):
            # C13: the line takes its parent from the batch, never from its data.
            step = replace(step, data={**step.data, linked[0].via: {"id": linked[1]}})
        data = with_initial(held.rules, step.data)
        refuse_computed_keys(held.plan, held.name, data)
        data = with_rollup_zero(held.plan, data)
        clean = run_formulas(held.plan, held.name, validate_record(plain, data))
        _check_write(held, None, clean)
        moves = _line_moves(batch, oid, None, clean)
        row = (await db.execute(text(_CREATE_RECORD_SQL), {
            "oid": oid, "data": record_json(clean), "actor": batch.who,
            "hot": _hot_rows(held.fields, clean)})).one()
        made = _record(row)
        batch.marks[(oid, made.id)] = _Mark(oid, batch.index, None, clean)
        batch.made[batch.index] = made.id
        if moves:
            batch.moved[(oid, made.id)] = batch.index
        return made
    mark = _live_mark(batch, step)
    if linked is not None and _parent_of(linked[0].via, mark.after) != linked[1]:
        raise RecordError("A line of this save is not a line of this record. "
                          "Change it through its own record.")
    binds = {"oid": oid, "rid": step.record_id, "pid": batch.pid}
    if step.op == "delete":
        # Fix round 1 (P1-1): a grant that reads the record must also write.
        _check_write(held, mark.after, None)
        await db.execute(text(_DELETE_RECORD_SQL), binds)
        mark.after = None
        return True
    changes = dict(step.data or {})
    _check_write(held, mark.after, None)
    _refuse_hidden_write(held, changes)
    refuse_computed_keys(held.plan, held.name, changes)
    merged = without_targets(held.plan, {**(mark.after or {}), **changes})
    clean = run_formulas(held.plan, held.name, validate_record(plain, merged))
    _check_write(held, mark.after, clean)
    if _line_moves(batch, oid, mark.after, clean):
        batch.moved[(oid, str(step.record_id))] = batch.index
    row = (await db.execute(text(_UPDATE_RECORD_SQL), {
        **binds, "data": record_json(clean), "actor": batch.who,
        "hot": _hot_rows(held.fields, clean)})).one()
    mark.after = clean
    return _record(row)


def _derived_moves(rule: Rule, mark: _Mark) -> bool:
    """True when the derived write of a roll-up parent changed a field that
    *rule* reads (C7b). A derived write changes only computed fields, so a key
    rule that reads none of them cannot break, and the store reads no key for
    it. An old clash on such a key then does not block a child write."""
    before, after = mark.before or {}, mark.after or {}
    return any(before.get(k) != after.get(k) for k in rule_reads(rule))


def _check_records(batch: _Batch) -> dict[tuple[str, Rule, tuple[Any, ...]], int]:
    """Step 5, and the keys of step 6, each with its first write. On a floor
    of 0 or less, a key that the batch does not lower stays out: the values of
    the batch prove that its sum cannot fall, and the org lock holds each
    other writer out. A floor above 0 always stays in."""
    keys: dict[tuple[str, Rule, tuple[Any, ...]], int] = {}
    nets: dict[tuple[str, Rule, tuple[Any, ...]], Decimal] = {}
    for mark in sorted(batch.marks.values(), key=lambda m: m.index):
        batch.index = mark.index
        held = batch.objects[mark.object_id]
        for rule in held.rules:
            check_kind_known(rule, held.name)
            check_rule_is_hot(rule, held.name, held.fields)
            if rule.kind == RANGE and mark.after is not None:
                check_range(rule, held.name, mark.after)
            if rule.kind == STATE_MACHINE:
                check_move(rule, held.name, mark.before, mark.after)
            if mark.derived and not _derived_moves(rule, mark):
                continue
            for key in touched_keys(rule, mark.before, mark.after):
                at = (mark.object_id, rule, key)
                keys.setdefault(at, mark.index)
                if floor_holds_at_zero(rule):
                    nets[at] = add_change(rule, key, mark.before, mark.after,
                                          nets.get(at, Decimal(0)))
    return {at: index for at, index in keys.items() if at not in nets or nets[at] < 0}


async def _check_key(timed: _Timed, oid: str, pid: str, held: _Held, rule: Rule,
                     key: tuple[Any, ...]) -> None:
    """Step 6, for one key. The filter of the key runs through the grammar of
    C2a, and the statement comes from its builders. So it reads hot rows only.
    The one exception is a key of 256 characters or more: C2a has no hot
    candidates for it, so it reads the records of the object, under the deadline."""
    conditions = studio_query.parse_query(held.fields, key_filter(rule, key)).conditions
    if rule.kind == UNIQUE:
        sql, params = build_count(conditions, object_id=oid, package_id=pid, cap=2)
        count = (await timed.execute(text(sql), params)).scalar_one()
        check_unique_count(rule, held.name, int(count))
        return
    sql, params = build_group_sum(conditions, str(rule.field), object_id=oid, package_id=pid)
    total = (await timed.execute(text(sql), params)).scalar_one()
    check_group_sum(rule, held.name, None if total is None else Decimal(total))


async def _check_keys(db: Any, batch: _Batch,
                      keys: Mapping[tuple[str, Rule, tuple[Any, ...]], int],
                      deadline: float) -> None:
    """Step 6, under the one *deadline* of the batch, which the recompute of the
    roll-ups (C7b) shares. A timeout refuses the batch."""
    if not keys:
        return
    async with _time_limit(db, deadline, _RULE_TIMEOUT_MESSAGE) as timed:
        for (oid, rule, key), index in keys.items():
            batch.index = index
            await _check_key(timed, oid, batch.pid, batch.objects[oid], rule, key)


# --- the roll-ups (C7b) -----------------------------------------------------
#
# The batch gets four more steps. Step 1b finds the parent objects (`_find_rollups`),
# and step 2 locks them with the others, in ascending id. Step 4b runs after all
# the writes:
#
#   1. `rollup_parents` names the parents that the net of the batch made stale (a
#      skip rule: only a change of the relation, the read field or a `where` field
#      counts).
#   2. The parent records are locked in ascending `(object_id, record_id)`
#      (`FOR NO KEY UPDATE`). A parent that no record holds is dangling and skipped.
#   3. One aggregate for each (parent, roll-up), then one derived write for each
#      parent. Both run under the one deadline of the batch. The aggregate of a money
#      roll-up also names the written children that it counts, and the currency of
#      each one is checked. A child that the roll-up does not count passes.
#
# The derived write is a store write: it re-runs the formulas of the parent,
# validates, rebuilds the hot rows, and sets `updated_at` and `updated_by`. It
# writes no history row. The checks of the parent run in `_check_records`, with the
# marks of the batch: `range`, a state machine, and each `unique` and
# `group_sum_min` that reads a field which the derived write changed. A formula
# can read a roll-up target and fill a key, so a child write can break a key rule
# of its parent, and then the whole batch is refused.

#: The aggregate of a sum, a min and a max over the hot rows of the read field.
_ROLLUP_NUMBER_AGG: Mapping[str, str] = {
    "sum": "sum(s.v_num)", "min": "min(s.v_num)", "max": "max(s.v_num)"}
_ROLLUP_DATE_AGG: Mapping[str, str] = {"min": "min(s.v_ts)", "max": "max(s.v_ts)"}
#: The ids of the children of `:mine` that the aggregate counts. A money roll-up
#: reads it, so the store checks the currency of each written child that counts.
_ROLLUP_MINE = "array_agg(g.id::text) FILTER (WHERE g.id = ANY(CAST(:mine AS uuid[])))"


def build_rollup(rule: Rule, fields: Sequence[FieldSpec], *, object_id: str, package_id: str,
                 parent_id: str, mine: Sequence[str] = ()) -> tuple[str, dict[str, Any]]:
    """The aggregate of the roll-up *rule* for the parent *parent_id*: its SQL
    and its binds. *fields* and *object_id* are those of the SOURCE. Pure.

    The candidates are the children of one parent, found by the hot rows of the
    relation field, as the index serves them. The ``where`` of the rule joins
    the conditions of a query, and so it reads hot rows too. Each child reads its
    hot row of the read field by the primary key. A child with no value adds
    nothing. The answer is a count, or the aggregate: NULL when no child holds a
    value, and the store gives 0 for a sum then. A money roll-up also answers
    which ids of *mine* (the children that the batch wrote) it counts, or NULL.
    So the one statement that counts a child also decides its currency check.
    """
    via = next(f for f in fields if f.key == rule.via)
    where = parse_query(fields, where_filter(rule)).conditions
    conditions = (Condition(via, "eq", parent_id), *where)
    params: dict[str, Any] = {"oid": object_id, "pid": package_id, "sk": rule.field}
    ids = _q_filtered(conditions, params, "c.id AS id")
    if rule.agg == "count":
        return f"SELECT count(*) FROM ({ids}) g", params
    read = next(f for f in fields if f.key == rule.field)
    what = (_ROLLUP_DATE_AGG if read.type == "date" else _ROLLUP_NUMBER_AGG)[str(rule.agg)]
    if rule.currency is not None:
        params["mine"] = list(mine)
        what = f"{what}, {_ROLLUP_MINE}"
    return (f"SELECT {what} FROM ({ids}) g "
            "LEFT JOIN LATERAL (SELECT s.v_num, s.v_ts FROM studio_record_hot s "
            f"WHERE s.{_OF_ORG} AND s.{_OF_OBJECT} AND s.field_key = CAST(:sk AS text) "
            "AND s.record_id = g.id OFFSET 0) s ON true"), params


def _check_rollups_fit(batch: _Batch) -> None:
    """Fail closed. A stored roll-up that does not fit its object or its source
    now (a source dropped and made again, a field that turned cold by raw SQL)
    refuses the batch, and never gives a sum from fields it cannot read."""
    for oid, owner, rule in batch.rollups:
        held, source = batch.objects[oid], batch.objects[batch.names[str(rule.source)]]
        try:
            check_rollups([rule], held.fields, owner, {
                source.name: RollupSource(source.name, tuple(source.fields), source.rules)})
        except ConstraintError as exc:
            raise RollupError(
                rule.name, owner, "declaration",
                f"Object `{owner}`: {exc.message}") from None


def _stale_parents(batch: _Batch) -> dict[tuple[str, str], list[Rule]]:
    """The parents that the net of the batch made stale, each with its roll-ups.
    The caps hold before any statement of the recompute."""
    work: dict[tuple[str, str], list[Rule]] = {}
    firsts: dict[tuple[str, str], int] = {}
    for oid, _owner, rule in batch.rollups:
        source_id = batch.names[str(rule.source)]
        for mark in sorted(batch.marks.values(), key=lambda m: m.index):
            if mark.object_id != source_id:
                continue
            batch.index = mark.index
            for rid in rollup_parents(rule, mark.before, mark.after):
                rules = work.setdefault((oid, rid), [])
                if rule not in rules:
                    rules.append(rule)
                firsts.setdefault((oid, rid), mark.index)
    if len(work) > MAX_ROLLUP_PARENTS:
        raise BudgetExceeded(
            f"A batch recomputes at most {MAX_ROLLUP_PARENTS} parent records of roll-ups.")
    if sum(len(rules) for rules in work.values()) > MAX_ROLLUP_STATEMENTS:
        raise BudgetExceeded(
            f"A batch runs at most {MAX_ROLLUP_STATEMENTS} aggregates of roll-ups.")
    batch.firsts = firsts
    return work


def _written(batch: _Batch, source_id: str) -> list[str]:
    """The ids of the records of the object *source_id* that the batch wrote
    and did not delete."""
    return sorted(rid for (oid, rid), mark in batch.marks.items()
                  if oid == source_id and mark.after is not None)


def _check_counted(batch: _Batch, rule: Rule, owner: str, source_id: str,
                   counted: Sequence[str] | None) -> None:
    """The currency of each written child that the aggregate of *rule* counted
    (C7b). The aggregate decides which children count, so a child that the
    ``where`` leaves out, or that names no parent record, passes in any
    currency. An update that makes a child count is checked at that write."""
    marks = [batch.marks[(source_id, rid)] for rid in counted or ()]
    for mark in sorted(marks, key=lambda m: m.index):
        batch.index = mark.index
        check_rollup_currency(rule, owner, mark.after or {})


async def _lock_parents(db: Any, batch: _Batch,
                        work: dict[tuple[str, str], list[Rule]]) -> None:
    """Step 4b, locks. A parent in the batch is locked already. The others come
    in ascending `(object_id, record_id)`. A parent that no record holds is
    dangling, and it leaves the work. So does a parent that the batch deleted.

    C8a fix round 1 (P1-3). A child write changes its parent, so a viewer that
    a row rule binds on the parent object must read the parent record AND
    write it. The lock reads with the row rule of the viewer, and a parent
    that the viewer cannot read, cannot write, or that is not there, refuses
    the batch with ONE text. A missing parent then tells such a viewer
    nothing. The owner and a viewer of an open parent object keep the skip."""
    for key in sorted(work):
        mark = batch.marks.get(key)
        if mark is not None:
            if mark.after is None:
                del work[key]
            continue
        oid, rid = key
        batch.index = batch.firsts[key]
        scope = batch.objects[oid].scope
        bound = scope if scope is not None and not scope.open else None
        binds = {"oid": oid, "rid": rid, "pid": batch.pid}
        sql = _LOCK_RECORD_SQL if bound is None else _scoped(
            _LOCK_RECORD_SQL, _LOCK_RECORD_SCOPED_SQL, bound, binds)
        row = (await db.execute(text(sql), binds)).first()
        data = None if row is None else dict(_json(row[2]))
        if bound is not None and (data is None or not bound.may_write(data)):
            via = str(work[key][0].via)
            raise FieldError(via, f"Field `{via}` names no `{batch.objects[oid].name}` record "
                             "that you can change.")
        if data is None:
            del work[key]
            continue
        batch.marks[key] = _Mark(oid, batch.firsts[key], data, data, derived=True)


async def _derive_parent(timed: _Timed, batch: _Batch, key: tuple[str, str],
                         data: dict[str, Any]) -> None:
    """Step 4b, the derived write of one parent. It is a store write: the
    formulas of the parent run again, the record is checked, and the hot rows
    are rebuilt. It writes no history row. The actor is the member of the batch,
    also for a single delete (:func:`delete_record` takes one)."""
    oid, rid = key
    held, mark = batch.objects[oid], batch.marks[key]
    plan = held.plan
    assert plan is not None  # the parent holds a roll-up, so it has a plan
    clean = run_formulas(plan, held.name, validate_record(
        list(plan.plain), without_targets(plan, data)))
    old = mark.after or {}
    moved = sorted(k for k in batch.readers.get(held.name, set()) if clean.get(k) != old.get(k))
    if moved:
        raise RollupError(
            plan.rollups[0][0].name, held.name, ROLLUP_OF_ROLLUP,
            f"Object `{held.name}`: field `{moved[0]}` is read by a roll-up of another object, "
            "and a roll-up of this object fills it. A roll-up cannot read a roll-up.")
    await timed.execute(text(_UPDATE_RECORD_SQL), {
        "oid": oid, "rid": rid, "pid": batch.pid, "data": record_json(clean),
        "actor": batch.who, "hot": _hot_rows(held.fields, clean)})
    mark.after = clean


async def _recompute_parents(db: Any, batch: _Batch, deadline: float) -> None:
    """Step 4b. After all the writes, recompute each parent that the net of the
    batch made stale. The aggregates, the derived writes and the key checks of
    step 6 share the one *deadline* of the batch (`query_timeout`), and a
    timeout refuses the whole batch. One aggregate runs for each parent and
    each roll-up, so 50 children of one parent cost one."""
    if not batch.rollups:
        return
    _check_rollups_fit(batch)
    work = _stale_parents(batch)
    await _lock_parents(db, batch, work)
    if not work:
        return
    async with _time_limit(db, deadline, _RULE_TIMEOUT_MESSAGE) as timed:
        for key in sorted(work):
            oid, rid = key
            batch.index = batch.firsts[key]
            held, mark = batch.objects[oid], batch.marks[key]
            assert held.plan is not None
            data = dict(mark.after or {})
            for rule in work[key]:
                source_id = batch.names[str(rule.source)]
                sql, params = build_rollup(
                    rule, batch.objects[source_id].fields, object_id=source_id,
                    package_id=batch.pid, parent_id=rid, mine=_written(batch, source_id))
                row = (await timed.execute(text(sql), params)).one()
                raw = row[0]
                if rule.currency is not None:
                    _check_counted(batch, rule, held.name, source_id, row[1])
                    batch.index = batch.firsts[key]
                field = next(f for r, f in held.plan.rollups if r.name == rule.name)
                value = rollup_result(rule, held.name, field, raw)
                if value is None:
                    data.pop(field.key, None)
                else:
                    data[field.key] = value
            await _derive_parent(timed, batch, key, data)


async def _run_steps(db: Any, batch: _Batch) -> list[Record | bool]:
    """The six steps, in their order, with the roll-up steps (C7b) between them.
    The caller holds the SAVEPOINT."""
    await _lock_org(db)
    await _find_rollups(db, batch)
    await _find_lines(db, batch)
    await _lock_objects(db, batch)
    _check_lines_fit(batch)
    await _lock_records(db, batch)
    answers: list[Record | bool] = []
    for i, step in enumerate(batch.steps):
        batch.index = i
        answers.append(await _apply_write(db, batch, step))
    # One deadline (C1b) for every read after the writes: the parents of the
    # lines (C13), the aggregates and the derived writes of the roll-ups (C7b),
    # then the key checks.
    deadline = _clock() + studio_query.TIMEOUT_MS / 1000
    await _check_line_parents(db, batch, deadline)
    await _recompute_parents(db, batch, deadline)
    keys = _check_records(batch)
    # C18: the walk of each record that got a new parent, after the checks
    # with no SQL, so a parent field that is not hot fails closed there first.
    await _check_trees(db, batch, deadline)
    await _check_keys(db, batch, keys, deadline)
    # C8a: each hidden key leaves the answer of a create and of an update.
    return [batch.objects[a.object_id].view().shown(a) if isinstance(a, Record) else a
            for a in answers]


#: C8a. The text of a refusal that would name a hidden key, or show a record
#: that the viewer cannot read. It names the rule and the object only.
_MASKED = "Rule `{rule}` of object `{object}` refuses this write. Ask the owner of the app why."


def _masked(batch: _Batch, exc: Exception) -> Exception:
    """C8a. The refusal of a rule as the viewer may read it. A rule that reads
    or fills a key hidden from the viewer, and a ``unique`` or a
    ``group_sum_min`` that reads records past the viewer's row rule, get one
    plain text that names no field. The owner, and a viewer that no rule
    binds, get the full text."""
    name = getattr(exc, "object", None)
    held = next((h for h in batch.objects.values() if h.name == name), None)
    if not isinstance(exc, RuleViolation) or held is None or held.scope is None:
        return exc
    if held.scope.open and not held.tainted:
        return exc
    rule = next((r for r in held.rules if r.name == exc.rule), None)
    reads: set[str] = set()
    if rule is not None:
        reads = set(rule_reads(rule)) | ({rule.target} if rule.target is not None else set())
    hidden = held.view().maybe_hidden()
    others = rule is not None and rule.kind in (UNIQUE, GROUP_SUM_MIN) and held.scope.row_bound
    if rule is None or others or reads & hidden:
        return ConstraintViolation(exc.rule, held.name, _MASKED.format(rule=exc.rule, object=held.name))
    return exc


async def _apply_batch(db: Any, who: str, pid: str, steps: list[Write],
                       viewer: Viewer) -> list[Record | bool]:
    """The one path of a record write. An error rolls back the SAVEPOINT as it
    leaves the block, so the batch writes nothing and the caller's earlier work
    stays."""
    if not steps:
        return []
    batch = _Batch(who, pid, steps, viewer)
    try:
        async with db.begin_nested():
            answers = await _run_steps(db, batch)
    except Exception as exc:
        shown = _masked(batch, exc)
        if shown is not exc:
            # The cause would carry the text that the mask hides.
            raise BatchError(batch.index, shown) from None
        raise BatchError(batch.index, exc) from exc
    return answers


async def apply_writes(db: Any, actor: str, *, package_id: str,
                       writes: Sequence[Write], viewer: Viewer) -> list[Record | bool]:
    """Apply *writes* to the package *package_id*, all or none (C1b).

    Answers one item for each write: the :class:`Record` of a create or an
    update, and True for a delete. *writes* holds at most
    :data:`~acb_common.studio_rules.MAX_WRITES_PER_BATCH` writes, and a longer
    list raises :class:`BudgetExceeded` before any statement. Any other error
    rolls back the batch and raises :class:`BatchError`, with the place of the
    write and the cause. The cause of a broken rule is a
    :class:`~acb_common.studio_rules.ConstraintViolation`. The cause of a
    write that names no such object or record is a :class:`RecordNotFound`.
    A timeout in a check gives ``query_timeout``. The caller's transaction
    stays usable with its earlier work.

    C8a: the scope of *viewer* binds each write. Roles with no write give
    :class:`AccessDenied`, and so does a write that leaves a record that the
    viewer cannot read. A change or a delete of a record that the row rule
    does not read gives :class:`RecordNotFound`. A key that is hidden from the
    viewer leaves each answer, and a write that names one is refused.
    """
    who = _actor(actor)
    seen = _check_viewer(viewer)
    pid = _uuid(package_id, "package_id")
    return await _apply_batch(db, who, pid, _clean_writes(writes), seen)


async def _one_write(db: Any, who: str, package_id: str, write: Write,
                     viewer: Viewer) -> Record | bool | None:
    """A batch of one, for the three record functions. Each raises the cause
    of a refusal, and answers None for a record that is not there."""
    seen = _check_viewer(viewer)
    pid = _uuid(package_id, "package_id")
    try:
        return (await _apply_batch(db, who, pid, _clean_writes([write]), seen))[0]
    except BatchError as exc:
        if isinstance(exc.cause, RecordNotFound):
            return None
        raise exc.cause from None


# --- the hierarchy (C18) ------------------------------------------------------
#
# A rule of kind `hierarchy` makes the records of one object a tree. Each record
# names its parent in one hot relation field of the same object. A record with
# no value is a root, and a root is level 1. Two statements use the hot rows of
# that field: the primary key finds the parent of a record, and the text index
# finds its children.
#
#   4c. `_check_trees` runs after the writes of a batch, in the same SAVEPOINT
#       and under the org lock. So it reads the tree as the batch leaves it. One
#       statement for each object walks up from each record that got a new
#       parent, and down to its children. The walk up refuses a cycle and a
#       parent that is no record of this object. Its level plus the levels
#       below it must be at most MAX_TREE_DEPTH. A new cycle and a new long path
#       each hold a record that got a new parent, so this check finds each one.
#   The read. `read_tree` reads a tree, or the whole forest, to a depth of at
#       most MAX_TREE_DEPTH levels in ONE recursive statement.
#
# Each walk carries its path and stops at a record that it saw, so an old cycle
# (raw SQL, or a rule declared over old data) cannot make a walk run forever.
# ⚠️ A declaration does not check old data (C1b). A rule binds each later write.

#: The most nodes of one read of a tree. The tree block (D18) opens a tree of
#: 2,000 nodes in one read.
MAX_TREE_NODES = 2_000

#: The parent of a record, by the primary key of the hot row. A parent id that
#: is no UUID, or that names no record of this object, ends the walk.
_TREE_PARENT = (
    "JOIN studio_record_hot h ON h.organization_id = " + _BOUND_ORG + " "
    "AND h.object_id = CAST(:oid AS uuid) AND h.record_id = u.id "
    "AND h.field_key = CAST(:fk AS text) "
    "JOIN studio_records p ON p.organization_id = " + _BOUND_ORG + " "
    "AND p.object_id = CAST(:oid AS uuid) "
    "AND p.id = CASE WHEN h.v_text ~ " + _UUID_FORM + " THEN CAST(h.v_text AS uuid) END "
)

#: The children of a record, by the text index of the hot rows.
_TREE_CHILDREN = (
    "JOIN studio_record_hot h ON h.organization_id = " + _BOUND_ORG + " "
    "AND h.object_id = CAST(:oid AS uuid) AND h.field_key = CAST(:fk AS text) "
    "AND h.v_text = CAST(t.id AS text) "
)

#: The walk of each record of `:rids` (C18): its level (the walk up, which stops
#: one past the cap), a cycle, the levels below it (the walk down, to the cap),
#: and whether its parent is a record of this object.
_TREE_CHECK_SQL = (
    "WITH RECURSIVE s AS (SELECT DISTINCT x.id FROM unnest(CAST(:rids AS uuid[])) AS x(id) "
    "WHERE " + _IN_PACKAGE + "), "
    "up(start, id, depth, path, cyc) AS ("
    "SELECT s.id, s.id, 1, ARRAY[s.id], false FROM s "
    "UNION ALL "
    "SELECT u.start, p.id, u.depth + 1, u.path || p.id, p.id = u.start FROM up u "
    + _TREE_PARENT +
    "WHERE NOT u.cyc AND u.depth <= CAST(:cap AS int) "
    "AND (p.id = u.start OR NOT p.id = ANY(u.path))), "
    "down(start, id, depth, path) AS ("
    "SELECT s.id, s.id, 0, ARRAY[s.id] FROM s "
    "UNION ALL "
    "SELECT t.start, h.record_id, t.depth + 1, t.path || h.record_id FROM down t "
    + _TREE_CHILDREN +
    "WHERE t.depth < CAST(:cap AS int) AND NOT h.record_id = ANY(t.path)) "
    "SELECT s.id::text, "
    "(SELECT max(u.depth) FROM up u WHERE u.start = s.id), "
    "(SELECT bool_or(u.cyc) FROM up u WHERE u.start = s.id), "
    "(SELECT max(d.depth) FROM down d WHERE d.start = s.id), "
    "EXISTS (SELECT 1 FROM up u WHERE u.start = s.id AND u.depth = 2) "
    "FROM s"
)

#: The object of a tree read, with its rules, of the package that the caller
#: names. No lock.
_TREE_OBJECT_SQL = (
    "SELECT name, constraints FROM studio_objects "
    f"WHERE organization_id = {_BOUND_ORG} AND id = CAST(:oid AS uuid) "
    "AND package_id = CAST(:pid AS uuid)"
)

#: The roots of a read of the whole forest: each record of the object with no
#: parent record of this object. A record whose parent is gone is a root too.
_TREE_ALL_ROOTS = (
    "SELECT r.id FROM studio_records r "
    f"WHERE r.{_OF_ORG} AND r.{_OF_OBJECT} AND NOT EXISTS ("
    "SELECT 1 FROM studio_record_hot h "
    "JOIN studio_records p ON p.organization_id = " + _BOUND_ORG + " "
    "AND p.object_id = CAST(:oid AS uuid) "
    "AND p.id = CASE WHEN h.v_text ~ " + _UUID_FORM + " THEN CAST(h.v_text AS uuid) END "
    f"WHERE h.{_OF_ORG} AND h.{_OF_OBJECT} AND h.record_id = r.id "
    "AND h.field_key = CAST(:fk AS text))"
)

#: The root of a read of one tree: the record `:root` of the object.
_TREE_ONE_ROOT = (
    "SELECT r.id FROM studio_records r "
    f"WHERE r.{_OF_ORG} AND r.{_OF_OBJECT} AND r.id = CAST(:root AS uuid)"
)


def _tree_read_sql(roots: str) -> str:
    """The one statement of a tree read, from the SQL of its *roots* (one of
    the two constants above). Each node comes once, with its level and the id
    of its parent in the read. The order is by level, then by parent, then by
    the time of the create. The statement reads one node past the limit, so
    the store knows that the read is cut."""
    return (
        "WITH RECURSIVE roots AS (" + roots + " AND " + _IN_PACKAGE + "), "
        "tree(id, parent, depth, path) AS ("
        "SELECT roots.id, CAST(NULL AS uuid), 1, ARRAY[roots.id] FROM roots "
        "UNION ALL "
        "SELECT h.record_id, t.id, t.depth + 1, t.path || h.record_id FROM tree t "
        + _TREE_CHILDREN +
        "WHERE t.depth < CAST(:depth AS int) AND NOT h.record_id = ANY(t.path)) "
        "SELECT t.depth, t.parent::text, " + _QUERY_COLUMNS + " FROM tree t "
        "JOIN studio_records r ON r." + _OF_ORG + " AND r." + _OF_OBJECT + " AND r.id = t.id "
        "ORDER BY t.depth, t.parent NULLS FIRST, r.created_at, r.id "
        "LIMIT CAST(:limit AS int) + 1"
    )


_TREE_ALL_SQL = _tree_read_sql(_TREE_ALL_ROOTS)
_TREE_ROOT_SQL = _tree_read_sql(_TREE_ONE_ROOT)


@dataclass(frozen=True)
class TreeNode:
    """One record of a tree read (C18)."""

    record: Record
    #: The level in the read: 1 for each root of the read.
    level: int
    #: The id of the parent in the read, or None for a root of the read.
    parent_id: str | None


@dataclass(frozen=True)
class TreePage:
    """The nodes of one tree read (C18), level by level."""

    nodes: tuple[TreeNode, ...]
    #: True when the tree holds more nodes than the limit, so the read is cut.
    truncated: bool


def _tree_rule(rules: Sequence[Rule]) -> Rule | None:
    return next((r for r in rules if r.kind == HIERARCHY), None)


def _no_tree_parent(rule: Rule, name: str) -> FieldError:
    key = str(rule.field)
    return FieldError(key, f"Field `{key}` names no `{name}` record of this app. "
                      f"Pick a record of `{name}` in this app.")


async def _check_trees(db: Any, batch: _Batch, deadline: float) -> None:
    """Step 4c (C18). For each object of the batch with a hierarchy, walk each
    record that got a new parent, in one statement, under the one deadline of
    the batch. A refusal names the first write of the record."""
    work: dict[str, dict[str, int]] = {}
    for (oid, rid), mark in batch.marks.items():
        rule = _tree_rule(batch.objects[oid].rules)
        if rule is not None and tree_moves(rule, mark.before, mark.after) is not None:
            work.setdefault(oid, {})[rid] = mark.index
    if not work:
        return
    async with _time_limit(db, deadline, _RULE_TIMEOUT_MESSAGE) as timed:
        for oid, starts in sorted(work.items()):
            held = batch.objects[oid]
            rule = _tree_rule(held.rules)
            assert rule is not None
            rows = (await timed.execute(text(_TREE_CHECK_SQL), {
                "oid": oid, "pid": batch.pid, "fk": str(rule.field), "rids": sorted(starts),
                "cap": MAX_TREE_DEPTH})).all()
            for row in sorted(rows, key=lambda r: starts[str(r[0])]):
                batch.index = starts[str(row[0])]
                if not row[4]:
                    raise _no_tree_parent(rule, held.name)
                check_tree(rule, held.name, cycle=bool(row[2]),
                           depth=int(row[1]) + int(row[3] or 0))


def _tree_bound(value: Any, what: str, most: int) -> int:
    if type(value) is not int or not 1 <= value <= most:
        raise ObjectStoreError(f"{what} is a whole number from 1 to {most}.")
    return value


async def read_tree(db: Any, *, package_id: str, object_id: str, root_id: str | None = None,
                    depth: Any = MAX_TREE_DEPTH, limit: Any = MAX_TREE_NODES) -> TreePage | None:
    """The tree of the object *object_id* of the package *package_id* (C18), in
    ONE statement. Answers None when the bound org holds no such object in that
    package.

    *root_id* names the record at the top of the read. None reads the whole
    forest: each record with no parent record of this object is a root. *depth*
    is the most levels to read, from 1 to
    :data:`~acb_common.studio_rules.MAX_TREE_DEPTH`, and *limit* is the most
    nodes, from 1 to :data:`MAX_TREE_NODES`. A root id that names no record of
    the object gives no nodes. An object with no hierarchy, or with a parent
    field that is not hot, raises :class:`ObjectStoreError`. The whole call has
    the deadline of :data:`~acb_common.studio_query.TIMEOUT_MS`.
    """
    deadline = _clock() + studio_query.TIMEOUT_MS / 1000
    oid = _uuid(object_id, "object_id")
    pid = _uuid(package_id, "package_id")
    root = None if root_id is None else _uuid(root_id, "root_id")
    levels = _tree_bound(depth, "depth", MAX_TREE_DEPTH)
    size = _tree_bound(limit, "limit", MAX_TREE_NODES)
    async with _time_limit(db, deadline) as timed:
        found = (await timed.execute(text(_TREE_OBJECT_SQL), {"oid": oid, "pid": pid})).first()
        if found is None:
            return None
        rule = _tree_rule(stored_rules(_json(found[1])))
        if rule is None:
            raise ObjectStoreError(f"Object `{found[0]}` holds no hierarchy rule.")
        hot = {f.key for f in await _fields(timed, oid) if f.hot}
        if str(rule.field) not in hot:
            raise ObjectStoreError(f"Field `{rule.field}` is not hot, so the tree cannot be read.")
        binds: dict[str, Any] = {"oid": oid, "pid": pid, "fk": str(rule.field),
                                 "depth": levels, "limit": size}
        if root is not None:
            binds["root"] = root
        rows = (await timed.execute(
            text(_TREE_ALL_SQL if root is None else _TREE_ROOT_SQL), binds)).all()
    nodes = tuple(TreeNode(record=_record(row[2:]), level=int(row[0]),
                           parent_id=None if row[1] is None else str(row[1]))
                  for row in rows[:size])
    return TreePage(nodes=nodes, truncated=len(rows) > size)
