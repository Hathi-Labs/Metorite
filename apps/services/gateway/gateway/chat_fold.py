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

_log = get_logger("gateway.chat_fold")

_PROGRESS_MAX = 20  # mirror route.ts progressLines cap


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
            is_delegate = "call_agent" in name.lower()
            sub_fields: dict[str, Any] = {}
            if is_delegate and (
                sub_agent["name"] or sub_agent["text"] or sub_agent["tools"]
            ):
                sub_fields = {
                    "subAgentName": sub_agent["name"],
                    "subAgentText": sub_agent["text"],
                    "subAgentTools": sub_agent["tools"],
                }
                sub_agent = {"name": "", "text": "", "tools": []}
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

        elif t == "CUSTOM" and ev.get("name") == "frontend_tool":
            # A dispatched browser action (H-164): a side effect, not part of
            # the answer. The page ran it live; the message never keeps it.
            pass

        elif t == "CUSTOM":
            custom_events.append({
                "name": str(ev.get("name") or ""),
                "value": ev.get("value"),
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

    if not (
        content.strip() or tool_events or reasoning_blocks
        or latest_todos or custom_events
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
    answer = str((folded or {}).get("content") or "")
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


async def persist_final_assistant_message(
    thread_id: str,
    message_id: str,
    *,
    user_id: str = "",
    agent_name: str = "orchestrator",
    run_id: str = "",
    model: str | None = None,
) -> dict[str, Any] | None:
    """Replay the run's event log and upsert the authoritative message row.

    Called from the detached task's ``finally`` — the run is over (finished,
    errored, or cancelled) and every event it emitted is in Redis. Idempotent
    with the Next translator's live-path checkpoints: same row id, upsert.

    Self-sufficient: ensures the parent ``chat_session`` row exists first
    (owned by ``user_id``) so a first-turn client-death can't lose the message
    to the chat_message → chat_session foreign key — the exact failure P0-3
    persistence exists to prevent.

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
        try:
            from gateway.run_trace import record_run_trace  # noqa: PLC0415
            _last_ms = (folded or {}).get("timestamp") or None
            await record_run_trace(
                run_id=run_id or message_id,  # message_id is run-unique per turn
                thread_id=thread_id,
                agent_name=agent_name,
                user_id=user_id,
                model=model,
                events=events,
                folded=folded,
                ended_ms=_last_ms,
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
        )

        record = MessageRecord(
            id=message_id,
            role="assistant",
            **folded,
        )
        await asyncio.to_thread(
            _upsert_messages, thread_id, [record],
            actor_email=user_id, agent_name=agent_name,
            authority=await _run_authority(thread_id, user_id),
        )
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
