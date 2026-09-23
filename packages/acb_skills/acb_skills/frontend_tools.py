"""The dispatcher for browser-side tools (``useFrontendTool``).

A page registers a handler in the browser (``src/hooks/useFrontendTool.ts``).
The model cannot call it by name: its tools are server-side. So a SKILL tool
that wants the page to act calls :func:`emit_frontend_tool`, which pushes one
CUSTOM ``frontend_tool`` event onto the run's stream — the same queue
``emit_generative_ui`` uses — and ``AgentChat`` runs the registered handler
once per event id.

This is a helper for skill code, not a model tool. It carries no schema, so
it costs the core floor nothing (``test_tool_schema_diet.py``).

Fire-and-forget on purpose. A page that is not open has no handler, and the
event is ignored there; the calling tool must return a plain link as well,
so the member always has a way to get where they asked to go.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

#: A handler name: lower case, digits, `_` and `.` (a namespace, e.g.
#: ``projects.open_task``). Anything else never reaches the browser.
NAME = re.compile(r"^[a-z][a-z0-9_.]{0,63}$")

EVENT = "frontend_tool"


async def emit_frontend_tool(name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    """Ask the member's open page to run the browser handler ``name``.

    Returns ``{"ok": True, "id": ...}`` when the event is queued, or
    ``{"ok": False, "error": ...}`` when there is no run stream to carry it.
    Never raises.
    """
    if not NAME.fullmatch(str(name or "")):
        return {"ok": False, "error": f"not a frontend tool name: {name!r}"}
    payload = args if isinstance(args, dict) else {}
    try:
        from orchestrator.executor import resolve_run_queue

        from acb_skills.write_artifact import _WRITE_ARTIFACT_CONTEXT

        queue = resolve_run_queue(_WRITE_ARTIFACT_CONTEXT.get("session_id"))
    except Exception as exc:  # pragma: no cover — platform absent in isolation
        return {"ok": False, "error": str(exc)}
    if queue is None:
        return {"ok": False, "error": "no active run stream to dispatch into"}
    event_id = uuid.uuid4().hex
    await queue.put(
        {
            "type": "CUSTOM",
            "name": EVENT,
            "value": {"id": event_id, "name": name, "args": payload},
        }
    )
    return {"ok": True, "id": event_id}


__all__ = ["EVENT", "NAME", "emit_frontend_tool"]
