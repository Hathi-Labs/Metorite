"""The retention of the act record (WS-PZ MM-1, ``personalization.md`` §19.4).

A row of ``member_action`` lives ``RETENTION_DAYS`` days (D-PZ-51). The prune
job of the job worker runs :func:`prune_hook` once a day for each org, on the
tenant session of that org. The hook never raises: a hook that raises fails
the whole prune of the org. A savepoint keeps the transaction usable after a
failure, and the log names the class only. It deletes nothing for an org that
the flag does not hold, because such an org has no rows.

``gateway/main.py`` imports this module at start, so the hook is in place
before the first tick.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from acb_common import get_logger, member_actions

_log = get_logger(__name__)


async def prune_hook(db: Any, today: date) -> None:
    """The ``worker.prune_hooks`` hook: delete the rows older than the
    retention."""
    try:
        async with db.begin_nested():
            deleted = await member_actions.prune(db)
    except Exception as exc:  # the class only: a DB message can echo a value
        _log.warning("member_actions.prune_failed", error=type(exc).__name__)
        return
    if deleted:
        _log.info("member_actions.pruned", deleted=deleted)


def register_prune() -> None:
    """Add :func:`prune_hook` to the prune of the job worker, once."""
    from gateway.jobs import worker

    if prune_hook not in worker.prune_hooks:
        worker.prune_hooks.append(prune_hook)


register_prune()
