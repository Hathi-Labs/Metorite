"""``GET /studio/actions`` — the action catalog (STU-B1, ``ai_studio.md`` §3.2).

A read. No action runs here. Three sources feed it:

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

A manifest row takes ``input_schema`` from ``agent_apps.args_schema`` (AD-9).

No row is class X. ``output_schema`` is null for every row until a later
slice defines it. ``connection`` names the integration, or is null. The
integration probe (``build_integrations``) is never called, so the catalog
reports no availability.
"""

from __future__ import annotations

from typing import Any

from acb_auth import UserContext, get_current_user, require_feature_router
from acb_common.app_manifests import door_ops, every_app
from fastapi import APIRouter, Depends, HTTPException
from gateway.db import tenant_session as _tenant_session
from gateway.routes.agent_apps import args_schema
from gateway.routes.studio import flags
from gateway.routes.workflows.engine.tool_args import parse_args_schema
from gateway.routes.workflows.tools import list_tools
from sqlalchemy import text

router = APIRouter(
    prefix="/studio",
    tags=["studio"],
    dependencies=[require_feature_router("workflows")],
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


@router.get("/actions")
async def list_actions(
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """The catalog. 404 while ``STUDIO_ORGS`` does not hold the caller's org."""
    if not flags.studio_allowed(getattr(user, "organization_id", None)):
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    return {"actions": await catalog_rows()}
