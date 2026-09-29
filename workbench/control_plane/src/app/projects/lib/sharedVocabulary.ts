/**
 * Shared vocabulary — the pure rules of the organization's section (WS-42 PS-3).
 *
 * Spec: `project-docs/specs/projects_settings.md` §7 row PS-3, and D-PM-33
 * (`project_management_app.md` §9.11.2): an org-wide row may be RENAMED, and a
 * tag also recoloured. Merge and delete stay refused, so this section offers
 * neither. Fence: `sharedVocabulary.test.ts`.
 */

import type { OrgVocabulary } from "./api";

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
      ? "You can rename them, and recolour a tag. They cannot be merged or deleted yet."
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
