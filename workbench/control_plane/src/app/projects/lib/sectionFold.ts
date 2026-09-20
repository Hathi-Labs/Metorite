/**
 * Projects · which task-panel sections a member has folded away.
 *
 * Pure plus one storage seam, so the rule is testable without a DOM.
 *
 * ## Why this is REMEMBERED rather than reset per task
 *
 * Folding is a standing preference about how you read a task, not a fact
 * about one task. A panel that forgets is a panel you stop folding: you close
 * Activity on a noisy ticket, open the next one, and it is back. The choice
 * is therefore stored per member, exactly as the panel's width stop already
 * is (`panelMode.ts`).
 *
 * ⚠️ **Folded is the exception, so the stored value is the FOLDED set and an
 * absent entry means open.** Storing the open set instead would make a new
 * section default to closed the moment it ships, and a member who has never
 * touched this preference would find parts of the panel missing.
 */

export const FOLD_STORAGE_KEY = "cc-projects-panel-folded";

/** The sections that can fold. Ids are stable; labels are free to change. */
export const FOLDABLE_SECTIONS = [
  "details",
  "description",
  "properties",
  "links",
  "files",
  "activity",
] as const;

export type FoldableSection = (typeof FOLDABLE_SECTIONS)[number];

export function isFoldable(value: unknown): value is FoldableSection {
  return (FOLDABLE_SECTIONS as readonly string[]).includes(value as string);
}

/**
 * Add or remove one section from the folded set.
 *
 * Returns a NEW set — the caller holds it in React state, and mutating in
 * place would not re-render.
 */
export function toggleFold(
  folded: ReadonlySet<string>,
  section: FoldableSection,
  open: boolean,
): Set<string> {
  const next = new Set(folded);
  if (open) next.delete(section);
  else next.add(section);
  return next;
}

/**
 * What was stored, reduced to sections that still exist.
 *
 * ⚠️ Unknown ids are DROPPED rather than kept. A section removed from the
 * product would otherwise sit in everybody's storage forever, and a typo
 * written by an older build would fold nothing while looking like it should.
 */
export function parseFolded(raw: string | null): Set<string> {
  if (!raw) return new Set();
  try {
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return new Set();
    return new Set(parsed.filter(isFoldable));
  } catch {
    // Storage can hold anything — a half-written value, or something a
    // different build left behind. Unreadable means "nothing folded", which
    // shows the member everything rather than hiding parts of the panel.
    return new Set();
  }
}

export function serialiseFolded(folded: ReadonlySet<string>): string {
  return JSON.stringify([...folded].filter(isFoldable).sort());
}

/**
 * The storage seam, shaped exactly like `panelMode.ts`'s.
 *
 * ⚠️ Injectable for the same reason that one is: a test must be able to hand
 * in a fake, and `localStorage` throws outright in a private window or a
 * blocked frame. One idiom for "a per-member panel preference", not two.
 */
export interface FoldStore {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

function browserStore(): FoldStore | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage;
  } catch {
    return null;
  }
}

export function readFolded(store?: FoldStore | null): Set<string> {
  const target = store === undefined ? browserStore() : store;
  if (!target) return new Set();
  try {
    return parseFolded(target.getItem(FOLD_STORAGE_KEY));
  } catch {
    return new Set();
  }
}

export function writeFolded(
  folded: ReadonlySet<string>,
  store?: FoldStore | null,
): void {
  const target = store === undefined ? browserStore() : store;
  if (!target) return;
  try {
    target.setItem(FOLD_STORAGE_KEY, serialiseFolded(folded));
  } catch {
    // The fold still applies for this session; it just is not remembered.
  }
}
