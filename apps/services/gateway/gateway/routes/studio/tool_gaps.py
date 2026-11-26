"""``GET /studio/tool-gaps`` — the gap view of an approver (SBX-1a).

Spec: ``project-docs/specs/agent_sandbox.md`` §9.2 and §12.1 decision 10.
The route registers on the one Studio router of ``routes/studio/actions.py``,
so the gate of J1 (``feature:studio``) runs first. The Control Plane proxy
``/api/studio/[[...path]]`` forwards it. SBX-1b draws it on ``/studio``.

The checks come in this order:

1. ``flags.studio_allowed`` for the org of the session, else 404.
2. ``user_tool_find_enabled``, else 404. With the flag off, nothing writes a
   row, so the route shows nothing.
3. ``studio:publish``, read once in the handler (never as a dependency, so
   the router keeps one gate), else 403.
4. One ``tenant_session()``, and ``studio/tool_gaps.gap_summary`` reads the
   bound org only.

The answer holds the counts by outcome and by category for 30 days, and the
50 newest rows with the need, the category, the outcome and the time only.
It names no member, no chat and no run. A read writes no audit row.
"""

from __future__ import annotations

from typing import Any

from acb_auth import UserContext, get_current_user
from acb_common import get_settings
from fastapi import Depends, HTTPException
from gateway.db import tenant_session as _tenant_session
from gateway.routes.studio import flags
from gateway.routes.studio.actions import NOT_FOUND, router
from gateway.routes.studio.packages import FORBIDDEN, SHARE_RIGHT
from gateway.studio import tool_gaps as gaps_store

#: The refusal of a caller without the share verb.
NO_GAPS_RIGHT = ("Only a holder of studio:publish reads the tool gaps of the organization. "
                 "Ask the owner of your organization for that right.")


def _find_enabled() -> bool:
    """True when ``user_tool_find_enabled`` is on. A fault reads as off."""
    try:
        return getattr(get_settings(), "user_tool_find_enabled", False) is True
    except Exception:  # fail closed
        return False


@router.get("/tool-gaps")
async def list_tool_gaps(user: UserContext = Depends(get_current_user)) -> dict[str, Any]:
    """The tool gaps of the bound org (``agent_sandbox.md`` §9.2). Only a
    holder of ``studio:publish`` reads it, and each other caller gets 403."""
    if not flags.studio_allowed(getattr(user, "organization_id", None)):
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    if not _find_enabled():
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    if not user.has_permission(SHARE_RIGHT):
        raise HTTPException(status_code=403, detail={"code": FORBIDDEN, "message": NO_GAPS_RIGHT})
    async with _tenant_session() as db:
        summary = await gaps_store.gap_summary(db)
    return {
        "days": summary.days,
        "counts": {"by_outcome": summary.by_outcome, "by_category": summary.by_category},
        "gaps": [{"need": r.need, "category": r.category, "outcome": r.outcome,
                  "created_at": r.created_at.isoformat()} for r in summary.rows],
    }
