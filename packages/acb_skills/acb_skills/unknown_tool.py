"""An unknown tool name gets a way through (chat_failure_modes.md §4.2 B1, HRD-1).

MAF answers a call to a name that the turn did not load with its own text,
``Error: Requested function "create_task" not found.``, and runs no
middleware for it. The model gets no pointer. Production, 14 days: 12 calls.
On 2026-11-12 and 2026-11-14 it was ``create_task`` and ``move_project``.

The seam is a :class:`ChatMiddleware`. It reads the messages of each model
request, finds that MAF text in a tool result and writes a better answer in
its place, before the model reads it. MAF keeps the text of a tool result
twice, in ``result`` and in ``items``, and the OpenAI client sends
``items``. So :func:`_set_text` writes both. Fence:
``tests/unit/test_unknown_tool_answer.py``.

The answer holds tool and pack names only, never member or tool data. It
names a tool only when the member may use it: a tool in the request now, or
a tool of a pack that ``load_skill_tools`` offers to this run (the planner
filtered that list by the member's permissions and the held run, H-236).

The text is always on, because it only replaces a raw error. The load is
``CHAT_TOOL_AUTOLOAD`` (default off). D-HRD-1 (D217) allows it, and it ships
dark. With it on, the middleware loads the offered pack through
``LoadSkillTools.process``, the loader's own entry and guards. Three rules
keep that load honest:

* Only a call of this run loads. A raw text in the history of the chat (a
  result that the first request of the run already holds) gets the text only.
* The load goes into the run's own live tool list. An outer middleware can
  send a request with a copy of that list (``tool_guard.WithholdTools`` does,
  in a held chat). A load into a copy would vanish at the next request, so
  the middleware loads only when the request holds the same list object as
  the request before it. Otherwise the text names ``load_skill_tools``.
* A refused load gives the loader's own refusal, which names a next step.
"""
from __future__ import annotations

import difflib
import re
from collections.abc import Awaitable, Callable, Iterable, Mapping
from typing import Any

from acb_common import get_logger
from agent_framework import ChatMiddleware, Content, ContextProvider, FunctionInvocationContext

__all__ = [
    "UnknownToolAnswer", "UnknownToolProvider", "answer_for", "autoload_enabled", "is_raw",
]

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
    'The tool "{name}" was not loaded. This turn loaded its pack "{slug}" now. '
    'Call "{tool}" again.'
)
LOADED_NOW_NEAR = (
    'No tool named "{name}" exists. The closest is "{tool}". This turn loaded '
    'its pack "{slug}" now. Call "{tool}".'
)
LOAD_FAILED = (
    'The tool "{name}" is in the pack "{slug}". This turn tried to load that '
    "pack, and the load answered:"
)
LIVE_NOW = 'The tool "{name}" is loaded now. Call "{name}" again.'
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


def is_raw(text: Any) -> str:
    """The tool name in MAF's own "not found" text *text*, or ""."""
    if not isinstance(text, str):
        return ""
    match = _RAW.match(text)
    return match.group(1) if match else ""


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


def _owners(offered: Iterable[str]) -> dict[str, str]:
    """Each tool of the packs *offered*, with the first pack that holds it."""
    by_tool: dict[str, str] = {}
    for slug in offered:
        for tool in _pack_tools(slug):
            by_tool.setdefault(tool, slug)
    return by_tool


def answer_for(
    name: str, *, live: Iterable[str], offered: Iterable[str], held: Iterable[str] = (),
    gone: Iterable[str] = (), loaded: str = "", tried: str = "", load_answer: str = "",
) -> str:
    """The answer to a call of the unknown tool *name*. Pure: no I/O.

    *live* names the tools of the request. *offered* is the packs that
    ``load_skill_tools`` offers. *held* is the packs already loaded and
    *gone* the packs that left the chat. *loaded* names the pack that this
    turn has just loaded for this call, and *tried* a pack whose load
    refused. *load_answer* is the loader's own text, which goes after the
    first line: the rules of the pack, or the refusal with its next step.
    """
    shown = _clean(name)
    live_names = set(live)
    tail = f"\n\n{load_answer.strip()}" if load_answer.strip() else ""
    if loaded:
        tools = _pack_tools(loaded)
        hit = name if name in tools else _closest(name, tools)
        if hit == name:
            return LOADED_NOW.format(name=shown, slug=loaded, tool=_clean(hit)) + tail
        if hit:
            return LOADED_NOW_NEAR.format(name=shown, slug=loaded, tool=_clean(hit)) + tail
    if tried and tail:
        return LOAD_FAILED.format(name=shown, slug=tried) + tail
    # A load in the same step added the tool after MAF looked for it: the
    # model called a pack tool beside load_skill_tools, or a sibling call
    # loaded the pack.
    if name in live_names:
        return LIVE_NOW.format(name=shown)
    by_tool = _owners(offered)
    # The exact name of a tool in an offered pack.
    if name in by_tool:
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


def _set_text(content: Any, text: str) -> None:
    """Write *text* as the whole result of *content*. MAF holds a tool result
    in ``items`` and keeps ``result`` as a copy, and the OpenAI client sends
    ``items``. So both change, or the model still reads the old text."""
    content.result = text
    content.items = [Content.from_text(text)]


def _live_names(tools: Any) -> list[str]:
    from acb_skills.tool_guard import tool_name

    return [n for n in (tool_name(t) for t in tools or []) if n]


def _result_key(content: Any) -> str:
    """The call id of a tool result, or its identity when it has none."""
    call_id = getattr(content, "call_id", None)
    return f"call:{call_id}" if call_id else f"id:{id(content)}"


def _unknown_results(messages: Iterable[Any]) -> list[tuple[Any, str]]:
    """The tool results that hold the raw MAF text, each with the name."""
    out: list[tuple[Any, str]] = []
    for message in messages or []:
        for content in getattr(message, "contents", None) or []:
            if getattr(content, "type", "") != "function_result":
                continue
            name = is_raw(getattr(content, "result", None))
            if not name:
                texts = [getattr(i, "text", None) for i in getattr(content, "items", None) or []]
                name = is_raw("\n".join(t for t in texts if isinstance(t, str)))
            if name:
                out.append((content, name))
    return out


class UnknownToolAnswer(ChatMiddleware):
    """Replaces the raw MAF text of an unknown tool with a way through.

    One instance serves ONE run (:class:`UnknownToolProvider` makes it in
    ``before_run``). It keeps the keys of the raw results of the first
    request, which came from the history of the chat, and the tool list of
    the request before, which tells it whether a request holds the run's
    live list."""

    def __init__(self, loader: Any = None) -> None:
        self._loader = loader
        #: The raw results that the first request held. The key is the call
        #: id, so a copy of a result keeps it. The list keeps each object
        #: alive, so the id of an object with no call id is not used twice.
        self._history: list[Any] | None = None
        self._history_keys: frozenset[str] = frozenset()
        self._last_tools: Any = None

    async def process(self, context: Any, call_next: Callable[[], Awaitable[None]]) -> None:
        try:
            await self._rewrite(context)
        except Exception as exc:  # the better text never costs the turn
            _log.warning("unknown_tool.rewrite_failed", error_type=type(exc).__name__)
        await call_next()

    def _live_list(self, options: Any) -> list[Any] | None:
        """The tool list of this request when it is the run's live list:
        the same object as the list of the request before. A copy that an
        outer middleware made is a new object each time, so it never counts."""
        tools = options.get("tools") if isinstance(options, Mapping) else None
        same = isinstance(tools, list) and tools is self._last_tools
        self._last_tools = tools
        return tools if same else None

    async def _rewrite(self, context: Any) -> None:
        options = getattr(context, "options", None)
        live_list = self._live_list(options)
        hits = _unknown_results(getattr(context, "messages", None))
        if self._history is None:
            self._history = [content for content, _name in hits]
            self._history_keys = frozenset(_result_key(c) for c in self._history)
        if not hits:
            return
        from acb_skills import tool_packs as tp

        loader = self._loader
        run = tp._run_context().get(tp.PACKS_KEY)
        run = run if isinstance(run, tp.PackRun) else None
        tools = options.get("tools") if isinstance(options, Mapping) else None
        for content, name in hits:
            held = run.holds() if run is not None else ()
            offered = tuple(s for s in (getattr(loader, "offered", ()) or ()) if s not in held)
            gone = tuple(getattr(run, "gone", ()) or ())
            loaded = tried = load_answer = ""
            if (_result_key(content) not in self._history_keys and live_list is not None
                    and loader is not None and autoload_enabled()):
                try:
                    loaded, tried, load_answer = await self._autoload(name, offered, live_list)
                except Exception as exc:  # a failed load still gets the text
                    _log.warning("unknown_tool.autoload_failed", error_type=type(exc).__name__)
            text = answer_for(
                name, live=_live_names(tools), offered=offered, held=held, gone=gone,
                loaded=loaded, tried=tried, load_answer=load_answer)
            _set_text(content, text)
            _log.info("unknown_tool.answered", tool=_clean(name),
                      loaded=loaded or None, refused=tried or None)

    async def _autoload(
        self, name: str, offered: tuple[str, ...], live_list: list[Any],
    ) -> tuple[str, str, str]:
        """Load the offered pack that holds *name*, or the closest name.

        Gives ``(loaded, tried, answer)``. *loaded* is the slug when the run
        holds the pack after the call. *tried* is the slug when the loader
        refused, and *answer* is the loader's own text. The call goes through
        ``LoadSkillTools.process``, the one entry of a load, with a
        ``FunctionInvocationContext`` over the run's live list. So the run
        check, the caps, the MCP rule, the build and the sticky write are the
        loader's own, and MAF's own ``add_tools`` adds the tools."""
        owners = _owners(offered)
        tool = name if name in owners else _closest(name, owners)
        if not tool:
            return "", "", ""
        slug = owners[tool]
        loader = self._loader
        frame = FunctionInvocationContext(
            function=loader.tool, arguments={"pack": slug}, tools=live_list)

        async def _not_taken() -> None:
            frame.result = ""

        await loader.process(frame, _not_taken)
        answer = str(frame.result or "")
        record = getattr(loader, "record", None)
        holds = record.holds() if hasattr(record, "holds") else ()
        if slug in holds:
            return slug, "", answer
        return "", slug, answer


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
