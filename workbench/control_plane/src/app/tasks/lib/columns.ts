// The columnar list model for Next Actions. On DESKTOP the Context (list) view
// renders as aligned columns (Jira/ClickUp-style) — Name plus a chosen set of
// the same signals that are pills on the card. On MOBILE we keep the stacked
// row (title + pills wrapping beneath) since there isn't width for columns.
//
// Which columns show is a per-browser DISPLAY preference (like the list/board
// toggle) — persisted in localStorage, toggled from the Task settings modal.
// Status is intentionally NOT a column: the list is already grouped by status
// (the section headers), so a Status column would just repeat the group.

/** A toggleable list column. `key` is the stable id used in storage + settings. */
export type ColumnKey =
  | "priority"
  | "mode"
  | "context"
  | "energy"
  | "estimate"
  | "due"
  | "attachments"
  | "subtasks";

export interface ColumnDef {
  key: ColumnKey;
  /** header label shown on the desktop column row */
  label: string;
  /** fixed track width for the CSS grid (Name takes the remaining 1fr) */
  width: string;
  /** horizontal alignment of the cell content */
  align: "left" | "center" | "right";
}

/** Columns in their fixed left→right order (Name is always first, rendered
 *  separately as the flexible 1fr track). Order here === on-screen order. */
// Widths measured against the widest real chip each column draws, not rounded
// to a comfortable number: "Speculative Bet" is the widest priority, "Delegate?"
// the widest suggestion, "@computer" the widest context. The old set was ~120px
// of slack spread across six tracks — invisible individually, and collectively
// the reason the Name column was left with 147px and every title truncated
// mid-word. Reclaiming it is what lets Name have a readable floor *without*
// pushing Due date off the right edge.
export const COLUMNS: ColumnDef[] = [
  { key: "priority", label: "Priority", width: "130px", align: "left" },
  { key: "mode", label: "Suggestion", width: "110px", align: "left" },
  { key: "context", label: "Context", width: "100px", align: "left" },
  { key: "energy", label: "Energy", width: "76px", align: "left" },
  { key: "estimate", label: "Estimate", width: "64px", align: "left" },
  { key: "due", label: "Due date", width: "96px", align: "left" },
  { key: "attachments", label: "Files", width: "56px", align: "center" },
  { key: "subtasks", label: "Subtasks", width: "72px", align: "center" },
];

/** Default visibility — the signals that were already pills on the card, on by
 *  default; the noisier count columns (files/subtasks) start hidden. */
export const DEFAULT_VISIBLE: Record<ColumnKey, boolean> = {
  priority: true,
  mode: true,
  context: true,
  energy: true,
  estimate: true,
  due: true,
  attachments: false,
  subtasks: false,
};

const STORAGE_KEY = "cc.tasks.listColumns";

// useSyncExternalStore compares snapshots with Object.is, so getSnapshot MUST
// return a STABLE reference until the value actually changes — otherwise it
// re-renders on every commit forever (React error #185, "maximum update depth").
// We cache the parsed map keyed on the raw localStorage string and only rebuild
// it when that string changes (a write here, or a storage event from another
// tab). `cachedRaw = undefined` means "not yet read".
let cachedRaw: string | null | undefined;
let cachedVis: Record<ColumnKey, boolean> = { ...DEFAULT_VISIBLE };

function parseVisibility(raw: string | null): Record<ColumnKey, boolean> {
  if (!raw) return { ...DEFAULT_VISIBLE };
  try {
    const parsed = JSON.parse(raw) as Partial<Record<ColumnKey, boolean>>;
    const out = { ...DEFAULT_VISIBLE };
    for (const c of COLUMNS) {
      if (typeof parsed[c.key] === "boolean") out[c.key] = parsed[c.key]!;
    }
    return out;
  } catch {
    return { ...DEFAULT_VISIBLE };
  }
}

/** The persisted visibility map, merged over the defaults. Returns a CACHED,
 *  stable object reference (rebuilt only when the stored string changes) so it's
 *  safe as a useSyncExternalStore getSnapshot. */
export function readColumnVisibility(): Record<ColumnKey, boolean> {
  let raw: string | null;
  try {
    raw = window.localStorage.getItem(STORAGE_KEY);
  } catch {
    // localStorage unavailable (SSR / private mode) — stable default.
    return cachedVis;
  }
  if (raw !== cachedRaw) {
    cachedRaw = raw;
    cachedVis = parseVisibility(raw);
  }
  return cachedVis;
}

const listeners = new Set<() => void>();

function notify(): void {
  listeners.forEach((cb) => cb());
}

/** Invalidate the cache and notify subscribers — call on a cross-tab storage
 *  event so the next getSnapshot re-reads localStorage. */
function invalidateAndNotify(): void {
  cachedRaw = undefined; // force the next read to re-parse from storage
  notify();
}

/** Subscribe to visibility changes (this tab's writes + other tabs' storage
 *  events). Shaped for useSyncExternalStore. */
export function subscribeColumns(cb: () => void): () => void {
  listeners.add(cb);
  const onStorage = (e: StorageEvent) => {
    if (e.key === STORAGE_KEY) invalidateAndNotify();
  };
  window.addEventListener("storage", onStorage);
  return () => {
    listeners.delete(cb);
    window.removeEventListener("storage", onStorage);
  };
}

/** Persist a single column's visibility and notify subscribers. */
export function setColumnVisible(key: ColumnKey, visible: boolean): void {
  const next = { ...readColumnVisibility(), [key]: visible };
  const serialized = JSON.stringify(next);
  try {
    window.localStorage.setItem(STORAGE_KEY, serialized);
  } catch {
    // Private mode — localStorage write failed; the in-memory cache below still
    // makes this tab reflect the change.
  }
  // Update the cache to the new value so getSnapshot returns this exact (stable)
  // reference — keeping it consistent with what we just wrote, without a re-read.
  cachedRaw = serialized;
  cachedVis = next;
  notify();
}

/** The visible columns, in order — the desktop grid's non-Name tracks. */
export function visibleColumns(
  vis: Record<ColumnKey, boolean>,
): ColumnDef[] {
  return COLUMNS.filter((c) => vis[c.key]);
}

/** The CSS grid-template-columns for a row: Name (flexible, with a floor so the
 *  title never collapses to nothing when many columns are shown on a narrow
 *  window) + each visible column's fixed track. Kept in one place so the header
 *  and the rows align. */
export function gridTemplate(cols: ColumnDef[]): string {
  // Name floor measured in a browser, not guessed. At 1440px with both rails
  // open the row is 865px; the six default fixed tracks plus gaps take 718 of
  // it, so the old `1fr` resolved to **147px** and every title truncated
  // mid-word ("Call the travel agent about an…" wanting 312px). 1fr cannot
  // help — the competing tracks are fixed pixels, so the name only ever gets
  // the remainder.
  //
  // 240px is a floor, not a size: it still grows with `1fr` on a wide screen or
  // a collapsed rail. It only fits because `COLUMNS` above gave back ~120px of
  // slack — the first attempt raised this floor alone, and a browser showed the
  // result immediately: names became readable and Due date fell off the right
  // edge. Both halves are one change; moving either on its own re-breaks the
  // other.
  return ["minmax(240px, 1fr)", ...cols.map((c) => c.width)].join(" ");
}
