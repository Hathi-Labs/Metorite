"""The chat tool source of the action catalog (TOF-1a, ``ai_studio.md`` §3.2).

Each chat tool of the CRM, Email, Projects and Calendar packs becomes one
catalog row: the id ``tool:<name>``, the source ``tool`` and ``tool`` set to
the chat tool name. ``acb_skills.skill_packs.chat_tools`` is the ONE scope,
and ``skill_packs.action_tool`` maps the id back to the name. So a member can
put a tool that the chat used into a skill (F6a), and the skill loads it.

**What a row does not do.** The ``uses`` grammar of
``acb_common.studio_manifest`` has no ``tool:`` form, so no package names one,
and the action door never runs one. A skill pack adds no tool of its own,
and the tool keeps its own card and guard. TOF-1b: when the run base does not
hold the tool of a ``tool:`` action, the skill brings the code pack of that
tool (``skill_packs.pack_needs``, ``tool_packs.choose``). The pack switch of
the org stays where it is: ``choose`` refuses a skill whose pack the allow
list of the run does not hold (``pack_off``). This module never reads that
switch.

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

**The cold build (TOF-1e).** The first build of the owners is slow (64 s on
the PC, measured). So the request path never runs it: :func:`chat_tool_rows`
with ``defer_cold=True`` gives class C rows until :func:`warm` has loaded the
owners, and it starts that warm-up on a thread. The gateway starts the same
warm-up at boot (``gateway.main.lifespan``). The loader keeps one lock for
its cold paths (``_tool_injection._PACK_LOAD_LOCK``), so the thread and a run
never import one owner twice. A class C row is the fail-closed answer of a
load fault, so the set of rows is the same.

Fences: ``tests/unit/test_studio_catalog.py`` (``TestTheChatToolSource``),
``tests/unit/test_chat_tool_warm.py`` and ``tests/unit/test_skill_packs.py``.
"""

from __future__ import annotations

import threading
import time
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


def _cached_tools(packs: Mapping[str, str]) -> dict[str, Any]:
    """The tools that the loader holds already, by name. It never builds, never
    takes the lock and never raises. A pack with no cached tools gives none, so
    its rows are class C and a pack that did load keeps its real classes."""
    out: dict[str, Any] = {}
    try:
        from acb_skills.tool_guard import tool_name
        from orchestrator._tool_injection import _cached_pack_tools
    except Exception:
        return out
    for slug, owner in packs.items():
        try:
            for fn in _cached_pack_tools(slug, owner) or ():
                out[tool_name(fn)] = fn
        except Exception:
            continue
    out.pop("", None)
    return out


def _owner_tools(packs: Mapping[str, str], failed: set[str] | None = None) -> dict[str, Any]:
    """The built tools of each pack of *packs* (``{slug: owner}``), by name.

    Best effort, and never raises. An owner that does not load gives no tool,
    and the rows of its tools are then class C. *failed*, when given, takes the
    slug of each pack whose owner or tool build failed."""
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
                if failed is not None:
                    failed.add(slug)
                continue
            for fn in _owner_pack_tools(slug, owner, module):
                out[tool_name(fn)] = fn
        except Exception as exc:  # a fault raises a class, never the catalog
            _log.warning("studio.chat_tools_pack_unavailable", pack=slug, owner=owner,
                         error_type=type(exc).__name__)
            if failed is not None:
                failed.add(slug)
    out.pop("", None)
    return out


def _title(name: str) -> str:
    """The title of the row of *name*: the name, with each ``_`` as a space."""
    words = name.replace("_", " ").strip()
    return words[:1].upper() + words[1:]


_WARM = threading.Event()
_WARM_LOCK = threading.Lock()
_warm_thread: threading.Thread | None = None
#: When the last try of :func:`warm` failed (``time.monotonic``), or None.
_warm_failed_at: float | None = None
#: How long a failed warm-up waits before the next try, as the module loader does.
WARM_RETRY_S = 60.0


def _owner_packs() -> dict[str, str]:
    """``{slug: owner}`` for each pack of the scope."""
    from acb_skills import skill_families

    return {slug: str(skill_families.SKILL_FAMILIES[slug]["owner"])
            for slug in dict.fromkeys(chat_tools().values())}


def is_warm() -> bool:
    """True when :func:`warm` loaded every owner of the scope."""
    return _WARM.is_set()


def warm() -> bool:
    """Load the owners of the scope now. Blocking: call it off the event loop.

    Never raises. It returns True, and :func:`is_warm` holds, only when every
    pack of the scope loaded its owner AND built its tools. A failure leaves it
    False, and the next try waits :data:`WARM_RETRY_S`."""
    global _warm_failed_at
    try:
        failed: set[str] = set()
        built = _owner_tools(_owner_packs(), failed)
        ok = bool(built) and not failed
    except Exception as exc:  # a fault leaves the catalog on class C
        _log.warning("studio.chat_tools_warm_failed", error_type=type(exc).__name__)
        ok = False
    if ok:
        _warm_failed_at = None
        _WARM.set()
    else:
        _warm_failed_at = time.monotonic()
    return ok


def start_warm() -> bool:
    """Start :func:`warm` on a daemon thread, once at a time. True when it
    started a thread. The event loop never waits for it."""
    global _warm_thread
    with _WARM_LOCK:
        if _WARM.is_set() or (_warm_thread is not None and _warm_thread.is_alive()):
            return False
        if _warm_failed_at is not None and time.monotonic() - _warm_failed_at < WARM_RETRY_S:
            return False
        _warm_thread = threading.Thread(target=warm, name="chat-tools-warm", daemon=True)
        _warm_thread.start()
        return True


def chat_tool_rows(*, defer_cold: bool = False) -> list[dict[str, Any]]:
    """One row for each chat tool of :func:`acb_skills.skill_packs.chat_tools`.

    With *defer_cold* (the request path) the call never loads an owner: when
    :func:`warm` has not finished, it starts it. The rows of a pack whose
    tools the loader holds already keep their real class, and the rows of
    each other pack are class C.

    The unfiltered builder: the caller checks ``STUDIO_CHAT_TOOLS_ORGS``
    first (``routes/studio/actions._chat_tool_rows``). The rows keep the order
    of the registry."""
    scope = chat_tools()
    if defer_cold and not _WARM.is_set():
        start_warm()
        built: dict[str, Any] = _cached_tools(_owner_packs())  # a cache hit, no build
    else:
        built = _owner_tools(_owner_packs())
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
