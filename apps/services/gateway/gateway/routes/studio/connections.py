"""Connect in place: the connections of a draft (WS-STU I8a, ``ai_studio.md``
§8.1 and §14 I8a), and the ask of an admin (WS-STU I8b-1a, §8.2).

    GET    /studio/packages/{id}/connections
    POST   /studio/packages/{id}/connections/ask          body {"name": ...}
    DELETE /studio/packages/{id}/connections/ask?name=...

The routes register on the router of ``actions.py``, as the sibling modules
do, so ``main.py`` mounts no second router and the gate
``require_feature_router("studio")`` holds (J1).

**The answer of the read.** One row for each name of the draft's
``connections``, in the order of the draft: ``name``, ``label``, ``as``,
``met``, ``reason``, ``checkable``, ``setup_href``, ``can_connect`` and
``asked``. The IDE draws one
line for each name, with Connect on an unmet one.

* ``met`` comes from ``package_host/resolve.met_connections``, the one read of
  a connection need (I1a). It reads the bound org of the session, and the org
  rows and the member rows of the caller only. The route adds no second query
  of the registry, and :func:`_met` is its one call. WS-STU I9a, I9b-1: each
  line reads the rule of the door for the draft (:func:`_need_reason`). A
  ``member`` need reads the rows of the caller, and one row must hold every
  scope of the need. An ``org`` need of a draft always reads
  ``org_approval``, whatever the org rows hold.
* ``label`` comes from the connector list (IN-11) for a provider id. It is the
  server name for ``mcp:<server>``, and the id itself for an id that the list
  does not hold.
* ``setup_href`` and ``can_connect`` come from
  ``routes/integrations_connections.setup_href_for`` and ``may_connect``. So
  Studio keeps no copy of a connect flow and reads no right of its own.
  ``can_connect`` is :func:`_can_connect`, the one rule that the ask reads too.
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
has now (409 ``already_met``), a name that the member may connect (409
``can_connect``), and a name that no connect of an admin can meet (409
``admin_cannot_connect``). ``already_met`` uses the rule of the line of the
read (:func:`_need_met`, I9a, I9b-1). So an ``org`` need of a draft is never
``already_met``, and the ask tells a caller nothing of the org rows.
``can_connect`` uses the rule of the line too (:func:`_can_connect`), and
:func:`_admin_can_meet` holds the last rule. It answers
``{"asked": true, "new": <bool>}``: ``new`` is False when a live ask of the
member exists, and that ask keeps its time. The
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
from collections.abc import Sequence
from typing import Any

from acb_auth import UserContext, get_current_user
from acb_common.connector_list import connector
from acb_common.studio_flags import connection_gate_on
from acb_common.studio_manifest import connection_as, connection_item, connection_scopes
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
ADMIN_CANNOT_CONNECT = "admin_cannot_connect"
TOO_MANY_ASKS = "too_many_asks"

BAD_ASK_TEXT = 'Send {"name": "<connection>"} with one name of the draft.'
ASK_TOO_LARGE_TEXT = "The ask is too large. Send one name only."
NOT_IN_DRAFT_TEXT = "The draft of this app does not name this connection."
NOT_CHECKABLE_TEXT = "Metorite cannot check this connection, so an admin cannot connect it from Studio."
ALREADY_MET_TEXT = "This connection works now. You do not need to ask."
CAN_CONNECT_TEXT = "You can connect it yourself."
ADMIN_CANNOT_CONNECT_TEXT = (
    "An admin cannot connect this for you. Change the connection in the draft.")
TOO_MANY_ASKS_TEXT = (
    "You have 10 open asks. Wait for an admin to answer one, or withdraw one, then ask again.")


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


#: One need of a draft: ``(name, as, scopes)``, from :func:`connection_needs`.
Need = tuple[str, str, tuple[str, ...] | None]


def connection_needs(manifest: Any) -> tuple[Need, ...]:
    """``(name, as, scopes)`` of each item of ``connections``, in order
    (WS-STU I9a, I9b-1).

    ``as`` is ``org`` or ``member``, from :func:`connection_item`, and the
    scopes come from :func:`connection_scopes` (None for a wrong shape), the
    one reading that the door and the sidebar use. A manifest with no valid
    ``connections`` gives an empty tuple. Pure."""
    if not host_resolve.connection_names(manifest):
        return ()
    raws = manifest.get("connections", [])
    items = [connection_item(raw) for raw in raws]
    if None in items:
        return ()
    return tuple((i[0], i[1], connection_scopes(raw))
                 for i, raw in zip(items, raws, strict=True) if i is not None)


def _need_reason(name: str, whose: str, scopes: tuple[str, ...] | None,
                 met: host_resolve.Met) -> str | None:
    """Why the need ``(name, whose, scopes)`` is unmet for the DRAFT of the
    owner, or None when it is met (WS-STU I9a, I9b-1). Pure.

    THE one rule of this module. It is
    :func:`~gateway.package_host.resolve.need_reason`, the rule of the door. A
    draft is never an approved install, so each ``org`` need reads
    ``org_approval``, whatever the org rows hold. That check comes first, so
    no line and no ask tells a caller whether an org account exists or which
    scopes it holds. A ``member`` need reads the rows of the caller only, and
    one row must hold every scope of the need."""
    return host_resolve.need_reason(name, whose, scopes, met, False)


def _need_met(name: str, whose: str, scopes: tuple[str, ...] | None,
              met: host_resolve.Met) -> bool:
    """True when :func:`_need_reason` finds the need met (WS-STU I9a, I9b-1).

    The ``met`` of a line and the ``already_met`` refusal of an ask both read
    this one rule. So an ``org`` need of a draft is never ``already_met``. Pure."""
    return _need_reason(name, whose, scopes, met) is None


def _can_connect(name: str, whose: str, user: UserContext) -> bool:
    """True when a Connect of *user* can meet the need ``(name, whose)``.

    A Connect writes a row of the SCOPE of the connector (member or org, the
    ``connection_as`` of the name with no declared ``as``). When the declared
    ``as`` differs from that scope, no writer can make a row that meets the
    need, so a Connect would be a dead end (I9a review P2). The
    ``can_connect`` of a line and the ``can_connect`` refusal of an ask both
    read this one rule (I8b-1a review P1). Each owning app asks FIXED scopes,
    so a ``reconnect`` cannot widen a grant yet (I9b-1, a known limit). Pure."""
    return (checkable(name) and connection_as(name) == whose
            and console.may_connect(user, name))


def _admin_can_meet(name: str, whose: str) -> bool:
    """True when the Connect of an admin can meet the need ``(name, whose)`` of
    another member (WS-STU I8b-1a). Pure.

    The Connect writes a row of the scope of the connector (``connection_as``
    of the name). A member row of the admin never meets a need of another
    member, and a member connector never gets an org row. So only an ``org``
    need on a connector of ``org`` scope can be met. That is also the one row
    that clears an ask (``connections/store._clear_asks``)."""
    return whose == "org" and connection_as(name) == "org"


def connection_rows(needs: Sequence[tuple[Any, ...]], met: host_resolve.Met,
                    user: UserContext,
                    asked: frozenset[str] = frozenset()) -> list[dict[str, Any]]:
    """One row for each ``(name, as, scopes)``, in order. Pure: it reads no
    database. Each need has three items: a shorter one raises, so no need
    meets by default.

    A row is ``met`` by the SAME rule that the door reads for that need
    (:func:`_need_reason`), for the DRAFT of the owner. A draft is never an
    approved install, so each ``org`` need reads ``org_approval`` and ``met``
    False, whatever the org rows hold (I9b-1). ``reason`` is ``ok``,
    ``missing``, ``reconnect`` or ``org_approval``. ``asked`` (I8b-1a) is True
    only on a line that is not met."""
    rows: list[dict[str, Any]] = []
    for need in needs:
        name, whose, scopes = need[0], need[1], need[2]
        reason = _need_reason(name, whose, scopes, met)
        rows.append({
            "name": name,
            "label": _label(name),
            "as": whose,
            "met": reason is None,
            "reason": reason or "ok",
            "checkable": checkable(name),
            "setup_href": console.setup_href_for(name),
            "can_connect": _can_connect(name, whose, user),
            "asked": name in asked and reason is not None,
        })
    return rows


async def _owned_needs(db: Any, user: UserContext, pid: str) -> tuple[Need, ...]:
    """The ``(name, as, scopes)`` needs of the draft of the caller's package
    *pid* (:func:`connection_needs`), or 404 for a package that the caller
    does not own."""
    package = await store.get_package(db, user.email, pid)
    if package is None:
        raise _not_found()
    return connection_needs(package.manifest)


async def _met(db: Any, user: UserContext) -> host_resolve.Met:
    """The one read of the met sets of the caller (I1a, split by I9a). No
    other line of this module calls ``met_connections``."""
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
        needs = await _owned_needs(db, user, pid)
        met = await _met(db, user) if needs else host_resolve.Met()
        unmet = [n for n, whose, scopes in needs if not _need_met(n, whose, scopes, met)]
        asked = (await attention.asked_by(db, str(user.organization_id), user.email, unmet)
                 if unmet else frozenset())
    return {"connections": connection_rows(needs, met, user, asked)}


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


def _check_name(name: str, needs: tuple[Need, ...]) -> None:
    """Refuse a name that is not in the draft, or that the registry cannot
    hold. The first two checks of §8.2, before any read of the met set."""
    if name not in {need[0] for need in needs}:
        raise _refused(422, NOT_IN_DRAFT, NOT_IN_DRAFT_TEXT)
    if not checkable(name):
        raise _refused(409, NOT_CHECKABLE, NOT_CHECKABLE_TEXT)


def _check_member(name: str, needs: tuple[Need, ...], met: host_resolve.Met,
                  user: UserContext) -> None:
    """Refuse a name that the caller has now, may connect, or that no admin
    can connect. The last three checks of §8.2. Each one reads the needs of
    the name that are not met.

    WS-STU I9a, I9b-1: "has now" is the rule of the line of the read
    (:func:`_need_met`). An ``org`` need of a draft is never met, so the ask
    never tells the caller whether an org row exists. A ``member`` need reads
    the rows of the caller only, scopes included. "May connect" is the rule of the line
    too (:func:`_can_connect`). So the ask never says "connect it yourself"
    where the line shows no Connect. Last, an ask that no admin can answer
    (:func:`_admin_can_meet`) stores no row: it would take a slot and show
    the admins an item that no connect clears."""
    unmet = [whose for n, whose, scopes in needs
             if n == name and not _need_met(n, whose, scopes, met)]
    if not unmet:
        raise _refused(409, ALREADY_MET, ALREADY_MET_TEXT)
    if any(_can_connect(name, whose, user) for whose in unmet):
        raise _refused(409, CAN_CONNECT, CAN_CONNECT_TEXT)
    if not any(_admin_can_meet(name, whose) for whose in unmet):
        raise _refused(409, ADMIN_CANNOT_CONNECT, ADMIN_CANNOT_CONNECT_TEXT)


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
        needs = await _owned_needs(db, user, pid)
        _check_name(name, needs)
        _check_member(name, needs, await _met(db, user), user)
        try:
            new = await attention.ask(db, str(user.organization_id), user.email, name, pid)
        except attention.TooManyAsks:
            raise _refused(429, TOO_MANY_ASKS, TOO_MANY_ASKS_TEXT) from None
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
        await _owned_needs(db, user, pid)
        gone = await attention.withdraw(db, str(user.organization_id), user.email, name)
        if gone:
            await record_app_audit_in(db, app_id=pid, user_email=user.email, kind="action",
                                      detail={"op": "connection_withdraw", "name": name})
    if gone:
        await _bump(user.organization_id)
    return {"asked": False, "withdrawn": gone}
