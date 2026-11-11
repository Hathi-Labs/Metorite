"""An unknown tool name gets a way through (chat_failure_modes.md §4.2 B1, HRD-1).

MAF answers a call to a name that the turn did not load with its own text,
``Error: Requested function "create_task" not found.``, and runs no
middleware for it. The model gets no pointer. Production, 14 days: 12 calls.

The seam is a :class:`ChatMiddleware`. It reads the messages of each model
request, finds that MAF text in a tool result and writes a better answer in
its place, before the model reads it. Fence:
``tests/unit/test_unknown_tool_answer.py``.

The answer holds tool and pack names only, never member or tool data. It
names a tool only when the member may use it: a tool in the request now, or
a tool of a pack that ``load_skill_tools`` offers to this run (the planner
filtered that list by the member's permissions and the held run, H-236).

The text is always on, because it only replaces a raw error. The load is
``CHAT_TOOL_AUTOLOAD`` (default off, owner decision D-HRD-1). With it on, the
middleware loads the offered pack through the loader's own guards.
"""
from __future__ import annotations

import difflib
import re
from collections.abc import Awaitable, Callable, Iterable, Mapping
from typing import Any

from acb_common import get_logger
from agent_framework import ChatMiddleware, ContextProvider

__all__ = ["UnknownToolAnswer", "UnknownToolProvider", "answer_for", "autoload_enabled"]

_log = get_logger("acb_skills.unknown_tool")

#: The two texts of MAF for a name that the tool map lacks.
_RAW = re.compile(r'^\s*(?:Error:\s*)?(?:Requested function|Function) "([^"]{1,200})" not found\.?\s*$')
#: A name goes into the answer only in this alphabet and at this length.
_SAFE = re.compile(r"[^A-Za-z0-9_.:-]")
_CUTOFF = 0.75

SUGGEST_LOAD = (
    'The tool "{name}" is not loaded in this turn. It is in the pack "{slug}". '
    'Call load_skill_tools with pack "{slug}", then call "{name}" again.'
)
SUGGEST_LOAD_NEAR = (
    'No tool named "{name}" exists. The closest is "{near}", in the pack "{slug}". '
    'Call load_skill_tools with pack "{slug}", then call "{near}".'
)
LOADED_NOW = (
    'The tool "{name}" was not loaded. The {slug} tools are loaded now. Call "{tool}" again.'
)
LOADED_NOW_NEAR = (
    'No tool named "{name}" exists. The {slug} tools are loaded now. Call "{tool}".'
)
SUGGEST_LIVE = 'No tool named "{name}" exists. The closest tool that you hold is "{near}".'
NEEDS_PACK = (
    'The tool "{name}" is not available in this turn. Its work needs the {slug} tools. '
    "Tell the member which part did not run, and that the next message can do it."
)
NOT_HERE = (
    'No tool named "{name}" exists in this chat. Use only the tools that you hold, '
    "or tell the member which part you cannot do."
)


def autoload_enabled() -> bool:
    """The one reader of ``CHAT_TOOL_AUTOLOAD``. A settings failure reads OFF."""
    try:
        from acb_common import get_settings

        return bool(getattr(get_settings(), "chat_tool_autoload", False))
    except Exception:
        return False


def _clean(name: str) -> str:
    return _SAFE.sub("", name)[:60]


def _pack_tools(slug: str) -> tuple[str, ...]:
    from acb_skills import tool_packs as tp

    found = tp.pack(slug)
    return tuple(found.tools) if found is not None else ()


def _closest(name: str, pool: Iterable[str]) -> str:
    names = sorted(set(pool))
    lowered = {n.lower(): n for n in names}
    hit = difflib.get_close_matches(name.lower(), list(lowered), n=1, cutoff=_CUTOFF)
    return lowered[hit[0]] if hit else ""


def answer_for(
    name: str, *, live: Iterable[str], offered: Iterable[str], held: Iterable[str] = (),
    gone: Iterable[str] = (), loaded: str = "",
) -> str:
    """The answer to a call of the unknown tool *name*. Pure: no I/O.

    *live* names the tools of the request. *offered* is the packs that
    ``load_skill_tools`` offers. *held* is the packs already loaded and
    *gone* the packs that left the chat. *loaded* names the pack that the
    caller has just loaded for this call, or "".
    """
    shown = _clean(name)
    live_names = set(live)
    by_tool: dict[str, str] = {}
    for slug in offered:
        for tool in _pack_tools(slug):
            by_tool.setdefault(tool, slug)
    if loaded:
        tools = _pack_tools(loaded)
        hit = name if name in tools else _closest(name, tools)
        if hit == name:
            return LOADED_NOW.format(name=shown, slug=loaded, tool=_clean(hit))
        if hit:
            return LOADED_NOW_NEAR.format(name=shown, slug=loaded, tool=_clean(hit))
    # The exact name of a tool in an offered pack.
    if name in by_tool and name not in live_names:
        return SUGGEST_LOAD.format(name=shown, slug=by_tool[name])
    # The closest real name among the tools that the member may use.
    near = _closest(name, [*live_names, *by_tool])
    if near:
        if near in live_names:
            return SUGGEST_LIVE.format(name=shown, near=_clean(near))
        return SUGGEST_LOAD_NEAR.format(
            name=shown, near=_clean(near), slug=by_tool[near])
    # A pack that left this chat and holds the name: name the pack, no tool.
    from acb_skills import tool_packs as tp

    for slug in gone:
        if name in _pack_tools(slug) and slug not in set(held):
            return NEEDS_PACK.format(name=shown, slug=tp._label(slug))
    return NOT_HERE.format(name=shown)


def _live_names(options: Any) -> list[str]:
    from acb_skills.tool_guard import tool_name

    tools = options.get("tools") if isinstance(options, Mapping) else None
    return [n for n in (tool_name(t) for t in tools or []) if n]


def _unknown_results(messages: Iterable[Any]) -> list[tuple[Any, str]]:
    """The tool results that hold the raw MAF text, each with the name."""
    out: list[tuple[Any, str]] = []
    for message in messages or []:
        for content in getattr(message, "contents", None) or []:
            if getattr(content, "type", "") != "function_result":
                continue
            result = getattr(content, "result", None)
            if not isinstance(result, str):
                continue
            match = _RAW.match(result)
            if match:
                out.append((content, match.group(1)))
    return out


class _LoadFrame:
    """The slice of ``FunctionInvocationContext`` that ``LoadSkillTools._load``
    reads, over the live tool list of the request."""

    def __init__(self, slug: str, live: list[Any]) -> None:
        self.arguments = {"pack": slug}
        self.tools = live

    def add_tools(self, tools: Any) -> None:
        from agent_framework._tools import normalize_tools

        from acb_skills.tool_guard import tool_name

        have = {tool_name(t) for t in self.tools}
        for tool in normalize_tools(list(tools)):
            if tool_name(tool) not in have:
                self.tools.append(tool)

    def remove_tools(self, names: Iterable[str]) -> None:
        from acb_skills.tool_guard import tool_name

        gone = set(names)
        self.tools[:] = [t for t in self.tools if tool_name(t) not in gone]


class UnknownToolAnswer(ChatMiddleware):
    """Replaces the raw MAF text of an unknown tool with a way through."""

    def __init__(self, loader: Any = None) -> None:
        self._loader = loader

    async def process(self, context: Any, call_next: Callable[[], Awaitable[None]]) -> None:
        try:
            await self._rewrite(context)
        except Exception as exc:  # the better text never costs the turn
            _log.warning("unknown_tool.rewrite_failed", error_type=type(exc).__name__)
        await call_next()

    async def _rewrite(self, context: Any) -> None:
        hits = _unknown_results(getattr(context, "messages", None))
        if not hits:
            return
        from acb_skills import tool_packs as tp

        loader = self._loader
        run = tp._run_context().get(tp.PACKS_KEY)
        run = run if isinstance(run, tp.PackRun) else None
        options = getattr(context, "options", None)
        live_list = options.get("tools") if isinstance(options, dict) else None
        live = _live_names(options)
        for content, name in hits:
            offered = tuple(
                s for s in (getattr(loader, "offered", ()) or ())
                if run is None or s not in run.holds())
            held = run.holds() if run is not None else ()
            gone = tuple(getattr(run, "gone", ()) or ())
            loaded = ""
            if autoload_enabled() and loader is not None and isinstance(live_list, list):
                loaded = await self._autoload(name, offered, live_list)
                if loaded:
                    live = _live_names(options)
            content.result = answer_for(
                name, live=live, offered=offered, held=held, gone=gone, loaded=loaded)
            _log.info("unknown_tool.answered", tool=_clean(name), loaded=loaded or None)

    async def _autoload(self, name: str, offered: tuple[str, ...], live_list: list[Any]) -> str:
        """Load the offered pack that holds *name*, or the closest name.
        Gives the slug when it loaded, else "". The loader's own guards
        (caps, MCP rule, held run) decide, and a refusal loads nothing."""
        owners = {t: s for s in offered for t in _pack_tools(s)}
        tool = name if name in owners else _closest(name, owners)
        if not tool:
            return ""
        slug = owners[tool]
        loader = self._loader
        frame = _LoadFrame(slug, live_list)
        async with loader._lock:
            text = await loader._load(frame)
        return slug if text.startswith("Loaded the ") else ""


class UnknownToolProvider(ContextProvider):
    """Adds :class:`UnknownToolAnswer` to ONE run of a native MAF agent."""

    SOURCE_ID = "unknown_tool_answer"

    def __init__(self) -> None:
        super().__init__(self.SOURCE_ID)

    async def before_run(
        self, *, agent: Any, session: Any, context: Any, state: dict[str, Any],
    ) -> None:
        from acb_skills.tool_packs import LoadSkillToolsProvider

        loader = None
        for provider in getattr(agent, "context_providers", None) or []:
            if isinstance(provider, LoadSkillToolsProvider):
                loader = provider.loader
        context.extend_middleware(self.SOURCE_ID, [UnknownToolAnswer(loader)])
