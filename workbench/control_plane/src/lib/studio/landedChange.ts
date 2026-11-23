/**
 * The change that landed in the draft, and its one-step undo (`ai_studio.md` §14, slice G5). Pure.
 *
 * The page keeps ONE previous state: the manifest before the newest change that landed. A change
 * lands when the save of the member succeeds, when an undo succeeds, or when the draft watch sees a
 * new manifest that this page did not write (Mira, or another tab). The page cannot know who wrote
 * that last one, so the bar says "Mira or another tab".
 */
import type { PackageDraft } from "./packageDraft";
import type { LiveObject } from "./draftEdit";
import { SAVE_BUSY, saveDraft } from "./draftSave";
import { cleanName, diffLines, diffManifests, type Change } from "./manifestDiff";
import { stableJson } from "./dataDoor";

export type Who = "you" | "undo" | "redo" | "other";

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
  if (who === "redo") return "You put the change back:";
  return "Mira or another tab changed the draft:";
}

/** The label of the one-step button: after an undo, a second press puts the change back. */
export function undoLabel(who: Who): string {
  return who === "undo" ? "Redo" : "Undo";
}

/** The reason that Undo is off, or null. */
export function undoBlock(s: { dirty: boolean; saving: boolean; fault: boolean; working: boolean; liveDrop?: string | null }): string | null {
  if (s.fault) return "The Code tab holds JSON that does not parse. Fix it or press Revert first.";
  if (s.saving || s.working) return "A save is running. Wait for it to end.";
  if (s.dirty) return "You have unsaved edits. Save or revert them first.";
  return s.liveDrop ?? null;
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

const isObj = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);

/**
 * The reason that Undo would remove something that is live, or null. A publish makes each record
 * type and field a table or a column that no member can remove yet (`objects_store._plan_sync`). So
 * an Undo is off only for a live record type or field that the CURRENT draft holds and `before` does
 * not. A part that the draft already lacks is not removed by Undo, so it never blocks.
 */
export function undoDropsLive(before: unknown, current: unknown, live: readonly LiveObject[]): string | null {
  if (live.length === 0) return null;
  const objectsOf = (m: unknown) => (isObj(m) && Array.isArray(m.objects) ? m.objects.filter(isObj) : []);
  const fieldsOf = (o: Record<string, unknown>) => (Array.isArray(o.fields) ? o.fields.filter(isObj) : []);
  const was = objectsOf(before);
  const now = objectsOf(current);
  const tail = "A published record type or field cannot be removed yet.";
  for (const lo of live) {
    const inNow = now.find((x) => x.name === lo.name);
    if (!inNow) continue;
    const inBefore = was.find((x) => x.name === lo.name);
    if (!inBefore) return `Undo is off: it would remove the record type ${q(shownName(inNow, "name"))}, which is live. ${tail}`;
    const keys = new Set(fieldsOf(inBefore).map((f) => f.key));
    const nowFields = fieldsOf(inNow);
    for (const f of lo.fields) {
      const held = nowFields.find((x) => x.key === f.key);
      if (held && !keys.has(f.key)) return `Undo is off: it would remove ${q(shownName(held, "key"))} from ${q(shownName(inNow, "name"))}, which is live. ${tail}`;
    }
  }
  return null;
}

const q = (s: string) => `«${s}»`;
const shownName = (item: Record<string, unknown>, key: string): string => cleanName(typeof item.label === "string" && item.label ? item.label : item[key]);

// ── The tracker: what the page remembers about the newest change ─────────────

export interface Tracker {
  /** The draft that the page saw last. */
  prev: PackageDraft | null;
  /** The newest change that landed, or null. */
  landed: Landed | null;
  /** The next draft with this hash is the page's own write. */
  own: { who: "you" | "undo" | "redo"; hash: string | null } | null;
}

export const NEW_TRACKER: Tracker = { prev: null, landed: null, own: null };

export function markOwn(t: Tracker, who: "you" | "undo" | "redo", hash: string | null): Tracker {
  return { ...t, own: { who, hash } };
}

/**
 * Take the draft that the page shows now.
 * - A manifest that moved lands a change, or clears `landed` when it has no line. A kept `landed`
 *   would hold a manifest older than the draft, and Undo would wipe the newer save.
 * - A new live version clears `landed`: the change is live, so Undo of it is no draft undo.
 * - Anything else keeps `landed`.
 */
export function observe(t: Tracker, draft: PackageDraft | null): Tracker {
  const prev = t.prev;
  if (!draft) return { ...t, prev: null };
  if (!prev) return { ...t, prev: draft };
  const moved = stableJson(prev.manifest ?? null) !== stableJson(draft.manifest ?? null);
  const mine = t.own && t.own.hash !== null && t.own.hash === draft.manifestHash ? t.own.who : null;
  const own = mine ? null : t.own;
  // A new live version clears the bar, even when the manifest moved in the same poll: the change is live then.
  if (prev.liveVersion !== draft.liveVersion) return { prev: draft, landed: null, own };
  if (moved) return { prev: draft, landed: landChange(prev, draft, mine ?? "other"), own };
  return { prev: draft, landed: t.landed, own };
}

/** Send the earlier manifest back through the one save. The words say why when it did not land. */
export async function runUndo(
  pkg: string, draft: PackageDraft, landed: Landed, save: typeof saveDraft = saveDraft,
): Promise<{ kind: "saved"; draft: PackageDraft; who: "undo" | "redo" } | { kind: "note"; text: string }> {
  const r = await save(pkg, landed.before, draft.manifestHash, draft);
  if (r.kind === "saved") return { kind: "saved", draft: r.draft, who: landed.who === "undo" ? "redo" : "undo" };
  return { kind: "note", text: undoFailure(r) };
}
