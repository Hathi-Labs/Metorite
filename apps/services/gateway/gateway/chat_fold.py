"""Server-side fold of a run's AG-UI event log into a persisted chat message.

Phase 1 of core-loop unification (specs/core_loop_unification.md): the gateway
becomes the authoritative persistence owner. The per-thread Redis stream is
the append-only source of truth; at run end the detached task replays it and
folds it into the same ``chat_message`` row shape the Next.js translator
writes, so a client that never reconnects still gets the complete turn
(review P0-3 — tail loss after browser close).

The fold helpers here are event-for-event ports of
``workbench/control_plane/src/lib/chatStream.ts`` (fold/unfold/grouping) and
``src/app/api/agent/chat/route.ts`` (event loop). Until Phase 3 makes the
protocol message-id-native and deletes the heuristic family, THESE MUST STAY
SEMANTICALLY IDENTICAL — the fold-parity trajectory evals in
``evals/trajectories/test_chat_fold_trajectory.py`` are the contract.

Timestamps derive from each event's Redis ``_stream_id`` (``<ms>-<seq>``), so
the fold is a pure function of the log (replay-safe, no wall clock).
"""
from __future__ import annotations

import json
import re
from typing import Any

from acb_common import get_logger
from acb_llm.message_hygiene import strip_placeholder_lines

_log = get_logger("gateway.chat_fold")

_PROGRESS_MAX = 20  # mirror route.ts progressLines cap

#: CUSTOM events that only label a turn. A row of labels alone is no answer
#: (AO-3), so the fold saves none. ``ai.route`` is
#: ``acb_skills.tier_policy.ROUTE_EVENT``. The browser's twin is
#: ``checkpointIsEmpty`` in ``lib/assistantCheckpoint.ts``. Fence:
#: ``tests/unit/test_chat_ai_unavailable.py``.
LABEL_EVENTS = frozenset({"ai.route"})


# ── Fold helpers (ports of lib/chatStream.ts) ────────────────────────────────

def group_reasoning_blocks(blocks: list[str], chunk: str) -> list[str]:
    """Append a streamed reasoning chunk into paragraph-grouped blocks.

    Port of ``groupReasoningBlocks``: tokens append to the current block; a
    new block starts only at a paragraph break (2+ newlines). Keeps a trailing
    empty segment (an in-progress block) but drops interior empty ones.
    """
    if not chunk:
        return list(blocks)
    if not blocks:
        return [chunk]
    merged = blocks[-1] + chunk
    parts = re.split(r"\n{2,}", merged)
    parts = [p for i, p in enumerate(parts) if p.strip() or i == len(parts) - 1]
    return [*blocks[:-1], *parts]


def fold_for_tool_start(
    blocks: list[str], narration: str,
) -> tuple[list[str], int]:
    """Fold pre-tool answer text into the reasoning timeline.

    Port of ``foldForToolStart``. Returns ``(blocks, cutoff)`` where cutoff is
    the reasoning-block count at this tool's start (persisted as
    ``reasoningCutoff`` for chronological interleaving).
    """
    folded = [*blocks, narration] if narration else list(blocks)
    if not folded:
        return folded, 0
    if not folded[-1].strip():
        # Trailing empty sentinel from a previous tool — reuse it.
        return folded, len(folded) - 1
    # Seal the current block so later reasoning starts a NEW block.
    return [*folded, ""], len(folded)


def unfold_trailing_answer(
    content: str, blocks: list[str], folded_answer_idx: int,
) -> tuple[str, list[str]]:
    """Restore the genuine answer when the turn ended on a tool call.

    Port of ``unfoldTrailingAnswer``: promote the last folded block back to
    ``content`` and blank it (kept as an empty sentinel so every tool's
    ``reasoningCutoff`` index stays aligned).
    """
    if content.strip() or folded_answer_idx < 0:
        return content, blocks
    if not (0 <= folded_answer_idx < len(blocks)):
        return content, blocks
    candidate = blocks[folded_answer_idx]
    if not candidate.strip():
        return content, blocks
    return candidate, [
        "" if i == folded_answer_idx else b for i, b in enumerate(blocks)
    ]


def parse_tool_args(raw: str | None) -> dict[str, Any]:
    """Port of ``parseToolArgs`` — tolerant parse of streamed JSON args."""
    if not raw or not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {"_raw": raw}
    return parsed if isinstance(parsed, dict) else {"value": parsed}


def _stream_id_ms(event: dict[str, Any]) -> int | None:
    """Epoch-ms from a Redis stream id (``<ms>-<seq>``), if present."""
    sid = str(event.get("_stream_id") or "")
    ms, _, _ = sid.partition("-")
    return int(ms) if ms.isdigit() else None


# ── The fold (port of route.ts translateAndPersistStream accumulation) ──────

def is_specialist_tool(name: str, sub_agent_name: str) -> bool:
    """True for the orchestrator's own tool for the agent *sub_agent_name*.

    The orchestrator names each specialist tool after its agent, with "-" as
    "_" (``orchestrator/agents.py``). Such a tool is a hand-off as much as
    ``call_agent`` is, so its sub-run's steps belong on it (§7.1.1 rule 5).
    The live twin is ``isSpecialistTool`` in ``lib/chatStream.ts``.
    """
    agent = str(sub_agent_name or "").strip().lower()
    return bool(agent) and str(name or "").strip().lower() == agent.replace("-", "_")


#: The tool names that hand work to another agent (substring match, as the
#: live twin ``isHandoffTool`` in ``lib/chatStream.ts``). ``spawn_subagents``
#: is a hand-off whose rows keep no child text (WS-67 OA-9b hardening).
HANDOFF_TOOLS = ("call_agent", "delegate_to_agent", "spawn_subagents")
SPAWN_TOOL = "spawn_subagents"


def is_handoff_tool(name: str) -> bool:
    """Port of ``isHandoffTool`` (``lib/chatStream.ts``)."""
    lowered = str(name or "").lower()
    return any(h in lowered for h in HANDOFF_TOOLS)


def take_handoff(
    tool_name: str, acc: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Port of ``takeHandoff`` (``lib/chatStream.ts``): ``(fields, rest)``.

    A hand-off row (``is_handoff_tool`` or a specialist tool) takes the name,
    the text and the steps, and the accumulator starts again. A spawn row takes
    no text, because a child's text is item JSON for the parent model. Any other
    tool takes nothing and leaves the accumulator alone.
    """
    if not (is_handoff_tool(tool_name) or is_specialist_tool(tool_name, acc["name"])):
        return {}, acc
    fields: dict[str, Any] = {}
    if acc["name"] or acc["text"] or acc["tools"]:
        fields = {
            "subAgentName": acc["name"],
            "subAgentText": "" if SPAWN_TOOL in str(tool_name).lower() else acc["text"],
            "subAgentTools": acc["tools"],
        }
    return fields, {"name": "", "text": "", "tools": []}


def fold_run_events(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Fold one run's AG-UI events into the persisted-message shape.

    Returns ``None`` when the run produced nothing worth persisting (same
    guard as the Next translator). Output keys match ``MessageRecord``:
    ``content, timestamp, tool_events, progress_lines, reasoning,
    agent_state, custom_events``.
    """
    tool_names: dict[str, str] = {}
    tool_args: dict[str, str] = {}
    tool_cutoffs: dict[str, int] = {}
    # Segment count when each tool started (Phase 3b) — mirrors tool_cutoffs so
    # a reloaded message can interleave real segments with tools chronologically.
    tool_seg_cutoffs: dict[str, int] = {}
    tool_starts: dict[str, int | None] = {}
    tool_events: list[dict[str, Any]] = []
    reasoning_blocks: list[str] = []
    progress_lines: list[str] = []
    custom_events: list[dict[str, Any]] = []
    latest_todos: list[dict[str, Any]] = []
    sub_agent: dict[str, Any] = {"name": "", "text": "", "tools": []}
    # Real message segments (Phase 3a) — ground truth for narration-vs-answer
    # when the runtime emitted per-segment ids; [] on legacy id-less streams.
    segments: list[dict[str, str]] = []
    content = ""
    folded_answer_idx = -1
    last_ms: int | None = None

    def _segment_append(msg_id: str, delta: str) -> None:
        for seg in segments:
            if seg["id"] == msg_id:
                seg["text"] += delta
                return
        segments.append({"id": msg_id, "text": delta})

    for ev in events:
        t = str(ev.get("type") or "")
        ms = _stream_id_ms(ev)
        if ms is not None:
            last_ms = ms

        if t == "TEXT_MESSAGE_START":
            msg_id = str(ev.get("messageId") or "")
            if msg_id and not any(s["id"] == msg_id for s in segments):
                segments.append({"id": msg_id, "text": ""})

        elif t == "TEXT_MESSAGE_CONTENT":
            delta = str(ev.get("delta") or "")
            content += delta
            if delta.strip():
                folded_answer_idx = -1
            msg_id = str(ev.get("messageId") or "")
            if msg_id:
                _segment_append(msg_id, delta)

        elif t in ("REASONING_MESSAGE_CONTENT", "THINKING_TEXT_MESSAGE_CONTENT"):
            chunk = str(ev.get("delta") or "")
            if chunk:
                reasoning_blocks = group_reasoning_blocks(reasoning_blocks, chunk)

        elif t == "PROGRESS_UPDATE":
            msg = str(ev.get("message") or "")
            if msg:
                progress_lines.append(msg)
                del progress_lines[:-_PROGRESS_MAX]

        elif t == "TODO_LIST":
            todos = ev.get("todos") or []
            if isinstance(todos, list):
                latest_todos = todos

        elif t == "TOOL_CALL_START":
            tc_id = str(ev.get("toolCallId") or "")
            name = str(ev.get("toolCallName") or ev.get("tool_call_name") or "tool")
            tool_names[tc_id] = name
            tool_args[tc_id] = ""
            # Phase 3b: mirror chatStream.ts / route.ts — when the runtime
            # supplied real segment ids, segments already own every piece of
            # assistant text, so do NOT fold narration into reasoning_blocks
            # (that would duplicate it: once as a segment, once as reasoning).
            # Still clear `content` so stale pre-tool narration doesn't linger;
            # reasoning_blocks then holds only genuine chain-of-thought.
            has_segments = bool(segments)
            narration = content.strip()
            if has_segments:
                cutoff = len(reasoning_blocks)
            else:
                reasoning_blocks, cutoff = fold_for_tool_start(
                    reasoning_blocks, narration,
                )
            tool_cutoffs[tc_id] = cutoff
            if has_segments:
                tool_seg_cutoffs[tc_id] = len(segments)
            if narration:
                if not has_segments:
                    folded_answer_idx = cutoff - 1
                content = ""
            tool_starts[tc_id] = ms
            progress_lines.append(name)
            del progress_lines[:-_PROGRESS_MAX]

        elif t == "TOOL_CALL_ARGS":
            tc_id = str(ev.get("toolCallId") or "")
            tool_args[tc_id] = tool_args.get(tc_id, "") + str(ev.get("delta") or "")

        elif t in ("TOOL_CALL_END", "TOOL_CALL_RESULT"):
            tc_id = str(ev.get("toolCallId") or "")
            name = tool_names.get(tc_id, "tool")
            result = str(ev.get("result") or ev.get("content") or "")
            sub_fields, sub_agent = take_handoff(name, sub_agent)
            tool_events.append({
                "id": tc_id,
                "name": name,
                "args": parse_tool_args(tool_args.get(tc_id)),
                "result": result,
                # Honour the real outcome — never hardcode "done" (P0-5).
                "status": "done" if ev.get("success") is not False else "error",
                "reasoningCutoff": tool_cutoffs.get(tc_id, 0),
                # Only present for id-carrying runs (Phase 3b); omitted otherwise
                # so id-less rows stay byte-identical to the pre-3b shape.
                **(
                    {"segmentCutoff": tool_seg_cutoffs[tc_id]}
                    if tc_id in tool_seg_cutoffs else {}
                ),
                "startedAt": tool_starts.get(tc_id),
                "endedAt": ms,
                **sub_fields,
            })

        elif t == "CUSTOM" and ev.get("name") in ("frontend_tool", "progress", "run_phase"):
            # A dispatched browser action (H-164): a side effect, not part of
            # the answer. The page ran it live; the message never keeps it.
            # HRD-6: the line of a slow tool (`acb_skills.progress_notice`
            # EVENT) shows while the tool runs, and a stored chat never
            # holds it.
            # CS-3a: the phase of the run (`acb_skills.run_phase` EVENT) is
            # live only. A run that sent only phases saves no row.
            pass

        elif t == "CUSTOM":
            # `segmentCutoff`: the count of segments when the card arrived, 0
            # before the first text, so the reloaded card draws before the
            # text that streamed after it. The proxy checkpoint and the live
            # hook stamp it the same way (`genUiFlow`, workbench
            # lib/chatPlacement.ts). A run with no segments draws in the old
            # layout, because `genUiFlow` answers null for it.
            custom_events.append({
                "name": str(ev.get("name") or ""),
                "value": ev.get("value"),
                "segmentCutoff": len(segments),
            })

        elif t == "SUB_AGENT_TEXT_DELTA":
            sub_agent["name"] = sub_agent["name"] or str(ev.get("agentName") or "")
            sub_agent["text"] += str(ev.get("delta") or "")

        elif t == "SUB_AGENT_TOOL_CALL_START":
            sub_agent["name"] = sub_agent["name"] or str(ev.get("agentName") or "")
            sub_agent["tools"].append({
                "id": str(ev.get("toolCallId") or ""),
                "name": str(ev.get("toolCallName") or "tool"),
                "status": "running",
                # §7.1.1 rule 5: the step's arguments, so a reload draws the
                # same email card as the live turn did.
                "args": parse_tool_args(
                    ev.get("args") if isinstance(ev.get("args"), str)
                    else json.dumps(ev.get("args") or {})),
            })

        elif t == "SUB_AGENT_TOOL_CALL_RESULT":
            st_id = str(ev.get("toolCallId") or "")
            ok = ev.get("success") is not False
            for st in sub_agent["tools"]:
                if st["id"] == st_id:
                    st["result"] = str(ev.get("content") or "")
                    st["status"] = "done" if ok else "error"
                    break

        elif t == "SUB_AGENT_ERROR":
            err = str(ev.get("error") or "Sub-agent error")
            sep = "\n" if sub_agent["text"] else ""
            sub_agent["text"] += f"{sep}[error] {err}"

        elif t == "RUN_FINISHED":
            content, reasoning_blocks = unfold_trailing_answer(
                content, reasoning_blocks, folded_answer_idx,
            )

    # ── Segment ground truth (Phase 3c — VS Code parity) ────────────────────
    # When the runtime emitted real message segments, EVERY assistant text
    # segment is answer body — not just the last. Assistant text emitted before
    # a tool call ("Here's the current state… Let me fix this now.") is answer
    # content, so `content` is the full answer = all non-empty segments joined,
    # matching the renderer's `answerBody`. (Earlier logic used only the last
    # segment, which dropped pre-tool answer text from `content` — the sidebar
    # preview, copy/edit, and any non-segment consumer then lost it.) The
    # renderer reads `segments` directly for inline display; `content` is the
    # flat mirror for everything else.
    if segments:
        _seg_texts = [s["text"] for s in segments if s["text"].strip()]
        _joined = "\n\n".join(_seg_texts)
        if _joined.strip():
            # Any segment text that the tool-boundary fold stranded in the
            # reasoning timeline is genuine answer text — blank those blocks so
            # it isn't shown twice (once as body, once in the thinking pane).
            for _st in _seg_texts:
                for i in range(len(reasoning_blocks) - 1, -1, -1):
                    if reasoning_blocks[i].strip() == _st.strip():
                        reasoning_blocks[i] = ""
                        break
            content = _joined

    # Safety net (task 1787): litellm's empty-text placeholder never reaches
    # the stored row, even if it got past the stream filter.
    content = strip_placeholder_lines(content)
    for _seg in segments:
        _seg["text"] = strip_placeholder_lines(_seg["text"])

    # AO-3 (incident 2026-11-17): a label is no answer. A turn whose model
    # call failed still sent its `ai.route` label, and the fold saved a row
    # with no text. The read then showed it, and the member saw an empty
    # reply. The label stays on a row that holds an answer.
    if not (
        content.strip() or tool_events or reasoning_blocks
        or latest_todos
        or any(e["name"] not in LABEL_EVENTS for e in custom_events)
    ):
        return None

    agent_state: dict[str, Any] = {}
    if latest_todos:
        agent_state["todos"] = latest_todos
    if segments:
        # Persisted for 3b's segment-native rendering on reload.
        agent_state["segments"] = segments

    return {
        "content": content,
        "timestamp": last_ms or 0,
        "tool_events": tool_events,
        "progress_lines": progress_lines,
        # JSON serialization matches serializeReasoning (never "---"-joined).
        "reasoning": json.dumps(reasoning_blocks) if reasoning_blocks else None,
        "agent_state": agent_state or None,
        "custom_events": custom_events,
    }


# ── Run-boundary memory extraction (P1-9) ──────────────────────────────────

#: RESUME-2e (task 3818): the key of the text that the memory extraction reads
#: in place of ``content``. Only the fold of a case A run that writes the
#: whole replay sets it (:func:`persist_final_assistant_message`). That row
#: holds part one and part two, and the end of the park already extracted
#: part one. So the key holds the text of THIS run's events only. Each other
#: fold has no key, and the extraction reads ``content`` as before.
MEMORY_CONTENT_KEY = "memory_content"


def build_extraction_conversation(
    history: list[dict[str, str]],
    message: str,
    folded: dict[str, Any] | None,
) -> list[dict[str, str]]:
    """Assemble the conversation to hand to Mem0 at the run boundary.

    Shared by both orchestrator paths (``/agent/run/stream`` and
    ``/copilot/chat``) so run-end memory extraction is identical: the prior
    user/assistant turns, then the current user *message* (deduped against the
    tail of history), then the FOLDED ANSWER (``folded["content"]``) — the piece
    a client-side, reader-lifetime-bound extraction could never guarantee
    (review P1-9).  Returns ``[]`` when there is no user turn to anchor on.

    RESUME-2e: a fold with :data:`MEMORY_CONTENT_KEY` gives the answer from
    that key, also when its text is empty. Then part one of a case A run
    does not go to memory a second time.
    """
    conv: list[dict[str, str]] = [
        {"role": str(m.get("role") or "user"),
         "content": str(m.get("content") or "")}
        for m in history
        if isinstance(m, dict) and m.get("role") in ("user", "assistant")
        and m.get("content")
    ]
    if message and not any(
        m["role"] == "user" and m["content"] == message for m in conv
    ):
        conv.append({"role": "user", "content": message})
    source = folded or {}
    answer = str((source[MEMORY_CONTENT_KEY] if MEMORY_CONTENT_KEY in source
                  else source.get("content")) or "")
    if answer.strip():
        conv.append({"role": "assistant", "content": answer})
    if not any(m["role"] == "user" for m in conv):
        return []
    return conv


# ── Persistence entry point (run_detached on_complete hook) ─────────────────

async def _run_authority(thread_id: str, actor: str) -> dict[str, Any] | None:
    """The clearance this run acted under, or ``None`` for a solo run.

    A shared run acts at the intersection of its participants' access
    (``groups_sessions_authority.md`` §3), so the output it produced is only
    safe for a reader who either was in the room at the time or independently
    holds everything the run held. Recording both facts on the row is what lets
    replay answer that question later, when the room's membership has moved on
    (§4).

    Solo runs record nothing: there is no intersection, the one participant has
    already seen everything, and a NULL keeps every existing row and every
    single-player row exactly as it was.
    """
    try:
        from acb_auth import resolve_session_access

        # Seeded with the acting member, exactly as the run itself was
        # (executor._integration_authorizer): the intersection that produced
        # this output includes the person who asked for it.
        folded, members = await resolve_session_access(thread_id, actor)
        if len(members) < 2:
            return None
        caps = sorted(
            c for c in folded.allowed
            if c.startswith(("integrations:use:", "agents:run:"))
        )
        return {"members": sorted(members), "caps": caps}
    except Exception:
        # A clearance we could not compute must not become a clearance of
        # "none" — that would mark the turn as freely readable. NULL means
        # "unlabelled", and unlabelled rows are governed by the waterline.
        _log.warning("chat_fold.authority_failed", thread_id=thread_id[:12])
        return None


async def _run_holds_mail(
    agent_name: str, thread_id: str, member: str, organization_id: str | None,
) -> bool:
    """``acb_skills.egress.run_holds_mail`` for a run that this module folds.

    The tenant is the run's own, which the caller resolved on the server, and
    it is bound only for the read. A fault reads as mail, so it fails closed.
    """
    from acb_common.db import bind_tenant, current_tenant, release_tenant

    token = None
    try:
        from acb_skills.egress import run_holds_mail

        if organization_id and not current_tenant():
            token = bind_tenant(str(organization_id))
        return await run_holds_mail(
            agent_name, {"session_id": thread_id, "member": member})
    except Exception:  # noqa: BLE001 — fail closed: the run holds mail
        return True
    finally:
        release_tenant(token)


#: The label of the fold was not read yet (the fold of each run that does not
#: go on in the same reply reads it at the write, as before).
_NOT_READ: Any = object()


async def _same_label_as_row(
    thread_id: str, message_id: str, authority: Any, organization_id: str | None,
    member: str,
) -> bool:
    """True when part two of a same-reply run may go into the row
    *message_id* (RESUME-2b, review round 2, P1). The row keeps its first
    label (``COALESCE`` in ``routes.chat._MESSAGE_UPSERT_SQL``), so a part
    two at another intersection must go to its own row.

    The tests are the tests of the gateway's check at the Approve
    (``routes.agent._same_label_as_park``), read again at the end of the run:

    * *authority*, the label of the fold now, equals the row's label.
    * No participant joined after the row (``ReplyTurn.late_join``).
    * With no label, the room of *member* is still solo. The read of the
      label fails open to no label, so this room read is the backstop.

    A row that is not there, a failed room read, and a fault give False."""
    import asyncio

    try:
        from gateway.rooms import resolve_room_access
        from gateway.routes.chat import label_of, reply_turn

        turn = await asyncio.to_thread(
            reply_turn, thread_id, message_id, organization_id=organization_id)
        if turn is None or turn.late_join or label_of(authority) != turn.label:
            return False
        if turn.label is None:
            room = await asyncio.to_thread(
                resolve_room_access, thread_id, member, organization_id=organization_id)
            if room is None or room.resolve_failed or room.is_shared:
                return False
    except Exception as exc:  # fail closed: the fallback row
        _log.warning("chat_fold.same_reply_label_read_failed", error=type(exc).__name__)
        return False
    return True


def same_reply_tail(events: list[dict[str, Any]], run_id: str) -> list[dict[str, Any]]:
    """The events from the LAST continuation start of *run_id*: a
    ``RUN_STARTED`` with the ``resumed`` mark (RESUME-2b). Empty when no
    continuation start landed."""
    starts = [
        i for i, e in enumerate(events)
        if e.get("type") == "RUN_STARTED" and str(e.get("runId") or "") == run_id
        and e.get("resumed") is True
    ]
    return events[starts[-1]:] if run_id and starts else []


def _entry_after(stream_id: Any, cut: str) -> bool:
    """True when the Redis entry id *stream_id* comes after the id *cut*. An
    id of another shape (a local id, no id) gives False."""
    def _key(value: Any) -> tuple[int, int] | None:
        ms, _, seq = str(value or "").partition("-")
        return (int(ms), int(seq or 0)) if ms.isdigit() and (seq or "0").isdigit() else None

    a, b = _key(stream_id), _key(cut)
    return a is not None and b is not None and a > b


def _is_resumed_start(event: dict[str, Any], run_id: str) -> bool:
    """True for the marked ``RUN_STARTED`` of a continuation of *run_id*."""
    return (event.get("type") == "RUN_STARTED" and bool(run_id)
            and str(event.get("runId") or "") == run_id and event.get("resumed") is True)


def same_reply_own(
    events: list[dict[str, Any]], run_id: str, parked_end_id: str | None = None,
) -> list[dict[str, Any]]:
    """The events of THIS continuation run (RESUME-2b, review round 3 and its
    delta, P2-1). With *parked_end_id*, the id of the parked end that its
    start deleted: each event after that id, and no other. In a chain of two
    continuations that excludes part two, which an earlier start marked too.
    With no id (a record from before round 3): the events from the last
    marked start (:func:`same_reply_tail`)."""
    if parked_end_id:
        return [e for e in events if _entry_after(e.get("_stream_id"), parked_end_id)]
    return same_reply_tail(events, run_id)


def same_reply_events(
    events: list[dict[str, Any]], run_id: str, message_id: str, fallback_id: str,
    parked_end_id: str | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    """``(events, row id)`` that the fold of a same-reply run writes, or
    ``([], None)`` when it writes nothing (RESUME-2b, case A, review of
    2026-11-17, P2-1, round 2, P2-a, and round 3, P2-1 and its delta).

    The decision reads the HEAD, as ``stream_relay.parked_end_of`` does
    (``stream_relay.part_one_at_head``). Part one starts with a plain
    ``RUN_STARTED`` of *run_id*, and each continuation with one marked
    ``resumed``. *parked_end_id* is the id of the parked end that the start
    of this run deleted (``stream_relay.start_detached``, the run record's
    ``parkedEndId``). The events of THIS run are the events after it
    (:func:`same_reply_own`).

    * This run's own marked start landed, and part one's start is at the
      head: the replay is whole, so it all goes into *message_id*.
    * Else the events of this run go into *fallback_id*: the rest of a run
      whose part one a trim took, the ``run_interrupted`` marker of a run
      that died before its start, or the rest of a part two whose start a
      trim took. An earlier part of the chain never goes there again.
    * With no event of this run, nothing new is written. With no
      *parked_end_id*, a run with no marked start writes nothing new too.

    So part one is never written again, and a partial replay never writes
    over it."""
    own = same_reply_own(events, run_id, parked_end_id)
    started = any(_is_resumed_start(e, run_id) for e in own)
    from orchestrator.stream_relay import part_one_at_head

    if started and part_one_at_head(events, run_id):
        return events, message_id
    if parked_end_id:
        return (own, fallback_id) if own else ([], None)
    return (own, fallback_id) if started else ([], None)


def _memory_of(
    events: list[dict[str, Any]], run_id: str, parked_end_id: str | None,
) -> dict[str, str]:
    """``{MEMORY_CONTENT_KEY: text}`` for the whole replay *events* of a case
    A run (RESUME-2e, task 3818). The text is the folded answer of THIS
    run's events only (:func:`same_reply_own`), so part one does not reach
    memory a second time. No event of this run gives an empty text."""
    own = fold_run_events(same_reply_own(events, run_id, parked_end_id))
    return {MEMORY_CONTENT_KEY: str((own or {}).get("content") or "")}


async def persist_final_assistant_message(
    thread_id: str,
    message_id: str,
    *,
    user_id: str = "",
    agent_name: str = "orchestrator",
    run_id: str = "",
    model: str | None = None,
    organization_id: str | None,
    expect_run_id: str | None = None,
    holds_mail: bool | None = None,
    same_reply_fallback_id: str | None = None,
    same_reply_parked_end: str | None = None,
) -> dict[str, Any] | None:
    """Replay the run's event log and upsert the authoritative message row.

    ``same_reply_fallback_id`` (RESUME-2b, case A, review of 2026-11-17,
    P2-1, round 2, P1 and P2-a, and round 3, P2-1) marks the fold of a run
    that went on in the SAME reply. *message_id* then holds part one, which
    the fold at the park wrote. ``same_reply_parked_end`` is the id of the
    parked end that the start deleted. :func:`same_reply_events` decides by
    the head. Part one's start at the head writes the whole replay into
    *message_id*. A trim of part one sends the events from the last
    continuation start to the fallback row. With no continuation start, the
    events after the parked end go to the fallback row, so a
    ``run_interrupted`` marker lands on a row. A whole replay goes
    into *message_id* only when the label of now equals the row's label
    (:func:`_same_label_as_row`). Else part two goes into the fallback row.
    So a partial replay, and a part two at another intersection, never write
    into part one's row. Fences: ``tests/unit/test_resume_continuation.py``,
    ``tests/unit/test_resume_continuation_r8.py`` (R8).

    ``holds_mail`` is the answer of ``acb_skills.egress.run_holds_mail`` for
    this run (WS-17 EM-G11f3, ``email_app_master_plan.md`` section
    12.3.13.9.1, item 6). True drops the body of the ``agent_run`` trace. A
    run-end route passes the answer that it took once. ``None`` asks the
    predicate here, under the run's tenant (:func:`_run_holds_mail`), and the
    restart sweep relies on that. The message row itself (W9) does not change.

    ``expect_run_id`` makes the fold refuse a stream that belongs to ANOTHER
    run. The restart sweep folds a dead run after the fact (gateway.
    chat_recovery). If a new run has reset the thread's stream by then, its
    ``RUN_STARTED`` names the new run, and folding it would write the new
    run's answer into the old row. A stream with no ``RUN_STARTED`` (its head
    trimmed) is folded as before. Fence:
    ``tests/unit/test_chat_deploy_recovery.py``.

    Called from the detached task's ``finally`` — the run is over (finished,
    errored, or cancelled) and every event it emitted is in Redis. Idempotent
    with the Next translator's live-path checkpoints: same row id, upsert.

    Self-sufficient: ensures the parent ``chat_session`` row exists first
    (owned by ``user_id``) so a first-turn client-death can't lose the message
    to the chat_message → chat_session foreign key — the exact failure P0-3
    persistence exists to prevent.

    ``organization_id`` is the run's tenant, which the route resolved from the
    server-side identity before it detached the run (WS-27bm S15, §21). Every
    write below binds it. With no tenant the writes fail closed and log
    ``chat_fold.persist_failed``, and nothing writes unbound.

    Best-effort: never raises. Returns the folded message dict on success
    (callers chain run-boundary work like memory extraction off it), else
    ``None``.
    """
    try:
        from orchestrator.stream_relay import replay_events  # noqa: PLC0415

        # drain=True paginates until the stream is exhausted so a long run's
        # tail (final answer, late tool events, trailing reasoning) is never
        # dropped from the persisted message. count is the per-batch size, not a
        # total cap. Reasoning emits one Redis event per delta, so long thinking
        # streams can exceed a single batch — draining keeps them whole on reload.
        events = await replay_events(
            thread_id, since_id="0-0", count=10_000, drain=True,
        )
        if expect_run_id:
            started = next(
                (e for e in events if e.get("type") == "RUN_STARTED"), None,
            )
            held = str((started or {}).get("runId") or "")
            if held and held != expect_run_id:
                _log.warning(
                    "chat_fold.persist_refused_other_run",
                    thread_id=thread_id[:12], message_id=message_id[:40],
                )
                return None
        _authority: Any = _NOT_READ
        _skip_trace = False
        #: RESUME-2e: the memory key, set only for a whole case A replay.
        _memory: dict[str, str] = {}
        if same_reply_fallback_id:
            _run = expect_run_id or run_id
            events, target = same_reply_events(
                events, _run, message_id, same_reply_fallback_id,
                parked_end_id=same_reply_parked_end,
            )
            if target is None:
                _log.info("chat_fold.same_reply_nothing_new", thread_id=thread_id[:12])
                return None
            if target == message_id:
                # Review round 2, P1: part two ran at the intersection of
                # NOW. The row keeps its first label for ever, so part two
                # goes into it only when its label is the row's label.
                _authority = await _run_authority(thread_id, user_id)
                if not await _same_label_as_row(
                        thread_id, message_id, _authority, organization_id, user_id):
                    # Round 3 delta, P2-1: only the events of THIS run.
                    events = same_reply_own(events, _run, same_reply_parked_end)
                    target = same_reply_fallback_id
                    _log.warning("chat_fold.same_reply_label_changed",
                                 thread_id=thread_id[:12])
            if target == same_reply_fallback_id:
                _log.warning("chat_fold.same_reply_partial", thread_id=thread_id[:12])
                # Round 3 delta, NOTED: a run whose own start never landed
                # writes no trace. The trace is an upsert on the shared run
                # id, so it would set part one's tool counts to zero.
                _skip_trace = not any(_is_resumed_start(e, _run) for e in events)
            # RESUME-2e (task 3818): a whole replay goes into the row, and the
            # end of the park already extracted part one. So memory gets the
            # text of THIS run's events only. The fallback row needs no key.
            _memory = ({} if target == same_reply_fallback_id
                       else _memory_of(events, _run, same_reply_parked_end))
            message_id = target
        # Head-trim visibility (audit R1): the stream is MAXLEN-capped, and
        # Redis trims the OLDEST entries — so a very long turn can lose its
        # head (RUN_STARTED + the start of the answer) before this replay
        # runs, and the persisted row silently starts mid-answer. Make that
        # loss observable instead of silent.
        if events and not any(
            e.get("type") == "RUN_STARTED" for e in events[:5]
        ):
            _log.warning(
                "chat_fold.head_trimmed",
                thread_id=thread_id[:12],
                first_event=str(events[0].get("type"))[:32],
                total_events=len(events),
            )
        folded = fold_run_events(events)

        # Durable per-run observability trace (E2): record the run row from the
        # SAME replay — status/tokens/tools/error for every run, full trace for
        # errored/flagged ones. Independent of message persistence: a run that
        # produced no assistant message (folded is None) still gets a trace row
        # (that itself is a debuggable outcome — e.g. an immediate error).
        # RESUME-2b, round 3 delta: a same-reply run whose own start never
        # landed writes no trace (`_skip_trace`).
        try:
            from gateway.run_trace import record_run_trace  # noqa: PLC0415
            _last_ms = (folded or {}).get("timestamp") or None
            if holds_mail is None and not _skip_trace:
                holds_mail = await _run_holds_mail(
                    agent_name, thread_id, user_id, organization_id)
            if not _skip_trace:
                await record_run_trace(
                    run_id=run_id or message_id,  # message_id is run-unique per turn
                    thread_id=thread_id,
                    agent_name=agent_name,
                    user_id=user_id,
                    model=model,
                    events=events,
                    folded=folded,
                    ended_ms=_last_ms,
                    organization_id=organization_id,
                    holds_mail=holds_mail,
                )
        except Exception:  # noqa: BLE001
            pass

        if folded is None:
            return None

        from gateway.routes.chat import (  # noqa: PLC0415
            MessageRecord,
            _ensure_session,
            _upsert_messages,
        )
        import asyncio  # noqa: PLC0415

        # Parent session must exist before the message FK insert.
        await asyncio.to_thread(
            _ensure_session, thread_id, user_id, agent_name,
            organization_id=organization_id,
        )

        record = MessageRecord(
            id=message_id,
            role="assistant",
            **folded,
        )
        declined = await asyncio.to_thread(
            _upsert_messages, thread_id, [record],
            # The member who started the run. It is also the run member that
            # the upsert checks and stamps on the agent row (S13).
            actor_email=user_id, agent_name=agent_name,
            authority=(await _run_authority(thread_id, user_id)
                       if _authority is _NOT_READ else _authority),
            # WS-27bm S12. The fold knows which agent ran, so it may set the
            # author of an agent turn again. It never changes a human turn.
            author_from_run=True,
            organization_id=organization_id,
        )
        # RESUME-2e: the row above got both parts. The caller's memory
        # extraction reads the key (build_extraction_conversation).
        # Each other fold returns the same dict as before.
        folded = {**folded, **_memory} if _memory else folded
        if declined:
            # S13: the row is a human or system turn, or another member's run.
            # The row stays as it was. The run is still over, so the caller
            # still gets the folded message.
            _log.warning(
                "chat_fold.persist_declined",
                thread_id=thread_id[:12],
                message_id=message_id[:40],
            )
            return folded
        _log.info(
            "chat_fold.persisted",
            thread_id=thread_id[:12],
            message_id=message_id[:40],
            tools=len(folded["tool_events"]),
            chars=len(folded["content"]),
        )
        return folded
    except Exception as exc:  # noqa: BLE001 — persistence must never kill the relay
        _log.warning(
            "chat_fold.persist_failed",
            thread_id=thread_id[:12],
            error=str(exc),
        )
        return None


async def persist_channel_reply(
    thread_id: str,
    message_id: str,
    content: str,
    *,
    user_id: str,
    agent_name: str = "orchestrator",
    timestamp_ms: int,
    organization_id: str,
) -> bool:
    """Write the final reply of a run that a CHANNEL started (WS-47 WAC-3).

    The WhatsApp bot runs the executor's batch ``run_agent``. That path
    writes no Redis event log, so :func:`persist_final_assistant_message`
    has nothing to replay. The caller holds the reply text, and this writes
    it as the run's sealed agent row, with the same rules as the fold:
    ``author_from_run`` names the agent that ran, and the member who started
    the run is the run member (S12, S13). It never changes a human turn.

    The caller wrote the session row first. ``organization_id`` is the tenant
    that the phone link names, never one from the message. Returns True when
    the row was written. Unlike the fold, it raises on a database error, so
    the caller can record the failure.
    """
    import asyncio

    from gateway.routes.chat import MessageRecord, _upsert_messages

    record = MessageRecord(
        id=message_id, role="assistant",
        content=strip_placeholder_lines(content), timestamp=timestamp_ms,
    )
    declined = await asyncio.to_thread(
        _upsert_messages, thread_id, [record],
        actor_email=user_id, agent_name=agent_name,
        # The fold's own rule: a solo thread records nothing (None), and a
        # thread that became a room records its members and caps (§4).
        authority=await _run_authority(thread_id, user_id),
        author_from_run=True,
        organization_id=organization_id,
    )
    if declined:
        _log.warning("chat_fold.channel_reply_declined",
                     thread_id=thread_id[:12], message_id=message_id[:40])
        return False
    return True
