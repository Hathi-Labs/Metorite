"""The shared durable job queue (WS-BRIEF BR-1a).

Spec: ``project-docs/specs/job_queue.md``. One Postgres table, ``jobs``, and
one worker in the gateway. A caller enqueues a job in its own tenant session,
so the job commits with the write that asked for it::

    from gateway.jobs import enqueue

    async with tenant_session() as db:
        await db.execute(...)                       # the caller's own write
        await enqueue(db, "brief.compose", {"member_id": member_id},
                      idempotency_key=f"brief:{member_id}:{day}")

A consumer registers one handler for each of its kinds with
:func:`register`. The rules for a handler are in ``registry.py``.

The worker (``worker.py``) is dark behind ``job_queue_enabled``. The store
(``store.py``) is the one writer of ``jobs``.
"""

from gateway.jobs.registry import register
from gateway.jobs.store import Enqueued, Job, PermanentJobError, enqueue

__all__ = ["Enqueued", "Job", "PermanentJobError", "enqueue", "register"]
