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
 * 3. Once the server ACCEPTS the edit, the old message and every reply
 *    after it leave the thread in one state change, and the edited message
 *    takes their place (`supersedeLocal`). The gateway deletes the same rows
 *    from `chat_message`. A refused edit changes nothing, and the member
 *    keeps the edited text and reads the reason (`editRefusalReason`).
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

/**
 * The id of the user turn that offers Edit, or null.
 *
 * It is the last user turn, and only when it is the member's own. A turn
 * with no author predates rooms: it is the reader's in a solo thread, and
 * nobody's in a shared room, which is the gateway's rule too
 * (`chat_supersede.plan_supersede`).
 */
export function editableLastUserId(
  messages: readonly ChatMessage[],
  viewerEmail: string | undefined,
  shared: boolean,
): string | null {
  const id = lastUserMessageId(messages);
  if (!id) return null;
  const m = messages.find((x) => x.id === id)!;
  if (m.authorKind && m.authorKind !== "human") return null;
  if (!m.authorEmail) return shared ? null : id;
  if (!viewerEmail) return shared ? null : id;
  return m.authorEmail.toLowerCase() === viewerEmail.toLowerCase() ? id : null;
}

/** The server's answer to an edit. */
export type EditOutcome = { ok: true } | { ok: false; reason: string };

/** The reasons an edit can be refused, in the member's words. */
export const EDIT_NOT_LAST = "Someone replied after your message, so it can no longer be edited.";
export const EDIT_NOT_YOURS = "You can only edit your own message.";
export const EDIT_RUN_BUSY = "The assistant is still working. Stop it first.";
export const EDIT_UNSENT = "Your edit was not sent. Try again.";
/** WS-51 D-3: the member has the most live runs the cap allows. */
export const EDIT_TOO_MANY_RUNS =
  "You have too many assistants running. Wait for one to finish or stop one, then save the edit again.";

/** The reason for a refused edit, from the status and the body the chat
 *  route returned (the gateway's `{"detail": {"error": <code>}}` inside). */
export function editRefusalReason(status: number, body: string): string {
  if (/too_many_runs/.test(body)) return EDIT_TOO_MANY_RUNS;
  if (/not_last/.test(body)) return EDIT_NOT_LAST;
  if (/not_yours/.test(body) || status === 403) return EDIT_NOT_YOURS;
  if (/run_in_progress/.test(body) || status === 202 || status === 409) return EDIT_RUN_BUSY;
  return EDIT_UNSENT;
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

/**
 * The thread after the server answered an edit, and the answer.
 *
 * Accepted: the superseded turn and its replies give way to `pair` (the
 * edited turn and its reply), and their ids are tombstoned. Refused: the
 * SAME messages come back untouched and nothing is tombstoned, so the save
 * effect writes nothing new and a reload shows no fork (review of #795).
 */
export function settleEditAnswer(
  threadId: string,
  messages: ChatMessage[],
  supersededId: string,
  status: number,
  accepted: boolean,
  body: string,
  pair: readonly ChatMessage[],
): { messages: ChatMessage[]; outcome: EditOutcome } {
  if (!accepted) {
    return { messages, outcome: { ok: false, reason: editRefusalReason(status, body) } };
  }
  markSuperseded(threadId, supersededIds(messages, supersededId));
  return {
    messages: supersedeLocal(messages, supersededId, pair) ?? [...messages, ...pair],
    outcome: { ok: true },
  };
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
  /** Start the new run, and resolve with the server's answer to the edit.
   *  It replaces the superseded turn only when the answer is yes. */
  send: (text: string, opts: { supersedes: string }) => Promise<EditOutcome>;
}

/**
 * Edit the last user message: stop a run in flight, wait for the stop, then
 * send the edited text as the replacement.
 *
 * A message that is not the last user turn, or an empty text, is refused
 * here and nothing is sent, so an edit can never add a turn. Otherwise the
 * answer is the server's. The tombstones (`markSuperseded`) are written
 * where the server accepts the edit, in `useAgentChat.sendMessage`.
 */
export async function submitEdit(
  deps: EditDeps,
  messageId: string,
  text: string,
): Promise<EditOutcome> {
  const trimmed = text.trim();
  const before = deps.getMessages();
  if (!trimmed || lastUserMessageId(before) !== messageId) {
    return { ok: false, reason: EDIT_NOT_LAST };
  }
  if (deps.isRunning()) await deps.stop();
  return deps.send(trimmed, { supersedes: messageId });
}
