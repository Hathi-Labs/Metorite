"""The preview mark of AI Studio (STU-G4, ``ai_studio.md`` §6.2 step 4 and §14 G4).

The action door (``routes/studio/invoke.py``) runs a call with ``preview=1``
inside :func:`preview_scope`. In that scope the door commits no write and runs
no send. This module holds the one mark, so a later side effect can see it.

**The record event (C3).** C3 makes a create, an update and a delete emit
``studio.record.*`` through ``ingestion/event_hooks.py``, and a workflow can
start from that event. C3 is not built. :func:`emit_record_event` is the ONE
way that Studio code may emit such an event. It emits nothing inside a
preview, so a preview cannot start a workflow. C3 calls it and adds no second
path. Fence: ``tests/unit/test_studio_preview_door.py``
(``test_only_the_preview_module_reaches_the_event_hooks``) refuses a module of
``gateway/studio`` or ``gateway/routes/studio`` that names ``event_hooks`` or
``emit_event``, except this one.

The mark is a ContextVar, so it binds the in-process caller only. It is never
read from request input: the door sets it from its own check of the query.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from contextvars import ContextVar
from typing import Any

from acb_common import get_logger

__all__ = ["RECORD_EVENT_SOURCE", "emit_record_event", "in_preview", "preview_scope"]

_log = get_logger("gateway.studio.preview")

#: The source name of a record event (C3).
RECORD_EVENT_SOURCE = "studio"

_PREVIEW: ContextVar[bool] = ContextVar("studio_preview", default=False)


def in_preview() -> bool:
    """True inside a preview call of the door (G4)."""
    return _PREVIEW.get()


@contextlib.contextmanager
def preview_scope(on: bool = True) -> Iterator[None]:
    """Mark the code in the block as a preview when *on*. A scope inside a
    preview never clears the mark, so ``preview_scope(False)`` in a preview
    stays a preview. The old value comes back as the block ends."""
    token = _PREVIEW.set(_PREVIEW.get() or on)
    try:
        yield
    finally:
        _PREVIEW.reset(token)


async def emit_record_event(event_type: str, payload: dict[str, Any]) -> bool:
    """Emit one record event (C3), and answer True. Inside a preview it emits
    nothing, logs ``studio.preview.event_held`` and answers False. The import
    is late, as in ``routes/projects/core.py``, so the gateway loads without
    the ingestion package."""
    if in_preview():
        _log.info("studio.preview.event_held", event_type=event_type)
        return False
    from ingestion.event_hooks import emit_event

    await emit_event(RECORD_EVENT_SOURCE, event_type, payload)
    return True
