"""The durable trigger of the alignment check (WS-ALN ALN-1, D160).

Reference: ``project-docs/specs/project_alignment.md`` §5.

A task write enqueues one ``align.check`` job on the session of the write. So
the write and its job commit together, or neither does. A crash between a
commit and a later enqueue would lose the job, and this form cannot.

**The seam is the handlers.** ``tasks.create_task``, ``tasks.patch_task`` and
``tasks.move_task_in`` call :func:`enqueue_check`, and each call sits inside the
``_tenant_session`` of the write. No hook sits on ``insert_row`` or
``update_row``. A personal task is never checked, so
``personal.create_personal_task`` does not call it. Recurrence and the file
import stay out in v1 (§5 item 5). Fence: ``test_the_seam_*`` in
``tests/unit/test_align_trigger.py``.

**The gate.** :func:`jobs_enabled` runs first and touches no database, so a
member of an org that is not listed pays no statement. It needs the worker on
(``worker.worker_enabled``) AND the org in ``ALIGN_JOBS_ORGS``. ``queue.prune``
deletes no queued row, so a queue with no worker would grow without a bound.

**A failed enqueue never fails the write** (§5 item 7). Each statement of this
module runs in a savepoint. An error rolls the savepoint back, so the
transaction of the member stays usable, and the line ``align.enqueue_failed``
names the task id and the error class. It holds no task text. A
``CancelledError`` is no ``Exception``, so a cancel still ends the request.

**Tenancy (R5).** This module opens no session and takes no org from a request.
The caller passes the tenant that the session binds (``current_tenant``), and
only the gate reads it. The job carries no org: the column DEFAULT reads the
bound tenant, and RLS holds the row to it. The payload holds the task id only.

**The hash.** :func:`content_hash` is the SHA-256 hex digest of the title, a NUL
and the description, in that order, as UTF-8. Postgres text holds no NUL, so two
different pairs never give one input. A NULL and an empty text are the same
text. No other field goes in, so a due date moves no hash. ``pm_tasks`` has no
acceptance column, so the acceptance text of §5 item 2 is not in it. ALN-7 reads
this same function for the ``content_hash`` of a finding.

**The dedupe key** is ``align:<task_id>:<sha256 hex>``: 6 + 36 + 1 + 64 = 107
characters, under the 200 of ``queue.DEDUPE_CHARS``, with no cut of the hash.
A repeat of a live key answers the live job.
"""

from __future__ import annotations

import functools
import hashlib
from typing import Any

from acb_common import get_logger, get_settings
from sqlalchemy import text

from gateway.jobs import queue
from gateway.jobs.worker import worker_enabled

_log = get_logger("align.trigger")

#: The job kind. The CHECK ``jobs_kind`` is a regex, so a kind needs no migration.
KIND = "align.check"

#: The separator of the hash input. Postgres text cannot hold it.
_SEPARATOR = "\x00"

_PROJECT_OWNER_SQL = (
    "SELECT personal_owner FROM pm_projects WHERE id = CAST(:project_id AS uuid)"
)


@functools.lru_cache(maxsize=16)
def _parse_orgs(raw: str) -> frozenset[str]:
    """The entries of a comma list, trimmed and in lower case."""
    return frozenset(entry.strip().lower() for entry in raw.split(",") if entry.strip())


def jobs_enabled(org: str | None) -> bool:
    """True when a task write of *org* may enqueue. The one reader of
    ``ALIGN_JOBS_ORGS``, and it reads the settings at each call, so a SIGHUP
    reload applies. No database work."""
    if not org or not worker_enabled():
        return False
    orgs = _parse_orgs(str(getattr(get_settings(), "align_jobs_orgs", "") or ""))
    return "*" in orgs or str(org).strip().lower() in orgs


def _checked_text(row: Any) -> tuple[str, str]:
    """The fields of the hash, in hash order: the title, then the description."""
    return (getattr(row, "title", None) or "", getattr(row, "description", None) or "")


def content_hash(row: Any) -> str:
    """The SHA-256 hex digest of the checked text of *row*."""
    return hashlib.sha256(_SEPARATOR.join(_checked_text(row)).encode("utf-8")).hexdigest()


def dedupe_key(task_id: str, digest: str) -> str:
    """The key that keeps one live job for one text of one task."""
    return f"align:{task_id}:{digest}"


async def _in_personal_project(db: Any, row: Any) -> bool:
    """True when the project of *row* is personal (§1.2). ``personal_owner`` is
    stamped on each node of a personal tree, so the node's own column is the
    whole test (``core._TENANT_PROJECTS_SQL``). A read that finds no project
    answers True, so a doubt enqueues nothing."""
    found = (await db.execute(
        text(_PROJECT_OWNER_SQL), {"project_id": str(row.project_id)},
    )).fetchone()
    return found is None or bool(found.personal_owner)


async def enqueue_check(
    db: Any, task_id: str, org: str | None, *, row: Any, before: Any = None,
    known_team: bool = False,
) -> queue.Enqueued | None:
    """Queue one check of the task on the session *db* of its write.

    *row* is the task row after the write. *org* is the tenant that *db*
    binds. *before* is the row before an edit. When the checked text is the
    same in both rows, nothing happens (§5 item 2). *known_team* says that the
    caller read the project and that it is no personal project, so this
    function needs no read of it. Left False, this function reads the project
    and enqueues nothing for a personal task.

    Answers the job (``created`` is False for a repeat of a live key), or None
    when the gate is closed, the text is the same, the task is personal or the
    enqueue failed. It never raises an ``Exception``, and the session stays
    usable.
    """
    if not jobs_enabled(org):
        return None
    try:
        if before is not None and _checked_text(before) == _checked_text(row):
            return None
        key = dedupe_key(task_id, content_hash(row))
        async with db.begin_nested():
            if not known_team and await _in_personal_project(db, row):
                return None
            return await queue.enqueue(db, KIND, {"task_id": str(task_id)}, dedupe_key=key)
    except Exception as exc:
        # The class only. An error of the driver quotes the statement.
        _log.warning("align.enqueue_failed", task_id=str(task_id), error=type(exc).__name__)
        return None
