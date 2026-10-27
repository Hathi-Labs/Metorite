"""A large tool result as a preview and a handle (CTX-1, Metorite #825).

Spec: ``project-docs/specs/one_assistant_context.md`` §7.1.

A model call sends each earlier tool result again, and the stored session
sends it in every later turn. A mail thread, a CRM list or an MCP page with no
cap can be 100K characters. Behind ``TOOL_RESULT_SPILL`` (``off``, ``shadow``
or ``on``, default ``off``), one per-run :class:`SpillToolResults` middleware
keeps a result above ``TOOL_RESULT_SPILL_CHARS`` in this process and gives the
model a preview, a handle (``tr_<8 hex>``) and one footer line. The tool
:func:`read_tool_result` reads the rest in pages.

Rules that bind:

1. **No lasting copy.** The store is an in-process LRU with a TTL, as the
   dropped-list cache of ``narrowing`` is. It writes no ``agent_blob`` row and
   no artifact, because a lasting copy of mail breaks the rule of
   ``test_mail_copies_in_chat_stores.py``.
2. **Tenancy (R5).** The key is the run's org, member and thread plus the ref.
   All three come from the run binding. No tool argument names them, and a
   run with a missing part keeps nothing. Another key finds "not found".
3. **Untrusted text stays framed.** An entry is untrusted when its text holds
   a marker shape, when its tool is an MCP tool, when its pack owner reads
   stranger text, or when the session is marked. A failed check counts as
   marked. The preview closes an open frame with that frame's own END line.
   A read of an untrusted entry marks the session first, then frames each page
   in a NEW :class:`UntrustedFrame`.
4. **The number check sees the full text.** The provider goes first in the
   provider list, so this middleware is outermost. ``RecordToolResults`` reads
   the full result inside it, and only then does the spill replace it.
5. **Exempt:** ``narrow_and_read`` (it bounds its own output), the delegation
   tools (their own cap), the tools that wait for the member, and the read
   tool itself.

The logs hold the tool name, counts and flags. They hold no result text.
"""
from __future__ import annotations

import re
import secrets
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

from acb_common import get_logger
from agent_framework import ContextProvider, FunctionMiddleware

from acb_skills.egress import (
    UNTRUSTED_INPUT_AGENTS,
    _from_mcp,
    is_delegation_tool,
    mark_untrusted_answer,
    session_is_marked,
)
from acb_skills.tool_guard import tool_name
from acb_skills.untrusted import MARKER_LINE_RE, UntrustedFrame, has_marker_shape

_log = get_logger("acb_skills.tool_result_spill")

SOURCE_ID = "metorite-tool-result-spill"

#: A stored result lives this long in the gateway process.
TTL_S = 30 * 60

#: The most results the process keeps. The oldest goes first.
MAX_ENTRIES = 128

#: The most characters of one result that the store keeps.
MAX_STORED_CHARS = 2_000_000

#: The size of one page of :func:`read_tool_result`.
PAGE_CHARS = 16_000

#: The preview keeps at least this many characters of the head exactly as the
#: tool returned them, because the stream cuts a tool result there
#: (``event_translator``, 2000 characters).
PREVIEW_FLOOR = 2000

#: Tools that never spill (rule 5). The others are the delegation tools,
#: which :func:`is_delegation_tool` names.
READ_TOOL = "read_tool_result"
EXEMPT_TOOLS: frozenset[str] = frozenset({
    "narrow_and_read", READ_TOOL,
    # They wait for the member, and their result is the member's answer.
    "ask_questions", "request_confirmation",
})

_REF = re.compile(r"^tr_[0-9a-f]{8}$")

NOT_FOUND = (
    "That stored result was not found. It is gone, or it never existed in this "
    "chat. A stored result lives for 30 minutes in this chat only. Call the "
    "tool again."
)
BAD_OFFSET = "read_tool_result: offset must be 0 or more."

FRAME_LABEL = "TOOL RESULT"


@dataclass(frozen=True)
class _Entry:
    expiry: float
    text: str
    untrusted: bool
    tool: str
    total: int


#: (org, member, thread, ref) -> the entry.
_STORE: OrderedDict[tuple[str, str, str, str], _Entry] = OrderedDict()


def _owner_key() -> tuple[str, str, str] | None:
    """The run's org, member and thread, from the run binding only (R5).

    It reads what ``narrowing._owner_key`` reads. ``None`` when any part is
    missing, so nothing is kept or found.
    """
    try:
        from acb_common.db import current_tenant
        from acb_llm.routed import run_attribution

        from acb_skills.write_artifact import artifact_context

        org = str(current_tenant() or "").strip()
        member = str(run_attribution().get("member") or "").strip()
        ctx = artifact_context() or {}
        thread = str(ctx.get("session_id") or "").strip()
        if not thread:
            from acb_common import get_run_context

            thread = str((get_run_context() or {}).get("thread_id") or "").strip()
    except Exception:
        return None
    if not (org and member and thread):
        return None
    return org, member, thread


def _sweep(now: float) -> None:
    for key in [k for k, e in _STORE.items() if e.expiry <= now]:
        _STORE.pop(key, None)


def clear_store() -> None:
    """Drop every stored result. For a test."""
    _STORE.clear()


def _keep(owner: tuple[str, str, str], text: str, untrusted: bool, tool: str) -> str:
    now = time.monotonic()
    _sweep(now)
    ref = "tr_" + secrets.token_hex(4)
    _STORE[(*owner, ref)] = _Entry(
        now + TTL_S, text[0:MAX_STORED_CHARS], untrusted, tool, len(text))
    while len(_STORE) > MAX_ENTRIES:
        _STORE.popitem(last=False)
    return ref


# ── The text of a result ─────────────────────────────────────────────────────

def _plain_text(result: Any) -> str | None:
    """The text of a result that is ALL text, or ``None``.

    A string, or a list of text contents. Anything else (an image, a card, an
    approval request) stays as it is.
    """
    if isinstance(result, str):
        return result
    if isinstance(result, (list, tuple)) and result:
        parts: list[str] = []
        for item in result:
            if isinstance(item, str):
                parts.append(item)
            elif getattr(item, "type", "") == "text" and isinstance(
                    getattr(item, "text", None), str):
                parts.append(item.text)
            else:
                return None
        return "\n".join(parts)
    return None


def _is_delegation(function: Any) -> bool:
    """A delegation tool, by its name or by the ``__delegates_to__`` mark of
    the callable that a ``FunctionTool`` wraps."""
    inner = getattr(function, "func", None)
    return is_delegation_tool(function) or (inner is not None and is_delegation_tool(inner))


def _source(function: Any) -> str:
    if _from_mcp(function):
        return "mcp"
    if _pack_owner(function):
        return "pack"
    return "core"


def _pack_owner(function: Any) -> str:
    for item in (function, getattr(function, "func", None)):
        owner = getattr(item, "__cc_pack_owner__", None)
        if owner:
            return str(owner)
    return ""


def _sync_untrusted(function: Any, text: str) -> bool:
    return (
        has_marker_shape(text)
        or _from_mcp(function)
        or _pack_owner(function) in UNTRUSTED_INPUT_AGENTS
    )


async def _untrusted(function: Any, text: str) -> bool:
    """True when the entry counts as stranger text. A failed check is True."""
    if _sync_untrusted(function, text):
        return True
    try:
        return await session_is_marked()
    except Exception:
        return True


# ── The preview ──────────────────────────────────────────────────────────────

def _open_frames(text: str) -> list[tuple[str, str]]:
    """The frames that *text* opens and does not close, outermost first."""
    stack: list[tuple[str, str]] = []
    for line in text.splitlines():
        match = MARKER_LINE_RE.match(line.strip())
        if not match:
            continue
        ends, label, token = match.groups()
        if ends:
            if (label, token) in stack:
                while stack and stack.pop() != (label, token):
                    pass
        else:
            stack.append((label, token))
    return stack


def _cut_point(text: str, limit: int) -> int:
    """The end of the preview: *limit*, moved back to a line end if one lies
    between the floor and the limit."""
    if len(text) <= limit:
        return len(text)
    floor = min(PREVIEW_FLOOR, limit)
    at = text.rfind("\n", floor, limit)
    return at + 1 if at != -1 else limit


def _footer(ref: str, text_len: int, text: str, shown: int, stored: int) -> str:
    tokens = round(text_len / 4, -2)
    lines = text.count("\n") + 1
    note = ""
    if stored < text_len:
        note = f" Only the first {stored:,} chars are stored."
    return (
        f"[Stored result {ref}: {text_len:,} chars, ~{int(tokens):,} tokens, "
        f"{lines:,} lines. Shown: chars 0-{shown:,}.{note} Call "
        f'{READ_TOOL}(ref="{ref}", offset={shown}) to read on.]'
    )


def preview(text: str, ref: str, limit: int) -> str:
    """The preview of *text*: its head, an END line for each open frame, and
    the footer. The head is the text exactly as it was."""
    end = _cut_point(text, limit)
    head = text[0:end]
    closers = [
        UntrustedFrame(token).close_line(label) for label, token in reversed(_open_frames(head))
    ]
    stored = min(len(text), MAX_STORED_CHARS)
    parts = [head if head.endswith("\n") or not closers else head + "\n"]
    parts.extend(line + "\n" for line in closers)
    body = "".join(parts)
    if not body.endswith("\n"):
        body += "\n"
    return body + _footer(ref, len(text), text, end, stored)


# ── The middleware ───────────────────────────────────────────────────────────

class SpillToolResults(FunctionMiddleware):
    """Keeps a large result of ONE run and gives the model a preview.

    In ``shadow`` it only logs one ``tool_result.size`` line per result.
    """

    def __init__(self, mode: str, spill_chars: int, preview_chars: int) -> None:
        self.mode = mode
        self.spill_chars = spill_chars
        self.preview_chars = preview_chars

    async def process(self, context: Any, call_next: Any) -> None:
        await call_next()
        function = getattr(context, "function", None)
        if function is None:
            return
        name = tool_name(function)
        if name in EXEMPT_TOOLS or _is_delegation(function):
            return
        try:
            text = _plain_text(getattr(context, "result", None))
            if text is None:
                return
            would = len(text) > self.spill_chars
            untrusted = await _untrusted(function, text) if would else (
                _sync_untrusted(function, text))
            _log.info(
                "tool_result.size", tool=name, chars=len(text),
                tokens=len(text) // 4, source=_source(function),
                untrusted=untrusted, would_spill=would, mode=self.mode,
            )
            if self.mode != "on" or not would:
                return
            owner = _owner_key()
            if owner is None:
                _log.warning("tool_result.spill_unkeyed", tool=name)
                return
            ref = _keep(owner, text, untrusted, name)
            context.result = preview(text, ref, self.preview_chars)
        except Exception as exc:  # a spill fault leaves the result whole
            _log.warning("tool_result.spill_failed", tool=name, error=type(exc).__name__)


class ToolResultSpillProvider(ContextProvider):
    """Adds :class:`SpillToolResults` to ONE run of a native MAF agent.

    ``_tool_injection._apply_tool_result_spill`` attaches it first, so the
    middleware is the outermost one. It adds no tool and no instruction.
    """

    def __init__(self, mode: str, spill_chars: int, preview_chars: int) -> None:
        super().__init__(SOURCE_ID)
        self.mode = mode
        self.spill_chars = spill_chars
        self.preview_chars = preview_chars

    async def before_run(
        self, *, agent: Any, session: Any, context: Any, state: dict[str, Any],
    ) -> None:
        context.extend_middleware(
            SOURCE_ID,
            [SpillToolResults(self.mode, self.spill_chars, self.preview_chars)],
        )


# ── The read tool ────────────────────────────────────────────────────────────

def _page_footer(ref: str, start: int, end: int, total: int) -> str:
    if end < total:
        return (
            f"[Stored result {ref}: showed chars {start:,}-{end:,} of {total:,}. "
            f'Call {READ_TOOL}(ref="{ref}", offset={end}) to read on.]'
        )
    return f"[Stored result {ref}: showed chars {start:,}-{end:,} of {total:,}. This is the end.]"


async def read_tool_result(ref: str, offset: int = 0) -> str:
    """Read the next page of a large tool result that was stored.

    A tool result that was too long to show in full ends with a line that
    names its ref. Pass that ref, and the offset that the line gives. Each
    page holds up to 16000 characters. A ref lives for 30 minutes in this
    chat only. When it is gone, call the first tool again.

    Args:
        ref: The ref of the stored result, for example tr_3f9c0a1b.
        offset: The first character to read. Start at 0.
    """
    owner = _owner_key()
    key = str(ref or "").strip()
    if owner is None or not _REF.match(key):
        return NOT_FOUND
    try:
        start = int(offset or 0)
    except (TypeError, ValueError):
        return BAD_OFFSET
    if start < 0:
        return BAD_OFFSET
    _sweep(time.monotonic())
    entry = _STORE.get((*owner, key))
    if entry is None:
        return NOT_FOUND
    _STORE.move_to_end((*owner, key))
    stored = len(entry.text)
    start = min(start, stored)
    end = min(start + PAGE_CHARS, stored)
    page = entry.text[start:end]
    footer = _page_footer(key, start, end, entry.total)
    if not entry.untrusted:
        return f"{page}\n{footer}"
    # Stranger text: hold the chat first, then frame the page afresh.
    await mark_untrusted_answer()
    frame = UntrustedFrame.new()
    note = frame.note("This page of a stored tool result comes from outside Metorite.")
    return f"{note}\n{frame.wrap(FRAME_LABEL, page)}\n{footer}"


def _risk(name: str) -> dict[str, bool]:
    from acb_skills.tool_annotations import TOOL_ANNOTATIONS

    return TOOL_ANNOTATIONS[name]


read_tool_result.__tool_risk__ = _risk(READ_TOOL)  # type: ignore[attr-defined]

TOOLS = (read_tool_result,)

__all__ = [
    "EXEMPT_TOOLS",
    "NOT_FOUND",
    "READ_TOOL",
    "SOURCE_ID",
    "TOOLS",
    "SpillToolResults",
    "ToolResultSpillProvider",
    "clear_store",
    "preview",
    "read_tool_result",
]
