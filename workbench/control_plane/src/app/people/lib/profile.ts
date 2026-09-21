/**
 * People Center · the profile's pure half (WS-28g).
 *
 * Spec: `project-docs/specs/people_center_app.md` §3, §5.3.
 *
 * ⚠️ **This is a LAYOUT catalogue, not a permission map.** It says what a field
 * is called, how to draw it and which panel it belongs to. It must never say
 * who may write it — that answer comes from the server as `editable_fields`
 * and the UI only ever asks whether a name is in that array (D-PC-4). A second
 * copy of the class map in TypeScript would drift, and it would drift silently
 * in the safe direction and loudly in the unsafe one: a control drawn, saved,
 * and 403'd after the click.
 *
 * Pure on purpose — no React, no fetch — so the interesting parts (what is
 * editable, what is missing, what a save actually sends) are testable as data.
 */

import type { PersonDetail } from "./api";

export type FieldKind =
  | "text"
  | "textarea"
  | "number"
  | "date"
  | "select"
  | "chips"
  | "hours"
  | "links"
  | "contact";

export type SectionKey = "about" | "work" | "capability" | "employment" | "private";

export interface Section {
  key: SectionKey;
  title: string;
  /** One line on what this panel is FOR — shown under the heading. */
  note: string;
}

export const SECTIONS: readonly Section[] = [
  {
    key: "about",
    title: "About you",
    note: "What the directory shows every colleague.",
  },
  {
    key: "work",
    title: "When and where you work",
    note: "How the scheduler and the assignment suggester read your week.",
  },
  {
    key: "capability",
    title: "What you can do",
    note: "What the assignment suggester matches a task against.",
  },
  {
    key: "employment",
    title: "Employment",
    note: "The organisation's record. Set by an administrator.",
  },
  {
    key: "private",
    title: "Private",
    note: "Visible only to you and to an HR administrator.",
  },
] as const;

export interface FieldSpec {
  name: string;
  label: string;
  kind: FieldKind;
  section: SectionKey;
  placeholder?: string;
  options?: readonly string[];
  /**
   * What filling this in actually buys — shown by the completeness meter
   * (§5.3). A meter that nags without naming the consequence is a nag.
   */
  why?: string;
}

/** The vocabularies the gateway validates against (`routes/people/fields.py`). */
export const EMPLOYMENT_TYPES = [
  "employee",
  "contractor",
  "intern",
  "vendor",
  "agent",
] as const;
export const SENIORITY_LEVELS = [
  "junior",
  "mid",
  "senior",
  "lead",
  "principal",
] as const;

export const FIELDS: readonly FieldSpec[] = [
  { name: "preferred_name", label: "Preferred name", kind: "text", section: "about",
    placeholder: "What people actually call you" },
  { name: "pronouns", label: "Pronouns", kind: "text", section: "about",
    placeholder: "she/her · he/him · they/them",
    why: "Agents write about you in the third person; without this they guess." },
  { name: "bio", label: "About", kind: "textarea", section: "about",
    placeholder: "A paragraph a new colleague would find useful" },
  { name: "links", label: "Links", kind: "links", section: "about" },
  { name: "languages", label: "Languages", kind: "chips", section: "about",
    why: "Answers which customer you can talk to." },

  { name: "location", label: "Location", kind: "text", section: "work",
    placeholder: "City or site" },
  { name: "timezone", label: "Time zone", kind: "text", section: "work",
    placeholder: "Asia/Kolkata",
    why: "Without it the scheduler assumes the org default, and a due date can land outside your day." },
  { name: "working_hours", label: "Working hours", kind: "hours", section: "work",
    why: "Stops a follow-up arriving at 11pm your time." },
  { name: "max_concurrent_tasks", label: "Comfortable parallel tasks",
    kind: "number", section: "work",
    why: "Your own ceiling. The suggester respects it before an hours figure derived from unestimated work." },

  { name: "skills", label: "Skills", kind: "chips", section: "capability",
    why: "The first and most defensible signal behind 'who should do this'." },
  { name: "interests", label: "Would like to work on", kind: "chips",
    section: "capability",
    why: "An assigner that only optimises for fit hands you the same work forever." },
  { name: "domain", label: "Domain", kind: "text", section: "capability" },
  { name: "years_experience", label: "Years of experience", kind: "number",
    section: "capability" },
  { name: "resume_summary", label: "Summary", kind: "textarea",
    section: "capability",
    placeholder: "Filled in from your CV, and yours to correct" },

  { name: "employee_id", label: "Employee ID", kind: "text", section: "employment" },
  { name: "employment_type", label: "Engagement", kind: "select",
    section: "employment", options: EMPLOYMENT_TYPES },
  { name: "seniority", label: "Level", kind: "select", section: "employment",
    options: SENIORITY_LEVELS },
  { name: "start_date", label: "Started", kind: "date", section: "employment" },
  { name: "end_date", label: "Engagement ends", kind: "date",
    section: "employment",
    why: "An assignment due after this date is a mistake the picker can warn about." },
  { name: "cost_center", label: "Cost centre", kind: "text",
    section: "employment" },

  { name: "phone", label: "Phone", kind: "text", section: "private" },
  { name: "emergency_contact", label: "Emergency contact", kind: "contact",
    section: "private" },
  { name: "birthday", label: "Birthday", kind: "text", section: "private",
    placeholder: "MM-DD" },
  { name: "personal_email", label: "Personal email", kind: "text",
    section: "private" },
] as const;

export interface RenderedField {
  spec: FieldSpec;
  value: unknown;
  /** Straight from the server's `editable_fields` — never decided here. */
  editable: boolean;
}

export interface RenderedSection {
  section: Section;
  fields: RenderedField[];
}

/**
 * The panels to draw, in order, for this person as seen by this caller.
 *
 * **A field the caller may not write is still rendered, read-only** — a person
 * should be able to see what the company records about them even where they
 * cannot change it. That is the whole difference between a profile and a form
 * (§5.3).
 *
 * A field the caller may not *read* is a different matter: the server has
 * already projected it to null, so it renders as an explicit blank rather than
 * being dropped, and the caller can tell "restricted" from "nobody filled it
 * in" by the panel's own restricted notice.
 */
export function renderSections(
  person: PersonDetail | null,
  opts: { includePrivate?: boolean } = {}
): RenderedSection[] {
  const editable = new Set(person?.editable_fields ?? []);
  const record = (person ?? {}) as unknown as Record<string, unknown>;
  return SECTIONS.filter(
    (s) => s.key !== "private" || opts.includePrivate !== false
  ).map((section) => ({
    section,
    fields: FIELDS.filter((f) => f.section === section.key).map((spec) => ({
      spec,
      value: record[spec.name] ?? null,
      editable: editable.has(spec.name),
    })),
  }));
}

/** Is a value worth counting as "filled in"? Empty strings and [] are not. */
export function isFilled(value: unknown): boolean {
  if (value === null || value === undefined) return false;
  if (typeof value === "string") return value.trim() !== "";
  if (Array.isArray(value)) return value.length > 0;
  if (typeof value === "object") return Object.keys(value).length > 0;
  return true;
}

export interface Completeness {
  filled: number;
  total: number;
  /** Only the fields that carry a `why` — a meter over everything is noise. */
  missing: Array<{
    label: string;
    why: string;
    /**
     * The column, so the row can send you to the input.
     *
     * Added 2026-09-21 (H-146): the meter said what each gap costs the
     * planner and then routed nowhere. It diagnosed and did not treat.
     */
    name: string;
  }>;
}

/**
 * How much of what the assignment AI actually uses is filled in (§5.3).
 *
 * Counts only fields with a stated consequence. "Cost centre is empty" is not
 * a thing to nag somebody about; "no timezone means the scheduler assumes
 * yours is the org default" is a sentence that earns the interruption.
 */
export function completeness(person: PersonDetail | null): Completeness {
  const record = (person ?? {}) as unknown as Record<string, unknown>;
  const counted = FIELDS.filter((f) => f.why);
  const missing = counted
    .filter((f) => !isFilled(record[f.name]))
    .map((f) => ({ label: f.label, why: f.why as string, name: f.name }));
  return {
    filled: counted.length - missing.length,
    total: counted.length,
    missing,
  };
}

/**
 * The PATCH body: only what changed, and only what this caller may write.
 *
 * Two filters, and they are not redundant. *Changed* keeps a save from
 * re-asserting forty untouched fields, which would turn every save into a
 * conflict with anybody editing the same row. *Editable* keeps the UI from
 * sending a field the server will refuse — but the server refusing it remains
 * the actual control (D-PC-5); this only stops the request being pointless.
 */
export function changedFields(
  draft: Record<string, unknown>,
  original: Record<string, unknown>,
  editableFields: readonly string[]
): Record<string, unknown> {
  const allowed = new Set(editableFields);
  const body: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(draft)) {
    if (!allowed.has(key)) continue;
    if (sameValue(value, original[key])) continue;
    body[key] = value;
  }
  return body;
}

function sameValue(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  // Null and "" are the same intent — "not set" — and treating them as
  // different makes an untouched empty text input look like a change on every
  // save.
  const blankA = a === null || a === undefined || a === "";
  const blankB = b === null || b === undefined || b === "";
  if (blankA && blankB) return true;
  if (Array.isArray(a) && Array.isArray(b)) {
    return a.length === b.length && a.every((v, i) => v === b[i]);
  }
  if (a && b && typeof a === "object" && typeof b === "object") {
    return JSON.stringify(a) === JSON.stringify(b);
  }
  return false;
}

/** `["a", "b"]` ⇄ `"a, b"` for the chip inputs. */
export function parseChips(value: string): string[] {
  return value
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
}

export function formatChips(value: unknown): string {
  return Array.isArray(value) ? value.join(", ") : "";
}
