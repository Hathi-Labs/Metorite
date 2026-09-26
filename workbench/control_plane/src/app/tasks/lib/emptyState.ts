/**
 * My Tasks · what an empty list says (continuity P3, item 5).
 *
 * The box is the shared `@/components/EmptyState`, the one Projects draws.
 * This file keeps what is My Tasks' own: the copy. "Inbox zero. Mind like
 * water." is this app's voice, and the shared box was promoted FROM it.
 *
 * Two states, never one, the rule `app/projects/lib/emptyState.ts` states:
 * "your filters hid everything" offers the way out, and "there is nothing
 * here" does not. Pure, so `emptyState.test.ts` fences the copy.
 */

import type { ViewKey } from "./types";

export interface TasksEmptyCopy {
  /** A Lucide name that the icon registry maps in every pack. */
  icon: string;
  message: string;
  hint?: string;
  /** `success` is the tick an empty inbox earns. An empty list is not a win. */
  tone: "muted" | "success";
  /** True when the filters are the cause. The caller then offers Clear. */
  filtered: boolean;
}

/** An empty list view: Next, Waiting, Priority, Done and the rest. */
export function tasksEmptyCopy(view: ViewKey, filtered: boolean): TasksEmptyCopy {
  if (filtered) {
    return {
      icon: "SearchX",
      message: "No tasks match your filters.",
      tone: "muted",
      filtered: true,
    };
  }
  const message =
    view === "inbox"
      ? "Inbox zero. Mind like water."
      : view === "waiting"
        ? "Nothing on your Waiting-For list."
        : view === "next"
          ? "No next actions assigned to you."
          : "Nothing here yet.";
  return { icon: "CheckCircle2", message, tone: "success", filtered: false };
}

/** The Inbox. Its filter row has no Clear control, so neither does this. */
export function inboxEmptyCopy(input: {
  /** Nothing in the inbox at all, before any filter. */
  empty: boolean;
  /** Items processed in this session, for the hint under inbox zero. */
  processed: number;
}): TasksEmptyCopy {
  if (!input.empty) {
    return {
      icon: "SearchX",
      message: "Nothing in the inbox matches this filter.",
      tone: "muted",
      filtered: true,
    };
  }
  return {
    icon: "CheckCircle2",
    message: "Inbox zero. Mind like water.",
    hint:
      input.processed > 0
        ? `You processed ${input.processed} item${input.processed === 1 ? "" : "s"} this session. 🎉`
        : "Nothing left to process. Capture the next thing above.",
    tone: "success",
    filtered: false,
  };
}
