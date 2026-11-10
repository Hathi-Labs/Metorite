// ── Saved views, the shared seam (WS-53 CRM-U8) ────────────────────────────
//
// A saved view is a named screen state that a member can come back to. The CRM
// is the first app to use this seam. Projects (`pm_views`) is the next one.
// It must not grow its own copy. It passes a scope key, the callbacks that
// read and write its table, and its own words (`components/ViewChips.tsx`).
//
// Pure, so the vitest setup (no DOM) tests every rule.
//
// - `ViewScope`: the key of one list. The CRM uses `deals`, `leads` and so on,
//   and `module:<api_name>` for a generic module list.
// - `orderViews`: the chip order. Private views first, then shared ones.
// - `dirtyState`: the ONE rule that decides which save control shows.
//   Fence: `index.test.ts`.

/** The key of one list that owns views. */
export type ViewScope = string;

/** What every saved view has, whatever app it belongs to. */
export type SavedView = {
  id: string;
  name: string;
  /** True when the whole organization sees the view. */
  shared: boolean;
  /** True when the caller made the view. */
  mine: boolean;
  position: number;
  /** The owner, when the server sends it. */
  owner_email?: string;
};

/** Stable JSON: the same text for the same value, whatever the key order. */
export function stableJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(stableJson).join(",")}]`;
  if (value && typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>)
      .filter(([, v]) => v !== undefined)
      .sort(([a], [b]) => a.localeCompare(b));
    return `{${entries.map(([k, v]) => `${JSON.stringify(k)}:${stableJson(v)}`).join(",")}}`;
  }
  return JSON.stringify(value ?? null);
}

function isMine(view: SavedView, me: string | null): boolean {
  if (view.mine) return true;
  return Boolean(me && view.owner_email && view.owner_email.toLowerCase() === me.toLowerCase());
}

/**
 * The views in chip order: the member's private views, then the views the
 * member shared, then the views that others shared. Each group keeps the
 * order of `position`, and a tie keeps the order of the list.
 */
export function orderViews<V extends SavedView>(
  views: readonly V[] | null | undefined,
  me: string | null = null,
): V[] {
  const group = (v: V) => (!v.shared ? 0 : isMine(v, me) ? 1 : 2);
  return (views ?? [])
    .map((view, index) => ({ view, index }))
    .sort(
      (a, b) =>
        group(a.view) - group(b.view) ||
        a.view.position - b.view.position ||
        a.index - b.index,
    )
    .map(({ view }) => view);
}

/**
 * Which save control the toolbar shows.
 *
 * - `blank`: the default view with nothing set. No control.
 * - `unsaved`: the default view with a filter, a sort or columns set. "+ Save view".
 * - `changed`: a saved view whose screen state differs. "Update view" and "Reset".
 * - `clean`: a saved view, as saved. No control.
 *
 * `view` is the saved view in effect, with the state it stores. `screen` is
 * the state on screen, and `blank` says that it holds nothing a view would keep.
 * Both states must be in the same shape. The compare ignores key order.
 */
export type DirtyState = "blank" | "unsaved" | "changed" | "clean";

export function dirtyState<S>(
  view: { state: S } | null,
  screen: { state: S; blank: boolean },
): DirtyState {
  if (!view) return screen.blank ? "blank" : "unsaved";
  return stableJson(view.state) === stableJson(screen.state) ? "clean" : "changed";
}

/** The view that `id` names, or null (a deleted or unseen id). */
export function viewById<V extends SavedView>(
  views: readonly V[] | null | undefined,
  id: string | null,
): V | null {
  return id ? ((views ?? []).find((v) => v.id === id) ?? null) : null;
}

/** May this caller change or delete the view? The server decides. This hides the controls. */
export function canEditView(view: SavedView, isAdmin: boolean): boolean {
  return view.mine || isAdmin;
}

/** One chip of the strip. `id` is null for the unsaved default view. */
export type ChipSpec = { id: string | null; label: string; shared: boolean; active: boolean };

/**
 * The chips of row 1: the default view first, then `orderViews`. A view in
 * effect that the list does not hold (deleted, not read yet) leaves the
 * default chip active.
 */
export function chipList<V extends SavedView>(
  allLabel: string,
  views: readonly V[] | null | undefined,
  activeId: string | null,
  me: string | null = null,
): ChipSpec[] {
  const ordered = orderViews(views, me);
  const known = viewById(ordered, activeId);
  return [
    { id: null, label: allLabel, shared: false, active: known === null },
    ...ordered.map((view) => ({
      id: view.id,
      label: view.name,
      shared: view.shared,
      active: view.id === known?.id,
    })),
  ];
}
