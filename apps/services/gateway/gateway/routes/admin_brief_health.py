"""``GET /admin/brief/health`` — the delivery stats of the brief (WS-BRIEF
BR-2d, ``core_brief.md`` §7).

The route answers :meth:`gateway.brief.monitor.Stats.body` for the org of
the session: the counted runs, the on-time runs, the share against the 99%
target, and the lag percentiles of the delivered runs, over 7 days.

**Gate.** An org admin (``admin:members:read``, ``require_admin_user``). The
brief off for the org answers 404, so the dark route looks like no route.

**A pure read** (R5). The org is the bound org of the session. The request
names no org, no member and no date. The answer holds counts and seconds
only, never an address, a lead or a claim.

Fences: ``tests/unit/test_brief_monitor.py`` (pure) and
``tests/unit/test_brief_monitor_r8.py`` (the real database).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from acb_auth import UserContext
from fastapi import APIRouter, Depends, HTTPException

from gateway.brief import flags, monitor
from gateway.routes.admin._common import require_admin_user

router = APIRouter(prefix="/admin/brief", tags=["admin", "brief"])


def _now() -> datetime:
    """The route's one clock. A test pins it."""
    return datetime.now(UTC)


@router.get("/health")
async def brief_health(user: UserContext = Depends(require_admin_user)) -> dict[str, Any]:
    from gateway.db import tenant_session

    org = str(getattr(user, "organization_id", None) or "")
    if not org or not flags.org_allowed(org):
        raise HTTPException(status_code=404, detail="Not found")
    async with tenant_session() as db:
        return (await monitor.stats(db, _now())).body()
