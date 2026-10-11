# The docstrings quote the vendor's markup, and its bar IS the fullwidth one.
# ruff: noqa: RUF002
"""Turn DeepSeek's DSML tool-call text into real tool calls.

Spec: ``customer_console.md`` §6A (the Router owns the vendor seam).

🔴 **A chat showed the member raw markup, and the tool never ran.** Owner
report, 2026-10-11. The Projects chat answered with this text where a tool
call belonged::

    <｜DSML｜calls> <｜DSML｜invoke name="update_task">
    <｜DSML｜parameter name="task_id" string="true">5ec5…</｜DSML｜parameter>
    </｜DSML｜invoke> </｜DSML｜calls>

DeepSeek sometimes writes its NATIVE tool-call format into ``content``, where
an OpenAI client expects structured ``tool_calls``. The delimiter is the
fullwidth bar U+FF5C, not ``|``. Nothing in the stack knew the format, so the
agent framework saw plain text, ran nothing, and showed the text.

## Why this lives in the Router

Production runs with ``ROUTER_SERVING_ENABLED=true`` (read on the box,
2026-10-11). So every chat completion crosses ``/v1/chat/completions`` here:
the agent runtimes through the gateway hop, and the in-product helpers through
``acb_llm.routed.completion_on_router``. The Router already normalises this
vendor's other quirk (:mod:`customer_console.reasoning`), and it is the one
place that sees the request beside the vendor's answer.

## The rules

Model text is NOT a trusted channel. An email, a web page or a tool result can
hold a DSML block, and a model that QUOTES it writes the block back out. So a
call runs only when all five of these hold:

1. **The request offered the tool** (:func:`declared_tool_names`).
   ``tool_choice="none"`` offers nothing, and a forced choice offers one tool.
2. **No input message holds DSML** (:func:`input_is_tainted`). If any input
   message carries the marker, escaped or not, the model may be echoing it,
   so nothing runs (``echoed_input``). Assistant turns count too.
3. **The block is not inside a code fence.** A fenced block is a quote. It
   stays visible and it never runs (``fenced``).
4. **The block is the trailing content.** The vendor template puts calls at
   the end of the answer. A block with prose after it is removed and never runs
   (``not_trailing``).
5. **The block parses**, and ``parallel_tool_calls=false`` keeps only one call.

**Markup outside a fence never reaches the member.** Each block that does not
run logs ``router.dsml_dropped`` with a ``dsml_reason``.

**Ordinary text costs nothing.** A chunk that the filter does not change is
yielded as the same object or the same bytes. The stream holds back only a
tail that could still grow into ``<｜DSML｜``, at most eight characters.
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "OPEN_MARKER",
    "DsmlPolicy",
    "DsmlStream",
    "ParsedText",
    "declared_tool_names",
    "extract_tool_calls",
    "input_is_tainted",
    "normalise_response",
    "normalise_stream",
    "policy_for",
]

_log = logging.getLogger("platform.router")

#: The fullwidth vertical bar DeepSeek uses as its DSML delimiter.
BAR = chr(0xFF5C)

#: The part every DSML tag shares. A message that holds it holds markup.
TAG_CORE = f"{BAR}DSML{BAR}"

#: Every opening DSML tag starts with this.
OPEN_MARKER = f"<{TAG_CORE}"

#: The names a block tag may carry. The owner's report showed ``calls``, the
#: DeepSeek chat template writes ``function_calls``, and ``tool_calls`` is the
#: OpenAI spelling a model may drift to.
_BLOCK_NAMES = r"(?:function_calls|tool_calls|calls)"

_BLOCK_RE = re.compile(
    rf"<{TAG_CORE}(?P<kind>{_BLOCK_NAMES})\s*>(?P<body>.*?)</{TAG_CORE}(?P=kind)\s*>",
    re.S,
)
_CLOSE_RE = re.compile(rf"</{TAG_CORE}{_BLOCK_NAMES}\s*>")
_INVOKE_CLOSE_RE = re.compile(rf"</{TAG_CORE}invoke\s*>")
#: The first tag of a region: a block or an invoke opens one, anything else
#: (a stray closing tag, a lone parameter) is one tag long.
_FIRST_TAG_RE = re.compile(rf"<(?P<slash>/?){TAG_CORE}(?P<name>[A-Za-z_]+)[^<>]*>")
#: Where a region may start: an opening tag, or a stray closing one.
_MARK_RE = re.compile(rf"</?{TAG_CORE}")
_MARKERS = (OPEN_MARKER, f"</{TAG_CORE}")
#: A real tag name starts with one of these. A marker followed by anything
#: else is plain text, not markup.
_TAG_START_RE = re.compile(r"[A-Za-z_]")
#: A region whose first tag has not closed within this many characters is
#: released as text. A real first tag is far shorter.
MAX_FIRST_TAG = 96
#: The start of a tag that has not reached its ``>`` yet.
_TAG_HEAD_RE = re.compile(
    rf'</?{TAG_CORE}[A-Za-z_]+(?:\s+[A-Za-z_][\w-]*(?:\s*=\s*(?:"[^"]*"?)?)?)*\s*'
)
_INVOKE_RE = re.compile(
    rf"<{TAG_CORE}invoke(?P<attrs>[^>]*)>(?P<body>.*?)</{TAG_CORE}invoke\s*>",
    re.S,
)
_PARAM_RE = re.compile(
    rf"<{TAG_CORE}parameter(?P<attrs>[^>]*)>(?P<value>.*?)</{TAG_CORE}parameter\s*>",
    re.S,
)
_ATTR_RE = re.compile(r'([A-Za-z_][\w-]*)\s*=\s*"([^"]*)"')
#: A fence line: any leading whitespace, then three or more ` or ~. Any
#: indent counts, so a fence inside a list item is found. That fails closed:
#: more text reads as a quote, and a quote never runs.
_FENCE_LINE_RE = re.compile(r"[ \t]*(`{3,}|~{3,})(.*)")
#: The marker in an input message, also when an encoder wrote the bar as the
#: six characters backslash, u, f, f, 5, c (once or twice escaped). Matched
#: against lowercased text.
_TAINT_RE = re.compile(rf"(?:{BAR}|\\+uff5c)dsml(?:{BAR}|\\+uff5c)")


class _Malformed(ValueError):
    """One invoke that cannot become a tool call."""


# ── What the request allows ─────────────────────────────────────────────────


@dataclass(frozen=True)
class DsmlPolicy:
    """What one request lets DSML text do."""

    #: The tool names a DSML call may use. Empty means none may run.
    declared: frozenset[str] = frozenset()
    #: An input message holds DSML, so the answer may echo it. Nothing runs.
    tainted: bool = False
    #: ``parallel_tool_calls=false``: at most ONE call in the turn.
    single_call: bool = False


def declared_tool_names(tools: Any, tool_choice: Any = None) -> frozenset[str]:
    """The tool names this request offered. Empty means none may run.

    ⚠️ ``tool_choice="none"`` empties the set. The caller offered the tools to
    describe them and forbade a call, and model text must not overrule that.
    A forced choice (``{"type": "function", "function": {"name": ...}}``)
    narrows the set to that one tool, for the same reason. Both the Chat
    Completions shape (``{"function": {"name": ...}}``) and the Responses
    shape (``{"type": "function", "name": ...}``) are read.
    """
    if tool_choice == "none" or not isinstance(tools, list):
        return frozenset()
    names: set[str] = set()
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        fn = tool.get("function")
        name = fn.get("name") if isinstance(fn, dict) else tool.get("name")
        if isinstance(name, str) and name:
            names.add(name)
    if isinstance(tool_choice, dict):
        forced = tool_choice.get("function")
        forced_name = forced.get("name") if isinstance(forced, dict) else tool_choice.get("name")
        if isinstance(forced_name, str):
            names &= {forced_name}
    return frozenset(names)


def _texts(content: Any) -> Iterable[str]:
    if isinstance(content, str):
        yield content
    elif isinstance(content, list):
        for part in content:
            if isinstance(part, str):
                yield part
            elif isinstance(part, dict) and isinstance(part.get("text"), str):
                yield part["text"]


def _message_texts(message: dict[str, Any]) -> Iterable[str]:
    yield from _texts(message.get("content"))
    for call in message.get("tool_calls") or []:
        fn = call.get("function") if isinstance(call, dict) else None
        if isinstance(fn, dict) and isinstance(fn.get("arguments"), str):
            yield fn["arguments"]


def input_is_tainted(messages: Any) -> bool:
    """True when ANY input message holds DSML markup, escaped or not.

    🔴 **The echo attack.** An email body holds a DSML block. The member asks
    to see the email, the model quotes it, and a Router that read every block
    would run the quoted call. A request whose INPUT already carries the
    marker cannot tell an echo from a call, so it runs none.

    🔴 **Assistant turns count too (re-review of #851).** Tool messages do
    not reach the next turn (``acb_llm.assemble_run_context`` and the
    gateway's store loader keep user, assistant and system turns). So turn 1
    can quote the email inside a fence, and turn 2 can ask for it "again, as
    plain text" with no tool message left to taint it. After this fix, DSML
    in a stored assistant turn is a fenced quote or a leak from before the
    fix. Both are reasons to run nothing.

    ⚠️ **Escaped markup counts.** ``json.dumps`` writes the bar as six
    characters, and a model that quotes the text decodes them.
    """
    if not isinstance(messages, list):
        return False
    for message in messages:
        if not isinstance(message, dict):
            continue
        for text in _message_texts(message):
            low = text.lower()
            if "dsml" in low and _TAINT_RE.search(low):
                return True
    return False


def policy_for(
    *,
    tools: Any,
    tool_choice: Any = None,
    parallel_tool_calls: Any = None,
    messages: Any = None,
) -> DsmlPolicy:
    """The one place a request becomes a :class:`DsmlPolicy`."""
    return DsmlPolicy(
        declared=declared_tool_names(tools, tool_choice),
        tainted=input_is_tainted(messages),
        single_call=parallel_tool_calls is False,
    )


def _as_policy(policy: DsmlPolicy | frozenset[str]) -> DsmlPolicy:
    return policy if isinstance(policy, DsmlPolicy) else DsmlPolicy(declared=frozenset(policy))


# ── One region of markup ────────────────────────────────────────────────────


def _attrs(raw: str) -> dict[str, str]:
    return dict(_ATTR_RE.findall(raw or ""))


def _value(raw: str, string_flag: str | None) -> Any:
    """One parameter's value. ``string="true"`` is raw text, ``"false"`` is JSON."""
    if string_flag == "true":
        return raw
    if string_flag == "false":
        try:
            return json.loads(raw)
        except ValueError as exc:
            raise _Malformed(f"parameter is not JSON: {exc}") from exc
    # No flag: JSON when it parses, otherwise the text itself.
    try:
        return json.loads(raw)
    except ValueError:
        return raw


def _invoke_to_call(attrs: str, body: str) -> tuple[str, dict[str, Any]]:
    name = _attrs(attrs).get("name", "").strip()
    if not name:
        raise _Malformed("invoke has no name")
    args: dict[str, Any] = {}
    for param in _PARAM_RE.finditer(body):
        pattrs = _attrs(param.group("attrs"))
        pname = pattrs.get("name", "").strip()
        if not pname:
            raise _Malformed("parameter has no name")
        args[pname] = _value(param.group("value"), pattrs.get("string"))
    # Text between parameters that is not whitespace is a shape we do not know.
    if _PARAM_RE.sub("", body).strip():
        raise _Malformed("invoke holds text outside its parameters")
    return name, args


def _new_call(name: str, args: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": f"call_{uuid.uuid4().hex[:24]}",
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)},
    }


def _drop(reason: str, **extra: Any) -> None:
    _log.warning("router.dsml_dropped", extra={"dsml_reason": reason, **extra})


def _call_from_invoke(inv: re.Match[str], declared: frozenset[str]) -> dict[str, Any] | None:
    """One invoke as a call, or None when it is malformed or not allowed."""
    try:
        name, args = _invoke_to_call(inv.group("attrs"), inv.group("body"))
    except _Malformed as exc:
        _drop("malformed_invoke", dsml_detail=str(exc)[:200])
        return None
    if name not in declared:
        # 🔴 The prompt-injection fence. See the module docstring.
        _drop("undeclared_tool", dsml_tool=name[:100])
        return None
    return _new_call(name, args)


def _calls_from_region(raw: str, policy: DsmlPolicy) -> list[dict[str, Any]]:
    """The calls one closed region asks for, after the request's rules."""
    first = _FIRST_TAG_RE.match(raw)
    if first is None or first.group("slash") or first.group("name") not in (
        "invoke", "calls", "function_calls", "tool_calls"
    ):
        _drop("stray_tag")
        return []
    if policy.tainted:
        _drop("echoed_input")
        return []
    if first.group("name") == "invoke":
        inv = _INVOKE_RE.match(raw)
        if inv is None:
            _drop("malformed_invoke", dsml_detail="unreadable invoke")
            return []
        call = _call_from_invoke(inv, policy.declared)
        return [call] if call is not None else []
    match = _BLOCK_RE.match(raw)
    if match is None:
        _drop("malformed_block")
        return []
    body = match.group("body")
    invokes = list(_INVOKE_RE.finditer(body))
    if not invokes:
        _drop("block_without_invoke")
        return []
    if _INVOKE_RE.sub("", body).strip():
        _drop("block_holds_text_outside_invokes")
    found = (_call_from_invoke(inv, policy.declared) for inv in invokes)
    return [c for c in found if c is not None]


# ── The state machine ───────────────────────────────────────────────────────


def _not_a_tag(region: str) -> bool:
    """True when the text after a marker can no longer become a DSML tag.

    A first tag that closes within :data:`MAX_FIRST_TAG` characters is a tag.
    Text that has no ``>`` yet is still a candidate only while it reads like
    the head of one: a name, then ``attr="value"`` pairs.
    """
    first = _FIRST_TAG_RE.match(region)
    if first is not None:
        return first.end() > MAX_FIRST_TAG
    return len(region) > MAX_FIRST_TAG or _TAG_HEAD_RE.fullmatch(region) is None


def _tail_that_may_grow(text: str) -> int:
    """How many trailing characters could still become a DSML marker."""
    best = 0
    for marker in _MARKERS:
        for k in range(min(len(marker), len(text)), best, -1):
            if text.endswith(marker[:k]):
                best = k
                break
    return best


@dataclass
class _Fence:
    """Tracks whether the text shown so far sits inside a code fence."""

    open: str | None = None
    line: str = ""
    #: How many fences have opened, so a quote logs once per fence.
    opened: int = 0

    def push(self, text: str) -> None:
        parts = text.split("\n")
        for i, part in enumerate(parts):
            if i:
                self._end_line()
            if len(self.line) < 200:
                self.line += part[: 200 - len(self.line)]

    def _end_line(self) -> None:
        match = _FENCE_LINE_RE.fullmatch(self.line)
        self.line = ""
        if match is None:
            return
        run, rest = match.group(1), match.group(2)
        if self.open is None:
            # A backtick fence may not hold a backtick in its info string.
            if not (run[0] == "`" and "`" in rest):
                self.open = run
                self.opened += 1
        elif run[0] == self.open[0] and len(run) >= len(self.open) and not rest.strip():
            self.open = None

    @property
    def inside(self) -> bool:
        return self.open is not None


@dataclass
class _Choice:
    held: str = ""
    region: str | None = None
    #: Calls from the trailing group, held until the choice finishes.
    pending: list[dict[str, Any]] = field(default_factory=list)
    native_next: int = 0
    calls_out: int = 0
    finished: bool = False
    saw_markup: bool = False
    fence: _Fence = field(default_factory=_Fence)
    #: The fence (by its count) whose quoted markup was already logged.
    fence_logged: int = 0


class DsmlStream:
    """The per-stream state that turns DSML text into tool calls.

    One instance per stream (or per buffered answer). :meth:`feed` takes one
    choice's text and returns what may be shown now. :meth:`finish` ends the
    choice and returns the calls that may run.

    ⚠️ **Calls leave only at the finish.** A block runs only if it is the
    trailing content, and that is not known until the text ends. Holding the
    calls also means their indexes follow every vendor call in the turn.
    """

    def __init__(self, policy: DsmlPolicy | frozenset[str]) -> None:
        self._policy = _as_policy(policy)
        self._choices: dict[int, _Choice] = {}

    def _state(self, index: int) -> _Choice:
        return self._choices.setdefault(index, _Choice())

    def note_native(self, index: int, tool_calls: Any) -> None:
        """Remember the vendor's own call indexes, so ours never collide."""
        st = self._state(index)
        for call in tool_calls or []:
            i = _field(call, "index")
            if not isinstance(i, int):
                i = st.native_next
            if i + 1 > st.native_next:
                st.native_next = i + 1

    def saw_markup(self, index: int) -> bool:
        return self._state(index).saw_markup

    def _show(self, st: _Choice, text: str, out: list[str]) -> None:
        if not text:
            return
        out.append(text)
        st.fence.push(text)
        if st.pending and text.strip():
            # 🔴 Prose after a block: the block was not the trailing content.
            _drop("not_trailing", dsml_calls=len(st.pending))
            st.pending = []

    def feed(self, index: int, text: str) -> str:
        """Take one text delta. Returns the text that may be shown now."""
        st = self._state(index)
        out: list[str] = []
        data = st.held + text
        st.held = ""
        while data:
            if st.region is None:
                data = self._outside(st, data, out)
                continue
            start = max(0, len(st.region) - 32)
            st.region += data
            data = ""
            if _not_a_tag(st.region):
                # No tag formed: the marker was text after all. Show the
                # marker and scan the rest again, so a later real block in
                # the same text is still found.
                data, st.region = st.region, None
                lead = _MARK_RE.match(data)
                cut = lead.end() if lead else 1
                self._show(st, data[:cut], out)
                data = data[cut:]
                continue
            end = self._region_end(st.region, start)
            if end is None:
                break
            raw, data = st.region[:end], st.region[end:]
            st.region = None
            st.pending.extend(_calls_from_region(raw, self._policy))
        return "".join(out)

    def _outside(self, st: _Choice, data: str, out: list[str]) -> str:
        """Scan text outside any region. Returns what is left to scan."""
        mark = _MARK_RE.search(data)
        if mark is None:
            keep = _tail_that_may_grow(data)
            self._show(st, data[: len(data) - keep], out)
            st.held = data[len(data) - keep :]
            return ""
        self._show(st, data[: mark.start()], out)
        after = data[mark.end() :]
        if not after:
            st.held = data[mark.start() :]
            return ""
        if st.fence.inside or not _TAG_START_RE.match(after):
            # A quote inside a fence, or a marker with no tag name: text.
            if st.fence.inside and _TAG_START_RE.match(after) and (
                st.fence_logged != st.fence.opened
            ):
                # Logged once per fence: the quote is visible, and it never runs.
                st.fence_logged = st.fence.opened
                _drop("fenced")
            self._show(st, mark.group(0), out)
            return after
        st.region = ""
        st.saw_markup = True
        return data[mark.start() :]

    @staticmethod
    def _region_end(region: str, start: int) -> int | None:
        """Where the region that starts at a marker ends, or None.

        A block tag waits for its closing block tag, and an invoke tag for its
        closing invoke tag. Any other tag is a region of its own, so a stray
        tag can never swallow the rest of the answer.
        """
        first = _FIRST_TAG_RE.match(region)
        if first is None:
            return None
        if first.group("slash"):
            return first.end()
        name = first.group("name")
        if re.fullmatch(_BLOCK_NAMES, name):
            closer = _CLOSE_RE
        elif name == "invoke":
            closer = _INVOKE_CLOSE_RE
        else:
            return first.end()
        close = closer.search(region, max(start, first.end()))
        return close.end() if close is not None else None

    def finish(self, index: int) -> tuple[str, list[dict[str, Any]]]:
        """End one choice. Returns the last text and the calls that may run."""
        st = self._state(index)
        out: list[str] = []
        if st.held:
            held, st.held = st.held, ""
            self._show(st, held, out)
        if st.region is not None:
            _drop("unterminated_block", dsml_chars=len(st.region))
            st.region = None
        calls, st.pending = st.pending, []
        if self._policy.single_call:
            room = max(0, 1 - st.native_next)
            if len(calls) > room:
                _drop("parallel_disabled", dsml_calls=len(calls) - room)
                calls = calls[:room]
        st.finished = True
        return "".join(out), calls

    def indexed(self, index: int, calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Give each call its stream index, after every vendor call."""
        st = self._state(index)
        out = []
        for call in calls:
            out.append({"index": st.native_next + st.calls_out, **call})
            st.calls_out += 1
        return out

    def calls_emitted(self, index: int) -> int:
        return self._state(index).calls_out

    def unfinished(self) -> list[int]:
        return [i for i, c in self._choices.items() if not c.finished]


# ── Buffered text and responses ─────────────────────────────────────────────


@dataclass
class ParsedText:
    """What one piece of assistant text holds once its DSML is read."""

    #: The text with every unfenced DSML region removed.
    content: str
    #: OpenAI-shaped calls: ``{id, type: "function", function: {name, arguments}}``.
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    #: True when markup outside a fence was found, parsed or not.
    found: bool = False


def extract_tool_calls(
    text: str, policy: DsmlPolicy | frozenset[str], *, native: int = 0
) -> ParsedText:
    """Read the DSML out of one whole answer. Never raises.

    It runs the SAME machine as the stream, in one step, so the buffered and
    the streamed path cannot disagree. ``native`` is the number of calls the
    vendor already made in this turn.
    """
    if not isinstance(text, str) or TAG_CORE not in text:
        return ParsedText(content=text)
    machine = DsmlStream(policy)
    machine.note_native(0, [{"index": i} for i in range(native)])
    shown = machine.feed(0, text)
    tail, calls = machine.finish(0)
    return ParsedText(content=shown + tail, tool_calls=calls, found=machine.saw_markup(0))


def _field(obj: Any, name: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _has_marker(response: Any) -> bool:
    for choice in _field(response, "choices") or []:
        content = _field(_field(choice, "message"), "content")
        if isinstance(content, str) and TAG_CORE in content:
            return True
    return False


def normalise_response(response: Any, policy: DsmlPolicy | frozenset[str]) -> Any:
    """Move DSML calls in a buffered completion into ``message.tool_calls``.

    ⚠️ **A response with no unfenced marker is returned as the SAME object**,
    so every other completion keeps its exact shape. Only a response that the
    filter changes is re-built as a dict, and FastAPI encodes a dict the same
    way.

    ⚠️ **Swallows its own failures.** A shape we cannot walk is a reason to
    leave the text alone, never a reason to lose a paid completion.
    """
    try:
        if not _has_marker(response):
            return response
        dump = getattr(response, "model_dump", None)
        body: dict[str, Any] = (
            dump(mode="json") if callable(dump) else json.loads(json.dumps(response, default=str))
        )
        changed = False
        for choice in body.get("choices") or []:
            message = choice.get("message")
            if not isinstance(message, dict):
                continue
            existing = message.get("tool_calls") or []
            parsed = extract_tool_calls(message.get("content"), policy, native=len(existing))
            if not parsed.found:
                continue
            changed = True
            text = parsed.content if parsed.content.strip() else ""
            if parsed.tool_calls:
                message["tool_calls"] = [*existing, *parsed.tool_calls]
                message["content"] = text or None
                if choice.get("finish_reason") in (None, "stop"):
                    choice["finish_reason"] = "tool_calls"
            else:
                message["content"] = text
        return body if changed else response
    except Exception:
        _log.exception("router.dsml_normalise_failed")
        return response


# ── Streams ─────────────────────────────────────────────────────────────────


_DATA = b"data:"


def _frame_body(frame: bytes) -> dict[str, Any] | None:
    line = frame.strip()
    if not line.startswith(_DATA):
        return None
    raw = line[len(_DATA) :].strip()
    if not raw or raw == b"[DONE]":
        return None
    try:
        body = json.loads(raw)
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


def _encode(body: dict[str, Any]) -> bytes:
    return b"data: " + json.dumps(body, ensure_ascii=False).encode("utf-8") + b"\n\n"


def _plan(state: DsmlStream, chunk: Any) -> dict[int, dict[str, Any]]:
    """Run one chunk through the machine. Returns the edits, by choice position.

    Reads the chunk without copying it, so a chunk that needs no edit costs
    one attribute walk and nothing else.
    """
    edits: dict[int, dict[str, Any]] = {}
    for pos, choice in enumerate(_field(chunk, "choices") or []):
        index = _field(choice, "index")
        index = index if isinstance(index, int) else 0
        delta = _field(choice, "delta")
        state.note_native(index, _field(delta, "tool_calls"))
        content = _field(delta, "content")
        shown = state.feed(index, content) if isinstance(content, str) and content else ""
        calls: list[dict[str, Any]] = []
        finish = _field(choice, "finish_reason")
        if finish:
            tail, ready = state.finish(index)
            shown += tail
            calls = state.indexed(index, ready)
        edit: dict[str, Any] = {}
        if (isinstance(content, str) and shown != content) or (
            not isinstance(content, str) and shown
        ):
            edit["content"] = shown
        if calls:
            edit["tool_calls"] = calls
        if finish == "stop" and state.calls_emitted(index):
            edit["finish_reason"] = "tool_calls"
        if edit:
            edits[pos] = edit
    return edits


def _apply(body: dict[str, Any], edits: dict[int, dict[str, Any]]) -> None:
    choices = body.get("choices") or []
    for pos, edit in edits.items():
        choice = choices[pos]
        delta = choice.get("delta")
        if not isinstance(delta, dict):
            delta = choice["delta"] = {}
        if "content" in edit:
            delta["content"] = edit["content"]
        if "tool_calls" in edit:
            delta["tool_calls"] = [*(delta.get("tool_calls") or []), *edit["tool_calls"]]
        if "finish_reason" in edit:
            choice["finish_reason"] = edit["finish_reason"]


def _plain(obj: Any) -> Any:
    """A JSON-safe copy of one vendor object, best-effort."""
    if obj is None or isinstance(obj, (dict, list, str, int, float, bool)):
        return obj
    try:
        return json.loads(json.dumps(obj, default=lambda o: getattr(o, "__dict__", str(o))))
    except Exception:
        return None


def _minimal(chunk: Any) -> dict[str, Any]:
    """Rebuild a chunk that cannot copy itself, from the fields a client reads."""
    choices = []
    for choice in _field(chunk, "choices") or []:
        delta = _field(choice, "delta")
        out: dict[str, Any] = {}
        role = _field(delta, "role")
        if isinstance(role, str):
            out["role"] = role
        content = _field(delta, "content")
        if isinstance(content, str):
            out["content"] = content
        native = _field(delta, "tool_calls")
        if native:
            out["tool_calls"] = [_plain(c) for c in native]
        index = _field(choice, "index")
        choices.append({
            "index": index if isinstance(index, int) else 0,
            "delta": out,
            "finish_reason": _field(choice, "finish_reason"),
        })
    body: dict[str, Any] = {"object": "chat.completion.chunk", **_head(chunk), "choices": choices}
    usage = _plain(_field(chunk, "usage"))
    if isinstance(usage, dict):
        body["usage"] = usage
    return body


def _head(chunk: Any) -> dict[str, Any]:
    return {k: v for k in ("id", "object", "created", "model") if (v := _field(chunk, k))}


def _flush_chunk(state: DsmlStream, head: dict[str, Any] | None) -> dict[str, Any] | None:
    """A closing chunk for a stream that ended without a finish reason."""
    choices = []
    for index in state.unfinished():
        text, ready = state.finish(index)
        calls = state.indexed(index, ready)
        if not text and not calls:
            continue
        delta: dict[str, Any] = {}
        if text:
            delta["content"] = text
        if calls:
            delta["tool_calls"] = calls
        choices.append({"index": index, "delta": delta})
    if not choices:
        return None
    return {"object": "chat.completion.chunk", **(head or {}), "choices": choices}


async def normalise_stream(
    source: Any, policy: DsmlPolicy | frozenset[str]
) -> AsyncIterator[Any]:
    """Relay *source*, turning DSML text deltas into ``tool_calls`` deltas.

    Yields ONE chunk for each chunk in, so usage frames and finish reasons
    keep their place, plus at most one closing chunk before the end.

    ⚠️ **A chunk that is not changed is yielded as the same object.** A byte
    frame stays the same bytes, which keeps ``relay_stream``'s byte-identity
    promise for every stream with no DSML in it. A changed object chunk is
    yielded as a dict, and ``frame_of`` already serialises a dict.
    """
    state = DsmlStream(policy)
    head: dict[str, Any] | None = None
    async for chunk in source:
        raw = isinstance(chunk, (bytes, bytearray))
        if raw:
            frame = bytes(chunk)
            if frame.strip() == b"data: [DONE]":
                flush = _flush_chunk(state, head)
                if flush is not None:
                    yield _encode(flush)
                yield frame
                continue
            view: Any = _frame_body(frame)
            if view is None:
                yield frame
                continue
        else:
            view = chunk
        edits = _plan(state, view)
        head = _head(view) or head
        if not edits:
            yield chunk
            continue
        if raw:
            _apply(view, edits)
            yield _encode(view)
            continue
        dump = getattr(chunk, "model_dump", None)
        try:
            body = dump(mode="json", exclude_none=True) if callable(dump) else dict(chunk)
        except Exception:
            # 🔴 The machine has ALREADY taken this chunk, so the original
            # must not go out: it would show the raw markup, and held text
            # twice. A minimal copy carries the planned edits instead.
            _log.exception("router.dsml_chunk_unreadable")
            body = _minimal(chunk)
        _apply(body, edits)
        yield body
    flush = _flush_chunk(state, head)
    if flush is not None:
        yield flush
