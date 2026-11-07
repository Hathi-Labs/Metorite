"""MCP resources of the edge (SW-34a).

Spec ``project-docs/specs/agent_coordination.md`` SW-34a and
``external_agent_access.md`` §9.3.

PB-3b (D186, ``orchestration_playbook.md`` §5) adds the playbook: one concrete
resource for each topic of ``acb_playbook``, ``metorite://playbook/{slug}``,
such as ``metorite://playbook/brief-contract`` or
``metorite://playbook/patterns/one-agent-first``. It makes no gateway request.
It needs ``projects:read`` and both flags: ``agent_resources_orgs`` for the
transport and ``playbook_orgs`` for the content. The body is the JSON of the
topic with its title, summary and text as ``{"untrusted_text": ...}``, the same
shape as the tool ``read_playbook``.

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

from acb_playbook import PlaybookError, load_all, load_topic

from agent_edge import tools

#: The JSON-RPC error of a resource that is not found (MCP, ``-32002``).
NOT_FOUND = -32002
TEXT_NOT_FOUND = "Resource not found"
TEXT_BAD_PARAMS = "Invalid params: uri must be a resource URI."

#: Hex in either case. Only the id is case-blind: the scheme and the kind are not.
_UUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
MIME = "application/json"

#: PB-3b. The playbook URI. The slug has the shape of a topic path, so a bad
#: shape never reaches the loader.
PLAYBOOK_PREFIX = "metorite://playbook/"
_PLAYBOOK_URI = re.compile(r"metorite://playbook/([a-z0-9]+(?:[/-][a-z0-9]+)*)", re.ASCII)
PLAYBOOK_SCOPE = agent_oauth.PROJECTS_READ


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


def _playbook_on(scopes: Iterable[str], organization_id: object) -> bool:
    """The playbook resources show: the transport flag, the content flag and
    the read scope. Each is checked from the token row, never from input."""
    return (offered(organization_id) and agent_oauth.playbook_allowed(organization_id)
            and PLAYBOOK_SCOPE in frozenset(scopes))


def listed(scopes: Iterable[str], organization_id: object) -> list[dict[str, Any]]:
    """``resources/list``. One row for each playbook topic while the playbook
    is on, and nothing else: no project and no task row, because a list of
    them would be one more gateway read. Empty, never an error, while dark."""
    if not _playbook_on(scopes, organization_id):
        return []
    try:
        topics = load_all()
    except PlaybookError:
        return []
    return [{
        "uri": f"{PLAYBOOK_PREFIX}{t.slug}", "name": t.slug, "title": t.title,
        "description": t.summary, "mimeType": MIME,
    } for t in topics]


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


def _playbook_match(uri: str) -> str | None:
    if len(uri) > 200:
        return None
    found = _PLAYBOOK_URI.fullmatch(uri)
    return found.group(1) if found else None


def _read_playbook(slug: str, scopes: Iterable[str], organization_id: object) -> dict[str, Any]:
    """A playbook topic. No gateway request. Not found while dark, for a bad
    scope and for an unknown topic: one answer, no echo."""
    if not _playbook_on(scopes, organization_id):
        raise ResourceNotFound(TEXT_NOT_FOUND)
    try:
        topic = load_topic(slug)
    except PlaybookError:
        topic = None
    if topic is None:
        raise ResourceNotFound(TEXT_NOT_FOUND)
    body = {"slug": topic.slug, "version": topic.version, "audience": topic.audience,
            "title": tools.untrusted(topic.title), "summary": tools.untrusted(topic.summary),
            "body": tools.untrusted(topic.body)}
    return {"contents": [{
        "uri": f"{PLAYBOOK_PREFIX}{topic.slug}", "mimeType": MIME,
        "text": json.dumps(body, ensure_ascii=False),
    }]}


async def read(ctx: tools.Context, params: Any) -> dict[str, Any]:
    """``resources/read``. Raise :class:`ResourceBadParams` or :class:`ResourceNotFound`.

    A gateway 401 raises :class:`tools.GatewayUnauthorized`, which the transport
    answers as the MCP 401, as it does for a tool call.
    """
    uri = params.get("uri") if isinstance(params, dict) else None
    if not isinstance(uri, str) or not uri:
        raise ResourceBadParams(TEXT_BAD_PARAMS)
    org = ctx.caller.lookup.organization_id
    playbook = _playbook_match(uri)
    if playbook is not None:
        return _read_playbook(playbook, ctx.caller.scopes, org)
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
