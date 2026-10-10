/**
 * SessionRunDot — the dot on a chat row in every chat list: /chat and the
 * My Tasks, Projects and Email rails (WS-51 S5).
 *
 * One rule, `sessionDot`, for every list:
 *   - a run goes on in the chat: green, and it pulses only when the member
 *     allows motion;
 *   - else the chat has a reply the member has not read: blue (`info`), still;
 *   - else no dot, or a quiet placeholder where a list keeps the column.
 *
 * The dot is not colour alone: it carries a spoken name.
 *
 * Fence (R7): `src/lib/runSignals.test.ts`.
 */

export type SessionDotState = "running" | "unread" | null;

/** Which dot a chat row wears. A live run wins over an unread reply. */
export function sessionDot(running: boolean, unread: boolean): SessionDotState {
  if (running) return "running";
  if (unread) return "unread";
  return null;
}

/** The spoken name of each dot. */
export const SESSION_DOT_LABEL: Record<Exclude<SessionDotState, null>, string> = {
  running: "Assistant is working",
  unread: "New reply",
};

export default function SessionRunDot({
  running,
  unread,
  placeholder = false,
}: {
  running: boolean;
  unread: boolean;
  /** Keep the column with a quiet dot when there is nothing to say. */
  placeholder?: boolean;
}) {
  const state = sessionDot(running, unread);
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
      className={`h-1.5 w-1.5 shrink-0 rounded-full ${
        state === "running" ? "bg-success motion-safe:animate-pulse" : "bg-info"
      }`}
    />
  );
}
