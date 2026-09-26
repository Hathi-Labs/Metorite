"use client";

import { useEffect } from "react";

import { useToast } from "@/components/ui/Toast";
import { useViewMode } from "@/components/ViewModeProvider";
import { isTypingTarget, isUndoShortcut } from "@/lib/keyboard";

import { useTaskStore } from "../lib/taskStore";
import { syncUndoToast } from "../lib/undoToast";

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
  const dismissUndo = useTaskStore((s) => s.dismissUndo);
  const openFocus = useTaskStore((s) => s.openFocus);
  const toast = useToast();
  // A decision that also wrote the shared task (a move, a reassign or a due
  // date) cannot be reversed from here. The toast offers the task instead of
  // an Undo that would only take back half of it.
  const sharedId = undoSnapshot?.sharedChangeTaskId;

  // A phone has no keyboard, so the toast names only its button there.
  const { isMobile } = useViewMode();

  useEffect(() => {
    const store = {
      // Read at the moment the toast closes, never the render's copy.
      current: () => useTaskStore.getState().undoSnapshot,
      undo: undoLastChange,
      dismiss: dismissUndo,
      openTask: openFocus,
    };
    syncUndoToast(undoSnapshot, toast, store, { keys: !isMobile });
    // Unmount (a route change) unbinds `u` and Ctrl+Z, but the toast lives on
    // in the app-wide viewport. Re-say it in place without the key hint. The
    // Undo button still works: the store is app-wide.
    return () => {
      syncUndoToast(useTaskStore.getState().undoSnapshot, toast, store, { keys: false });
    };
  }, [undoSnapshot, toast, undoLastChange, dismissUndo, openFocus, isMobile]);

  /**
   * Keyboard: Ctrl/Cmd+Z, or the bare `u` this app has always used.
   *
   * Ctrl+Z was added 2026-08-31 so the shortcut is the same across the product
   * — undo is the one binding a user brings with them from every other
   * application, and having it work in Projects but not here is worse than
   * having it nowhere. `u` stays: it is in the toast's own hint and in
   * people's fingers.
   *
   * Both predicates come from `@/lib/keyboard` rather than the hand-rolled
   * tag check that was here. That check missed `<select>`, so `u` fired while
   * a dropdown had focus.
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
      const bare = !e.metaKey && !e.ctrlKey && !e.altKey && (e.key === "u" || e.key === "U");
      if (bare || isUndoShortcut(e)) {
        e.preventDefault();
        undoLastChange();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [undoSnapshot, undoLastChange, sharedId]);

  return null;
}
