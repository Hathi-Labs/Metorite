"""``/auth/me/agent-access`` — the member's switch for agent deletes (WS-55 EA-6h).

Spec: ``project-docs/specs/external_agent_access.md`` EA3-51 and §15 EA-6h
and EA-6i. The column is ``user_settings.agent_delete_enabled`` (migrations
295 and 316). It is layer 1 of the four layers of EA3-51: "Let my AI agents
delete tasks".

**Default ON (EA-6i).** The owner, 2026-10-30: "Agents delete switch should
default to on." So a member who has not chosen reads as on. That is no
settings row, or a row that holds NULL. Only a stored ``false`` is off.
Migration 316 turned each old ``false`` into NULL, because the old default
and an explicit off were the same value.

=========  ===========================================================
Route      Does
=========  ===========================================================
``GET``    ``{"delete_enabled": bool}``. True when the member has not
           chosen (no settings row, or NULL)
``PUT``    Body ``{"delete_enabled": bool}``. Writes an explicit
           ``true`` or ``false`` to each spelling of the member's row
=========  ===========================================================

**The case of the address (fix round 1).** A web session keeps the spelling
of the sign-in (``Ravi.K@acme.com``), and an agent reads with the lower
case. So the read matches in any case, and one ``false`` in any spelling is
off. The PUT writes every spelling, so the member's last choice wins.

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
error there fails the delete. Fences: ``tests/unit/test_mcp_edge_delete.py``
and ``tests/unit/test_agent_delete_default_r8.py`` (the four states and
migration 316).
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

#: The switch over EVERY spelling of the address (EA-6i fix round 1). A row
#: keys on the spelling of the session that wrote it (``Ravi.K@acme.com``),
#: and the address of an agent is lower case. ``bool_and`` skips a NULL. So
#: no row, or NULL rows only, give NULL (on), and one ``false`` gives false
#: (off). ⚠️ Not the ``any_case`` read of ``tasks/settings._load``: it picks
#: ONE row, so a lower-case NULL row would outvote a ``false`` and fail open.
_READ_SQL = (
    "SELECT bool_and(agent_delete_enabled) FROM user_settings "
    "WHERE organization_id = {org} AND lower(user_id) = lower(:uid)"
)

#: The row of the session's own spelling: a new row gets the calendar seed.
_OWN_ROW_SQL = (
    "SELECT 1 FROM user_settings WHERE organization_id = {org} AND user_id = :uid"
)

#: The PUT sets every spelling of the member's row, so a stale value under
#: another spelling never outvotes the member's new choice.
_SET_ALL_SQL = (
    "UPDATE user_settings SET agent_delete_enabled = :on, updated_at = now() "
    "WHERE organization_id = {org} AND lower(user_id) = lower(:uid)"
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
    """Is the switch of the member *uid* on, in the bound organization?

    EA-6i: the switch is ON until the member turns it off. No row is on,
    NULL ("the member has not chosen") is on, ``true`` is on, and only
    ``false`` is off. The address matches in any case, and a ``false`` in
    any spelling is off. An empty *uid* is off, and no read runs. A failed
    read raises, so a fault never reads as on or as off.
    """
    if not uid:
        return False
    row = (await db.execute(_sql(_READ_SQL), {"uid": uid})).first()
    value = None if row is None else row[0]
    if value is None:
        return True
    return value is True


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
            # Every spelling first, in one statement. Two PUTs of two
            # spellings then wait on each other, and they cannot deadlock.
            await db.execute(_sql(_SET_ALL_SQL), {"uid": uid, "on": body.delete_enabled})
            # The calendar seed, for a member with no settings row yet, as
            # `assistant_profile._upsert` does. An existing row changes the
            # switch only.
            seed: dict[str, Any] = {}
            if (await db.execute(_sql(_OWN_ROW_SQL), {"uid": uid})).first() is None:
                seed = await _seed_from_work_schedule(db, uid)
            cols = ["agent_delete_enabled", *seed]
            sql = upsert_settings_sql(cols, update=["agent_delete_enabled"])
            await db.execute(
                text(sql), {"uid": uid, "agent_delete_enabled": body.delete_enabled, **seed})
            # The answer is what an agent of this member now reads.
            on = await delete_enabled(db, uid)
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("auth.agent_access.write_failed", extra={"error": type(exc).__name__})
        raise HTTPException(status_code=503, detail="unavailable") from exc
    logger.info("auth.agent_access.set", extra={"delete_enabled": on})
    return {"delete_enabled": on}
