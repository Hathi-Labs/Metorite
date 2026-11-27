"""``GET /shell/setup``: the state of the setup steps of the member (OB-1a).

Spec: ``project-docs/specs/onboarding.md`` §7.1. The route runs no SQL. The one
function ``gateway.setup_status.setup_status`` holds the registry and the
queries. The card (OB-2) and the chat (OB-5) read the same function.

The org and the member come from the session (R5, R11). The request names
neither. The route needs no feature, as ``/shell/feed/prefs`` needs none, and
each step carries its own audience. A signed agent has no setup list, so it
gets 404. No caller reads the route yet, so it is dark by having no reader.

Fence: ``tests/unit/test_setup_status_r8.py`` and
``tests/unit/test_setup_status_source.py``.
"""

from __future__ import annotations

from acb_auth import UserContext, get_current_user
from fastapi import Depends, HTTPException
from gateway.routes.shell.search import router
from gateway.setup_status import setup_status


@router.get("/setup", summary="The setup steps of the member (OB-1a)")
async def get_setup(user: UserContext = Depends(get_current_user)) -> dict:
    """One item for each step that the member's role and features allow."""
    org = getattr(user, "organization_id", None)
    if getattr(user, "agent", None) is not None or not org or not user.email:
        raise HTTPException(status_code=404, detail="Not found")
    steps = await setup_status(
        str(org), user.email,
        permission=user.has_permission, feature=user.can_use_feature,
    )
    return {"steps": steps}
