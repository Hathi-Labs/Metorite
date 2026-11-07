/**
 * The change that landed in the draft, and its one-step undo (`ai_studio.md` §14, slice G5). Pure.
 *
 * The page keeps ONE previous state: the manifest before the newest change that landed. A change
 * lands when the save of the member succeeds, when an undo succeeds, or when the draft watch sees a
 * new manifest that this page did not write (Mira, or another tab). The page cannot know who wrote
 * that last one, so the bar says "Mira or another tab".
 */
import type { PackageDraft } from "./packageDraft";
import { SAVE_BUSY } from "./draftSave";
import { diffLines, diffManifests, type Change } from "./manifestDiff";
import { stableJson } from "./dataDoor";

export type Who = "you" | "undo" | "other";

export interface Landed {
  /** The manifest before the change. Undo sends it back. */
  before: unknown;
  who: Who;
  changes: Change[];
}

/** How many lines the bar shows before "Show all". */
export const BAR_LINES = 3;

/**
 * The change from `prev` to `next`, or null when the manifest is the same, or the same in another
 * order (that is no change).
 */
export function landChange(prev: PackageDraft | null, next: PackageDraft, who: Who): Landed | null {
  if (!prev) return null;
  if (stableJson(prev.manifest ?? null) === stableJson(next.manifest ?? null)) return null;
  const changes = diffManifests(prev.manifest, next.manifest);
  if (changes.length === 0) return null;
  return { before: prev.manifest, who, changes };
}

/** The heading of the bar. It names the actor, as far as the page knows it. */
export function barHeading(who: Who): string {
  if (who === "you") return "You saved:";
  if (who === "undo") return "You undid the last change:";
  return "Mira or another tab changed the draft:";
}

/** The label of the one-step button: after an undo, a second press puts the change back. */
export function undoLabel(who: Who): string {
  return who === "undo" ? "Redo" : "Undo";
}

/** The reason that Undo is off, or null. */
export function undoBlock(s: { dirty: boolean; saving: boolean; fault: boolean; working: boolean }): string | null {
  if (s.fault) return "The Code tab holds JSON that does not parse. Fix it or press Revert first.";
  if (s.saving || s.working) return "A save is running. Wait for it to end.";
  if (s.dirty) return "You have unsaved edits. Save or revert them first.";
  return null;
}

export const UNDO_STALE =
  "The draft changed again after the bar showed this change. Nothing was undone. Press Reload to see the newest draft.";
export const UNDO_FAILED =
  "The undo got no answer. It may have worked. Press Reload to see the stored draft.";
export const UNDO_BUSY = "Studio is busy with another save. Nothing was undone. Press the button again in a few seconds.";
export const UNDO_REFUSED = "Studio refused the earlier draft. Nothing was undone. Press Reload to see the stored draft.";
export const UNDO_NOTE = "This changes the draft only. The live app changes when you publish.";

/** Words for an undo that did not land. */
export function undoFailure(r: { kind: "stale" | "invalid" | "failed"; message: string }): string {
  if (r.kind === "stale") return UNDO_STALE;
  if (r.kind === "invalid") return UNDO_REFUSED;
  return r.message === SAVE_BUSY ? UNDO_BUSY : UNDO_FAILED;
}

/** The lines of the bar: the first ones, or all of them. */
export function barLines(l: Landed, all: boolean): { lines: string[]; hidden: number } {
  const full = diffLines(l.changes, 12);
  if (all || full.length <= BAR_LINES) return { lines: full, hidden: 0 };
  return { lines: full.slice(0, BAR_LINES), hidden: full.length - BAR_LINES };
}
