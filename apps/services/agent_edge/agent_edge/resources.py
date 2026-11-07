"""MCP resources of the edge (SW-34a).

Spec ``project-docs/specs/agent_coordination.md`` SW-34a and
``external_agent_access.md`` §9.3.

Two resources, each the same read as a tool: ``metorite://project/{id}`` is
``get_project`` and ``metorite://task/{id}`` is ``get_task``. So **a resource
read uses the authority rule of the tool call** (§9.3): the same gateway
routes, the same member, the same pins and the same visibility seam. This
module adds no gateway route and no scope. A client lists the two as
templates (``resources/templates/list``). ``resources/list`` holds no concrete
row, because a list of every project would be one more gateway read.

``metorite://company/context`` is not served. Its read, ``get_company_context``
(EA-5, D-EA-6), does not exist yet. The slice that adds the tool adds the
resource here.

**Fixed text and the validated id only.** An id passes only as a UUID. A body
leaves as the JSON of the tool result, so each free-text value is already
``{"untrusted_text": ...}`` (§9.4). A resource that the member cannot see, a
bad URI and an unknown URI all answer the same ``-32002`` "Resource not
found". The text never echoes the URI.

**Dark.** ``agent_resources_orgs`` is empty by default
(``agent_oauth.resources_allowed``, the organization of the token row). While
it does not name the organization, the list is empty and a read is not found.

Fence: ``tests/unit/test_mcp_edge_resources.py``.
"""
from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from acb_auth import agent_oauth

from agent_edge import tools

#: The JSON-RPC error of a resource that is not found (MCP, ``-32002``).
NOT_FOUND = -32002
TEXT_NOT_FOUND = "Resource not found"
TEXT_BAD_PARAMS = "Invalid params: uri must be a resource URI."

#: Hex in either case. Only the id is case-blind: the scheme and the kind are not.
_UUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
MIME = "application/json"


@dataclass(frozen=True)
class Resource:
    """One kind of resource: a URI template and the tool that reads it."""

    template: str
    name: str
    title: str
    description: str
    tool: tools.Tool
    #: The input of :attr:`tool` that the id fills.
    argument: str
    pattern: re.Pattern[str]


RESOURCES: tuple[Resource, ...] = (
    Resource(
        template="metorite://project/{id}",
        name="project",
        title="A project",
        description=("One project with its statuses, fields and tags. The same read"
                     " as the tool get_project."),
        tool=tools.GET_PROJECT, argument="project_id",
        pattern=re.compile(rf"metorite://project/({_UUID})", re.ASCII)),
    Resource(
        template="metorite://task/{id}",
        name="task",
        title="A task",
        description="One task with its references. The same read as the tool get_task.",
        tool=tools.GET_TASK, argument="task_id",
        pattern=re.compile(rf"metorite://task/({_UUID})", re.ASCII)),
)


class ResourceNotFound(Exception):
    """A read that gets ``-32002``. The text is fixed."""


class ResourceBadParams(Exception):
    """A read with no usable ``uri`` gets ``-32602``. The text is fixed."""


def offered(organization_id: object) -> bool:
    return agent_oauth.resources_allowed(organization_id)


def _visible(resource: Resource, scopes: Iterable[str]) -> bool:
    """The scope of the tool that the resource reads, and the tool's own flags."""
    held = frozenset(scopes)
    tool = resource.tool
    return (tool.required_scope is None or tool.required_scope in held) and tools.offered(tool)


def listed(scopes: Iterable[str], organization_id: object) -> list[dict[str, Any]]:
    """``resources/list``. Always empty today: no concrete row (see the module doc)."""
    return []


def templates(scopes: Iterable[str], organization_id: object) -> list[dict[str, Any]]:
    """``resources/templates/list``. Empty while dark, never an error."""
    if not offered(organization_id):
        return []
    return [{
        "uriTemplate": r.template, "name": r.name, "title": r.title,
        "description": r.description, "mimeType": MIME,
    } for r in RESOURCES if _visible(r, scopes)]


def _match(uri: Any) -> tuple[Resource, str] | None:
    if not isinstance(uri, str) or len(uri) > 200:
        return None
    for resource in RESOURCES:
        found = resource.pattern.fullmatch(uri)
        if found:
            return resource, found.group(1).lower()
    return None


async def read(ctx: tools.Context, params: Any) -> dict[str, Any]:
    """``resources/read``. Raise :class:`ResourceBadParams` or :class:`ResourceNotFound`.

    A gateway 401 raises :class:`tools.GatewayUnauthorized`, which the transport
    answers as the MCP 401, as it does for a tool call.
    """
    uri = params.get("uri") if isinstance(params, dict) else None
    if not isinstance(uri, str) or not uri:
        raise ResourceBadParams(TEXT_BAD_PARAMS)
    org = ctx.caller.lookup.organization_id
    found = _match(uri)
    if found is None or not offered(org):
        raise ResourceNotFound(TEXT_NOT_FOUND)
    resource, ident = found
    if not _visible(resource, ctx.caller.scopes):
        raise ResourceNotFound(TEXT_NOT_FOUND)
    try:
        body = await resource.tool.run(ctx, {resource.argument: ident})
    except tools.ToolError:
        # Not visible, not found, busy: one answer, so a resource never
        # tells a member that a record of another member exists.
        raise ResourceNotFound(TEXT_NOT_FOUND) from None
    return {"contents": [{
        "uri": f"metorite://{resource.name}/{ident}",
        "mimeType": MIME,
        "text": json.dumps(body, ensure_ascii=False),
    }]}


__all__ = ["NOT_FOUND", "RESOURCES", "Resource", "ResourceBadParams", "ResourceNotFound",
           "listed", "offered", "read", "templates"]
