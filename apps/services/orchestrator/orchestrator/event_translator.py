"""ONE event translator — runtime updates → canonical AG-UI events.

Phase 2 of core-loop unification (specs/core_loop_unification.md). Every
chat bug fixed in the 2026-06/07 hardening batches was drift between the
four copies of this mapping (native MAF Tier 1 · Copilot Tier 1.5 · Tier 2
batch · sub-agent forwarding). This module is now the single source of
truth: all four executor paths translate through :func:`translate_update`
(or its helpers), so a mapping fix lands everywhere at once.

Design (list: "How Middleware Lets You Customize Your Agent Harness"):
the translator is a PURE mapping — runtime update in, event dicts out —
with per-tier differences expressed as :class:`TranslatorHooks`, not as
divergent copies. Side effects (elicitation Future parking, todo-tracker
state, session stores) stay in the executor and reach the translator only
through hooks.

Both agent runtimes yield ``agent_framework`` response updates with a
``contents`` list of typed items (``text`` / ``text_reasoning`` /
``function_call`` / ``function_result``); Copilot-SDK updates additionally
carry a ``role`` and a ``raw_representation`` (progress/partial/intent
events). The canonical mapping:

    text (assistant)   → TEXT_MESSAGE_START (once, with messageId)
                         + TEXT_MESSAGE_CONTENT deltas
    text (tool role)   → TOOL_CALL_PARTIAL (id known) / PROGRESS_UPDATE
    text_reasoning     → THINKING_TEXT_MESSAGE_CONTENT
    function_call      → TOOL_CALL_START (+ TOOL_CALL_ARGS), id-deduped via
                         ToolCallStreamState (streamed calls send their id
                         only on the first chunk; re-sent ids never
                         duplicate rows)
    function_result    → TOOL_CALL_RESULT (content capped, success flag)
    raw INTENT         → synthetic TOOL_CALL_START (intent row)

Events are plain payload dicts — the caller wraps them (``_sse`` /
sub-agent envelope). Trajectory evals in
``evals/trajectories/test_event_translator_trajectory.py`` lock cross-tier
parity: identical updates must translate identically on every path.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable
from uuid import uuid4

from acb_common import get_logger

_log = get_logger("orchestrator.event_translator")

RESULT_CAP = 2000       # chars of tool result forwarded to the stream
PARTIAL_CAP = 2000      # chars of live partial tool output per frame
PROGRESS_CAP = 200      # chars of a progress header line


@dataclass
class ToolCallStreamState:
    """Id-dedup state for STREAMED function calls (one logical call → one row).

    OpenAI-style streaming sends a tool call's id only on its FIRST chunk;
    argument chunks that follow carry ``call_id=""`` and must attribute to
    the in-flight call instead of minting new rows.
    """
    run_id: str
    seen: set[str] = field(default_factory=set)
    last_id: str | None = None
    counter: int = 0
    # The argument text forwarded so far, per call id. MAF 1.19 puts the
    # provider's call id on EVERY streamed chunk, not only the first, so a
    # known id is not by itself a re-send: it can carry the next fragment.
    args: dict[str, str] = field(default_factory=dict)
    # Name of the most recently STARTED tool call. A turn that ends on a
    # blocking HITL tool (ask_questions / ask_user / request_confirmation)
    # legitimately has no closing text — the question card IS the output — so
    # the caller needs the name, not just the count, to tell "waiting for the
    # user" apart from "the answer went missing".
    last_name: str | None = None


@dataclass
class TranslationState:
    """Per-run translation state shared by all content types."""
    run_id: str
    fc: ToolCallStreamState = None  # type: ignore[assignment]
    text_started: bool = False
    message_id: str | None = None
    # Segments the runtime has already used for a CLOSED text message on this
    # run.  A tool call closes the open segment (Phase 3c); if the runtime then
    # reuses the SAME message_id for the post-tool answer (or omits it, so we'd
    # mint the same one), we must force a FRESH id — AG-UI requires distinct
    # message ids for distinct logical streams, and the client dedups by id.
    used_message_ids: set[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.fc is None:
            self.fc = ToolCallStreamState(self.run_id)
        if self.used_message_ids is None:
            self.used_message_ids = set()


@dataclass
class TranslatorHooks:
    """Tier-specific extensions, injected by the executor.

    extra_function_call_events(tool_call_id, name, args_obj, args_str)
        → additional events to emit BEFORE the TOOL_CALL_START (Copilot
        path: TODO_LIST interception, elicitation-bridge CUSTOM event).
    on_function_result(tool_call_id)
        → side-effect notification (Copilot path: elicitation cleanup).
    """
    extra_function_call_events: Callable[
        [str, str, Any, str], list[dict[str, Any]]
    ] | None = None
    on_function_result: Callable[[str], None] | None = None


def _complete_args(text: str) -> bool:
    """True when *text* is a whole JSON object — the call's arguments are in."""
    # A whole object ends in "}". Most fragments do not, so they cost no
    # parse, and a long argument stream stays linear rather than quadratic.
    if not text.rstrip().endswith("}"):
        return False
    try:
        return isinstance(json.loads(text), dict)
    except ValueError:
        return False


def function_call_events(
    content: Any, state: ToolCallStreamState,
) -> list[dict[str, Any]]:
    """Map one ``function_call`` content to TOOL_CALL_* payloads (id-deduped).

    Canonical for BOTH runtimes: complete calls (Copilot — id on every
    event) pass straight through once; streamed calls (native MAF) collapse
    continuation chunks onto the in-flight id.
    """
    cid = getattr(content, "call_id", None) or ""
    targs = getattr(content, "arguments", None)
    # Streamed args arrive in pieces and are concatenated downstream —
    # forward fragments verbatim (never JSON-parse a fragment).
    delta = (
        targs if isinstance(targs, str)
        else json.dumps(targs) if isinstance(targs, dict)
        else str(targs or "")
    )
    if not cid:
        # Continuation chunk for the in-flight call — args only, no new row.
        if state.last_id is not None:
            if not delta:
                return []
            state.args[state.last_id] = state.args.get(state.last_id, "") + delta
            return [{"type": "TOOL_CALL_ARGS",
                     "toolCallId": state.last_id, "delta": delta}]
        # No call started yet (defensive) — mint a synthetic id.
        state.counter += 1
        cid = f"{state.run_id}:fc:{state.counter}"
    # A known id never opens a second row. It is either the NEXT argument
    # fragment of a streamed call (MAF 1.19 repeats the provider id on every
    # chunk, and parallel calls interleave by id), or a complete call sent
    # again. The arguments seen so far tell the two apart: once they parse as
    # a whole JSON object the call is complete, and more text is a re-send.
    # Dropping every known-id chunk, as this did before, sent NO arguments for
    # a native MAF call, so the trail could not show a command or a path.
    if cid in state.seen:
        so_far = state.args.get(cid, "")
        if not delta or _complete_args(so_far):
            return []
        state.args[cid] = so_far + delta
        return [{"type": "TOOL_CALL_ARGS", "toolCallId": cid, "delta": delta}]
    state.seen.add(cid)
    state.last_id = cid
    state.args[cid] = delta
    name = getattr(content, "name", "") or "tool"
    state.last_name = name
    out: list[dict[str, Any]] = [{
        "type": "TOOL_CALL_START",
        "toolCallId": cid, "toolCallName": name, "args": delta,
    }]
    if delta:
        out.append({"type": "TOOL_CALL_ARGS", "toolCallId": cid, "delta": delta})
    return out


def _partial_tool_call_id(update: Any) -> str:
    """tool_call_id carried by a PARTIAL_RESULT / PROGRESS raw event, if any."""
    raw = getattr(update, "raw_representation", None)
    try:
        raw_t = str(getattr(raw, "type", ""))
        if "PARTIAL_RESULT" in raw_t or "PROGRESS" in raw_t:
            return getattr(getattr(raw, "data", None), "tool_call_id", "") or ""
    except Exception:  # noqa: BLE001
        pass
    return ""


def _intent_events(update: Any, run_id: str) -> list[dict[str, Any]]:
    """Agent-intent raw events → a synthetic intent tool row (Copilot)."""
    raw = getattr(update, "raw_representation", None)
    if raw is None:
        return []
    try:
        if "INTENT" in str(raw.type):
            intent = getattr(raw.data, "intent", "") or ""
            if intent:
                return [{
                    "type": "TOOL_CALL_START",
                    "toolCallId": f"{run_id}:intent:{intent[:20]}",
                    "toolCallName": intent,
                }]
    except Exception:  # noqa: BLE001
        pass
    return []


def translate_update(
    update: Any,
    state: TranslationState,
    hooks: TranslatorHooks | None = None,
) -> list[dict[str, Any]]:
    """Translate one runtime response update into AG-UI event payloads.

    The single canonical mapping for every streaming path. Pure aside from
    ``state`` mutation; tier-specific behaviour comes only from ``hooks``.
    """
    events: list[dict[str, Any]] = []
    role = getattr(update, "role", None)
    role = getattr(role, "value", role)

    for content in (getattr(update, "contents", None) or []):
        ctype = getattr(content, "type", None)

        if ctype == "text":
            delta = getattr(content, "text", "") or ""
            if not delta:
                continue
            # Tool-role text frames (progress lines, partial terminal
            # output) belong in the thinking timeline — never the visible
            # assistant message. Frames carrying a tool_call_id stream INTO
            # that tool's row (VS Code style live output).
            if role == "tool":
                msg = delta
                if msg.startswith("[progress] "):
                    msg = msg[len("[progress] "):]
                ptc_id = _partial_tool_call_id(update)
                if ptc_id:
                    events.append({
                        "type": "TOOL_CALL_PARTIAL",
                        "toolCallId": ptc_id,
                        "delta": msg[:PARTIAL_CAP],
                    })
                else:
                    events.append({
                        "type": "PROGRESS_UPDATE",
                        "message": msg[:PROGRESS_CAP],
                    })
                continue
            update_msg_id = getattr(update, "message_id", None)
            # Message-id-native segmentation (Phase 3a): runtimes mint a new
            # message id per assistant segment (text between tool rounds).
            # A changed id is a REAL message boundary — close the open
            # segment and start the next, so downstream never has to infer
            # narration-vs-answer from tool positions.
            if (
                state.text_started
                and update_msg_id
                and update_msg_id != state.message_id
            ):
                events.extend(close_text_message(state))
            if not state.text_started:
                state.text_started = True
                # Pick the segment id: prefer the runtime's, but if it's absent
                # OR already belongs to a CLOSED segment (runtime reused an id
                # across a tool boundary — Phase 3c), mint a fresh one so each
                # logical segment has a distinct id (AG-UI requirement + the
                # client dedups by id).
                _mid = update_msg_id or str(uuid4())
                if _mid in state.used_message_ids:
                    _mid = str(uuid4())
                state.message_id = _mid
                state.used_message_ids.add(_mid)
                events.append({
                    "type": "TEXT_MESSAGE_START",
                    "messageId": state.message_id,
                    "role": "assistant",
                })
            events.append({
                "type": "TEXT_MESSAGE_CONTENT",
                "messageId": state.message_id,
                "delta": delta,
            })

        elif ctype == "text_reasoning":
            delta = getattr(content, "text", "") or ""
            if delta:
                events.append({
                    "type": "THINKING_TEXT_MESSAGE_CONTENT",
                    "delta": delta,
                })

        elif ctype == "function_call":
            # Phase 3c: a tool call closes the open text segment. Text emitted
            # BEFORE a tool is narration/plan (thinking timeline); text AFTER
            # the last tool is the answer (message body). Closing here makes
            # every TEXT_MESSAGE_START..END bracket one tool-round-aligned
            # segment — the renderer's "last segment = answer" contract — no
            # matter what the runtime does with message_id. Only closes a
            # genuinely open segment (idempotent when text hasn't started).
            events.extend(close_text_message(state))
            tc_events = function_call_events(content, state.fc)
            if tc_events and hooks and hooks.extra_function_call_events:
                start = next(
                    (e for e in tc_events if e["type"] == "TOOL_CALL_START"),
                    None,
                )
                if start is not None:
                    args_obj = getattr(content, "arguments", None)
                    if isinstance(args_obj, str) and args_obj.strip():
                        try:
                            args_obj = json.loads(args_obj)
                        except Exception:  # noqa: BLE001
                            pass
                    try:
                        events.extend(hooks.extra_function_call_events(
                            start["toolCallId"],
                            start["toolCallName"],
                            args_obj,
                            start["args"],
                        ))
                    except Exception:  # noqa: BLE001
                        _log.exception("event_translator.hook_failed")
            events.extend(tc_events)

        elif ctype == "function_result":
            tc_id = getattr(content, "call_id", None) or ""
            exc = getattr(content, "exception", None)
            result = getattr(content, "result", "") or ""
            events.append({
                "type": "TOOL_CALL_RESULT",
                "toolCallId": tc_id,
                "content": (str(exc) if exc else str(result))[:RESULT_CAP],
                "success": exc is None,
            })
            if hooks and hooks.on_function_result:
                try:
                    hooks.on_function_result(tc_id)
                except Exception:  # noqa: BLE001
                    _log.exception("event_translator.hook_failed")

    events.extend(_intent_events(update, state.run_id))
    return events


def close_text_message(state: TranslationState) -> list[dict[str, Any]]:
    """TEXT_MESSAGE_END for the open message, if any (idempotent)."""
    if state.text_started and state.message_id:
        state.text_started = False
        return [{"type": "TEXT_MESSAGE_END", "messageId": state.message_id}]
    return []


def text_message_events(
    text: str, *, message_id: str | None = None, words_per_chunk: int = 3,
) -> list[dict[str, Any]]:
    """A complete text as START → word-chunked CONTENT deltas → END.

    Tier 2's batch path streams its final answer through this so even the
    fallback tier speaks the message-id-native protocol.
    """
    msg_id = message_id or str(uuid4())
    events: list[dict[str, Any]] = [{
        "type": "TEXT_MESSAGE_START", "messageId": msg_id, "role": "assistant",
    }]
    chunk: list[str] = []
    for word in text.split(" "):
        chunk.append(word)
        if len(chunk) >= words_per_chunk:
            events.append({
                "type": "TEXT_MESSAGE_CONTENT",
                "messageId": msg_id,
                "delta": " ".join(chunk) + " ",
            })
            chunk = []
    if chunk:
        events.append({
            "type": "TEXT_MESSAGE_CONTENT",
            "messageId": msg_id,
            "delta": " ".join(chunk),
        })
    events.append({"type": "TEXT_MESSAGE_END", "messageId": msg_id})
    return events


# ── Sub-agent envelope ───────────────────────────────────────────────────────

_SUB_AGENT_EVENT_MAP = {
    "TEXT_MESSAGE_CONTENT": "SUB_AGENT_TEXT_DELTA",
    "TOOL_CALL_START": "SUB_AGENT_TOOL_CALL_START",
    "TOOL_CALL_RESULT": "SUB_AGENT_TOOL_CALL_RESULT",
}


def wrap_sub_agent_events(
    events: list[dict[str, Any]], *, agent_name: str, run_id: str,
) -> list[dict[str, Any]]:
    """Re-envelope canonical events as SUB_AGENT_* for the parent stream.

    The sub-agent path translates through the SAME canonical mapping and
    only the envelope differs: text deltas, tool starts (args inlined), and
    tool results are forwarded; message lifecycle / args-streaming /
    reasoning frames are internal to the sub-agent and dropped.
    """
    wrapped: list[dict[str, Any]] = []
    for ev in events:
        sub_type = _SUB_AGENT_EVENT_MAP.get(str(ev.get("type")))
        if sub_type is None:
            continue
        out: dict[str, Any] = {
            "type": sub_type, "agentName": agent_name, "runId": run_id,
        }
        if sub_type == "SUB_AGENT_TEXT_DELTA":
            out["delta"] = ev.get("delta", "")
        elif sub_type == "SUB_AGENT_TOOL_CALL_START":
            out["toolCallId"] = ev.get("toolCallId", run_id)
            out["toolCallName"] = ev.get("toolCallName", "tool")
            out["args"] = ev.get("args", "")
        else:  # SUB_AGENT_TOOL_CALL_RESULT
            out["toolCallId"] = ev.get("toolCallId", run_id)
            out["content"] = ev.get("content", "")
            out["success"] = ev.get("success", True)
        wrapped.append(out)
    return wrapped
