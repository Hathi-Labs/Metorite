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
place that sees the request's declared ``tools`` beside the vendor's answer.

## The rules

* **A call runs only if the REQUEST offered that tool.** Model text is not a
  trusted channel. A prompt-injected page could make the model write a DSML
  block for any name, so a name outside the request's ``tools`` is dropped,
  and so is every call when ``tool_choice`` is ``"none"``.
* **Markup never reaches the member.** A block that does not parse, a refused
  call, and an unterminated block are all removed from the text, and each one
  logs ``router.dsml_dropped``.
* **Ordinary text costs nothing.** A chunk with no ``<`` passes through as the
  same object or the same bytes. The stream holds back only a tail that could
  still grow into ``<｜DSML｜``, at most six characters.
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
    "DsmlStream",
    "ParsedText",
    "declared_tool_names",
    "extract_tool_calls",
    "normalise_response",
    "normalise_stream",
]

_log = logging.getLogger("platform.router")

#: The fullwidth vertical bar DeepSeek uses as its DSML delimiter.
BAR = "\uff5c"

#: Every DSML tag starts with this. Text that holds it is never shown.
OPEN_MARKER = f"<{BAR}DSML{BAR}"

#: The names a block tag may carry. The owner's report showed ``calls``, the
#: DeepSeek chat template writes ``function_calls``, and ``tool_calls`` is the
#: OpenAI spelling a model may drift to.
_BLOCK_NAMES = r"(?:function_calls|tool_calls|calls)"

_BLOCK_RE = re.compile(
    rf"<{BAR}DSML{BAR}(?P<kind>{_BLOCK_NAMES})\s*>(?P<body>.*?)</{BAR}DSML{BAR}(?P=kind)\s*>",
    re.S,
)
_CLOSE_RE = re.compile(rf"</{BAR}DSML{BAR}{_BLOCK_NAMES}\s*>")
_INVOKE_CLOSE_RE = re.compile(rf"</{BAR}DSML{BAR}invoke\s*>")
#: The first tag of a region: a block or an invoke opens one, anything else
#: (a stray closing tag, a lone parameter) is one tag long.
_FIRST_TAG_RE = re.compile(rf"<(?P<slash>/?){BAR}DSML{BAR}(?P<name>[A-Za-z_]+)[^>]*>")
#: Where a region may start: an opening tag, or a stray closing one.
_MARK_RE = re.compile(rf"</?{BAR}DSML{BAR}")
_MARKERS = (OPEN_MARKER, f"</{BAR}DSML{BAR}")
_OPEN_BLOCK_RE = re.compile(rf"<{BAR}DSML{BAR}{_BLOCK_NAMES}\b[^>]*>")
_OPEN_REGION_RE = re.compile(rf"<{BAR}DSML{BAR}(?:{_BLOCK_NAMES}|invoke)\b[^>]*>")
_INVOKE_RE = re.compile(
    rf"<{BAR}DSML{BAR}invoke(?P<attrs>[^>]*)>(?P<body>.*?)</{BAR}DSML{BAR}invoke\s*>",
    re.S,
)
_PARAM_RE = re.compile(
    rf"<{BAR}DSML{BAR}parameter(?P<attrs>[^>]*)>(?P<value>.*?)</{BAR}DSML{BAR}parameter\s*>",
    re.S,
)
_ATTR_RE = re.compile(r'([A-Za-z_][\w-]*)\s*=\s*"([^"]*)"')
#: Any single DSML tag, for the clean-up pass after the blocks are gone.
_ANY_TAG_RE = re.compile(rf"</?{BAR}DSML{BAR}[^>]*>")


class _Malformed(ValueError):
    """One invoke that cannot become a tool call."""


@dataclass
class ParsedText:
    """What one piece of assistant text holds once its DSML is read."""

    #: The text with every DSML block and stray tag removed.
    content: str
    #: OpenAI-shaped calls: ``{id, type: "function", function: {name, arguments}}``.
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    #: True when ANY markup was found, parsed or not.
    found: bool = False


def declared_tool_names(tools: Any, tool_choice: Any = None) -> frozenset[str]:
    """The tool names this request offered. Empty means none may run.

    ⚠️ ``tool_choice="none"`` empties the set. The caller offered the tools to
    describe them and forbade a call, and model text must not overrule that.
    A forced choice (``{"type": "function", "function": {"name": ...}}``)
    narrows the set to that one tool, for the same reason.
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
    forced = tool_choice.get("function") if isinstance(tool_choice, dict) else None
    if isinstance(forced, dict) and isinstance(forced.get("name"), str):
        names &= {forced["name"]}
    return frozenset(names)


def _attrs(raw: str) -> dict[str, str]:
    return {k: v for k, v in _ATTR_RE.findall(raw or "")}


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


def _calls_from_block(body: str, declared: frozenset[str]) -> list[dict[str, Any]]:
    """Every allowed call in one block. A bad invoke is dropped, not fatal."""
    invokes = list(_INVOKE_RE.finditer(body))
    if not invokes:
        _drop("block_without_invoke")
        return []
    if _INVOKE_RE.sub("", body).strip():
        _drop("block_holds_text_outside_invokes")
    return [c for c in (_call_from_invoke(inv, declared) for inv in invokes) if c is not None]


def extract_tool_calls(text: str, declared: frozenset[str]) -> ParsedText:
    """Read every DSML block out of *text*. Never raises.

    Returns the visible text and the calls. A text with no marker comes back
    as itself, with ``found`` False.
    """
    if not isinstance(text, str) or (
        OPEN_MARKER not in text and f"</{BAR}DSML{BAR}" not in text
    ):
        return ParsedText(content=text)
    calls: list[dict[str, Any]] = []

    def _take_block(match: re.Match[str]) -> str:
        calls.extend(_calls_from_block(match.group("body"), declared))
        return ""

    def _take_invoke(match: re.Match[str]) -> str:
        # An invoke with no block around it is still a call the model meant.
        call = _call_from_invoke(match, declared)
        if call is not None:
            calls.append(call)
        return ""

    rest = _BLOCK_RE.sub(_take_block, text)
    # A block that opens and never closes (a truncated answer) is dropped with
    # everything after it, complete invokes included. The stream does the
    # same, and a truncated block may have lost the call that mattered.
    unclosed = _OPEN_BLOCK_RE.search(rest)
    if unclosed is not None:
        _drop("unterminated_block")
        rest = rest[: unclosed.start()]
    rest = _INVOKE_RE.sub(_take_invoke, rest)
    # An invoke that opens and never closes never runs with half its arguments.
    unclosed = _OPEN_REGION_RE.search(rest)
    if unclosed is not None:
        _drop("unterminated_invoke")
        rest = rest[: unclosed.start()]
    if _ANY_TAG_RE.search(rest):
        _drop("stray_tag")
        rest = _ANY_TAG_RE.sub("", rest)
    if OPEN_MARKER in rest:
        # A bare marker with no closing ">" at the very end of the text.
        _drop("unterminated_tag")
        rest = rest[: rest.find(OPEN_MARKER)]
    return ParsedText(content=rest, tool_calls=calls, found=True)


# ── Buffered responses ──────────────────────────────────────────────────────


def _field(obj: Any, name: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _has_marker(response: Any) -> bool:
    for choice in _field(response, "choices") or []:
        content = _field(_field(choice, "message"), "content")
        if isinstance(content, str) and f"{BAR}DSML{BAR}" in content:
            return True
    return False


def normalise_response(response: Any, declared: frozenset[str]) -> Any:
    """Move DSML calls in a buffered completion into ``message.tool_calls``.

    ⚠️ **A response with no marker is returned as the SAME object**, so every
    other completion keeps its exact shape. Only a response that holds a
    marker is re-built as a dict, and FastAPI encodes a dict the same way.

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
        for choice in body.get("choices") or []:
            message = choice.get("message")
            if not isinstance(message, dict):
                continue
            parsed = extract_tool_calls(message.get("content"), declared)
            if not parsed.found:
                continue
            text = parsed.content if parsed.content.strip() else ""
            if parsed.tool_calls:
                existing = message.get("tool_calls") or []
                message["tool_calls"] = [*existing, *parsed.tool_calls]
                message["content"] = text or None
                if choice.get("finish_reason") in (None, "stop"):
                    choice["finish_reason"] = "tool_calls"
            else:
                message["content"] = text
        return body
    except Exception:
        _log.exception("router.dsml_normalise_failed")
        return response


# ── Streams ─────────────────────────────────────────────────────────────────


def _tail_that_may_grow(text: str) -> int:
    """How many trailing characters could still become :data:`OPEN_MARKER`."""
    best = 0
    for marker in _MARKERS:
        for k in range(min(len(marker) - 1, len(text)), best, -1):
            if text.endswith(marker[:k]):
                best = k
                break
    return best


@dataclass
class _Choice:
    held: str = ""
    block: str | None = None
    calls_out: int = 0
    native_next: int = 0
    finished: bool = False


class DsmlStream:
    """The per-stream state that turns text deltas into tool-call deltas.

    One instance per stream. :meth:`feed` takes one choice's text delta and
    returns what may be shown now, plus any calls that just completed.
    """

    def __init__(self, declared: frozenset[str]) -> None:
        self._declared = declared
        self._choices: dict[int, _Choice] = {}

    def _state(self, index: int) -> _Choice:
        return self._choices.setdefault(index, _Choice())

    @property
    def idle(self) -> bool:
        """No choice holds text back or owes a finish reason.

        A chunk with no ``<`` may then pass as is. A choice that sent a call
        and has not finished yet is NOT idle: its ``stop`` must still become
        ``tool_calls``.
        """
        return all(
            c.held == "" and c.block is None and (c.calls_out == 0 or c.finished)
            for c in self._choices.values()
        )

    def note_native(self, index: int, tool_calls: Any) -> None:
        """Remember the vendor's own call indexes, so ours never collide."""
        st = self._state(index)
        for call in tool_calls or []:
            i = _field(call, "index")
            if isinstance(i, int) and i + 1 > st.native_next:
                st.native_next = i + 1

    def feed(self, index: int, text: str) -> tuple[str, list[dict[str, Any]]]:
        st = self._state(index)
        shown: list[str] = []
        calls: list[dict[str, Any]] = []
        data = st.held + text
        st.held = ""
        while data:
            if st.block is None:
                mark = _MARK_RE.search(data)
                if mark is not None:
                    shown.append(data[: mark.start()])
                    st.block = ""
                    data = data[mark.start() :]
                    continue
                keep = _tail_that_may_grow(data)
                shown.append(data[: len(data) - keep])
                st.held = data[len(data) - keep :]
                break
            # Inside a region: swallow until it closes. The search starts a
            # little before the new text, because the closing tag itself can
            # be split across two deltas.
            start = max(0, len(st.block) - 32)
            st.block += data
            data = ""
            end = self._region_end(st.block, start)
            if end is None:
                break
            raw, data = st.block[:end], st.block[end:]
            st.block = None
            parsed = extract_tool_calls(raw, self._declared)
            shown.append(parsed.content)
            calls.extend(self._indexed(st, parsed.tool_calls))
        return "".join(shown), calls

    @staticmethod
    def _region_end(region: str, start: int) -> int | None:
        """Where the region that starts at :data:`OPEN_MARKER` ends, or None.

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

    def finish(self, index: int) -> str:
        """End one choice. Returns held text that turned out to be plain text."""
        st = self._state(index)
        st.finished = True
        out = st.held
        st.held = ""
        if st.block is not None:
            _drop("unterminated_block", dsml_chars=len(st.block))
            st.block = None
        return out

    def calls_emitted(self, index: int) -> int:
        return self._state(index).calls_out

    def pending(self) -> Iterable[int]:
        return [i for i, c in self._choices.items() if c.held or c.block is not None]

    @staticmethod
    def _indexed(st: _Choice, calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out = []
        for call in calls:
            out.append({"index": st.native_next + st.calls_out, **call})
            st.calls_out += 1
        return out


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


def _may_hold_marker_bytes(frame: bytes) -> bool:
    # Python's encoder never escapes `<`, but other encoders write it as a
    # `\u003c` escape. Either spelling sends the frame through the parser.
    return b"<" in frame or b"\\u003c" in frame.lower()


def _may_hold_marker_obj(chunk: Any) -> bool:
    for choice in _field(chunk, "choices") or []:
        content = _field(_field(choice, "delta"), "content")
        if isinstance(content, str) and "<" in content:
            return True
    return False


def _note_native(state: DsmlStream, chunk: Any) -> None:
    """Record the vendor's own call indexes from a chunk that passes as is."""
    for choice in _field(chunk, "choices") or []:
        calls = _field(_field(choice, "delta"), "tool_calls")
        if calls:
            index = _field(choice, "index")
            state.note_native(index if isinstance(index, int) else 0, calls)


def _rewrite(state: DsmlStream, body: dict[str, Any]) -> bool:
    """Apply the filter to one chunk dict in place. True when it changed."""
    changed = False
    for choice in body.get("choices") or []:
        if not isinstance(choice, dict):
            continue
        index = choice.get("index") if isinstance(choice.get("index"), int) else 0
        delta = choice.get("delta")
        if not isinstance(delta, dict):
            delta = {}
        state.note_native(index, delta.get("tool_calls"))
        content = delta.get("content")
        shown, calls = ("", [])
        if isinstance(content, str) and content:
            shown, calls = state.feed(index, content)
        finish = choice.get("finish_reason")
        if finish:
            shown += state.finish(index)
        if (isinstance(content, str) and shown != content) or (not isinstance(content, str) and shown):
            delta["content"] = shown
            changed = True
        if calls:
            delta["tool_calls"] = [*(delta.get("tool_calls") or []), *calls]
            changed = True
        if changed and delta and choice.get("delta") is not delta:
            choice["delta"] = delta
        if finish == "stop" and state.calls_emitted(index):
            choice["finish_reason"] = "tool_calls"
            changed = True
    return changed


def _flush_chunk(state: DsmlStream, last: dict[str, Any] | None) -> dict[str, Any] | None:
    """A closing chunk for text still held when the stream ended without a finish."""
    choices = []
    for index in state.pending():
        text = state.finish(index)
        if text:
            choices.append({"index": index, "delta": {"content": text}})
    if not choices:
        return None
    head = {k: last[k] for k in ("id", "object", "created", "model") if last and k in last}
    head.setdefault("object", "chat.completion.chunk")
    return {**head, "choices": choices}


async def normalise_stream(source: Any, declared: frozenset[str]) -> AsyncIterator[Any]:
    """Relay *source*, turning DSML text deltas into ``tool_calls`` deltas.

    Yields ONE chunk for each chunk in, so usage frames and finish reasons
    keep their place, plus at most one closing chunk.

    ⚠️ **A chunk that is not changed is yielded as the same object.** A byte
    frame stays the same bytes, which keeps ``relay_stream``'s byte-identity
    promise for every stream with no DSML in it. A changed object chunk is
    yielded as a dict, and ``frame_of`` already serialises a dict.
    """
    state = DsmlStream(declared)
    last: dict[str, Any] | None = None
    async for chunk in source:
        if isinstance(chunk, (bytes, bytearray)):
            frame = bytes(chunk)
            if frame.strip() == b"data: [DONE]":
                flush = _flush_chunk(state, last)
                if flush is not None:
                    yield _encode(flush)
                yield frame
                continue
            if state.idle and not _may_hold_marker_bytes(frame):
                if b"tool_calls" in frame:
                    _note_native(state, _frame_body(frame))
                yield frame
                continue
            body = _frame_body(frame)
            if body is None:
                yield frame
                continue
            last = body
            yield _encode(body) if _rewrite(state, body) else frame
            continue
        if state.idle and not _may_hold_marker_obj(chunk):
            _note_native(state, chunk)
            yield chunk
            continue
        dump = getattr(chunk, "model_dump", None)
        try:
            body = dump(mode="json", exclude_none=True) if callable(dump) else dict(chunk)
        except Exception:
            yield chunk
            continue
        last = body
        yield body if _rewrite(state, body) else chunk
    flush = _flush_chunk(state, last)
    if flush is not None:
        yield flush
