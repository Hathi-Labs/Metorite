// ── Saved views (WS-53 CRM-Q8, CRM-S2) ────────────────────────────────────
//
// A saved view is a list or a board that a member named: the filters, the sort,
// the chosen custom columns, the layout and the deal pipeline. The table is
// `crm_views` and the routes are `/crm/views` (`routes/crm/views.py`).
//
// Pure, so the vitest setup (no DOM) tests every rule. The page composes it.
//
// ⚠️ A view is the URL grammar in a box. `applyView` turns a saved view into a
// `CrmView` and `captureView` turns a `CrmView` back into the saved shape, so
// applying a view and typing the same filters by hand give one result. The
// divergence check (`viewDiverges`) reads those two functions and holds no
// second opinion about what a view is.

import { MAX_COLUMNS } from "./fields";
import {
  RANGE_KEYS,
  defaultLayout,
  hasBoard,
  type CrmLayout,
  type CrmView,
  type RangeKey,
} from "./urlState";
import type { EntitySlug } from "./types";

/** What a view stores as filters. The names are the URL names of the list. */
export type SavedFilters = {
  q?: string;
  status?: string;
  owner?: string;
  source?: string;
  converted?: boolean;
  custom?: Record<string, string[]>;
} & Partial<Record<RangeKey, string>>;

export type SavedSort = { key: string; dir: "asc" | "desc" };

/** One row of `GET /crm/views`. */
export type SavedView = {
  id: string;
  entity: EntitySlug;
  name: string;
  owner_email: string;
  shared: boolean;
  layout: CrmLayout;
  filters: SavedFilters;
  sort: SavedSort | null;
  columns: string[] | null;
  pipeline_id: string | null;
  position: number;
  /** True when the caller made the view. */
  mine: boolean;
};

/** The body of a create. A patch sends a part of it. */
export type SavedViewBody = {
  entity: EntitySlug;
  name: string;
  layout: CrmLayout;
  filters: SavedFilters;
  sort: SavedSort | null;
  columns: string[] | null;
  pipeline_id: string | null;
  shared?: boolean;
};

/** What a view captures from the screen. */
export type ViewState = Pick<
  SavedViewBody,
  "layout" | "filters" | "sort" | "columns" | "pipeline_id"
>;

/** The filters of the screen, in the saved shape. Empty ones are left out. */
export function captureFilters(view: CrmView): SavedFilters {
  const out: SavedFilters = {};
  if (view.q.trim()) out.q = view.q.trim();
  if (view.statusId) out.status = view.statusId;
  if (view.owner) out.owner = view.owner;
  if (view.source) out.source = view.source;
  if (view.includeConverted) out.converted = true;
  for (const key of RANGE_KEYS) {
    const value = view.ranges[key];
    if (value) out[key] = value;
  }
  const custom = Object.fromEntries(
    Object.entries(view.custom).filter(([, items]) => items.length > 0),
  );
  if (Object.keys(custom).length > 0) out.custom = custom;
  return out;
}

/**
 * The screen, as a view would store it.
 *
 * `columns` is the member's choice: `null` for nothing saved, and `[]` for
 * saved empty (CRM-Q17a-2 item 2). A view keeps that difference. A view with
 * `null` shows the columns of the fill rank, and a view with `[]` shows none.
 */
export function captureView(
  view: CrmView,
  columns: string[] | null,
  pipelineId: string | null,
): ViewState {
  return {
    layout: hasBoard(view.tab) ? view.layout : "list",
    filters: captureFilters(view),
    sort: view.sort ? { key: view.sort, dir: view.dir } : null,
    columns: columns === null ? null : columns.slice(0, MAX_COLUMNS),
    pipeline_id: view.tab === "deals" ? pipelineId : null,
  };
}

/** The `CrmView` that a saved view opens. It carries `viewId`, so the URL names it. */
export function applyView(saved: SavedView): CrmView {
  const f = saved.filters ?? {};
  const ranges: Partial<Record<RangeKey, string>> = {};
  for (const key of RANGE_KEYS) {
    const value = f[key];
    if (typeof value === "string") ranges[key] = value;
  }
  return {
    tab: saved.entity,
    layout: hasBoard(saved.entity) ? saved.layout : defaultLayout(saved.entity),
    pipelineId: saved.pipeline_id,
    record: null,
    q: f.q ?? "",
    statusId: f.status ?? null,
    includeConverted: f.converted === true,
    owner: f.owner ?? null,
    sort: saved.sort?.key ?? null,
    dir: saved.sort?.dir === "asc" ? "asc" : "desc",
    source: f.source ?? null,
    ranges,
    custom: { ...(f.custom ?? {}) },
    viewId: saved.id,
  };
}

function stable(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(stable).join(",")}]`;
  if (value && typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>)
      .filter(([, v]) => v !== undefined)
      .sort(([a], [b]) => a.localeCompare(b));
    return `{${entries.map(([k, v]) => `${JSON.stringify(k)}:${stable(v)}`).join(",")}}`;
  }
  return JSON.stringify(value ?? null);
}

/**
 * What a saved view holds, as a `ViewState`. The column list stays `null`
 * (nothing saved) or a list, which can be empty (CRM-Q17a-2 item 2).
 */
export function stateOf(saved: SavedView): ViewState {
  return {
    layout: saved.layout,
    filters: saved.filters ?? {},
    sort: saved.sort,
    columns: Array.isArray(saved.columns) ? saved.columns : null,
    pipeline_id: saved.pipeline_id,
  };
}

/** Has the screen moved away from the saved view? Compared on the stored shape. */
export function viewDiverges(saved: SavedView, current: ViewState): boolean {
  return stable(stateOf(saved)) !== stable(current);
}

/** May this caller change or delete the view? The server decides; this hides the controls. */
export function canEditView(saved: SavedView, isAdmin: boolean): boolean {
  return saved.mine || isAdmin;
}

/** The views in the rail order: shared ones last, each group by position. */
export function railViews(views: SavedView[] | null | undefined): SavedView[] {
  return [...(views ?? [])].sort(
    (a, b) => Number(a.shared) - Number(b.shared) || a.position - b.position,
  );
}

/** The saved view that `?view=` names, or null (a deleted or unseen id). */
export function activeSavedView(
  views: SavedView[] | null | undefined,
  viewId: string | null,
): SavedView | null {
  return viewId ? (views ?? []).find((v) => v.id === viewId) ?? null : null;
}

/** Is any filter in force? Drives the "Clear" button and the Save view button. */
export function isFiltered(view: CrmView): boolean {
  return Object.keys(captureFilters(view)).length > 0;
}

/** Clear every filter, keep the tab, the layout, the pipeline and the sort. */
export function clearFilters(view: CrmView): CrmView {
  return {
    ...view,
    q: "",
    statusId: null,
    includeConverted: false,
    owner: null,
    source: null,
    ranges: {},
    custom: {},
  };
}

/** The words for the delete dialog. Only the owner's view can be a private loss. */
export function deleteCopy(saved: SavedView): { title: string; body: string } {
  return {
    title: `Delete “${saved.name}”?`,
    body: saved.shared
      ? "This view is shared. Each member of the organization loses it. Records do not change."
      : "The view goes away. Records do not change.",
  };
}

/**
 * A link that carries only `?view=<id>` opens that view. Returns the view to
 * show, or null when the URL says more than the id, or the id is not known
 * (yet, or any more).
 */
export function resolveBareLink(
  search: string,
  views: ReadonlyArray<SavedView>,
): CrmView | null {
  const params = new URLSearchParams(search);
  const id = params.get("view");
  const keys = [...new Set([...params.keys()])];
  if (!id || keys.length !== 1) return null;
  const saved = views.find((v) => v.id === id);
  return saved ? applyView(saved) : null;
}
