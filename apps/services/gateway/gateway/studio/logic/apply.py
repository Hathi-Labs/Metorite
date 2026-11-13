"""Run a logic module and apply what it proposes (STU-B4a, ``ai_studio.md`` §3.7).

Two functions. :func:`run_module` reads the input and runs the module with a
reader. :func:`apply_proposed_writes` checks the proposal and applies it with
ONE ``objects_store.apply_writes`` call, so the batch is all or none.

**What is refused, and when.** Each refusal below is a :class:`LogicRefused`
and comes BEFORE any write statement. Only :func:`apply_proposed_writes` reads
the object rows, and only after the shape checks.

* A proposal that is not a list, or an item that is not a
  :class:`~gateway.studio.logic.ProposedWrite` of the right shape:
  ``logic_bad_output``.
* A proposal past ``MAX_WRITES_PER_BATCH`` (the cap of the store): ``too_many_writes``.
* An ``(object, op)`` outside the ``writes`` of the module:
  ``logic_undeclared_write``. A read outside ``reads`` is ``logic_undeclared_read``.
* An object that THIS package does not hold: ``object_missing``. The lookup
  takes the package id of the door, so the object of another package or org
  is never found.
* A module that raises: ``logic_failed``. Nothing is applied. The log line
  names the exception type and never its text, because a text can hold data.

**The audit.** One ``app_audit`` row for each write, in the SAME transaction.
The outcome is ``ran``, or ``simulated`` for a preview. The row holds the
object name, the op and the record id, and no value. ``via`` is None: the
change history has no ``logic`` kind yet, and a migration is not in this slice.

**The preview.** The batch runs in a SAVEPOINT that ALWAYS rolls back. No row
stays, and the caller emits no event.

The module opens no session and holds no ``db``: ``db`` stays here.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from acb_common import get_logger
from acb_common.studio_access import Viewer
from acb_common.studio_rules import MAX_WRITES_PER_BATCH
from pydantic import BaseModel, ValidationError
from sqlalchemy.exc import SQLAlchemyError

from gateway.studio import objects_store
from gateway.studio.logic import LogicModule, ProposedWrite, Reader

__all__ = [
    "AppliedWrite",
    "LogicRefused",
    "apply_proposed_writes",
    "run_module",
]

_log = get_logger("gateway.studio.logic")

OBJECT_MISSING = "object_missing"
BAD_INPUT = "bad_input"
LOGIC_FAILED = "logic_failed"
BAD_OUTPUT = "logic_bad_output"
UNDECLARED_WRITE = "logic_undeclared_write"
TOO_MANY_WRITES = "too_many_writes"
UNDECLARED_READ = "logic_undeclared_read"
#: A batch that the store refused. The door maps ``cause`` as it maps a direct write.
RECORD_REFUSED = "record_refused"

_TEXTS = {
    BAD_INPUT: "The input does not fit this logic module, so send the input that it declares.",
    LOGIC_FAILED: "This logic module failed, so nothing was written.",
    BAD_OUTPUT: "This logic module gave a bad proposal, so nothing was written.",
    UNDECLARED_WRITE: "This logic module proposed a write that it does not declare, "
                      "so nothing was written.",
    UNDECLARED_READ: "This logic module read an object that it does not declare, "
                     "so nothing was written.",
    TOO_MANY_WRITES: f"A logic module may propose at most {MAX_WRITES_PER_BATCH} writes, "
                     "so nothing was written.",
    OBJECT_MISSING: "This package holds no object for this action yet, so publish the package.",
}

_OPS = ("create", "update", "delete")


class LogicRefused(Exception):
    """A refusal that the door answers. ``code`` is one of the codes above,
    and ``message`` is one fixed sentence. ``cause`` holds the error of the
    store for a batch that the store refused, so the door maps it as it maps
    a direct write. The text holds no value."""

    def __init__(self, code: str, cause: Exception | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.message = _TEXTS.get(code, _TEXTS[LOGIC_FAILED])
        self.cause = cause


@dataclasses.dataclass(frozen=True)
class AppliedWrite:
    """One write that ran: for the answer, the audit row and the record event.
    It holds ids, names and the changed keys, and no value."""

    object_id: str
    object_name: str
    op: str
    record_id: str | None
    changed: tuple[str, ...]


def _reader(db: Any, *, package_id: str, viewer: Viewer, module: LogicModule,
            store: Any) -> Reader:
    """The reader of one run: a closure over ``query_records`` in the session
    *db*, with *viewer* of the caller. It reads only an object in the
    ``reads`` of *module*, and only of the package *package_id*."""

    async def read(object_name: str, *, filter: Any = None, sort: Any = None,
                   limit: Any = None, offset: Any = None) -> list[dict[str, Any]]:
        if object_name not in module.reads:
            raise LogicRefused(UNDECLARED_READ)
        obj = await store.get_object_by_name(db, object_name, package_id=package_id)
        if obj is None:
            raise LogicRefused(OBJECT_MISSING)
        page = await store.query_records(
            db, package_id=package_id, object_id=obj.id, viewer=viewer,
            filter=filter, sort=sort, limit=limit, offset=offset)
        if page is None:
            raise LogicRefused(OBJECT_MISSING)
        return [{"id": r.id, "data": dict(r.data)} for r in page.records]

    return read


async def run_module(db: Any, *, module: LogicModule, body: Mapping[str, Any],
                     package_id: str, viewer: Viewer, store: Any = objects_store) -> Any:
    """Parse *body* with the input model of *module*, then run it. Answers
    what the module returned, unchecked: :func:`apply_proposed_writes` checks it.

    A body that the model refuses is ``bad_input``. A module that raises is
    ``logic_failed``, and the log holds the exception type only. A database
    fault is not a module fault: it is raised as it is, so the door answers 500
    and the session rolls back."""
    try:
        parsed: BaseModel = module.input_model.model_validate(dict(body))
    except ValidationError:
        raise LogicRefused(BAD_INPUT) from None
    reader = _reader(db, package_id=package_id, viewer=viewer, module=module, store=store)
    try:
        return await module.run(parsed, reader)
    except LogicRefused:
        raise
    except SQLAlchemyError:
        raise
    except Exception as exc:
        _log.warning("studio.logic.module_failed", module=module.name, error=type(exc).__name__)
        raise LogicRefused(LOGIC_FAILED) from None


def _shape(module: LogicModule, proposed: Any) -> list[ProposedWrite]:
    """The proposal as a list of checked writes, with no statement. Raises
    :class:`LogicRefused` for each fault of the shape."""
    if not isinstance(proposed, list):
        raise LogicRefused(BAD_OUTPUT)
    if len(proposed) > MAX_WRITES_PER_BATCH:
        raise LogicRefused(TOO_MANY_WRITES)
    for item in proposed:
        if not isinstance(item, ProposedWrite) or not isinstance(item.object, str) \
                or item.op not in _OPS:
            raise LogicRefused(BAD_OUTPUT)
        has_data = isinstance(item.data, Mapping)
        ok_id = isinstance(item.id, str) and bool(item.id)
        well_formed = (
            (item.op == "create" and item.id is None and has_data)
            or (item.op == "update" and ok_id and has_data)
            or (item.op == "delete" and ok_id and not item.data))
        if not well_formed:
            raise LogicRefused(BAD_OUTPUT)
    for item in proposed:
        if (item.object, item.op) not in module.writes:
            raise LogicRefused(UNDECLARED_WRITE)
    return proposed


class _Undo(Exception):
    """Roll the SAVEPOINT of a preview back."""


async def apply_proposed_writes(
    db: Any, *, member: str, package_id: str, module: LogicModule, proposed: Any,
    viewer: Viewer, audit: Callable[[dict[str, Any]], Awaitable[None]],
    detail: Mapping[str, Any], preview: bool = False, store: Any = objects_store,
) -> list[AppliedWrite]:
    """Check *proposed* and apply it as *member*, with ONE ``apply_writes``
    call, all or none. See the module text for the refusals.

    *audit* writes one ``app_audit`` row in the open session. *detail* is the
    base of each row: the action, its ``uses`` and its class. The org comes
    from the bound session and the package from *package_id*, never from the
    proposal. A *preview* rolls the whole batch back, and its rows say
    ``simulated``. Answers one :class:`AppliedWrite` for each write, in order."""
    writes = _shape(module, proposed)
    if not writes:
        return []
    objects: dict[str, Any] = {}
    for name in dict.fromkeys(w.object for w in writes):
        try:
            found = await store.get_object_by_name(db, name, package_id=package_id)
        except objects_store.ObjectStoreError:
            found = None
        if found is None:
            raise LogicRefused(OBJECT_MISSING)
        objects[name] = found
    batch = [objects_store.Write(op=w.op, object_id=objects[w.object].id, record_id=w.id,
                                 data=None if w.op == "delete" else dict(w.data or {}))
             for w in writes]
    answers: list[Any] = []
    try:
        if preview:
            try:
                async with db.begin_nested():
                    answers = await store.apply_writes(
                        db, member, package_id=package_id, writes=batch, viewer=viewer)
                    raise _Undo
            except _Undo:
                pass
        else:
            answers = await store.apply_writes(
                db, member, package_id=package_id, writes=batch, viewer=viewer)
    except objects_store.BatchError as exc:
        # The SAVEPOINT of the batch rolled back, so the session is usable.
        raise LogicRefused(RECORD_REFUSED, exc.cause) from None
    except objects_store.ObjectStoreError as exc:
        raise LogicRefused(RECORD_REFUSED, exc) from None
    done: list[AppliedWrite] = []
    for w, held, answer in zip(writes, batch, answers, strict=True):
        obj = objects[w.object]
        rid = w.id if w.op == "delete" else getattr(answer, "id", None)
        done.append(AppliedWrite(obj.id, obj.name, w.op, rid,
                                 () if w.op == "delete" else tuple(sorted(held.data or {}))))
    for i, item in enumerate(done):
        row: dict[str, Any] = {**detail, "outcome": "simulated" if preview else "ran",
                               "object": item.object_name, "op": item.op, "index": i}
        if preview:
            row["preview"] = True
        if item.record_id is not None and not (preview and item.op == "create"):
            row["record_id"] = item.record_id
        await audit(row)
    return done
