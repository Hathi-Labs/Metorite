"""The ledger of a batch run (WS-STU E8a, task 3095).

Spec: ``project-docs/specs/ai_studio.md`` §14 E8a. Tables: ``studio_batches``
and ``studio_batch_items`` (migration 438). This module is the ONE writer of
both. It is the store level only: no route, no workflow node, no flag. E8b
puts a node on top of it.

**What it does.** :func:`open_batch` freezes a list of items. One item is one
record, with its lines (C13), as a list of :class:`~gateway.studio.objects_store.Write`.
:func:`run_chunk` takes the next pending items in place order, at most
:data:`CHUNK_WRITES` writes of them, and runs them through ONE
:func:`~gateway.studio.objects_store.apply_writes` call. It sets each item
``done`` with its record id in the SAME transaction as the writes.

**The invariant that carries the slice.** The status of an item and the
write of the item commit together or not at all. A status in a second
transaction would repeat an item after a crash (the write committed, the
status did not), or lose one. So this module never commits. The caller owns
the transaction, and ONE call of :func:`run_chunk` is ONE transaction. A
fault in a chunk rolls back all of that chunk, and the earlier chunks stay.
A second drive of the batch then continues from the first pending item.

**Where the frozen list lives (the decision of this slice).** The store keeps
a DIGEST of each item, and NOT its writes. A copy of pay data outside the
object store would skip the rules of C9 for the restricted fields, and nothing
here can prove that a write holds no such value. So the caller passes the
writes again to each :func:`run_chunk`, and the store checks each item against
its stored digest BEFORE it writes. A digest that differs raises
:class:`DigestMismatch`, and the call writes nothing. The caller holds the
list (E8b: the run state, or the source that built it). The digest of the list
(:func:`list_digest`) is what an approval binds. The ``item_key`` is named by
the caller and must be an id or a row number, never pay data.

**One session, one connection.** Every function takes the session of the
caller and opens no connection of its own. A driver of many chunks opens one
session for each chunk, one after the other, and never holds two at once.

**Known limits (E8b must handle them).**

* The delete of a package takes the org lock and then deletes the ``apps`` row,
  which cascades onto a batch row. A driver holds that row ``FOR UPDATE`` and
  then waits for the org lock. In a narrow window that is a deadlock (40P01).
  The member can retry the delete step. A driver retries a chunk.
* ``StoreBusy`` from the wait for the batch row aborts the transaction
  (``bounded_wait`` opens no SAVEPOINT), so the caller rolls back. ``StoreBusy``
  from ``apply_writes`` leaves the transaction usable. Both mark no item.
* The digest is type sensitive: ``1`` and ``1.0`` differ, and ``1e16`` comes
  back from JSONB as an int. So E8b must keep the writes lossless, and pass
  the same Python values that it froze.
* ``_result`` never moves a batch that is not ``open`` back to ``open``.

**Locks.** :func:`run_chunk` locks the batch row ``FOR UPDATE`` first, with the
bound of :func:`~gateway.studio.objects_store.bounded_wait`. Then the write
takes the org lock. The order is always batch row, then org lock, so two
drivers of one batch cannot deadlock. The second driver waits for the first
to commit, and then reads the items that remain. A wait past the bound raises
:class:`~gateway.studio.objects_store.StoreBusy`. That is a retry signal and
not a failure: it marks no item, and the transaction ends with the caller.
``StoreBusy`` from the write itself is the same signal.

**A refusal fails ONE item.** When the batch of a chunk raises ``BatchError``,
the SAVEPOINT of ``apply_writes`` has rolled it back. The store marks the item
that holds the place ``failed``, with a short ``error_code`` (never a
message, because a message can hold data), and runs the other items of the
chunk again. The next call continues past the failed item. A fault that is
not a refusal of a write (a database error) is raised, and marks nothing.

Fence: ``tests/unit/test_studio_batch.py`` and ``test_studio_batch_r8.py``.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from acb_common import studio_query
from acb_common.studio_access import Viewer
from acb_common.studio_query import QueryError
from acb_common.studio_rules import MAX_WRITES_PER_BATCH, RuleViolation
from sqlalchemy import text

from gateway.studio import objects_store
from gateway.studio.objects_store import BatchError, StoreBusy, Write

#: The items of one batch. A literal, so a change shows in a diff.
MAX_BATCH_ITEMS = 1000
#: The writes of one chunk: the cap of one ``apply_writes`` call. An item with
#: ``n`` lines counts ``1 + n`` of it.
CHUNK_WRITES = MAX_WRITES_PER_BATCH
#: The longest key of an item, as the CHECK holds it.
MAX_KEY_CHARS = 120
#: The wait for the row of a batch, in milliseconds.
BATCH_LOCK_WAIT_MS = 1000
#: The ops that a batch may hold. A delete is never in a batch.
BATCH_OPS = ("create", "update")

STATUS_OPEN = "open"
STATUS_DONE = "done"
STATUS_FAILED = "failed"

_BOUND_ORG = "CAST(NULLIF(current_setting('app.tenant_id', true), '') AS uuid)"


class BatchStoreError(ValueError):
    """A call that the store refuses before it writes."""


class BatchNotFound(BatchStoreError):
    """No batch of this id in the bound org."""


class DigestMismatch(BatchStoreError):
    """The writes that the caller passed are not the writes that were frozen.
    The call wrote nothing."""


@dataclass(frozen=True)
class BatchItem:
    """One item of a list: a key, and the writes of one record. The first
    write is the record, and each later write is a line of it (C13)."""

    key: str
    writes: tuple[Write, ...]


@dataclass(frozen=True)
class Batch:
    id: str
    package_id: str
    action: str
    list_digest: str
    total: int
    status: str
    run_id: str | None
    node_id: str | None
    created_by: str
    pending: int = 0
    done: int = 0
    failed: int = 0


@dataclass(frozen=True)
class ItemRow:
    key: str
    position: int
    op: str
    write_count: int
    status: str
    record_id: str | None
    error_code: str | None
    item_digest: str


@dataclass(frozen=True)
class ChunkResult:
    """The outcome of one :func:`run_chunk`. ``written`` maps the key of each
    item that is now ``done`` to its record id. ``failed`` maps the key of each
    item that this call marked ``failed`` to its code."""

    written: dict[str, str]
    failed: dict[str, str]
    pending: int
    status: str


# --- the digests (pure) --------------------------------------------------------


def _canon(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False, default=str)


def _write_form(write: Write) -> list[Any]:
    return [write.op, write.object_id, write.record_id,
            None if write.data is None else dict(write.data), write.parent]


def item_digest(writes: Sequence[Write]) -> str:
    """sha256 of the canonical form of the writes of one item. It binds the
    op, the object, the record id, the data and the parent place of each."""
    return hashlib.sha256(_canon([_write_form(w) for w in writes]).encode("utf-8")).hexdigest()


def list_digest(package_id: str, action: str, items: Sequence[BatchItem]) -> str:
    """sha256 of the package, the action and the ordered keys and digests of
    the items. An approval of E8b binds this value."""
    body = _canon([str(package_id), action, [[i.key, item_digest(i.writes)] for i in items]])
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _clean_items(items: Any) -> list[BatchItem]:
    if isinstance(items, str | bytes | Mapping) or not isinstance(items, Sequence):
        raise BatchStoreError("items must be a list of items")
    if not items:
        raise BatchStoreError("A batch holds at least one item.")
    if len(items) > MAX_BATCH_ITEMS:
        raise BatchStoreError(f"A batch holds at most {MAX_BATCH_ITEMS} items.")
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, BatchItem) or not isinstance(item.key, str):
            raise BatchStoreError("Each item is a BatchItem with a text key.")
        if not item.key or len(item.key) > MAX_KEY_CHARS or item.key != item.key.strip():
            raise BatchStoreError(f"An item key is 1 to {MAX_KEY_CHARS} characters, with no edge space.")
        if item.key in seen:
            raise BatchStoreError("Two items hold the same key.")
        seen.add(item.key)
        writes = item.writes
        if not writes or len(writes) > CHUNK_WRITES:
            raise BatchStoreError(f"An item holds 1 to {CHUNK_WRITES} writes.")
        if any(not isinstance(w, Write) for w in writes):
            raise BatchStoreError("Each write of an item is a Write.")
        if any(w.op not in BATCH_OPS for w in writes):
            raise BatchStoreError("A batch holds creates and updates only. It holds no delete.")
        if writes[0].parent is not None:
            raise BatchStoreError("The first write of an item is the record. It has no parent.")
        for j, w in enumerate(writes[1:], start=1):
            if type(w.parent) is not int or not 0 <= w.parent < j:
                raise BatchStoreError("The parent of a line is the place of an earlier write of its item.")
    return list(items)


def _shift(writes: Sequence[Write], base: int) -> list[Write]:
    """The writes of an item as part of a chunk. The parent place of a line is
    the place of its record in the CHUNK, so it moves by *base*."""
    if base == 0:
        return list(writes)
    return [w if w.parent is None else Write(w.op, w.object_id, w.record_id, w.data, w.parent + base)
            for w in writes]


# --- the statements -----------------------------------------------------------

_INSERT_BATCH_SQL = (
    "INSERT INTO studio_batches (package_id, run_id, node_id, action, list_digest, total, "
    "created_by) VALUES (CAST(:pid AS uuid), CAST(:run AS uuid), :node, :action, :digest, "
    ":total, :who) RETURNING id"
)
_INSERT_ITEM_SQL = (
    "INSERT INTO studio_batch_items (batch_id, item_key, position, op, write_count, item_digest) "
    "VALUES (CAST(:bid AS uuid), :key, :pos, :op, :wc, :digest)"
)
_LOCK_BATCH_SQL = (
    "SELECT package_id, status FROM studio_batches "
    f"WHERE organization_id = {_BOUND_ORG} AND id = CAST(:bid AS uuid) FOR UPDATE"
)
_PENDING_SQL = (
    "SELECT item_key, write_count, item_digest FROM studio_batch_items "
    f"WHERE organization_id = {_BOUND_ORG} AND batch_id = CAST(:bid AS uuid) "
    "AND status = 'pending' ORDER BY position LIMIT :lim"
)
_DONE_SQL = (
    "UPDATE studio_batch_items SET status = 'done', record_id = CAST(:rid AS uuid), "
    "error_code = NULL, updated_at = now() "
    f"WHERE organization_id = {_BOUND_ORG} AND batch_id = CAST(:bid AS uuid) "
    "AND item_key = :key AND status = 'pending'"
)
_FAILED_SQL = (
    "UPDATE studio_batch_items SET status = 'failed', error_code = :code, updated_at = now() "
    f"WHERE organization_id = {_BOUND_ORG} AND batch_id = CAST(:bid AS uuid) "
    "AND item_key = :key AND status = 'pending'"
)
_COUNTS_SQL = (
    "SELECT count(*) FILTER (WHERE status = 'pending'), count(*) FILTER (WHERE status = 'done'), "
    "count(*) FILTER (WHERE status = 'failed') FROM studio_batch_items "
    f"WHERE organization_id = {_BOUND_ORG} AND batch_id = CAST(:bid AS uuid)"
)
_SET_STATUS_SQL = (
    "UPDATE studio_batches SET status = :status, updated_at = now() "
    f"WHERE organization_id = {_BOUND_ORG} AND id = CAST(:bid AS uuid)"
)
_GET_BATCH_SQL = (
    "SELECT id, package_id, action, list_digest, total, status, run_id, node_id, created_by "
    f"FROM studio_batches WHERE organization_id = {_BOUND_ORG} AND id = CAST(:bid AS uuid)"
)
_LIST_ITEMS_SQL = (
    "SELECT item_key, position, op, write_count, status, record_id, error_code, item_digest "
    f"FROM studio_batch_items WHERE organization_id = {_BOUND_ORG} "
    "AND batch_id = CAST(:bid AS uuid) AND (CAST(:status AS text) IS NULL OR status = :status) "
    "ORDER BY position"
)


def _uuid(value: object, what: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        raise BatchStoreError(f"{what} must be a UUID") from None


# --- open ---------------------------------------------------------------------


async def open_batch(db: Any, actor: str, *, package_id: str, action: str,
                     items: Sequence[BatchItem], run_id: str | None = None,
                     node_id: str | None = None, expect_digest: str | None = None) -> Batch:
    """Freeze *items* as one batch of the package, in the bound org.

    It refuses a list of more than :data:`MAX_BATCH_ITEMS` items, a duplicate
    key, an op other than create or update, and an item of more than
    :data:`CHUNK_WRITES` writes. It stores the digest of each item and the
    digest of the list, and never the writes. *expect_digest*, when given, is
    the list digest that an approval named: a list with another digest is
    refused, so the list that the member approved is the list that runs.
    *run_id* and *node_id* come together or not at all, and one pair opens one
    batch. It does not commit.
    """
    who = objects_store._actor(actor)
    pid = _uuid(package_id, "package_id")
    if not isinstance(action, str) or not action or len(action) > 120:
        raise BatchStoreError("action is 1 to 120 characters")
    if (run_id is None) != (node_id is None):
        raise BatchStoreError("run_id and node_id come together")
    run = None if run_id is None else _uuid(run_id, "run_id")
    clean = _clean_items(items)
    digest = list_digest(pid, action, clean)
    if expect_digest is not None and expect_digest != digest:
        raise DigestMismatch("The list is not the list that was approved.")
    bid = str((await db.execute(text(_INSERT_BATCH_SQL), {
        "pid": pid, "run": run, "node": node_id, "action": action, "digest": digest,
        "total": len(clean), "who": who})).scalar_one())
    for pos, item in enumerate(clean):
        await db.execute(text(_INSERT_ITEM_SQL), {
            "bid": bid, "key": item.key, "pos": pos, "op": item.writes[0].op,
            "wc": len(item.writes), "digest": item_digest(item.writes)})
    return Batch(id=bid, package_id=pid, action=action, list_digest=digest, total=len(clean),
                 status=STATUS_OPEN, run_id=run, node_id=node_id, created_by=who,
                 pending=len(clean))


# --- read ---------------------------------------------------------------------


async def get_batch(db: Any, batch_id: str) -> Batch | None:
    """The batch with its counts, or None. Org B reads no batch of org A: the
    statement names the org, and RLS holds it too."""
    bid = _uuid(batch_id, "batch_id")
    row = (await db.execute(text(_GET_BATCH_SQL), {"bid": bid})).first()
    if row is None:
        return None
    counts = (await db.execute(text(_COUNTS_SQL), {"bid": bid})).one()
    return Batch(id=str(row[0]), package_id=str(row[1]), action=str(row[2]),
                 list_digest=str(row[3]), total=int(row[4]), status=str(row[5]),
                 run_id=None if row[6] is None else str(row[6]),
                 node_id=None if row[7] is None else str(row[7]), created_by=str(row[8]),
                 pending=int(counts[0]), done=int(counts[1]), failed=int(counts[2]))


async def list_items(db: Any, batch_id: str, *, status: str | None = None) -> list[ItemRow]:
    """The items of a batch in place order, with no writes (none are stored)."""
    bid = _uuid(batch_id, "batch_id")
    if status is not None and status not in ("pending", "done", "failed"):
        raise BatchStoreError("status is pending, done or failed")
    rows = (await db.execute(text(_LIST_ITEMS_SQL), {"bid": bid, "status": status})).all()
    return [ItemRow(key=str(r[0]), position=int(r[1]), op=str(r[2]), write_count=int(r[3]),
                    status=str(r[4]), record_id=None if r[5] is None else str(r[5]),
                    error_code=None if r[6] is None else str(r[6]), item_digest=str(r[7]))
            for r in rows]


# --- run one chunk ------------------------------------------------------------


def _take(pending: Sequence[Any]) -> list[Any]:
    """The pending rows of one chunk, in order: whole items, while their
    writes fit in :data:`CHUNK_WRITES`. The first item always fits, because
    open refuses an item of more than the cap."""
    out: list[Any] = []
    used = 0
    for row in pending:
        if used + int(row[1]) > CHUNK_WRITES:
            break
        out.append(row)
        used += int(row[1])
    return out


def _code(cause: BaseException) -> str:
    """A short code for the refusal of an item. Never the message."""
    if isinstance(cause, RuleViolation):
        return "rule_refused"
    if isinstance(cause, QueryError):
        return str(cause.code)[:80]
    if isinstance(cause, objects_store.RecordNotFound):
        return "record_not_found"
    return type(cause).__name__[:80]


def _is_busy(exc: BaseException) -> bool:
    cause = exc.cause if isinstance(exc, BatchError) else exc
    return isinstance(cause, StoreBusy)


def _is_item_refusal(cause: BaseException) -> bool:
    """A refusal of the write itself: a rule, a field, a missing record, a cap.
    A database error or any other fault is not one, and it is raised."""
    return isinstance(cause, ValueError) and not isinstance(cause, StoreBusy)


async def run_chunk(db: Any, actor: str, batch_id: str,
                    writes_by_key: Mapping[str, Sequence[Write]], *,
                    viewer: Viewer) -> ChunkResult:
    """Run the next chunk of the batch, as ONE transaction (the caller's).

    It locks the batch row, reads the pending items in place order, and takes
    whole items while their writes fit in :data:`CHUNK_WRITES` (an item with
    ``n`` lines counts ``1 + n``). *writes_by_key* must hold the writes of each
    item that the chunk takes. Each is checked against the digest that was
    frozen, and a difference raises :class:`DigestMismatch` before any write.
    The chunk then runs as one ``apply_writes`` call, and each item is marked
    ``done`` with its record id in the same transaction.

    A refused chunk is split in halves until a single item is refused. That
    item is marked ``failed``, and no other. :class:`~gateway.studio.objects_store.StoreBusy` is a retry
    signal: it marks nothing. The caller commits after the call, or rolls back
    when it raises, and each call is one transaction, so the org lock is free
    between chunks.

    *viewer* is required (C8a). The caller resolves it in the session of the
    chunk, as the door does with ``_row_viewer(member)``, and the store never
    builds one. So the row rules bind a batch write exactly as they bind a
    single write. The store passes it on to every ``apply_writes`` call,
    including the halves of a split.
    """
    bid = _uuid(batch_id, "batch_id")
    who = objects_store._actor(actor)
    async with objects_store.bounded_wait(db, BATCH_LOCK_WAIT_MS):
        held = (await db.execute(text(_LOCK_BATCH_SQL), {"bid": bid})).first()
    if held is None:
        raise BatchNotFound("No such batch.")
    pid, status = str(held[0]), str(held[1])
    if status != STATUS_OPEN:
        return await _result(db, bid, {}, {}, status)
    pending = (await db.execute(text(_PENDING_SQL), {"bid": bid, "lim": CHUNK_WRITES})).all()
    taken = _take(pending)
    for row in taken:
        writes = writes_by_key.get(str(row[0]))
        if writes is None or not all(isinstance(w, Write) for w in writes):
            raise DigestMismatch("The writes of an item are missing.")
        if item_digest(writes) != str(row[2]):
            raise DigestMismatch("The writes of an item are not the frozen writes.")
    written: dict[str, str] = {}
    failed: dict[str, str] = {}
    await _run_group(db, who, pid, bid, taken, writes_by_key, written, failed, viewer)
    return await _result(db, bid, written, failed, status)


async def _run_group(db: Any, who: str, pid: str, bid: str, rows: Sequence[Any],
                     writes_by_key: Mapping[str, Sequence[Write]], written: dict[str, str],
                     failed: dict[str, str], viewer: Viewer) -> None:
    """Write the items *rows* as one ``apply_writes`` call, and when the call
    refuses, split the group in two halves and run each half. A group of ONE
    item that the store refuses fails that item, and no other. So a refusal
    that the whole chunk causes (the roll-up caps, the shared check deadline)
    never fails an item that writes fine alone. The caps are per call, so a
    smaller group passes. A unique clash between two items of one chunk gives
    the same outcome wherever the chunk edge falls: the earlier item writes,
    and the later one fails alone against it.

    A group of one item that times out raises ``StoreBusy`` (a retry signal),
    because a deadline says nothing sure about the item. An item that always
    times out blocks its batch until the driver stops, and E8b bounds the
    retries.

    A fault that is not a refusal of a write (a database error, any cause
    that is not a ``ValueError``) and a busy org are raised, and mark nothing.
    A refused call rolls back its own SAVEPOINT, so the earlier halves stay."""
    if not rows:
        return
    flat: list[Write] = []
    starts: list[int] = []
    for row in rows:
        starts.append(len(flat))
        flat.extend(_shift(writes_by_key[str(row[0])], len(flat)))
    try:
        answers = await objects_store.apply_writes(
            db, who, package_id=pid, writes=flat, viewer=viewer)
    except BatchError as exc:
        if _is_busy(exc):
            raise StoreBusy(str(exc.cause)) from None
        if not _is_item_refusal(exc.cause):
            raise
        if len(rows) > 1:
            half = len(rows) // 2
            await _run_group(db, who, pid, bid, rows[:half], writes_by_key, written, failed,
                             viewer)
            await _run_group(db, who, pid, bid, rows[half:], writes_by_key, written, failed,
                             viewer)
            return
        if isinstance(exc.cause, QueryError) and exc.cause.code == studio_query.TIMEOUT:
            # A deadline is load, not a verdict on the item: a retry signal.
            raise StoreBusy(str(exc.cause)) from None
        key = str(rows[0][0])
        failed[key] = _code(exc.cause)
        await db.execute(text(_FAILED_SQL), {"bid": bid, "key": key, "code": failed[key]})
        return
    for row, start in zip(rows, starts, strict=True):
        record = answers[start]
        if not isinstance(record, objects_store.Record):
            raise BatchStoreError("A write of an item gave no record.")
        written[str(row[0])] = record.id
        await db.execute(text(_DONE_SQL), {"bid": bid, "key": str(row[0]), "rid": record.id})


async def _result(db: Any, bid: str, written: dict[str, str], failed: dict[str, str],
                  held: str) -> ChunkResult:
    """The counts after the chunk, and the status of the batch: ``done`` when
    nothing is pending and no item failed, ``failed`` when nothing is pending
    and one did, else ``open``. A batch that was not ``open`` keeps its
    status: this never moves it back."""
    counts = (await db.execute(text(_COUNTS_SQL), {"bid": bid})).one()
    pending, bad = int(counts[0]), int(counts[2])
    if held != STATUS_OPEN:
        return ChunkResult(written=written, failed=failed, pending=pending, status=held)
    status = STATUS_OPEN if pending else (STATUS_FAILED if bad else STATUS_DONE)
    await db.execute(text(_SET_STATUS_SQL), {"bid": bid, "status": status})
    return ChunkResult(written=written, failed=failed, pending=pending, status=status)
