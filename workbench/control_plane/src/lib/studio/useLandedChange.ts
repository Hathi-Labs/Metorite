"use client";

/**
 * The hook of the change bar and its one-step undo (`ai_studio.md` §14, slice G5). The rules and the
 * tracker are pure, in `landedChange.ts`, and tested there. This file only wires them to React and to
 * the ONE save (`saveDraft`).
 *
 * The page keeps one `Landed`: the manifest before the newest change. The page marks its own writes
 * (`markOwn`) with the hash that the server answered, so a poll that brings a draft of Mira is never
 * taken for a save of the member. Undo sends the earlier manifest through `saveDraft` with
 * `based_on` = the current hash, so a draft that changed again gets 409 and nothing is undone.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { LiveObject } from "./draftEdit";
import type { DraftWatch } from "./draftWatch";
import { NEW_TRACKER, markOwn as mark, observe, runUndo, undoDropsLive, undoFailure, type Landed, type Tracker } from "./landedChange";
import { StudioOff } from "./realDoor";

export interface LandedChange {
  landed: Landed | null;
  /** Mark the next draft with this hash as the member's own write. */
  markOwn: (who: "you" | "undo" | "redo", hash: string | null) => void;
  undo: () => void;
  undoing: boolean;
  /** Why the last undo did not land, or null. */
  note: string | null;
  /** Why Undo would drop something that is live, or null. */
  liveBlock: string | null;
}

export function useLandedChange(pkg: string, watch: DraftWatch): LandedChange {
  const { draft } = watch;
  const tracker = useRef<Tracker>(NEW_TRACKER);
  const [landed, setLanded] = useState<Landed | null>(null);
  const [undoing, setUndoing] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  useEffect(() => {
    const next = observe(tracker.current, draft);
    const changed = next.landed !== tracker.current.landed;
    tracker.current = next;
    if (changed) { setLanded(next.landed); setNote(null); }
  }, [draft]);

  const markOwn = useCallback((who: "you" | "undo" | "redo", hash: string | null) => { tracker.current = mark(tracker.current, who, hash); }, []);

  const liveBlock = useMemo(
    () => (landed ? undoDropsLive(landed.before, draft?.manifest, (draft?.objects ?? []) as unknown as LiveObject[]) : null),
    [landed, draft],
  );

  const undo = useCallback(() => {
    if (!draft || !landed || undoing || liveBlock) return;
    setUndoing(true);
    setNote(null);
    runUndo(pkg, draft, landed).then(
      (r) => {
        setUndoing(false);
        if (r.kind === "saved") { markOwn(r.who, r.draft.manifestHash); watch.adopt(r.draft); return; }
        setNote(r.text);
      },
      (e: unknown) => {
        setUndoing(false);
        setNote(e instanceof StudioOff ? "This app is not available to you any more. Nothing was undone. Go back to Studio." : undoFailure({ kind: "failed", message: "" }));
      },
    );
  }, [pkg, draft, landed, undoing, liveBlock, watch, markOwn]);

  return { landed, markOwn, undo, undoing, note, liveBlock };
}
