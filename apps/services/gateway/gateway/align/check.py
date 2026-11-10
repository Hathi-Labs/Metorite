"""The ``align.check`` handler, a stub (WS-ALN ALN-1, D160).

Reference: ``project-docs/specs/project_alignment.md`` §5 item 9.

The worker claims only the kinds that it holds a handler for, so a job of a
kind with no handler waits for ever. ALN-1 ships the producer before ALN-7
ships the checker. So this stub completes each job and does nothing else. It
reads no task and logs no text, and a second try of it is safe.

ALN-7 replaces it with the checker of §6, in ``detached`` mode, because a model
call needs that mode. The kind registers at import, and ``main.py`` imports this
module before the worker starts. Fence: ``tests/unit/test_align_trigger.py``.
"""

from __future__ import annotations

from typing import Any

from gateway.align.trigger import KIND
from gateway.jobs import worker


async def check(db: Any, payload: dict[str, Any]) -> None:
    """The ``session`` handler of :data:`KIND`. It returns at once, so the
    worker settles the job ``done``."""


def register_jobs(*, replace: bool = False) -> None:
    """Register the stub. *replace* serves a test that empties the registry."""
    worker.register(KIND, check, mode="session", idempotent=True, replace=replace,
                    effect="read")


register_jobs()
