/**
 * Shared vocabulary — the pure rules of the organization's section (WS-42 PS-3).
 *
 * Spec: `project-docs/specs/projects_settings.md` §7 row PS-3, and D-PM-33
 * (`project_management_app.md` §9.11.2): an org-wide row may be RENAMED, and a
 * tag also recoloured. Merge and delete stay refused, so this section offers
 * neither. Fence: `sharedVocabulary.test.ts`.
 */

import type { OrgVocabulary } from "./api";
import type { DeleteCopy } from "./deleteCopy";

export type VocabularyKind = "tags" | "fields" | "types";

export const VOCABULARY_GROUPS: { kind: VocabularyKind; label: string; icon: string; noun: string }[] = [
  { kind: "tags", label: "Tags", icon: "Tag", noun: "tag" },
  { kind: "fields", label: "Custom fields", icon: "SlidersHorizontal", noun: "field" },
  { kind: "types", label: "Task types", icon: "Shapes", noun: "task type" },
];

/** No shared row of any kind: the section says so once, not three times. */
export function vocabularyEmpty(v: Pick<OrgVocabulary, "tags" | "fields" | "types">): boolean {
  return v.tags.length === 0 && v.fields.length === 0 && v.types.length === 0;
}

/**
 * How many open tasks use a row. `undefined` means the gateway did not send a
 * count (the member cannot rename), and then the row says nothing rather
 * than "0", which would be a wrong number.
 */
export function usageLine(count: number | undefined): string | null {
  if (count === undefined) return null;
  if (count === 0) return "No open task uses it";
  return `${count} open task${count === 1 ? "" : "s"}`;
}

/** What the section says above the lists: who may change what, and why. */
export function vocabularyNotes(v: Pick<OrgVocabulary, "can_edit" | "can_create">): string[] {
  const notes = [
    "Shared entries appear in every space. A space that has its own entry with the same name keeps its own.",
  ];
  notes.push(
    v.can_edit
      ? "You can rename, recolour a tag, merge a tag into another shared tag, and delete."
      : "Only an organization owner or admin can change them.",
  );
  if (!v.can_create) notes.push("Adding new shared entries is not turned on for this organization.");
  return notes;
}

/** The notice after a rename. Every space sees the new name at once. */
export function renamedNotice(noun: string, to: string, retagged?: number): string {
  const tasks = retagged ? ` It was rewritten on ${retagged} task${retagged === 1 ? "" : "s"}.` : "";
  return `Renamed the shared ${noun} to “${to}” in every space.${tasks}`;
}

export type Impact = { tasks: number; projects: number } | null;

const tasks = (n: number) => `${n} task${n === 1 ? "" : "s"}`;
const spaces = (n: number) => `${n} space${n === 1 ? "" : "s"}`;
const WHAT_DELETE: Record<VocabularyKind, string> = {
  tags: "It is taken off",
  fields: "Its values are cleared from",
  types: "It is taken off",
};

/**
 * H-205 — the question before a shared entry is deleted. The count is the
 * point (as for the shared rename, D-PM-33): it reaches spaces the admin may
 * not open. A failed count asks with the size unknown, never as zero.
 */
export function sharedDeleteCopy(kind: VocabularyKind, name: string, impact: Impact): DeleteCopy {
  const noun = VOCABULARY_GROUPS.find((g) => g.kind === kind)?.noun ?? "entry";
  const reach = impact
    ? impact.tasks === 0
      ? "No task uses it. It is deleted for good."
      : `${WHAT_DELETE[kind]} ${tasks(impact.tasks)} across ${spaces(impact.projects)}, and deleted for good.`
    : `${WHAT_DELETE[kind]} every task that uses it, and deleted for good. The number could not be read.`;
  return {
    title: `Delete this shared ${noun}?`,
    subject: name,
    body: `${reach} The tasks stay. This cannot be undone.`,
    note: "A space that has its own entry with this name keeps it. That includes spaces you may not be able to open.",
    confirmLabel: "Delete",
  };
}

/** H-205 — the question before one shared tag is merged into another. */
export function sharedMergeCopy(from: string, into: string, impact: Impact): DeleteCopy {
  return {
    title: "Merge this shared tag?",
    subject: from,
    body: impact
      ? impact.tasks === 0
        ? `No task wears it. “${from}” is deleted, and “${into}” stays.`
        : `${tasks(impact.tasks)} across ${spaces(impact.projects)} ${impact.tasks === 1 ? "wears" : "wear"} “${into}” instead, and “${from}” is deleted.`
      : `Every task with it wears “${into}” instead, and “${from}” is deleted. The number could not be read.`,
    note: "A space that has its own tag with this name keeps it. This cannot be undone.",
    confirmLabel: "Merge",
  };
}
