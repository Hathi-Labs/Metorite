/**
 * What a failed turn leaves in the thread: an error card, or nothing.
 *
 * `useAgentChat` calls this for every failure that is not a browser
 * disconnect. Before 2026-10-05 every such failure became an `__ERROR__` card.
 * One case now does not: the server refused the SESSION (another org, or a
 * room the member is not in) and the surface said it can recover, because the
 * id was restored from storage and not opened on purpose (`railSessions.ts`).
 * Then the turn leaves the thread, and the surface opens a new chat with the
 * member's words in the composer.
 *
 * Fence: `railSessions.test.ts`, which runs this against the real chat store.
 */
import { setSessionState } from "@/lib/chatStore";
import { runErrorView } from "@/lib/runErrors";
import { nanoid } from "@/lib/chatStream";
import { isSessionRefusal, isSessionRefusalText } from "@/lib/sessions";

/**
 * A surface's answer to a refused session. Return true when it took the turn
 * (it opens a new chat), false to let the error card show.
 */
export type SessionRefusedHandler = (pendingText: string) => boolean;

/**
 * The run cap refused the send (WS-51 D-3). The surface puts the member's
 * words back in its composer, unless the member typed something new.
 */
export type RunCappedHandler = (pendingText: string) => void;

/** The id of the one run-cap notice in a thread. A second refusal replaces it. */
export const RUN_CAP_NOTICE_ID = "run-cap-notice";

export interface FailedTurn {
  threadId: string;
  userMsgId: string;
  assistantId: string;
  /** The member's words, handed to the surface when it recovers. */
  content: string;
  /** The raw failure text: a response body or a stream error frame. */
  rawErr: string;
  /** The HTTP status, or null for an error frame inside a stream. */
  status: number | null;
  /** The server's code for the failure (`lib/runErrors.ts`), when it sent one. */
  code?: string | null;
  /** The server's short reference for the failure, when it sent one. */
  ref?: string | null;
  onSessionRefused?: SessionRefusedHandler;
  onRunCapped?: RunCappedHandler;
}

export function settleFailedTurn(t: FailedTurn): "recovered" | "capped" | "error" {
  const refused =
    t.status !== null ? isSessionRefusal(t.status, t.rawErr) : isSessionRefusalText(t.rawErr);
  if (refused && t.onSessionRefused?.(t.content)) {
    setSessionState(t.threadId, (prev) => ({
      ...prev,
      error: null,
      messages: prev.messages.filter((m) => m.id !== t.assistantId && m.id !== t.userMsgId),
    }));
    return "recovered";
  }
  // The words come from the server's code. Nothing here reads the raw text,
  // which goes into the card's fold only (owner report, 2026-10-08).
  const view = runErrorView({ raw: t.rawErr, code: t.code, ref: t.ref, status: t.status });
  // ── The run cap (WS-51 D-3) ───────────────────────────────────────────────
  // Nothing was sent: the gateway refused before it saved or started
  // anything. So the turn leaves the thread, the words go back to the
  // composer, and ONE notice says why. It is not a retry, and nothing sends
  // again by itself.
  if (view.code === "too_many_runs") {
    setSessionState(t.threadId, (prev) => ({
      ...prev,
      error: null,
      messages: prev.messages
        .filter((m) => m.id !== t.assistantId && m.id !== t.userMsgId && m.id !== RUN_CAP_NOTICE_ID)
        .concat({
          id: RUN_CAP_NOTICE_ID,
          role: "system",
          content: `__ERROR__${JSON.stringify(view)}`,
          timestamp: Date.now(),
        }),
    }));
    t.onRunCapped?.(t.content);
    return "capped";
  }
  setSessionState(t.threadId, (prev) => ({
    ...prev,
    error: t.rawErr,
    messages: prev.messages
      .filter((m) => m.id !== t.assistantId)
      .concat({
        id: nanoid(),
        role: "system",
        content: `__ERROR__${JSON.stringify(view)}`,
        timestamp: Date.now(),
      }),
  }));
  return "error";
}
