"""The MCP source of the action catalog (STU-B1b-1, ``ai_studio.md`` §3.2 source 3).

Each tool of an MCP server of the org becomes one catalog row with the id
``mcp:<server>.<tool>`` (``studio_manifest.parse_uses``, source ``mcp``).

**Pure.** This module calls no server, keeps no cache and opens no session.
The CALLER gives it two things:

* ``servers``: the servers of ONE org. The caller reads them with
  ``gateway.connections.mcp.enabled_meta(org)``, for the bound org, and never
  from request input (R5). That read opens no header blob and refreshes no
  token. A tools list for a server outside this list gives no row, so a
  wrong key in ``tools`` cannot reach another org.
* ``tools``: the ``tools/list`` answer of each server, keyed by the server
  name. B1b-1 has no client, so the catalog passes an empty map and serves
  no ``mcp`` row. B1b-2 adds the read and its cache.

**The one class rule** (:func:`mcp_tool_class`). A tool is class A when
``acb_skills.permission_policy.mcp_needs_confirmation`` says that it needs no
confirmation AND its server has no ``always_confirm``. Each other tool is
class B. The gate of a run (``orchestrator._maf_mcp``) calls the same seam
on the same hints, so the catalog and the gate agree. A ``destructiveHint``
tool is a write, so it is B, and no MCP tool is class C. ⚠️ The admin's
read-only key (``read_only_key``) lets a run skip the card, but it never
makes a catalog row class A. The class of a row comes from the hints and
"always confirm" only, so the catalog can be stricter than the gate and
never looser.

**Data only.** A row holds the server key, the tool name, the description
and the hints, and nothing else of the server: no url, no header, no scope.
The server writes the name, the description and the hints, and a hostile
server can write anything there. So the description is a capped string that
nothing reads as an instruction, a colour or a size. A hint counts only as
a real boolean. A tool name that the ``uses`` grammar refuses gives no row.

**A duplicate name takes the stricter class** (B1b-2, :func:`unique_tools`).
A hostile server can list one name twice, first as read-only and then as a
write. The item that needs the confirmation wins, wherever it sits in the
list. Two items of one class keep the first. The cache of
``gateway.connections.mcp_tools`` and :func:`mcp_rows` call the same rule.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Protocol

from acb_common.studio_manifest import parse_uses
from acb_skills.permission_policy import MCP_HINT_NAMES, mcp_needs_confirmation
from acb_skills.skill_packs import action_tool

#: The source name of a row, and of ``UsesRef.source``.
MCP_SOURCE = "mcp"
#: The longest description that a row holds. A longer one is cut.
DESCRIPTION_CHARS = 1000


class Server(Protocol):
    """The fields of ``connections.mcp.StoredServer`` that a row reads."""

    name: str
    enabled: bool
    always_confirm: bool


def tool_hints(annotations: Any) -> dict[str, bool] | None:
    """The MCP hints of one tool, or None when it sent none.

    Only a real boolean of a name in ``MCP_HINT_NAMES`` is kept. So a hint of
    ``"true"`` counts as missing, as it does for the gate."""
    if not isinstance(annotations, Mapping):
        return None
    hints = {n: annotations[n] for n in MCP_HINT_NAMES if isinstance(annotations.get(n), bool)}
    return hints or None


def mcp_tool_class(hints: Any, *, always_confirm: bool) -> str:
    """The ONE class rule of an MCP tool: ``A`` or ``B``.

    ``A`` only when the server has no "always confirm" and the live seam
    ``mcp_needs_confirmation`` says the tool needs no confirmation. Each
    other case, a missing hint and a damaged value too, is ``B``."""
    if always_confirm is not False or mcp_needs_confirmation(hints):
        return "B"
    return "A"


def action_id(server: str, tool: str) -> str | None:
    """The id ``mcp:<server>.<tool>``, or None when the grammar refuses it.

    The id must parse back to the same server and the same tool, so no name
    can make an id of another source or of another server."""
    if not isinstance(server, str) or not isinstance(tool, str):
        return None
    uses = f"{MCP_SOURCE}:{server}.{tool}"
    ref = parse_uses(uses)
    if ref is None or ref.source != MCP_SOURCE or ref.server != server or ref.tool != tool:
        return None
    return uses


def _description(raw: Any) -> str:
    return raw[:DESCRIPTION_CHARS] if isinstance(raw, str) else ""


def _needs_card(item: Mapping[str, Any]) -> bool:
    return mcp_needs_confirmation(tool_hints(item.get("annotations")))


def unique_tools(items: Iterable[Any]) -> list[Mapping[str, Any]]:
    """The items of one ``tools/list``, one for each name, in the first order.

    An item that is not a mapping, or has no text name, is left out. When two
    items of one name disagree, the item that needs the confirmation (class
    B) takes the place of the other. So a later read-only copy can never make
    a write look like a read."""
    kept: dict[str, Mapping[str, Any]] = {}
    for item in items:
        if not isinstance(item, Mapping):
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name:
            continue
        old = kept.get(name)
        if old is None or (_needs_card(item) and not _needs_card(old)):
            kept[name] = item
    return list(kept.values())


def mcp_rows(servers: Iterable[Server],
             tools: Mapping[str, Sequence[Mapping[str, Any]]]) -> list[dict[str, Any]]:
    """The catalog rows of the MCP tools of ONE org.

    *servers* is the server list of that org, from the caller (see the module
    text). *tools* maps a server name to its ``tools/list`` items. A server
    that is not enabled, a server name that is not in *servers*, an item that
    is not a mapping, and a name that the grammar refuses give no row. Two
    items of one name give one row, with the stricter class
    (:func:`unique_tools`). The order is the server name, then the order of
    the list."""
    rows: list[dict[str, Any]] = []
    for server in sorted(servers, key=lambda s: str(s.name)):
        if server.enabled is not True:
            continue
        for item in unique_tools(tools.get(server.name) or ()):
            name = item.get("name")
            uses = action_id(server.name, name) if isinstance(name, str) else None
            if uses is None:
                continue
            hints = tool_hints(item.get("annotations"))
            rows.append({
                "id": uses,
                "title": name,
                "source": MCP_SOURCE,
                "class": mcp_tool_class(hints, always_confirm=server.always_confirm),
                "input_schema": None,
                "output_schema": None,
                "connection": f"{MCP_SOURCE}:{server.name}",
                "app": None,
                # The ONE map from an action to a chat tool. It gives None today.
                "tool": action_tool(uses),
                "server": server.name,
                "mcp_tool": name,
                "description": _description(item.get("description")),
                "hints": hints,
            })
    return rows
