/**
 * The logic of the Form block (`ai_studio.md` §5.1, slice STU-D2a), apart
 * from React so a test can drive it in the node environment.
 *
 * A draft is checked on blur and on submit with `fieldCheck.ts`. A submit
 * that passes goes through `door.run`, once: a second press while the first
 * is pending does nothing (DESIGN_SYSTEM.md §12, M3). The server stays the
 * authority. Its refusal of a field shows under that field.
 */
import { invalidate } from "@/lib/dataCache";
import { type Draft, draftToValue, emptyDraft, valueToDraft } from "./blocks";
import { doorPrefix, type StudioDataDoor } from "./dataDoor";
import { type FieldSpec, validateRecord } from "./fieldCheck";
import { ACTION_FAILED, pressOnce } from "./TypedActionButton";

export type Drafts = Record<string, Draft>;

/** A draft for each field, from the stored record when there is one. */
export function initialDrafts(
  fields: readonly FieldSpec[],
  initial?: Record<string, unknown>,
): Drafts {
  const out: Drafts = {};
  for (const f of fields) out[f.key] = initial ? valueToDraft(f, initial[f.key]) : emptyDraft(f);
  return out;
}

/** The record that the drafts stand for. A blank control leaves its key out. */
export function recordOf(fields: readonly FieldSpec[], drafts: Drafts): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const f of fields) {
    const v = draftToValue(f, drafts[f.key] ?? emptyDraft(f));
    if (v !== undefined) out[f.key] = v;
  }
  return out;
}

/** The problem of one field on blur, or `null`. */
export function blurProblem(fields: readonly FieldSpec[], drafts: Drafts, key: string): string | null {
  const f = fields.find((x) => x.key === key);
  if (!f) return null;
  const r = validateRecord([f], recordOf([f], drafts));
  return r.ok ? null : r.problems[0].message;
}

export type FormCheck =
  | { ok: true; value: Record<string, unknown> }
  | { ok: false; errors: Record<string, string>; first: string | null };

/** Check the whole form. `first` is the first bad field in form order. */
export function checkForm(fields: readonly FieldSpec[], drafts: Drafts): FormCheck {
  const r = validateRecord(fields, recordOf(fields, drafts));
  if (r.ok) return { ok: true, value: r.value };
  const errors: Record<string, string> = {};
  for (const p of r.problems) if (!(p.field in errors)) errors[p.field] = p.message;
  const first = fields.find((f) => f.key in errors)?.key ?? null;
  return { ok: false, errors, first };
}

export type SendResult =
  | { kind: "busy" }
  | { kind: "sent"; ok: boolean; message: string | null; fieldErrors: Record<string, string> };

/**
 * The 422 of the door: `data.fieldErrors` maps a field key to one sentence.
 * ⚠️ B2c fixes the final shape of a refusal. Until it lands, this is the
 * shape of the mocked door in the preview.
 */
export function readFieldErrors(data: unknown): Record<string, string> {
  const raw = data && typeof data === "object" ? (data as Record<string, unknown>).fieldErrors : null;
  const out: Record<string, string> = {};
  if (raw && typeof raw === "object" && !Array.isArray(raw)) {
    for (const [k, v] of Object.entries(raw)) if (typeof v === "string" && v.trim()) out[k] = v;
  }
  return out;
}

/** Send a checked record through the door. A second call while one is pending is `busy`. */
export async function sendRecord(
  door: StudioDataDoor,
  action: string,
  value: Record<string, unknown>,
  busy: { current: boolean },
  extra?: Record<string, unknown>,
): Promise<SendResult> {
  let result: SendResult = { kind: "busy" };
  await pressOnce(busy, async () => {
    try {
      const res = await door.run(action, { ...extra, data: value });
      if (res && res.ok === true) {
        invalidate(doorPrefix(door));
        result = { kind: "sent", ok: true, message: typeof res.message === "string" ? res.message : null, fieldErrors: {} };
        return;
      }
      const message = res && typeof res.message === "string" && res.message.trim() ? res.message : ACTION_FAILED;
      result = { kind: "sent", ok: false, message, fieldErrors: readFieldErrors(res?.data) };
    } catch {
      result = { kind: "sent", ok: false, message: ACTION_FAILED, fieldErrors: {} };
    }
  });
  return result;
}
