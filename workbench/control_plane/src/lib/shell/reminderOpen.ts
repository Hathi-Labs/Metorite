/**
 * A tap on a reminder link opens the one assistant (WS-66 RM-4,
 * `calendar_events_reminders.md` §5.7, D114).
 *
 * The push, the feed card and the Calendar pin open `/chat?reminder=<id>`.
 * `ChatFullView` (`app/chat/page.tsx`) reads the id (`reminderParam`) and
 * opens a fresh chat with it. The dock then calls `requestReminderOpen` once
 * for that chat. The server builds every word: the first message, the
 * `reminderCard` with the chips, and the context block (D-PM-39). This file
 * only asks, and keeps what came back.
 *
 * **The store.** The newest row of each reminder that this page knows, from
 * the open and from each chip. The card draws from it, so a chip's answer
 * still shows after a remount. The dock reads it to withdraw the context
 * block when a chip closes the reminder (`reminderFactsShown`). Memory only,
 * and a change of member or org clears it (`onClear`).
 *
 * **The chips** (§5.7 item 3). Done and Drop act at once through
 * `/resolve`, and Undo puts the reminder back through `/reopen` (D-CF-5).
 * Not yet moves it to the smart slot through `/reschedule` and says the new
 * time. Later and Make it a task send the member's words to the model, so
 * the model asks for a time, or shows the one confirm of `promote_reminder`.
 * No chip calls `/promote` itself.
 *
 * **The Calendar** (RM-5) closes and opens a reminder through the same
 * `runReminderAct`, and its other writes read `refusalWords` and
 * `UNREACHABLE_WORDS` from here. So `/resolve` and `/reopen` have one client,
 * and a refusal has one set of words.
 *
 * Fence (R7): `app/chat/reminderOpen.test.ts`.
 */
import { useSyncExternalStore } from "react";

import { onClear } from "@/lib/dataCache";

import type { AssistantFacts } from "./assistant";

// ── The link ─────────────────────────────────────────────────────────────────

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** The reminder id of a chat link, or undefined when it is not a UUID. */
export function reminderParam(params: URLSearchParams): string | undefined {
  const raw = params.get("reminder")?.trim() ?? "";
  return UUID_RE.test(raw) ? raw.toLowerCase() : undefined;
}

/** The route that opens a reminder in one chat. */
export function reminderOpenPath(sessionId: string): string {
  return `/api/chat/sessions/${encodeURIComponent(sessionId)}/reminder-open`;
}

// ── The row ──────────────────────────────────────────────────────────────────

/** The fields of a reminder row that the chat reads. */
export interface ReminderRow {
  id: string;
  title: string;
  kind: string;
  state: string;
  resolution: string | null;
  fire_at: string | null;
  tz: string | null;
}

const str = (v: unknown): string | null => (typeof v === "string" ? v : null);

/** A row from a route's answer, or null when it is not one. */
export function readReminderRow(value: unknown): ReminderRow | null {
  if (!value || typeof value !== "object") return null;
  const v = value as Record<string, unknown>;
  const id = str(v.id);
  const state = str(v.state);
  if (!id || !UUID_RE.test(id) || !state) return null;
  return {
    id: id.toLowerCase(),
    title: str(v.title) ?? "",
    kind: str(v.kind) ?? "action",
    state,
    resolution: str(v.resolution),
    fire_at: str(v.fire_at),
    tz: str(v.tz),
  };
}

// ── The open ─────────────────────────────────────────────────────────────────

export interface ReminderOpened {
  /** Whether this call wrote the first row. False when the chat held it. */
  posted: boolean;
  /** The first message, which the session list shows as its preview. */
  preview: string;
  reminder: ReminderRow;
  /** The context block: the chip's label and the facts, which the server fenced. */
  context: AssistantFacts;
}

function readFacts(value: unknown): AssistantFacts | null {
  if (!value || typeof value !== "object") return null;
  const v = value as Record<string, unknown>;
  if (!Array.isArray(v.facts)) return null;
  const facts = v.facts.filter((f): f is string => typeof f === "string" && f.trim() !== "");
  return { label: str(v.label) ?? undefined, facts };
}

/**
 * How long the chat waits for the open. The BFF route stops its own wait at
 * 15 s, so its answer, good or bad, always comes first. This is the backstop.
 * The brief keeps its own short `BRIEF_HOLD_MS`, because a brief is optional.
 * A reminder's first row is the whole point of the link.
 */
export const REMINDER_HOLD_MS = 16_000;

/** An open that failed. `gone` is a 404: not the member's reminder, or no such id. */
export interface ReminderOpenFailed {
  failed: true;
  gone: boolean;
}

/** The words over a chat whose reminder did not open. */
export function openFailureWords(failure: ReminderOpenFailed): string {
  return failure.gone
    ? "This reminder is not yours, or it no longer exists."
    : "Metorite could not open this reminder. Tap the link again.";
}

/**
 * Ask the server to open `reminderId` in the chat `sessionId`. The body is
 * the id alone: the server reads the row and builds every word. Null on a
 * 204 (a shared room), where nothing is wrong. A 404, any other error and a
 * network fault answer `{failed: true}`, so the dock can say so. Nothing is
 * retried.
 */
export async function requestReminderOpen(
  sessionId: string,
  reminderId: string,
  fetchImpl: typeof fetch = fetch,
): Promise<ReminderOpened | ReminderOpenFailed | null> {
  try {
    const res = await fetchImpl(reminderOpenPath(sessionId), {
      method: "POST",
      cache: "no-store",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reminder_id: reminderId }),
    });
    if (res.status === 204) return null;
    if (res.status === 404) return { failed: true, gone: true };
    if (res.status !== 200) return { failed: true, gone: false };
    const body = (await res.json()) as Record<string, unknown> | null;
    const reminder = readReminderRow(body?.reminder);
    const context = readFacts(body?.context);
    if (!body || !reminder || !context) return { failed: true, gone: false };
    return {
      posted: body.posted === true,
      preview: str(body.preview) ?? "",
      reminder,
      context,
    };
  } catch {
    return { failed: true, gone: false };
  }
}

/** Whether an answer of `requestReminderOpen` is a failure. */
export function isOpenFailure(got: ReminderOpened | ReminderOpenFailed | null): got is ReminderOpenFailed {
  return got !== null && "failed" in got;
}

// One ask for each chat and reminder, and the chats that opened a reminder.
// Memory only, cleared with the rest on a change of member (`onClear`).
const _claimed = new Set<string>();
const _reminderChats = new Set<string>();

/**
 * Whether this page may ask to open `reminderId` in `sessionId`: true once
 * for each pair. A second mount of the dock, or React's second run of an
 * effect, asks nothing.
 */
export function claimReminderOpen(sessionId: string, reminderId: string): boolean {
  const key = `${sessionId}|${reminderId}`;
  if (!sessionId || !reminderId || _claimed.has(key)) return false;
  _claimed.add(key);
  _reminderChats.add(sessionId);
  return true;
}

/** Whether a reminder opened in this chat. The day's brief never lands there. */
export function opensReminder(sessionId: string): boolean {
  return _reminderChats.has(sessionId);
}

// ── The store ────────────────────────────────────────────────────────────────

/**
 * Who wrote the newest row of a reminder: the open, a chip, or the receipt
 * of a tool (or a 409) that told the card the reminder changed elsewhere.
 */
export type ReminderVia = "open" | "chip" | "receipt";

export interface KnownReminder {
  row: ReminderRow;
  via: ReminderVia;
}

const _known = new Map<string, KnownReminder>();
const _listeners = new Set<() => void>();

/** Keep the newest row of a reminder. */
export function noteReminder(row: ReminderRow, via: ReminderVia): void {
  _known.set(row.id, { row, via });
  _listeners.forEach((l) => l());
}

/**
 * Keep a new state for a reminder that something else changed: the model
 * through a tool, or a 409 that said the reminder is closed. The rest of the
 * row stays as the page knew it.
 */
export function noteReminderState(id: string, state: string, resolution: string | null): void {
  const key = id.toLowerCase();
  const was = _known.get(key)?.row;
  const row: ReminderRow = was
    ? { ...was, state, resolution }
    : { id: key, title: "", kind: "action", state, resolution, fire_at: null, tz: null };
  noteReminder(row, "receipt");
}

const RECEIPT_TOOLS: ReadonlySet<string> = new Set([
  "resolve_reminder", "reopen_reminder", "reschedule_reminder", "promote_reminder", "update_reminder",
]);

/**
 * The reminder that a tool receipt says changed, or null. The receipt prints
 * `reminder_id: <uuid>` and, but for `promote_reminder`, a line
 * `state <word> · kind <word>` (`writes.py::_reminder_receipt`). A promote
 * ends the reminder as done with the resolution `promoted`.
 */
export function reminderFromReceipt(
  tool: string,
  result: string,
): { id: string; state: string; resolution: string | null } | null {
  if (!RECEIPT_TOOLS.has(tool)) return null;
  const id = result.match(/^\s*reminder_id:\s*([0-9a-f-]{36})\s*$/im)?.[1];
  if (!id || !UUID_RE.test(id)) return null;
  if (tool === "promote_reminder") return { id: id.toLowerCase(), state: "done", resolution: "promoted" };
  const state = result.match(/^\s*state\s+([a-z_]+)\s*·/im)?.[1];
  if (!state) return null;
  const resolution = tool === "resolve_reminder" && (state === "done" || state === "dropped") ? state : null;
  return { id: id.toLowerCase(), state, resolution };
}

/** Write the state that a tool receipt reports into the store. True when it wrote one. */
export function noteReminderReceipt(tool: string, result: string): boolean {
  const changed = reminderFromReceipt(tool, result);
  if (!changed) return false;
  noteReminderState(changed.id, changed.state, changed.resolution);
  return true;
}

/** The reminder a 409 names as closed, from its words, or null. */
function closedStateOf409(detail: string | null): string | null {
  return detail?.match(/^This reminder is closed \(([a-z_]+)\)/)?.[1] ?? null;
}

/** The newest row of a reminder that this page knows, read once. */
export function knownReminder(id: string): KnownReminder | null {
  return _known.get(id.toLowerCase()) ?? null;
}

function subscribe(listener: () => void): () => void {
  _listeners.add(listener);
  return () => {
    _listeners.delete(listener);
  };
}

/** The newest row of a reminder that this page knows, live. */
export function useKnownReminder(id: string): KnownReminder | null {
  return useSyncExternalStore(subscribe, () => knownReminder(id), () => null);
}

/** For tests: forget every reminder and every ask. */
export function __resetReminderOpenForTests(): void {
  _known.clear();
  _claimed.clear();
  _reminderChats.clear();
  _listeners.clear();
}

onClear(() => {
  _known.clear();
  _claimed.clear();
  _reminderChats.clear();
  _listeners.forEach((l) => l());
});

// ── The rules ────────────────────────────────────────────────────────────────

const OPEN_STATES: ReadonlySet<string> = new Set(["scheduled", "due", "unconfirmed"]);

/** Whether a reminder still waits on the member or on the loop (§5.3). */
export function isOpenReminder(state: string | null | undefined): boolean {
  return OPEN_STATES.has(state ?? "");
}

/** Whether `/reopen` can undo the close: a done, or a drop (§5.3). */
export function isReopenable(state: string | null | undefined, resolution: string | null | undefined): boolean {
  return (state === "done" && resolution === "done") || state === "dropped";
}

/**
 * Whether the dock still publishes the context block. It leaves when a chip
 * closes the reminder, and comes back when Undo opens it again. A `notice`
 * that the open itself closed keeps it, so a typed answer still has it.
 */
export function reminderFactsShown(known: KnownReminder | null): boolean {
  if (!known) return true;
  return known.via === "open" || isOpenReminder(known.row.state);
}

// ── The chips ────────────────────────────────────────────────────────────────

export type ReminderAct = "done" | "not_yet" | "later" | "drop" | "task" | "undo";

/** The words of each chip (§5.7 item 3). */
export const REMINDER_CHIPS: Readonly<Record<ReminderAct, string>> = {
  done: "Done",
  not_yet: "Not yet",
  later: "Later",
  drop: "Drop",
  task: "Make it a task",
  undo: "Undo",
};

/** The member's turn that "Later" sends: the model asks for a time. */
export const LATER_TURN = "Snooze this reminder";
/** The member's turn that "Make it a task" sends: the model shows the confirm. */
export const TASK_TURN = "Make it a task";

/**
 * The chips of a reminder card. An open reminder offers all five. A closed
 * one offers Undo only when a chip of this page closed it and `/reopen` can
 * undo it. Any other closed reminder offers nothing.
 */
export function reminderActs(a: { state: string; resolution?: string | null; via?: ReminderVia }): ReminderAct[] {
  if (isOpenReminder(a.state)) return ["done", "not_yet", "later", "drop", "task"];
  if (a.via === "chip" && isReopenable(a.state, a.resolution)) return ["undo"];
  return [];
}

/** The chip a pill's words name, or null. */
export function actOfWords(words: string): ReminderAct | null {
  for (const [act, label] of Object.entries(REMINDER_CHIPS)) if (label === words) return act as ReminderAct;
  return null;
}

/** The member's turn of a chip that the model answers, or null for a route act. */
export function actTurn(act: ReminderAct): string | null {
  if (act === "later") return LATER_TURN;
  if (act === "task") return TASK_TURN;
  return null;
}

/**
 * The route call of a chip, through the Projects proxy, or null for a chip
 * that the model answers. The bodies are the RM-1 routes' own: `/resolve`
 * takes `action`, `/reschedule` takes `fire_at`, and `/reopen` takes none.
 */
export function actRequest(id: string, act: ReminderAct): { url: string; body: Record<string, string> } | null {
  const base = `/api/projects/my/reminders/${encodeURIComponent(id)}`;
  switch (act) {
    case "done":
      return { url: `${base}/resolve`, body: { action: "done" } };
    case "drop":
      return { url: `${base}/resolve`, body: { action: "drop" } };
    case "not_yet":
      return { url: `${base}/reschedule`, body: { fire_at: "smart" } };
    case "undo":
      return { url: `${base}/reopen`, body: {} };
    default:
      return null;
  }
}

export type ActResult = { ok: true; row: ReminderRow } | { ok: false; words: string };

/** The words of a write that did not reach the server. */
export const UNREACHABLE_WORDS = "Metorite could not reach the server. Check the connection, then try again.";

/**
 * The words of a refused reminder write. Never a raw status, and never an
 * error object. A 409 says the server's own words, and a 404 says the
 * reminder is gone. Any other status says `fallback`. The chips and the
 * Calendar (`app/calendar/lib/reminders.ts`) both read this one rule.
 */
export function refusalWords(
  status: number,
  body: unknown,
  fallback = "Metorite could not save your answer. Try again.",
): string {
  const detail = body && typeof body === "object" ? str((body as Record<string, unknown>).detail) : null;
  if (status === 409 && detail) return detail;
  if (status === 404) return "Metorite could not find this reminder. It may be gone.";
  return fallback;
}

/**
 * Run one route chip. On success the newest row goes into the store, so the
 * card and the dock both see it. On a refusal the card shows the words.
 */
export async function runReminderAct(
  id: string,
  act: ReminderAct,
  fetchImpl: typeof fetch = fetch,
): Promise<ActResult> {
  const req = actRequest(id, act);
  if (!req || !UUID_RE.test(id)) return { ok: false, words: "This reminder cannot change here." };
  try {
    const res = await fetchImpl(req.url, {
      method: "POST",
      cache: "no-store",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req.body),
    });
    const body: unknown = await res.json().catch(() => null);
    const row = res.ok ? readReminderRow(body) : null;
    if (row) {
      noteReminder(row, "chip");
      return { ok: true, row };
    }
    // A 409 that says the reminder is closed: the model or another tab closed
    // it. Keep the state, so the card draws no chip that can only fail again.
    const detail = body && typeof body === "object" ? str((body as Record<string, unknown>).detail) : null;
    const closedState = res.status === 409 ? closedStateOf409(detail) : null;
    if (closedState) noteReminderState(id, closedState, null);
    return { ok: false, words: refusalWords(res.status, body) };
  } catch {
    return { ok: false, words: UNREACHABLE_WORDS };
  }
}

// ── The words after a chip ───────────────────────────────────────────────────

/** A zone the browser knows, or undefined for the browser's own. The Calendar reads it too. */
export function knownZone(zone: string | null | undefined): string | undefined {
  if (!zone) return undefined;
  try {
    new Intl.DateTimeFormat("en-GB", { timeZone: zone });
    return zone;
  } catch {
    return undefined;
  }
}

/**
 * "Moved to Sat 10:00." — the new time after Not yet, in `zone` (§5.7 item
 * 3). Today and tomorrow say so. A day in the next six days is its weekday,
 * and a later day adds its date.
 */
export function movedLine(fireAt: string | null | undefined, zone: string | null | undefined, now: Date = new Date()): string {
  const at = fireAt ? new Date(fireAt) : null;
  if (!at || Number.isNaN(at.getTime())) return "Moved.";
  const timeZone = knownZone(zone);
  const fmt = (o: Intl.DateTimeFormatOptions, d: Date = at) => new Intl.DateTimeFormat("en-GB", { ...o, timeZone }).format(d);
  const clock = fmt({ hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
  const dayOf = (d: Date) => Date.parse(new Intl.DateTimeFormat("en-CA", { timeZone, year: "numeric", month: "2-digit", day: "2-digit" }).format(d));
  const days = Math.round((dayOf(at) - dayOf(now)) / 86_400_000);
  if (days === 0) return `Moved to ${clock} today.`;
  if (days === 1) return `Moved to ${clock} tomorrow.`;
  const day = days > 1 && days <= 6 ? fmt({ weekday: "short" }) : fmt({ weekday: "short", day: "numeric", month: "short" });
  return `Moved to ${day} ${clock}.`;
}

/** How a closed reminder closed, in a few words. An open one has none. */
export function closedWords(state: string | null | undefined, resolution: string | null | undefined): string {
  if (state === "done") {
    if (resolution === "promoted") return "Made into a task.";
    if (resolution === "evidence") return "Closed on its own.";
    return "Done.";
  }
  if (state === "dropped") return "Dropped.";
  if (state === "cancelled") return "Cancelled.";
  return "";
}

/** The label over the chips: an answer for an open reminder, else the Undo. */
export function chipsLabel(acts: readonly ReminderAct[]): string {
  return acts.length === 1 && acts[0] === "undo" ? "Changed your mind?" : "Your answer";
}

/** The line a card shows after a chip's route answered. */
export function actResultWords(act: ReminderAct, row: ReminderRow, zone?: string | null, now: Date = new Date()): string {
  switch (act) {
    case "done":
      return "Done. The reminder is closed.";
    case "drop":
      return "Dropped. The reminder is closed.";
    case "not_yet":
      return movedLine(row.fire_at, row.tz ?? zone, now);
    case "undo":
      return "The reminder is open again.";
    default:
      return "";
  }
}
