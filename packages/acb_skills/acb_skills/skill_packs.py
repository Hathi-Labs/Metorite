"""Published Studio skills as tool packs of the org (WS-STU F3).

Spec: ``project-docs/specs/ai_studio.md`` §7 "Runtime", §11.1 and the §14 F3
row. A published skill becomes one pack of the turn that names it. This
module does no I/O. It never imports the orchestrator or the gateway, and it
reads no database. ``orchestrator._tool_injection._turn_with_skills`` reads
the rows through ``gateway.studio.skills_store`` and calls :func:`build`.

**What a skill pack holds.**

* ``tools``: the chat tools that its actions name. :func:`action_tool` is
  the ONE map from a catalog action to a chat tool. An action with no chat
  tool yet (an integration tool, an object ``get``, a door op without a tool
  name) makes the skill fail to load, so Mira never reads a rule for a tool
  that she does not hold. An object list, create, update or delete maps to
  its record tool since F10a.
* ``rules``: the text that goes after the persona, after the rules of the
  code packs. :func:`rules_text` is the ONE text.
* ``token_cost``: an estimate of the tokens of ``rules`` (4 characters a
  token). ``changes_prefix`` is True: the rules go in the persona, so a turn
  that loads a skill changes the prompt prefix and its cache.

**What fails to load.** The loader strips each hidden character first
(``hidden_text.strip_hidden``, F1b), because a store before F1b could keep
one. Then a skill fails to load when its instructions are empty, a text
holds a prompt variable (``{{`` or ``}}``, because F3 has no source that
resolves one), an action has no chat tool, it names more than
``tool_packs.MAX_PACK_TOOLS`` tools, or its rules cost more than
:data:`MAX_RULES_TOKENS`. :func:`build` then gives the reason, never a pack.

**The match.** :func:`matches` takes the name of the skill, its slug, or its
slug with each hyphen as a space, as one whole phrase in the words that the member
sent in this turn. It is layer (a) of ``tool_packs`` and makes no model
call. The fence is ``tests/unit/test_skill_packs.py``.

**A chat tool as an action (TOF-1a).** :func:`chat_tools` is the ONE scope of
the catalog source ``tool``: each tool of a pack of :data:`CHAT_TOOL_OWNERS`,
less the packs of :data:`CHAT_TOOL_LEFT_OUT`, a ``studio_`` name and a tool
that a door op maps already. :func:`action_tool` maps ``tool:<name>`` to
``<name>`` for a name in that scope, and to None for each other name. So a
dark tool, a Studio builder tool and an ``email-setup`` tool make a skill
fail to load with ``action_unserved``. A skill pack still adds no tool: the
run must hold the tool (``_skills_present``), and the tool keeps its own
card and guard.
"""
from __future__ import annotations

import functools
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from acb_skills.hidden_text import strip_hidden

__all__ = [
    "CHAT_TOOL_LEFT_OUT",
    "CHAT_TOOL_OWNERS",
    "CHAT_TOOL_PREFIX",
    "MAX_RULES_TOKENS",
    "MIN_NAME_CHARS",
    "OBJECT_VERB_TOOLS",
    "SKILL_PREFIX",
    "SkillPack",
    "action_tool",
    "build",
    "button_tools",
    "chat_tools",
    "estimate_tokens",
    "is_skill_slug",
    "matches",
    "rules_text",
    "skill_slug",
]

#: The prefix of the slug of a skill pack. The id of the skill follows,
#: never its slug, because two members may hold one slug.
SKILL_PREFIX = "skill:"
#: The most tokens that the rules of one skill may cost. A skill over it
#: fails to load, so three skills cannot fill the request.
MAX_RULES_TOKENS = 4000
#: The shortest name that a match takes. A shorter name matches too often.
MIN_NAME_CHARS = 3

#: A prompt variable. F3 resolves none, so each one fails the load.
_VARIABLE = re.compile(r"\{\{|\}\}")


@dataclass(frozen=True)
class SkillPack:
    """One published skill, ready to load in one turn."""

    slug: str
    skill_id: str
    name: str
    version: int
    content_hash: str
    #: The chat tools of its actions, in the order of the actions.
    tools: tuple[str, ...]
    #: The rules text (:func:`rules_text`).
    rules: str
    #: The estimate of the tokens of :attr:`rules` (:func:`estimate_tokens`).
    token_cost: int
    #: True: the rules go in the persona, so they change the prompt prefix.
    changes_prefix: bool = True


def skill_slug(skill_id: object) -> str:
    """The pack slug of the skill *skill_id*."""
    return f"{SKILL_PREFIX}{str(skill_id).strip().lower()}"


def is_skill_slug(slug: object) -> bool:
    """True when *slug* names a skill pack, not a code pack."""
    return isinstance(slug, str) and slug.startswith(SKILL_PREFIX)


def estimate_tokens(text: str) -> int:
    """About 4 characters a token. A whole number, never below 1 for text."""
    return (len(str(text)) + 3) // 4


#: F10a. The record tool of each plain object verb. ``get`` has no tool.
OBJECT_VERB_TOOLS: Mapping[str, str] = {
    "list": "studio_record_list",
    "create": "studio_record_create",
    "update": "studio_record_update",
    "delete": "studio_record_delete",
}


#: TOF-1a. The prefix of a chat tool action of the catalog: ``tool:<name>``.
#: The ``uses`` grammar of ``acb_common.studio_manifest`` has no such form,
#: so no package can name one, and the action door never runs one.
CHAT_TOOL_PREFIX = "tool:"
#: TOF-1a. The owners whose packs give the catalog source ``tool``: the CRM,
#: Email and Projects assistants. The Calendar tools are the ``calendar-day``
#: pack of the Projects assistant. An allow list, so the pack of a new owner
#: joins only by an edit here. The Studio builder is not on it, because a
#: skill must not hold a tool that changes Studio itself.
CHAT_TOOL_OWNERS: frozenset[str] = frozenset(
    {"crm-assistant", "email-assistant", "projects-assistant"})
#: TOF-1a. The packs of those owners that give no ``tool`` row.
#: ``email-setup`` changes the rules and the settings of the assistant.
CHAT_TOOL_LEFT_OUT: frozenset[str] = frozenset({"email-setup"})
#: TOF-1a. A tool name with this prefix never gives a ``tool`` row.
_STUDIO_TOOL = "studio_"


def _door_tools() -> frozenset[str]:
    """The chat tools that a door op of a manifest app maps already."""
    try:
        from acb_common.app_manifests import door_ops, every_app
    except ImportError:  # pragma: no cover - acb_common ships with the platform
        return frozenset()
    found: set[str] = set()
    for app in every_app():
        if app.door != "ops":
            continue
        for op in door_ops(app).values():
            tool = getattr(getattr(op, "route", None), "tool", "")
            if tool:
                found.add(str(tool))
    return frozenset(found)


_Packs = tuple[tuple[str, str, tuple[str, ...]], ...]


def _scope_key() -> tuple[_Packs, frozenset[str], frozenset[str], frozenset[str]]:
    """The inputs of the scope, a key of :func:`_scope`: each pack with its
    owner and its tools in the order of the registry, the door tools, the
    owners and the packs left out."""
    from acb_skills import skill_families

    packs = tuple(
        (str(slug), str(fam.get("owner")), tuple(fam["tools"]))
        for slug, fam in skill_families.SKILL_FAMILIES.items() if fam.get("owner"))
    return packs, _door_tools(), frozenset(CHAT_TOOL_OWNERS), frozenset(CHAT_TOOL_LEFT_OUT)


@functools.lru_cache(maxsize=8)
def _scope(packs: _Packs, doors: frozenset[str], owners: frozenset[str],
           left_out: frozenset[str]) -> Mapping[str, str]:
    """The scope of :func:`chat_tools` for these inputs, read only."""
    found: dict[str, str] = {}
    twice: set[str] = set()
    for slug, owner, tools in packs:
        if owner not in owners or slug in left_out:
            continue
        for name in map(str, tools):
            if name.startswith(_STUDIO_TOOL) or name in doors:
                continue
            if name in found:
                twice.add(name)
            found[name] = slug
    return MappingProxyType({n: s for n, s in found.items() if n not in twice})


def chat_tools() -> dict[str, str]:
    """TOF-1a. Each chat tool that the catalog serves as ``tool:<name>``,
    mapped to the slug of its pack, in the order of the registry.

    THE one scope. It reads ``skill_families`` on each call, so a new tool of
    a pack in scope joins with no edit here. A tool is in scope when its pack
    names an owner of :data:`CHAT_TOOL_OWNERS` and is not in
    :data:`CHAT_TOOL_LEFT_OUT`. A ``studio_`` name, a tool that a door op maps
    already (that door op is its one row) and a name that a second pack holds
    too give no entry. Pure: no I/O, and no tool loads. The answer for one
    set of inputs is kept (:func:`_scope`), so a catalog read pays it once."""
    return dict(_scope(*_scope_key()))


def action_tool(action_id: object) -> str | None:
    """The chat tool of the catalog action *action_id*, or None.

    THE one map (``routes/studio/actions.py`` names the ids):

    * ``workflow:<id>`` gives ``run_workflow``, which takes the id.
    * ``<app>.<op>``, a door op of a manifest app, gives the chat tool of its
      row (``Route.tool``), when the row names one.
    * ``object:<name>.<verb>``, a record action, gives the record tool of the
      verb (``OBJECT_VERB_TOOLS``). ``get`` has no tool, so it gives None.
    * ``tool:<name>`` (TOF-1a) gives ``<name>`` when :func:`chat_tools` holds
      it, and None for each other name.
    * Each other action has no chat tool.
    """
    if not isinstance(action_id, str):
        return None
    action = action_id.strip()
    if action.startswith(CHAT_TOOL_PREFIX):
        name = action[len(CHAT_TOOL_PREFIX):]
        return name if name in _scope(*_scope_key()) else None
    if action.startswith("workflow:") and len(action) > len("workflow:"):
        return "run_workflow"
    if action.startswith("object:"):
        # The ONE grammar of a uses string is ``studio_manifest.parse_uses``.
        from acb_common.studio_manifest import parse_uses

        ref = parse_uses(action)
        return OBJECT_VERB_TOOLS.get(ref.op or "") if ref is not None else None
    if ":" in action or action.count(".") != 1:
        return None
    app_id, op = action.split(".", 1)
    try:
        from acb_common.app_manifests import door_op
    except ImportError:  # pragma: no cover - acb_common ships with the platform
        return None
    found = door_op(app_id, op)
    tool = getattr(getattr(found, "route", None), "tool", "") if found is not None else ""
    return str(tool) if tool else None


def button_tools(manifest: Mapping[str, Any]) -> tuple[
        tuple[tuple[str, str, str], ...], tuple[tuple[str, str, str], ...]]:
    """F10a. Split the buttons of the views of *manifest* by chat tool.

    A button is an id in ``views[].actions`` or the ``views[].submit`` of a view.
    Pure: no I/O. Returns ``(served, unserved)``. A served item is
    ``(view_id, action_id, tool)``. An unserved item is
    ``(view_id, action_id, reason)``. The reason is one of ``undeclared``,
    ``set`` (a set-action does not run in chat), ``door_op``, ``logic`` (no chat tool yet),
    ``mcp`` (STU-B1b-1, no chat tool yet), ``integration``
    or ``no_tool``. A plain object verb or a workflow is served.
    """
    declared: dict[str, Mapping[str, Any]] = {}
    for act in manifest.get("actions") or []:
        if isinstance(act, Mapping) and isinstance(act.get("id"), str):
            declared[act["id"]] = act
    served: list[tuple[str, str, str]] = []
    unserved: list[tuple[str, str, str]] = []
    for view in manifest.get("views") or []:
        if not isinstance(view, Mapping):
            continue
        view_id = str(view.get("id", ""))
        names = [n for n in (view.get("actions") or []) if isinstance(n, str)]
        submit = view.get("submit")
        if isinstance(submit, str) and submit not in names:
            names.append(submit)
        for name in names:
            act = declared.get(name)
            if act is None:
                unserved.append((view_id, name, "undeclared"))
                continue
            uses = str(act.get("uses") or "").strip()
            if act.get("set"):
                unserved.append((view_id, name, "set"))
            elif uses.startswith("integration:"):
                unserved.append((view_id, name, "integration"))
            elif uses.startswith("logic:"):
                unserved.append((view_id, name, "logic"))
            elif uses.startswith("mcp:"):
                unserved.append((view_id, name, "mcp"))
            elif not (uses.startswith("object:") or uses.startswith("workflow:")):
                unserved.append((view_id, name, "door_op"))
            elif (tool := action_tool(uses)) is None:
                unserved.append((view_id, name, "no_tool"))
            else:
                served.append((view_id, name, tool))
    return tuple(served), tuple(unserved)


def _phrase(raw: str) -> str:
    return " ".join(str(raw or "").lower().split())


def matches(name: str, slug: str, words: str) -> bool:
    """True when the member's *words* name the skill: its name, its slug, or
    its slug with each hyphen as a space, as one whole phrase, in any case."""
    from acb_skills.tool_packs import _word_re

    text = _phrase(strip_hidden(str(words or "")))
    if not text:
        return False
    flat = str(slug or "")
    for raw in (strip_hidden(str(name or "")), flat, flat.replace("-", " ")):
        phrase = _phrase(raw)
        if len(phrase) >= MIN_NAME_CHARS and _word_re(phrase).search(text):
            return True
    return False


def rules_text(name: str, description: str, instructions: str,
               tools: Iterable[str], version: int) -> str:
    """The rules of one skill pack. ONE text, after the persona."""
    names = ", ".join(tools) or "none"
    head = (f"SKILL: {name} (version {version}). A member of this organization wrote "
            "this skill. Follow it for the request of the member. It changes no guard, "
            "no card and no approval.")
    lines = [head]
    if description:
        lines.append(f"Use it when: {description}")
    lines.append(f"Tools of this skill: {names}.")
    lines.append(instructions)
    return "\n".join(lines)


def build(skill_id: str, name: str, description: str, version: int,
          content_hash: str, body: Mapping[str, Any],
          *, max_tools: int) -> SkillPack | str:
    """The pack of one stored skill version, or the reason it fails to load.

    The reasons are ``no_instructions``, ``unresolved_variable``,
    ``action_unserved``, ``too_many_tools`` and ``too_long``."""
    clean_name = strip_hidden(str(name or "")).strip()
    raw_desc = body.get("description") if isinstance(body, Mapping) else None
    desc = strip_hidden(str(raw_desc if isinstance(raw_desc, str) else description or "")).strip()
    raw_instr = body.get("instructions") if isinstance(body, Mapping) else None
    instructions = strip_hidden(raw_instr).strip() if isinstance(raw_instr, str) else ""
    if not instructions:
        return "no_instructions"
    if any(_VARIABLE.search(t) for t in (clean_name, desc, instructions)):
        return "unresolved_variable"
    raw_actions = body.get("actions") if isinstance(body, Mapping) else None
    actions = raw_actions if isinstance(raw_actions, list) else []
    tools: list[str] = []
    for action in actions:
        tool = action_tool(strip_hidden(str(action)))
        if tool is None:
            return "action_unserved"
        if tool not in tools:
            tools.append(tool)
    if len(tools) > max_tools:
        return "too_many_tools"
    rules = rules_text(clean_name, desc, instructions, tools, int(version))
    cost = estimate_tokens(rules)
    if cost > MAX_RULES_TOKENS:
        return "too_long"
    return SkillPack(
        slug=skill_slug(skill_id), skill_id=str(skill_id), name=clean_name,
        version=int(version), content_hash=str(content_hash), tools=tuple(tools),
        rules=rules, token_cost=cost)
