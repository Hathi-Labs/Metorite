"""``/agent-access/apps`` — the gateway half of the app door (MCP-P1a, D136).

Spec ``project-docs/specs/external_agent_access.md`` §19.4 and AD-9.

The edge (``apps/services/agent_edge``) serves four tools to an outside
agent. Two of them read these routes, as the signed agent of branch 0:

* ``GET /agent-access/apps`` gives ``list_apps``: one row for each app that
  the door serves to this member.
* ``GET /agent-access/apps/{app}`` gives ``describe_app``: the header of one
  door app and its ops, each with an args schema that this module builds from
  the live route (AD-9).

``app_read`` and ``app_write`` reach the route of each op itself. Branch 0
admits that pair from the manifests (``acb_auth.agent_oauth.door_route``).

## The rules

1. **An agent only.** A caller with no ``UserContext.agent`` gets 404. Only a
   signed request of branch 0 sets it.
2. **Dark.** 404 while ``agent_access_enabled`` is off, and while
   ``agent_door_apps`` names no app (AD-15).
3. **No data of an app.** An answer holds the manifest, the args schemas
   and the state of the organization. The module opens no database session
   and adds no seam (R5).
4. **The member and the tenant come from the session (R11).** No input names
   them. ``{app}`` is an app id of the pattern of the manifests.
5. **Three kinds of app stay out** (§19.4): an app that the door flag does
   not name, an app whose feature the member does not hold, and, from
   MCP-P3, an app that the admin switched off.
6. **One rule of availability.** ``agent_oauth.door_available`` decides each
   op, on both sides of the door. A refused op gives a code, never a text.

Fences: ``tests/unit/test_mcp_edge_app_door.py`` and, on a real database,
``tests/unit/test_mcp_edge_app_door_r8.py``.
"""

from __future__ import annotations

import copy
import functools
import importlib
import json
from typing import Any

from acb_auth import UserContext, agent_oauth, get_current_user
from acb_auth.roles import AgentScope
from acb_common import app_manifests, get_logger
from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, ConfigDict, Field, create_model
from pydantic.fields import FieldInfo

_log = get_logger("gateway.agent_apps")

#: The largest args schema of one op, in bytes of JSON (AD-9). A larger one
#: keeps its top-level field names only, and says so.
MAX_SCHEMA_BYTES = 16 * 1024

#: The largest ``describe_app`` answer, in bytes of JSON (§19.7).
MAX_DESCRIBE_BYTES = 64 * 1024

#: The text of a cut schema.
CUT_NOTE = "The full schema is too large. Only the names of the fields show."


async def _require_door() -> None:
    """404 while agent access is off, or while the door serves no app."""
    if not agent_oauth.enabled() or not agent_oauth.door_apps():
        raise HTTPException(status_code=404, detail="Not Found")


router = APIRouter(
    prefix="/agent-access",
    tags=["agent-access"],
    dependencies=[Depends(_require_door)],
)


def _agent_of(user: UserContext) -> AgentScope:
    """The scope of the signed agent, or the 404 of rule 1."""
    agent = user.agent
    if agent is None:
        raise HTTPException(status_code=404, detail="Not Found")
    return agent


def _feature_held(user: UserContext, app: app_manifests.App) -> bool:
    return bool(user.has_permission(f"feature:{app.feature}"))


def _scopes_of(app: app_manifests.App) -> tuple[str, str | None]:
    """The read scope and the write scope of *app*. Projects keeps its two
    fixed scopes. A door app with no change op has no write scope."""
    if app.door == "tools":
        return agent_oauth.PROJECTS_READ, agent_oauth.PROJECTS_WRITE
    write = app.write_scope if app.write_scope in app.scopes else None
    return app.read_scope, write


@router.get("/apps")
async def list_apps(user: UserContext = Depends(get_current_user)) -> dict[str, Any]:
    """The apps that the door serves to this member (§19.4, ``list_apps``)."""
    agent = _agent_of(user)
    rows: list[dict[str, Any]] = []
    for app in app_manifests.every_app():
        if app.door == "ops" and not agent_oauth.door_serves(app.id):
            continue
        if not _feature_held(user, app):
            continue
        read, write = _scopes_of(app)
        rows.append({
            "id": app.id,
            "name": app.name,
            "reach": app.reach,
            "door": app.door,
            "read": agent_oauth.scope_holds(agent.scopes, read),
            "write": write is not None and agent_oauth.scope_holds(agent.scopes, write),
            "available": True,
            "why_not": None,
            # MCP-P1b fills it with the open requests of this connection.
            "waiting": [],
        })
    return {"apps": rows}


# ── The args schema (AD-9) ──────────────────────────────────────────────────


def _nested(dependant: Any, kind: str) -> list[Any]:
    """The parameters of *kind* of a route and of its dependencies."""
    out = list(getattr(dependant, kind))
    for sub in dependant.dependencies:
        out.extend(_nested(sub, kind))
    return out


def _route_of(app: app_manifests.App, door: app_manifests.DoorOp) -> Any | None:
    """The live route of a door op, from the router that the app names."""
    router_ = importlib.import_module(app.router).router
    for route in router_.routes:
        if (getattr(route, "path", None) == door.route.path
                and door.route.method in (getattr(route, "methods", None) or ())):
            return route
    return None


def _fields_of(route: Any, door: app_manifests.DoorOp) -> dict[str, tuple[Any, FieldInfo]]:
    """Each input of the op: the path and query parameters, the fields of the
    body model, and ``If-Match`` as ``expected_version``. The identity
    headers never show, and nor do the page keys that the edge sets."""
    page = door.route.page
    page_keys = {page.limit, page.cursor} if page is not None else set()
    dependant = route.dependant
    fields: dict[str, tuple[Any, FieldInfo]] = {}
    for param in dependant.path_params:
        fields[param.alias] = (param.field_info.annotation, param.field_info)
    for param in _nested(dependant, "query_params"):
        if param.alias not in page_keys:
            fields.setdefault(param.alias, (param.field_info.annotation, param.field_info))
    if any(h.alias.lower() == "if-match" for h in _nested(dependant, "header_params")):
        fields[app_manifests.VERSION_ARG] = (str | None, Field(
            None, max_length=40,
            description="The version that you read. Metorite sends it as If-Match."))
    for param in dependant.body_params:
        kind = param.field_info.annotation
        if isinstance(kind, type) and issubclass(kind, BaseModel):
            for name, field in kind.model_fields.items():
                fields[field.alias or name] = (field.annotation, field)
        else:
            fields[param.alias] = (kind, param.field_info)
    return fields


def _cut(schema: dict[str, Any]) -> dict[str, Any]:
    """*schema* with its top-level field names only (AD-9)."""
    names = list((schema.get("properties") or {}).keys())
    return {"type": "object", "properties": {n: {} for n in names},
            "additionalProperties": False, "description": CUT_NOTE}


def _size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


@functools.lru_cache(maxsize=256)
def _args_schema(app_id: str, op: str) -> dict[str, Any]:
    """The JSON Schema of the args of one op, from ``route.dependant`` (AD-9).

    Never a hand list. A route that the router does not serve gives an object
    with no fields, and the coverage fence fails on it first.
    """
    app = app_manifests.door_app(app_id)
    door = app_manifests.door_ops(app).get(op) if app is not None else None
    route = _route_of(app, door) if door is not None else None
    if route is None:
        return {"type": "object", "properties": {}, "additionalProperties": False}
    model = create_model(
        f"{app_id}_{op}_args", __config__=ConfigDict(extra="forbid"),
        **_fields_of(route, door))
    schema = model.model_json_schema()
    schema.pop("title", None)
    return _cut(schema) if _size(schema) > MAX_SCHEMA_BYTES else schema


def args_schema(app_id: str, op: str) -> dict[str, Any]:
    """A copy of the cached schema, so no caller can change the cache."""
    return copy.deepcopy(_args_schema(app_id, op))


@router.get("/apps/{app}")
async def describe_app(
    app: str = Path(..., pattern=f"^{app_manifests.APP_ID_PATTERN}$"),
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """One door app and its ops (§19.4, ``describe_app``).

    404 for an app that no manifest holds, that the door flag does not name,
    or whose feature the member does not hold. The answer holds no data of
    the app.
    """
    _agent_of(user)
    found = app_manifests.door_app(app)
    if found is None or not agent_oauth.door_serves(found.id) or not _feature_held(user, found):
        raise HTTPException(status_code=404, detail="Not Found")
    ops: list[dict[str, Any]] = []
    for door in app_manifests.door_ops(found).values():
        available, code = agent_oauth.door_available(door)
        ops.append({
            "op": door.name,
            "title": door.route.title,
            "class": door.route.cls,
            "kind": door.kind,
            "asks": door.asks,
            "sends": door.route.sends,
            "args": args_schema(found.id, door.name),
            "paged": door.route.page is not None,
            "available": available,
            "why_not": code,
        })
    out = {"id": found.id, "name": found.name, "reach": found.reach,
           "finish_in": found.finish_in or None, "ops": ops}
    if _size(out) > MAX_DESCRIBE_BYTES:
        for row in ops:
            row["args"] = _cut(row["args"])
        _log.warning("agent_apps.describe_cut", app=found.id)
    return out


__all__ = ["MAX_DESCRIBE_BYTES", "MAX_SCHEMA_BYTES", "args_schema", "router"]
