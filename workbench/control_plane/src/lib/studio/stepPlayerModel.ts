/**
 * The pure half of the guided step player (`ai_studio.md` §14 D14, slice STU-D14a).
 *
 * A run is a parent record. Its steps are line records (C13) with the fields in `STEP_FIELDS`.
 * The player shows one step at a time. This file orders the steps, finds the step where a member
 * resumes, gives the progress words, holds the GATE of a required check, builds the write of a
 * sign-off, and reads the sign-off line from the row meta. It has no React import.
 * Fence: `stepPlayerModel.test.ts`.
 *
 * ⚠️ IDENTITY. A sign-off writes RESULT keys only: `done`, and a note. The member and the time
 * come from the session at the store, which stamps `updated_by` and `updated_at` (R5). So the
 * write never holds an identity key, and the sign-off line is read from the row meta, never typed.
 *
 * ⚠️ D14a draws on sample data only. The real door drops the row meta (`flattenRecords`), so a
 * live player waits for D14b.
 */
import type { ActionResult, Row, StudioDataDoor } from "./dataDoor";
import type { FieldSpec } from "./fieldCheck";
import { readRefusal } from "./formModel";
import { safeAddress } from "./galleryModel";

/** A live door reads at most this many records, so the client sort is bounded. */
export const MAX_STEPS = 100;

/** The fields of a step line. `check` is the required check. `note` is the optional result note. */
export const STEP_FIELDS: FieldSpec[] = [
  { key: "order", type: "number", label: "Order" },
  { key: "title", type: "text", label: "Step", required: true },
  { key: "instruction", type: "richText", label: "Instruction" },
  { key: "photo", type: "file", label: "Photo" },
  { key: "check", type: "checkbox", label: "Required check" },
  { key: "done", type: "checkbox", label: "Done" },
  { key: "note", type: "text", label: "Note" },
];

/** The keys that a sign-off may write. No identity key is on the list. */
export const RESULT_KEYS = ["done", "note"] as const;

const isObject = (v: unknown): v is Record<string, unknown> =>
  !!v && typeof v === "object" && !Array.isArray(v);

/** The steps in the order of their `order` field. A step with no number goes last. The sort is stable. */
export function orderSteps(rows: readonly Row[]): Row[] {
  const n = (r: Row) => (typeof r.order === "number" && Number.isFinite(r.order) ? r.order : Infinity);
  return rows
    .filter(isObject)
    .slice(0, MAX_STEPS)
    .map((r, i) => ({ r, i }))
    .sort((a, b) => (n(a.r) === n(b.r) ? a.i - b.i : n(a.r) < n(b.r) ? -1 : 1))
    .map((x) => x.r);
}

export const isDone = (step: Row | undefined): boolean => step?.done === true;

export function doneCount(steps: readonly Row[]): number {
  return steps.filter(isDone).length;
}

/** The place where a member resumes: the first step that is not done. With every step done, the last one. */
export function resumeAt(steps: readonly Row[]): number {
  const at = steps.findIndex((s) => !isDone(s));
  return at < 0 ? Math.max(0, steps.length - 1) : at;
}

/** 'Step N of M', for the place `index` (from 0). */
export function progressLine(index: number, total: number): string {
  return `Step ${Math.min(total, Math.max(0, index) + 1)} of ${total}`;
}

/** 'N of M done'. */
export function doneLine(steps: readonly Row[]): string {
  return `${doneCount(steps)} of ${steps.length} done`;
}

/** The percent of steps that are done. */
export function donePercent(steps: readonly Row[]): number {
  return steps.length === 0 ? 0 : (doneCount(steps) / steps.length) * 100;
}

/** A step has a required check when its row carries a `check` value. A row with no `check` has none to draw. */
export function requiresCheck(step: Row | undefined): boolean {
  return typeof step?.check === "boolean";
}

/** The check as the member sees it at first: ticked when the stored value is true. */
export const initialTick = (step: Row | undefined): boolean => step?.check === true;

export const CHECK_FIRST = "Tick the check before you sign off this step.";

/** THE GATE. A step with a required check does not pass while the check is not ticked. */
export function gate(step: Row | undefined, ticked: boolean): { ok: true } | { ok: false; message: string } {
  if (requiresCheck(step) && !ticked) return { ok: false, message: CHECK_FIRST };
  return { ok: true };
}

/** The args of a sign-off: the record id and the result keys. Never an identity key. */
export function signArgs(step: Row, note?: string): { id: string; data: Record<string, unknown> } {
  const data: Record<string, unknown> = { done: true };
  const text = typeof note === "string" ? note.trim() : "";
  if (text) data.note = text;
  return { id: String(step.id), data };
}

/** The update action of an object: `object:<name>.update`. */
export const updateAction = (object: string): string => `object:${object}.update`;

/** 'Signed by <name> at <time>' from the row meta, or null for a step with no sign-off. */
export function signedLine(step: Row | undefined): string | null {
  if (!isDone(step)) return null;
  const by = typeof step?.updated_by === "string" ? step.updated_by.trim() : "";
  const at = typeof step?.updated_at === "string" ? Date.parse(step.updated_at) : NaN;
  if (!by || Number.isNaN(at)) return null;
  const time = new Date(at).toISOString().slice(0, 16).replace("T", " ");
  return `Signed by ${by} at ${time} UTC`;
}

/** The row meta of an ok answer: who and when. The store stamps both. */
export function metaOfAnswer(data: unknown): { updated_by: string; updated_at: string } | null {
  const o = isObject(data) ? data : {};
  return typeof o.updated_by === "string" && typeof o.updated_at === "string"
    ? { updated_by: o.updated_by, updated_at: o.updated_at }
    : null;
}

/** The one sentence of a refusal: the rule or message of the answer, else the first field error. */
export function refusalText(result: ActionResult): string {
  const r = readRefusal(result.data);
  const first = Object.values(r.fieldErrors)[0];
  return r.message ?? first ?? (result.message?.trim() || "That did not save.");
}

export type SignResult =
  | { ok: true; message: string; meta: { updated_by: string; updated_at: string } | null }
  | { ok: false; message: string };

/**
 * THE one sign-off. The gate runs first, and a refused press makes NO door call. Then ONE `run`
 * with the result keys. An `ok:false` answer keeps the member on the step with its refusal text.
 */
export async function signOff(
  door: Pick<StudioDataDoor, "run">,
  action: string,
  step: Row,
  ticked: boolean,
  note?: string,
): Promise<SignResult> {
  const g = gate(step, ticked);
  if (!g.ok) return { ok: false, message: g.message };
  let answer: ActionResult;
  try {
    answer = await door.run(action, signArgs(step, note));
  } catch {
    return { ok: false, message: "The sign-off did not reach the server. Try again." };
  }
  if (!answer.ok) return { ok: false, message: refusalText(answer) };
  return { ok: true, message: answer.message?.trim() || "Signed", meta: metaOfAnswer(answer.data) };
}

/** The image address of a step photo, or null for the placeholder. Only `safeAddress` lets one through. */
export function photoSrc(step: Row | undefined, address: StudioDataDoor["fileAddress"] | null | undefined): string | null {
  const v = step?.photo;
  const id = isObject(v) && typeof v.id === "string" && v.id ? v.id : null;
  if (!id || !address) return null;
  try {
    return safeAddress(address(id));
  } catch {
    return null;
  }
}

/** A text value of a step, or an empty string for a field that the door left out (C8). */
export function textOf(step: Row | undefined, key: string): string {
  const v = step?.[key];
  return typeof v === "string" ? v : typeof v === "number" ? String(v) : "";
}
