"""The chat tool source of the action catalog (TOF-1a, ``ai_studio.md`` §3.2).

Each chat tool of the CRM, Email, Projects and Calendar packs becomes one
catalog row: the id ``tool:<name>``, the source ``tool`` and ``tool`` set to
the chat tool name. ``acb_skills.skill_packs.chat_tools`` is the ONE scope,
and ``skill_packs.action_tool`` maps the id back to the name. So a member can
put a tool that the chat used into a skill (F6a), and the skill loads it.

**What a row does not do.** The ``uses`` grammar of
``acb_common.studio_manifest`` has no ``tool:`` form, so no package names one,
and the action door never runs one. A skill pack adds rules, not tools: the
run must hold the tool (``orchestrator._tool_injection._skills_present``), and
the tool keeps its own card and guard. The pack switch of the org stays where
it is: the load refuses a skill whose tool the run does not hold
(``tool_absent``). This module never reads that switch.

**The one class rule (fail closed)** (:func:`chat_tool_class`). No row shows
a class lower than the real gate of its tool:

* C for a send of ``egress.CARD_BOUND_SEND_TOOLS`` or
  ``egress.HELD_CARD_SEND_TOOLS``, whatever its annotation says;
* C when the annotation says ``destructive``, or when ``open_world`` is not
  ``False`` (a missing key is open world, H-236);
* C when the annotation cannot be read: an owner that does not load, a tool
  that the owner did not build, or no ``__tool_risk__``;
* A only when ``read_only`` is ``True`` and no C condition holds;
* B for each other tool. An email read that says ``read_only=False`` is B.

The rule reads ``__tool_risk__`` from the tool object and from its ``func``,
as ``egress`` and ``pack_tool_guard`` do. It never falls back to a name in
``TOOL_ANNOTATIONS``, and it keeps no class table.

**The read of the tools.** :func:`_owner_tools` takes them from THE loader of
a pack owner: ``orchestrator._tool_injection._pack_owner_module`` and
``_owner_pack_tools``, as the skills catalog
(``routes/integrations_skills.py``) does. Those keep the module and the built
tools for the process, and a failed import for 60 s, so this module keeps no
second cache. Best effort: an owner that does not load gives class C rows for
its tools, and a later read tries again. The set of rows comes from the scope
alone, so a load fault never adds or drops a row. It only raises a class.

Fences: ``tests/unit/test_studio_catalog.py`` (``TestTheChatToolSource``)
and ``tests/unit/test_skill_packs.py``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from acb_common import get_logger
from acb_skills import egress
from acb_skills.skill_packs import CHAT_TOOL_PREFIX, action_tool, chat_tools

_log = get_logger("studio.chat_tool_actions")

#: The source name of a row of this module.
SOURCE = "tool"


def chat_tool_class(name: str, hints: Mapping[str, Any] | None) -> str:
    """THE class of the chat tool *name* with the annotation *hints*.

    *hints* is None when the annotation cannot be read, and that gives C."""
    if name in egress.CARD_BOUND_SEND_TOOLS or name in egress.HELD_CARD_SEND_TOOLS:
        return "C"
    if not isinstance(hints, Mapping):
        return "C"
    if hints.get("destructive") is not False or hints.get("open_world") is not False:
        return "C"
    return "A" if hints.get("read_only") is True else "B"


def risk_of(tool: Any) -> Mapping[str, Any] | None:
    """The ``__tool_risk__`` of *tool* or of its ``func``, or None."""
    for obj in (tool, getattr(tool, "func", None)):
        hints = getattr(obj, "__tool_risk__", None)
        if isinstance(hints, Mapping):
            return hints
    return None


def _owner_tools(packs: Mapping[str, str]) -> dict[str, Any]:
    """The built tools of each pack of *packs* (``{slug: owner}``), by name.

    Best effort, and never raises. An owner that does not load gives no tool,
    and the rows of its tools are then class C."""
    out: dict[str, Any] = {}
    try:
        from acb_skills.tool_guard import tool_name
        from orchestrator._tool_injection import _owner_pack_tools, _pack_owner_module
    except Exception as exc:  # a fault raises a class, never the catalog
        _log.warning("studio.chat_tools_unloaded", error_type=type(exc).__name__)
        return out
    for slug, owner in packs.items():
        try:
            module = _pack_owner_module(owner)
            if module is None:
                _log.warning("studio.chat_tools_owner_unavailable", pack=slug, owner=owner)
                continue
            for fn in _owner_pack_tools(slug, owner, module):
                out[tool_name(fn)] = fn
        except Exception as exc:  # a fault raises a class, never the catalog
            _log.warning("studio.chat_tools_pack_unavailable", pack=slug, owner=owner,
                         error_type=type(exc).__name__)
    out.pop("", None)
    return out


def _title(name: str) -> str:
    """The title of the row of *name*: the name, with each ``_`` as a space."""
    words = name.replace("_", " ").strip()
    return words[:1].upper() + words[1:]


def chat_tool_rows() -> list[dict[str, Any]]:
    """One row for each chat tool of :func:`acb_skills.skill_packs.chat_tools`.

    The unfiltered builder: the caller checks ``STUDIO_CHAT_TOOLS_ORGS``
    first (``routes/studio/actions._chat_tool_rows``). The rows keep the order
    of the registry."""
    from acb_skills import skill_families

    scope = chat_tools()
    packs = {slug: str(skill_families.SKILL_FAMILIES[slug]["owner"])
             for slug in dict.fromkeys(scope.values())}
    built = _owner_tools(packs)
    rows: list[dict[str, Any]] = []
    for name in scope:
        action_id = f"{CHAT_TOOL_PREFIX}{name}"
        rows.append({
            "id": action_id,
            "title": _title(name),
            "source": SOURCE,
            "class": chat_tool_class(name, risk_of(built[name]) if name in built else None),
            "input_schema": None,
            "output_schema": None,
            "connection": None,
            "app": None,
            "tool": action_tool(action_id),
        })
    return rows
