"""Connect in place: the connections of a draft (WS-STU I8a, ``ai_studio.md``
§8.1 and §14 I8a).

    GET /studio/packages/{id}/connections

The route registers on the router of ``actions.py``, as the sibling modules
do, so ``main.py`` mounts no second router and the gate
``require_feature_router("studio")`` holds (J1).

**The answer.** One row for each name of the draft's ``connections``, in the
order of the draft: ``name``, ``label``, ``met``, ``checkable``,
``setup_href`` and ``can_connect``. The IDE draws one line for each name,
with Connect on an unmet one.

* ``met`` comes from ``package_host/resolve.met_connections``, the one read of
  a connection need (I1a). It reads the bound org of the session, and the org
  rows and the member rows of the caller only. The route adds no second query
  of the registry.
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

**The order of the checks.** The switch ``STUDIO_ORGS`` (404, no session),
the id (404), then ``STUDIO_CONNECTION_GATE_ORGS`` (404, no session). So with
the gate off the read answers nothing and reads no registry. Then ONE session
reads the draft as the owner (``packages_store.get_package``). Another member,
another org and an App Workshop app get 404. The met set is read only when the
draft names a connection.

**Tenancy (R5).** The org is the bound session and the member is
``user.email``. The route takes no body and no query, so no org, member or
name comes from the request. A ``connections`` value that is not a list of
text gives no row, the fail-closed reading of the door.

Fences: ``tests/unit/test_studio_connect_in_place.py`` and
``tests/unit/test_studio_connect_in_place_r8.py`` (R8).
"""

from __future__ import annotations

import uuid
from typing import Any

from acb_auth import UserContext, get_current_user
from acb_common.connector_list import connector
from acb_common.studio_flags import connection_gate_on
from fastapi import Depends, HTTPException
from gateway.connections import mcp as mcp_registry
from gateway.connections import store as connections_store
from gateway.db import tenant_session as _tenant_session
from gateway.package_host import resolve as host_resolve
from gateway.routes import integrations_connections as console
from gateway.routes.studio import flags
from gateway.routes.studio.actions import NOT_FOUND, router
from gateway.studio import packages_store as store

__all__ = ["MCP_PREFIX", "checkable", "connection_rows", "router"]

#: The prefix of an MCP server in ``connections`` (I1a grammar).
MCP_PREFIX = "mcp:"


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail=NOT_FOUND)


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


def connection_rows(names: tuple[str, ...], met: frozenset[str],
                    user: UserContext) -> list[dict[str, Any]]:
    """One row for each name, in order. Pure: it reads no database."""
    return [{
        "name": name,
        "label": _label(name),
        "met": name in met,
        "checkable": checkable(name),
        "setup_href": console.setup_href_for(name),
        "can_connect": checkable(name) and console.may_connect(user, name),
    } for name in names]


@router.get("/packages/{package_id}/connections")
async def package_connections(
    package_id: str,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """The connections that the draft of the caller's package names, and
    whether the caller has each one now (WS-STU I8a)."""
    if not flags.studio_allowed(user.organization_id):
        raise _not_found()
    pid = _package_id(package_id)
    if not connection_gate_on(user.organization_id):
        raise _not_found()
    async with _tenant_session() as db:
        package = await store.get_package(db, user.email, pid)
        if package is None:
            raise _not_found()
        names = host_resolve.connection_names(package.manifest) or ()
        met = await host_resolve.met_connections(db, viewer=user.email) if names else frozenset()
    return {"connections": connection_rows(names, met, user)}
