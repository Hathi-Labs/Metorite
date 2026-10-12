"use client";

import { useEffect, useSyncExternalStore } from "react";

import { useToast } from "@/components/ui/Toast";
import { useViewMode } from "@/components/ViewModeProvider";
import { isTypingTarget, isUndoShortcut } from "@/lib/keyboard";

import { useTaskStore } from "../lib/taskStore";
import { syncUndoToast } from "../lib/undoToast";

// ── How many pages hold the undo now ────────────────────────────────────────
//
// The shell's bell (NS-6) completes a task from ANY page, through the store's
// gesture. A page that mounts `UndoToast` already says its Undo. On any other
// page, `UndoToastFallback` (mounted by the bell's host) says it, with no keys.
// This count is how the fallback knows which case it is in. Memory only.

let mounted = 0;
const mountListeners = new Set<() => void>();

function setMounted(delta: number): void {
  mounted += delta;
  mountListeners.forEach((l) => l());
}

function subscribeMounted(listener: () => void): () => void {
  mountListeners.add(listener);
  return () => {
    mountListeners.delete(listener);
  };
}

/** True while a page holds `UndoToast`. */
function useUndoToastMounted(): boolean {
  return useSyncExternalStore(subscribeMounted, () => mounted > 0, () => false);
}

/**
 * Keep THE toast in step with the store's snapshot. `keys` says whether the
 * Ctrl+Z key is bound where it is read. `enabled: false` holds still.
 * `resay` re-says the toast with no key hint when the reader leaves.
 */
function useUndoToastSync(keys: boolean, enabled = true, resay = true) {
  const undoSnapshot = useTaskStore((s) => s.undoSnapshot);
  const undoLastChange = useTaskStore((s) => s.undoLastChange);
  const dismissUndo = useTaskStore((s) => s.dismissUndo);
  const openFocus = useTaskStore((s) => s.openFocus);
  const toast = useToast();

  useEffect(() => {
    if (!enabled) return;
    const store = {
      // Read at the moment the toast closes, never the render's copy.
      current: () => useTaskStore.getState().undoSnapshot,
      undo: undoLastChange,
      dismiss: dismissUndo,
      openTask: openFocus,
    };
    syncUndoToast(undoSnapshot, toast, store, { keys });
    // Unmount (a route change) unbinds Ctrl+Z, but the toast lives on
    // in the app-wide viewport. Re-say it in place without the key hint. The
    // Undo button still works: the store is app-wide.
    // The fallback passes `resay: false`. It stops only because a page took
    // the toast over, and that page has said it with its keys already.
    if (!resay) return;
    return () => {
      syncUndoToast(useTaskStore.getState().undoSnapshot, toast, store, { keys: false });
    };
  }, [enabled, resay, undoSnapshot, toast, undoLastChange, dismissUndo, openFocus, keys]);
}

/**
 * The global one-level undo for the task manager.
 *
 * Mounted once at the page level so it works in EVERY view (Inbox, Next,
 * Done, …) and in `/calendar`. Since continuity P3 it draws nothing itself:
 * the undo speaks through THE toast (`useToast`), the one Projects uses, with
 * an "Undo" action. `lib/undoToast.ts` owns the toast's life, and the rule
 * that the soft-delete purge runs exactly once when the window closes.
 */
export function UndoToast() {
  const undoSnapshot = useTaskStore((s) => s.undoSnapshot);
  const undoLastChange = useTaskStore((s) => s.undoLastChange);
  // A decision that also wrote the shared task (a move, a reassign or a due
  // date) cannot be reversed from here. The toast offers the task instead of
  // an Undo that would only take back half of it.
  const sharedId = undoSnapshot?.sharedChangeTaskId;

  // A phone has no keyboard, so the toast names only its button there.
  const { isMobile } = useViewMode();

  useEffect(() => {
    setMounted(1);
    return () => setMounted(-1);
  }, []);

  useUndoToastSync(!isMobile);

  /**
   * Keyboard: Ctrl/Cmd+Z, the same key as in Projects. Undo is the one
   * binding a member brings with them from every other application. The bare
   * `u` this app also answered went with the letter shortcuts (owner,
   * 2026-10-13).
   *
   * Both predicates come from `@/lib/keyboard` rather than a hand-rolled tag
   * check, which missed `<select>`.
   *
   * ⚠️ **This is still ONE level and there is no redo.** The store's model is a
   * snapshot of the rows before a change, and two snapshots of overlapping
   * state cannot both be true, so it cannot become a history. The
   * command-based stack in `@/lib/undo` is the shape that can; migrating
   * `taskStore`'s dispose / clarify / delete / archive / schedule actions onto
   * it is the outstanding work, not a missing wire.
   */
  useEffect(() => {
    if (!undoSnapshot || sharedId) return;
    const onKey = (e: KeyboardEvent) => {
      if (isTypingTarget(e.target as HTMLElement | null)) return;
      if (isUndoShortcut(e)) {
        e.preventDefault();
        undoLastChange();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [undoSnapshot, undoLastChange, sharedId]);

  return null;
}

/**
 * The same toast, with no keys, for a page that holds no `UndoToast`. The
 * shell bell's host mounts it (NS-6), so a Done from the bell offers the
 * store's Undo on every page, as it does on My Day. It binds no key: a page
 * such as Projects has its own Ctrl+Z, and this must not take it.
 */
export function UndoToastFallback() {
  const held = useUndoToastMounted();
  useUndoToastSync(false, !held, false);
  return null;
}
