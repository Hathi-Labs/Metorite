/**
 * Edit the last message: the edit REPLACES it, it never forks (owner,
 * 2026-10-09).
 *
 * Before this module, Edit re-sent the text as a new turn. The old turn and
 * its reply stayed, so the thread showed the same message twice, and a run in
 * flight took the edit as a steer instead of a replacement.
 *
 * The rules, and where each one lives:
 *
 * 1. Only the LAST user message may be edited (`lastUserMessageId`). The
 *    gateway checks the same rule (`gateway/chat_supersede.py`).
 * 2. A run in flight stops first, and the edit waits for the stop to settle
 *    (`submitEdit`). It never steers into the old run.
 * 3. The old message and every reply after it leave the thread in one state
 *    change, and the edited message takes their place (`supersedeLocal`).
 *    The gateway deletes the same rows from `chat_message`.
 * 4. The gateway composes the note that tells the model about the edit. The
 *    browser sends only the id of the message it replaces.
 *
 * Fence: `chatEdit.test.ts`.
 */
import type { ChatMessage } from "@/lib/chatStore";

/** The custom event that marks a user turn as an edit. It persists through
 *  the `custom_events` column, so the "Edited" label survives a reload. */
export const EDITED_EVENT = "edited";

/** The id of the last user turn in the thread, or null when there is none.
 *  Any person's turn counts: in a room, a later turn by somebody else means
 *  my message is no longer the last one. */
export function lastUserMessageId(messages: readonly ChatMessage[]): string | null {
  for (let i = messages.length - 1; i >= 0; i--) {
    if (messages[i].role === "user") return messages[i].id;
  }
  return null;
}

/** True when this user turn replaced an earlier one. */
export function isEdited(m: Pick<ChatMessage, "customEvents">): boolean {
  return (m.customEvents ?? []).some((e) => e.name === EDITED_EVENT);
}

/** The edit marker for the new user turn. */
export function editedMarker(supersededId: string): { name: string; value: unknown } {
  return { name: EDITED_EVENT, value: { supersedes: supersededId } };
}

/**
 * The thread after an edit: everything before the superseded turn, then
 * `next` (the edited turn and its reply placeholder). Null when the
 * superseded turn is not in the thread, so the caller can refuse rather than
 * append.
 */
export function supersedeLocal(
  messages: readonly ChatMessage[],
  supersededId: string,
  next: readonly ChatMessage[],
): ChatMessage[] | null {
  const idx = messages.findIndex((m) => m.id === supersededId);
  if (idx < 0) return null;
  return [...messages.slice(0, idx), ...next];
}

/** The ids an edit removes: the superseded turn and every message after it. */
export function supersededIds(
  messages: readonly ChatMessage[],
  supersededId: string,
): string[] {
  const idx = messages.findIndex((m) => m.id === supersededId);
  return idx < 0 ? [] : messages.slice(idx).map((m) => m.id);
}

// ── Tombstones ──────────────────────────────────────────────────────────────
// The gateway deletes the old rows when the edited run starts. A poll or a
// history load that answers before that delete, or a late fold of the
// cancelled run, would bring them back. So the ids an edit removed stay
// removed in this page, whatever a stale read says.

const _gone = new Map<string, Set<string>>();

/** Remember that an edit removed these ids from this thread. */
export function markSuperseded(threadId: string, ids: readonly string[]): void {
  if (!ids.length) return;
  const set = _gone.get(threadId) ?? new Set<string>();
  for (const id of ids) set.add(id);
  _gone.set(threadId, set);
}

/** True when an edit in this page removed this id. */
export function isSuperseded(threadId: string, id: string): boolean {
  return _gone.get(threadId)?.has(id) ?? false;
}

/** The messages an edit has not removed. Returns the SAME array when nothing
 *  is filtered, so a caller's identity check still works. */
export function withoutSuperseded<T extends { id: string }>(
  threadId: string,
  messages: T[],
): T[] {
  const set = _gone.get(threadId);
  if (!set || set.size === 0) return messages;
  const kept = messages.filter((m) => !set.has(m.id));
  return kept.length === messages.length ? messages : kept;
}

// ── The order of an edit ────────────────────────────────────────────────────

export interface EditDeps {
  threadId: string;
  /** The current thread, read when the edit starts. */
  getMessages: () => readonly ChatMessage[];
  /** True while a run is in flight on the thread. */
  isRunning: () => boolean;
  /** Stop the run and resolve when the stop has settled on the server. */
  stop: () => Promise<void>;
  /** Start the new run. It replaces the superseded turn in the thread. */
  send: (text: string, opts: { supersedes: string }) => Promise<void>;
}

/**
 * Edit the last user message: stop a run in flight, wait for the stop, then
 * send the edited text as the replacement.
 *
 * `"refused"` when the message is not the last user turn, or the text is
 * empty. Nothing is sent then, so an edit can never add a turn.
 */
export async function submitEdit(
  deps: EditDeps,
  messageId: string,
  text: string,
): Promise<"sent" | "refused"> {
  const trimmed = text.trim();
  const before = deps.getMessages();
  if (!trimmed || lastUserMessageId(before) !== messageId) return "refused";
  if (deps.isRunning()) await deps.stop();
  // Read again: the stop may have changed the thread.
  markSuperseded(deps.threadId, supersededIds(deps.getMessages(), messageId));
  await deps.send(trimmed, { supersedes: messageId });
  return "sent";
}
