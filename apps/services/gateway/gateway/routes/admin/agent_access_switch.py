"""``/auth/me/agent-access`` — the member's switch for agent deletes (WS-55 EA-6h).

Spec: ``project-docs/specs/external_agent_access.md`` EA3-51 and §15 EA-6h.
The column is ``user_settings.agent_delete_enabled`` (migration 295). It is
layer 1 of the four layers of EA3-51: "Let my AI agents delete tasks",
default OFF.

=========  ===========================================================
Route      Does
=========  ===========================================================
``GET``    ``{"delete_enabled": bool}``. False when the member has no
           settings row
``PUT``    Body ``{"delete_enabled": bool}``. Sets the switch
=========  ===========================================================

**Who reads it.** The Integrations app (the AI agents view), and the MCP edge
at ``tools/list``. So the ``GET`` is in ``agent_oauth.MCP_ROUTES``, and a
signed agent request reads the switch of the connection's member. The edge
shows the tool ``delete_task`` only while the switch is on.
``POST /projects/tasks/{task_id}/agent-delete`` reads it through
:func:`delete_enabled`, in the transaction of the delete.

**Only the member writes it.** The ``PUT`` is NOT in ``MCP_ROUTES``, so
branch 0 refuses a signed request with 401. The route also refuses an agent
caller with 403, so an agent can never turn its own switch on.

**The flag.** While ``agent_access_enabled`` is off, both routes answer 404,
as ``routes/agent_access.py`` does.

The member comes from the session and the tenant from the bound session,
never from the body (R5, R11). The body forbids an extra field. A failed read
answers 503, never false, so a database fault never reads as "off" in the
UI. The delete route reads the switch inside its own transaction, and an
error there fails the delete. Fence: ``tests/unit/test_mcp_edge_delete.py``.
"""

from __future__ import annotations

import logging
from typing import Any

from acb_auth import UserContext, agent_oauth, get_current_user
from fastapi import Depends, HTTPException
from gateway.db import tenant_session as _tenant_session
from gateway.routes.admin.me import me_router
from pydantic import BaseModel, ConfigDict

logger = logging.getLogger(__name__)

#: The 403 of a PUT from an agent. An agent never sets its own switch.
AGENT_REFUSED = "Only the member sets this switch, in Metorite."

_READ_SQL = (
    "SELECT agent_delete_enabled FROM user_settings "
    "WHERE organization_id = {org} AND user_id = :uid"
)


class AgentAccessPut(BaseModel):
    """What ``PUT /auth/me/agent-access`` accepts."""

    model_config = ConfigDict(extra="forbid")

    delete_enabled: bool


def _sql(template: str) -> Any:
    from gateway.routes.tasks.settings import _BOUND_ORG
    from sqlalchemy import text

    return text(template.replace("{org}", _BOUND_ORG))


def _member(user: UserContext) -> str:
    """The session's address, the key that ``/tasks/settings`` writes under."""
    uid = getattr(user, "email", None) or ""
    if not uid:
        raise HTTPException(status_code=401, detail="Authentication required")
    return uid


def _require_enabled() -> None:
    if not agent_oauth.enabled():
        raise HTTPException(status_code=404, detail="Not Found")


async def delete_enabled(db: Any, uid: str) -> bool:
    """True only when the member *uid* turned the switch on in the bound
    organization. No row is false. A failed read raises."""
    row = (await db.execute(_sql(_READ_SQL), {"uid": uid})).first()
    return bool(row is not None and row[0] is True)


@me_router.get("/me/agent-access", summary="The caller's agent switches (EA-6h)")
async def get_agent_access(user: UserContext = Depends(get_current_user)) -> dict[str, Any]:
    """The switch of the caller. An agent reads the switch of its member."""
    _require_enabled()
    uid = _member(user)
    try:
        async with _tenant_session() as db:
            on = await delete_enabled(db, uid)
    except Exception as exc:
        logger.warning("auth.agent_access.read_failed", extra={"error": type(exc).__name__})
        raise HTTPException(status_code=503, detail="unavailable") from exc
    return {"delete_enabled": on}


@me_router.put("/me/agent-access", summary="Set the caller's agent switches (EA-6h)")
async def put_agent_access(
    body: AgentAccessPut, user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Set the switch. Only a member, never an agent."""
    _require_enabled()
    if getattr(user, "agent", None) is not None:
        raise HTTPException(status_code=403, detail=AGENT_REFUSED)
    uid = _member(user)
    from gateway.routes.tasks.settings import _seed_from_work_schedule, upsert_settings_sql
    from sqlalchemy import text

    try:
        async with _tenant_session() as db:
            # The calendar seed, for a member with no settings row yet, as
            # `assistant_profile._upsert` does. An existing row changes the
            # switch only.
            seed: dict[str, Any] = {}
            if (await db.execute(_sql(_READ_SQL), {"uid": uid})).first() is None:
                seed = await _seed_from_work_schedule(db, uid)
            cols = ["agent_delete_enabled", *seed]
            sql = upsert_settings_sql(cols, update=["agent_delete_enabled"])
            row = (await db.execute(
                text(f"{sql} RETURNING agent_delete_enabled"),
                {"uid": uid, "agent_delete_enabled": body.delete_enabled, **seed},
            )).first()
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("auth.agent_access.write_failed", extra={"error": type(exc).__name__})
        raise HTTPException(status_code=503, detail="unavailable") from exc
    on = bool(row is not None and row[0] is True)
    logger.info("auth.agent_access.set", extra={"delete_enabled": on})
    return {"delete_enabled": on}
