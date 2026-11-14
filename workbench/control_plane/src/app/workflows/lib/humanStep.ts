/**
 * The rules of the human step form (WFC-4a-1, `ai_studio.md` §14 row E4).
 *
 * A human step gives one member a task, and the run waits until the task is
 * done. The engine takes three keys and no other (`graph.HUMAN_STEP_KEYS`),
 * and each value is literal: publish refuses a `{{ref}}`, so a trigger
 * payload never picks who gets the task. The form keeps the same two rules,
 * so the member sees the refusal while typing, not at publish.
 *
 * The form only draws these rules. The gateway decides: the editor is not a
 * security boundary. Fences: `humanStep.test.ts` and
 * `tests/unit/test_workflows_node_offer.py`.
 */

import type { SelectOption } from "@/components/ui/SelectButton";
import type { DirectoryEntry } from "@/lib/rooms";

/** The config keys of a human step, and no other (`graph.HUMAN_STEP_KEYS`). */
export const HUMAN_STEP_KEYS = ["assignee", "title", "description"] as const;
export type HumanStepKey = (typeof HUMAN_STEP_KEYS)[number];

/** The config of a human step that the palette drops. */
export const NEW_HUMAN_STEP_CONFIG: Record<HumanStepKey, string> = {
  assignee: "",
  title: "",
  description: "",
};

/** What the form says when a value holds `{{`. */
export const LITERAL_ONLY_MESSAGE =
  "A human step takes plain words only. It cannot read data from earlier steps, so remove the {{ marks.";

export type HumanStepEdit =
  | { ok: true; patch: Partial<Record<HumanStepKey, string>> }
  | { ok: false; message: string };

/**
 * The patch for one edit of the form, or the refusal.
 *
 * The patch holds ONE of the three keys. A value with `{{` writes nothing,
 * so the config never holds a value that publish would refuse.
 */
export function humanStepEdit(key: HumanStepKey, value: string): HumanStepEdit {
  if (!(HUMAN_STEP_KEYS as readonly string[]).includes(key)) {
    return { ok: false, message: "A human step has no such field." };
  }
  if (value.includes("{{")) return { ok: false, message: LITERAL_ONLY_MESSAGE };
  return { ok: true, patch: { [key]: value } };
}

/** The text of one config value, for a field. */
export function humanStepValue(config: Record<string, unknown>, key: HumanStepKey): string {
  const value = config[key];
  return typeof value === "string" ? value : "";
}

/** The directory answers at most this many people (`rooms._directory`). */
export const DIRECTORY_PEOPLE_CAP = 100;

/** The cache key of the read of the people of the org. */
export const DIRECTORY_KEY = "/api/chat/directory?q=";

/** What the person picker draws, from the state of its one read. */
export type PersonPickerView =
  | { kind: "loading"; text: string }
  | { kind: "error"; text: string }
  | { kind: "empty"; text: string }
  | { kind: "ready"; options: SelectOption[]; capped: boolean };

/**
 * The person picker's state, as a pure decision.
 *
 * The directory lists active members and groups. A human step needs one
 * person, so a group never becomes an option. The current assignee stays an
 * option when the list does not hold it, so the picker never hides a value
 * that the graph holds.
 */
export function personPickerView(
  read: { data: DirectoryEntry[] | undefined; loading: boolean; error: string | null },
  current: string,
): PersonPickerView {
  if (read.data === undefined) {
    if (read.error) {
      return {
        kind: "error",
        text: "Could not load the members of your organization. Check the connection, then try again.",
      };
    }
    return { kind: "loading", text: "Loading the members of your organization…" };
  }
  const people = read.data.filter((entry) => entry.kind === "person");
  const options: SelectOption[] = people.map((person) => ({
    value: person.subject,
    label: person.displayName || person.subject,
    hint: person.displayName && person.displayName !== person.subject ? person.subject : undefined,
    keywords: person.subject,
  }));
  if (current && !options.some((option) => option.value === current)) {
    options.unshift({
      value: current,
      label: current,
      description: "Not in the list of active members. The run checks this person when it reaches the step.",
    });
  }
  if (options.length === 0) {
    return {
      kind: "empty",
      text: "No active members found. Invite a member in Settings, then pick them here.",
    };
  }
  return { kind: "ready", options, capped: people.length >= DIRECTORY_PEOPLE_CAP };
}

/** The read-only note of a node type that has no form in the editor. */
export function readOnlyNote(type: string): string | null {
  if (type === "pm_task" || type === "pm_lifecycle") {
    return "This step cannot run in a workflow yet.";
  }
  if (type === "studio_write") return "The form for this step is not built yet.";
  return null;
}
