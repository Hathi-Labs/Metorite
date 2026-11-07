"use client";

/**
 * The hook of the Studio IDE edit state (`ai_studio.md` §6.3, slice G3c). The reducer and the save
 * step are in `draftEditor.ts`. This file only wires them to React, the draft watch and the leave guard.
 */
import { useCallback, useReducer, useRef } from "react";

import type { DraftWatch } from "./draftWatch";
import { CLEAN, commitSave, isDirty, newerDraft, reduce, shownManifest, type EditState } from "./draftEditor";
import { useLeaveGuard } from "./useLeaveGuard";

export interface DraftEditor {
  state: EditState;
  dirty: boolean;
  /** The manifest the tabs draw: the working copy, else the stored draft. */
  shown: unknown;
  /** A newer draft exists than the one that the edit began from. */
  newer: boolean;
  /** An edit from the Data or Views tab. Ignored while the Code text does not parse. */
  edit: (manifest: unknown) => void;
  /** The text of the Code tab. */
  typeCode: (text: string) => void;
  save: () => void;
  revert: () => void;
  /** Drop the edit and read the stored draft again. */
  reload: () => void;
}

export function useDraftEditor(pkg: string, watch: DraftWatch): DraftEditor {
  const [state, dispatch] = useReducer(reduce, CLEAN);
  const { draft } = watch;
  const dirty = isDirty(state, draft);
  // Leaving with an edit asks first: the browser's prompt for a reload or a closed tab, and a confirm for an in-app link.
  const never = useRef(false);
  useLeaveGuard(dirty, never);

  const edit = useCallback((manifest: unknown) => { if (draft) dispatch({ type: "edit", manifest, draft }); }, [draft]);
  const typeCode = useCallback((text: string) => { if (draft) dispatch({ type: "code", text, draft }); }, [draft]);
  const revert = useCallback(() => dispatch({ type: "revert" }), []);
  const reload = useCallback(() => { dispatch({ type: "revert" }); watch.refresh(); }, [watch]);
  const save = useCallback(() => {
    if (!draft || state.saving) return;
    dispatch({ type: "saving" });
    commitSave(pkg, state, draft).then(
      ({ action, adopt }) => {
        // The page adopts the saved draft first, so the facts that Mira reads carry the new hash.
        if (adopt) watch.adopt(adopt);
        dispatch(action);
      },
      () => dispatch({ type: "settled", notice: { kind: "failed", text: "The save did not finish. Nothing was saved, and your edit is still here. Press Save again." } }),
    );
  }, [pkg, draft, state, watch]);

  return {
    state, dirty, shown: shownManifest(state, draft), newer: newerDraft(state, draft),
    edit, typeCode, save, revert, reload,
  };
}
