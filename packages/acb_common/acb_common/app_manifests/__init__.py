"""The app manifests: one table for each app, read by both doors (MCP-P1a, D136).

Spec: ``project-docs/specs/external_agent_access.md`` §19.3 and AD-1 to AD-3.

:mod:`acb_common.app_manifests.base` holds the shared types and readers.
Each app module holds one :class:`App`. This module holds the registry: the
apps of the door, in the order of ``list_apps``, and the readers that the
edge, branch 0 of ``acb_auth.deps`` and the gateway call.

* :data:`APPS` are the apps of the four door tools (``door = "ops"``).
* :data:`TOOL_APPS` are the apps whose door is their own typed tools.
  Projects is the one. Its manifest stays in ``skill_projects`` and its
  allowlist stays ``agent_oauth.MCP_ROUTES`` (§19.3). Only its header is
  here, for ``list_apps``.

Each reader reads :data:`APPS` when it runs, so a test can register a test
manifest with ``monkeypatch.setattr(app_manifests, "APPS", ...)``.
"""

from __future__ import annotations

from collections.abc import Mapping

from acb_common.app_manifests.base import (
    APP_ID_PATTERN,
    ARG_KEY_PATTERN,
    CLASSES,
    OP_PATTERN,
    UUID_PATTERN,
    VERSION_ARG,
    App,
    BadCall,
    Call,
    DoorOp,
    Page,
    Route,
    build_call,
    door_ops,
    path_params,
    route_for,
    scopes_of,
)
from acb_common.app_manifests.base import door_routes as _door_routes
from acb_common.app_manifests.knowledge import KNOWLEDGE

#: The Projects header (§19.1). Its door is the typed tools of §9.
PROJECTS = App(
    id="projects",
    name="Projects",
    reach="The projects and tasks that the member can see.",
    pane="/projects",
    feature="projects",
    router="gateway.routes.projects",
    door="tools",
)

#: The apps of the four door tools, in the order of ``list_apps``.
APPS: tuple[App, ...] = (KNOWLEDGE,)

#: The apps whose door is their own typed tools.
TOOL_APPS: tuple[App, ...] = (PROJECTS,)


def every_app() -> tuple[App, ...]:
    """Each app that ``list_apps`` may show: the tool apps, then the door apps."""
    return (*TOOL_APPS, *APPS)


def app(app_id: object) -> App | None:
    """The app with the id *app_id*, a door app or a tool app, or ``None``."""
    if not isinstance(app_id, str):
        return None
    for one in every_app():
        if one.id == app_id:
            return one
    return None


def door_app(app_id: object) -> App | None:
    """The door app with the id *app_id*, or ``None`` for any other value."""
    found = app(app_id)
    return found if found is not None and found.door == "ops" else None


def door_routes() -> Mapping[tuple[str, str], DoorOp]:
    """Each ``(method, template)`` of a door op of :data:`APPS` (AD-3)."""
    return _door_routes(APPS)


def door_route(pair: tuple[str, str | None]) -> DoorOp | None:
    """The door op of one ``(method, template)``, or ``None``."""
    method, template = pair
    if template is None:
        return None
    return door_routes().get((method, template))


def app_scopes() -> tuple[str, ...]:
    """The scopes of the door apps (§19.6), in the order of :data:`APPS`."""
    return scopes_of(APPS)


def door_op(app_id: object, op: object) -> DoorOp | None:
    """The door op *op* of the door app *app_id*, or ``None``."""
    found = door_app(app_id)
    if found is None or not isinstance(op, str):
        return None
    return door_ops(found).get(op)


__all__ = [
    "APPS",
    "APP_ID_PATTERN",
    "ARG_KEY_PATTERN",
    "CLASSES",
    "KNOWLEDGE",
    "OP_PATTERN",
    "PROJECTS",
    "TOOL_APPS",
    "UUID_PATTERN",
    "VERSION_ARG",
    "App",
    "BadCall",
    "Call",
    "DoorOp",
    "Page",
    "Route",
    "app",
    "app_scopes",
    "build_call",
    "door_app",
    "door_op",
    "door_ops",
    "door_route",
    "door_routes",
    "every_app",
    "path_params",
    "route_for",
]

