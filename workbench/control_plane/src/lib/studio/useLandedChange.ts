"use client";

/**
 * The hook of the change bar and its one-step undo (`ai_studio.md` §14, slice G5). The rules are in
 * `landedChange.ts`. This file wires them to React and to the ONE save (`saveDraft`).
 *
 * The page keeps one `Landed`: the manifest before the newest change. A change lands when the draft
 * moves to a manifest that is not the last one. The page marks its own writes (`markOwn`) with the
 * hash that the server answered, so a poll that brings a draft of Mira is never taken for a save of
 * the member. Undo sends the earlier manifest through `saveDraft` with `based_on` = the current
 * hash, so a draft that changed again gets 409 and nothing is undone.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import type { DraftWatch } from "./draftWatch";
import { saveDraft } from "./draftSave";
import { landChange, undoFailure, type Landed, type Who } from "./landedChange";
import type { PackageDraft } from "./packageDraft";
import { StudioOff } from "./realDoor";

export interface LandedChange {
  landed: Landed | null;
  /** Mark the next draft with this hash as the member's own write. */
  markOwn: (who: "you" | "undo" | "redo", hash: string | null) => void;
  undo: () => void;
  undoing: boolean;
  /** Why the last undo did not land, or null. */
  note: string | null;
}

export function useLandedChange(pkg: string, watch: DraftWatch): LandedChange {
  const { draft } = watch;
  const prev = useRef<PackageDraft | null>(null);
  const own = useRef<{ who: "you" | "undo" | "redo"; hash: string | null } | null>(null);
  const [landed, setLanded] = useState<Landed | null>(null);
  const [undoing, setUndoing] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  useEffect(() => {
    const before = prev.current;
    prev.current = draft;
    if (!before || !draft) return;
    const mine = own.current;
    const who: Who = mine && mine.hash !== null && mine.hash === draft.manifestHash ? mine.who : "other";
    if (who !== "other") own.current = null;
    const next = landChange(before, draft, who);
    if (next) { setLanded(next); setNote(null); }
  }, [draft]);

  const markOwn = useCallback((who: "you" | "undo" | "redo", hash: string | null) => { own.current = { who, hash }; }, []);

  const undo = useCallback(() => {
    if (!draft || !landed || undoing) return;
    setUndoing(true);
    setNote(null);
    saveDraft(pkg, landed.before, draft.manifestHash, draft).then(
      (r) => {
        setUndoing(false);
        if (r.kind === "saved") { markOwn(landed.who === "undo" ? "redo" : "undo", r.draft.manifestHash); watch.adopt(r.draft); return; }
        setNote(undoFailure(r));
      },
      (e: unknown) => {
        setUndoing(false);
        setNote(e instanceof StudioOff ? "This app is not available to you any more. Nothing was undone. Go back to Studio." : undoFailure({ kind: "failed", message: "" }));
      },
    );
  }, [pkg, draft, landed, undoing, watch, markOwn]);

  return { landed, markOwn, undo, undoing, note };
}
