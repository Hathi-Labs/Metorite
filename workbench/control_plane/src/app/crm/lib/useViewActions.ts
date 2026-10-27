"use client";

// ── The saved-view controller of one CRM list (WS-53 CRM-U8) ────────────────
//
// The page and the module list both run the same acts: open a view, save the
// screen as a view, update it, reset it, rename, share, delete. This hook holds
// them once, over the shared seam (`src/lib/savedViews`). It owns the columns
// that the member picked since a saved view opened. A saved view keeps its
// columns on the view. The default view keeps them in this browser.
//
// `scope` is the key of the list: an entity slug, or `module:<api_name>`.

import { useMemo, useState } from "react";

import { dirtyState, viewById, type DirtyState } from "@/lib/savedViews";

import * as api from "./api";
import { CrmError } from "./api";
import {
  applyView,
  captureView,
  clearFilters,
  deleteCopy,
  isBlankState,
  stateOf,
  type SavedView,
  type ViewEntity,
} from "./savedViews";
import type { CrmView } from "./urlState";
import { invalidateViews, useCrmViews } from "./useSavedViews";

export type ViewActionsInput = {
  scope: ViewEntity | null;
  view: CrmView;
  /** The deal pipeline in effect, for a deals view. */
  pipelineId: string | null;
  /** The columns this browser keeps for the default view, or null. */
  defaultColumns: string[] | null;
  go: (next: CrmView) => void;
  onError: (message: string) => void;
  onSaved?: (made: SavedView) => void;
};

export type ViewActions = {
  views: SavedView[];
  active: SavedView | null;
  dirty: DirtyState;
  busy: boolean;
  /** The custom columns in effect for the list. `null` is "nothing saved". */
  columns: string[] | null;
  /** The list picked columns. Call it when a saved view is in effect. */
  setColumns: (keys: string[]) => void;
  select: (saved: SavedView | null) => void;
  save: (name: string, shared: boolean) => Promise<void>;
  update: () => Promise<void>;
  reset: () => void;
  rename: (saved: SavedView, name: string) => Promise<void>;
  toggleShare: (saved: SavedView) => Promise<void>;
  remove: (saved: SavedView) => Promise<void>;
  deleteCopy: typeof deleteCopy;
};

/** The default view of a list: the same tab, with no filter, sort or view. */
export function defaultViewOf(view: CrmView): CrmView {
  return { ...clearFilters(view), sort: null, dir: "desc", viewId: null };
}

function message(err: unknown, fallback: string): string {
  return err instanceof CrmError ? err.message : fallback;
}

export function useViewActions({
  scope,
  view,
  pipelineId,
  defaultColumns,
  go,
  onError,
  onSaved,
}: ViewActionsInput): ViewActions {
  const views = useCrmViews(scope);
  const active = viewById(views, view.viewId);
  const [draft, setDraft] = useState<{ viewId: string; keys: string[] } | null>(null);
  const [busy, setBusy] = useState(false);

  // `null` is "nothing saved", and `[]` is "saved empty" (CRM-Q17a-2 item 2).
  const draftOfView = draft?.viewId === view.viewId ? draft?.keys : undefined;
  const columns: string[] | null = active ? (draftOfView ?? active.columns ?? null) : defaultColumns;

  const dirty = useMemo(() => {
    if (active) {
      return dirtyState(
        { state: stateOf(active) },
        { state: captureView(view, columns, view.pipelineId), blank: false },
      );
    }
    const state = captureView(view, columns, pipelineId);
    return dirtyState(null, { state, blank: isBlankState(state) });
  }, [active, view, columns, pipelineId]);

  async function run(act: () => Promise<void>, fallback: string) {
    setBusy(true);
    try {
      await act();
    } catch (err) {
      onError(message(err, fallback));
    } finally {
      setBusy(false);
    }
  }

  return {
    views,
    active,
    dirty,
    busy,
    columns,
    setColumns: (keys) => {
      if (view.viewId) setDraft({ viewId: view.viewId, keys });
    },
    select: (saved) => {
      setDraft(null);
      go(saved ? applyView(saved) : defaultViewOf(view));
    },
    save: async (name, shared) => {
      if (!scope) return;
      await run(async () => {
        const state = captureView(view, columns, pipelineId);
        const made = await api.createView({ entity: scope, name, shared, ...state });
        invalidateViews();
        setDraft(null);
        go(applyView(made));
        onSaved?.(made);
      }, "The view was not saved.");
    },
    update: async () => {
      if (!active) return;
      await run(async () => {
        await api.patchView(active.id, captureView(view, columns, pipelineId));
        invalidateViews();
        setDraft(null);
      }, "The view was not updated.");
    },
    reset: () => {
      if (!active) return;
      setDraft(null);
      go(applyView(active));
    },
    rename: (saved, name) =>
      run(async () => {
        await api.patchView(saved.id, { name });
        invalidateViews();
      }, "The view was not renamed."),
    toggleShare: (saved) =>
      run(async () => {
        await api.patchView(saved.id, { shared: !saved.shared });
        invalidateViews();
      }, "The view was not changed."),
    remove: (saved) =>
      run(async () => {
        await api.deleteView(saved.id);
        invalidateViews();
        // A deleted view in effect leaves its filters on screen but no longer names it.
        if (view.viewId === saved.id) go({ ...view, viewId: null });
      }, "The view was not deleted."),
    deleteCopy,
  };
}
