"use client";

/**
 * useAgentChat — streaming chat hook backed by a module-level store (chatStore.ts).
 *
 * State (messages, isLoading, error) lives in chatStore — a singleton Map that
 * survives component unmount/remount.  SSE loops continue writing to the store
 * even after navigation away; returning to the chat page immediately reflects
 * the current stream state via useSyncExternalStore.
 *
 * Multiple concurrent sessions are supported: each threadId has its own store
 * entry and streams independently.
 */

import { useCallback, useEffect, useRef, useSyncExternalStore } from "react";
import type { Dispatch, SetStateAction } from "react";
import {
  getSessionState,
  setSessionState,
  subscribeSession,
  claimStreamOwnership,
  ownsStream,
  releaseStreamOwnership,
} from "@/lib/chatStore";
import type { ChatMessage, ToolEvent } from "@/lib/chatStore";
import { parseAgentError } from "@/lib/parseAgentError";
import { activeContextSlice, isCompactionCheckpoint } from "@/lib/tokenCount";
import { emitAgentEvent } from "@/lib/agentEvents";
import { applyStateSnapshot, applyStateDelta } from "@/hooks/useAgentState";
import { applyStreamEvent, applySubAgentEvent, nanoid, parseReasoning, type StreamFold } from "@/lib/chatStream";
import { isInterruptedReply } from "@/lib/chatInterrupted";

// Re-export types for backward compatibility with AgentChat.tsx imports.
export type { ChatMessage, ToolEvent };

// HITL control events drive the inline ElicitationCard / ConfirmationCard — they
// are interaction signals, not data to display in the generic "Interactive view"
// AG-UI panel.  Keep them out of message.customEvents so they don't render twice
// (and linger after the user answers).
const HITL_CONTROL_EVENTS = new Set([
  "elicitation_requested",
  "user_input_requested",
  "confirmation_requested",
  // A dispatched browser action (H-164): a side effect, never a view. Kept
  // off the message so it neither draws a raw-JSON fold nor is saved.
  "frontend_tool",
]);

/**
 * Read an SSE response body and yield each parsed event object.
 *
 * Centralises the reader/decoder/buffer/line-parsing boilerplate that the LIVE
 * and RECONNECT loops both carried verbatim, plus the `lastEventId` tracking.
 * `isOwner()` is checked before every network read so a loop that has been
 * superseded by a newer one (ownership handover) stops draining its now-dead
 * stream — exactly the guard both loops had at their while-loop top.
 *
 * The per-event EFFECT (the switch) stays in each caller on purpose: the live
 * loop handles sub_agent_* / state / state_delta / todos and throws on error,
 * while the reconnect loop handles a smaller set with its own done/recovery and
 * non-throwing error handling.  Merging those would be a leaky over-
 * generalisation; only the transport is shared here.
 */
async function* readSSEEvents(
  body: ReadableStream<Uint8Array>,
  threadId: string,
  isOwner: () => boolean,
): AsyncGenerator<Record<string, unknown>> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    if (!isOwner()) return;
    const { done, value } = await reader.read();
    if (done) return;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";
    for (const line of lines) {
      if (!line.startsWith("data: ")) continue;
      const raw = line.slice(6).trim();
      if (!raw || raw === "[DONE]") continue;
      let evt: Record<string, unknown>;
      try { evt = JSON.parse(raw) as Record<string, unknown>; } catch { continue; }
      // Track the last SSE event id for reconnection support.
      if (evt._stream_id) {
        setSessionState(threadId, (prev) => ({
          ...prev,
          lastEventId: String(evt._stream_id),
        }));
      }
      yield evt;
    }
  }
}

export interface ArtifactEntry {
  path: string;
  sha256?: string;
  size?: number;
  mimeType?: string;
}

interface UseAgentChatOptions {
  agentName: string;
  threadId: string;
  initialMessages?: ChatMessage[];
  model?: string;
  mode?: "copilot" | "litellm";
  systemContext?: string;
  /** Thinking mode: "auto" | "thinking" | "max" */
  thinkMode?: string;
  onArtifact?: (entry: ArtifactEntry) => void;
}

interface UseAgentChatReturn {
  messages: ChatMessage[];
  isLoading: boolean;
  error: string | null;
  sendMessage: (content: string) => Promise<void>;
  clearMessages: () => void;
  stopGeneration: () => void;
  /** Replace the messages array (used to hydrate from Postgres on mount). */
  setMessages: Dispatch<SetStateAction<ChatMessage[]>>;
  /** True while polling is actively recovering a stream that was interrupted
   *  by a page refresh. The UI shows a "Reconnecting…" indicator. */
  recovering: boolean;
  /** Agent run status for the UI: "idle" | "running" | "recovering" | "unknown".
   *  Lets the user know if execution is stored, stuck, or ongoing. */
  runStatus: "idle" | "running" | "recovering" | "unknown";
}

// nanoid, foldForToolStart, unfoldTrailingAnswer and the per-event message
// reducer now live in @/lib/chatStream (shared by the live loop, the reconnect
// loop, and — for the fold helpers — the server persistence translator).

export function useAgentChat({
  agentName,
  threadId,
  initialMessages = [],
  model,
  mode = "litellm",
  systemContext,
  thinkMode,
  onArtifact,
}: UseAgentChatOptions): UseAgentChatReturn {
  const onArtifactRef = useRef(onArtifact);
  useEffect(() => { onArtifactRef.current = onArtifact; }, [onArtifact]);

  // Keep latest values in refs so sendMessage always uses current values
  // even if its useCallback closure hasn't been recreated yet.
  const modelRef = useRef(model);
  const modeRef = useRef(mode);
  const agentNameRef = useRef(agentName);
  const systemContextRef = useRef(systemContext);
  const thinkModeRef = useRef(thinkMode);
  useEffect(() => { modelRef.current = model; }, [model]);
  useEffect(() => { modeRef.current = mode; }, [mode]);
  useEffect(() => { agentNameRef.current = agentName; }, [agentName]);
  useEffect(() => { systemContextRef.current = systemContext; }, [systemContext]);
  useEffect(() => { thinkModeRef.current = thinkMode; }, [thinkMode]);

  // Subscribe to the module-level store (survives navigation/unmount).
  const sessionState = useSyncExternalStore(
    (l) => subscribeSession(threadId, l),
    () => getSessionState(threadId),
    () => getSessionState(threadId),
  );
  const messages = sessionState.messages;
  const isLoading = sessionState.isLoading;
  const error = sessionState.error;

  // Initialise from initialMessages when store has nothing for this session.
  const initialMessagesRef = useRef(initialMessages);
  useEffect(() => { initialMessagesRef.current = initialMessages; }, [initialMessages]);
  useEffect(() => {
    const current = getSessionState(threadId);
    if (current.messages.length === 0 && initialMessagesRef.current.length > 0) {
      setSessionState(threadId, (prev) => ({
        ...prev,
        messages: initialMessagesRef.current,
      }));
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [threadId]);

  // setMessages — backward-compat for AgentChat.tsx DB hydration.
  const setMessages = useCallback(
    (updater: ChatMessage[] | ((prev: ChatMessage[]) => ChatMessage[])) => {
      setSessionState(threadId, (prev) => ({
        ...prev,
        messages:
          typeof updater === "function" ? updater(prev.messages) : updater,
      }));
    },
    [threadId],
  );

  const sendMessage = useCallback(
    async (content: string) => {
      if (!content.trim() || getSessionState(threadId).isLoading) return;

      const controller = new AbortController();
      // Stamp the assistant 1ms after the user so the pair never shares a
      // timestamp_ms. The DB orders by (timestamp_ms, id); an identical ms for
      // both would let a reload render the reply before its own prompt.
      const turnTs = Date.now();
      const userMsg: ChatMessage = {
        id: nanoid(), role: "user", content: content.trim(), timestamp: turnTs,
      };
      const assistantId = nanoid();
      const assistantMsg: ChatMessage = {
        id: assistantId, role: "assistant", content: "", timestamp: turnTs + 1,
        streaming: true, toolEvents: [], progressLines: [], isThinkingActive: true,
      };

      // Claim exclusive write ownership of this message.  If a reconnect/replay
      // loop later takes over the same message, this loop's writes silently
      // no-op (see ownsStream below) — preventing the two loops from both
      // appending deltas and doubling the text.
      const streamToken = nanoid();
      claimStreamOwnership(threadId, assistantId, streamToken);

      setSessionState(threadId, (prev) => ({
        ...prev,
        messages: [...prev.messages, userMsg, assistantMsg],
        isLoading: true, error: null, abortController: controller,
      }));

      // Emit run started event for subscribers
      emitAgentEvent("onRunStarted", { runId: assistantId, threadId });

      try {
        // Build the history sent to the model from the ACTIVE context window
        // (everything from the most recent compaction checkpoint onward), so a
        // compacted conversation sends [summary + recent turns] instead of the
        // whole transcript — matching Claude Code / Copilot CLI.  The checkpoint
        // summary (a system message) is kept; other system messages are dropped.
        // Slice off BOTH just-appended messages (userMsg + assistant placeholder):
        // the current turn travels separately as `message`, and leaving it in the
        // history sent the user's prompt to the model twice on the copilot and
        // executor paths (only litellm deduped server-side).
        const prior = getSessionState(threadId).messages.slice(0, -2);
        const active = activeContextSlice(prior);
        const history = active
          .filter((m, idx) => m.role !== "system" || (idx === 0 && isCompactionCheckpoint(m)))
          .map((m) => ({ role: m.role, content: m.content }));

        const res = await fetch("/api/agent/chat", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          signal: controller.signal,
          body: JSON.stringify({
            agentName: agentNameRef.current,
            message: userMsg.content,
            messages: history,
            threadId,
            mode: modeRef.current,
            model: modelRef.current ?? "auto",
            context: systemContextRef.current ?? undefined,
            thinkMode: thinkModeRef.current ?? "auto",
            // Pass the assistant message ID so the server-side proxy persists
            // content to the SAME row the frontend renders.  Without this the
            // proxy used a constant id (assistant-<threadId>) that overwrote
            // every turn and never correlated with the frontend's nanoid —
            // breaking refresh recovery.
            assistantMessageId: assistantId,
          }),
        });

        // ── Stand down: this message was folded into a run already going ──
        // docs/multiplayer/README.md §4.6. A 202 means the gateway steered our
        // words into somebody else's in-flight turn rather than starting a
        // second run. That turn owns the answer, and it will arrive here over
        // the room's live stream like any other participant's. If we also
        // rendered an assistant bubble we would show the reply twice — the
        // exact bug qm hit and warned about.
        //
        // Note 202 IS `res.ok`, so this check has to come first.
        if (res.status === 202) {
          const outcome = (await res.json().catch(() => ({}))) as {
            steered?: boolean;
          };
          if (outcome.steered) {
            // Drop our own placeholder; the user's message stays, attributed.
            setSessionState(threadId, (prev) => ({
              ...prev,
              messages: prev.messages.filter((m) => m.id !== assistantId),
            }));
          }
          return;
        }

        if (!res.ok || !res.body) {
          const text = await res.text().catch(() => `status ${res.status}`);
          throw new Error(text);
        }

        // Per-stream fold cursor — mutated by applyStreamEvent at tool_start /
        // delta and read at run end by unfoldTrailingAnswer.
        const fold: StreamFold = { foldedAnswerIdx: -1 };

        // Helper: update just the assistant message.  No-op once another loop
        // (reconnect/replay) has claimed this message — only the current owner
        // may mutate it, so concurrent loops can't both append the same deltas.
        const upd = (fn: (m: ChatMessage) => ChatMessage) => {
          if (!ownsStream(threadId, assistantId, streamToken)) return;
          setSessionState(threadId, (prev) => ({
            ...prev,
            messages: prev.messages.map((m) => m.id === assistantId ? fn(m) : m),
          }));
        };

        // Drain the SSE stream via the shared reader; the owner check stops it
        // once a reconnect supersedes this message.
        for await (const evt of readSSEEvents(
          res.body, threadId, () => ownsStream(threadId, assistantId, streamToken),
        )) {
            switch (evt.type) {
              // Common message-state events (delta, reasoning, tool_start,
              // tool_end, tool_partial, progress, todos) — handled identically
              // here and in the reconnect loop, so the logic lives in one place.
              case "delta":
              case "progress":
              case "todos":
              case "reasoning":
              case "tool_start":
              case "tool_end":
              case "tool_partial":
                upd((m) => applyStreamEvent(m, evt, fold));
                break;
              // Nested delegation timeline — shared reducer (also used by the
              // reconnect loop, so a refresh mid-delegation replays it too).
              case "sub_agent_delta":
              case "sub_agent_tool_start":
              case "sub_agent_tool_end":
              case "sub_agent_error":
                upd((m) => applySubAgentEvent(m, evt));
                break;
              case "done":
                upd((m) => applyStreamEvent(m, evt, fold));
                emitAgentEvent("onRunFinalized", { runId: String(evt.run_id ?? ""), threadId });
                break;
              case "state":
                upd((m) => ({ ...m, agentState: (evt.snapshot as Record<string, unknown>) ?? {} }));
                applyStateSnapshot(threadId, (evt.snapshot as Record<string, unknown>) ?? {});
                emitAgentEvent("onStateChanged", { state: evt.snapshot as Record<string, unknown>, threadId });
                break;
              case "state_delta":
                applyStateDelta(threadId, (evt.delta as Array<{ op: string; path: string; value?: unknown }>) ?? []);
                emitAgentEvent("onStateChanged", { stateDelta: evt.delta, threadId });
                break;
              case "custom": {
                const evtName = String(evt.name ?? "");
                emitAgentEvent("onCustomEvent", { name: evtName, value: evt.value ?? evt.data, threadId });
                if ((evtName === "artifact_created" || evtName === "artifact_updated") && onArtifactRef.current) {
                  const data = (evt.value ?? evt.data) as Record<string, unknown> | undefined;
                  onArtifactRef.current({
                    path: String(data?.path ?? ""),
                    sha256: data?.sha256 ? String(data.sha256) : undefined,
                    size: data?.size != null ? Number(data.size) : undefined,
                    mimeType: data?.mime_type ? String(data.mime_type) : undefined,
                  });
                }
                // HITL control events (ask_questions / ask_user / confirm) are
                // rendered by the inline ElicitationCard — they are NOT data to
                // display.  Storing them in customEvents made them surface in the
                // generic "Interactive view" AG-UI panel too, a duplicate that
                // never cleared after the user answered.  Skip persisting them;
                // the asking already shows in the thinking stream as a tool call.
                if (!HITL_CONTROL_EVENTS.has(evtName)) {
                  upd((m) => ({ ...m, customEvents: [...(m.customEvents ?? []), { name: evtName, value: evt.value ?? evt.data }] }));
                }
                break;
              }
              case "error":
                throw new Error(String(evt.content ?? "Stream error"));
            }
        }

        // Ensure streaming flag is cleared even if "done" was missing — via the
        // shared done reducer so the trailing-answer un-fold still runs (a
        // stream that ends without a done frame otherwise strands the final
        // answer inside the folded thinking timeline).
        upd((m) => applyStreamEvent(m, { type: "done" }, fold));
      } catch (err) {
        // Browser-disconnect errors: the user refreshed, navigated away, or
        // the network dropped.  Don't surface these as agent errors — the
        // backend continues running and polling will recover the output.
        //
        // IMPORTANT: Only match browser-level network errors (TypeError /
        // DOMException).  Backend SSE error events (plain Error thrown by our
        // own code from "case 'error':" in the reader loop) MUST pass through
        // so the user sees them.  Otherwise legitimate backend failures like
        // "Load failed" (Copilot SDK model load error) get swallowed.
        const rawErr = err instanceof Error ? err.message : String(err);
        const lc = rawErr.toLowerCase();
        const isBrowserNetworkError =
          err instanceof TypeError || err instanceof DOMException;
        const isDisconnect =
          isBrowserNetworkError && (
            err instanceof DOMException && err.name === "AbortError" ||
            lc.includes("failed to fetch") ||
            lc.includes("fetch") ||
            lc.includes("network") ||
            lc.includes("load failed") ||
            lc.includes("aborted") ||
            lc.includes("bodystreambuffer") ||
            lc.includes("err_network") ||
            lc.includes("connection")
          );
        if (isDisconnect) {
          // Mark assistant as no longer streaming but keep the partial content.
          // The polling effect will pick up the completed message from Postgres.
          setSessionState(threadId, (prev) => ({
            ...prev,
            messages: prev.messages.map((m) =>
              m.id === assistantId ? { ...m, streaming: false, isThinkingActive: false } : m
            ),
          }));
          // Don't clear isLoading — let the polling effect handle recovery.
          return;
        }
        const rawMsg = rawErr;
        const parsed = parseAgentError(rawMsg);
        emitAgentEvent("onError", { error: rawMsg, threadId });
        setSessionState(threadId, (prev) => ({
          ...prev,
          error: rawMsg,
          messages: prev.messages
            .filter((m) => m.id !== assistantId)
            .concat({
              id: nanoid(),
              role: "system",
              content: `__ERROR__${JSON.stringify(parsed)}`,
              timestamp: Date.now(),
            }),
        }));
      } finally {
        // If a reconnect/replay loop superseded us mid-stream it now owns the
        // message AND the shared loading/abort state. A superseded loop must NOT
        // reset isLoading/abortController or it would kill the live reconnect
        // (Stop button stops working, the spinner flickers off, and polling
        // clobbers the replay). Only the still-current owner clears them.
        const stillOwner = ownsStream(threadId, assistantId, streamToken);
        releaseStreamOwnership(threadId, assistantId, streamToken);
        if (stillOwner) {
          setSessionState(threadId, (prev) => ({ ...prev, isLoading: false, abortController: null }));
        }
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [agentName, threadId, model, mode, systemContext],
  );

  const clearMessages = useCallback(() => {
    setSessionState(threadId, (prev) => ({ ...prev, messages: [], error: null }));
  }, [threadId]);

  // ── Recover after refresh / reconnect ──────────────────────────────
  // On mount, reconnect to a live agent run when EITHER:
  //   a) the last local message looks interrupted (mid-stream refresh), OR
  //   b) the server says an agent is still running for this thread
  //      (covers refresh-during-thinking, where the empty assistant
  //      placeholder was never persisted so no local evidence exists).
  // If the reconnect endpoint is unavailable or the stream has expired,
  // we fall back to the existing Postgres polling recovery path.
  useEffect(() => {
    const abortCtrl = new AbortController();
    let reconnected = false;
    let cancelled = false;

    (async () => {
      const state = getSessionState(threadId);
      const last = state.messages[state.messages.length - 1];
      // Only the saved `streaming` flag starts a recovery. A punctuation guess
      // used to, and a finished reply that ends in a list, table or card then
      // showed "Reconnecting…" and a Stop button on every reopen.
      const localInterrupted = isInterruptedReply(last);

      // Server truth: is an agent actually running for this thread?
      let serverActive = false;
      try {
        const r = await fetch("/api/chat/active-sessions", { signal: abortCtrl.signal });
        if (r.ok) {
          const arr = (await r.json()) as { threadId?: string }[];
          serverActive = Array.isArray(arr) && arr.some((s) => s.threadId === threadId);
        }
      } catch { /* server unreachable — rely on local heuristic */ }

      // Surface the agent's run status so the UI can show a meaningful
      // indicator — not just a generic "Reconnecting…" spinner.
      if (!cancelled) {
        setSessionState(threadId, (prev) => ({
          ...prev,
          runStatus: serverActive ? "running" : localInterrupted ? "recovering" : "idle",
        }));
      }

      if (cancelled) return;
      if (!localInterrupted && !serverActive) return;

      // ── Non-active interruption: let polling recover from Postgres ──
      // If the server is not actively running (agent finished) but the
      // last local message looks interrupted (e.g. ends mid-code-block
      // without terminal punctuation), DON'T try to reconnect — it would
      // reset the perfectly-valid local content and leave an empty
      // placeholder while waiting for a Redis stream that already expired.
      // Just set recovering=true so the polling loop fetches the settled
      // content from Postgres at 1.5s intervals.
      if (!serverActive) {
        if (localInterrupted && !cancelled) {
          setSessionState(threadId, (prev) => ({
            ...prev,
            recovering: true,
            runStatus: "recovering",
          }));
        }
        return;
      }

      // ── Surface loading state so the stop button appears ──────────
      // When an agent is still running on the server, the user needs a
      // way to abort it.  Store the abort controller so stopGeneration()
      // can tear down the reconnect SSE + polling.
      setSessionState(threadId, (prev) => ({
        ...prev,
        isLoading: true,
        abortController: abortCtrl,
      }));

      // Ensure there's an assistant message to stream the replay into.
      let lastId: string;
      let lastContent: string;
      if (localInterrupted && last) {
        lastId = last.id;
        lastContent = last.content || "";
        // Clear stale streaming flags and show "Reconnecting…".
        setSessionState(threadId, (prev) => ({
          ...prev,
          error: null,
          recovering: true,
          runStatus: "recovering",
          isLoading: true,
          abortController: abortCtrl,
          messages: prev.messages.map((m) =>
            m.id === lastId ? { ...m, streaming: false, isThinkingActive: false } : m
          ),
        }));
      } else {
        // Agent is running but no assistant message survived the refresh
        // (it was an empty thinking placeholder) — create a fresh one.
        lastId = nanoid();
        lastContent = "";
        const placeholder: ChatMessage = {
          id: lastId, role: "assistant", content: "", timestamp: Date.now(),
          streaming: true, toolEvents: [], progressLines: [], isThinkingActive: true,
        };
        setSessionState(threadId, (prev) => ({
          ...prev,
          error: null,
          recovering: true,
          runStatus: "running",
          isLoading: true,
          abortController: abortCtrl,
          messages: [...prev.messages, placeholder],
        }));
      }

      // ── Attempt live SSE reconnection ────────────────────────────────
      // Ownership token for the message we replay into — claimed once we commit
      // to streaming (after res.ok) so any still-running live loop for the same
      // message stops writing and we rebuild it without doubling the text.
      let reconToken: string | null = null;
      try {
        const curState = getSessionState(threadId);
        const res = await fetch("/api/agent/chat", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          signal: abortCtrl.signal,
          body: JSON.stringify({
            agentName: agentNameRef.current,
            message: lastContent || "(reconnect)",
            messages: activeContextSlice(curState.messages)
              .filter((m, idx) => m.role !== "system" || (idx === 0 && isCompactionCheckpoint(m)))
              .map((m) => ({ role: m.role, content: m.content })),
            threadId,
            mode: modeRef.current,
            model: modelRef.current ?? "auto",
            context: systemContextRef.current ?? undefined,
            thinkMode: thinkModeRef.current ?? "auto",
            assistantMessageId: lastId,
            lastEventId: curState.lastEventId ?? undefined,
            reconnect: true,
          }),
        });

        // A reconnect that got steered (someone else's run took the thread
        // while we were away) has nothing to replay into — the running turn
        // owns the answer and polling will pick it up. §4.6.
        if (res.status === 202) { return; }
        if (!res.ok || !res.body) { return; }  // Fall back to polling.

        // Take exclusive ownership of the replay target before resetting it.
        // Any live loop still writing this message now loses ownership and
        // stops, so its deltas don't interleave with the replayed ones.
        reconToken = nanoid();
        claimStreamOwnership(threadId, lastId, reconToken);

        // ── Reset the interrupted message before replay ─────────────────
        // The reconnect replays ALL events from the Redis stream (since
        // lastEventId is null — initial SSE frames don't carry stream IDs).
        // If we don't clear the existing partial content, replayed deltas
        // get appended on top of what's already there, doubling text,
        // reasoning blocks, and tool events.
        setSessionState(threadId, (prev) => ({
          ...prev,
          messages: prev.messages.map((m) =>
            m.id === lastId
              ? {
                  ...m,
                  content: "",
                  toolEvents: [],
                  reasoningBlocks: [],
                  // Reset segments too (Phase 3b): the replay re-streams from
                  // 0-0, and the delta reducer APPENDS to an existing segment by
                  // id — leaving stale segments here would double their text.
                  segments: [],
                  progressLines: [],
                  isThinkingActive: true,
                  streaming: true,
                }
              : m
          ),
        }));

        // Per-stream fold cursor (mirror of the live loop).
        const fold: StreamFold = { foldedAnswerIdx: -1 };

        // Update helper: applies changes ONLY to the specific message we're
        // replaying into (matched by lastId).  Previous messages — even those
        // without terminal punctuation — must never be modified by replayed
        // delta events; only the reset target message should accumulate content.
        const updLast = (fn: (m: ChatMessage) => ChatMessage) => {
          // No-op once a newer loop has claimed this message (see ownsStream).
          if (!reconToken || !ownsStream(threadId, lastId, reconToken)) return;
          setSessionState(threadId, (prev) => ({
            ...prev,
            messages: prev.messages.map((m) =>
              m.id === lastId ? fn(m) : m
            ),
          }));
        };

        // Drain the replay stream via the shared reader; the owner check stops
        // it once a newer loop supersedes this message.
        for await (const evt of readSSEEvents(
          res.body, threadId, () => !!reconToken && ownsStream(threadId, lastId, reconToken),
        )) {
            switch (evt.type) {
              // Common message-state events — same handling as the live loop
              // (see applyStreamEvent).  A replayed stream never carries todos,
              // but routing it through the shared reducer is harmless.
              case "delta":
              case "reasoning":
              case "progress":
              case "tool_start":
              case "tool_end":
              case "tool_partial":
                updLast((m) => applyStreamEvent(m, evt, fold));
                break;
              // Nested delegation timeline — same shared reducer as the live
              // loop.  Without these cases a refresh mid-delegation dropped
              // the sub-agent's text and tool rows from the replayed message.
              case "sub_agent_delta":
              case "sub_agent_tool_start":
              case "sub_agent_tool_end":
              case "sub_agent_error":
                updLast((m) => applySubAgentEvent(m, evt));
                break;
              case "custom": {
                // Mirror the live loop so a HITL question pending when the page
                // was refreshed re-shows its card on reconnect (the "restored
                // question session" path).  HITL control events drive the
                // ElicitationCard via the subscriber and are NOT stored as
                // displayable customEvents (no duplicate "Interactive view").
                const evtName = String(evt.name ?? "");
                emitAgentEvent("onCustomEvent", { name: evtName, value: evt.value ?? evt.data, threadId });
                if ((evtName === "artifact_created" || evtName === "artifact_updated") && onArtifactRef.current) {
                  const data = (evt.value ?? evt.data) as Record<string, unknown> | undefined;
                  onArtifactRef.current({
                    path: String(data?.path ?? ""),
                    sha256: data?.sha256 ? String(data.sha256) : undefined,
                    size: data?.size != null ? Number(data.size) : undefined,
                    mimeType: data?.mime_type ? String(data.mime_type) : undefined,
                  });
                }
                if (!HITL_CONTROL_EVENTS.has(evtName)) {
                  updLast((m) => ({ ...m, customEvents: [...(m.customEvents ?? []), { name: evtName, value: evt.value ?? evt.data }] }));
                }
                break;
              }
              case "done": {
                reconnected = true;
                // Restore a trailing answer folded into the timeline (turn ended
                // on a tool call) and mark the message settled — the shared
                // reducer does the un-fold + streaming:false in one update.
                updLast((m) => applyStreamEvent(m, evt, fold));
                // Check if we actually recovered content.  When the Redis
                // stream has expired, the reconnect endpoint returns only a
                // synthetic RUN_FINISHED with no prior delta/tool events —
                // the recovered message will be empty.  In that case keep
                // recovering=true so the polling effect fills in content
                // from Postgres.
                const cur = getSessionState(threadId);
                const recoveredMsg = cur.messages.find((m) => m.id === lastId);
                const hasRecoveredContent =
                  (recoveredMsg?.content?.trim() ?? "") ||
                  (recoveredMsg?.toolEvents?.length ?? 0) > 0 ||
                  (recoveredMsg?.reasoningBlocks?.length ?? 0) > 0;
                setSessionState(threadId, (prev) => ({
                  ...prev,
                  recovering: hasRecoveredContent ? false : prev.recovering,
                  runStatus: hasRecoveredContent ? "idle" : "recovering",
                  isLoading: hasRecoveredContent ? false : prev.isLoading,
                  abortController: hasRecoveredContent ? null : prev.abortController,
                }));
                break;
              }
              case "error":
                // The agent emitted an error during its run (e.g. Copilot
                // SDK "Load failed" during model warmup).  Don't abort the
                // entire reconnection — the agent may have recovered and
                // produced output afterward.  The error will be surfaced
                // alongside the recovered content.
                updLast((m) => ({
                  ...m,
                  customEvents: [
                    ...(m.customEvents ?? []),
                    { name: "agent_error", value: evt.content ?? evt.message ?? "Agent error" },
                  ],
                }));
                break;
            }
        }

        // If we got here without a "done" event, the stream ended cleanly.
        if (!reconnected) {
          const cur2 = getSessionState(threadId);
          const recoveredMsg2 = cur2.messages.find((m) => m.id === lastId);
          const hasContent =
            (recoveredMsg2?.content?.trim() ?? "") ||
            (recoveredMsg2?.toolEvents?.length ?? 0) > 0 ||
            (recoveredMsg2?.reasoningBlocks?.length ?? 0) > 0;
          updLast((m) => ({ ...m, streaming: false, isThinkingActive: false }));
          setSessionState(threadId, (prev) => ({
            ...prev,
            recovering: hasContent ? false : prev.recovering,
            runStatus: hasContent ? "idle" : "recovering",
            isLoading: hasContent ? false : prev.isLoading,
            abortController: hasContent ? null : prev.abortController,
          }));
        }
      } catch {
        // Reconnect failed (network error, timeout, etc.) — polling handles it.
        // Clear loading state so the stop button doesn't linger.
        if (!cancelled) {
          setSessionState(threadId, (prev) => ({
            ...prev,
            isLoading: false,
            abortController: null,
          }));
        }
      } finally {
        // Release replay ownership iff we still hold it (a newer loop that
        // superseded us keeps its own claim).
        if (reconToken) releaseStreamOwnership(threadId, lastId, reconToken);
        // If we never reconnected, make sure recovering is set for polling.
        if (!reconnected && !cancelled) {
          setSessionState(threadId, (prev) =>
            prev.recovering
              ? prev
              : { ...prev, recovering: true, runStatus: "recovering" }
          );
        }
      }
    })();

    return () => {
      cancelled = true;
      abortCtrl.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [threadId]);

  const stopGeneration = useCallback(() => {
    const current = getSessionState(threadId);
    // 1. Abort the local SSE fetch (stops the browser reading the stream).
    current.abortController?.abort();
    // 2. Tell the backend to ACTUALLY cancel the run.  Without this the agent
    //    keeps executing detached server-side (burning tokens, writing files)
    //    because it's decoupled from the HTTP response lifecycle.  Fire-and-
    //    forget — the UI shouldn't block on it.
    void fetch("/api/agent/cancel", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ threadId }),
      keepalive: true,
    }).catch(() => {});
    // 3. Immediately clear loading/recovering state so the UI reflects idle
    //    (don't wait for the next polling tick to clear the stale flag).
    setSessionState(threadId, (prev) => ({
      ...prev,
      abortController: null,
      isLoading: false,
      recovering: false,
      runStatus: "idle",
    }));
  }, [threadId]);

  // ── Reconnection & cross-device polling ─────────────────────────────
  // Three-tier polling (always runs, even while a stream appears active):
  //   1. Recovery poll (1.5s): when recovering=true (stream was interrupted
  //      by refresh and we're actively pulling content from Postgres).
  //   2. Fast poll (3s): when the last assistant message looks incomplete
  //      (no terminal punctuation) but we're not in recovery mode.
  //   3. Slow poll (30s): always runs, picks up messages from other devices
  //      without requiring a session switch.
  //
  // We poll even when isLoading because the browser may have aborted the
  // SSE fetch on refresh/navigation, leaving the chatStore in a stale
  // loading state.  The backend continues running regardless, so polling
  // lets us recover the persisted messages.
  useEffect(() => {
    const last = messages[messages.length - 1];
    const lastIncomplete =
      last?.role === "assistant" &&
      last.content &&
      (last.streaming || !/[.?!]\s*$/.test(last.content.trim()));

    // If the store says we're loading but no abortController is present,
    // the stream was lost (browser refresh / tab close).  Clear the stale
    // loading flag so polling can take over immediately.
    if (isLoading) {
      const current = getSessionState(threadId);
      if (!current.abortController) {
        // Stale loading flag from a lost connection — clear it.
        setSessionState(threadId, (prev) => ({ ...prev, isLoading: false }));
      }
      // Regardless, still poll so we don't miss messages.
    }

    let cancelled = false;
    let pollTimer: ReturnType<typeof setTimeout>;

    // Track the last known message count so we only update on change
    let lastKnownCount = messages.length;

    // Recovery timeout: set when recovering is first detected, cleared when done.
    let recoveryStartedAt = 0;

    const poll = async () => {
      if (cancelled) return;
      try {
        // Poll only the most recent window — polling exists to pick up the
        // latest assistant message growing / new turns, never to backfill old
        // history (that's loadOlderHistory's job).  The content-aware merge
        // below only updates/appends by id, so older loaded messages are kept.
        const res = await fetch(
          `/api/chat/sessions/${threadId}/messages?limit=50`,
          { signal: AbortSignal.timeout(5000) }
        );
        if (!res.ok) return;
        // The gateway serves this endpoint in camelCase (chat.py
        // _render_message) and always has. This mapper read snake_case, so
        // every recovery poll rebuilt messages with an empty tool timeline, no
        // reasoning, no todos and no generative-UI cards. The "only grows"
        // merge below hid it for messages the browser had already streamed —
        // but a message it had NOT (a refresh mid-run, and now another
        // participant's turn arriving) came back stripped.
        const remote = await res.json() as Array<{
          id: string; role: string; content: string;
          timestamp: number; toolEvents?: unknown[];
          progressLines?: string[]; reasoning?: string | null;
          agentState?: Record<string, unknown> | null;
          customEvents?: unknown[];
          authorEmail?: string | null;
          authorKind?: "human" | "agent" | "system" | null;
          redacted?: boolean;
          redactedCaps?: string[];
        }>;
        if (!Array.isArray(remote) || remote.length === 0) return;

        // Map remote format to ChatMessage.  Drop stale __ERROR__ system
        // messages persisted by older builds — transient errors must never
        // resurface via polling.
        const remoteMsgs: ChatMessage[] = remote
          .filter((r) => !(r.role === "system" && (r.content ?? "").startsWith("__ERROR__")))
          .map((r) => ({
          id: r.id,
          role: r.role as "user" | "assistant" | "system",
          content: r.content ?? "",
          timestamp: r.timestamp ?? Date.now(),
          toolEvents: (r.toolEvents as ToolEvent[]) ?? [],
          progressLines: r.progressLines ?? [],
          // Split WITHOUT dropping empty segments — block indices must stay
          // aligned with each tool's reasoningCutoff (empty sentinels are
          // skipped at render time instead).
          reasoningBlocks: parseReasoning(r.reasoning),
          agentState: r.agentState ?? undefined,
          // Restore the todo list from agent_state (where it was persisted
          // by persistAssistantMessage).  Mirrors the mapping in
          // sessions.ts fetchMessagesFromDb so the Todos panel survives
          // polling recovery after a page refresh mid-stream.
          todos: (
            (r.agentState as Record<string, unknown> | null)
              ?.todos as { id: string; title: string; status: string }[] | undefined
          ),
          // Restore real message segments (Phase 3b) so segment-native
          // rendering survives a reload/poll — the renderer prefers these over
          // the folded content when present.
          segments: (
            (r.agentState as Record<string, unknown> | null)
              ?.segments as { id: string; text: string }[] | undefined
          ),
          customEvents: (r.customEvents as Array<{ name: string; value: unknown }>) ?? [],
          // `null` means a pre-authorship row — keep it undefined so the
          // bubble falls back to the un-attributed rendering.
          authorEmail: r.authorEmail ?? undefined,
          authorKind: r.authorKind ?? undefined,
          redacted: r.redacted || undefined,
          redactedCaps: r.redactedCaps,
        }));

        // Content-aware merge.  Handles two recovery cases:
        //   (a) New messages appended on the server (count grew).
        //   (b) The same assistant message got LONGER content while we were
        //       away (refresh recovery) — count is unchanged but content grew.
        const cur = getSessionState(threadId);
        // Skip merge entirely if we're actively streaming locally (live SSE
        // is authoritative — avoid clobbering it with slightly-stale polls).
        if (cur.abortController) {
          pollTimer = setTimeout(poll, 3000);
          return;
        }

        const localById = new Map(cur.messages.map((m) => [m.id, m]));
        let changed = false;

        // Find the longest local assistant content (to detect server growth
        // even when ids differ, e.g. local nanoid vs server assistant-<tid>).
        const localAssistantContents = cur.messages
          .filter((m) => m.role === "assistant")
          .map((m) => m.content);

        const merged: ChatMessage[] = [...cur.messages];

        for (const rm of remoteMsgs) {
          const localMatch = localById.get(rm.id);
          if (localMatch) {
            // Same id — update if the server has more content/tool events/
            // reasoning/custom events.  Custom events count: the run-boundary
            // fold persists generative_ui + artifact cards this client may
            // never have seen (dropped SSE), and they are the whole payload of
            // an inline AG-UI card.
            if (
              rm.content.length > localMatch.content.length ||
              (rm.toolEvents?.length ?? 0) > (localMatch.toolEvents?.length ?? 0) ||
              (rm.reasoningBlocks?.length ?? 0) > (localMatch.reasoningBlocks?.length ?? 0) ||
              (rm.customEvents?.length ?? 0) > (localMatch.customEvents?.length ?? 0) ||
              // Authorship arriving for the first time is itself news: an
              // optimistic local turn has no author until the server stamps
              // one, and without this the name never appears until the next
              // token grows the row.
              (!!rm.authorKind && !localMatch.authorKind)
            ) {
              const idx = merged.findIndex((m) => m.id === rm.id);
              if (idx >= 0) {
                // Spreading `rm` wholesale used to REPLACE the local stream
                // artifacts with the server's — so a poll that fired only
                // because the server content had grown also blanked the
                // generative_ui cards this client had already rendered live
                // (the "my UI card closed when I sent the next message" bug).
                // Each artifact list is monotonic: keep whichever side has more.
                const richer = <T,>(a?: T[], b?: T[]): T[] | undefined =>
                  (a?.length ?? 0) >= (b?.length ?? 0) ? a : b;
                merged[idx] = {
                  ...localMatch,
                  ...rm,
                  // A poll can now fire for authorship alone, so the content
                  // must not shrink just because the server row is a beat
                  // behind.  A redacted row is the exception: its notice is
                  // the whole point and always wins.
                  content: rm.redacted || rm.content.length >= localMatch.content.length
                    ? rm.content
                    : localMatch.content,
                  toolEvents: richer(rm.toolEvents, localMatch.toolEvents),
                  customEvents: richer(rm.customEvents, localMatch.customEvents),
                  reasoningBlocks: richer(rm.reasoningBlocks, localMatch.reasoningBlocks),
                  progressLines: richer(rm.progressLines, localMatch.progressLines),
                  segments: richer(rm.segments, localMatch.segments),
                  // Authorship only ever GROWS, same as the lists above: a poll
                  // that answers from a pre-authorship row (or an older
                  // gateway) carries `undefined`, and spreading that over a
                  // name we already know would silently un-attribute the turn.
                  authorEmail: rm.authorEmail ?? localMatch.authorEmail,
                  authorKind: rm.authorKind ?? localMatch.authorKind,
                  redacted: rm.redacted ?? localMatch.redacted,
                  redactedCaps: rm.redactedCaps ?? localMatch.redactedCaps,
                  streaming: false,
                };
                changed = true;
              }
            }
            continue;
          }

          // No id match.  If this is an assistant message whose content is a
          // superset of a local partial assistant message, replace that local
          // partial (refresh recovery — server has the fuller version).
          if (rm.role === "assistant" && rm.content) {
            const supersedesIdx = merged.findIndex(
              (m) =>
                m.role === "assistant" &&
                m.content &&
                rm.content.startsWith(m.content) &&
                rm.content.length >= m.content.length,
            );
            if (supersedesIdx >= 0) {
              const superseded = merged[supersedesIdx];
              merged[supersedesIdx] = {
                ...rm,
                // Same "only grows" rule — the local partial may already know
                // which agent spoke even when this server row does not.
                authorEmail: rm.authorEmail ?? superseded.authorEmail,
                authorKind: rm.authorKind ?? superseded.authorKind,
                streaming: false,
              };
              changed = true;
              continue;
            }
            // Skip if identical content already present anywhere.
            if (localAssistantContents.includes(rm.content)) continue;
          }

          // Genuinely new message.
          merged.push(rm);
          changed = true;
        }

        if (changed) {
          lastKnownCount = merged.length;
          setSessionState(threadId, (prev) => ({
            ...prev,
            // Clear loading once we've recovered settled content.
            isLoading: prev.abortController ? prev.isLoading : false,
            messages: merged,
          }));
        }

        // Determine next poll interval and whether to clear recovering.
        const updatedLast = remoteMsgs[remoteMsgs.length - 1];
        const stillIncomplete =
          updatedLast?.role === "assistant" &&
          updatedLast.content &&
          !/[.?!]\s*$/.test(updatedLast.content.trim());

        // Clear recovering if the server message looks settled (ends with
        // punctuation) or we've been in recovery for >45s.
        const curRecovering = getSessionState(threadId).recovering;
        if (curRecovering) {
          // Track when recovery first started.
          if (!recoveryStartedAt) recoveryStartedAt = Date.now();
          const recoveryAge = Date.now() - recoveryStartedAt;
          if (!stillIncomplete || recoveryAge > 45000) {
            setSessionState(threadId, (prev) => ({ ...prev, recovering: false, runStatus: "idle" }));
            recoveryStartedAt = 0;
          }
        } else {
          recoveryStartedAt = 0;
        }

        if (!cancelled) {
          // Recovery mode: fast 1.5s polling. Incomplete: 3s. Settled: 30s.
          const curStillRecovering = getSessionState(threadId).recovering;
          const interval = curStillRecovering ? 1500 : stillIncomplete ? 3000 : 30000;
          pollTimer = setTimeout(poll, interval);
        }
      } catch {
        if (!cancelled) pollTimer = setTimeout(poll, 10000);
      }
    };

    // Start polling: always fast initially (2s) when there are messages,
    // let the poll function dynamically adjust based on recovering/incomplete state.
    const hasMessages = messages.length > 0;
    const initialInterval = hasMessages ? 2000 : 30000;
    pollTimer = setTimeout(poll, initialInterval);

    return () => { cancelled = true; clearTimeout(pollTimer); };
  }, [threadId, messages, isLoading]);

  const recovering = getSessionState(threadId).recovering;
  const runStatus = getSessionState(threadId).runStatus;

  return { messages, isLoading, error, sendMessage, clearMessages, stopGeneration, setMessages, recovering, runStatus };
}
