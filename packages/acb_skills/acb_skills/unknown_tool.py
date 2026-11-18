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
The one exception is the answer of HRD-1d, which repeats the name that the
model called, and no other name.

Each answer says what happened ("Not run …") and what is true now. None
tells the model to call the tool again. The rewrite changes the result in
place, so the text stays in the stored history, and a later turn reads it.

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
* One load try for each name in a run (task 3599). A later call of the same
  name gets the text of the first try, and the loader is not asked again. So
  a model that calls the name again and again cannot spin the loader.

The member never reads MAF's text either (task 3599). MAF puts the
``KeyError`` text, ``'Function "x" not found.'``, in the ``exception`` of the
result, and the trail of the chat showed it. :func:`unknown_call_name` finds
MAF's result in each of its three fields. ``orchestrator.event_translator``
asks it, and shows :data:`MEMBER_LINE` on the step in its place.

HRD-1b (task 3230): a batch run gets the same answer, with no flag. One hook
of the executor, ``_unknown_tool_view``, puts the provider on the stream run
and on each batch run (``_assemble_batch_agents``). So a WhatsApp run, a
delegated agent, a worker and a self-anneal retry are in scope. A batch run
has no loader, so it never loads. HRD-1c (task 3679): the stream run of each
agent gets it, not only the orchestrator's. An agent that is not the
orchestrator has no loader either, so its answer names only a tool of its
own request. Fence: ``tests/unit/test_unknown_tool_non_orchestrator_stream.py``.
A delegated run answers another model, and a run with no chat answers no
member. Each of those runs is a *step* (``step=True``). Its answer names only
a tool of the request, or it is :data:`NOT_IN_STEP`. A step never reads the
record of the packs and never asks a loader, so its text names no pack and
offers no load. Fence: ``tests/unit/test_unknown_tool_batch_paths.py``.

HRD-1d (task 3719): a run that holds a pack that reads mail from outside
senders gets no MCP server (``_tool_injection._inject_mcp_servers``). The
model can still call a tool of such a server, because the name is in the
history of the chat. :data:`NOT_HERE` then said that no tool has that name,
which is false. Now the skip tells the provider of the run which servers it
held back (:func:`note_withheld`), as a :class:`Withheld` value from the
server config. A call of a tool of one of them gets :data:`WITHHELD_MAIL`,
which says why and what the member can do. A step ignores the value. Fence:
``tests/unit/test_unknown_tool_withheld.py``.
"""
from __future__ import annotations

import difflib
import re
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from acb_common import get_logger
from agent_framework import ChatMiddleware, Content, ContextProvider, FunctionInvocationContext

__all__ = [
    "MEMBER_LINE", "NOT_IN_STEP", "WITHHELD_MAIL", "WITHHELD_MAIL_REASON", "UnknownToolAnswer",
    "UnknownToolProvider", "Withheld", "answer_for", "autoload_enabled", "is_raw",
    "note_withheld", "reads_withheld", "unknown_call_name",
]

_log = get_logger("acb_skills.unknown_tool")

#: The two texts of MAF for a name that the tool map lacks. The text of the
#: ``KeyError`` that MAF keeps in ``exception`` has one pair of single quotes
#: around it (task 3599), so a quote may open and close the text.
_RAW = re.compile(
    r'^\s*\'?(?:Error:\s*)?(?:Requested function|Function) "([^"]{1,200})" not found\.?\'?\s*$')
#: A name goes into the answer only in this alphabet and at this length.
_SAFE = re.compile(r"[^A-Za-z0-9_.:-]")
_CUTOFF = 0.75

# Each text says what happened and what is true now. None tells the model to
# call the tool again: ``_set_text`` changes the result in place, so the text
# stays in the history of the chat, and a later turn reads it. An order to
# retry there could repeat a write that the member moved past.
SUGGEST_LOAD = (
    "Not run: {name} is in the «{slug}» pack, which this chat had not loaded. "
    "To run it, load that pack with load_skill_tools first."
)
SUGGEST_LOAD_NEAR = (
    "Not run: no tool is named {name}. The closest tool is {near}, in the "
    "«{slug}» pack, which this chat had not loaded. To use it, load that pack "
    "with load_skill_tools first."
)
LOADED_NOW = (
    "Not run on this call. The «{slug}» pack is loaded now, so {tool} is in your tools."
)
LOADED_NOW_NEAR = (
    "Not run: no tool is named {name}. The closest tool is {tool}. The «{slug}» "
    "pack is loaded now, so {tool} is in your tools."
)
LOAD_FAILED = "Not run: {name} is in the «{slug}» pack. The load of that pack answered:"
LIVE_NOW = "Not run on this call. {name} is in your tools now."
SUGGEST_LIVE = "Not run: no tool is named {name}. The closest tool is {near}."
NEEDS_PACK = (
    "Not run: {name} is not available in this chat. Its work needs the {slug} "
    "tools. Tell the member which part did not run, and that the next message "
    "can do it."
)
NOT_HERE = (
    "Not run: no tool is named {name} in this chat. Use only the tools that you "
    "hold, or tell the member which part you cannot do."
)
#: HRD-1b: the answer of a step (a delegated run, or a run with no chat) when
#: no tool of the request is close. It names no pack and offers no load.
NOT_IN_STEP = "Not run: {name} is not available in this step. Use only the tools that you hold."
#: HRD-1d: the answer to a call of a tool of an MCP server that the run held
#: back, because the run reads mail from outside senders. The model can quote
#: it to the member, so it holds no internal word and names no other server.
WITHHELD_MAIL = (
    "Not run: {name} is a tool of a connected system, and a chat that reads email "
    "from outside senders cannot use it. Tell the member that this tool works in a "
    "new chat that does not read email."
)
#: The reason code of :data:`WITHHELD_MAIL`.
WITHHELD_MAIL_REASON = "untrusted_mail"
#: Each reason code of a :class:`Withheld` value, with its answer.
WITHHELD_TEXTS: Mapping[str, str] = {WITHHELD_MAIL_REASON: WITHHELD_MAIL}
#: Every answer text, for the fence.
TEXTS = (
    SUGGEST_LOAD, SUGGEST_LOAD_NEAR, LOADED_NOW, LOADED_NOW_NEAR, LOAD_FAILED,
    LIVE_NOW, SUGGEST_LIVE, NEEDS_PACK, NOT_HERE, NOT_IN_STEP, WITHHELD_MAIL,
)
#: What the member reads on the step of the trail in place of MAF's text
#: (task 3599). It names no tool, no pack and no framework. The model never
#: reads it: the stream event and the saved step carry it, and no request does.
MEMBER_LINE = (
    "This step did not run, because its tool was not ready in this chat. "
    "The assistant continues with the tools that it has."
)


def autoload_enabled() -> bool:
    """The one reader of ``CHAT_TOOL_AUTOLOAD``. A settings failure reads OFF."""
    try:
        from acb_common import get_settings

        return bool(getattr(get_settings(), "chat_tool_autoload", False))
    except Exception:
        return False


def is_raw(text: Any) -> str:
    """The tool name in MAF's own "not found" text *text*, or "".

    The text of the ``KeyError`` that MAF keeps in ``exception`` comes in one
    pair of single quotes, ``'Function "x" not found.'``. It counts too."""
    if not isinstance(text, str):
        return ""
    match = _RAW.match(text)
    return match.group(1) if match else ""


def unknown_call_name(content: Any) -> str:
    """The tool name when *content* is MAF's own result for a call of a name
    that the run did not hold, or "". Pure: no I/O.

    It reads ``result``, the text of ``items`` and ``exception``. After the
    rewrite of :class:`UnknownToolAnswer`, ``result`` and ``items`` hold the
    answer for the model, and ``exception`` still holds MAF's text. So a
    reader after the run, as a batch run is, still finds the call."""
    if getattr(content, "type", "function_result") != "function_result":
        return ""
    items = getattr(content, "items", None)
    texts = [getattr(i, "text", None) for i in items] if isinstance(items, (list, tuple)) else []
    exc = getattr(content, "exception", None)
    for text in (getattr(content, "result", None), *texts,
                 str(exc) if exc is not None else None):
        name = is_raw(text)
        if name:
            return name
    return ""


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


def _first_party(name: str) -> bool:
    """True when a pack of the platform holds a tool named *name*."""
    from acb_skills import tool_packs as tp

    return any(name in tuple(getattr(p, "tools", ()) or ()) for p in tp.PACKS.values())


def _first_party_names() -> set[str]:
    """The tool names of every pack of the platform."""
    from acb_skills import tool_packs as tp

    return {t for p in tp.PACKS.values() for t in tuple(getattr(p, "tools", ()) or ())}


@dataclass(frozen=True)
class Withheld:
    """HRD-1d: the MCP servers that ONE run held back on purpose, and why.

    *prefixes* holds the start of each tool name of a held server, as MAF
    makes it: the server name, then one ``_`` (``_maf_mcp.tool_prefix``).
    The orchestrator makes each one from the server config, never from a tool
    argument or the member's text. *reason* is a key of
    :data:`WITHHELD_TEXTS`."""

    prefixes: frozenset[str]
    reason: str

    def holds(self, name: str) -> bool:
        """True when *name* is a tool name of a held server.

        The name must start with the full prefix, ``_`` included, so a call of
        ``shopx_find`` never matches the server ``shop``. A name that a pack
        of the platform holds keeps the answer that it had before HRD-1d,
        because the tool of the platform is the more likely meaning."""
        if self.reason not in WITHHELD_TEXTS or not name:
            return False
        if not any(p and name.startswith(p) and len(name) > len(p) for p in self.prefixes):
            return False
        # A server may share its name with a family of the platform (``crm``).
        # A near miss of a tool of the platform then keeps its close-name
        # answer, so the model corrects itself in the same turn (review P2).
        return not _first_party(name) and not _closest(name, _first_party_names())


def answer_for(
    name: str, *, live: Iterable[str], offered: Iterable[str], held: Iterable[str] = (),
    gone: Iterable[str] = (), loaded: str = "", tried: str = "", load_answer: str = "",
    step: bool = False, withheld: Withheld | None = None,
) -> str:
    """The answer to a call of the unknown tool *name*. Pure: no I/O.

    *live* names the tools of the request. *offered* is the packs that
    ``load_skill_tools`` offers. *held* is the packs already loaded and
    *gone* the packs that left the chat. *loaded* names the pack that this
    turn has just loaded for this call, and *tried* a pack whose load
    refused. *load_answer* is the loader's own text, which goes after the
    first line: the rules of the pack, or the refusal with its next step.

    HRD-1b: *step* is True for a delegated run or a run with no chat. It
    reads *live* only, so the answer names a tool of the request or none.

    HRD-1d: *withheld* is the servers that the run held back on purpose. A
    name of one of them gets the text of its reason, in place of a close
    name or :data:`NOT_HERE`. A tool of the request and the exact name of
    an offered tool keep their answer. A step ignores *withheld*.
    """
    shown = _clean(name)
    live_names = set(live)
    if step:
        # A step reads no pack: no pack name and no load, whatever the caller passed.
        # HRD-1d: and no held server, so NOT_IN_STEP stays its answer.
        if name in live_names:
            return LIVE_NOW.format(name=shown)
        near = _closest(name, live_names)
        if near:
            return SUGGEST_LIVE.format(name=shown, near=_clean(near))
        return NOT_IN_STEP.format(name=shown)
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
    # HRD-1d: a tool of a server that this run held back on purpose. The text
    # says why, so the model does not tell the member that the tool is gone.
    if withheld is not None and withheld.holds(name):
        return WITHHELD_TEXTS[withheld.reason].format(name=shown)
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
    live list.

    HRD-1b: with *step*, it keeps no loader, reads no record of the packs
    and answers with ``answer_for(step=True)``.

    HRD-1d: *withheld* is the servers that the run held back on purpose. A
    call of one of their tools never asks the loader, so a server tool name
    can never load a pack with a close name. A step keeps no *withheld*."""

    def __init__(
        self, loader: Any = None, *, step: bool = False, withheld: Withheld | None = None,
    ) -> None:
        self._step = step
        self._loader = None if step else loader
        self._withheld = None if step else withheld
        #: The raw results that the first request held. The key is the call
        #: id, so a copy of a result keeps it. The list keeps each object
        #: alive, so the id of an object with no call id is not used twice.
        self._history: list[Any] | None = None
        self._history_keys: frozenset[str] = frozenset()
        self._last_tools: Any = None
        #: Task 3599: the names that this run tried to load, each with the
        #: slug and the text of a refused load ("" and "" after a load). A
        #: name gets ONE load try in a run, so the loop cannot spin the loader.
        self._tried: dict[str, tuple[str, str]] = {}

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
        if self._step:
            # HRD-1b: a step reads only the tools of the request.
            live = _live_names(options.get("tools") if isinstance(options, Mapping) else None)
            for content, name in hits:
                _set_text(content, answer_for(name, live=live, offered=(), step=True))
                _log.info("unknown_tool.answered", tool=_clean(name), step=True)
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
            # HRD-1d: a tool of a server that the run held back never asks
            # the loader. A close pack name is not what the model meant.
            held_back = self._withheld is not None and self._withheld.holds(name)
            repeat = name in self._tried
            if repeat:
                # Task 3599: the second call of a name in this run. The loader
                # is not asked again. A refusal of the first try stays the
                # answer, unless the name is in the tools now.
                if name not in _live_names(tools):
                    tried, load_answer = self._tried[name]
            elif (not held_back and _result_key(content) not in self._history_keys
                    and live_list is not None and loader is not None and autoload_enabled()):
                self._tried[name] = ("", "")
                try:
                    loaded, tried, load_answer = await self._autoload(name, offered, live_list)
                except Exception as exc:  # a failed load still gets the text
                    _log.warning("unknown_tool.autoload_failed", error_type=type(exc).__name__)
                self._tried[name] = (tried, load_answer)
            text = answer_for(
                name, live=_live_names(tools), offered=offered, held=held, gone=gone,
                loaded=loaded, tried=tried, load_answer=load_answer, withheld=self._withheld)
            _set_text(content, text)
            reason = self._withheld.reason if held_back and self._withheld is not None else ""
            shown = bool(reason) and text == WITHHELD_TEXTS[reason].format(name=_clean(name))
            _log.info("unknown_tool.answered", tool=_clean(name),
                      loaded=loaded or None, refused=tried or None, repeat=repeat or None,
                      **({"withheld": reason} if shown else {}))

    async def _autoload(
        self, name: str, offered: tuple[str, ...], live_list: list[Any],
    ) -> tuple[str, str, str]:
        """Load the offered pack that holds *name*, or the closest name.

        Gives ``(loaded, tried, answer)``. *loaded* is the slug when the run
        holds the pack after the call. *tried* is the slug when the loader
        refused. *answer* is the loader's own text: the refusal, or after a
        load the rules of the pack without the loader's first line, which
        tells the model to call the tools. The call goes through
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
            return slug, "", _without_load_line(answer)
        return "", slug, answer


def _without_load_line(answer: str) -> str:
    """*answer* of a load without its first paragraph, "Loaded the … tools
    (… tools). Call them from your next step." The first line of the HRD-1
    text says that the pack is loaded, and it orders no call."""
    from acb_skills import tool_packs as tp

    head = tp.LOADED.split("{label}", 1)[0]
    if answer.startswith(head) and "\n\n" in answer:
        return answer.split("\n\n", 1)[1]
    return answer


class UnknownToolProvider(ContextProvider):
    """Adds :class:`UnknownToolAnswer` to ONE run of a native MAF agent.

    HRD-1b: *step* is True for a delegated run or a run with no chat. Then
    it looks for no loader on the agent, so the answer never loads a pack.

    HRD-1d: :attr:`withheld` is None until :func:`note_withheld` sets it.
    The executor makes the provider before the MCP step decides what to hold
    back, so the value comes after the provider and before ``before_run``.
    With no value, the answer is the answer of HRD-1c, byte for byte."""

    SOURCE_ID = "unknown_tool_answer"

    def __init__(self, *, step: bool = False) -> None:
        super().__init__(self.SOURCE_ID)
        self.step = step
        #: HRD-1d: the servers that this run held back. Only a chat run reads it.
        self.withheld: Withheld | None = None

    async def before_run(
        self, *, agent: Any, session: Any, context: Any, state: dict[str, Any],
    ) -> None:
        if self.step:
            context.extend_middleware(self.SOURCE_ID, [UnknownToolAnswer(step=True)])
            return
        from acb_skills.tool_packs import LoadSkillToolsProvider

        loader = None
        for provider in getattr(agent, "context_providers", None) or []:
            if isinstance(provider, LoadSkillToolsProvider):
                loader = provider.loader
        answer = (UnknownToolAnswer(loader) if self.withheld is None
                  else UnknownToolAnswer(loader, withheld=self.withheld))
        context.extend_middleware(self.SOURCE_ID, [answer])


def _chat_provider(agent: Any) -> UnknownToolProvider | None:
    """The provider of a chat run (not a step) on the view *agent*, or None."""
    for provider in getattr(agent, "context_providers", None) or []:
        if isinstance(provider, UnknownToolProvider) and not provider.step:
            return provider
    return None


def reads_withheld(agent: Any) -> bool:
    """HRD-1d: True when the view *agent* holds a provider that reads a
    :class:`Withheld` value. A step, a run with no view and an agent that is
    not the first of the run hold none. Then the caller reads no registry."""
    return _chat_provider(agent) is not None


def note_withheld(agent: Any, prefixes: Iterable[str], *, reason: str) -> bool:
    """HRD-1d: hand the servers that this run held back to the provider of
    the view *agent*. *prefixes* is the start of each tool name of a held
    server (``_maf_mcp.tool_prefix``). The caller makes them from the server
    config. True when the provider took the value. An empty set, a reason
    with no text and a view with no chat provider change nothing."""
    kept = frozenset(p for p in prefixes if isinstance(p, str) and p)
    provider = _chat_provider(agent)
    if provider is None or not kept or reason not in WITHHELD_TEXTS:
        return False
    provider.withheld = Withheld(prefixes=kept, reason=reason)
    return True
