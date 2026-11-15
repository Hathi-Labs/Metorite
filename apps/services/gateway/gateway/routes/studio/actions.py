"""``GET /studio/actions`` — the action catalog (STU-B1, ``ai_studio.md`` §3.2).

A read. No action runs here. These sources feed it:

1. ``manifest``: each door op of each app in ``app_manifests.every_app()``.
   A row with no ``op`` never appears, so class X rows stay off.
2. ``integration``: each workflow tool that names an integration.
3. ``workflow``: each published workflow of the caller's org.

**The class mapping (a proposal, ``ai_studio.md`` §3.2).**

* A manifest row keeps ``route.cls``.
* An integration tool: ``read_only`` is A. ``destructive`` or ``open_world``
  is C. Every other tool is B.
* A published workflow is B. Its ``input_schema`` is null until a workflow
  declares a trigger input (``workflows.variables`` are start values).

5. ``logic`` (STU-B4a): each first-party logic module of
   ``gateway.studio.logic.MODULES``, as ``logic:<name>``, class B. A row shows
   only for an org on ``STUDIO_LOGIC_ORGS``. The registry ships empty, so the
   source gives no row until a module registers.

6. ``mcp`` (STU-B1b-1): each tool of an MCP server of the org, as
   ``mcp:<server>.<tool>``, class A or B (``gateway.studio.mcp_actions``).
   B1b-2 reads the rows from the cache of ``gateway.connections.mcp_tools``
   only, so the catalog NEVER calls a server. A cold cache gives no row,
   and the GET answers ``stale: true``. ``POST /studio/mcp/{server}/refresh``
   is the one member action that reads a server and fills the cache.
   B1b-3: a tool whose text an admin has not approved is ``changed: true``
   and class B. ``POST /studio/mcp/{server}/tools/{tool}/approve`` is the
   admin action that approves the text of the fresh cached list. It needs
   ``integrations:manage``, the right that connects and edits an MCP server.

Source 2 is empty until a tool registers with an integration. Today the
registry holds only tools with ``integration=None``.

4. ``package`` (STU-B1c): the object actions and named actions of the
   caller's own packages. ``package_rows`` builds them for this route only.
   ``catalog_rows`` never holds one, because two packages can both hold
   ``object:items.list``, and the callers of ``catalog_rows`` key on the id.

A manifest row takes ``input_schema`` from ``agent_apps.args_schema`` (AD-9).

No row is class X. ``output_schema`` is null for every row until a later
slice defines it. ``connection`` names the integration, or is null. The
integration probe (``build_integrations``) is never called, so the catalog
reports no availability.

**The chat tool of each row (STU-F6a, ``ai_studio.md`` §14 F6a).** ``tool``
is ``acb_skills.skill_packs.action_tool`` of the row id, or null. That is the
ONE map from an action to a chat tool, and no route keeps a second one. The
skill offer of the chat maps each call of a turn to a row through it.
Fence: ``tests/unit/test_studio_catalog.py``.

**The gate of every Studio route (STU-J1, ``ai_studio.md`` §9).** The router
below carries ``require_feature_router("studio")``. Each module of this
package registers its routes on this one router, so a new route has the gate
by construction. A caller without ``feature:studio`` gets 403 before a
handler runs, so the 403 comes before the 404 of ``STUDIO_ORGS``. A member,
a manager and an admin hold the feature through ``feature:*``, and the owner
through ``*``. A guest and a member with a deny override do not. No route
adds a second feature gate. Two handlers read the share verb of J2: the
org and group install of WS-PKG PKG-8b (``packages.install_package``) and
its uninstall of PKG-8e (``packages.uninstall_package``), in their bodies and
not as a gate. Fence: ``tests/unit/test_studio_route_gate.py``
(``EXPECTED_ROUTES``) and ``tests/unit/test_studio_route_gate_r8.py``.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Annotated, Any

from acb_auth import UserContext, get_current_user, require_feature_router
from acb_common import get_logger
from acb_common.app_manifests import door_ops, every_app
from acb_common.db import current_tenant
from acb_common.studio_fields import NAME_PATTERN
from acb_common.studio_manifest import OBJECT_OP_CLASSES, OBJECT_OPS, parse_uses
from acb_skills.skill_packs import action_tool
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from gateway.connections import mcp_tools
from gateway.db import tenant_session as _tenant_session
from gateway.routes import integrations as _integrations
from gateway.routes.agent_apps import args_schema
from gateway.routes.studio import flags
from gateway.routes.workflows.engine.tool_args import parse_args_schema
from gateway.routes.workflows.tools import list_tools
from gateway.studio import logic as _logic
from gateway.studio import packages_store
from sqlalchemy import text

router = APIRouter(
    prefix="/studio",
    tags=["studio"],
    dependencies=[require_feature_router("studio")],
)

NOT_FOUND = "Not found"

_log = get_logger("studio.actions")

_WORKFLOWS_SQL = text(
    "SELECT id, name FROM workflows "
    "WHERE status = 'published' ORDER BY name, id"
)


def tool_class(read_only: bool, destructive: bool, open_world: bool) -> str:
    """The class of an integration tool. ``destructive`` and ``open_world``
    win over ``read_only``, so a tool that flags both is C."""
    if destructive or open_world:
        return "C"
    return "A" if read_only else "B"


def _manifest_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for app in every_app():
        if app.door != "ops":
            continue
        for name, op in door_ops(app).items():
            action_id = f"{app.id}.{name}"
            rows.append({
                "id": action_id,
                "title": op.route.title,
                "source": "manifest",
                "class": op.route.cls,
                "input_schema": args_schema(app.id, name),
                "output_schema": None,
                "connection": None,
                "app": app.id,
                "tool": action_tool(action_id),
            })
    return rows


def _integration_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for spec in list_tools():
        if not spec.integration:
            continue
        args = parse_args_schema(spec.args_schema)
        action_id = f"integration:{spec.action}"
        rows.append({
            "id": action_id,
            "title": spec.label,
            "source": "integration",
            "class": tool_class(spec.read_only, spec.destructive, spec.open_world),
            "input_schema": {
                "type": "object",
                "properties": {
                    a.name: {"type": a.type, "description": a.description} for a in args
                },
                "required": [a.name for a in args if a.required],
            },
            "output_schema": None,
            "connection": spec.integration,
            "app": None,
            "tool": action_tool(action_id),
        })
    return rows


async def _workflow_rows() -> list[dict[str, Any]]:
    """Published workflows. RLS binds the tenant, so the query has no org."""
    async with _tenant_session() as db:
        found = (await db.execute(_WORKFLOWS_SQL)).fetchall()
    rows: list[dict[str, Any]] = []
    for row in found:
        action_id = f"workflow:{row.id}"
        rows.append({
            "id": action_id,
            "title": row.name,
            "source": "workflow",
            "class": "B",
            "input_schema": None,
            "output_schema": None,
            "connection": None,
            "app": None,
            "tool": action_tool(action_id),
        })
    return rows


def _logic_rows() -> list[dict[str, Any]]:
    """The logic modules of the registry (B4a), for an org on ``STUDIO_LOGIC_ORGS``.
    The org is the bound tenant. A dark org, and an empty registry, give no row."""
    if not flags.logic_allowed(current_tenant()):
        return []
    return [{
        "id": f"logic:{name}",
        "title": module.title or name,
        "source": "logic",
        "class": _logic.LOGIC_CLASS,
        "input_schema": module.input_model.model_json_schema(),
        "output_schema": None,
        "connection": None,
        "app": None,
    } for name, module in sorted(_logic.MODULES.items())]


async def _mcp_rows() -> tuple[list[dict[str, Any]], bool]:
    """The MCP tools of the bound org from the cache (B1b-2), and ``stale``.

    ``mcp_tools.catalog_tools`` reads the cache and the server list of the
    org (``enabled_meta``). It NEVER calls a server. With no bound org it
    gives no row. The rows come from ``mcp_actions.mcp_rows``."""
    org = current_tenant()
    if not org:
        return [], True
    return await mcp_tools.catalog_tools(str(org))


class CatalogRows(list):
    """The rows of :func:`catalog_rows`, a plain list with one more field.

    ``stale`` is the answer of the MCP source (B1b-2): true when the cache may
    not hold every MCP tool of the org. A caller that keys on the rows reads
    a list as before."""

    stale: bool = True


async def catalog_rows() -> CatalogRows:
    """Every row of the catalog: the door ops, the integration tools, the
    published workflows of the bound org, the logic modules (B4a) and the MCP
    tools (B1b-1, from the cache of B1b-2). The one source of the catalog.
    ``routes/studio/skills.py`` checks a skill's actions against it.

    ⚠️ It opens its own ``_tenant_session()`` for the workflows. A caller runs
    it BEFORE it opens a session of its own, so no request holds two. The MCP
    read opens a session only when the cache of the org names a server."""
    mcp, stale = await _mcp_rows()
    rows = CatalogRows([*_manifest_rows(), *_integration_rows(), *await _workflow_rows(),
                        *_logic_rows(), *mcp])
    rows.stale = stale
    return rows


#: A stored object name that A2 would refuse gives no row. Only a write past the
#: store can make one, and a row id must stay a short, plain name.
_OBJECT_NAME = re.compile(NAME_PATTERN)


def _object_row(package_id: str, name: str, op: str) -> dict[str, Any]:
    uses = f"object:{name}.{op}"
    return {
        "id": uses,
        "title": f"{op.capitalize()} {name}",
        "source": "package",
        "class": OBJECT_OP_CLASSES[op],
        "input_schema": None,
        "output_schema": None,
        "connection": None,
        "app": package_id,
        "tool": action_tool(uses),
    }


def _named_row(package_id: str, found: tuple[str, str, str | None],
               catalog: dict[str, str]) -> dict[str, Any] | None:
    """The row of a named action, or None when its target is class X or the
    catalog does not serve it. An object target takes its class from
    ``OBJECT_OP_CLASSES``. Each other target takes the class of the catalog."""
    action_id, uses, title = found
    ref = parse_uses(uses)
    if ref is None:
        return None
    cls = OBJECT_OP_CLASSES.get(str(ref.op)) if ref.source == "object" else catalog.get(uses)
    if cls not in ("A", "B", "C"):
        return None
    return {
        "id": action_id,
        "uses": uses,
        "title": title or action_id,
        "source": "package",
        "class": cls,
        "input_schema": None,
        "output_schema": None,
        "connection": None,
        "app": package_id,
        # A named action's id is local to its package, so it maps no chat tool.
        "tool": action_tool(action_id),
    }


async def package_rows(
    member: str, catalog: dict[str, str], *, app: str | None = None
) -> list[dict[str, Any]]:
    """The rows of the packages that *member* owns in the bound org (B1c).
    *catalog* maps an id to its class, from ``catalog_rows``. One statement
    reads every package. ``routes/studio/actions.py`` is the only caller.

    ⚠️ It opens its own ``_tenant_session()``. The caller runs it after
    ``catalog_rows`` has closed its session."""
    async with _tenant_session() as db:
        found = await packages_store.list_package_actions(db, member, package_id=app)
    rows: list[dict[str, Any]] = []
    for package in found:
        for name in package.objects:
            if not _OBJECT_NAME.fullmatch(name):
                continue
            rows.extend(_object_row(package.id, name, op) for op in OBJECT_OPS)
        for named in package.actions:
            row = _named_row(package.id, named, catalog)
            if row is not None:
                rows.append(row)
    return rows


@router.get("/actions")
async def list_actions(
    user: UserContext = Depends(get_current_user),
    app: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    """The catalog. 404 while ``STUDIO_ORGS`` does not hold the caller's org.

    The rows of the caller's own packages come with it (B1c). ``?app=<uuid>``
    keeps only the rows whose ``app`` is that package."""
    if not flags.studio_allowed(getattr(user, "organization_id", None)):
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    only: str | None = None
    if app is not None:
        try:
            only = str(uuid.UUID(app))
        except ValueError:
            raise HTTPException(status_code=422, detail="app must be a UUID") from None
    base = await catalog_rows()
    mine = await package_rows(
        user.email, {str(r["id"]): str(r["class"]) for r in base}, app=only)
    if only is not None:
        return {"actions": mine}
    # B1b-2: ``stale`` is true when the cache may not hold every MCP tool.
    return {"actions": [*base, *mine], "stale": bool(getattr(base, "stale", True))}


#: The words of a failed refresh. They never hold the text of an exception.
_REFRESH_FAILED = "The server did not give its list of tools. Try again later."
_REFRESH_SOON = "This server was read a moment ago. Try again in {s} seconds."
_REFRESH_DOWN = "The tool list cannot be read now. Try again later."
_REFRESH_REFUSED = (
    "You cannot refresh this server: it is not a connected server of your organization, "
    "or you may not use it.")


@router.post("/mcp/{server}/refresh")
async def refresh_mcp_tools(
    server: str,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Read the ``tools/list`` of ONE MCP server of the caller's org (B1b-2).

    The one member action that calls a server. It fills the cache that the
    catalog reads. The org is the bound session's, never request input, so
    a server of another org is 403 ``mcp_not_allowed``, as a server that the
    member may not use (the grant ``integrations:use:mcp-<server>`` or the
    agent scope) is. A bad key is 404. The answer holds the server key, the
    count and the result, and never a url, a header or an error text."""
    org = getattr(user, "organization_id", None)
    if not flags.studio_allowed(org):
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    try:
        done = await mcp_tools.refresh(str(org), server, may_use=user.has_permission)
    except mcp_tools.ServerNotFound:
        raise HTTPException(status_code=404, detail=NOT_FOUND) from None
    except mcp_tools.RefreshRefused:
        # I11a review: no grant, no agent scope and no such server give one
        # answer, so the answer does not say that a server exists.
        raise HTTPException(status_code=403, detail={
            "code": "mcp_not_allowed", "message": _REFRESH_REFUSED}) from None
    except mcp_tools.RefreshTooSoon as soon:
        raise HTTPException(
            status_code=429, detail=_REFRESH_SOON.format(s=soon.retry_after),
            headers={"Retry-After": str(soon.retry_after)}) from None
    except Exception as exc:
        _log.warning("studio.mcp_refresh_unavailable", kind=type(exc).__name__)
        raise HTTPException(status_code=503, detail=_REFRESH_DOWN) from None
    if not done.ok:
        raise HTTPException(status_code=502, detail={
            "code": f"mcp_{done.reason}", "message": _REFRESH_FAILED, "server": done.server})
    return {"server": done.server, "ok": True, "tools": done.tools,
            "truncated": done.truncated, "items": list(done.items)}


#: The words of an approval. They never hold the text of the tool.
_APPROVE_REFUSED = (
    "You cannot approve a tool. Approval needs the right to manage the connections "
    "of your organization.")
_APPROVE_STALE = (
    "The tool list of this server is not fresh. Refresh the server, then approve the tool.")
_APPROVE_SOON = "This tool was approved a moment ago. Try again in {s} seconds."
_APPROVE_DOWN = "The approval cannot be saved now. Try again later."
_APPROVE_NO_TOOL = "The tool list of this server holds no tool of that name."
_APPROVE_TEXT_CHANGED = (
    "The text of this tool changed after you read it. Read the catalog again, check the "
    "tool, then approve it.")
_APPROVE_BAD_BODY = (
    'Send {"expected_hash": "<text_hash>"}: the 64 hex characters of the catalog row '
    "that you read.")
#: The most bytes of the body of an approval.
APPROVE_BODY_BYTES = 4096
_HASH_RE = re.compile(r"[0-9a-f]{64}")


async def _expected_hash(request: Request) -> str:
    """``expected_hash`` of the JSON body, or a 422. The read stops past
    :data:`APPROVE_BODY_BYTES`. The hash is no secret."""
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > APPROVE_BODY_BYTES:
            break
        chunks.append(chunk)
    bad = HTTPException(status_code=422, detail={
        "code": "mcp_expected_hash", "message": _APPROVE_BAD_BODY})
    if size > APPROVE_BODY_BYTES:
        raise bad
    try:
        body = json.loads(b"".join(chunks) or b"null")
    except (ValueError, RecursionError):
        raise bad from None
    found = body.get("expected_hash") if isinstance(body, dict) else None
    if not isinstance(found, str) or not _HASH_RE.fullmatch(found):
        raise bad
    return found


@router.post("/mcp/{server}/tools/{tool}/approve")
async def approve_mcp_tool(
    server: str,
    tool: str,
    request: Request,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Approve the CURRENT text of ONE tool of an MCP server of the caller's
    org (B1b-3).

    The right is ``integrations:manage``, the right of the routes that
    connect and edit an MCP server (``routes/integrations.py``). It is a
    member write, not a role write. The grant ``integrations:use:mcp-<server>``
    alone is not enough. The org and the member come from the session. The
    approval takes the hash of the tool in the cached list, which must be
    fresh and of the server of now (409 ``mcp_list_stale`` else). A server
    that the org does not hold is 404 and writes nothing. One audit event
    names the server, the tool and the hash, and no text of the tool.

    **Compare and approve** (B1b-3 review). The body is
    ``{"expected_hash": "<64 hex>"}``, the ``text_hash`` of the catalog row
    that the admin read (422 ``mcp_expected_hash`` without it). A tool whose
    text hashes another way now is 409 ``mcp_tool_text_changed``, and
    nothing is written."""
    org = getattr(user, "organization_id", None)
    if not flags.studio_allowed(org):
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    if not user.has_permission(_integrations.MANAGE_PERMISSION):
        raise HTTPException(status_code=403, detail={
            "code": "mcp_approve_not_allowed", "message": _APPROVE_REFUSED})
    expected = await _expected_hash(request)
    try:
        done = await mcp_tools.approve_tool(str(org), server, tool, member=str(user.email),
                                            expected_hash=expected)
    except mcp_tools.ServerNotFound:
        raise HTTPException(status_code=404, detail=NOT_FOUND) from None
    except mcp_tools.ToolNotListed:
        raise HTTPException(status_code=404, detail={
            "code": "mcp_tool_unknown", "message": _APPROVE_NO_TOOL}) from None
    except mcp_tools.TextChanged:
        raise HTTPException(status_code=409, detail={
            "code": "mcp_tool_text_changed", "message": _APPROVE_TEXT_CHANGED}) from None
    except mcp_tools.ListNotFresh:
        raise HTTPException(status_code=409, detail={
            "code": "mcp_list_stale", "message": _APPROVE_STALE}) from None
    except mcp_tools.RefreshTooSoon as soon:
        raise HTTPException(
            status_code=429, detail=_APPROVE_SOON.format(s=soon.retry_after),
            headers={"Retry-After": str(soon.retry_after)}) from None
    except Exception as exc:
        _log.warning("studio.mcp_approve_unavailable", kind=type(exc).__name__)
        raise HTTPException(status_code=503, detail=_APPROVE_DOWN) from None
    _integrations.audit_integration(
        user, str(org), f"mcp:{done.server}", action="integration.mcp.tool_approved",
        tool=done.tool, text_hash=done.text_hash)
    return {"server": done.server, "tool": done.tool, "changed": False, "class": done.cls}
