"""The handler registry of the job queue (WS-BRIEF BR-1a).

Spec: ``project-docs/specs/job_queue.md`` §4.3. One handler for each kind.
``register`` refuses a second handler of one kind. One built-in kind exists,
``jobs.noop``, whose handler returns at once. The JQ-2 check on staging uses
it.

The rules for a handler (§4.3):

1. It runs its own ``tenant_session(job.organization_id)`` for its own
   writes. The worker holds no session while the handler runs.
2. It is safe to run twice, because the queue runs a job at least once.
3. It does not catch a DB error inside its own ``tenant_session``. It lets the
   error raise, so the job retries (H-249).
4. It raises ``PermanentJobError`` when a retry cannot help.
5. It does not swallow ``asyncio.CancelledError``. A handler that swallows it
   can outlive its timeout and its lease, and then two runs overlap.
6. Its payload holds ids and small parameters only. The rule on the content
   is advisory, and the review checks it.

A test registers its own kinds with ``monkeypatch.setitem(registry._HANDLERS,
...)``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from gateway.jobs.store import KIND_RE, Job

#: A handler: an ``async def handler(job: Job) -> None``.
Handler = Callable[[Job], Awaitable[None]]

_HANDLERS: dict[str, Handler] = {}


def register(kind: str, handler: Handler) -> None:
    """Add *handler* for *kind*. A bad kind or a second handler of one kind
    raises ``ValueError``."""
    if not isinstance(kind, str) or KIND_RE.fullmatch(kind) is None:
        raise ValueError("kind must match [a-z][a-z0-9_.]{2,63}")
    if kind in _HANDLERS:
        raise ValueError(f"a handler of kind {kind!r} is already registered")
    _HANDLERS[kind] = handler


def get(kind: str) -> Handler | None:
    """The handler of *kind*, or None when no handler exists."""
    return _HANDLERS.get(kind)


async def _noop(job: Job) -> None:
    """The handler of ``jobs.noop``: it does nothing."""
    return None


register("jobs.noop", _noop)
