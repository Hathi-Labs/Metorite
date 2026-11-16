/**
 * The edit card of a new Projects task (`project_ai_chat.md` §32, slice EAC-2,
 * task 3469). The server half is EAC-1: `packages/acb_skills/acb_skills/task_card.py`.
 *
 * ⚠️ The file is `taskEditCard.ts`, not `taskCard.ts`. `lib/taskCard.ts` is the
 * task CHIP vocabulary of the boards, and a second `taskCard` would hide both.
 *
 * With `PROJECT_EDIT_CARD_ORGS` on, the `confirmation_requested` event of
 * `create_task` carries `edit`: `{kind: "create_task", project_id, values}`.
 * The card draws a form from `values`. The member changes a value, then presses
 * Approve. The answer is `APPROVE ` and the JSON `{"edit": {...}}` with all
 * seven keys. A card with no readable `edit` is the card of before.
 *
 * What stays true (R7, fenced by `taskEditCard.test.ts`):
 *
 * - The builder is plain `JSON.stringify` of `{edit}`. The server reduces a
 *   LATE edit answer only when the body decodes to an object with the key
 *   `edit` (`task_card.is_late_edit_answer`), so no other framing may be used.
 * - The answer holds the seven keys of `TASK_EDIT_KEYS`, no more and no fewer.
 *   It never holds `project_id` or any id. The tool keeps the project of the card.
 * - The server trims nothing. The form trims and lower-cases in ONE place,
 *   `norm`, which `matchTyped` and `addAssignee` call for a typed person (the
 *   server folds a person the same way). A value that the member did not
 *   change is sent exactly as the card showed it.
 * - No change sends a plain `APPROVE` (`taskApproval`). It approves the
 *   original values, and a LATE plain `APPROVE` still writes them, where a late
 *   edit answer writes nothing and asks the member to show the card again.
 *
 * Pure, and in `lib/`, because `vitest.config.ts` runs in the node
 * environment. Fence: `src/lib/taskEditCard.test.ts`.
 */

import { cardKey } from "@/lib/cardFields";

/** The prefix of an Approve with a body (`task_card.EDIT_ANSWER_PREFIX`). */
export const TASK_EDIT_PREFIX = "APPROVE ";

/** The one key of the body (`task_card.EDIT_KEY`). It never reuses `reply`. */
export const TASK_EDIT_KEY = "edit";

/**
 * The limits of the form, one copy. The server owns them
 * (`task_card.DESCRIPTION_MAX`, `STATUS_MAX`, `ASSIGNEE_MAX`, `MAX_ASSIGNEES`),
 * and `taskEditCard.test.ts` reads `task_card.py` to hold the numbers equal.
 */
export const DESCRIPTION_MAX = 20_000;
export const STATUS_MAX = 200;
export const ASSIGNEE_MAX = 320;
export const MAX_ASSIGNEES = 50;

/** The seven values of the card. The server's `TASK_EDIT_KEYS`, in the order of the card. */
export interface TaskCardEdits {
  /** `YYYY-MM-DD`, or "". */
  due: string;
  /** `YYYY-MM-DD`, or "". Shown read-only in EAC-2. */
  start: string;
  /** The name of a lane, or "" for the default. Shown read-only in EAC-2. */
  status: string;
  /** True, false, or null when nobody judged the task. Read-only in EAC-2. */
  important: boolean | null;
  /** Read-only in EAC-2. */
  leveraged: boolean;
  /** Read-only in EAC-2. */
  description: string;
  /** Emails or `agent:<name>`, in the order of the card. */
  assignees: string[];
}

/** The key set of an edit answer, as the server's `TASK_EDIT_KEYS`. */
export const TASK_EDIT_KEYS = ["assignees", "description", "due", "important", "leveraged", "start", "status"] as const;

/** What the card event carries (`task_card.clean_task_edit_form`). */
export interface TaskEditForm {
  kind: "create_task";
  /**
   * The project of the card. The client may list its lanes and people from it
   * (EAC-3). It is NEVER sent back: no answer names an id.
   */
  projectId: string;
  values: TaskCardEdits;
}

/** The answer of an edited card: `APPROVE {"edit":...}`. */
export type TaskEditApproval = `APPROVE {"edit":${string}`;

const DATE = /^[0-9]{4}-[0-9]{2}-[0-9]{2}$/;

/** True for "" or a real calendar date written `YYYY-MM-DD` in ASCII digits. */
export function isIsoDate(value: unknown): value is string {
  if (typeof value !== "string") return false;
  if (value === "") return true;
  if (!DATE.test(value)) return false;
  const d = new Date(`${value}T00:00:00Z`);
  return !Number.isNaN(d.getTime()) && d.toISOString().slice(0, 10) === value;
}

/** True for a value of the card that the server would take. */
export function valuesOk(values: unknown): values is TaskCardEdits {
  if (!values || typeof values !== "object" || Array.isArray(values)) return false;
  const v = values as Record<string, unknown>;
  const keys = Object.keys(v);
  if (keys.length !== TASK_EDIT_KEYS.length || !TASK_EDIT_KEYS.every((k) => k in v)) return false;
  const who = v.assignees;
  if (!Array.isArray(who) || who.length > MAX_ASSIGNEES) return false;
  if (!who.every((a) => typeof a === "string" && a.trim() !== "" && a.length <= ASSIGNEE_MAX)) return false;
  const folded = (who as string[]).map((a) => a.trim().toLowerCase());
  if (new Set(folded).size !== folded.length) return false;
  return (
    isIsoDate(v.due) &&
    isIsoDate(v.start) &&
    typeof v.status === "string" && v.status.length <= STATUS_MAX &&
    (v.important === null || typeof v.important === "boolean") &&
    typeof v.leveraged === "boolean" &&
    typeof v.description === "string" && v.description.length <= DESCRIPTION_MAX
  );
}

/**
 * The form of a card event, or null for the card of before. An unknown shape,
 * a kind that is not `create_task`, a value outside the limits and a missing
 * key all give null, so the card draws plain and Approve sends a plain APPROVE.
 */
export function taskEditFromEvent(raw: unknown): TaskEditForm | null {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const r = raw as Record<string, unknown>;
  if (r.kind !== "create_task") return null;
  if (typeof r.project_id !== "string" || !r.project_id) return null;
  if (!valuesOk(r.values)) return null;
  const v = r.values;
  return {
    kind: "create_task",
    projectId: r.project_id,
    values: {
      due: v.due, start: v.start, status: v.status, important: v.important,
      leveraged: v.leveraged, description: v.description, assignees: [...v.assignees],
    },
  };
}

/** True for the edits of a task card, and false for those of a new mail. */
export function isTaskCardEdits(x: unknown): x is TaskCardEdits {
  if (!x || typeof x !== "object") return false;
  const v = x as Record<string, unknown>;
  return Array.isArray(v.assignees) && typeof v.due === "string" && typeof v.leveraged === "boolean" && !("subject" in v);
}

/**
 * The answer of an edited card. Plain `JSON.stringify`, with the seven keys
 * built here in fixed order, so a stray key of the state never reaches the
 * wire (a fence: `taskEditCard.test.ts`).
 */
export function taskEditAnswer(edits: TaskCardEdits): TaskEditApproval {
  const edit = {
    due: edits.due,
    start: edits.start,
    status: edits.status,
    important: edits.important,
    leveraged: edits.leveraged,
    description: edits.description,
    assignees: [...edits.assignees],
  };
  return `${TASK_EDIT_PREFIX}${JSON.stringify({ [TASK_EDIT_KEY]: edit })}` as TaskEditApproval;
}

/** True when each value is the value of the card, with the people in the same order. */
export function isUnchanged(original: TaskCardEdits, edits: TaskCardEdits): boolean {
  return (
    original.due === edits.due &&
    original.start === edits.start &&
    original.status === edits.status &&
    original.important === edits.important &&
    original.leveraged === edits.leveraged &&
    original.description === edits.description &&
    original.assignees.length === edits.assignees.length &&
    original.assignees.every((a, i) => a === edits.assignees[i])
  );
}

/**
 * What Approve sends. Nothing changed gives the plain `APPROVE`: it approves
 * the original values, it has no body to refuse, and a late one still writes
 * them. A change gives the edit answer.
 */
export function taskApproval(original: TaskCardEdits, edits: TaskCardEdits): "APPROVE" | TaskEditApproval {
  return isUnchanged(original, edits) ? "APPROVE" : taskEditAnswer(edits);
}

/** The people who changed: who the member took off, and who the member added. */
export function assigneeChanges(original: TaskCardEdits, edits: TaskCardEdits): { added: string[]; removed: string[] } {
  const was = new Set(original.assignees.map(norm));
  const now = new Set(edits.assignees.map(norm));
  return {
    added: edits.assignees.filter((a) => !was.has(norm(a))),
    removed: original.assignees.filter((a) => !now.has(norm(a))),
  };
}

/** The same fold as the server's `_resolve_assignee_name`: trimmed, lower case. */
export function norm(who: string): string {
  return who.trim().toLowerCase();
}

// ── The people ──────────────────────────────────────────────────────────────

/** One person or agent of the org directory (`GET /projects/assignees`). */
export interface DirectoryRow {
  /** What the task stores: an email, or `agent:<name>`. */
  assignee: string;
  name: string;
}

/** What the member typed in the person field. */
export type TypedPerson =
  | { kind: "add"; assignee: string }
  | { kind: "none"; typed: string }
  | { kind: "many"; typed: string }
  /** The server would read this value as no answer (`why`), so the form refuses it. */
  | { kind: "bad"; typed: string; why: "control" | "long" }
  | { kind: "empty" };

/**
 * Why the server would refuse this person, or null. The same rule as
 * `task_card._one_line` for an assignee: at most `ASSIGNEE_MAX` characters and
 * no character of category Cc, Zl or Zp (a tab, a line break, U+0085, U+2028,
 * U+2029). A value that fails reads as NO ANSWER there, and the card would
 * fail with no word, so the form refuses it first. `taskEditCard.test.ts`
 * reads the category list from `task_card.py`.
 */
export function assigneeProblem(value: string): "control" | "long" | null {
  if (/[\p{Cc}\p{Zl}\p{Zp}]/u.test(value)) return "control";
  return value.length > ASSIGNEE_MAX ? "long" : null;
}

/** The refusal of a typed person that is not added, as `{kind: "bad"}`, or null. */
function refused(typed: string, assignee: string): TypedPerson | null {
  const why = assigneeProblem(assignee) ?? assigneeProblem(typed);
  return why ? { kind: "bad", typed, why } : null;
}

/**
 * The rule for a typed person, the server's rule (`_resolve_assignee_name`).
 * An address (it holds `@`) or `agent:<name>` passes as typed, in lower case.
 * A name matches a person of the directory EXACTLY (case does not count, no
 * part of a name counts), and exactly one. A name that matches none, or two,
 * adds nobody, and the form asks for another. The server refuses such a name
 * AFTER the Approve, with the act already spent, so the form says it before.
 */
export function matchTyped(typed: string, directory: readonly DirectoryRow[]): TypedPerson {
  const raw = typed.trim();
  if (!raw) return { kind: "empty" };
  if (raw.includes("@") || raw.toLowerCase().startsWith("agent:")) {
    return refused(raw, norm(raw)) ?? { kind: "add", assignee: norm(raw) };
  }
  const same = directory.filter((p) => norm(p.name) === norm(raw));
  if (same.length === 1) return refused(raw, norm(same[0].assignee)) ?? { kind: "add", assignee: norm(same[0].assignee) };
  return same.length === 0 ? { kind: "none", typed: raw } : { kind: "many", typed: raw };
}

/** The line under the person field for a name that did not match. */
export function typedLine(t: TypedPerson): string {
  if (t.kind === "none") return `Nobody is called exactly “${t.typed}”. Pick a person from the list, or type an address.`;
  if (t.kind === "many") return `More than one person is called “${t.typed}”. Pick one from the list.`;
  if (t.kind === "bad") {
    return t.why === "long"
      ? `That address is too long. A person is at most ${ASSIGNEE_MAX} characters.`
      : "That text holds a tab or line break. Type the address on one line.";
  }
  return "";
}

/** The reason that no more people fit, or "". */
export function fullLine(count: number): string {
  return count >= MAX_ASSIGNEES ? `A task takes at most ${MAX_ASSIGNEES} people.` : "";
}

/**
 * Add a person to the list, or give the same list back (a person twice, in
 * any case, is one person, and the server refuses a list that holds one twice).
 * The list stays in order, and a new person goes last.
 */
export function addAssignee(list: readonly string[], assignee: string): string[] {
  const value = norm(assignee);
  if (!value || assigneeProblem(value)) return list as string[];
  if (list.length >= MAX_ASSIGNEES || list.some((a) => norm(a) === value)) return list as string[];
  return [...list, value];
}

/** Take one person off the list. The others keep their order and their text. */
export function removeAssignee(list: readonly string[], assignee: string): string[] {
  return list.filter((a) => a !== assignee);
}

/**
 * The addresses that the directory does not know, by the same lookup as the
 * server's receipt (`_unknown_addresses`, H-162). `known` holds the lower-case
 * addresses that `GET /projects/people/names` returned. An agent is no address.
 */
export function strangersOf(list: readonly string[], known: ReadonlySet<string>): string[] {
  return list.filter((a) => a.includes("@") && !known.has(norm(a)));
}

/** The warning line for the addresses that the directory does not know. */
export function strangerLine(strangers: readonly string[]): string {
  if (strangers.length === 0) return "";
  return `Not in the directory: ${strangers.join(", ")}. Check the address before you approve.`;
}

/** The people that are new on the card and that the directory does not know. */
export function newStrangers(original: TaskCardEdits, edits: TaskCardEdits, known: ReadonlySet<string>): string[] {
  const { added } = assigneeChanges(original, edits);
  return strangersOf(added, known);
}

// ── The form's own checks ───────────────────────────────────────────────────

/** The reason that Approve is off, or "". The form shows it beside the field. */
export function problemOf(edits: TaskCardEdits): string {
  if (!isIsoDate(edits.due)) return "Pick a due date as day, month and year, or clear it.";
  if (edits.assignees.length > MAX_ASSIGNEES) return fullLine(MAX_ASSIGNEES);
  return "";
}

/**
 * The rows of the card's body that the form draws itself, by their key on the
 * card (`writes.py`: `_priority_card`, `_assignee_card_lines`). The card hides
 * them when it draws the form, so no stale value stays beside the new one. The
 * other rows (project, status, start, priority, description) stay: they are the
 * values that this slice does not edit.
 */
export const TASK_FORM_ROWS: ReadonlySet<string> = new Set(["due_at", "due", "assignees", "not in the directory"]);

/** The fields of a card's body, less the rows that the form draws itself. */
export function withoutFormRows<T extends { key: string }>(fields: readonly T[]): T[] {
  return fields.filter((f) => !TASK_FORM_ROWS.has(cardKey(f.key)));
}

/** The line beside Approve once the member changed a value. */
export const TASK_EDITED_HINT = "Approve writes the task with your edits.";

/**
 * The reason that Approve waits when the person field holds text that no
 * Enter added. Without it, a click on Approve would leave the typed person out
 * with no word, because the field adds a person on Enter only.
 */
export function pendingLine(typed: string): string {
  const t = typed.trim();
  return t ? `Press Enter to add “${t}”, or clear the field.` : "";
}

/** The first line of the form's summary of what changed, for the "edited" state. */
export function changedSummary(original: TaskCardEdits, edits: TaskCardEdits): string {
  const parts: string[] = [];
  if (original.due !== edits.due) parts.push(edits.due ? `due ${edits.due}` : "no due date");
  const { added, removed } = assigneeChanges(original, edits);
  if (added.length) parts.push(`added ${added.join(", ")}`);
  if (removed.length) parts.push(`removed ${removed.join(", ")}`);
  if (!added.length && !removed.length && original.assignees.join("\n") !== edits.assignees.join("\n")) parts.push("people in a new order");
  return parts.length ? `Edited: ${parts.join(" · ")}.` : "";
}
