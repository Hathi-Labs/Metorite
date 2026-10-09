/**
 * chatStream — pure SSE-event → assistant-message reduction.
 *
 * The chat hook (useAgentChat) consumes the agent SSE stream in TWO loops — the
 * LIVE stream and the RECONNECT/replay stream — that historically carried two
 * near-identical copies of "apply this event to the assistant message".  Every
 * fix (narration fold, tool-row dedup, trailing-answer un-fold) then had to be
 * written twice and kept in sync by hand, which is exactly where past bugs hid.
 *
 * This module centralises that logic:
 *   • foldForToolStart / unfoldTrailingAnswer — the narration-fold helpers, also
 *     imported by the server-side persistence translator
 *     (app/api/agent/chat/route.ts) so all THREE call sites share one copy.
 *   • applyStreamEvent — the pure message-state reducer for the events whose
 *     handling is identical in both loops (delta, reasoning, tool_start,
 *     tool_end, tool_partial, progress, todos, done).
 *   • applySubAgentEvent — the nested-delegation reducer (sub_agent_*), shared
 *     the same way.  Side-effecting / loop-specific events (custom, state,
 *     error) stay in the hook.
 *
 * Pure: no React, no DOM — safe to import from both client and server.
 */

import type { ChatMessage, ToolEvent } from "@/lib/chatStore";

/** Short non-cryptographic id (frontend-local tool/message ids). */
export function nanoid(): string {
  return Math.random().toString(36).slice(2, 11);
}

/**
 * Fold pre-tool narration into the reasoning blocks and compute the timeline
 * cutoff for a starting tool (VS Code-style interleaved thinking).
 *
 * Blocks with index < cutoff render BEFORE the tool in the timeline; reasoning
 * that streams in afterwards lands at index >= cutoff and renders AFTER it.  An
 * empty sentinel block is appended when needed so later reasoning never merges
 * into a pre-tool block.
 */
export function foldForToolStart(
  blocks: string[] | undefined,
  narration: string,
): { blocks: string[]; cutoff: number } {
  const folded = narration ? [...(blocks ?? []), narration] : [...(blocks ?? [])];
  if (folded.length === 0) return { blocks: folded, cutoff: 0 };
  if (!folded[folded.length - 1].trim()) {
    // Trailing empty sentinel from a previous tool — future reasoning merges
    // into it, so it must sit after this tool too.
    return { blocks: folded, cutoff: folded.length - 1 };
  }
  // Seal the current block so later reasoning starts a NEW block after the tool.
  return { blocks: [...folded, ""], cutoff: folded.length };
}

/**
 * Un-fold the trailing narration block back into the visible answer.
 *
 * `foldForToolStart` moves answer text into the thinking timeline at every
 * tool_start, on the assumption that only text emitted AFTER the last tool call
 * is the real answer.  That assumption breaks when a turn ENDS on a tool call
 * (save_memory, manage_todo_list, write_artifact, a trailing verification
 * command, …): the genuine answer was the last folded block and `content` ends
 * up empty — the answer "disappears" into the consciousness stream.
 *
 * This restores it: promote reasoningBlocks[foldedAnswerIdx] back to `content`
 * and blank that block (kept as an empty sentinel so each tool's reasoningCutoff
 * index stays aligned — empty blocks are skipped at render).  No-op when answer
 * text already followed the last tool call (content non-empty) or nothing was
 * folded (foldedAnswerIdx < 0).
 */
export function unfoldTrailingAnswer(
  content: string,
  reasoningBlocks: string[] | undefined,
  foldedAnswerIdx: number,
): { content: string; reasoningBlocks: string[] | undefined } {
  if (content.trim() || foldedAnswerIdx < 0) return { content, reasoningBlocks };
  const blocks = reasoningBlocks ?? [];
  const candidate = blocks[foldedAnswerIdx];
  if (!candidate || !candidate.trim()) return { content, reasoningBlocks };
  return {
    content: candidate,
    reasoningBlocks: blocks.map((b, i) => (i === foldedAnswerIdx ? "" : b)),
  };
}

/**
 * Append a streamed reasoning chunk into paragraph-grouped blocks.
 *
 * Verbose token streams append to the current block; a new block starts only at
 * a paragraph break (\n\n) — the grouping VS Code Copilot uses for thinking
 * text.  Returns a NEW array (never mutates the input).  Shared by the live
 * reducer and the server persistence translator so the cascade stays identical.
 */
export function groupReasoningBlocks(
  blocks: string[] | undefined,
  chunk: string,
): string[] {
  const cur = blocks ?? [];
  if (!chunk) return [...cur];
  if (cur.length === 0) return [chunk];
  const merged = cur[cur.length - 1] + chunk;
  const parts = merged.split(/\n{2,}/).filter((p, i, a) => p.trim() || i === a.length - 1);
  return [...cur.slice(0, -1), ...parts];
}

/** Legacy separator for the `reasoning` TEXT column — kept ONLY for reading rows
 *  written before the JSON format below. */
const LEGACY_REASONING_SEP = "\n---\n";

/**
 * Serialize reasoning blocks for the `chat_message.reasoning` TEXT column.
 *
 * JSON (not a "\n---\n"-joined string): a reasoning block can itself contain a
 * markdown "---" rule on its own line, and the old join/split silently tore such
 * a block in two on read — misaligning every later tool's `reasoningCutoff`
 * index.  Empty sentinel blocks are preserved (indices must stay aligned).
 * Returns null for an empty/absent list.
 */
export function serializeReasoning(blocks: string[] | undefined | null): string | null {
  if (!blocks || blocks.length === 0) return null;
  return JSON.stringify(blocks);
}

/**
 * Parse the `reasoning` column back into blocks.  Accepts the new JSON-array
 * form and falls back to the legacy "\n---\n"-joined form for rows written
 * before the change (the legacy split keeps empty segments so block indices stay
 * aligned with each tool's `reasoningCutoff`).  Returns undefined when empty.
 */
export function parseReasoning(raw: string | null | undefined): string[] | undefined {
  if (typeof raw !== "string" || !raw) return undefined;
  if (raw.startsWith("[")) {
    try {
      const parsed = JSON.parse(raw);
      if (Array.isArray(parsed) && parsed.every((b) => typeof b === "string")) {
        return parsed as string[];
      }
    } catch { /* not JSON — fall through to the legacy split */ }
  }
  return raw.split(LEGACY_REASONING_SEP);
}

/**
 * Keep one CUSTOM event on the message. A run with real segment ids stamps
 * the event with the count of segments it holds now, so a card draws before
 * the text that streams after it (`genUiFlow`, `lib/chatPlacement.ts`). Both
 * SSE loops call this, so the live view and a reconnect place a card alike.
 */
export function withCustomEvent(m: ChatMessage, name: string, value: unknown): ChatMessage {
  const segs = m.segments?.length ?? 0;
  const event = segs > 0 ? { name, value, segmentCutoff: segs } : { name, value };
  return { ...m, customEvents: [...(m.customEvents ?? []), event] };
}

/** Per-stream fold cursor threaded through {@link applyStreamEvent}. */
export interface StreamFold {
  /** Index of the most recently folded answer block — text that streamed as the
   *  visible answer but was moved into the timeline at a tool_start.  Restored
   *  as `content` at run end if no further answer text followed the last tool
   *  call.  -1 = nothing to un-fold. */
  foldedAnswerIdx: number;
}

/**
 * Apply ONE streamed event to the assistant message.  Returns a NEW message
 * (immutable) and mutates `fold` in place — the fold cursor is per-stream state,
 * not per-message.  Returns the message unchanged for events this reducer
 * doesn't own; the caller handles those (custom / state / error), since they
 * have side effects or differ between the live and reconnect loops.
 * Sub-agent events go through {@link applySubAgentEvent}.
 *
 * This is the single source of truth shared by both SSE loops in useAgentChat.
 */
export function applyStreamEvent(
  m: ChatMessage,
  evt: Record<string, unknown>,
  fold: StreamFold,
): ChatMessage {
  switch (evt.type) {
    case "delta": {
      const deltaText = String(evt.content ?? "");
      // New visible answer text → any earlier narration fold was genuine
      // narration, not the answer.  Drop the un-fold candidate.  Only REAL
      // text counts: models emit stray whitespace/newline deltas around tool
      // boundaries, and letting those reset the cursor left the actual answer
      // stranded in the folded thinking timeline at run end.
      if (deltaText.trim()) fold.foldedAnswerIdx = -1;
      // Segment capture (Phase 3a): accumulate onto the real message segment
      // when the backend sent its id — ground truth for 3b's segment-native
      // rendering; content/fold behaviour is unchanged.
      const segId = typeof evt.messageId === "string" ? evt.messageId : "";
      let segments = m.segments;
      if (segId) {
        const cur = [...(m.segments ?? [])];
        const idx = cur.findIndex((s) => s.id === segId);
        if (idx >= 0) cur[idx] = { ...cur[idx], text: cur[idx].text + deltaText };
        else cur.push({ id: segId, text: deltaText });
        segments = cur;
      }
      return {
        ...m,
        content: m.content + deltaText,
        segments,
        streaming: true,
        // Brief live snippet in the ThinkingContainer header (activity hint);
        // NOT dumped into reasoning (reserved for real chain-of-thought).
        progressLines: [
          ...(m.progressLines ?? []).filter((l) => !l.startsWith("↳ ")),
          `↳ ${deltaText.slice(0, 80)}`,
        ].slice(-3),
      };
    }
    case "message_start": {
      // Real per-segment message boundary from the runtime (Phase 3a).
      // Idempotent on replays: never duplicate a segment id.
      const segId = typeof evt.messageId === "string" ? evt.messageId : "";
      if (!segId || (m.segments ?? []).some((s) => s.id === segId)) return m;
      return { ...m, segments: [...(m.segments ?? []), { id: segId, text: "" }] };
    }
    case "message_end":
      // Boundary close — segment content is complete. No state change needed
      // until 3b renders segments; kept explicit so the event is "owned" here.
      return m;
    case "reasoning": {
      const chunk = String(evt.content ?? "");
      if (!chunk) return m;
      return { ...m, reasoningBlocks: groupReasoningBlocks(m.reasoningBlocks, chunk) };
    }
    case "tool_start": {
      const toolId = String(evt.id ?? nanoid());
      // Dedup: a tool call's TOOL_CALL_START can arrive more than once (MAF
      // streams a function_call across several updates as its args fill in, and
      // a replayed stream re-sends it).  Never add a second row for an id we
      // already have — keeps the consciousness timeline from duplicating.
      if ((m.toolEvents ?? []).some((t) => t.id === toolId)) return m;
      const isDelegate = String(evt.name ?? "").toLowerCase().includes("call_agent");
      const hasSegments = (m.segments?.length ?? 0) > 0;
      // Phase 3c (VS Code parity): when the runtime supplied real segment ids,
      // EVERY assistant text segment is answer body — including text emitted
      // before this tool call (it is answer content, not disposable narration).
      // The SEGMENTS hold every piece of assistant text and the renderer builds
      // the body from ALL of them, interleaving tool cards via segmentCutoff. So
      // we must NOT fold `content` into reasoningBlocks (that would wrongly bury
      // answer text in the thinking pane — the startup-guru bug). We still clear
      // the live `content` scratch so it doesn't double-render alongside the
      // segment-derived body; segments are the source of truth. reasoningBlocks
      // holds ONLY genuine chain-of-thought (THINKING_TEXT_MESSAGE_CONTENT).
      //
      // Legacy id-less runtimes (no segments) keep the old fold: their pre-tool
      // text goes to the timeline, since there's no segment ground truth to
      // render inline. Those runtimes never emitted structured answer text
      // before tools anyway.
      const narration = m.content.trim();
      const { blocks, cutoff } = hasSegments
        ? { blocks: m.reasoningBlocks ?? [], cutoff: m.reasoningBlocks?.length ?? 0 }
        : foldForToolStart(m.reasoningBlocks, narration);
      // The just-folded answer text sits at cutoff-1 (foldForToolStart appends
      // narration then an empty sentinel).  Remember it so the run-end handler
      // can restore it if no further answer follows.  Not needed with segments
      // (last-segment rescue at `done` handles that case).
      if (narration && !hasSegments) fold.foldedAnswerIdx = cutoff - 1;
      const newEvent: ToolEvent = {
        id: toolId,
        name: String(evt.name ?? "tool"),
        args: (evt.args as Record<string, unknown>) ?? {},
        status: "running",
        startedAt: Date.now(),
        reasoningCutoff: cutoff,
        // Segment-native anchor (Phase 3b): how many real segments existed when
        // this tool started, so the renderer can interleave segments ⊕ tools
        // chronologically — the same trick reasoningCutoff uses. Only meaningful
        // when the runtime sent segment ids; id-less streams leave it undefined
        // and fall back to the reasoning fold.
        ...(hasSegments ? { segmentCutoff: m.segments!.length } : {}),
        ...(isDelegate ? { subAgentActive: true } : {}),
      };
      return {
        ...m,
        content: narration ? "" : m.content,
        reasoningBlocks: blocks,
        toolEvents: [...(m.toolEvents ?? []), newEvent],
      };
    }
    case "tool_end": {
      const status: ToolEvent["status"] = evt.success ? "done" : "error";
      return {
        ...m,
        toolEvents: (m.toolEvents ?? []).map((t) =>
          t.id === String(evt.id)
            ? {
                ...t,
                args:
                  evt.args && Object.keys(evt.args as object).length > 0
                    ? (evt.args as Record<string, unknown>)
                    : t.args,
                result: String(evt.result ?? ""),
                status,
                endedAt: Date.now(),
                subAgentActive: false,
              }
            : t,
        ),
      };
    }
    case "tool_args": {
      // The running step's arguments, once whole (route.ts). The trail then
      // names the command or the path while the step still runs.
      const args = evt.args;
      if (!args || typeof args !== "object") return m;
      return {
        ...m,
        toolEvents: (m.toolEvents ?? []).map((t) =>
          t.id === String(evt.id) ? { ...t, args: args as Record<string, unknown> } : t,
        ),
      };
    }
    case "tool_partial":
      // Streaming partial output (terminal stdout, tool progress) — accumulate
      // without marking the tool as complete.
      return {
        ...m,
        toolEvents: (m.toolEvents ?? []).map((t) =>
          t.id === String(evt.id)
            ? { ...t, result: (t.result ?? "") + String(evt.result ?? "") }
            : t,
        ),
      };
    case "progress":
      return {
        ...m,
        progressLines: [...(m.progressLines ?? []), String(evt.name ?? "Working")],
      };
    case "todos":
      return {
        ...m,
        todos: Array.isArray(evt.todos)
          ? (evt.todos as { id: string; title: string; status: string }[])
          : m.todos,
      };
    case "done": {
      const u = unfoldTrailingAnswer(m.content, m.reasoningBlocks, fold.foldedAnswerIdx);
      let content = u.content;
      // Segment ground truth (Phase 3b): when the runtime emitted real segments
      // the LAST segment IS the answer. Keep `content` in sync with it so
      // downstream consumers that read `content` (copy/action bar, memory
      // extraction, non-segment clients) get the answer even when the turn
      // ended on a tool call (no trailing text → content cleared at tool_start).
      // The renderer itself reads segments directly; this only backfills content.
      const segs = m.segments ?? [];
      if (segs.length > 0 && !content.trim()) {
        const lastText = segs[segs.length - 1].text;
        if (lastText.trim()) content = lastText;
      }
      return {
        ...m,
        content,
        reasoningBlocks: u.reasoningBlocks,
        streaming: false,
        isThinkingActive: false,
      };
    }
    default:
      return m;
  }
}

/**
 * Apply ONE sub-agent (delegation) event to the assistant message — the nested
 * timeline shown inside the parent `call_agent` tool row.  Shared by the live
 * and reconnect SSE loops (the reconnect loop historically lacked these cases,
 * so a refresh mid-delegation silently dropped the whole nested timeline).
 *
 * Events target the most recent still-active delegate tool row, matching how
 * the backend interleaves SUB_AGENT_* events between the parent tool's start
 * and end.  Unknown event types return the message unchanged.
 */
export function applySubAgentEvent(
  m: ChatMessage,
  evt: Record<string, unknown>,
): ChatMessage {
  const evts = m.toolEvents ?? [];
  /** Index of the last active delegate row, or -1. */
  const lastDelegate = (): number => {
    const idx = [...evts].reverse().findIndex(
      (t) => t.subAgentActive && (t.name.toLowerCase().includes("call_agent") || t.subAgentName),
    );
    return idx === -1 ? -1 : evts.length - 1 - idx;
  };

  switch (evt.type) {
    case "sub_agent_delta": {
      const ri = lastDelegate();
      if (ri === -1) return m;
      const agent = String(evt.agentName ?? "");
      return {
        ...m,
        toolEvents: evts.map((t, i) => i === ri
          ? { ...t, subAgentName: t.subAgentName ?? agent, subAgentText: (t.subAgentText ?? "") + String(evt.delta ?? "") }
          : t),
      };
    }
    case "sub_agent_tool_start": {
      const ri = lastDelegate();
      if (ri === -1) return m;
      const agent = String(evt.agentName ?? "");
      const stId = String(evt.id ?? nanoid());
      return {
        ...m,
        toolEvents: evts.map((t, i) => i === ri
          ? { ...t, subAgentName: t.subAgentName ?? agent, subAgentTools: [...(t.subAgentTools ?? []), { id: stId, name: String(evt.name ?? "tool"), status: "running" as const }] }
          : t),
      };
    }
    case "sub_agent_tool_end": {
      const stId = String(evt.id ?? "");
      return {
        ...m,
        toolEvents: evts.map((t) => !t.subAgentTools ? t : {
          ...t,
          subAgentTools: t.subAgentTools.map((st) =>
            st.id === stId ? { ...st, result: String(evt.result ?? ""), status: evt.success ? "done" as const : "error" as const } : st,
          ),
        }),
      };
    }
    case "sub_agent_error": {
      const idx = [...evts].reverse().findIndex((t) => t.subAgentActive);
      if (idx === -1) return m;
      const ri = evts.length - 1 - idx;
      return {
        ...m,
        toolEvents: evts.map((t, i) => i === ri
          ? { ...t, subAgentActive: false, status: "error" as const, result: String(evt.error ?? "Sub-agent error") }
          : t),
      };
    }
    default:
      return m;
  }
}
