/**
 * The ask-first delete of a record (`ai_studio.md` §14 D2b-1, slice STU-D2b).
 *
 * A store with no React, as `boardMove.ts` is. `DeleteRecordButton.tsx` draws
 * it. The button only OPENS the dialog. Cancel closes it and sends nothing.
 * Confirm sends exactly one `door.run(object:<name>.delete, {id})`, and a
 * second Confirm while the first is pending does nothing (`pressOnce`). A
 * refusal keeps the dialog open with its words, and the record stays.
 *
 * B2c-1 puts the proof of a direct write on the server. It cannot see this
 * dialog, so `DeleteRecordButton.test.ts` holds the test that a delete asks first.
 */
import { invalidate } from "@/lib/dataCache";
import { refusalText } from "./boardMove";
import { doorPrefix, type StudioDataDoor } from "./dataDoor";
import { ACTION_FAILED, pressOnce } from "./TypedActionButton";

export interface DeleteState {
  open: boolean;
  busy: boolean;
  /** The refusal to show in the dialog, or null. */
  error: string | null;
}

export interface DeleteFlow {
  getState(): DeleteState;
  subscribe(fn: () => void): () => void;
  /** The button was pressed: ask. Sends nothing. */
  ask(): void;
  /** Cancel, Escape or the close button. Sends nothing. A delete in flight keeps the dialog. */
  cancel(): void;
  /** Confirm. Resolves true when the record is gone. */
  confirm(): Promise<boolean>;
}

/** The words of the dialog. They say what is true: a delete cannot be undone. */
export function deleteCopy(title: string | null | undefined): {
  heading: string; subject: string | null; body: string; confirmLabel: string;
} {
  const subject = typeof title === "string" && title.trim() ? title.trim() : null;
  return {
    heading: "Delete this record?",
    subject,
    body: "This deletes the record for good. You cannot undo it.",
    confirmLabel: "Delete",
  };
}

export function createDeleteFlow(door: StudioDataDoor, action: string, id: string): DeleteFlow {
  let state: DeleteState = { open: false, busy: false, error: null };
  const listeners = new Set<() => void>();
  const busy = { current: false };
  const set = (next: Partial<DeleteState>) => {
    state = { ...state, ...next };
    listeners.forEach((fn) => fn());
  };
  return {
    getState: () => state,
    subscribe(fn) {
      listeners.add(fn);
      return () => { listeners.delete(fn); };
    },
    ask: () => set({ open: true, error: null }),
    cancel() {
      if (state.busy) return;
      set({ open: false, error: null });
    },
    async confirm() {
      let gone = false;
      await pressOnce(busy, async () => {
        set({ busy: true, error: null });
        try {
          const res = await door.run(action, { id });
          if (res && res.ok === true) {
            invalidate(doorPrefix(door));
            gone = true;
            set({ busy: false, open: false });
            return;
          }
          set({ busy: false, error: refusalText(res) });
        } catch {
          set({ busy: false, error: ACTION_FAILED });
        }
      });
      return gone;
    },
  };
}
