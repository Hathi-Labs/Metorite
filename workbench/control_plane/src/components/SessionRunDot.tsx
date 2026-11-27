/**
 * SessionRunDot — the dot on a chat row in every chat list: /chat and the
 * My Tasks, Projects and Email rails (WS-51 S5).
 *
 * One rule, `sessionDot`, for every list:
 *   - a card waits on the member's answer: amber (`warning`), still, and it
 *     wins over the other two (task 3600);
 *   - else a run goes on in the chat: green, and it pulses only when the member
 *     allows motion;
 *   - else the chat has a reply the member has not read: blue (`info`), still;
 *   - else no dot, or a quiet placeholder where a list keeps the column.
 *
 * The dot is not colour alone: it carries a spoken name.
 *
 * Fence (R7): `src/lib/runSignals.test.ts`.
 */

import { accentForHue } from "@/lib/statusAccent";

export type SessionDotState = "needs_input" | "running" | "unread" | null;

/**
 * Which dot a chat row wears. A card that waits on the member wins over a live
 * run, and a live run wins over an unread reply.
 */
export function sessionDot(running: boolean, unread: boolean, needsInput = false): SessionDotState {
  if (needsInput) return "needs_input";
  if (running) return "running";
  if (unread) return "unread";
  return null;
}

/** The spoken name of each dot. */
export const SESSION_DOT_LABEL: Record<Exclude<SessionDotState, null>, string> = {
  needs_input: "Needs your answer",
  running: "Assistant is working",
  unread: "New reply",
};

export default function SessionRunDot({
  running,
  unread,
  needsInput = false,
  placeholder = false,
}: {
  running: boolean;
  unread: boolean;
  /** The server says a card waits on the member's answer in this chat. */
  needsInput?: boolean;
  /** Keep the column with a quiet dot when there is nothing to say. */
  placeholder?: boolean;
}) {
  const state = sessionDot(running, unread, needsInput);
  if (!state) {
    return placeholder ? (
      <span aria-hidden className="h-1.5 w-1.5 shrink-0 rounded-full bg-muted-foreground/30" />
    ) : null;
  }
  return (
    <span
      role="img"
      aria-label={SESSION_DOT_LABEL[state]}
      title={SESSION_DOT_LABEL[state]}
      data-session-dot={state}
      className={`h-1.5 w-1.5 shrink-0 rounded-full glass:h-2 glass:w-2 ${
        state === "needs_input"
          ? accentForHue("amber").dot
          : state === "running"
            ? "bg-success motion-safe:animate-pulse"
            : "bg-info"
      }`}
    />
  );
}
