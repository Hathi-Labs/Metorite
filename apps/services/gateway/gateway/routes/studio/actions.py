"""``GET /studio/actions`` — the action catalog (STU-B1, ``ai_studio.md`` §3.2).

A read. No action runs here. Four sources feed it:

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

**The gate of every Studio route (STU-J1, ``ai_studio.md`` §9).** The router
below carries ``require_feature_router("studio")``. Each module of this
package registers its routes on this one router, so a new route has the gate
by construction. A caller without ``feature:studio`` gets 403 before a
handler runs, so the 403 comes before the 404 of ``STUDIO_ORGS``. A member,
a manager and an admin hold the feature through ``feature:*``, and the owner
through ``*``. A guest and a member with a deny override do not. No route
adds a second feature gate. One handler reads the share verb of J2: the org
and group install of WS-PKG PKG-8b (``packages.install_package``), in its
body and not as a gate. Fence: ``tests/unit/test_studio_route_gate.py``
(``EXPECTED_ROUTES``) and ``tests/unit/test_studio_route_gate_r8.py``.
"""

from __future__ import annotations

import re
import uuid
from typing import Annotated, Any

from acb_auth import UserContext, get_current_user, require_feature_router
from acb_common.app_manifests import door_ops, every_app
from acb_common.studio_fields import NAME_PATTERN
from acb_common.studio_manifest import OBJECT_OP_CLASSES, OBJECT_OPS, parse_uses
from fastapi import APIRouter, Depends, HTTPException, Query
from gateway.db import tenant_session as _tenant_session
from gateway.routes.agent_apps import args_schema
from gateway.routes.studio import flags
from gateway.routes.workflows.engine.tool_args import parse_args_schema
from gateway.routes.workflows.tools import list_tools
from gateway.studio import packages_store
from sqlalchemy import text

router = APIRouter(
    prefix="/studio",
    tags=["studio"],
    dependencies=[require_feature_router("studio")],
)

NOT_FOUND = "Not found"

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
            rows.append({
                "id": f"{app.id}.{name}",
                "title": op.route.title,
                "source": "manifest",
                "class": op.route.cls,
                "input_schema": args_schema(app.id, name),
                "output_schema": None,
                "connection": None,
                "app": app.id,
            })
    return rows


def _integration_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for spec in list_tools():
        if not spec.integration:
            continue
        args = parse_args_schema(spec.args_schema)
        rows.append({
            "id": f"integration:{spec.action}",
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
        })
    return rows


async def _workflow_rows() -> list[dict[str, Any]]:
    """Published workflows. RLS binds the tenant, so the query has no org."""
    async with _tenant_session() as db:
        found = (await db.execute(_WORKFLOWS_SQL)).fetchall()
    rows: list[dict[str, Any]] = []
    for row in found:
        rows.append({
            "id": f"workflow:{row.id}",
            "title": row.name,
            "source": "workflow",
            "class": "B",
            "input_schema": None,
            "output_schema": None,
            "connection": None,
            "app": None,
        })
    return rows


async def catalog_rows() -> list[dict[str, Any]]:
    """Every row of the catalog: the door ops, the integration tools, and the
    published workflows of the bound org. The one source of the catalog.
    ``routes/studio/skills.py`` checks a skill's actions against it.

    ⚠️ It opens its own ``_tenant_session()`` for the workflows. A caller runs
    it BEFORE it opens a session of its own, so no request holds two."""
    return [*_manifest_rows(), *_integration_rows(), *await _workflow_rows()]


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
    return {"actions": [*base, *mine]}
