/**
 * My Tasks · the one-level undo, drawn through THE toast (continuity P3,
 * item 3).
 *
 * Until this slice the undo had its own pill (`UndoToast.tsx`) with its own
 * timer and no live region, while Projects spoke through the shared `Toast`.
 * Now the undo is one more `useToast().show()` call. The store's model does
 * not change: one snapshot, one level, no redo. Moving it onto Projects'
 * multi-level `lib/undo.ts` is a separate, larger job.
 *
 * ## The purge must run exactly once
 *
 * A delete in my own tree is soft first. `dismissUndo` makes it permanent
 * when the window closes without an Undo. Four ways the toast can close, and
 * what each must do:
 *
 * | The toast closes because       | The purge                           |
 * |--------------------------------|-------------------------------------|
 * | its timer ran out              | runs, once                          |
 * | the reader pressed ×           | runs, once                          |
 * | the reader pressed Undo        | does NOT run                        |
 * | a newer change replaced it     | ran already, inside the store       |
 *
 * Two facts make that hold:
 *
 * 1. The substrate closes a toast BEFORE it runs the toast's action
 *    (`Toast.tsx`: "Closed BEFORE the action runs"). So `onClose` fires for an
 *    Undo click too, and the Undo has not happened yet. `onClose` therefore
 *    DEFERS its check to a microtask. By then the action has run.
 * 2. The check asks "is the snapshot this toast was raised for still the
 *    store's snapshot?". After an Undo it is null. After a replacement it is
 *    the newer one. Only a plain timeout or dismissal leaves it in place, and
 *    only then does `onClose` call `dismissUndo`. `dismissUndo` clears the
 *    snapshot before it purges, so a second call finds nothing to purge.
 *
 * Fence: `undoToast.test.ts`, over a fake toast that closes the way the
 * substrate does.
 */

import { UNDO_WINDOW_SECONDS } from "./removal";

/** One toast key for the undo. A newer change updates it in place. */
export const UNDO_TOAST_KEY = "tasks-undo";

/** The parts of the store's `UndoSnapshot` this toast reads. */
export interface UndoSnapshotLike {
  label: string;
  /** The change also wrote the shared task: offer the task, not an Undo. */
  sharedChangeTaskId?: string;
}

/** The store, as the toast reaches it. */
export interface UndoToastStore<S extends UndoSnapshotLike> {
  /** The store's snapshot NOW (not the one this toast was raised for). */
  current(): S | null;
  /** `undoLastChange`. */
  undo(): void;
  /** `dismissUndo`: clears the snapshot, then purges a soft delete. */
  dismiss(): void;
  /** `openFocus`. */
  openTask(id: string): void;
}

/** The slice of `useToast()` this needs. */
export interface UndoToastApi {
  show(spec: {
    key: string;
    variant: "success";
    title: string;
    description?: string;
    action?: { label: string; onClick: () => void };
    timeout: number;
    onClose: () => void;
  }): unknown;
  dismiss(key: string): void;
}

/** Runs `fn` after the current task. Injected so the test can step it. */
export type Defer = (fn: () => void) => void;

const microtask: Defer = (fn) => queueMicrotask(fn);

/**
 * Put the toast in step with the store's snapshot: show it (or update it in
 * place) when there is one, and take it down when there is not.
 */
export function syncUndoToast<S extends UndoSnapshotLike>(
  snap: S | null,
  toast: UndoToastApi,
  store: UndoToastStore<S>,
  defer: Defer = microtask,
): void {
  if (!snap) {
    // Undone (the `u` key, ⌘Z) or dismissed. `onClose` runs and finds the
    // snapshot gone, so nothing is purged here.
    toast.dismiss(UNDO_TOAST_KEY);
    return;
  }
  const shared = snap.sharedChangeTaskId;
  toast.show({
    key: UNDO_TOAST_KEY,
    variant: "success",
    title: snap.label,
    description: shared
      ? "This changed the task on its board. Open it to change it back."
      : "Press U or Ctrl+Z to undo.",
    action: shared
      ? {
          label: "Open task",
          onClick: () => {
            store.dismiss();
            store.openTask(shared);
          },
        }
      : { label: "Undo", onClick: () => store.undo() },
    // The delete dialog quotes this number (`removalCopy`), so it is ONE
    // constant and the promise cannot drift from the toast.
    timeout: UNDO_WINDOW_SECONDS * 1000,
    onClose: () =>
      defer(() => {
        // Still the snapshot this toast was raised for: the window closed
        // without an Undo. Finalize it. See the header for the other cases.
        if (store.current() === snap) store.dismiss();
      }),
  });
}
