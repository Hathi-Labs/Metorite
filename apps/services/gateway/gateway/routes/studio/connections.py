"""Connect in place: the connections of a draft (WS-STU I8a, ``ai_studio.md``
§8.1 and §14 I8a), and the ask of an admin (WS-STU I8b-1a, §8.2).

    GET    /studio/packages/{id}/connections
    POST   /studio/packages/{id}/connections/ask          body {"name": ...}
    DELETE /studio/packages/{id}/connections/ask?name=...

The routes register on the router of ``actions.py``, as the sibling modules
do, so ``main.py`` mounts no second router and the gate
``require_feature_router("studio")`` holds (J1).

**The answer of the read.** One row for each name of the draft's
``connections``, in the order of the draft: ``name``, ``label``, ``met``,
``checkable``, ``setup_href``, ``can_connect`` and ``asked``. The IDE draws one
line for each name, with Connect on an unmet one.

* ``met`` comes from ``package_host/resolve.met_connections``, the one read of
  a connection need (I1a). It reads the bound org of the session, and the org
  rows and the member rows of the caller only. The route adds no second query
  of the registry, and :func:`_met` is its one call.
* ``label`` comes from the connector list (IN-11) for a provider id. It is the
  server name for ``mcp:<server>``, and the id itself for an id that the list
  does not hold.
* ``setup_href`` and ``can_connect`` come from
  ``routes/integrations_connections.setup_href_for`` and ``may_connect``. So
  Studio keeps no copy of a connect flow and reads no right of its own.
* ``checkable`` is True only for a name that the registry can hold: a real
  ``mcp:<server>``, or a connector-list row that a registry writer writes
  (:func:`checkable`). It is False for a provider whose connection lives
  outside the registry (``connections/store.OUTSIDE_REGISTRY``: a mailbox, a
  WhatsApp number, a key of the console), for the bare id ``mcp`` (the list
  row of "Another system", which no writer writes), and for an id that the
  list does not hold. The read cannot meet such a name, so its ``met`` stays
  False and ``can_connect`` is False for each caller: a Connect would never
  turn the line connected, and the action door refuses each action while the
  draft names it.
* ``asked`` (I8b-1a) is True when the caller has a live ask of an admin for
  the name. The read asks ``connections/attention.asked_by`` once, and only
  when a name is not met. A met name reads False.

**The ask (I8b-1a).** A member who may not connect a name asks an admin. The
ask refuses a name that the draft does not hold (422 ``not_in_draft``), a name
that the registry cannot hold (409 ``not_checkable``), a name that the member
has now (409 ``already_met``), and a name that the member may connect (409
``can_connect``). It answers ``{"asked": true, "new": <bool>}``: ``new`` is
False when a live ask of the member exists, and that ask keeps its time. The
withdraw deletes the ask of the caller only, and answers
``{"asked": false, "withdrawn": <bool>}``. ``connections/attention.py`` runs
the SQL, and this module runs none. A change writes one ``app_audit`` row in
the same transaction, and moves the My Day cache of the org after the commit.

**The order of the checks.** The switch ``STUDIO_ORGS`` (404, no session),
the id (404), then ``STUDIO_CONNECTION_GATE_ORGS`` (404, no session). So with
the gate off no route answers, and none reads the registry or the store of
an ask. The ask then reads its body (413, 422). Then ONE session reads the
draft as the owner (``packages_store.get_package``). Another member, another
org and an App Workshop app get 404. The met set is read only when the draft
names a connection.

**Tenancy (R5).** The org is ``user.organization_id`` and the bound session,
and the member is ``user.email``. No org and no member come from the request.
The name of an ask must be a name of the draft. A ``connections`` value that
is not a list of text gives no row, the fail-closed reading of the door.

Fences: ``tests/unit/test_studio_connect_in_place.py``,
``tests/unit/test_studio_connect_in_place_r8.py`` (R8),
``tests/unit/test_studio_connection_ask.py`` and
``tests/unit/test_studio_connection_ask_r8.py`` (R8).
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from acb_auth import UserContext, get_current_user
from acb_common.connector_list import connector
from acb_common.studio_flags import connection_gate_on
from fastapi import Depends, HTTPException, Request
from gateway.connections import attention
from gateway.connections import mcp as mcp_registry
from gateway.connections import store as connections_store
from gateway.db import tenant_session as _tenant_session
from gateway.package_host import resolve as host_resolve
from gateway.routes import integrations_connections as console
from gateway.routes.apps._common import record_app_audit_in
from gateway.routes.studio import flags
from gateway.routes.studio.actions import NOT_FOUND, router
from gateway.studio import packages_store as store
from pydantic import BaseModel, ConfigDict, Field, ValidationError

__all__ = ["MCP_PREFIX", "checkable", "connection_rows", "router"]

#: The prefix of an MCP server in ``connections`` (I1a grammar).
MCP_PREFIX = "mcp:"

#: The most bytes of the body of an ask: ``{"name": "<connection>"}``.
MAX_ASK_BODY_BYTES = 1024
#: The longest connection name, the CHECK of the store of an ask.
MAX_NAME = 60

#: The codes of a refused ask (I8b-1a). The IDE reads them.
BAD_ASK = "bad_ask"
ASK_TOO_LARGE = "body_too_large"
NOT_IN_DRAFT = "not_in_draft"
NOT_CHECKABLE = "not_checkable"
ALREADY_MET = "already_met"
CAN_CONNECT = "can_connect"

BAD_ASK_TEXT = 'Send {"name": "<connection>"} with one name of the draft.'
ASK_TOO_LARGE_TEXT = "The ask is too large. Send one name only."
NOT_IN_DRAFT_TEXT = "The draft of this app does not name this connection."
NOT_CHECKABLE_TEXT = "Metorite cannot check this connection, so an admin cannot connect it from Studio."
ALREADY_MET_TEXT = "This connection works now. You do not need to ask."
CAN_CONNECT_TEXT = "You can connect it yourself."


class _AskBody(BaseModel):
    """The body of an ask: one name of the draft, and no other key."""

    model_config = ConfigDict(extra="forbid", strict=True)

    name: str = Field(min_length=1, max_length=MAX_NAME)


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail=NOT_FOUND)


def _refused(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def _package_id(raw: str) -> str:
    """The id as the store reads it. A bad id names nothing, so it is 404."""
    try:
        return str(uuid.UUID(raw))
    except (ValueError, TypeError, AttributeError):
        raise _not_found() from None


def _label(name: str) -> str:
    """The name that a member reads for *name*: the connector list label, or
    the MCP server name."""
    if name.startswith(MCP_PREFIX):
        return name[len(MCP_PREFIX):]
    row = connector(name)
    return str(row["label"]) if row else name


#: The sign-in of the list row of "Another system": the id ``mcp`` names the
#: family, and each server is a row of its own (``mcp:<server>``).
_FAMILY_SIGNIN = "mcp_address"


def checkable(name: str) -> bool:
    """True only for a name that the registry can hold, so that a connect can
    turn it met: a real ``mcp:<server>``, or a connector-list row that is no
    family row and not :data:`~gateway.connections.store.OUTSIDE_REGISTRY`.
    The bare id ``mcp`` and an id off the list are False: no writer writes
    them. ``test_studio_connect_in_place.py`` holds this rule equal to the
    registry writers for each id of the list."""
    if name.startswith(MCP_PREFIX):
        return bool(mcp_registry.NAME_RE.fullmatch(name[len(MCP_PREFIX):]))
    row = connector(name)
    return (row is not None and row.get("signin") != _FAMILY_SIGNIN
            and name not in connections_store.OUTSIDE_REGISTRY)


def connection_rows(names: tuple[str, ...], met: frozenset[str], user: UserContext,
                    asked: frozenset[str] = frozenset()) -> list[dict[str, Any]]:
    """One row for each name, in order. Pure: it reads no database."""
    return [{
        "name": name,
        "label": _label(name),
        "met": name in met,
        "checkable": checkable(name),
        "setup_href": console.setup_href_for(name),
        "can_connect": checkable(name) and console.may_connect(user, name),
        "asked": name in asked and name not in met,
    } for name in names]


async def _owned_names(db: Any, user: UserContext, pid: str) -> tuple[str, ...]:
    """The names of the draft of the caller's package *pid*, or 404 for a
    package that the caller does not own."""
    package = await store.get_package(db, user.email, pid)
    if package is None:
        raise _not_found()
    return host_resolve.connection_names(package.manifest) or ()


async def _met(db: Any, user: UserContext) -> frozenset[str]:
    """The one read of the met set of the caller (I1a). No other line of this
    module calls ``met_connections``."""
    return await host_resolve.met_connections(db, viewer=user.email)


@router.get("/packages/{package_id}/connections")
async def package_connections(
    package_id: str,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """The connections that the draft of the caller's package names, and
    whether the caller has each one now (WS-STU I8a). It changes no row."""
    if not flags.studio_allowed(user.organization_id):
        raise _not_found()
    pid = _package_id(package_id)
    if not connection_gate_on(user.organization_id):
        raise _not_found()
    async with _tenant_session() as db:
        names = await _owned_names(db, user, pid)
        met = await _met(db, user) if names else frozenset()
        unmet = [n for n in names if n not in met]
        asked = (await attention.asked_by(db, str(user.organization_id), user.email, unmet)
                 if unmet else frozenset())
    return {"connections": connection_rows(names, met, user, asked)}


async def _ask_name(request: Request) -> str:
    """The name of the body of an ask. The read stops past
    :data:`MAX_ASK_BODY_BYTES` (413). Any other shape is 422. No session is
    open during the read."""
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_ASK_BODY_BYTES:
            raise _refused(413, ASK_TOO_LARGE, ASK_TOO_LARGE_TEXT)
        chunks.append(chunk)
    try:
        return _AskBody.model_validate(json.loads(b"".join(chunks))).name
    except (ValueError, RecursionError, ValidationError):
        raise _refused(422, BAD_ASK, BAD_ASK_TEXT) from None


def _withdraw_name(request: Request) -> str:
    """The one ``name`` of the query of a withdraw, or 422."""
    given = request.query_params.getlist("name")
    if len(given) != 1 or not 1 <= len(given[0]) <= MAX_NAME:
        raise _refused(422, BAD_ASK, BAD_ASK_TEXT)
    return given[0]


def _check_name(name: str, names: tuple[str, ...]) -> None:
    """Refuse a name that is not in the draft, or that the registry cannot
    hold. The first two checks of §8.2, before any read of the met set."""
    if name not in names:
        raise _refused(422, NOT_IN_DRAFT, NOT_IN_DRAFT_TEXT)
    if not checkable(name):
        raise _refused(409, NOT_CHECKABLE, NOT_CHECKABLE_TEXT)


def _check_member(name: str, met: frozenset[str], user: UserContext) -> None:
    """Refuse a name that the caller has now, or may connect. The last two
    checks of §8.2."""
    if name in met:
        raise _refused(409, ALREADY_MET, ALREADY_MET_TEXT)
    if console.may_connect(user, name):
        raise _refused(409, CAN_CONNECT, CAN_CONNECT_TEXT)


async def _bump(org: Any) -> None:
    """Move the My Day cache of the org after a commit, because the needs view
    holds the asks (I8b-1b). It never raises. Imported here, as the other
    callers do, so the Studio routes do not load the shell routes."""
    from gateway.routes.shell import cache as feed_cache

    await feed_cache.bump_org_safe(org)


@router.post("/packages/{package_id}/connections/ask")
async def ask_admin(
    package_id: str,
    request: Request,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Ask the admins of the org to connect one name of the caller's draft
    (WS-STU I8b-1a). A second ask of a live ask changes nothing."""
    if not flags.studio_allowed(user.organization_id):
        raise _not_found()
    pid = _package_id(package_id)
    if not connection_gate_on(user.organization_id):
        raise _not_found()
    name = await _ask_name(request)
    async with _tenant_session() as db:
        _check_name(name, await _owned_names(db, user, pid))
        _check_member(name, await _met(db, user), user)
        new = await attention.ask(db, str(user.organization_id), user.email, name, pid)
        if new:
            # The row commits with the ask, or neither does. It holds the name only.
            await record_app_audit_in(db, app_id=pid, user_email=user.email, kind="action",
                                      detail={"op": "connection_ask", "name": name})
    if new:
        await _bump(user.organization_id)
    return {"asked": True, "new": new}


@router.delete("/packages/{package_id}/connections/ask")
async def withdraw_ask(
    package_id: str,
    request: Request,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Withdraw the ask of the caller for one name (WS-STU I8b-1a). The ask
    of another member stays."""
    if not flags.studio_allowed(user.organization_id):
        raise _not_found()
    pid = _package_id(package_id)
    if not connection_gate_on(user.organization_id):
        raise _not_found()
    name = _withdraw_name(request)
    async with _tenant_session() as db:
        await _owned_names(db, user, pid)
        gone = await attention.withdraw(db, str(user.organization_id), user.email, name)
        if gone:
            await record_app_audit_in(db, app_id=pid, user_email=user.email, kind="action",
                                      detail={"op": "connection_withdraw", "name": name})
    if gone:
        await _bump(user.organization_id)
    return {"asked": False, "withdrawn": gone}
