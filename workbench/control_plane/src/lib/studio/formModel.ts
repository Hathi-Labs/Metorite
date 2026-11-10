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
import { type Draft, draftToValue, emptyDraft, manyUnsupported, valueToDraft } from "./blocks";
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

const sameDraft = (a: Draft, b: Draft): boolean => JSON.stringify(a) === JSON.stringify(b);

/**
 * The record that the drafts stand for. A blank control leaves its key out.
 * With `initial` (an edit), a field whose draft is unchanged keeps its stored
 * value, so a datetime with seconds is not rewritten by a save.
 */
export function recordOf(
  fields: readonly FieldSpec[],
  drafts: Drafts,
  initial?: Record<string, unknown>,
): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const f of fields) {
    const d = drafts[f.key] ?? emptyDraft(f);
    if (initial && initial[f.key] != null && sameDraft(d, valueToDraft(f, initial[f.key]))) {
      out[f.key] = initial[f.key];
      continue;
    }
    const v = draftToValue(f, d);
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

/**
 * Check the whole form. `first` is the first bad field in form order.
 * A field with no control (`manyUnsupported`) is skipped. With `initial` (an
 * edit), a field that held a value and is now blank is sent as `null`, so a
 * merge on the server clears it.
 */
export function checkForm(
  fields: readonly FieldSpec[],
  drafts: Drafts,
  initial?: Record<string, unknown>,
): FormCheck {
  const drawn = fields.filter((f) => !manyUnsupported(f));
  const r = validateRecord(drawn, recordOf(drawn, drafts, initial));
  if (r.ok) {
    const value = { ...r.value };
    if (initial) for (const f of drawn) if (initial[f.key] != null && !(f.key in value)) value[f.key] = null;
    return { ok: true, value };
  }
  const errors: Record<string, string> = {};
  for (const p of r.problems) if (!(p.field in errors)) errors[p.field] = p.message;
  const first = fields.find((f) => f.key in errors)?.key ?? null;
  return { ok: false, errors, first };
}

export type SendResult =
  | { kind: "busy" }
  | {
    kind: "sent";
    ok: boolean;
    message: string | null;
    fieldErrors: Record<string, string>;
    /** The rule that refused the write, or null. */
    rule?: string | null;
  };

/*
 * THE REFUSAL CONTRACT (D2a; D2b and B2c must meet it)
 *
 * A door that refuses a write answers `{ ok: false, message, data }` with
 *   data = { fieldErrors?: { <field key>: <one sentence> }, rule?: string, message?: string }
 * The store refuses in three shapes, and B2c maps each to that answer:
 *   FieldError(field, msg)                    -> fieldErrors[field] = msg
 *   RuleViolation(rule, msg)                  -> rule, message
 *   ConstraintViolation(rule, object, msg)    -> rule, message
 * The Form shows a field sentence under its field. A `rule` shows in the
 * status line as "Refused by <rule>: <message>". A `fieldErrors` key that
 * names no field of the form joins the status line, and is never dropped.
 */
export interface Refusal {
  fieldErrors: Record<string, string>;
  rule: string | null;
  message: string | null;
}

export function readRefusal(data: unknown): Refusal {
  const o = data && typeof data === "object" && !Array.isArray(data) ? (data as Record<string, unknown>) : {};
  const fieldErrors: Record<string, string> = {};
  const raw = o.fieldErrors;
  if (raw && typeof raw === "object" && !Array.isArray(raw)) {
    for (const [k, v] of Object.entries(raw)) if (typeof v === "string" && v.trim()) fieldErrors[k] = v;
  }
  const text = (v: unknown) => (typeof v === "string" && v.trim() ? v : null);
  return { fieldErrors, rule: text(o.rule), message: text(o.message) };
}

/** The sentences for the status line: a rule, and each error that names no field. */
export function statusLines(
  fields: readonly FieldSpec[],
  out: { message: string | null; fieldErrors: Record<string, string>; rule?: string | null },
): string[] {
  const known = new Set(fields.map((f) => f.key));
  const lines: string[] = [];
  if (out.rule) lines.push(`Refused by ${out.rule}: ${out.message ?? ACTION_FAILED}`);
  for (const [k, v] of Object.entries(out.fieldErrors)) if (!known.has(k)) lines.push(`${k}: ${v}`);
  if (lines.length === 0 && !Object.keys(out.fieldErrors).some((k) => known.has(k))) {
    lines.push(out.message ?? ACTION_FAILED);
  }
  return lines;
}

export interface SendOutcome {
  /** The drafts to show next. */
  drafts: Drafts;
  errors: Record<string, string>;
  /** One short sentence for a toast, or null. */
  toast: string | null;
  /** The line under the button, or null. */
  status: { ok: boolean; text: string } | null;
  /** The key to focus, or null. */
  focus: string | null;
}

/**
 * What the form shows after a send. A save that worked says so in a toast,
 * empties the form (a new record) and focuses the first field, so the member
 * cannot save the same record twice by mistake. A refusal keeps the values.
 */
export function afterSend(
  fields: readonly FieldSpec[],
  drafts: Drafts,
  out: Extract<SendResult, { kind: "sent" }>,
  opts: { savedMessage: string; reset: boolean },
): SendOutcome {
  if (out.ok) {
    return {
      drafts: opts.reset ? initialDrafts(fields) : drafts,
      errors: {},
      toast: opts.savedMessage,
      status: null,
      focus: opts.reset ? (fields[0]?.key ?? null) : null,
    };
  }
  // A refusal: each known field shows its own sentence. The rest go in the line.
  const known = new Set(fields.map((f) => f.key));
  const mine = Object.fromEntries(Object.entries(out.fieldErrors).filter(([k]) => known.has(k)));
  const firstBad = fields.find((f) => f.key in mine)?.key ?? null;
  const lines = statusLines(fields, out);
  return {
    drafts,
    errors: mine,
    toast: null,
    status: lines.length > 0 ? { ok: false, text: lines.join(" ") } : null,
    focus: firstBad,
  };
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
      const refusal = readRefusal(res?.data);
      const own = res && typeof res.message === "string" && res.message.trim() ? res.message : null;
      // A rule refusal carries its own sentence in `data.message`.
      const message = (refusal.rule ? refusal.message ?? own : own) ?? ACTION_FAILED;
      result = { kind: "sent", ok: false, message, fieldErrors: refusal.fieldErrors, rule: refusal.rule };
    } catch {
      result = { kind: "sent", ok: false, message: ACTION_FAILED, fieldErrors: {} };
    }
  });
  return result;
}
