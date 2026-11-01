/**
 * The receipt of a reminder write, read from the tool's text (WS-66 RX-2).
 *
 * `set_reminder`, `update_reminder` and `reschedule_reminder` act at once in a
 * chat that may send. Their reply is a receipt (`writes.py::_reminder_receipt`):
 *
 *     Reminder set: «Get a haircut» at Fri 31 Oct, 17:00 IST.
 *       state scheduled · kind action · private, yours. A reminder, not a task.
 *       reminder_id: <uuid>
 *       fire_at: 2026-10-31T11:30:00+00:00
 *       zone: Asia/Kolkata
 *       task_id: <uuid>            (only when the reminder links a task)
 *       undo: drop | reschedule | patch   (only when Undo can put it back)
 *       was_fire_at / was_zone / was_title / was_kind   (what Undo puts back)
 *
 * The card (`components/projects/ReminderReceiptCard.tsx`) draws the receipt
 * from these lines, so it shows a real reminder: its title, its exact time and
 * its relative time. The member never reads the machine lines
 * (`forPeopleFenced` drops them). This file is pure, so it is testable.
 *
 * Fence (R7): `reminderReceipt.test.ts` beside this file.
 */
import { REMINDERS_BASE } from "@/lib/reminderWrite";
import { refusalWords, runReminderAct, UNREACHABLE_WORDS } from "@/lib/shell/reminderOpen";

/** The tools whose receipt draws as a reminder card. */
export const REMINDER_RECEIPT_TOOLS: ReadonlySet<string> = new Set([
  "set_reminder",
  "update_reminder",
  "reschedule_reminder",
]);

/** How a receipt is undone: drop a new reminder, move one back, or restore its words and time. */
export type UndoKind = "drop" | "reschedule" | "patch";

export interface ReminderReceipt {
  id: string;
  title: string;
  fireAt: string;
  fireMs: number;
  zone: string | null;
  /** The zone as the member reads it ("IST"), from the first line of the receipt. Null when there is none. */
  zoneName: string | null;
  taskId: string | null;
  undo: UndoKind | null;
  was: { title?: string; kind?: string; fireAt?: string; zone?: string };
}

const UUID = "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}";

/** The value of a `  key: value` machine line, or null. */
function line(result: string, key: string): string | null {
  const m = result.match(new RegExp("^\\s*" + key + ":\\s*(.+?)\\s*$", "im"));
  return m ? m[1] : null;
}

/** The receipt of a reminder write, or null when the text is not one. */
export function readReminderReceipt(tool: string, result: string): ReminderReceipt | null {
  if (!REMINDER_RECEIPT_TOOLS.has(tool)) return null;
  const id = line(result, "reminder_id");
  if (!id || !new RegExp(`^${UUID}$`, "i").test(id)) return null;
  const fireAt = line(result, "fire_at");
  const fireMs = fireAt ? Date.parse(fireAt) : NaN;
  if (!fireAt || Number.isNaN(fireMs)) return null;
  const title = result.match(/^[^\n«]*«(.*)»\s+at\s/m)?.[1]?.trim() ?? "";
  const task = line(result, "task_id");
  const undo = line(result, "undo");
  const was: ReminderReceipt["was"] = {};
  const wasTitle = line(result, "was_title");
  const wasKind = line(result, "was_kind");
  const wasFire = line(result, "was_fire_at");
  const wasZone = line(result, "was_zone");
  if (wasTitle) was.title = wasTitle;
  if (wasKind === "action" || wasKind === "notice") was.kind = wasKind;
  if (wasFire && !Number.isNaN(Date.parse(wasFire))) was.fireAt = wasFire;
  if (wasZone) was.zone = wasZone;
  return {
    id: id.toLowerCase(),
    title,
    fireAt,
    fireMs,
    zone: line(result, "zone"),
    zoneName: result.split(String.fromCharCode(10))[0].match(/ at .* ([A-Z]{2,5})[.]?$/)?.[1] ?? null,
    taskId: task && new RegExp(`^${UUID}$`, "i").test(task) ? task.toLowerCase() : null,
    undo: undo === "drop" || undo === "reschedule" || undo === "patch" ? undo : null,
    was,
  };
}

/**
 * Whether the card may offer Undo now. A new reminder is always dropped. A
 * move goes back only to a time after now, because the loop fires a past
 * `scheduled` reminder within a minute (`moveRefusal` in the Calendar rules).
 */
export function canUndo(r: ReminderReceipt, nowMs: number = Date.now()): boolean {
  if (r.undo === "drop") return true;
  if (r.undo === "reschedule") return !!r.was.fireAt && Date.parse(r.was.fireAt) > nowMs;
  if (r.undo === "patch") return !!r.was.title || !!r.was.kind || (!!r.was.fireAt && Date.parse(r.was.fireAt) > nowMs);
  return false;
}

/** The route call that puts a receipt back, or null. `drop` goes through `runReminderAct`. */
export function undoRequest(
  r: ReminderReceipt,
  nowMs: number = Date.now(),
): { method: "POST" | "PATCH"; url: string; body: Record<string, string> } | null {
  const base = `${REMINDERS_BASE}/${encodeURIComponent(r.id)}`;
  const future = !!r.was.fireAt && Date.parse(r.was.fireAt) > nowMs;
  if (r.undo === "reschedule" && future) {
    const body: Record<string, string> = { fire_at: r.was.fireAt! };
    if (r.was.zone) body.tz = r.was.zone;
    return { method: "POST", url: `${base}/reschedule`, body };
  }
  if (r.undo === "patch") {
    const body: Record<string, string> = {};
    if (r.was.title) body.title = r.was.title;
    if (r.was.kind) body.kind = r.was.kind;
    if (future) {
      body.fire_at = r.was.fireAt!;
      if (r.was.zone) body.tz = r.was.zone;
    }
    return Object.keys(body).length ? { method: "PATCH", url: base, body } : null;
  }
  return null;
}

/** The Calendar link of a reminder: it opens the Reminders panel and marks the row. */
export function calendarReminderHref(id: string): string {
  return `/calendar?reminder=${encodeURIComponent(id)}`;
}

/**
 * Undo a receipt. A new reminder is dropped through `runReminderAct`, the one
 * client of `/resolve`. A move or a change goes back through its own call.
 * The words of a refusal come from `refusalWords`.
 */
export async function runUndo(
  r: ReminderReceipt,
  fetchImpl: typeof fetch = fetch,
  nowMs: number = Date.now(),
): Promise<{ ok: true } | { ok: false; words: string }> {
  if (r.undo === "drop") {
    const got = await runReminderAct(r.id, "drop", fetchImpl);
    return got.ok ? { ok: true } : { ok: false, words: got.words };
  }
  const req = undoRequest(r, nowMs);
  if (!req) return { ok: false, words: "This change cannot be undone here." };
  try {
    const res = await fetchImpl(req.url, {
      method: req.method,
      cache: "no-store",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req.body),
    });
    if (res.ok) return { ok: true };
    const body: unknown = await res.json().catch(() => null);
    return { ok: false, words: refusalWords(res.status, body, "Metorite could not undo this. Try again.") };
  } catch {
    return { ok: false, words: UNREACHABLE_WORDS };
  }
}
