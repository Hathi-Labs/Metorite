"use client";

import Button from "@/components/ui/Button";
import Icon from "@/components/Icon";
import { isTypingTarget, isUndoShortcut } from "@/lib/keyboard";
import { useEffect } from "react";
import { useTaskStore } from "../lib/taskStore";

/**
 * The global one-level undo toast for the task manager.
 *
 * Mounted once at the page level so it appears in EVERY view (Inbox, Next,
 * Done, …) — previously it lived inside InboxView and so was invisible when a
 * delete/archive happened from a task view. It owns the auto-dismiss timer:
 * when the window closes without an Undo, `dismissUndo` finalizes any pending
 * soft delete (purge + ClickUp propagation).
 */
export function UndoToast() {
  const undoSnapshot = useTaskStore((s) => s.undoSnapshot);
  const undoLastChange = useTaskStore((s) => s.undoLastChange);
  const dismissUndo = useTaskStore((s) => s.dismissUndo);

  // Auto-dismiss after a few seconds (async → effect-safe). Dismiss also
  // finalizes a pending soft delete, so this is the point deletion becomes
  // permanent / propagates upstream.
  useEffect(() => {
    if (!undoSnapshot) return;
    const t = setTimeout(() => dismissUndo(), 7000);
    return () => clearTimeout(t);
  }, [undoSnapshot, dismissUndo]);

  /**
   * Keyboard: Ctrl/Cmd+Z, or the bare `u` this app has always used.
   *
   * Ctrl+Z was added 2026-08-31 so the shortcut is the same across the product
   * — undo is the one binding a user brings with them from every other
   * application, and having it work in Projects but not here is worse than
   * having it nowhere. `u` stays: it is in the toast's own kbd hint and in
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
    if (!undoSnapshot) return;
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
  }, [undoSnapshot, undoLastChange]);

  if (!undoSnapshot) return null;

  return (
    <div className="chat-fade-in fixed bottom-20 left-1/2 z-[70] flex -translate-x-1/2 items-center gap-3 rounded-full border border-border bg-popover px-4 py-2 shadow-2xl sm:bottom-6">
      <span className="whitespace-nowrap text-sm text-foreground">
        {undoSnapshot.label}
      </span>
      <button
        type="button"
        onClick={undoLastChange}
        className="tech-transition inline-flex items-center gap-1 whitespace-nowrap text-sm font-semibold text-primary hover:underline"
      >
        <Icon name="Undo2" className="h-3.5 w-3.5" />
        Undo
        <kbd className="ml-0.5 hidden rounded border border-border px-1 py-0.5 font-mono text-[9px] text-muted-foreground sm:inline">
          Ctrl+Z
        </kbd>
      </button>
      <Button variant="text" size="none" radius="keep" layout="" type="button" onClick={dismissUndo} aria-label="Dismiss" className="rounded-md p-0.5">
        <Icon name="X" className="h-3.5 w-3.5" />
      </Button>
    </div>
  );
}
