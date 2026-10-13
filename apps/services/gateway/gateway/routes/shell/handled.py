"""Handled: what the apps closed for the member today (WS-58 MD-2c).

Spec: ``project-docs/specs/my_day_feed.md`` §6.7, and the MD-2c ticket of
§16, which wins where the two differ. The answer field is in §14.1a.

**Handled reads app history. It never infers anything from a missing
signal.** Each provider of ``needs.REGISTRY`` may carry a ``handled`` read.
Three do: My Tasks (``personal.my_completed_since``), Email
(``runner.applied_rules_since``) and Approvals
(``action_broker.read_decided``). Each read is the app's own authorized
function, and each opens the app's own session. This module adds no SQL and
no visibility rule.

**The gate is the provider's own** (``Provider.held_by``). A source that the
member does not hold reads ``absent``, and its read never runs. So only an
org admin who holds ``feature:approvals`` gets approval rows.

**Since the start of the member's day.** ``member_day`` calls
``state._hours``, the function that the state join calls (§6.8 item 8), and
``Day.day_start`` gives the instant. This module copies neither.

**The budget** is the state join's: ``state.STATE_TIMEOUT_S``, 1.5 seconds.
The feed runs this at the same time as the join, so the feed's worst case
stays at 7.5 seconds. The day read counts inside it. A read that is not done
when the budget ends counts as failed.

**The shape.** ``entries`` is a list of ``{app, title, href, by, at}``, the
newest first. Each source gives ``needs.PER_APP`` (15) entries at most, and
one entry for each entity. ``ok`` is true when no read that ran failed. A
source that reads ``absent`` does not count as failed. The client shows "The
rest is handled" only when ``entries`` is not empty and ``ok`` is true.

Fences: ``tests/unit/test_feed_handled.py`` and
``tests/unit/test_feed_handled_r8.py`` (R8).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from acb_auth import UserContext
from gateway.routes.shell import needs, state
from gateway.routes.shell.needs import PER_APP, Closed, Provider, _instant

logger = logging.getLogger(__name__)


@dataclass
class HandledRun:
    """The Handled group of one feed read."""

    entries: list[dict[str, Any]] = field(default_factory=list)
    #: True when no Handled read that ran failed (the claim flag).
    ok: bool = True
    #: What each source did: ``ok``, ``failed`` or ``absent``. The log and the
    #: tests read it. The answer carries only ``ok``.
    sources: dict[str, str] = field(default_factory=dict)


def handled_providers() -> list[Provider]:
    """The providers that carry a Handled read, in the registry's order."""
    return [p for p in needs.REGISTRY.values() if p.handled is not None]


async def member_day(user: UserContext) -> state.Day:
    """The member's zone and day hours, read as the state join reads them
    (``state.apply_state``): ``state._hours``, on the state module's own
    tenant session when the member holds no Calendar pane."""
    if user.has_permission("feature:tasks"):
        return await state._hours(user, None)
    async with state._tenant_session() as db:
        return await state._hours(user, db)


def _newest(closed: list[Closed]) -> list[Closed]:
    """One entry for each entity, the newest, and the newest first. An entry
    with no time goes last."""
    def stamp(c: Closed) -> float:
        at = _instant(c.at)
        return at.timestamp() if at is not None else float("-inf")

    kept: dict[str, Closed] = {}
    for item in closed:
        old = kept.get(item.key)
        if old is None or stamp(item) > stamp(old):
            kept[item.key] = item
    return sorted(kept.values(), key=lambda c: (-stamp(c), c.key))


async def handled_run(user: UserContext, now: datetime, *, budget_s: float) -> HandledRun:
    """Run each Handled read the member holds, at the same time, inside
    ``budget_s``. It never raises: a failure reads ``failed``, and the
    feed keeps its cards."""
    run = HandledRun()
    held: list[Provider] = []
    for provider in handled_providers():
        if provider.held_by(user):
            held.append(provider)
        else:
            run.sources[provider.source] = "absent"
    if not held:
        return run

    loop = asyncio.get_running_loop()
    deadline = loop.time() + budget_s
    try:
        day = await asyncio.wait_for(member_day(user), budget_s)
        since = day.day_start(now)
    except Exception as exc:  # a timeout too: every read that would run failed
        logger.warning("shell.feed handled day failed", extra={"error": type(exc).__name__})
        run.sources.update({p.source: "failed" for p in held})
        run.ok = False
        return run

    tasks = {p.source: asyncio.ensure_future(p.handled(user, since))  # type: ignore[misc]
             for p in held}
    left = max(0.0, deadline - loop.time())
    _done, late = await asyncio.wait(tasks.values(), timeout=left)
    for task in late:
        task.cancel()
    if late:
        await asyncio.gather(*late, return_exceptions=True)

    found: list[Closed] = []
    for source, task in tasks.items():
        if task in late or task.cancelled():
            logger.info("shell.feed handled read timed out", extra={"provider": source})
            run.sources[source] = "failed"
            continue
        exc = task.exception()
        if exc is not None:
            # The error's TYPE only, for the reason in ``needs.run_providers``.
            logger.warning("shell.feed handled read failed",
                           extra={"provider": source, "error": type(exc).__name__})
            run.sources[source] = "failed"
            continue
        run.sources[source] = "ok"
        found.extend(_newest(list(task.result() or []))[:PER_APP])
    run.ok = all(v != "failed" for v in run.sources.values())
    run.entries = [c.entry() for c in _newest(found)]
    return run
