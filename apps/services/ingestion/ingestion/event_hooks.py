"""Event sink registry — lets higher layers subscribe to normalized provider
events without ingestion importing upward (the ``email_ingestion/post_sync.py``
hook pattern).

The gateway registers the Workflows event dispatcher here at startup
(``gateway/main.py``), so a Zoho/Gmail webhook can fire workflow
event triggers. With nothing registered, ``emit_event`` is a no-op — the
receivers behave exactly as before, and ingestion keeps working standalone.

``emit_event`` swallows sink errors **by default** and that default is
load-bearing for the receivers (a webhook must never 5xx because a workflow
sink failed). The ingestion consumer opts out with the keyword-only
``raise_on_error=True`` (§BO-20b): it must be able to *see* a failed dispatch
in order to withhold the ``XACK``. Never flip the default.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from acb_common import get_logger

_log = get_logger("ingestion.event_hooks")

EventSink = Callable[[str, str, dict[str, Any]], Awaitable[Any]]
"""async sink(source, event_type, payload)."""

_SINKS: list[EventSink] = []


def register_event_sink(sink: EventSink) -> None:
    """Subscribe to normalized provider events (idempotent per function)."""
    if sink not in _SINKS:
        _SINKS.append(sink)


def clear_event_sinks() -> None:
    """Drop all sinks (tests)."""
    _SINKS.clear()


async def emit_event(
    source: str,
    event_type: str,
    payload: dict[str, Any],
    *,
    raise_on_error: bool = False,
) -> None:
    """Fan an event out to every sink.

    Default (``raise_on_error=False``) is **best-effort**: a sink error is
    logged on ``event_hooks.sink_failed`` and the next sink still runs, so a
    sink error can never propagate back into a provider webhook response. Every
    receiver relies on this — the three ``BackgroundTasks.add_task(emit_event,
    source, event_type, payload)`` call sites pass three *positional* arguments
    and never reach this parameter.

    ``raise_on_error=True`` is the strict mode the ingestion consumer needs
    (§BO-20b): the **first** sink exception propagates to the caller and the
    remaining sinks are not invoked, because a drain loop that cannot observe a
    failure cannot honestly withhold an ``XACK``. It does **not** log — the
    caller owns the outcome and logs it once (``consumer.dispatch_failed``);
    logging here too would double-report the same failure.

    Keyword-only on purpose: a strict fan-out changes provider-facing
    behaviour, so it must be impossible to switch on by an extra positional
    argument slipping into an ``add_task`` call.
    """
    for sink in list(_SINKS):
        try:
            await sink(source, event_type, payload)
        except Exception as exc:
            if raise_on_error:
                raise
            _log.warning(
                "event_hooks.sink_failed",
                source=source,
                event_type=event_type,
                error=str(exc)[:160],
            )
