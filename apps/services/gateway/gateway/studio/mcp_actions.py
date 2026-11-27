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
class B. A ``destructiveHint`` tool is a write, so it is B, and no MCP tool
is class C. The class is a read test, and the preview reads class A only, so
a preview sends nothing (I11). ⚠️ MCP-NC1 (owner directive, 2026-11-16, task
3603): the gate of a verified member's run in Metorite
(``orchestrator._maf_mcp``) runs a class B tool with no card, unless its
server is set to always confirm. WhatsApp still refuses it. The admin's
read-only key (``read_only_key``) lets such a refused run use the tool, but
it never makes a catalog row class A. The class of a row comes from the
hints and "always confirm" only, so the catalog can be stricter than the gate
and never looser.

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

**A changed tool is class B** (B1b-3, :func:`tool_text_hash`). An admin of
the org approves the text of a tool: its name, its cleaned description and
its hints. ``gateway.studio.mcp_approvals`` keeps the SHA-256 of that text.
:func:`mcp_rows` takes the approved hashes of the org. A tool with no
approval, or with a text that hashes another way, gets ``changed: true``,
and its row is class B whatever its hints say. So a new tool, and a tool
that a server changed after the approval, never reads as class A. With no
approvals (``None``, the read failed) every tool is changed (fail closed).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Protocol

from acb_common.studio_manifest import parse_uses
from acb_skills.hidden_text import strip_hidden
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
    ``mcp_needs_confirmation`` says the hints show a read. Each other case,
    a missing hint and a damaged value too, is ``B``. MCP-NC1 leaves this
    rule as it was: a member's run calls a ``B`` tool with no card, and a
    preview still never calls one."""
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


def clean_description(raw: Any) -> str:
    """The description of a tool as clean, capped text: the ONE cleaning.

    ``strip_hidden`` runs before and after the cut, because a cut can leave
    a joiner at the end of the text, where it is hidden. The cache of
    ``connections.mcp_tools`` and :func:`tool_text_hash` both call it, so a
    cached description and a raw one hash the same."""
    if not isinstance(raw, str):
        return ""
    return strip_hidden(strip_hidden(raw)[:DESCRIPTION_CHARS])


def tool_text_hash(tool: Mapping[str, Any]) -> str:
    """The SHA-256 hex of the text of ONE tool that an admin approves (B1b-3).

    The text is the tool name, its description after
    :func:`clean_description`, and each hint of ``MCP_HINT_NAMES`` as a real
    boolean or null (:func:`tool_hints`). It is canonical JSON with sorted
    keys, so the order of the keys of a server never changes the hash. A
    hidden character and a lone surrogate are cleaned out first, so the
    hash of a cached tool equals the hash of the same raw tool."""
    item: Mapping[str, Any] = tool if isinstance(tool, Mapping) else {}
    name = item.get("name")
    hints = tool_hints(item.get("annotations")) or {}
    text = json.dumps({
        "name": name if isinstance(name, str) else "",
        "description": clean_description(item.get("description")),
        "hints": {n: hints.get(n) for n in MCP_HINT_NAMES},
    }, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(text.encode("ascii")).hexdigest()


def tool_changed(approved: Mapping[tuple[str, str], str] | None, server: str,
                 tool: Mapping[str, Any]) -> bool:
    """True when *tool* of *server* has no approval in *approved*, or its text
    hashes another way. ``None`` (no approvals were read) is True for each
    tool, so a failed read fails closed."""
    if approved is None:
        return True
    name = tool.get("name")
    want = approved.get((server, name)) if isinstance(name, str) else None
    return not isinstance(want, str) or want != tool_text_hash(tool)


def skill_tool_map(ids: Iterable[str], tools: Mapping[str, Sequence[Mapping[str, Any]]], *,
                   approved: Mapping[tuple[str, str], str] | None,
                   ) -> tuple[dict[str, str], frozenset[str]]:
    """TOF-5a-1. The MCP tools that a skill may name, and the ids not approved.

    Pure. *ids* are the ``mcp:<server>.<tool>`` ids that the skills of ONE
    turn name. *tools* holds the cached ``tools/list`` of each server that the
    member can reach, and only of those servers. *approved* is the read of
    ``mcp_approvals.approved_hashes`` for the bound org.

    Returns ``(served, unapproved)``. *served* maps an id to its local name
    (``skill_packs.mcp_local_name``). An id is served when its tool is in the
    cached list AND :func:`tool_changed` says False: a row alone is not
    enough. An id whose server is in *tools* but whose tool has no approval,
    has another hash, or is not in the list is in *unapproved*. An id of a
    server that *tools* does not hold is in neither, so it gives
    ``action_unserved``. ``None`` for *approved* (a failed read) gives
    nothing at all, so no ``mcp:`` action is served (fail closed)."""
    from acb_skills.skill_packs import mcp_local_name

    served: dict[str, str] = {}
    refused: set[str] = set()
    if approved is None:
        return served, frozenset()
    for raw in ids:
        uses = str(raw).strip()
        ref = parse_uses(uses)
        local = mcp_local_name(uses)
        if ref is None or ref.source != MCP_SOURCE or local is None:
            continue
        server, tool = str(ref.server), str(ref.tool)
        if server not in tools:
            continue
        item = next((t for t in unique_tools(tools[server]) if t.get("name") == tool), None)
        if item is None or tool_changed(approved, server, item):
            refused.add(uses)
            continue
        served[uses] = local[1]
    return served, frozenset(refused)


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
             tools: Mapping[str, Sequence[Mapping[str, Any]]], *,
             approved: Mapping[tuple[str, str], str] | None) -> list[dict[str, Any]]:
    """The catalog rows of the MCP tools of ONE org.

    *servers* is the server list of that org, from the caller (see the module
    text). *tools* maps a server name to its ``tools/list`` items. A server
    that is not enabled, a server name that is not in *servers*, an item that
    is not a mapping, and a name that the grammar refuses give no row. Two
    items of one name give one row, with the stricter class
    (:func:`unique_tools`). The order is the server name, then the order of
    the list.

    *approved* maps ``(server, tool)`` to the approved hash of the org (B1b-3).
    A tool that :func:`tool_changed` names gets ``changed: true`` and class
    B. ``None`` makes each tool changed. Each row carries ``text_hash``, the
    hash of the text that it shows, so an approval names that text."""
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
            changed = tool_changed(approved, server.name, item)
            # B1b-3 review: the hash that an admin sends back to approve
            # exactly the text that this row shows (compare and approve).
            text_hash = tool_text_hash(item)
            rows.append({
                "id": uses,
                "title": name,
                "source": MCP_SOURCE,
                # B1b-3: a changed tool is B, whatever its hints say.
                "class": "B" if changed else mcp_tool_class(
                    hints, always_confirm=server.always_confirm),
                "input_schema": None,
                "output_schema": None,
                "connection": f"{MCP_SOURCE}:{server.name}",
                "app": None,
                # The ONE map from an action to a chat tool. It gives None for
                # an ``mcp:`` id. TOF-5a-1 serves a skill through the map of
                # :func:`skill_tool_map`, never through this column.
                "tool": action_tool(uses),
                "server": server.name,
                "mcp_tool": name,
                "description": _description(item.get("description")),
                "hints": hints,
                "changed": changed,
                "text_hash": text_hash,
            })
    return rows
